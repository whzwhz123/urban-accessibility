"""On-demand exports. No model execution, source dataset copying or checkpoints.

HTTP integration (handled by local_api's owner):
GET .../{runs|composite-runs}/{run_id}/export?format=zip|gpkg|shp&report=1
    package(DATA, city, run_id, kind, format=..., include_report=...)
Return Path as attachment; MIME: zip/shp application/zip, gpkg application/geopackage+sqlite3.
GET .../{runs|composite-runs}/{run_id}/report
    result_reports.generate_report(DATA, city, run_id, kind, comparison_ids=())
Return Path as text/html; charset=utf-8. Map ValueError to HTTP 400.
Only call generators on an explicit download/report request, never during run/list.
"""
import json
import re
import zipfile
from pathlib import Path
from uuid import uuid4

RUN_ID = re.compile(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}')
GRID_FILES = ('composite.geojson', 'demand_grid.geojson', 'coverage_grid.geojson',
              'opportunity_grid.geojson', 'tci_grid.geojson')
RESULT_FILES = ('summary.json', 'parameters.json', 'preflight_final.json',
                'route_section_flows.csv', 'physical_segment_flows.csv',
                'assignment_iterations.csv', 'capacity_reallocation.csv',
                'navigation_stability.json') + GRID_FILES


def run_folder(data, city, identity, kind='assignment'):
    if kind not in ('assignment', 'evaluation'):
        raise ValueError('结果类型无效')
    if not re.fullmatch(r'[\w-]+', city) or not RUN_ID.fullmatch(identity):
        raise ValueError('城市或结果版本无效')
    root = Path(data).resolve()
    folder = (root / ('runs' if kind == 'assignment' else 'evaluation_runs') / city / identity).resolve()
    folder.relative_to(root)
    if not (folder / 'summary.json').is_file():
        raise ValueError('结果不存在')
    return folder


def export_directory(data, city, identity, kind):
    # Unique directories prevent stale caches and concurrent writers; no files removed.
    folder = Path(data).resolve() / 'exports' / city / (identity + '_' + kind + '_' + uuid4().hex)
    folder.mkdir(parents=True)
    return folder


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def spatial_layers(folder, geometry_crs=None):
    import geopandas as gpd
    import pandas as pd
    from shapely import wkt
    layers = []
    physical = folder / 'physical_segment_flows.csv'
    if physical.is_file():
        frame = pd.read_csv(physical)
        if 'geometry_wkt' in frame:
            frame = frame.loc[frame.geometry_wkt.notna() & frame.geometry_wkt.str.strip().ne('')].copy()
            if not frame.empty:
                geometry = frame.pop('geometry_wkt').map(wkt.loads)
                # city_network converts source segments to EPSG:4326 before WKT export.
                layers.append(('physical_segments', gpd.GeoDataFrame(frame, geometry=geometry, crs=geometry_crs or 'EPSG:4326')))
    for name in GRID_FILES:
        path = folder / name
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
        features = [f for f in payload.get('features', []) if f.get('geometry')]
        if not features:
            continue
        declared = (payload.get('crs') or {}).get('properties', {}).get('name')
        # Workflow writes RFC 7946 longitude/latitude GeoJSON.
        layers.append((Path(name).stem, gpd.GeoDataFrame.from_features(features, crs=geometry_crs or declared or 'EPSG:4326')))
    return layers


def _write_spatial(folder, output, format, geometry_crs):
    layers = spatial_layers(folder, geometry_crs)
    if not layers:
        raise ValueError('没有可导出的几何结果')
    metadata = {'encoding': 'UTF-8', 'layers': {}, 'limitations': [
        '无几何的线路区段不伪造空间位置，仅保留原始 CSV（完整 ZIP）。',
        'SHP 字段名使用唯一短名；长文本可能受 DBF 254 字节限制，请优先使用 GeoPackage。',
        '不改变坐标位置；声明 CRS 不等于坐标转换。']}
    gpkg = output / 'results.gpkg'
    for name, frame in layers:
        frame = frame.copy()
        fields = [c for c in frame.columns if c != frame.geometry.name]
        mapping = {c: (f'f{i:08d}' if format == 'shp' else c) for i, c in enumerate(fields)}
        for column in fields:
            if frame[column].dtype == object:
                frame[column] = frame[column].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        # SHP cannot mix single and multi geometry families in arbitrary collections.
        if format == 'shp':
            families = frame.geometry.geom_type.map(lambda v: v.removeprefix('Multi'))
            if families.nunique() > 1 or 'GeometryCollection' in set(families):
                raise ValueError(f'{name} 包含混合几何，请改用 GeoPackage')
            frame = frame.rename(columns=mapping)
            frame.to_file(output / (name + '.shp'), driver='ESRI Shapefile', engine='pyogrio', encoding='UTF-8', index=False)
            (output / (name + '.cpg')).write_text('UTF-8', encoding='ascii')
        else:
            frame.to_file(gpkg, layer=name, driver='GPKG', engine='pyogrio', index=False)
        metadata['layers'][name] = {'geometryCRS': frame.crs.to_string(),
            'coordinate_order': 'x,y (longitude,latitude for EPSG:4326)',
            'feature_count': len(frame), 'fields': {v: k for k, v in mapping.items()},
            'units': {'*_pax_per_hour': '人次/小时', '*_vc': '无量纲', '*_km': '千米'},
            'crs_source': 'caller declaration' if geometry_crs else 'recorded GeoJSON CRS or workflow EPSG:4326 output contract'}
    _json(output / 'field_mapping.json', metadata)
    return gpkg, metadata


def package(data, city, identity, kind='assignment', *, format='zip', include_report=False,
            comparison_ids=(), geometry_crs=None):
    """Return newly generated attachment Path; no source_files/pickle are copied.

    format=zip includes CSV/JSON/grids, GeoPackage when geometry exists, images,
    and optional report.html. format=gpkg returns a GPKG; format=shp returns ZIP
    with field_mapping.json. To attach a report to gpkg use format=zip instead.
    """
    if format not in ('zip', 'gpkg', 'shp'):
        raise ValueError('导出格式无效')
    if format == 'gpkg' and include_report:
        raise ValueError('单独 GPKG 不能附 HTML，请选择完整 ZIP')
    folder = run_folder(data, city, identity, kind)
    output = export_directory(data, city, identity, kind)
    summary = json.loads((folder / 'summary.json').read_text(encoding='utf-8-sig'))
    files = []
    spatial = None
    if format != 'zip' or any((folder / n).is_file() for n in GRID_FILES + ('physical_segment_flows.csv',)):
        if spatial_layers(folder, geometry_crs):
            gpkg, spatial = _write_spatial(folder, output, 'shp' if format == 'shp' else 'gpkg', geometry_crs)
            if format == 'gpkg':
                return gpkg
            files.extend((p, p.name) for p in output.iterdir() if p.is_file())
        elif format != 'zip':
            raise ValueError('没有可导出的几何结果')
    if format == 'zip':
        files.extend((folder / name, 'results/' + name) for name in RESULT_FILES if (folder / name).is_file())
        demand_id=summary.get('demand_run_id')
        if demand_id and RUN_ID.fullmatch(str(demand_id)):
            demand=Path(data)/'demand_runs'/city/demand_id
            files.extend((demand/name,'shared_demand/'+name) for name in ('summary.json','parameters.json') if (demand/name).is_file())
        # Only existing result plots, not source datasets or executable SVG/HTML.
        files.extend((p, 'results/' + p.name) for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
    if include_report:
        from .result_reports import render_report
        report = output / 'report.html'
        report.write_text(render_report(data, city, identity, kind, comparison_ids=comparison_ids), encoding='utf-8')
        files.append((report, 'report.html'))
    manifest = {'city_id': city, 'run_id': identity, 'kind': kind, 'format': format,
        'demand_run_id': summary.get('demand_run_id'), 'spatial': spatial,
        'contents': [name for _, name in files] + ['manifest.json'],
        'limitations': summary.get('warnings', []), 'source_files': 'excluded; references retained in summary/parameters',
        'checkpoints': 'excluded', 'report_requested': include_report}
    attachment = output / 'results.zip'
    with zipfile.ZipFile(attachment, 'w', zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
        for path, name in files:
            archive.write(path, name)
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return attachment
