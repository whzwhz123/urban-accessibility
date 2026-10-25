import csv
import json
import math
from pathlib import Path
from openpyxl import load_workbook
from pyproj import Transformer

def gcj02_to_wgs84(lon, lat):
    """GCJ-02 → WGS-84（迭代法，精度 < 1m）"""
    a = 6378245.0
    ee = 0.00669342162296594323

    def transform_lat(x, y):
        r = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
        r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
        r += (20.0*math.sin(y*math.pi) + 40.0*math.sin(y/3.0*math.pi)) * 2.0/3.0
        r += (160.0*math.sin(y/12.0*math.pi) + 320*math.sin(y*math.pi/30.0)) * 2.0/3.0
        return r

    def transform_lon(x, y):
        r = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
        r += (20.0*math.sin(6.0*x*math.pi) + 20.0*math.sin(2.0*x*math.pi)) * 2.0/3.0
        r += (20.0*math.sin(x*math.pi) + 40.0*math.sin(x/3.0*math.pi)) * 2.0/3.0
        r += (150.0*math.sin(x/12.0*math.pi) + 300.0*math.sin(x/30.0*math.pi)) * 2.0/3.0
        return r

    wgs_lon, wgs_lat = lon, lat
    for _ in range(10):
        dlat = transform_lat(wgs_lon - 105.0, wgs_lat - 35.0)
        dlon = transform_lon(wgs_lon - 105.0, wgs_lat - 35.0)
        radlat = wgs_lat / 180.0 * math.pi
        magic = math.sin(radlat)
        magic = 1 - ee * magic * magic
        sqrtmagic = math.sqrt(magic)
        dlat2 = (dlat * 180.0) / ((a*(1-ee)) / (magic*sqrtmagic) * math.pi)
        dlon2 = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * math.pi)
        wgs_lon -= (wgs_lon + dlon2) - lon
        wgs_lat -= (wgs_lat + dlat2) - lat
    return wgs_lon, wgs_lat


def metric_crs(city):
    zone = int((city['center'][0] + 180) // 6) + 1
    return 'EPSG:' + str((32600 if city['center'][1] >= 0 else 32700) + zone)
COLUMNS = ['cluster_id', 'source_id', 'name', 'class_1', 'class_2', 'lon', 'lat', 'x_m', 'y_m', 'city', 'province']
JIANGSU_COLUMNS = ['id', 'unused', 'name', 'type', 'address', 'location', 'typecode', 'pcode', 'pname', 'citycode', 'cityname', 'adcode', 'adname', 'tel']


def cached_city_poi(city, poi_dir: Path, output_root: Path):
    if not city['poi_available']:
        return None
    source = poi_dir / city['poi_file']
    output_dir = output_root / city['id']
    output = output_dir / 'poi_clustered.csv'
    manifest = output_dir / 'poi_manifest.json'
    if not output.is_file() or not manifest.is_file():
        return None
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    signature = {'source': source.name, 'size': source.stat().st_size,
                 'mtime_ns': source.stat().st_mtime_ns, 'city_code': city['city_code'], 'schema': 3, 'metric_crs': metric_crs(city)}
    return saved if saved.get('signature') == signature else None


def _city_key(value):
    return str(value or '').strip().removesuffix('市')


def _source_rows(path):
    if path.suffix == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            first = next(reader)
            if {'name', 'type', 'location', 'cityname'}.issubset(first):
                fields = first
                fmt = 'named_csv'
            else:
                fields = JIANGSU_COLUMNS
                fmt = 'jiangsu_headerless_csv'
                yield dict(zip(fields, first)), fmt
            for row in reader:
                yield dict(zip(fields, row)), fmt
    else:
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in book.worksheets:
                rows = sheet.values
                fields = [str(value or '') for value in next(rows)]
                if not {'name', 'type', 'location', 'cityname'}.issubset(fields):
                    raise ValueError(f'Unsupported Excel columns in {sheet.title}')
                for row in rows:
                    yield dict(zip(fields, row)), 'xlsx'
        finally:
            book.close()


def prepare_city_poi(city, poi_dir: Path, output_root: Path, progress=None):
    if not city['poi_available']:
        raise ValueError('The province POI source is missing')
    source = poi_dir / city['poi_file']
    signature = {'source': source.name, 'size': source.stat().st_size,
                 'mtime_ns': source.stat().st_mtime_ns, 'city_code': city['city_code'], 'schema': 3, 'metric_crs': metric_crs(city)}
    output_dir = output_root / city['id']
    output = output_dir / 'poi_clustered.csv'
    manifest_path = output_dir / 'poi_manifest.json'
    if output.is_file() and manifest_path.is_file():
        saved = json.loads(manifest_path.read_text(encoding='utf-8'))
        if saved.get('signature') == signature:
            return saved

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.csv.tmp')
    transformer = Transformer.from_crs('EPSG:4326', metric_crs(city), always_xy=True)
    scanned = kept = invalid_coordinates = 0
    source_format = ''
    try:
        with temporary.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS)
            writer.writeheader()
            for row, source_format in _source_rows(source):
                scanned += 1
                if progress and scanned % 50000 == 0:
                    progress(scanned, kept)
                if _city_key(row.get('cityname')) != _city_key(city['name']):
                    continue
                try:
                    lon_text, lat_text = str(row.get('location') or '').split(',', 1)
                    lon, lat = float(lon_text), float(lat_text)
                    if not (-180 <= lon <= 180 and -85 <= lat <= 85):
                        raise ValueError('invalid coordinate')
                except ValueError:
                    invalid_coordinates += 1
                    continue
                lon, lat = gcj02_to_wgs84(lon, lat)
                x_m, y_m = transformer.transform(lon, lat)
                category = str(row.get('type') or '').split(';')
                kept += 1
                writer.writerow({
                    'cluster_id': f"{city['id']}_{kept:09d}",
                    'source_id': row.get('id') or '',
                    'name': row.get('name') or '',
                    'class_1': category[0].strip() or '其他',
                    'class_2': category[1].strip() if len(category) > 1 else '',
                    'lon': lon, 'lat': lat,
                    'x_m': x_m, 'y_m': y_m,
                    'city': city['name'], 'province': city['province'],
                })
        if kept == 0:
            raise ValueError(f"No POI matched {city['name']} in {source.name}")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    result = {'signature': signature, 'city': city['name'], 'format': source_format,
              'scanned_rows': scanned, 'poi_count': kept, 'invalid_coordinates': invalid_coordinates,
              'output': output.relative_to(output_root).as_posix(),
              'metric_crs': metric_crs(city), 'source_coordinate_system': 'GCJ-02',
              'output_coordinate_system': 'WGS84'}
    manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if progress:
        progress(scanned, kept)
    return result
