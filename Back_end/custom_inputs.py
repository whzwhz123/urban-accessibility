"""User packages: real source geometries, explicit CRS, versioned computation inputs."""
import csv
import hashlib
import io
import json
import math
import re
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

import geopandas as gpd
import pandas as pd
from pyproj import CRS, Transformer
from shapely.geometry import Point, mapping
from shapely.ops import transform

from .city_poi import COLUMNS, gcj02_to_wgs84, metric_crs

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 200
SCHEMA_VERSION = 1


def package_schema():
    return {
        'schema': SCHEMA_VERSION,
        'files': ['stops.geojson', 'segments.geojson', 'routes.geojson', 'poi.csv'],
        'stops': {'required': ['stop_id', 'route_id', 'sequence', 'name_cn', 'route_cn'],
                  'optional': ['name_en', 'route_en'], 'geometry': 'Point',
                  'note': '每个线路方向的每个站序一行；sequence为非负整数，同一route_id内唯一'},
        'segments': {'required': ['s_stopid', 'e_stopid'],
                     'optional': ['distance', 'num'], 'geometry': 'LineString或MultiLineString',
                     'note': '有向物理路段；distance单位公里，缺省按真实几何计算；num缺省按经过的线路方向数计算'},
        'routes': {'required': ['route_id', 'route_cn'], 'optional': ['route_en'],
                   'geometry': 'LineString或MultiLineString',
                   'note': 'CPTOND SHP允许无route_id，但必须用route_cn/route_en与站点方向唯一关联；线路几何不代替站序'},
        'poi': {'required': ['id', 'name', 'class_1', 'lon', 'lat'], 'optional': ['class_2'],
                'alternative': ['id', 'name', 'type', 'location', 'cityname'],
                'note': 'UTF-8 CSV；投影CRS时lon/lat分别填X/Y；旧高德表须包含cityname且全部属于所选城市'},
        'coordinate_system': '必填WGS84、GCJ02或投影EPSG代码；可传字典分别指定stops/segments/routes/poi。投影坐标不得标成WGS84。',
        'shapefile': '支持*_bus_stops/segments/routes.shp及配套.shx/.dbf； .prj如存在必须与声明一致；缺失时使用明确声明的CRS。仅同一包内的这些文件参与处理。',
        'limits': {'upload_bytes': MAX_UPLOAD_BYTES, 'expanded_bytes': MAX_EXPANDED_BYTES,
                   'members': MAX_MEMBERS},
        'example_only': True,
    }


def build_template_package():
    """Bytes for GET custom-input-template (application/zip); not research data."""
    stops = []
    for index, lon in enumerate((116.4, 116.405, 116.41), 1):
        stops.append({'type': 'Feature', 'properties': {
            'stop_id': f'EXAMPLE_S{index}', 'route_id': 'EXAMPLE_R1', 'sequence': index,
            'name_cn': f'示例站点{index}_非研究数据', 'name_en': f'EXAMPLE_STOP_{index}',
            'route_cn': '示例线路_非研究数据', 'route_en': 'EXAMPLE_ONLY'},
            'geometry': mapping(Point(lon, 39.9))})
    segments = [{'type': 'Feature', 'properties': {
        's_stopid': f'EXAMPLE_S{i}', 'e_stopid': f'EXAMPLE_S{i+1}'},
        'geometry': {'type': 'LineString', 'coordinates': [[116.4+(i-1)*.005, 39.9],
                      [116.4025+(i-1)*.005, 39.9001], [116.4+i*.005, 39.9]]}}
        for i in (1, 2)]
    routes = [{'type': 'Feature', 'properties': {'route_id': 'EXAMPLE_R1',
               'route_cn': '示例线路_非研究数据', 'route_en': 'EXAMPLE_ONLY'},
               'geometry': {'type': 'LineString', 'coordinates': [[116.4,39.9], [116.405,39.9], [116.41,39.9]]}}]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for kind, features in [('stops', stops), ('segments', segments), ('routes', routes)]:
            archive.writestr(kind+'.geojson', json.dumps({'type': 'FeatureCollection',
                'features': features}, ensure_ascii=False))
        archive.writestr('poi.csv', '\ufeffid,name,class_1,class_2,lon,lat\nEXAMPLE_P1,示例POI_非研究数据,其他,,116.401,39.9\nEXAMPLE_P2,示例POI_非研究数据,其他,,116.409,39.9\n')
        archive.writestr('schema.json', json.dumps({**package_schema(), 'template_coordinate_system': 'WGS84'}, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _members(content):
    if not isinstance(content, bytes) or not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError('ZIP为空或超过100 MiB上传限制')
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise ValueError('上传文件不是有效ZIP数据包') from error
    seen, total = set(), 0
    try:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError('ZIP文件数量超过200个')
        for info in infos:
            name = info.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            if not parts or name.startswith('/') or any(p in ('.', '..') or ':' in p for p in parts):
                raise ValueError('ZIP含不安全路径：'+info.filename)
            if any(p.endswith((' ', '.')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', p, re.I) for p in parts):
                raise ValueError('ZIP含不安全Windows文件名：'+info.filename)
            if name.casefold() in seen:
                raise ValueError('ZIP含重复文件路径：'+info.filename)
            seen.add(name.casefold())
            if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                raise ValueError('ZIP不允许符号链接或加密文件')
            total += info.file_size
            if total > MAX_EXPANDED_BYTES or info.file_size > MAX_EXPANDED_BYTES:
                raise ValueError('ZIP解压大小超过512 MiB')
        return archive, infos
    except Exception:
        archive.close()
        raise


def _coordinate_spec(value):
    text = str(value or '').strip().upper().replace('-', '')
    if text in ('WGS84', 'EPSG:4326', '4326'):
        return 'WGS84', CRS.from_epsg(4326)
    if text == 'GCJ02':
        return 'GCJ02', None
    try:
        crs = CRS.from_user_input(value)
    except Exception as error:
        raise ValueError('必须声明WGS84、GCJ02或有效的投影EPSG坐标系') from error
    if not crs.is_projected:
        raise ValueError('除WGS84/GCJ02外仅支持明确的投影CRS')
    return crs.to_string(), crs


def _gcj(lon, lat, z=None):
    # GCJ offset applies only in mainland coordinate coverage, not globally.
    x, y = gcj02_to_wgs84(lon, lat) if 72.004 <= lon <= 137.8347 and .8293 <= lat <= 55.8271 else (lon, lat)
    return (x, y) if z is None else (x, y, z)


def _required(frame, fields, label):
    missing = sorted(set(fields)-set(frame.columns))
    if missing:
        raise ValueError(label+'缺少必需字段：'+', '.join(missing))
    for field in fields:
        if frame[field].isna().any() or frame[field].astype(str).str.strip().eq('').any():
            raise ValueError(label+'字段不能为空：'+field)


def _find(folder, kind):
    candidates = [p for p in folder.rglob('*') if p.is_file() and
                  p.suffix.lower() in ('.geojson', '.shp') and
                  (p.stem.lower() == kind or p.stem.lower().endswith('_bus_'+kind))]
    if len(candidates) != 1:
        raise ValueError(f'数据包必须包含唯一的{kind}.geojson或*_bus_{kind}.shp')
    path = candidates[0]
    if path.suffix.lower() == '.shp':
        for ext in ('.shx', '.dbf'):
            if not path.with_suffix(ext).is_file():
                raise ValueError(path.name+'缺少配套文件'+ext)
    return path


def _read_layer(path, declaration, kind):
    system, declared_crs = _coordinate_spec(declaration)
    frame = gpd.read_file(path)
    stored = frame.crs.to_string() if frame.crs else None
    # A GeoJSON without explicit legacy crs is assigned 4326 by GDAL even for X/Y.
    explicit = path.suffix.lower() == '.shp' or bool(json.loads(path.read_text(encoding='utf-8-sig')).get('crs'))
    if explicit and frame.crs is not None:
        expected = CRS.from_epsg(4326) if system == 'GCJ02' else declared_crs
        if not CRS(frame.crs).equals(expected, ignore_axis_order=True):
            raise ValueError(kind+'文件CRS与所声明坐标系不一致')
    allowed = ('Point',) if kind == 'stops' else ('LineString', 'MultiLineString')
    if frame.empty or frame.geometry.isna().any() or frame.geometry.is_empty.any() or not frame.geometry.geom_type.isin(allowed).all() or not frame.geometry.is_valid.all():
        raise ValueError(kind+'必须包含非空、有效的'+ '/'.join(allowed)+'真实几何')
    if system == 'GCJ02':
        frame.geometry = frame.geometry.map(lambda geometry: transform(_gcj, geometry))
        frame = frame.set_crs(4326, allow_override=True)
    else:
        frame = frame.set_crs(declared_crs, allow_override=True).to_crs(4326)
    bounds = frame.total_bounds
    if not all(math.isfinite(v) for v in bounds) or not (-180 <= bounds[0] <= bounds[2] <= 180 and -85 <= bounds[1] <= bounds[3] <= 85):
        raise ValueError(kind+'坐标超出经纬度范围，请核对声明的CRS')
    return frame, {'declared': system, 'file_crs': stored, 'output_crs': 'EPSG:4326'}


def _read_poi(path, declaration, city):
    try:
        frame = pd.read_csv(path, encoding='utf-8-sig', dtype=str, keep_default_na=False)
    except (ValueError, UnicodeError) as error:
        raise ValueError('POI必须是带表头的UTF-8 CSV') from error
    if {'name', 'type', 'location', 'cityname'}.issubset(frame.columns):
        _required(frame, ['id', 'name', 'type', 'location', 'cityname'], 'POI')
        if not frame.cityname.str.strip().str.removesuffix('市').eq(city['name'].strip().removesuffix('市')).all():
            raise ValueError('POI cityname必须全部属于所选城市，不会静默筛除外市记录')
        xy = frame.location.str.split(',', expand=True)
        if xy.shape[1] != 2:
            raise ValueError('POI location必须为两个逗号分隔坐标')
        frame['lon'], frame['lat'] = xy[0], xy[1]
        categories = frame.type.str.split(';')
        frame['class_1'] = categories.str[0]
        frame['class_2'] = categories.map(lambda parts: parts[1] if len(parts)>1 else '')
    _required(frame, ['id', 'name', 'class_1', 'lon', 'lat'], 'POI')
    if frame.empty or frame.id.duplicated().any():
        raise ValueError('POI不能为空且id必须唯一')
    try:
        xy = frame[['lon', 'lat']].astype(float)
    except ValueError as error:
        raise ValueError('POI lon/lat必须为数值坐标') from error
    if not xy.map(math.isfinite).all().all():
        raise ValueError('POI坐标必须为有限数值')
    system, crs = _coordinate_spec(declaration)
    converter = Transformer.from_crs(crs, 4326, always_xy=True).transform if crs else _gcj
    converted = [converter(x, y) for x, y in xy.to_numpy()]
    frame['lon'], frame['lat'] = zip(*converted)
    if not all(math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -85 <= y <= 85 for x,y in converted):
        raise ValueError('POI坐标超出范围，请核对坐标系统')
    if 'class_2' not in frame:
        frame['class_2'] = ''
    return frame[['id', 'name', 'class_1', 'class_2', 'lon', 'lat']], {'declared': system, 'output_crs': 'EPSG:4326'}


def _normalize_network(layers, city):
    stops, segments, routes = (layers[k] for k in ('stops', 'segments', 'routes'))
    _required(stops, ['stop_id', 'route_id', 'sequence', 'name_cn', 'route_cn'], '站点')
    _required(segments, ['s_stopid', 'e_stopid'], '路段')
    for key in ('stop_id', 'route_id'):
        stops[key] = stops[key].astype(str).str.strip()
    for key in ('s_stopid', 'e_stopid'):
        segments[key] = segments[key].astype(str).str.strip()
    try:
        sequence = pd.to_numeric(stops.sequence)
    except ValueError as error:
        raise ValueError('站点sequence必须为非负整数') from error
    if not sequence.map(math.isfinite).all() or (sequence < 0).any() or (sequence % 1 != 0).any():
        raise ValueError('站点sequence必须为非负整数')
    stops['sequence'] = sequence.astype('int64')
    if stops.duplicated(['route_id', 'sequence']).any():
        raise ValueError('同一route_id下sequence重复，请为上下行使用不同方向ID')
    for key in ('name_en', 'route_en'):
        if key not in stops:
            stops[key] = ''
        if key not in routes:
            routes[key] = ''
    if 'route_id' not in routes:
        if 'route_cn' not in routes:
            raise ValueError('CPTOND线路缺少route_cn，无法关联站点中的方向ID')
        names = stops[['route_id', 'route_cn', 'route_en']].drop_duplicates()
        routes = routes.merge(names, on=['route_cn', 'route_en'], how='left')
    _required(routes, ['route_id', 'route_cn'], '线路')
    routes['route_id'] = routes.route_id.astype(str).str.strip()
    if set(routes.route_id) != set(stops.route_id):
        raise ValueError('线路route_id与站点route_id不一致，或CPTOND线路名称未匹配')
    known = set(stops.stop_id)
    if not set(segments.s_stopid).union(segments.e_stopid) <= known:
        raise ValueError('路段起终站ID未全部存在于站点stop_id中')
    if segments.s_stopid.eq(segments.e_stopid).any():
        raise ValueError('路段起终站ID不能相同')
    pairs = set(zip(segments.s_stopid, segments.e_stopid))
    route_counts = {}
    for route_id, part in stops.groupby('route_id'):
        if len(part)<2 or part.route_cn.nunique() != 1:
            raise ValueError('线路方向至少需要两站且route_cn必须一致：'+route_id)
        ordered = part.sort_values('sequence').stop_id.tolist()
        for pair in zip(ordered, ordered[1:]):
            if pair not in pairs:
                raise ValueError(f'线路{route_id}缺少相邻站的有向路段：{pair[0]} → {pair[1]}')
            route_counts.setdefault(pair, set()).add(route_id)
    lengths = segments.to_crs(metric_crs(city)).length/1000
    if (lengths <= 0).any():
        raise ValueError('路段真实几何长度必须大于零')
    for key, default in [('distance', lengths), ('num', pd.Series([len(route_counts.get(pair, set())) for pair in zip(segments.s_stopid, segments.e_stopid)], index=segments.index))]:
        if key not in segments:
            segments[key] = default
        try:
            values = pd.to_numeric(segments[key])
        except ValueError as error:
            raise ValueError('路段'+key+'必须为数值') from error
        if not values.map(math.isfinite).all() or (values <= 0).any():
            raise ValueError('路段'+key+'必须为正数；未使用路段请明确提供num')
        segments[key] = values
    # Only fields read by city_network/assignment are emitted; never invent geometry.
    stops['city_code'] = city['city_code']
    routes['city_code'] = city['city_code']
    normalized = {'stops': stops[['stop_id','route_id','sequence','name_cn','name_en','route_cn','route_en','city_code','geometry']],
                  'segments': segments[['s_stopid','e_stopid','distance','num','geometry']],
                  'routes': routes[['route_id','route_cn','route_en','city_code','geometry']]}
    return normalized


def import_package(city, data, content_bytes, filename, coordinate_system):
    """city must be an existing catalog record; data is the application's data root.

    Returns JSON-safe city + source_overrides. bus_dir is the network_root parent
    (the actual SHP folder is bus_dir/city['id']). No global catalog is mutated.
    """
    city_id = str(city.get('id', ''))
    if not re.fullmatch(r'[A-Za-z0-9_-]+', city_id) or not all(k in city for k in ('name','province','city_code','center')):
        raise ValueError('必须选择全国目录中已有城市，不能新增或杜撰cityid')
    if not str(filename).lower().endswith('.zip'):
        raise ValueError('只支持ZIP数据包')
    declarations = coordinate_system if isinstance(coordinate_system, dict) else {k: coordinate_system for k in ('stops','segments','routes','poi')}
    for kind in ('stops','segments','routes','poi'):
        _coordinate_spec(declarations.get(kind))
    archive, infos = _members(content_bytes)
    version = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid4().hex[:8]
    root = Path(data).resolve()/'custom_inputs'/city_id/version
    original = root/'original'
    manifest = {'schema': SCHEMA_VERSION, 'version': version, 'city_id': city_id,
                'status': 'importing', 'filename': Path(str(filename)).name,
                'source_sha256': hashlib.sha256(content_bytes).hexdigest(), 'source_files': {},
                'source_crs': {}, 'output_crs': 'EPSG:4326'}
    try:
        original.mkdir(parents=True, exist_ok=False)
        (original/'package.zip').write_bytes(content_bytes)
        extracted = original/'extracted'
        extracted.mkdir()
        for info in infos:
            path = extracted/PurePosixPath(info.filename.replace('\\','/'))
            if not path.resolve().is_relative_to(extracted.resolve()):
                raise ValueError('ZIP路径逃逸')
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            # Stream extraction enforces the actual expanded size too.
            size = 0
            with archive.open(info) as source, path.open('xb') as destination:
                for chunk in iter(lambda: source.read(1024*1024), b''):
                    size += len(chunk)
                    if size > info.file_size or size > MAX_EXPANDED_BYTES:
                        raise ValueError('ZIP实际解压大小超出限制')
                    destination.write(chunk)
            manifest['source_files'][path.relative_to(extracted).as_posix()] = _sha(path)
        layers = {}
        for kind in ('stops','segments','routes'):
            layers[kind], manifest['source_crs'][kind] = _read_layer(_find(extracted, kind), declarations[kind], kind)
        poi_paths = [p for p in extracted.rglob('*') if p.is_file() and p.name.lower() in ('poi.csv','pois.csv')]
        if len(poi_paths) != 1:
            raise ValueError('数据包必须包含唯一的poi.csv（或pois.csv）')
        poi, manifest['source_crs']['poi'] = _read_poi(poi_paths[0], declarations['poi'], city)
        network = _normalize_network(layers, city)
        bus_dir = root/'standardized'/'network_root'
        network_folder = bus_dir/city_id
        poi_dir = root/'standardized'/'poi'
        network_folder.mkdir(parents=True)
        poi_dir.mkdir(parents=True)
        for kind, frame in network.items():
            for field in frame.select_dtypes(include=['object']).columns:
                if frame[field].astype(str).str.encode('utf-8').str.len().gt(254).any():
                    raise ValueError(kind+'字段超过Shapefile 254字节限制：'+field)
            frame.to_file(network_folder/f'{city_id}_bus_{kind}.shp', driver='ESRI Shapefile', encoding='UTF-8', index=False)
        poi.to_csv(poi_dir/'poi_wgs84.csv', index=False, encoding='utf-8-sig')
        overrides = {'bus_dir': str(bus_dir), 'poi_dir': str(poi_dir)}
        updated_city = {**city, 'poi_available': True, 'poi_file': 'poi_wgs84.csv',
                        'poi_coordinate_system': 'WGS84', 'poi_format': 'custom_standard_v1',
                        'network_complete': True, 'route_count': len(network['routes']),
                        'network_prepared': False, 'poi_prepared': False,
                        'custom_input_version': version, 'custom_input_manifest': str(root/'manifest.json'),
                        'source_directory_override': overrides}
        warnings = []
        for geometry, length in zip(network['segments'].geometry, network['segments'].to_crs(metric_crs(city)).length):
            vertices = len(geometry.coords) if geometry.geom_type == 'LineString' else sum(len(g.coords) for g in geometry.geoms)
            if vertices <= 2 and length >= 2000:
                warnings.append('存在两顶点且长度不少于2公里的直线路段，现有city_network会剔除；请提供真实折线后再计算')
                break
        manifest.update(status='completed', city=updated_city, source_overrides=overrides,
                        counts={**{k:len(v) for k,v in network.items()}, 'poi':len(poi)}, warnings=warnings,
                        standardized_hashes={p.relative_to(root).as_posix(): _sha(p) for p in (root/'standardized').rglob('*') if p.is_file()})
        (root/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return {'version':version, 'city':updated_city, 'source_overrides':overrides,
                'manifest':str(root/'manifest.json'), 'counts':manifest['counts'], 'warnings':warnings,
                'prepared':False}
    except Exception as error:
        manifest.update(status='failed', error=str(error))
        if root.is_dir():
            (root/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        if isinstance(error, ValueError):
            raise
        raise ValueError('自有数据导入失败：'+str(error)) from error
    finally:
        archive.close()


def source_directory_override(city, bus_dir, poi_dir):
    """Shared resolver for source reads, including assignment's raw_stops lookup."""
    overrides = city.get('source_directory_override') or {}
    root = Path(bus_dir).resolve().parents[3]
    result = {}
    for key, fallback in [('bus_dir', bus_dir), ('poi_dir', poi_dir)]:
        path = Path(overrides.get(key, fallback))
        result[key] = path if path.is_absolute() else root / path
    return result


def _poi_signature(city, source):
    return {'schema': SCHEMA_VERSION, 'format':'custom_standard_v1',
            'source':str(source.resolve()), 'sha256':_sha(source),
            'custom_input_version':city['custom_input_version'], 'metric_crs':metric_crs(city)}


def cached_custom_city_poi(city, poi_dir, output_root):
    source = Path(poi_dir)/city['poi_file']
    folder = Path(output_root)/city['id']
    manifest = folder/'poi_manifest.json'
    if not source.is_file() or not (folder/'poi_clustered.csv').is_file() or not manifest.is_file():
        return None
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    return saved if saved.get('signature') == _poi_signature(city, source) else None


def prepare_custom_city_poi(city, poi_dir, output_root, progress=None):
    """Use instead of prepare_city_poi for poi_format=custom_standard_v1 only."""
    if city.get('poi_format') != 'custom_standard_v1' or city.get('poi_coordinate_system') != 'WGS84':
        raise ValueError('自有POI准备函数仅接受已标准化的WGS84版本')
    saved = cached_custom_city_poi(city, poi_dir, output_root)
    if saved:
        return saved
    source = Path(poi_dir)/city['poi_file']
    frame, _ = _read_poi(source, 'WGS84', city)
    transformer = Transformer.from_crs(4326, metric_crs(city), always_xy=True)
    frame['x_m'], frame['y_m'] = transformer.transform(frame.lon.to_numpy(), frame.lat.to_numpy())
    frame['cluster_id'] = [f"{city['id']}_{i:09d}" for i in range(1,len(frame)+1)]
    frame['source_id'] = frame['id']
    frame['city'], frame['province'] = city['name'], city['province']
    folder = Path(output_root)/city['id']
    folder.mkdir(parents=True, exist_ok=True)
    output = folder/'poi_clustered.csv'
    temporary = folder/('poi_clustered_'+uuid4().hex+'.tmp')
    frame[COLUMNS].to_csv(temporary, index=False, encoding='utf-8')
    temporary.replace(output)
    result = {'signature':_poi_signature(city, source), 'city':city['name'],
              'format':'custom_standard_v1', 'scanned_rows':len(frame), 'poi_count':len(frame),
              'invalid_coordinates':0, 'metric_crs':metric_crs(city),
              'source_coordinate_system':'WGS84', 'output_coordinate_system':'WGS84',
              'output':output.relative_to(output_root).as_posix()}
    (folder/'poi_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if progress:
        progress(len(frame), len(frame))
    return result

