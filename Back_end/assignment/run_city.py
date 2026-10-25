from __future__ import annotations

import argparse
from dataclasses import asdict
import csv
import json
import time
from datetime import datetime
from pathlib import Path, PureWindowsPath
from uuid import uuid4
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from ..city_network import cached_city_network
from ..city_poi import cached_city_poi
from ..city_service import cached_city_service, resolve_headways
from ..custom_inputs import source_directory_override, cached_custom_city_poi
from ..local_api import BUS, BY_ID, DATA, POI
from .candidate import assign_msa_kpaths_optimized
from .capacity import capacity_filter
from .core import ExperimentConfig, _route_segment_flows
from .streaming import prepare_model_sparse
from .parameters import normalize_parameters
from ..task_store import (CheckpointStore, TaskCancelled, check_cancel, content_signature,
                          write_json, atomic_write, assignment_preflight)


RESULT_FILES = ('route_section_flows.csv', 'physical_segment_flows.csv',
                'assignment_iterations.csv', 'capacity_reallocation.csv', 'parameters.json')


def assignment_signature(city_id, cfg, headways_path, network, poi, shared=None, *, bus_dir=None):
    """Content-based signature, including resolved headways and solver code."""
    processed = DATA / 'processed' / city_id
    files = [('effective_headways', headways_path)]
    source_bus = Path(bus_dir) if bus_dir is not None else BUS
    for label, folder in (('processed_network', processed / 'network'), ('source_bus', source_bus / city_id)):
        for path in sorted(folder.glob('*')):
            if path.is_file() and path.suffix.lower() not in ('.pickle', '.pkl', '.tmp'):
                files.append((label + '/' + path.name, path))
    files.append(('poi_clustered.csv', processed / 'poi_clustered.csv'))
    backend = Path(__file__).resolve().parents[1]
    code = list((backend / 'assignment').glob('*.py'))
    code += [backend / name for name in ('task_store.py', 'city_network.py', 'city_poi.py', 'city_service.py', 'city_demand.py', 'custom_inputs.py')]
    files += [('code/' + path.relative_to(backend).as_posix(), path) for path in code]
    return content_signature({'city_id': city_id, 'parameters': asdict(cfg),
        'network': network, 'poi': poi, 'shared_demand': shared, 'source_bus': str(source_bus.resolve())}, files)


def _checkpoint_location(output, metadata):
    """Never follow a historical drive; migrated run-local checkpoints win."""
    local = output / 'checkpoints'
    if local.is_dir():
        return local
    stored = metadata.get('checkpoint_path', 'checkpoints')
    path = Path(stored)
    if path.is_absolute() or PureWindowsPath(stored).is_absolute():
        raise ValueError('Legacy checkpoint must be migrated to the current run/checkpoints')
    base = DATA if metadata.get('checkpoint_path_base') == 'data' else output
    resolved = (base / path).resolve()
    resolved.relative_to(base.resolve())
    if not resolved.is_dir():
        raise ValueError('Checkpoint directory is missing in the current project')
    return resolved


def _checkpoint_metadata(output, checkpoint_root):
    path = checkpoint_root.resolve()
    try:
        return {'checkpoint_path': path.relative_to(output.resolve()).as_posix(), 'checkpoint_path_base': 'output'}
    except ValueError:
        try:
            return {'checkpoint_path': path.relative_to(DATA.resolve()).as_posix(), 'checkpoint_path_base': 'data'}
        except ValueError as error:
            raise ValueError('Checkpoint directory must be inside DATA') from error

def _cached_assignment(output, signature):
    summary_path = output / 'summary.json'
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding='utf-8'))
        if summary.get('assignment_signature') != signature:
            return None
        files = [(name, output / name) for name in RESULT_FILES]
        if not all(path.is_file() for _, path in files):
            return None
        if summary.get('result_signature') != content_signature({}, files):
            return None
        return {**summary, 'cache_hit': True}
    except (OSError, ValueError):
        return None


def _assumed_headways(city_id: str, output: Path, minutes: float) -> Path:
    if not np.isfinite(minutes) or minutes <= 0:
        raise ValueError('Assumed headway must be positive and finite')
    template = DATA / 'processed' / city_id / 'network' / 'bus_headway_template.csv'
    output_path = output / 'assumed_uniform_headways.csv'
    with template.open(encoding='utf-8-sig', newline='') as source, output_path.open(
        'w', encoding='utf-8-sig', newline=''
    ) as destination:
        writer = csv.DictWriter(destination, fieldnames=[
            'route_id', 'peak_headway_min', 'flat_headway_min',
            'peak_headway_source', 'flat_headway_source',
        ])
        writer.writeheader()
        for row in csv.DictReader(source):
            writer.writerow({
                'route_id': row['route_id'],
                'peak_headway_min': minutes, 'flat_headway_min': minutes,
                'peak_headway_source': 'explicit_uniform_assumption',
                'flat_headway_source': 'explicit_uniform_assumption',
            })
    return output_path


def _flow_tables(model, desired: np.ndarray, served: np.ndarray):
    route = model.route_segments.copy()
    desired_section, _ = _route_segment_flows(model, desired)
    desired_section = np.maximum(desired_section, 0.0)
    served_section, _ = _route_segment_flows(model, served)
    served_section = np.maximum(served_section, 0.0)
    route['desired_flow_pax_per_hour'] = desired_section
    route['served_flow_pax_per_hour'] = served_section
    route['desired_vc'] = desired_section / route.capacity_per_hour.to_numpy(float)
    route['served_vc'] = served_section / route.capacity_per_hour.to_numpy(float)
    physical = model.topology.copy()
    index = route.physical_edge_idx.to_numpy(np.int32)
    count = len(physical)
    capacity = np.bincount(index, weights=route.capacity_per_hour.to_numpy(float), minlength=count)
    desired_flow = np.bincount(index, weights=desired_section, minlength=count)
    served_flow = np.bincount(index, weights=served_section, minlength=count)
    physical['capacity_pax_per_hour'] = capacity
    physical['desired_flow_pax_per_hour'] = desired_flow
    physical['served_flow_pax_per_hour'] = served_flow
    physical['desired_vc'] = np.divide(desired_flow, capacity, out=np.zeros(count), where=capacity > 0)
    physical['served_vc'] = np.divide(served_flow, capacity, out=np.zeros(count), where=capacity > 0)
    return route, physical


def run_city(city_id: str, period: str, poi_count: int, total_demand: float,
             assumed_headway_min: float | None = None, max_iterations: int = 500,
             msa_tolerance: float = 1e-4, progress=None, *, parameters: dict | None = None, demand_run_id: str | None = None, default_headway_min: float = 15,
             cancel_check=None, checkpoint_dir=None, resume_run_id=None, on_run_created=None) -> dict:
    check_cancel(cancel_check)
    settings = normalize_parameters({
        'max_iterations': max_iterations, 'relative_gap_tol': msa_tolerance,
        **(parameters or {}),
    })
    city = BY_ID.get(city_id)
    if city is None:
        raise ValueError(f'Unknown city: {city_id}')
    if period not in ('am_peak', 'offpeak', 'pm_peak'):
        raise ValueError('请选择早高峰、平峰或晚高峰')
    if poi_count < 2 or not np.isfinite(total_demand) or total_demand <= 0:
        raise ValueError('POI count and hourly demand must be positive')
    sources = source_directory_override(city, BUS, POI)
    bus_dir, poi_dir = sources['bus_dir'], sources['poi_dir']
    network = cached_city_network(city, bus_dir, DATA / 'processed')
    poi_loader = cached_custom_city_poi if city.get('poi_format') == 'custom_standard_v1' else cached_city_poi
    poi = poi_loader(city, poi_dir, DATA / 'processed')
    if not network or not poi:
        raise ValueError('Prepare city network and POI before assignment')
    if poi_count > poi['poi_count']:
        raise ValueError(f'Only {poi["poi_count"]} POIs are available')
    if (DATA / 'processed' / city_id / 'service/bus_headways_by_period.csv').is_file() and not cached_city_service(city, bus_dir, DATA / 'processed'):
        raise ValueError('上传间隔对应的线网已变化，请重新导入；未静默复用旧线路数据')
    # Resolve current effective values before cache/resume validation, without changing past runs.
    with TemporaryDirectory(prefix='assignment_headways_') as temporary:
        snapshot = Path(temporary)
        if assumed_headway_min is not None:
            headways_path = _assumed_headways(city_id, snapshot, assumed_headway_min)
            headway_provenance = {'headway_basis': 'explicit_uniform_assumption', 'assumed_headway_min': assumed_headway_min}
        else:
            headways_path, headway_provenance = resolve_headways(city_id, DATA / 'processed', snapshot, default_headway_min, period)
        headways_content = headways_path.read_bytes()
        headways_name = headways_path.name
    shared = None
    if demand_run_id:
        from ..city_demand import load_run, ensure_current_poi
        _, shared = load_run(DATA, city_id, demand_run_id)
        ensure_current_poi(DATA, shared)
    cfg = ExperimentConfig(
        period=period, sample_size=poi_count, random_seed=20260825,
        total_demand_pax_per_hour=total_demand, fallback_headway_min=default_headway_min, **settings,
        output_detail='compact',
        poi_category_weights=shared['parameters']['poi_weights'] if shared else None,
        od_period_weights=shared['parameters']['od_period_weights'] if shared else None,
    )
    with TemporaryDirectory(prefix='assignment_signature_') as temporary:
        current_headways = Path(temporary) / headways_name
        current_headways.write_bytes(headways_content)
        signature = assignment_signature(city_id, cfg, current_headways, network, poi, shared, bus_dir=bus_dir)
    check_cancel(cancel_check)
    run_root = DATA / 'runs' / city_id
    if resume_run_id:
        if Path(resume_run_id).name != resume_run_id or resume_run_id in ('.', '..'):
            raise ValueError('Invalid resume run id')
        run_id = resume_run_id
        output = run_root / run_id
        metadata = json.loads((output / 'run_state.json').read_text(encoding='utf-8'))
        if metadata['signature'] != signature:
            raise ValueError('Resume inputs or code version changed')
        if on_run_created:
            on_run_created(run_id)
        cached = _cached_assignment(output, signature)
        if cached:
            return cached
        if (output / 'summary.json').exists():
            raise ValueError('Completed run artifacts changed; start a new run instead of overwriting history')
        checkpoint_root = Path(checkpoint_dir) / city_id / run_id if checkpoint_dir else _checkpoint_location(output, metadata)
        _checkpoint_metadata(output, checkpoint_root)
    else:
        for previous in sorted(run_root.glob('*/summary.json'), reverse=True):
            cached = _cached_assignment(previous.parent, signature)
            if cached:
                if on_run_created:
                    on_run_created(cached['run_id'])
                return cached
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
        output = run_root / run_id
        output.mkdir(parents=True)
        checkpoint_root = Path(checkpoint_dir) / city_id / run_id if checkpoint_dir else output / 'checkpoints'
        write_json(output / 'run_state.json', {'run_id': run_id, 'signature': signature,
            **_checkpoint_metadata(output, checkpoint_root), 'trusted_local_pickle_only': True,
            'options': {'city_id': city_id, 'period': period, 'poi_count': poi_count, 'total_demand': total_demand,
                'assumed_headway_min': assumed_headway_min, 'default_headway_min': default_headway_min,
                'parameters': settings, 'demand_run_id': demand_run_id}})
        atomic_write(output / headways_name, headways_content)
        write_json(output / 'parameters.json', {**asdict(cfg), **headway_provenance})
        write_json(output / 'preflight.json', assignment_preflight(poi_count, settings))
        if on_run_created:
            on_run_created(run_id)
    store = CheckpointStore(checkpoint_root, signature)
    try:
        started = time.perf_counter()
        saved = store.load() if resume_run_id and (store.root / 'latest.json').is_file() else None
        if saved:
            model = saved['model']
            preparation_seconds = saved['preparation_seconds']
        else:
            if progress:
                progress('生成 OD 与候选路径')
            check_cancel(cancel_check)
            source_options = {}
            if city.get('source_directory_override'):
                source_options['raw_stops'] = next((bus_dir / city_id).glob('*_bus_stops.shp'))
            model = prepare_model_sparse(DATA, city_id, cfg, output / headways_name, **source_options)
            if int(model.summary.get('configured_fallback', 0)):
                raise RuntimeError('Some active routes fell back to the default headway; check the frequency CSV')
            preparation_seconds = time.perf_counter() - started
            store.save({'phase': 'prepared', 'model': model, 'solver': None, 'preparation_seconds': preparation_seconds})
            write_json(output / 'preflight_measured.json', assignment_preflight(poi_count, settings, model))
        if progress:
            progress('求解路径流量')
        check_cancel(cancel_check)
        if saved and saved['phase'] == 'assignment_done':
            desired, iteration_log, assignment = saved['result']
        else:
            def checkpoint(solver):
                store.save({'phase': 'assignment', 'model': model, 'solver': solver,
                            'preparation_seconds': preparation_seconds})
            desired, iteration_log, assignment = assign_msa_kpaths_optimized(model,
                cancel_check=cancel_check, checkpoint=checkpoint,
                resume_state=saved['solver'] if saved else None)
            store.save({'phase': 'assignment_done', 'model': model,
                'preparation_seconds': preparation_seconds, 'result': (desired, iteration_log, assignment)})
        if progress:
            progress('进行容量再分配')
        check_cancel(cancel_check)
        reallocation_started = time.perf_counter()
        (operator, served, unserved, _, desired_section, served_section,
         violation, reallocation_log, reallocation) = capacity_filter(model, desired, cancel_check=cancel_check)
        reallocation_seconds = time.perf_counter() - reallocation_started
        write_json(output / 'preflight_final.json', assignment_preflight(poi_count, settings, model))
        route, physical = _flow_tables(model, desired, served)
        balance_error = abs(float(operator.demand.sum()) - float(served.sum()) - float(unserved.sum()))
        if violation > 1e-9 or balance_error > 1e-8 * total_demand:
            raise RuntimeError('Capacity or demand conservation check failed')
        check_cancel(cancel_check)
        route.to_csv(output / 'route_section_flows.csv', index=False, encoding='utf-8-sig')
        physical.to_csv(output / 'physical_segment_flows.csv', index=False, encoding='utf-8-sig')
        iteration_log.to_csv(output / 'assignment_iterations.csv', index=False, encoding='utf-8-sig')
        reallocation_log.to_csv(output / 'capacity_reallocation.csv', index=False, encoding='utf-8-sig')
        summary = {
            'assignment_signature': signature, 'cache_hit': False,
            'run_id': run_id, 'city_id': city_id, 'city': city['name'], 'period': period,
            'poi_count': poi_count, 'hourly_demand_pax': total_demand,
            'poi_coordinate_system': poi.get('output_coordinate_system'),
            **headway_provenance,
            'assumed_headway_min': assumed_headway_min,
            'network_route_directions_ready': network['route_directions_ready'],
            'model_route_directions': int(model.summary['routes']),
            'candidate_od_pairs': int(model.summary['candidate_od_pairs']),
            'sampled_nonzero_od_pairs': int(model.summary['sampled_nonzero_od_pairs']),
            'candidate_paths': int(model.summary['candidate_paths']),
            'converged': assignment['converged'], 'relative_gap': float(assignment['relative_gap']),
            'assignment_iterations': int(assignment['iterations']),
            'preparation_seconds': preparation_seconds,
            'assignment_seconds': float(assignment['total_solver_seconds']),
            'reallocation_seconds': reallocation_seconds,
            'served_demand_pax_per_hour': float(served.sum()),
            'unserved_demand_pax_per_hour': float(unserved.sum()),
            'served_demand_share': float(served.sum() / operator.demand.sum()),
            'capacity_violation': float(violation), 'demand_balance_error': balance_error,
            'reallocation_rounds': int(reallocation['reallocation_rounds']),
            'physical_segments': len(physical),
            'output': output.relative_to(DATA).as_posix(),
            'algorithm': f'k{cfg.paths_per_transfer}_common_line_msa_plus_capacity_reallocation',
            'parameters': asdict(cfg), 'demand_run_id': demand_run_id, 'shared_demand_signature': shared['signature'] if shared else None,
            'service_status_basis': 'no_period_timetable; topology-ready routes treated as available except legacy night-name exclusion',
            'routes_with_unknown_service_status': int(model.summary.get('service_unknown_route_directions_retained', 0)),
        }
        summary['total_seconds'] = time.perf_counter() - started
        summary['result_signature'] = content_signature({}, [(name, output / name) for name in RESULT_FILES])
        write_json(output / 'summary.json', summary)
        if progress:
            progress('完成')
        return summary
    except TaskCancelled as error:
        error.run_id = run_id
        error.checkpoint_dir = str(checkpoint_root.resolve())
        error.output = str(output.resolve())
        raise



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--city', required=True)
    parser.add_argument('--period', choices=['am_peak', 'offpeak', 'pm_peak'], default='pm_peak')
    parser.add_argument('--poi-count', type=int, required=True)
    parser.add_argument('--total-demand', type=float, required=True)
    parser.add_argument('--assumed-headway-min', type=float)
    parser.add_argument('--default-headway-min', type=float, default=15)
    parser.add_argument('--max-iterations', type=int, default=500)
    parser.add_argument('--msa-tolerance', type=float, default=1e-4)
    args = parser.parse_args()
    result = run_city(args.city, args.period, args.poi_count, args.total_demand,
                      args.assumed_headway_min, args.max_iterations, args.msa_tolerance,
                      progress=lambda phase: print(phase, flush=True), default_headway_min=args.default_headway_min)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()