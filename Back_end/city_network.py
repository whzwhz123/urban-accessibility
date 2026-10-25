import json
from collections import Counter
from pathlib import Path

import geopandas as gpd
import pandas as pd
from scipy.spatial import cKDTree
from shapely.ops import linemerge, unary_union
from .city_poi import metric_crs


def _label(chinese, english):
    text = '' if pd.isna(chinese) else str(chinese).strip()
    return text if text and '\ufffd' not in text else str(english or '').strip()


def _clusters(xy, radius_m):
    if len(xy) < 2:
        return list(range(len(xy)))
    parent = list(range(len(xy)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left, right in cKDTree(xy).query_pairs(radius_m):
        parent[root(right)] = root(left)
    return [root(index) for index in range(len(xy))]


def _vertices(geometry):
    if geometry is None or geometry.is_empty:
        return 0
    if geometry.geom_type == 'LineString':
        return len(geometry.coords)
    if geometry.geom_type == 'MultiLineString':
        return sum(len(part.coords) for part in geometry.geoms)
    return 0


def _source_signature(paths):
    return [[path.name, path.stat().st_size, path.stat().st_mtime_ns]
            for stem in paths for path in sorted(stem.parent.glob(f'{stem.stem}.*'))]


def cached_city_network(city, bus_dir: Path, output_root: Path):
    folder = bus_dir / city['id']
    stops_path = next(folder.glob('*_bus_stops.shp'))
    segments_path = next(folder.glob('*_bus_segments.shp'))
    signature = {'inputs': _source_signature((stops_path, segments_path)), 'schema': 3, 'metric_crs': metric_crs(city)}
    output_dir = output_root / city['id'] / 'network'
    manifest = output_dir / 'network_manifest.json'
    expected = [output_dir / name for name in
                ('bus_stations_merged.csv', 'bus_stopid_to_station.csv', 'bus_edges_topology.csv',
                 'bus_route_readiness.csv', 'bus_headway_template.csv')]
    if not manifest.is_file() or not all(path.is_file() for path in expected):
        return None
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    return saved if saved.get('signature') == signature else None


def prepare_city_network(city, bus_dir: Path, output_root: Path, progress=None):
    local_crs = metric_crs(city)
    folder = bus_dir / city['id']
    stops_path = next(folder.glob('*_bus_stops.shp'))
    segments_path = next(folder.glob('*_bus_segments.shp'))
    signature = {'inputs': _source_signature((stops_path, segments_path)), 'schema': 3, 'metric_crs': metric_crs(city)}
    output_dir = output_root / city['id'] / 'network'
    manifest_path = output_dir / 'network_manifest.json'
    expected = [output_dir / name for name in
                ('bus_stations_merged.csv', 'bus_stopid_to_station.csv', 'bus_edges_topology.csv',
                 'bus_route_readiness.csv', 'bus_headway_template.csv')]
    if manifest_path.is_file() and all(path.is_file() for path in expected):
        saved = json.loads(manifest_path.read_text(encoding='utf-8'))
        if saved.get('signature') == signature:
            return saved

    if progress:
        progress('读取站点和路段')
    stops = gpd.read_file(stops_path).to_crs(4326)
    segments = gpd.read_file(segments_path).to_crs(4326)
    stop_xy = stops.to_crs(local_crs).geometry
    stops['lon'] = stops.geometry.x
    stops['lat'] = stops.geometry.y
    stops['x_m'] = stop_xy.x
    stops['y_m'] = stop_xy.y
    stops['stop_id'] = stops.stop_id.astype(str)
    stops['display_name'] = [_label(cn, en) for cn, en in zip(stops.name_cn, stops.name_en)]
    stops['display_route'] = [_label(cn, en) for cn, en in zip(stops.route_cn, stops.route_en)]

    if progress:
        progress('按站点 ID 归并')
    stop_records = []
    dropped_stop_rows = 0
    for stop_id, group in stops.groupby('stop_id', sort=True):
        labels = _clusters(group[['x_m', 'y_m']].to_numpy(), 300.0)
        winning = Counter(labels).most_common(1)[0][0]
        selected = group.iloc[[index for index, label in enumerate(labels) if label == winning]]
        dropped_stop_rows += len(group) - len(selected)
        name = selected.display_name.mode().iloc[0]
        stop_records.append({
            'stop_id': stop_id, 'name': name,
            'name_key': name.casefold().replace(' ', '') or stop_id,
            'lon': selected.lon.mean(), 'lat': selected.lat.mean(),
            'x_m': selected.x_m.mean(), 'y_m': selected.y_m.mean(),
            'route_ids': '|'.join(sorted(set(selected.route_id.dropna().astype(str)))),
            'route_names': '|'.join(sorted(set(selected.display_route.dropna().astype(str)))),
        })
    by_id = pd.DataFrame(stop_records)

    if progress:
        progress('归并同名相邻站点')
    stations = []
    mapping = []
    for _, group in by_id.groupby('name_key', sort=True):
        labels = _clusters(group[['x_m', 'y_m']].to_numpy(), 300.0)
        group = group.assign(group_label=labels)
        for _, part in group.groupby('group_label', sort=True):
            station_id = f'S{len(stations) + 1:06d}'
            ids = sorted(part.stop_id.astype(str))
            route_ids = sorted({item for value in part.route_ids for item in value.split('|') if item})
            names = sorted({item for value in part.route_names for item in value.split('|') if item})
            stations.append({
                'station_id': station_id, 'station_name': part.name.mode().iloc[0],
                'lon': part.lon.mean(), 'lat': part.lat.mean(),
                'x_m': part.x_m.mean(), 'y_m': part.y_m.mean(),
                'n_member_stop_ids': len(ids), 'n_routes': len(route_ids),
                'member_stop_ids': '|'.join(ids), 'route_ids': '|'.join(route_ids),
                'route_names': '|'.join(names),
            })
            mapping.extend({'stop_id': stop_id, 'station_id': station_id} for stop_id in ids)
    stations_df = pd.DataFrame(stations)
    mapping_df = pd.DataFrame(mapping)
    stop_to_station = dict(zip(mapping_df.stop_id, mapping_df.station_id))

    if progress:
        progress('清理异常路段并建立拓扑')
    segments['geom_len_km'] = segments.to_crs(local_crs).geometry.length / 1000.0
    segments['vertices'] = segments.geometry.map(_vertices)
    bad = (segments.vertices <= 2) & (segments.geom_len_km >= 2.0)
    cleaned = segments.loc[~bad].copy()
    cleaned['from_station'] = cleaned.s_stopid.astype(str).map(stop_to_station)
    cleaned['to_station'] = cleaned.e_stopid.astype(str).map(stop_to_station)
    cleaned['distance_km'] = pd.to_numeric(cleaned['distance'], errors='coerce')
    cleaned['distance_km'] = cleaned['distance_km'].where(cleaned['distance_km'] > 0, cleaned['geom_len_km'])
    mapped = cleaned.dropna(subset=['from_station', 'to_station', 'distance_km'])
    mapped = mapped[(mapped.from_station != mapped.to_station) & (mapped['distance_km'] > 0)]
    station_lookup = stations_df.set_index('station_id')
    edges = []
    for (start, end), group in mapped.groupby(['from_station', 'to_station'], sort=True):
        geoms = [geometry for geometry in group.geometry if geometry is not None and not geometry.is_empty]
        geometry = unary_union(geoms) if geoms else None
        if geometry is not None and geometry.geom_type == 'MultiLineString':
            geometry = linemerge(geometry)
        counts = pd.to_numeric(group['num'], errors='coerce').fillna(0)
        edges.append({
            'from_station': start, 'to_station': end, 'raw_segment_count': len(group),
            'distance_km_mean': group['distance_km'].mean(),
            'distance_km_median': group['distance_km'].median(),
            'distance_km_min': group['distance_km'].min(),
            'line_count_sum': counts.sum(), 'line_count_max': counts.max(),
            'line_count_mean': counts.mean(),
            'geom_len_km_mean': group.geom_len_km.mean(),
            'geometry_wkt': geometry.wkt if geometry is not None else '',
            'from_lon': station_lookup.loc[start, 'lon'],
            'from_lat': station_lookup.loc[start, 'lat'],
            'from_name': station_lookup.loc[start, 'station_name'],
            'to_lon': station_lookup.loc[end, 'lon'],
            'to_lat': station_lookup.loc[end, 'lat'],
            'to_name': station_lookup.loc[end, 'station_name'],
        })
    edges_df = pd.DataFrame(edges)
    valid_edges = set(zip(edges_df.from_station, edges_df.to_station))
    route_rows = []
    route_stops = stops[['route_id', 'display_route', 'stop_id', 'sequence']].merge(
        mapping_df, on='stop_id', how='left'
    )
    for (route_id, route_name), group in route_stops.groupby(['route_id', 'display_route'], sort=True):
        group = group.sort_values(['sequence', 'stop_id'])
        if group.station_id.isna().any():
            status = 'missing_station'
            station_ids = []
        elif group.groupby('sequence').station_id.nunique().gt(1).any():
            status = 'sequence_conflict'
            station_ids = []
        else:
            station_ids = []
            for station_id in group.station_id:
                if not station_ids or station_ids[-1] != station_id:
                    station_ids.append(station_id)
            status = 'ready' if len(station_ids) >= 2 else 'too_few_stations'
            if status == 'ready' and any(
                (start, end) not in valid_edges
                for start, end in zip(station_ids[:-1], station_ids[1:])
            ):
                status = 'missing_physical_edge'
        route_rows.append({
            'route_id': route_id, 'route_name': route_name,
            'station_count': len(station_ids),
            'route_section_count': max(len(station_ids) - 1, 0),
            'topology_status': status,
        })
    routes_df = pd.DataFrame(route_rows)
    headway_template = routes_df[['route_id', 'route_name', 'topology_status']].copy()
    headway_template['peak_headway_min'] = ''
    headway_template['flat_headway_min'] = ''

    output_dir.mkdir(parents=True, exist_ok=True)
    routes_df.to_csv(expected[3], index=False, encoding='utf-8')
    headway_template.to_csv(expected[4], index=False, encoding='utf-8-sig')
    stations_df.to_csv(expected[0], index=False, encoding='utf-8')
    mapping_df.to_csv(expected[1], index=False, encoding='utf-8')
    edges_df.to_csv(expected[2], index=False, encoding='utf-8')
    result = {
        'signature': signature, 'city': city['name'], 'metric_crs': local_crs,
        'raw_stop_rows': len(stops), 'unique_stop_ids': len(by_id),
        'dropped_stop_rows': dropped_stop_rows, 'merged_stations': len(stations_df),
        'raw_segments': len(segments), 'bad_straight_segments': int(bad.sum()),
        'mapped_segments': len(mapped), 'mapped_segment_share': len(mapped) / max(len(cleaned), 1),
        'topology_edges': len(edges_df),
        'route_directions': len(routes_df),
        'route_directions_ready': int((routes_df.topology_status == 'ready').sum()),
        'route_directions_missing_edge': int((routes_df.topology_status == 'missing_physical_edge').sum()),
    }
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if progress:
        progress('完成')
    return result
