import csv
import hashlib
import io
import json
import math
from datetime import datetime
from uuid import uuid4
from pathlib import Path

from .city_network import cached_city_network


HEADWAY_COLUMNS = {'route_id', 'peak_headway_min', 'flat_headway_min'}


def cached_city_service(city, bus_dir: Path, output_root: Path):
    network = cached_city_network(city, bus_dir, output_root)
    manifest = output_root / city['id'] / 'service' / 'service_manifest.json'
    output = manifest.with_name('bus_headways_by_period.csv')
    if network is None or not manifest.is_file() or not output.is_file():
        return None
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    valid = saved.get('network_signature') == network['signature']
    valid = valid and saved.get('content_sha256') == hashlib.sha256(output.read_bytes()).hexdigest()
    return saved if valid else None


def _headway(value):
    text = str(value or '').strip()
    if not text:
        return ''
    number = float(text)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('发车间隔必须是大于零的分钟数')
    return number


def import_city_service(city, bus_dir: Path, output_root: Path, content: str):
    network = cached_city_network(city, bus_dir, output_root)
    if network is None:
        raise ValueError('请先准备该城市的线网')
    template = output_root / city['id'] / 'network' / 'bus_headway_template.csv'
    with template.open(encoding='utf-8-sig', newline='') as stream:
        catalog = {row['route_id']: row for row in csv.DictReader(stream)}
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    fields = set(reader.fieldnames or [])
    if 'route_id' not in fields or not ({'peak_headway_min', 'flat_headway_min'} <= fields or {'peak_frequency_per_hour', 'flat_frequency_per_hour'} <= fields):
        raise ValueError('CSV需包含route_id及高峰/平峰间隔（分钟）或frequency_per_hour（班次/小时）字段')
    values = {}
    for row in reader:
        route_id = str(row.get('route_id') or '').strip()
        if not route_id:
            continue
        if route_id not in catalog:
            raise ValueError(f'未知线路 ID：{route_id}')
        if route_id in values:
            raise ValueError(f'线路 ID 重复：{route_id}')
        intervals = []
        for period in ('peak', 'flat'):
            headway = _headway(row.get(period + '_headway_min'))
            frequency = _headway(row.get(period + '_frequency_per_hour'))
            if headway and frequency and not math.isclose(headway, 60 / frequency, rel_tol=1e-5):
                raise ValueError(f'{route_id}的{period}间隔与频率不一致')
            intervals.append(headway or (60 / frequency if frequency else ''))
        values[route_id] = tuple(intervals)
    if not any(peak or flat for peak, flat in values.values()):
        raise ValueError('至少填写一个线路方向的发车间隔')

    output_dir = output_root / city['id'] / 'service'
    output_dir.mkdir(parents=True, exist_ok=True)
    uploads = output_dir / 'uploads'
    uploads.mkdir(exist_ok=True)
    upload_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    (uploads / (upload_id + '.csv')).write_text(content, encoding='utf-8-sig')
    output = output_dir / 'bus_headways_by_period.csv'
    temporary = output.with_suffix('.tmp')
    fields = ['route_id', 'route_name', 'peak_headway_min', 'flat_headway_min',
              'peak_headway_source', 'flat_headway_source']
    ready = [route_id for route_id, row in catalog.items() if row['topology_status'] == 'ready']
    peak_count = sum(bool(values.get(route_id, ('', ''))[0]) for route_id in ready)
    flat_count = sum(bool(values.get(route_id, ('', ''))[1]) for route_id in ready)
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for route_id, route in catalog.items():
            peak, flat = values.get(route_id, ('', ''))
            writer.writerow({
                'route_id': route_id, 'route_name': route['route_name'],
                'peak_headway_min': peak, 'flat_headway_min': flat,
                'peak_headway_source': 'user_csv' if peak else '',
                'flat_headway_source': 'user_csv' if flat else '',
            })
    temporary.replace(output)
    result = {
        'upload_id': upload_id, 'city': city['name'], 'network_signature': network['signature'],
        'content_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'topology_ready_routes': len(ready),
        'peak_headway_routes': peak_count, 'flat_headway_routes': flat_count,
        'all_ready_routes_covered': peak_count == flat_count == len(ready),
    }
    (output_dir / 'service_manifest.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return result

def resolve_headways(city_id, output_root, output, default_minutes=15, period='pm_peak'):
    """Preserve uploaded intervals; fill only missing cells, snapshot every value."""
    default_minutes = _headway(default_minutes)
    if not default_minutes:
        raise ValueError('缺失线路默认间隔必须大于零')
    root = Path(output_root) / city_id
    uploaded = root / 'service/bus_headways_by_period.csv'
    values = {}
    if uploaded.is_file():
        with uploaded.open(encoding='utf-8-sig', newline='') as stream:
            values = {r['route_id']: r for r in csv.DictReader(stream)}
    source_counts = {'user_csv': 0, 'default_fallback': 0}
    selected = 'flat' if period == 'offpeak' else 'peak'
    fields = ['route_id', 'route_name', 'peak_headway_min', 'flat_headway_min', 'peak_headway_source', 'flat_headway_source']
    target = Path(output) / 'resolved_headways.csv'
    with (root / 'network/bus_headway_template.csv').open(encoding='utf-8-sig', newline='') as stream, target.open('w', encoding='utf-8-sig', newline='') as destination:
        writer = csv.DictWriter(destination, fieldnames=fields); writer.writeheader()
        for route in csv.DictReader(stream):
            old = values.get(route['route_id'], {})
            row = {'route_id': route['route_id'], 'route_name': route['route_name']}
            for name in ('peak', 'flat'):
                value = _headway(old.get(name + '_headway_min'))
                source = 'user_csv' if value else 'default_fallback'
                row[name + '_headway_min'] = value or default_minutes
                row[name + '_headway_source'] = source
                if name == selected and route.get('topology_status') == 'ready':
                    source_counts[source] += 1
            writer.writerow(row)
    total = sum(source_counts.values())
    fallback = source_counts['default_fallback']
    return target, {'headway_basis': 'user_csv_with_default_fallback' if fallback and source_counts['user_csv'] else 'default_fallback_only' if fallback else 'user_supplied_csv',
                    'default_headway_min': default_minutes, 'headway_source_counts': source_counts,
                    'headway_default_share': fallback / total if total else 0,
                    'headway_upload_sha256': hashlib.sha256(uploaded.read_bytes()).hexdigest() if uploaded.is_file() else None}
