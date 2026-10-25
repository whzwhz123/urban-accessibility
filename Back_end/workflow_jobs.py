"""Sequential batch runner; API wiring and model calls are injected.

Integration:
    job = create_batch(city_id, options)
    job_registry[job['id']] = job  # DurableJob/TaskStore persistence
    run_batch(job['id'], job_registry, task_update, adapters, cancel_check)

Adapters are synchronous functions accepting one context dict:
    fingerprint(ctx) -> JSON snapshot of RAW input content/version identities
    prepare(ctx) -> prepared input references (never collect remote data)
    demand(ctx) -> existing demand summary format, including run_id
    assignment(ctx) -> existing assignment summary format, including run_id
    evaluation(ctx) -> existing workflow summary format, including run_id

fingerprint must include selected raw network/POI/population/service/evaluation
files, selected demand (if any), and relevant code versions; exclude generated
prepared outputs. Resolve 'latest' selections before create_batch, not on resume.
prepare must honor options.inputs and return JSON references, not live objects.
Each adapter honors output_key/fresh, writes new artifacts without overwriting,
and returns only after committing results. Immutable exact-input cache hits are
allowed, but must be reported as cache_hit; never reuse mismatched artifacts.
For solver checkpoints call ctx['checkpoint']({'run_id': id}) as soon as the run
is created; ctx.resume_run_id is only for that same unfinished step. Pass
ctx.cancel_check to cooperative solvers. Never launch this runner in the same
single-worker pool and then wait for another queued API job: call models directly.
All returned values and checkpoint fields must be JSON serializable.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from threading import Lock
from uuid import uuid4

from .task_store import TaskCancelled, check_cancel

PERIODS = ('am_peak', 'offpeak', 'pm_peak')
_LOCKS = {}
_LOCKS_GUARD = Lock()


def _copy(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def normalize_options(options):
    value = deepcopy(options)
    periods = value.get('periods')
    if not isinstance(periods, list) or not periods:
        raise ValueError('请至少选择一个分配时段')
    seen = set()
    for row in periods:
        period = row.get('period')
        if period not in PERIODS or period in seen:
            raise ValueError('分配时段无效或重复')
        seen.add(period)
        demand = row.get('total_demand')
        if demand is None or isinstance(demand, bool):
            raise ValueError('请填写各时段总需求，不能按POI数量推算')
        demand = float(demand)
        if not math.isfinite(demand) or demand <= 0:
            raise ValueError('各时段总需求必须为有限正数')
        row.clear()
        row.update(period=period, total_demand=demand)
    assignment = value.setdefault('assignment', {})
    count = assignment.get('poi_count', 500)
    if isinstance(count, bool) or int(count) != float(count) or int(count) < 2:
        raise ValueError('分配POI数必须为不小于2的整数')
    assignment['poi_count'] = int(count)
    from .assignment.parameters import normalize_parameters
    from .city_workflow import normalize as normalize_evaluation
    assignment['parameters'] = normalize_parameters(assignment.get('parameters'))
    for key in ('default_headway_min', 'assumed_headway_min'):
        number = assignment.get(key, 15 if key == 'default_headway_min' else None)
        if number is not None:
            if isinstance(number, bool) or not math.isfinite(float(number)) or float(number) <= 0:
                raise ValueError('发车间隔必须为有限正数')
            number = float(number)
        elif key == 'default_headway_min':
            raise ValueError('请显式填写缺省发车间隔')
        assignment[key] = number
    value.setdefault('inputs', {})
    value.setdefault('demand', {})
    value.setdefault('evaluation', {})
    value['evaluation']['parameters'] = normalize_evaluation(value['evaluation'].get('parameters'))
    value['evaluation']['input_files'] = {
        key: value['evaluation'].get('input_files', {}).get(key)
        for key in ('isochrone', 'tci')
    }
    # Even when using an explicit demand as parameter template, generate one
    # shared demand for this batch; do not silently use a city-wide latest run.
    value['demand']['parameters'] = {
        **value['demand'].get('parameters', {}),
        'hourly_demands': {
            **value['demand'].get('parameters', {}).get('hourly_demands', {}),
            **{row['period']: row['total_demand'] for row in periods},
        },
    }
    return _copy(value)


def create_batch(city_id, options):
    """POST creates a new record, including when re-running an old batch."""
    options = normalize_options(options)
    steps = [{'id': 'prepare', 'kind': 'prepare', 'label': '准备所选输入'},
             {'id': 'demand', 'kind': 'demand', 'label': '生成共享需求'}]
    steps += [{'id': 'assignment:' + row['period'], 'kind': 'assignment',
               'label': '分配：' + row['period'], **row} for row in options['periods']]
    steps.append({'id': 'evaluation', 'kind': 'evaluation', 'label': '指标评价'})
    for step in steps:
        step.update(status='pending', attempts=0)
    return dict(id=uuid4().hex, kind='batch', city_id=city_id, status='queued',
                created_at=datetime.now(timezone.utc).isoformat(),
                phase='等待执行', options=options, options_signature=_signature(options),
                state={}, steps=steps, cursor=0, progress=0, cancel_requested=False)


def run_batch(job_id, job_registry, task_update, adapters, cancel_check=None):
    """Run/resume one durable record. task_update(job_id, **fields) persists it.

    API resume must first reserve the city, clear cancel_requested and set queued.
    API cancel only sets cancel_requested; completed steps are never reset.
    API must exclude concurrent input activation/import and other city writers.
    GET batches returns {'batches': [...]} (newest first), POST returns the job.
    """
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault((id(job_registry), job_id), Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('该批任务已经运行中')
    try:
        job = job_registry[job_id]
        if job.get('kind') != 'batch':
            raise ValueError('不是批任务')
        if job.get('status') in ('completed', 'partial'):
            return job
        if job.get('status') == 'running':
            raise ValueError('请先将中断任务标记为可恢复状态')
        return _execute(job, job_registry, task_update, adapters, cancel_check)
    finally:
        lock.release()


def _execute(job, registry, task_update, adapters, cancel_check):
    job_id = job['id']
    options = _copy(job['options'])
    steps = _copy(job['steps'])
    state = _copy(job['state'])
    current = None

    def stopped():
        return bool(registry[job_id].get('cancel_requested') or
                    (cancel_check is not None and cancel_check()))

    def persist(**fields):
        # Nested mutations do not trigger DurableJob.update; commit full copies.
        job.update(**_copy(fields))
        if task_update is not None:
            task_update(job_id, **_copy(fields))

    def context(step=None):
        row = step or {}
        return dict(job_id=job_id, city_id=job['city_id'], options=_copy(options),
                    state=_copy(state), step=_copy(row),
                    demand_run_id=state.get('demand', {}).get('run_id'),
                    input_files=_copy(options['evaluation'].get('input_files',
                                      {'isochrone': None, 'tci': None})),
                    output_key=f"{job_id}/{row.get('id', 'inputs')}/{row.get('attempt_id', 'snapshot')}",
                    fresh=not bool(row.get('checkpoint')),
                    resume_run_id=row.get('checkpoint', {}).get('run_id'),
                    cancel_check=stopped)

    def save_steps(**fields):
        done = sum(row['status'] == 'completed' for row in steps)
        cursor = next((i for i, row in enumerate(steps) if row['status'] != 'completed'), len(steps))
        persist(state=state, steps=steps, cursor=cursor,
                progress=round(100 * done / len(steps)), **fields)

    def verify_inputs():
        snapshot = _copy(adapters['fingerprint'](context()))
        if not snapshot:
            raise ValueError('输入指纹不能为空')
        digest = _signature(snapshot)
        if state.get('input_signature') and state['input_signature'] != digest:
            raise ValueError('输入版本已变化；禁止复用旧步骤，请创建新批次')
        if not state.get('input_signature'):
            state.update(input_signature=digest, input_snapshot=snapshot)
            save_steps()

    try:
        if _signature(options) != job['options_signature']:
            raise ValueError('批任务配置已变化，请创建新批次')
        for name in ('fingerprint', 'prepare', 'demand', 'assignment', 'evaluation'):
            if not callable(adapters.get(name)):
                raise ValueError('缺少批任务回调：' + name)
        check_cancel(stopped)
        verify_inputs()
        persist(status='running', phase='开始批任务', error=None)
        for step in steps:
            if step['status'] == 'completed':
                continue
            current = step
            check_cancel(stopped)
            verify_inputs()
            step.update(status='running', error=None, attempts=step['attempts'] + 1)
            step.setdefault('attempt_id', uuid4().hex)
            save_steps(phase=step['label'])

            def checkpoint(fields):
                step['checkpoint'] = {**step.get('checkpoint', {}), **_copy(fields)}
                save_steps()

            def progress(text):
                check_cancel(stopped)
                persist(phase=step['label'] + ' · ' + str(text))

            ctx = context(step)
            ctx.update(checkpoint=checkpoint, progress=progress,
                       allow_collection=False, allow_partial=True)
            result = _copy(adapters[step['kind']](ctx))
            if not isinstance(result, dict):
                raise ValueError('步骤回调必须返回结果对象')
            if step['kind'] != 'prepare' and not result.get('run_id'):
                raise ValueError('步骤结果缺少run_id')
            if result.get('city_id', job['city_id']) != job['city_id']:
                raise ValueError('步骤结果城市不匹配')
            if step['kind'] in ('assignment', 'evaluation'):
                if result.get('demand_run_id') != state['demand']['run_id']:
                    raise ValueError('步骤结果未绑定本批共享需求')
            if step['kind'] == 'assignment':
                if result.get('period') != step['period']:
                    raise ValueError('分配结果时段不匹配')
            verify_inputs()
            if step['kind'] == 'assignment':
                state.setdefault('assignments', {})[step['period']] = result
            else:
                state[step['kind']] = result
            step.update(status='completed', result=result, error=None)
            save_steps()
            current = None
            check_cancel(stopped)
        partial = state.get('evaluation', {}).get('status') == 'partial'
        save_steps(status='partial' if partial else 'completed',
                   phase='批任务执行结束，评价数据不完整' if partial else '批任务完成', error=None,
                   result_status='partial' if partial else 'completed',
                   result={key: state.get(key) for key in ('demand', 'assignments', 'evaluation')})
    except TaskCancelled as error:
        if current is not None and current['status'] != 'completed':
            current.update(status='cancelled', error=str(error))
            if getattr(error, 'run_id', None):
                current['checkpoint'] = {**current.get('checkpoint', {}), 'run_id': error.run_id}
        save_steps(status='cancelled', phase='已停止，已完成步骤和断点保留', error=None)
    except Exception as error:
        if current is not None and current['status'] != 'completed':
            current.update(status='failed', error=str(error))
        save_steps(status='failed', phase='批任务失败，可重试未完成步骤', error=str(error))
    return registry[job_id]

