"""Generate a resumable BUS-only ArrivalRange script for the official AMap demo."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from Back_end.city_poi import gcj02_to_wgs84
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]


def wgs84_to_gcj02(lon, lat):
    gx, gy = lon, lat
    for _ in range(10):
        wx, wy = gcj02_to_wgs84(gx, gy)
        gx += lon - wx
        gy += lat - wy
    return gx, gy


def make_config(grid_path, minutes=(45,), limit=None, interval_ms=700, timeout_ms=45000, max_attempts=3):
    path = Path(grid_path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    points, seen = [], set()
    for index, feature in enumerate(data['features']):
        properties = feature.get('properties', {})
        center = shape(feature['geometry']).centroid
        lon, lat = float(properties.get('cx', center.x)), float(properties.get('cy', center.y))
        grid_id = hashlib.sha256(f'{lon:.8f},{lat:.8f}'.encode()).hexdigest()[:20]
        if grid_id in seen:
            continue
        seen.add(grid_id)
        gx, gy = wgs84_to_gcj02(lon, lat)
        points.append({'grid_id': grid_id, 'grid_idx': index, 'wgs_lon': lon, 'wgs_lat': lat, 'gcj_lon': gx, 'gcj_lat': gy})
        if limit is not None and len(points) >= limit:
            break
    if not points or any(t <= 0 for t in minutes) or (limit is not None and limit < 1):
        raise ValueError('Nonempty grid, positive minutes and limit are required')
    signature = hashlib.sha256(json.dumps({'points': points, 'minutes': list(minutes), 'policy': 'BUS', 'schema': 2}, sort_keys=True).encode()).hexdigest()[:20]
    return {'collection_id': 'bus_' + signature, 'points': points, 'minutes': list(minutes), 'interval_ms': interval_ms, 'timeout_ms': timeout_ms, 'max_attempts': max_attempts, 'source_origin_count': len(data['features']), 'subset_only': limit is not None and len(points) < len(data['features'])}


def generate_script(config):
    template = Path(__file__).with_name('arrival_range_collector.js').read_text(encoding='utf-8')
    return template.replace('__ARRIVAL_CONFIG__', json.dumps(config, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    original_samples = ROOT / 'data/isochrone_45min_6districts.geojson'
    parser.add_argument('--grid', type=Path, default=original_samples if original_samples.is_file() else ROOT / 'public/data/output/isochrone_grid_beijing.geojson')
    parser.add_argument('--minutes', type=int, nargs='+', default=[45])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    config = make_config(args.grid, args.minutes, args.limit)
    output = args.output or ROOT / 'data/isochrone_scripts' / (config['collection_id'] + '.js')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generate_script(config), encoding='utf-8')
    print(json.dumps({'script': str(output.resolve()), 'collection_id': config['collection_id'], 'origins': len(config['points']), 'requests': len(config['points']) * len(config['minutes']), 'policy': 'BUS', 'execution': 'Official AMap ArrivalRange demo console; stop/export via urbanArrivalCollector'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
