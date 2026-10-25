"""Import completed assignment tables without running the solver again."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd


def import_run(source: Path, output_root: Path, city_id='Beijing'):
    source = source.resolve()
    metrics_frame = pd.read_csv(source / 'run_summary.csv')
    metrics = dict(zip(metrics_frame.metric, metrics_frame.value))
    parameters = json.loads((source / 'parameters.json').read_text(encoding='utf-8'))
    config = parameters['experiment']
    run_id = source.name[:15] + '_' + hashlib.sha256(source.name.encode()).hexdigest()[:8]
    output = output_root / city_id / run_id
    if (output / 'summary.json').is_file():
        return json.loads((output / 'summary.json').read_text(encoding='utf-8'))

    def number(key, default=None):
        value = metrics.get(key)
        return float(value) if value is not None else default

    served = pd.read_csv(source / 'physical_segment_flows.csv').set_index('edge_idx')
    desired = pd.read_csv(source / 'desired_assignment' / 'physical_segment_flows.csv').set_index('edge_idx')
    if not served.index.is_unique or set(served.index) != set(desired.index):
        raise ValueError('Physical edge keys do not match')
    desired = desired.reindex(served.index)
    if not np.allclose(served.capacity_pax_per_hour, desired.capacity_pax_per_hour):
        raise ValueError('Desired and served capacities do not match')
    if not served.geometry_wkt.equals(desired.geometry_wkt):
        raise ValueError('Desired and served geometries do not match')
    physical = served.drop(columns=['flow_pax_per_hour', 'vc'])
    for variant, table in [('desired', desired), ('served', served)]:
        physical[f'{variant}_flow_pax_per_hour'] = table.flow_pax_per_hour
        physical[f'{variant}_vc'] = table.vc
    route = pd.read_csv(source / 'route_segment_flows.csv').set_index('segment_id')
    route_desired = pd.read_csv(source / 'desired_assignment' / 'route_segment_flows.csv').set_index('segment_id')
    if not route.index.is_unique or set(route.index) != set(route_desired.index):
        raise ValueError('Route section keys do not match')
    route_desired = route_desired.reindex(route.index)
    route['desired_flow_pax_per_hour'] = route_desired.onboard_flow_pax_per_hour
    route['served_flow_pax_per_hour'] = route.onboard_flow_pax_per_hour
    route['desired_vc'] = route_desired.vc
    route['served_vc'] = route.vc
    output.mkdir(parents=True, exist_ok=True)
    physical.reset_index().to_csv(output / 'physical_segment_flows.csv', index=False, encoding='utf-8-sig')
    route.reset_index().to_csv(output / 'route_section_flows.csv', index=False, encoding='utf-8-sig')
    shutil.copy2(source / 'desired_assignment' / 'iteration_log.csv', output / 'assignment_iterations.csv')
    shutil.copy2(source / 'capacity_reallocation_log.csv', output / 'capacity_reallocation.csv')
    shutil.copy2(source / 'parameters.json', output / 'parameters.json')
    shutil.copy2(source / 'run_summary.csv', output / 'source_run_summary.csv')
    summary = {
        'run_id': run_id, 'city_id': city_id, 'city': '北京市' if city_id == 'Beijing' else city_id,
        'period': config['period'], 'poi_count': int(number('sampled_pois', config['sample_size'])),
        'hourly_demand_pax': number('total_demand_pax_per_hour'),
        'poi_coordinate_system': None, 'coordinate_provenance': 'Source experiment does not explicitly record the coordinate conversion; original geometry preserved.',
        'headway_basis': 'observed_with_empirical_imputation',
        'headway_empirical_imputation_share': number('headway_empirical_imputation_share'),
        'assumed_headway_min': None, 'candidate_od_pairs': int(number('candidate_od_pairs', 0)),
        'sampled_nonzero_od_pairs': int(number('sampled_nonzero_od_pairs', number('feasible_od_pairs', 0))),
        'candidate_paths': int(number('candidate_paths', 0)), 'converged': metrics.get('converged'),
        'relative_gap': number('relative_gap'), 'assignment_iterations': int(number('iterations', 0)),
        'preparation_seconds': number('preparation_seconds'), 'assignment_seconds': number('assignment_seconds'),
        'reallocation_seconds': number('capacity_filter_seconds'), 'total_seconds': number('computational_pipeline_seconds'),
        'served_demand_pax_per_hour': number('served_demand_pax_per_hour'),
        'unserved_demand_pax_per_hour': number('unserved_demand_pax_per_hour'),
        'served_demand_share': number('served_demand_share'),
        'capacity_violation': number('maximum_relative_capacity_violation'),
        'demand_balance_error': number('demand_balance_error_pax_per_hour'),
        'reallocation_rounds': int(number('reallocation_rounds', 0)), 'physical_segments': len(physical),
        'algorithm': metrics.get('model_variant'), 'parameters': config,
        'source_experiment': source.name, 'imported_from': str(source), 'imported_result': True,
        'output': output.relative_to(output_root.parent).as_posix(),
    }
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--city', default='Beijing')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[2] / 'data' / 'runs')
    args = parser.parse_args()
    sources = [args.source] if (args.source / 'run_summary.csv').is_file() else sorted(
        folder for folder in args.source.iterdir() if folder.is_dir() and (folder / 'run_summary.csv').is_file())
    for source in sources:
        result = import_run(source, args.output, args.city)
        print(json.dumps({key: result[key] for key in ['run_id', 'poi_count', 'hourly_demand_pax', 'served_demand_share']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
