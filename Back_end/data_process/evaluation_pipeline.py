"""Import ArrivalRange, score sampled grids and rebuild evaluation without overwriting inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely import contains_xy, STRtree
from shapely.geometry import Point, shape

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from Back_end.data_process.evaluation_coordinates import gcj02_to_wgs84_arr
from Back_end.data_process.process_arrival_range import process
from Back_end.data_process.rebuild_composite import rebuild, geometry_key

ROOT = Path(__file__).resolve().parents[2]
POI_WEIGHTS = {"医疗保健": 3.0, "科教文化": 2.5, "购物消费": 2.0,
    "商务住宅": 2.0, "公司企业": 2.0, "运动健身": 1.5, "休闲娱乐": 1.5,
    "生活服务": 1.5, "金融机构": 1.2, "酒店住宿": 1.2, "旅游景点": 1.0,
    "餐饮美食": 1.0, "交通设施": 0.5, "汽车相关": 0.3}


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load_poi(path, cache_root):
    path = Path(path)
    signature = hashlib.sha256(path.read_bytes()).hexdigest()
    cache = Path(cache_root) / ('opportunity_poi_v1_' + signature[:20] + '.npz')
    if cache.is_file():
        with np.load(cache) as value:
            return value['xy'], value['weights'], signature
    frame = pd.read_csv(path, usecols=['经度', '纬度', '大类'], low_memory=False)
    lon = pd.to_numeric(frame['经度'], errors='coerce').to_numpy()
    lat = pd.to_numeric(frame['纬度'], errors='coerce').to_numpy()
    valid = np.isfinite(lon) & np.isfinite(lat) & (lon >= 115.4) & (lon <= 117.6) & (lat >= 39.4) & (lat <= 41.1)
    x, y = gcj02_to_wgs84_arr(lon[valid], lat[valid])
    weights = frame.loc[valid, '大类'].map(POI_WEIGHTS).fillna(1.0).to_numpy(dtype=float)
    xy = np.column_stack([x, y])
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, xy=xy, weights=weights)
    return xy, weights, signature


def score(polygons_path, grid_path, poi_path, output, cache_root, sigma=15.0, progress=lambda value: None):
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError('sigma must be positive and finite')
    polygons = json.loads(Path(polygons_path).read_text(encoding='utf-8'))
    grids = json.loads(Path(grid_path).read_text(encoding='utf-8-sig'))
    grid_tree = STRtree([shape(f['geometry']) for f in grids['features']])
    progress('读取并缓存历史POI坐标')
    xy, weights, poi_hash = load_poi(poi_path, cache_root)
    poi_tree = cKDTree(xy)
    features, iso_features, unmatched, seen = [], [], [], set()
    for index, feature in enumerate(polygons['features']):
        props = feature['properties']
        if int(props['minute']) != 45:
            continue
        origin = np.array(props['origin_wgs84'], dtype=float)
        matches = grid_tree.query(Point(origin), predicate='within')
        if len(matches) != 1:
            unmatched.append({'source_record': props['source_record'], 'grid_matches': len(matches)})
            continue
        grid = grids['features'][int(matches[0])]
        key = geometry_key(grid)
        if key in seen:
            raise ValueError('Multiple 45-minute origins in the same grid; do not silently aggregate')
        seen.add(key)
        polygon = shape(feature['geometry'])
        lowx, lowy, highx, highy = polygon.bounds
        midpoint = [(lowx + highx)/2, (lowy + highy)/2]
        candidates = poi_tree.query_ball_point(midpoint, np.hypot(highx-lowx, highy-lowy)/2 + 1e-12)
        candidates = np.asarray(candidates, dtype=int)
        inside = contains_xy(polygon, xy[candidates, 0], xy[candidates, 1])
        ids = candidates[inside]
        radius = max(highx-lowx, highy-lowy)/2
        if radius <= 0:
            raise ValueError('Degenerate isochrone bounds')
        # Preserve the old estimator; this is NOT a network travel-time observation.
        time_proxy = np.minimum(np.linalg.norm(xy[ids]-origin, axis=1)/radius, 1)*45.0
        raw_score = float(np.sum(weights[ids]*np.exp(-time_proxy**2/(2*sigma**2))))
        iso_index = len(iso_features)
        iso_features.append(feature)
        features.append({'type': 'Feature', 'geometry': grid['geometry'], 'properties': {
            **grid['properties'], 'iso_index': iso_index, 'has_isochrone': True,
            'iso_score_45m': raw_score, 'reachable_poi_count': len(ids), 'score_valid': True}})
        if index % 100 == 0:
            progress(f'机会评分 {index + 1}/{len(polygons["features"])}')
    if len(features) < 2:
        raise ValueError('At least two uniquely matched valid grids are needed; failed records are not zero scores')
    scores = np.array([f['properties']['iso_score_45m'] for f in features])
    low, high = np.percentile(scores, [2, 98])
    normalized = np.clip((scores-low)/(high-low), 0, 1)*100 if high > low else np.zeros_like(scores)
    for feature, value in zip(features, normalized):
        feature['properties'].update(iso_score_45m_norm=float(value), iso_score_combined=float(value))
    summary = {'valid_grids': len(features), 'unmatched_origins': unmatched, 'zero_opportunity_grids': int(np.sum(scores == 0)),
        'poi_count': len(xy), 'poi_file': str(Path(poi_path).resolve()), 'poi_sha256': poi_hash,
        'poi_input_crs': 'GCJ-02', 'geometry_crs': 'WGS84', 'sigma_minutes': sigma,
        'poi_weights': POI_WEIGHTS, 'default_poi_weight': 1.0, 'minutes': 45,
        'normalization': 'percentile 2/98 of valid sampled grids, including genuine zero scores',
        'normalization_bounds': [float(low), float(high)], 'grid_matching': 'unique grid containing origin; no nearest-grid substitution',
        'time_estimator': 'legacy angular-distance proxy: t=45*min(||poi-origin|| / (max(bbox width,height)/2),1)',
        'limitations': ['ArrivalRange provides a boundary, not individual POI travel times.',
            'Legacy angular-distance time proxy is approximate and is not measured transit time.',
            'Weights and normalization depend on the sampled evaluation scope.',
            'Uncollected and failed origins are omitted, not interpreted as zero opportunity.']}
    save(output/'opportunity_grid.geojson', {'type':'FeatureCollection', 'metadata':summary, 'features':features})
    save(output/'isochrone_polys.geojson', {'type':'FeatureCollection', 'features':iso_features})
    save(output/'opportunity_summary.json', summary)
    return summary


def run(source, data_root=None, sigma=15.0, progress=lambda value: None):
    data_root = Path(data_root or ROOT/'data')
    inputs = ROOT/'public/data/output'
    progress('转换等时圈坐标并检查采集状态')
    imported, import_summary = process(source, data_root/'isochrone_imports')
    summary = score(imported/'isochrone_polygons.geojson', inputs/'demand_grid_beijing.geojson',
        data_root/'Beijing_fixed.csv', imported, data_root/'evaluation_cache', sigma, progress)
    progress('重算综合评价与供需分类')
    output, result = rebuild(inputs/'demand_grid_beijing.geojson', inputs/'coverage_grid_beijing.geojson',
        imported/'opportunity_grid.geojson', data_root/'evaluation_runs', city='Beijing', normalization='minmax')
    for name in ['opportunity_grid.geojson', 'isochrone_polys.geojson', 'opportunity_summary.json', 'failed_records.json']:
        shutil.copy2(imported/name, output/name)
    metadata = import_summary.get('collection_metadata') or {}
    result.update(has_opportunity=True, pipeline='arrival_opportunity_composite_v1',
        source_scope={'record_count': import_summary['processed_records'], 'valid_polygons':import_summary['valid_polygons'],
            'failed_records':import_summary['failed_records'], 'sdk_compatibility_records':import_summary['sdk_compatibility_records'], 'valid_grids':summary['valid_grids'],
            'pilot_only': bool(metadata.get('subset_only') or import_summary['processed_records'] <= 5),
            'live_collection': metadata.get('source') == 'AMap.ArrivalRange',
            'legacy_policy_assumption':import_summary['legacy_policy_assumption']},
        opportunity_summary=summary, import_directory=str(imported.resolve()))
    result['limitations'][1] = 'Demand and coverage remain historical; opportunity was recalculated from the supplied isochrones.'
    save(output/'summary.json',result)
    parameters=json.loads((output/'parameters.json').read_text(encoding='utf-8'))
    parameters['opportunity']=summary
    parameters['arrival_import']=import_summary
    save(output/'parameters.json',parameters)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--data-root',type=Path,default=ROOT/'data')
    parser.add_argument('--sigma',type=float,default=15)
    args=parser.parse_args()
    result=run(args.input,args.data_root,args.sigma,lambda value: print(value,flush=True))
    print(json.dumps({'run_id':result['run_id'],'grid_count':result['grid_count'],'scope':result['source_scope']},ensure_ascii=False))


if __name__=='__main__':
    main()
