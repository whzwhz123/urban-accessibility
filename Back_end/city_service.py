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
PERIOD_COLUMNS = ('peak', 'flat', 'am', 'pm')
SOURCE_KINDS = ('user_csv', 'timetable_discrete', 'timetable_fixed')


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
    text = str(value if value is not None else '').strip()
    if not text:
        return ''
    number = float(text)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('发车间隔必须是大于零的分钟数')
    return number


def _columns():
    return ['route_id', 'route_name'] + [period + suffix for period in PERIOD_COLUMNS
        for suffix in ('_headway_min', '_headway_source', '_pending_reason', '_departure_count')]


def import_city_service(city, bus_dir: Path, output_root: Path, content: str, *, provenance=None):
    """Accept legacy peak/flat or separate am/pm/flat intervals/frequencies.

    Explicit am/pm blanks stay pending; only absent am/pm columns inherit peak.
    provenance is internal metadata from import_timetable, not an HTTP option.
    """
    bus_dir, output_root = Path(bus_dir), Path(output_root)
    network = cached_city_network(city, bus_dir, output_root)
    if network is None:
        raise ValueError('请先准备该城市的线网')
    template = output_root / city['id'] / 'network' / 'bus_headway_template.csv'
    with template.open(encoding='utf-8-sig', newline='') as stream:
        catalog = {row['route_id']: row for row in csv.DictReader(stream)}
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    fields = set(reader.fieldnames or [])
    supplied = lambda period: bool({period + '_headway_min', period + '_frequency_per_hour'} & fields)
    if 'route_id' not in fields or not supplied('flat') or not (supplied('peak') or (supplied('am') and supplied('pm'))):
        raise ValueError('CSV需含route_id，以及peak/flat或am/pm/flat的_headway_min或_frequency_per_hour字段')
    values = {}
    for row in reader:
        route_id = str(row.get('route_id') or '').strip()
        if not route_id:
            raise ValueError('CSV存在空线路ID')
        if route_id not in catalog:
            raise ValueError(f'未知线路 ID：{route_id}')
        if route_id in values:
            raise ValueError(f'线路 ID 重复：{route_id}')
        record = {}
        for period in PERIOD_COLUMNS:
            headway = _headway(row.get(period + '_headway_min'))
            frequency = _headway(row.get(period + '_frequency_per_hour'))
            if headway and frequency and not math.isclose(headway, 60 / frequency, rel_tol=1e-5):
                raise ValueError(f'{route_id}的{period}间隔与频率不一致')
            converted = _headway(60 / frequency) if frequency else ''
            value = headway or converted
            raw_source = row.get(period + '_headway_source', '')
            source = raw_source if provenance and raw_source in SOURCE_KINDS else 'user_csv'
            record[period + '_headway_min'] = value
            record[period + '_headway_source'] = source if value else ''
            record[period + '_pending_reason'] = row.get(period + '_pending_reason', '') if not value else ''
            record[period + '_departure_count'] = row.get(period + '_departure_count', '')
        for period in ('am', 'pm'):
            if not supplied(period):
                for suffix in ('_headway_min', '_headway_source', '_pending_reason', '_departure_count'):
                    record[period + suffix] = record['peak' + suffix]
        values[route_id] = record
    if not values or (not provenance and not any(row[p + '_headway_min'] for row in values.values() for p in PERIOD_COLUMNS)):
        raise ValueError('至少填写一个线路方向的发车间隔')
    output_dir = output_root / city['id'] / 'service'
    output_dir.mkdir(parents=True, exist_ok=True)
    uploads = output_dir / 'uploads'
    uploads.mkdir(exist_ok=True)
    upload_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
    (uploads / (upload_id + '.csv')).write_text(content, encoding='utf-8-sig')
    metadata = {key: value for key, value in (provenance or {}).items() if key != 'original_content'}
    if provenance and 'original_content' in provenance:
        original = provenance['original_content']
        (uploads / (upload_id + '.original.csv')).write_text(original, encoding='utf-8-sig')
        metadata['original_sha256'] = hashlib.sha256(original.encode('utf-8')).hexdigest()
    output = output_dir / 'bus_headways_by_period.csv'
    temporary = output.with_name('bus_headways_' + upload_id + '.tmp')
    ready = [route_id for route_id, row in catalog.items() if row['topology_status'] == 'ready']
    counts = {period: sum(bool(values.get(route_id, {}).get(period + '_headway_min')) for route_id in ready) for period in PERIOD_COLUMNS}
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=_columns())
        writer.writeheader()
        for route_id, route in catalog.items():
            writer.writerow({'route_id': route_id, 'route_name': route['route_name'], **values.get(route_id, {})})
    temporary.replace(output)
    result = {
        'upload_id': upload_id, 'city': city['name'], 'network_signature': network['signature'],
        'content_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'topology_ready_routes': len(ready),
        **{period + '_headway_routes': count for period, count in counts.items()},
        'all_ready_routes_covered': all(counts[p] == len(ready) for p in ('am', 'flat', 'pm')),
        'provenance': metadata,
    }
    (output_dir / 'service_manifest.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if provenance:
        (uploads / (upload_id + '.metadata.json')).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        result['summary'] = provenance.get('summary')
    return result


def resolve_headways(city_id, output_root, output, default_minutes=15, period='pm_peak'):
    """Select am/pm explicitly and project it to assignment's legacy peak column."""
    default_minutes = _headway(default_minutes)
    if not default_minutes:
        raise ValueError('缺失线路默认间隔必须大于零')
    selected = {'am_peak': 'am', 'offpeak': 'flat', 'pm_peak': 'pm'}.get(period)
    if selected is None:
        raise ValueError('未知时段：' + str(period))
    root = Path(output_root) / city_id
    uploaded = root / 'service/bus_headways_by_period.csv'
    values = {}
    if uploaded.is_file():
        with uploaded.open(encoding='utf-8-sig', newline='') as stream:
            values = {row['route_id']: row for row in csv.DictReader(stream)}
    source_counts = {source: 0 for source in (*SOURCE_KINDS, 'default_fallback')}
    fields = _columns() + ['selected_period', 'selected_headway_min', 'selected_headway_source']
    target = Path(output) / 'resolved_headways.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    with (root / 'network/bus_headway_template.csv').open(encoding='utf-8-sig', newline='') as stream, target.open('w', encoding='utf-8-sig', newline='') as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        for route in csv.DictReader(stream):
            old = values.get(route['route_id'], {})
            row = {'route_id': route['route_id'], 'route_name': route['route_name']}
            for name in PERIOD_COLUMNS:
                original_name = 'peak' if name in ('am', 'pm') and name + '_headway_min' not in old else name
                value = _headway(old.get(original_name + '_headway_min'))
                source = old.get(original_name + '_headway_source') or 'user_csv'
                row[name + '_headway_min'] = value or default_minutes
                row[name + '_headway_source'] = source if value else 'default_fallback'
                row[name + '_pending_reason'] = old.get(original_name + '_pending_reason', '')
                row[name + '_departure_count'] = old.get(original_name + '_departure_count', '')
            row['selected_period'] = period
            row['selected_headway_min'] = row[selected + '_headway_min']
            row['selected_headway_source'] = row[selected + '_headway_source']
            if selected != 'flat':
                for suffix in ('_headway_min', '_headway_source', '_pending_reason', '_departure_count'):
                    row['peak' + suffix] = row[selected + suffix]
            if route.get('topology_status') == 'ready':
                source = row['selected_headway_source']
                source_counts[source] = source_counts.get(source, 0) + 1
            writer.writerow(row)
    total = sum(source_counts.values())
    fallback = source_counts['default_fallback']
    return target, {'headway_basis': 'user_csv_with_default_fallback' if fallback and total > fallback else 'default_fallback_only' if fallback else 'user_supplied_csv',
                    'headway_period': period, 'default_headway_min': default_minutes, 'headway_source_counts': source_counts,
                    'headway_default_share': fallback / total if total else 0,
                    'headway_upload_sha256': hashlib.sha256(uploaded.read_bytes()).hexdigest() if uploaded.is_file() else None}
