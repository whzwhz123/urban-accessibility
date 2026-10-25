"""Reconstruct thesis sections 3.7 and 5.5 without overwriting historical outputs."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
from shapely import normalize, set_precision
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
COLORS = {'供给不足': [232, 76, 61], '供需匹配': [74, 222, 128], '供给过剩': [96, 165, 250], '低效区域': [136, 136, 136]}
INDICATORS = {'demand': 'demand_final', 'coverage': 'coverage_score', 'opportunity': 'iso_score_combined'}


def geometry_key(feature):
    # Six decimals reconcile JSON coordinate precision, not different spatial cells.
    return normalize(set_precision(shape(feature['geometry']), 1e-6)).wkb_hex


def load_indicator(path, field):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    index, duplicate_count = {}, 0
    for feature in data['features']:
        key = geometry_key(feature)
        value = feature['properties'].get(field)
        if field == 'iso_score_combined' and feature['properties'].get('has_isochrone') is False:
            value = None
        if value is not None:
            value = float(value)
            if not np.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f'{path}: {field} must be a finite normalized score in [0,100]')
        if key in index:
            duplicate_count += 1
            if index[key][1] != value:
                raise ValueError(f'{path}: conflicting values for the same grid; no averaging is allowed')
        else:
            index[key] = (feature, value)
    return index, {'source_records': len(data['features']), 'unique_grids': len(index), 'identical_duplicates_removed': duplicate_count}


def entropy_weights(matrix):
    matrix = np.asarray(matrix, dtype=float)
    if matrix.ndim != 2 or len(matrix) < 2 or not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError('Entropy weighting requires at least two units with finite nonnegative scores')
    sums = matrix.sum(axis=0)
    shares = np.divide(matrix, sums, out=np.zeros_like(matrix), where=sums > 0)
    logarithms = np.zeros_like(shares)
    np.log(shares, out=logarithms, where=shares > 0)
    entropy = -(shares * logarithms).sum(axis=0) / np.log(len(matrix))
    entropy[sums == 0] = 1.0
    differences = np.clip(1 - entropy, 0, 1)
    if differences.sum() <= 1e-12:
        raise ValueError('All indicators are constant; entropy weights are undefined')
    return differences / differences.sum(), entropy


def normalize_matrix(matrix, method='minmax'):
    matrix = np.asarray(matrix, dtype=float)
    if method == 'source':
        return matrix.copy(), None
    if method == 'minmax':
        low, high = matrix.min(axis=0), matrix.max(axis=0)
    elif method == 'percentile':
        low, high = np.percentile(matrix, [2, 98], axis=0)
    else:
        raise ValueError('Unknown normalization method')
    span = high - low
    normalized = np.divide(matrix - low, span, out=np.zeros_like(matrix), where=span > 0)
    return np.clip(normalized, 0, 1) * 100, {'low': low.tolist(), 'high': high.tolist()}


def evaluate_matrix(matrix, normalization='minmax'):
    normalized, bounds = normalize_matrix(matrix, normalization)
    weights, entropy = entropy_weights(normalized)
    composite = normalized @ weights
    # Historical program classifies supply using service indicators only, not demand.
    if weights[1:].sum() <= 1e-12:
        raise ValueError('Service indicators contain no variation; supply weights are undefined')
    supply_weights = weights[1:] / weights[1:].sum()
    supply = normalized[:, 1:] @ supply_weights
    return normalized, weights, entropy, composite, supply, supply_weights, bounds


def quadrant(demand, supply, demand_median, supply_median):
    # Ties are classified as high; zero-demand supply ratios remain undefined.
    if demand >= demand_median:
        return '供需匹配' if supply >= supply_median else '供给不足'
    return '供给过剩' if supply >= supply_median else '低效区域'


def rebuild(demand_path, coverage_path, opportunity_path, output_root, city='Beijing', footprint=None, paper=None, normalization='minmax'):
    paths = [Path(demand_path), Path(coverage_path), Path(opportunity_path)]
    indices, input_stats = [], {}
    for (name, field), path in zip(INDICATORS.items(), paths):
        index, stats = load_indicator(path, field)
        indices.append(index)
        input_stats[name] = {**stats, 'file': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'field': field}
    common = set.intersection(*(set(index) for index in indices))
    reference_stats = None
    if footprint:
        reference = json.loads(Path(footprint).read_text(encoding='utf-8-sig'))
        reference_keys = {geometry_key(feature) for feature in reference['features']}
        reference_stats = {'file': str(Path(footprint).resolve()), 'sha256': hashlib.sha256(Path(footprint).read_bytes()).hexdigest(), 'records': len(reference['features']), 'unique_grids': len(reference_keys), 'missing_from_common_inputs': len(reference_keys - common)}
        selected = common & reference_keys
    else:
        selected = common
    valid = [key for key in indices[0] if key in selected and all(index[key][1] is not None for index in indices)]
    matrix = np.array([[index[key][1] for index in indices] for key in valid], dtype=float)
    matrix, weights, entropy, scores, supplies, supply_weights, normalization_bounds = evaluate_matrix(matrix, normalization)
    demand_median, supply_median = float(np.median(matrix[:, 0])), float(np.median(supplies))
    features, rows = [], []
    for key, values, score, supply in zip(valid, matrix, scores, supplies):
        source = indices[0][key][0]
        grid_id = hashlib.sha256(key.encode()).hexdigest()[:20]
        status = quadrant(values[0], supply, demand_median, supply_median)
        properties = {**source['properties'], 'grid_id': grid_id, 'composite': float(score), 'd_norm': float(values[0]), 'c_norm': float(values[1]), 'i_norm': float(values[2]), 'supply': float(supply), 'supply_demand_ratio': float(supply / values[0]) if values[0] > 0 else None, 'ratio_defined': bool(values[0] > 0), 'status': status, 'fill_r': COLORS[status][0], 'fill_g': COLORS[status][1], 'fill_b': COLORS[status][2], 'alpha': 115}
        features.append({'type': 'Feature', 'geometry': source['geometry'], 'properties': properties})
        rows.append({name: properties[name] for name in ['grid_id', 'd_norm', 'c_norm', 'i_norm', 'composite', 'supply', 'supply_demand_ratio', 'ratio_defined', 'status']})
    assert len({geometry_key(f) for f in features}) == len(features)
    assert np.isclose(weights.sum(), 1) and np.allclose(scores, matrix @ weights)
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    output = Path(output_root) / city / run_id
    output.mkdir(parents=True, exist_ok=False)
    parameters = {'indicators': list(INDICATORS), 'normalization': normalization, 'normalization_bounds': normalization_bounds, 'normalization_reference': 'minmax matches observed historical program; percentile implements thesis equation 3-4; source keeps already normalized inputs', 'weighting': 'entropy; 0*ln(0)=0; zero columns have zero information weight', 'supply': 'service-only weighted coverage and opportunity; demand excluded, matching observed historical program', 'supply_weights': dict(zip(['coverage', 'opportunity'], supply_weights.tolist())), 'supply_demand_ratio': 'service-only supply / normalized demand; differs from thesis equation 3-20 using composite / demand', 'quadrant_threshold': 'sample medians; >= classified as high', 'geometry_key_precision_degrees': 1e-6, 'scope': 'historical unique-grid footprint, not verified administrative boundary' if footprint else 'all common valid grids', 'paper_sections': ['3.7', '5.5'], 'paper_pdf_pages': [47, 68]}
    if paper:
        parameters['paper_file'] = Path(paper).name
        parameters['paper_sha256'] = hashlib.sha256(Path(paper).read_bytes()).hexdigest()
    summary = {'run_id': run_id, 'city_id': city, 'method': 'three_indicator_entropy_unique_grid_v2', 'generated_at': datetime.now(timezone.utc).isoformat(), 'grid_count': len(features), 'common_input_grids': len(common), 'selected_grid_count': len(selected), 'excluded_missing_scores': len(selected) - len(valid), 'input_stats': input_stats, 'reference_footprint': reference_stats, 'weights': dict(zip(INDICATORS, weights.tolist())), 'supply_weights': dict(zip(['coverage', 'opportunity'], supply_weights.tolist())), 'entropies': dict(zip(INDICATORS, entropy.tolist())), 'demand_median': demand_median, 'supply_median': supply_median, 'category_counts': dict(Counter(row['status'] for row in rows)), 'score_mean': float(scores.mean()), 'score_std_population': float(scores.std()), 'undefined_supply_demand_ratios': sum(not row['ratio_defined'] for row in rows), 'parameters': parameters, 'limitations': ['Reconstruction from thesis, not recovered original source code.', 'Historical input indicators have not been recalculated or independently validated.', 'Weights and class counts are recomputed, not forced to match thesis reported values.', 'Default normalization and supply definition follow observed historical program, not all thesis narrative formulas.']}
    geojson = {'type': 'FeatureCollection', 'metadata': {'method': summary['method'], 'run_id': run_id, 'grid_count': len(features)}, 'features': features}
    for name, value in [('composite.geojson', geojson), ('summary.json', summary), ('parameters.json', parameters)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    with (output / 'grid_scores.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return output, summary


def main():
    parser = argparse.ArgumentParser()
    inputs = ROOT / 'public/data/output'
    parser.add_argument('--demand', type=Path, default=inputs / 'demand_grid_beijing.geojson')
    parser.add_argument('--coverage', type=Path, default=inputs / 'coverage_grid_beijing.geojson')
    parser.add_argument('--opportunity', type=Path, default=inputs / 'isochrone_grid_beijing.geojson')
    parser.add_argument('--footprint', type=Path, default=inputs / 'composite_xicheng.geojson')
    parser.add_argument('--all-common', action='store_true')
    parser.add_argument('--normalization', choices=['minmax', 'percentile', 'source'], default='minmax')
    parser.add_argument('--city', default='Beijing')
    parser.add_argument('--output-root', type=Path, default=ROOT / 'data/evaluation_runs')
    args = parser.parse_args()
    output, summary = rebuild(args.demand, args.coverage, args.opportunity, args.output_root, args.city, None if args.all_common else args.footprint, next(ROOT.glob('170*.pdf'), None), args.normalization)
    print(json.dumps({'output': str(output.resolve()), 'grid_count': summary['grid_count'], 'weights': summary['weights'], 'category_counts': summary['category_counts']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
