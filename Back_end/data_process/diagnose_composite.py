"""Replay the historical intersection joins for diagnosis only; never use duplicated output as normal results."""
from collections import Counter, defaultdict
from datetime import datetime
import csv
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
from shapely.geometry import shape
from shapely.strtree import STRtree
from .rebuild_composite import ROOT, geometry_key, evaluate_matrix, quadrant


def diagnose():
    folder = ROOT / 'public/data/output'
    old = json.loads((folder / 'composite_xicheng.geojson').read_text(encoding='utf-8'))['features']
    groups = defaultdict(list)
    references = {}
    for feature in old:
        key = geometry_key(feature)
        groups[key].append(feature['properties'])
        references[key] = feature
    fields = ['demand_final', 'coverage_score', 'iso_score_combined']
    names = ['demand_grid_beijing', 'coverage_grid_beijing', 'isochrone_grid_beijing']
    sources = [json.loads((folder / (name + '.geojson')).read_text(encoding='utf-8'))['features'] for name in names]
    trees = [STRtree([shape(feature['geometry']) for feature in source]) for source in sources[:2]]
    opportunity = {geometry_key(feature): feature for feature in sources[2]}
    raw, geometry_ids, join_counts = [], [], Counter()
    for key, reference in references.items():
        geometry = shape(reference['geometry'])
        demand_matches = trees[0].query(geometry, predicate='intersects')
        coverage_matches = trees[1].query(geometry, predicate='intersects')
        join_counts[f'{len(demand_matches)}x{len(coverage_matches)}'] += 1
        for di in demand_matches:
            for ci in coverage_matches:
                raw.append([sources[0][di]['properties'][fields[0]], sources[1][ci]['properties'][fields[1]], opportunity[key]['properties'][fields[2]]])
                geometry_ids.append(key)
    matrix, weights, _, scores, supplies, supply_weights, bounds = evaluate_matrix(raw, 'minmax')
    replay = defaultdict(list)
    for key, values, score, supply in zip(geometry_ids, matrix, scores, supplies):
        replay[key].append((values, float(score), float(supply)))
    dmedian = float(np.median(matrix[:, 0])); smedian = float(np.median(supplies))
    rows, exact_groups = [], 0
    for key, historical in groups.items():
        stored = sorted(historical, key=lambda p: tuple(round(p[k], 8) for k in ['d_norm', 'c_norm', 'i_norm']))
        calculated = sorted(replay[key], key=lambda r: tuple(np.round(r[0], 8)))
        actual_matrix = Counter(tuple(round(p[k], 8) for k in ['d_norm', 'c_norm', 'i_norm']) for p in stored)
        replay_matrix = Counter(tuple(np.round(r[0], 8)) for r in calculated)
        exact_groups += actual_matrix == replay_matrix
        if len(stored) != len(calculated):
            raise ValueError('The reconstructed join does not match the stored record count')
        for old_row, (values, score, supply) in zip(stored, calculated):
            status = quadrant(values[0], supply, dmedian, smedian)
            rows.append({'geometry_id': key, 'historical_score': old_row['composite'], 'replayed_score': score, 'score_error': score - old_row['composite'], 'historical_supply': old_row['supply'], 'replayed_supply': supply, 'supply_error': supply - old_row['supply'], 'historical_status': old_row['status'], 'replayed_status': status, 'class_equal': status == old_row['status']})
    corrected_runs = [p for p in sorted((ROOT / 'data/evaluation_runs/Beijing').glob('*/summary.json'), reverse=True) if json.loads(p.read_text(encoding='utf-8'))['method'] == 'three_indicator_entropy_unique_grid_v2']
    if not corrected_runs:
        raise ValueError('Run the corrected rebuild first')
    corrected_summary = json.loads(corrected_runs[0].read_text(encoding='utf-8'))
    corrected = json.loads(corrected_runs[0].with_name('composite.geojson').read_text(encoding='utf-8'))['features']
    compare_rows = []
    for feature in corrected:
        key = geometry_key(feature); properties = feature['properties']; original = groups[key]
        compare_rows.append({'grid_id': properties['grid_id'], 'historical_mean_score': float(np.mean([p['composite'] for p in original])), 'corrected_score': properties['composite'], 'historical_mean_supply': float(np.mean([p['supply'] for p in original])), 'corrected_supply': properties['supply'], 'historical_class_count': len({p['status'] for p in original}), 'corrected_status': properties['status']})
    a = np.array([r['historical_mean_score'] for r in compare_rows]); b = np.array([r['corrected_score'] for r in compare_rows])
    summary = {'historical_records': len(old), 'historical_unique_grids': len(groups), 'intersection_match_counts': dict(join_counts), 'exact_replayed_indicator_groups': exact_groups, 'replayed_weights': weights.tolist(), 'replayed_supply_weights': supply_weights.tolist(), 'replayed_normalization_bounds': bounds, 'replayed_class_matches': sum(r['class_equal'] for r in rows), 'max_score_error': max(abs(r['score_error']) for r in rows), 'max_supply_error': max(abs(r['supply_error']) for r in rows), 'historical_grids_with_conflicting_classes': sum(len({p['status'] for p in group}) > 1 for group in groups.values()), 'corrected_run_id': corrected_summary['run_id'], 'corrected_weights': corrected_summary['weights'], 'corrected_supply_weights': corrected_summary['supply_weights'], 'corrected_unique_grids': len(corrected), 'corrected_vs_historical_mean_score_mae': float(np.mean(abs(a-b))), 'corrected_vs_historical_mean_score_rmse': float(np.sqrt(np.mean((a-b)**2))), 'corrected_vs_historical_mean_score_correlation': float(np.corrcoef(a,b)[0,1]), 'interpretation': 'Historical replay verifies the lost algorithm behavior, including the erroneous intersect join. Corrected results use one-to-one geometry matching; numerical identity with duplicated historical results is not a correctness criterion.'}
    assert summary['exact_replayed_indicator_groups'] == len(groups)
    assert summary['replayed_class_matches'] == len(old)
    assert summary['max_score_error'] < 1e-5 and summary['max_supply_error'] < 1e-5
    output = ROOT / 'data/evaluation_checks/Beijing' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    (output / 'comparison_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    for filename, records in [('historical_replay.csv', rows), ('unique_grid_comparison.csv', compare_rows)]:
        with (output / filename).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0])); writer.writeheader(); writer.writerows(records)
    print(json.dumps({'output': str(output), **summary}, ensure_ascii=False))


if __name__ == '__main__':
    diagnose()
