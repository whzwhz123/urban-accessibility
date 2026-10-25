"""Read-only package inspection; mappings are canonical field -> source field."""
import io
import json
import math
import zipfile
from pathlib import PurePosixPath

import geopandas as gpd
import pandas as pd
from pyproj import Transformer

from .custom_inputs import _members, _coordinate_spec, package_schema

KINDS = ('stops', 'segments', 'routes', 'poi')


def map_fields(frame, kind, field_mapping=None):
    mapping = (field_mapping or {}).get(kind, {})
    schema = package_schema()[kind]
    allowed = set(schema['required'] + schema.get('optional', []) + schema.get('alternative', []))
    selected = {target: source for target, source in mapping.items() if source}
    if set(selected) - allowed:
        raise ValueError(kind + '包含未知标准字段')
    if len(set(selected.values())) != len(selected):
        raise ValueError(kind + '同一源字段不能映射到多个标准字段')
    for target, source in selected.items():
        if source not in frame.columns:
            raise ValueError(f'{kind}源字段不存在：{source}')
        if target != source and target in frame.columns and target not in selected.values():
            raise ValueError(f'{kind}映射会覆盖已有字段：{target}')
    return frame.rename(columns={source: target for target, source in selected.items()}).copy()


def normalize_poi_fields(frame, category_mapping=None):
    frame = frame.copy()
    if {'type', 'location', 'cityname'}.issubset(frame.columns):
        xy = frame['location'].astype(str).str.split(',')
        frame['lon'] = xy.map(lambda parts: parts[0] if len(parts) == 2 else '')
        frame['lat'] = xy.map(lambda parts: parts[1] if len(parts) == 2 else '')
        categories = frame['type'].astype(str).str.split(';')
        frame['class_1'] = categories.str[0]
        frame['class_2'] = categories.map(lambda parts: parts[1] if len(parts) > 1 else '')
    if category_mapping and 'class_1' in frame:
        # Unmapped categories remain unchanged; never infer a category.
        frame['class_1'] = frame['class_1'].map(lambda value: category_mapping.get(str(value), value))
    return frame


def _read_frames(content):
    archive, infos = _members(content)
    frames, files = {}, {}
    try:
        names = [info.filename for info in infos if not info.is_dir()]
        for kind in KINDS:
            matches = [name for name in names if (
                PurePosixPath(name).name.lower() in ('poi.csv', 'pois.csv') if kind == 'poi' else
                PurePosixPath(name).suffix.lower() in ('.geojson', '.shp') and
                (PurePosixPath(name).stem.lower() == kind or
                 PurePosixPath(name).stem.lower().endswith('_bus_' + kind)))]
            if len(matches) != 1:
                raise ValueError('数据包必须包含唯一的' + kind + '图层')
            name = matches[0]
            files[kind] = name
            if kind == 'poi':
                frames[kind] = pd.read_csv(io.BytesIO(archive.read(name)), encoding='utf-8-sig', dtype=str, keep_default_na=False)
            elif name.lower().endswith('.geojson'):
                payload = json.loads(archive.read(name).decode('utf-8-sig'))
                features = payload.get('features', [])
                frames[kind] = gpd.GeoDataFrame.from_features(features) if features else gpd.GeoDataFrame({'geometry': []}, geometry='geometry')
            else:
                stem = str(PurePosixPath(name).with_suffix('')).casefold()
                companions = [item for item in names if str(PurePosixPath(item).with_suffix('')).casefold() == stem]
                for suffix in ('.dbf', '.shx'):
                    if not any(item.lower().endswith(suffix) for item in companions):
                        raise ValueError(name + '缺少' + suffix)
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, 'w') as subset:
                    for item in companions:
                        subset.writestr(PurePosixPath(item).name, archive.read(item))
                frames[kind] = gpd.read_file(io.BytesIO(buffer.getvalue()))
        return frames, files
    finally:
        archive.close()


def _disconnected(geometry):
    if geometry is None or geometry.is_empty or not geometry.is_valid or geometry.geom_type != 'MultiLineString':
        return False
    parts = list(geometry.geoms)
    reached, pending = {0}, [0]
    while pending:
        current = pending.pop()
        for index, part in enumerate(parts):
            if index not in reached and parts[current].intersects(part):
                reached.add(index)
                pending.append(index)
    return len(reached) != len(parts)


def _quality(frames, coordinate_system):
    declarations = coordinate_system if isinstance(coordinate_system, dict) else {kind: coordinate_system for kind in KINDS}
    layers = {}
    for kind, frame in frames.items():
        required = package_schema()[kind]['required']
        missing = [field for field in required if field not in frame]
        present = [field for field in required if field in frame]
        empty = pd.Series(False, index=frame.index)
        for field in present:
            empty |= frame[field].isna() | frame[field].astype(str).str.strip().eq('')
        duplicate_key = {'stops': ['route_id', 'sequence'], 'segments': ['s_stopid', 'e_stopid'], 'routes': ['route_id'], 'poi': ['id']}[kind]
        duplicates = int(frame.duplicated(duplicate_key).sum()) if set(duplicate_key) <= set(frame) else None
        invalid = None
        geometry_invalid = None
        if kind != 'poi':
            allowed = ('Point',) if kind == 'stops' else ('LineString', 'MultiLineString')
            geometry_invalid = int(sum(g is None or g.is_empty or not g.is_valid or g.geom_type not in allowed for g in frame.geometry))
        if declarations.get(kind):
            system, crs = _coordinate_spec(declarations[kind])
            converter = Transformer.from_crs(crs, 4326, always_xy=True).transform if system != 'GCJ02' else lambda x, y: (x, y)
            if kind == 'poi' and {'lon', 'lat'} <= set(frame):
                invalid = 0
                for x, y in zip(frame.lon, frame.lat):
                    try:
                        lon, lat = converter(float(x), float(y))
                        invalid += int(not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -85 <= lat <= 85))
                    except (ValueError, TypeError, OverflowError):
                        invalid += 1
            elif kind != 'poi':
                invalid = 0
                for geometry in frame.geometry:
                    try:
                        if geometry is None or geometry.is_empty:
                            invalid += 1
                            continue
                        from shapely.ops import transform
                        bounds = transform(converter, geometry).bounds
                        invalid += int(not (all(math.isfinite(v) for v in bounds) and -180 <= bounds[0] <= bounds[2] <= 180 and -85 <= bounds[1] <= bounds[3] <= 85))
                    except (ValueError, TypeError, OverflowError):
                        invalid += 1
        layers[kind] = {'rows': len(frame), 'missing_fields': missing,
                        'missing_field_rows': len(frame) if missing else int(empty.sum()),
                        'duplicate_rows': duplicates, 'invalid_coordinates': invalid,
                        'invalid_geometries': geometry_invalid}
    stops, segments = frames['stops'], frames['segments']
    dangling = missing_links = disconnected = None
    if {'stop_id'} <= set(stops) and {'s_stopid', 'e_stopid'} <= set(segments):
        known = set(stops.stop_id.astype(str).str.strip())
        starts = segments.s_stopid.astype(str).str.strip()
        ends = segments.e_stopid.astype(str).str.strip()
        dangling = int((~starts.isin(known) | ~ends.isin(known) | starts.eq(ends)).sum())
        if {'route_id', 'sequence'} <= set(stops):
            pairs = set(zip(starts, ends))
            ordered = stops.assign(_sequence=pd.to_numeric(stops.sequence, errors='coerce'))
            if ordered._sequence.notna().all() and not ordered.duplicated(['route_id', '_sequence']).any():
                missing_pairs = set()
                for _, part in ordered.groupby('route_id'):
                    ids = part.sort_values('_sequence').stop_id.astype(str).str.strip().tolist()
                    missing_pairs.update(pair for pair in zip(ids, ids[1:]) if pair not in pairs)
                missing_links = len(missing_pairs)
    disconnected = sum(_disconnected(geometry) for geometry in segments.geometry)
    return {'layers': layers, 'dangling_segments': dangling, 'missing_adjacent_segments': missing_links,
            'disconnected_multilines': disconnected,
            'broken_segments': None if dangling is None or missing_links is None else dangling + missing_links + disconnected,
            'notes': ['null表示缺少字段或坐标声明，未评估；重复按标准业务键统计多余记录。',
                      '断裂总数为悬空/自环路段、缺失有向邻接对、未连通多段线三项诊断之和，可能重叠；不代表几何端点贴合检查。']}


def inspect_package(content_bytes, *, field_mapping=None, category_mapping=None, coordinate_system=None):
    """Return JSON-safe fields/samples and full-row quality counts; no files written."""
    frames, files = _read_frames(content_bytes)
    layers = {}
    mapped = {}
    source_categories = []
    for kind, frame in frames.items():
        properties = frame.drop(columns=['geometry'], errors='ignore')
        samples = json.loads(properties.head(3).to_json(orient='records', force_ascii=False))
        mapped[kind] = map_fields(frame, kind, field_mapping)
        if kind == 'poi':
            raw_poi = normalize_poi_fields(mapped[kind])
            raw_counts = raw_poi.get('class_1', pd.Series(dtype=str)).astype(str).value_counts()
            source_categories = [{'value': key, 'count': int(value)} for key, value in raw_counts.items()]
            mapped[kind] = normalize_poi_fields(mapped[kind], category_mapping)
        layers[kind] = {'file': files[kind], 'fields': list(properties.columns), 'samples': samples, 'rows': len(frame),
                        'canonical': package_schema()[kind]}
    categories = mapped['poi'].get('class_1', pd.Series(dtype=str)).astype(str).value_counts()
    return {'source_poi_categories': source_categories, 'layers': layers, 'poi_categories': [{'value': key, 'count': int(value)} for key, value in categories.items()],
            'quality': _quality(mapped, coordinate_system)}
