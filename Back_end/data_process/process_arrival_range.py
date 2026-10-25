"""Import raw ArrivalRange records, preserving original files and collection provenance."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from Back_end.city_poi import gcj02_to_wgs84
from shapely.geometry import Polygon, MultiPolygon, mapping
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]


def is_point(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return False
    try:
        float(value[0]); float(value[1]); return True
    except (TypeError, ValueError):
        return False


def is_ring(value):
    return isinstance(value, list) and len(value) >= 3 and all(is_point(point) for point in value)


def convert_bounds(bounds):
    def ring(points):
        return [gcj02_to_wgs84(float(point[0]), float(point[1])) for point in points]
    def polygon(shell, holes=()):
        geometry = Polygon(ring(shell), [ring(hole) for hole in holes])
        return geometry if geometry.is_valid else geometry.buffer(0)
    if is_ring(bounds):
        polygons = [polygon(bounds)]
    elif isinstance(bounds, list) and bounds and all(is_ring(item) for item in bounds):
        polygons = [polygon(item) for item in bounds]
    elif isinstance(bounds, list) and bounds and all(isinstance(item, list) and item and all(is_ring(r) for r in item) for item in bounds):
        polygons = [polygon(item[0], item[1:]) for item in bounds]
    else:
        raise ValueError('Unknown bounds nesting or invalid coordinate ring')
    geometry = unary_union(polygons)
    if geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon') or not geometry.is_valid:
        raise ValueError('No valid polygon after coordinate conversion and geometry repair')
    return geometry


def process(source, output_root, limit=None):
    source = Path(source)
    raw = json.loads(source.read_text(encoding='utf-8-sig'))
    if raw.get('metadata', {}).get('bounds_crs', 'GCJ-02') != 'GCJ-02':
        raise ValueError('Input bounds must be GCJ-02; do not convert WGS84 twice')
    features, failures, seen = [], [], set()
    records = raw['features'][:limit] if limit else raw['features']
    for index, feature in enumerate(records):
        properties = dict(feature['properties'])
        try:
            if properties.get('status') != 'ok':
                raise ValueError(str(properties.get('error') or 'source_status_not_ok'))
            if properties.get('policy', 'BUS') != 'BUS' or properties.get('bounds_crs', 'GCJ-02') != 'GCJ-02':
                raise ValueError('Expected BUS policy and GCJ-02 bounds')
            origin = [float(properties['wgsLon']), float(properties['wgsLat'])]
            identity = (*origin, int(properties['minute']))
            if identity in seen:
                raise ValueError('Duplicate origin and minute; resolve source records before import')
            geometry = convert_bounds(properties.get('bounds'))
            seen.add(identity)
            properties.pop('bounds', None)
            properties.update(origin_wgs84=origin, bounds_crs='WGS84', source_bounds_crs='GCJ-02', source_record=index)
            features.append({'type': 'Feature', 'geometry': mapping(geometry), 'properties': properties})
        except (KeyError, TypeError, ValueError) as error:
            failures.append({'source_record': index, 'grid_id': properties.get('grid_id'), 'error': str(error)})
    output = Path(output_root) / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    summary = {'source_file': str(source.resolve()), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'source_records': len(raw['features']), 'processed_records': len(records), 'valid_polygons': len(features), 'sdk_compatibility_records': sum(f['properties'].get('sdk_status_compatibility') == 'polylines_status_bug' for f in features), 'failed_records': len(failures), 'subset_only': len(records) != len(raw['features']) or bool(raw.get('metadata', {}).get('subset_only')), 'collection_metadata': raw.get('metadata'), 'legacy_policy_assumption': any('policy' not in f['properties'] for f in records), 'geometry_crs': 'WGS84', 'source_bounds_crs': 'GCJ-02', 'all_processed_records_valid': len(failures) == 0}
    for name, value in [('isochrone_polygons.geojson', {'type': 'FeatureCollection', 'metadata': summary, 'features': features}), ('summary.json', summary), ('failed_records.json', failures)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return output, summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, default=ROOT / 'data/isochrone_imports')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    output, summary = process(args.input, args.output_root, args.limit)
    print(json.dumps({'output': str(output.resolve()), **summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
