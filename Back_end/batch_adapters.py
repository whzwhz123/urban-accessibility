"""Synchronous local model adapters for workflow_jobs; no collection or API jobs."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .task_store import check_cancel


def create_adapters(city, data, bus, poi):
    """Create per-city callbacks; retain the live city dict to detect activation.

    options.inputs = {network: bool, poi: bool, custom_input_version: optional}
    options.demand = {parameters: city_demand settings}
    options.assignment = {poi_count, parameters, assumed_headway_min,
                          default_headway_min}
    options.evaluation = {parameters, input_files: {isochrone: name|null,
                                                  tci: name|null}}
    Missing evaluation selections mean NONE, never a moving 'latest' file.
    Exact-input immutable model cache hits remain valid and are reported.
    data/bus/poi must match the local API roots used by run_city.
    """
    data, bus, poi = Path(data), Path(bus), Path(poi)

    def digest(path, ctx):
        path = Path(path)
        if not path.is_file():
            return None
        sha = hashlib.sha256()
        before = path.stat()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                check_cancel(ctx['cancel_check'])
                sha.update(block)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('读取期间输入文件发生变化，请重新创建批次')
        return dict(path=str(path.resolve()), size=after.st_size,
                    mtime_ns=after.st_mtime_ns, sha256=sha.hexdigest())

    def evaluation_files(ctx, demand_id=None):
        from .city_workflow import normalize, sources
        options = ctx['options']['evaluation']
        params = normalize(options.get('parameters'))
        selected = {key: options.get('input_files', {}).get(key)
                    for key in ('isochrone', 'tci')}
        return sources(data, city['id'], demand_id,
                       params['isochrone']['max_time'], selected)

    def fingerprint(ctx):
        from .custom_inputs import source_directory_override
        from .city_demand import population_file
        check_cancel(ctx['cancel_check'])
        expected = ctx['options']['inputs']
        if ('custom_input_version' in expected and
                expected['custom_input_version'] != city.get('custom_input_version')):
            raise ValueError('当前城市输入版本已改变，请创建新批次')
        roots = source_directory_override(city, bus, poi)
        network = roots['bus_dir'] / city['id']
        paths = list(sorted(network.glob('*_bus_*.*')))
        if city.get('poi_file'):
            paths.append(roots['poi_dir'] / city['poi_file'])
        population = population_file(data, city['id'])
        if population:
            paths.append(population)
        # Restored service outputs are derived from this immutable attachment.
        if city.get('pending_service_csv'):
            paths.append(data / city['pending_service_csv'])
        else:
            paths += [data / 'processed' / city['id'] / 'service' / name
                      for name in ('bus_headways_by_period.csv', 'service_manifest.json')]
        chosen = evaluation_files(ctx)
        paths += [path for path in chosen.values() if path]
        # Code changes also invalidate continuation, not just source-data changes.
        code = Path(__file__).parent
        paths += sorted(path for path in code.rglob('*.py')
                        if not any(part.startswith('.') for part in path.relative_to(code).parts))
        # Only source identity and preprocessing semantics belong in the snapshot.
        # UI readiness/count/result flags can change without changing raw inputs.
        identity = {key: deepcopy(city.get(key)) for key in (
            'id', 'name', 'province', 'city_code', 'center', 'poi_file',
            'poi_format', 'poi_coordinate_system', 'custom_input_version',
        )}
        return dict(city=identity, roots={key: str(value.resolve()) for key, value in roots.items()},
                    input_files={key: path.name if path else None for key, path in chosen.items()},
                    files={str(path.resolve()): digest(path, ctx) for path in paths})

    def check_prepared(ctx):
        prepared = ctx['state'].get('prepare', {})
        expected = prepared.get('artifacts')
        if not expected:
            raise ValueError('缺少已准备输入的指纹，不能继续批任务')
        for name, snapshot in expected.items():
            if digest(data / name, ctx) != snapshot:
                raise ValueError('已准备输入发生变化，请创建新批次：' + name)

    def committed(ctx):
        return deepcopy(ctx['step'].get('checkpoint', {}).get('result'))

    def commit(ctx, result):
        ctx['checkpoint']({'run_id': result.get('run_id'), 'result': result})
        return result

    def prepare(ctx):
        from .local_api import (prepare_city_network, prepare_city_poi,
                                cached_city_network, cached_city_poi)
        check_cancel(ctx['cancel_check'])
        options = ctx['options']['inputs']
        processed = data / 'processed'
        ctx['progress']('准备公交线网')
        network = (prepare_city_network(city, bus, processed, progress=ctx['progress'])
                   if options.get('network', True) else cached_city_network(city, bus, processed))
        check_cancel(ctx['cancel_check'])
        ctx['progress']('准备本市POI')
        poi_result = (prepare_city_poi(city, poi, processed,
                        progress=lambda scanned, kept: ctx['progress'](f'筛选POI：{scanned} / {kept}'))
                      if options.get('poi', True) else cached_city_poi(city, poi, processed))
        if not network or not poi_result:
            raise ValueError('跳过准备的输入尚无有效缓存，请勾选线网和POI准备')
        files = [processed / city['id'] / name for name in ('poi_clustered.csv', 'poi_manifest.json')]
        files += sorted((processed / city['id'] / 'network').glob('*'))
        artifacts = {path.relative_to(data).as_posix(): digest(path, ctx)
                     for path in files if path.is_file()}
        # Record absence too, so a later manual service upload invalidates reuse.
        artifacts.update({
            (path := processed / city['id'] / 'service' / name).relative_to(data).as_posix(): digest(path, ctx)
            for name in ('bus_headways_by_period.csv', 'service_manifest.json')
        })
        return dict(city_id=city['id'], network=network, poi=poi_result, artifacts=artifacts)

    def demand(ctx):
        from .city_demand import build
        check_prepared(ctx)
        previous = committed(ctx)
        if previous:
            return previous
        check_cancel(ctx['cancel_check'])
        result = build(city, data, ctx['options']['demand'].get('parameters'), ctx['progress'])
        return commit(ctx, result)

    def assignment(ctx):
        from .assignment.run_city import run_city
        from . import local_api
        if (data.resolve(), bus.resolve(), poi.resolve()) != (
                local_api.DATA.resolve(), local_api.BUS.resolve(), local_api.POI.resolve()):
            raise ValueError('批任务目录必须与local_api模型目录一致')
        check_prepared(ctx)
        options, step = ctx['options']['assignment'], ctx['step']
        previous = committed(ctx)
        if previous:
            return previous
        check_cancel(ctx['cancel_check'])
        result = run_city(city['id'], step['period'], options['poi_count'], step['total_demand'],
                          options.get('assumed_headway_min'),
                          default_headway_min=options.get('default_headway_min', 15),
                          parameters=options.get('parameters'), demand_run_id=ctx['demand_run_id'],
                          progress=ctx['progress'], cancel_check=ctx['cancel_check'],
                          resume_run_id=ctx['resume_run_id'],
                          on_run_created=lambda identity: ctx['checkpoint']({'run_id': identity}))
        if result.get('hourly_demand_pax') != step['total_demand']:
            raise ValueError('模型结果总需求与本批时段配置不一致')
        return commit(ctx, result)

    def evaluation(ctx):
        from .city_workflow import compute
        check_prepared(ctx)
        previous = committed(ctx)
        if previous:
            return previous
        chosen = evaluation_files(ctx, ctx['demand_run_id'])
        check_cancel(ctx['cancel_check'])
        result = compute(city, data, ctx['demand_run_id'],
                         ctx['options']['evaluation'].get('parameters'), ctx['progress'],
                         input_files={key: path.name if path else None for key, path in chosen.items()})
        layers = set(result.get('available_layers', []))
        scope = result.get('source_scope', {})
        stats = result.get('source_stats', {})
        navigation = stats.get('navigation_collection', {})
        collection = stats.get('collection_metadata') or {}
        sessions = set()
        if chosen['tci']:
            raw = json.loads(chosen['tci'].read_text(encoding='utf-8-sig'))
            records = raw.get('records', []) if isinstance(raw, dict) else raw
            session_periods = {'morning': 'am_peak', 'midday': 'offpeak', 'evening': 'pm_peak'}
            sessions = {session_periods.get(row.get('session'), row.get('session')) for row in records}
        missing_periods = [row['period'] for row in ctx['options']['periods']
                           if row['period'] not in sessions]
        missing = sorted({'coverage', 'isochrone', 'tci', 'composite', 'imbalance'} - layers)
        partial = bool(missing or missing_periods or collection.get('complete') is False or
                       stats.get('tci', {}).get('excluded_records') or scope.get('pilot_only') or scope.get('failed_records') or
                       scope.get('legacy_policy_assumption') or
                       scope.get('scored_grid_count', 0) < scope.get('demand_grid_count', 0) or
                       stats.get('incomplete_time_band_origins') or
                       navigation.get('subset_only') or navigation.get('complete') is False)
        result.update(status='partial' if partial else 'completed', missing_layers=missing,
                      missing_periods=missing_periods)
        if partial:
            result['warnings'] = list(result.get('warnings', [])) + [
                '批任务已执行完毕，但评价输入缺失或仅为样本；不是完整评价。']
        return commit(ctx, result)

    return dict(fingerprint=fingerprint, prepare=prepare, demand=demand,
                assignment=assignment, evaluation=evaluation)


