"""Offline timetable conversion. All windows are local time, left-closed/right-open."""
import csv
import io
import json
import math
import re

PERIODS = {'am': (7 * 60, 9 * 60), 'flat': (9 * 60, 17 * 60), 'pm': (17 * 60, 19 * 60)}
MAX_CSV_BYTES = 2_000_000


def _clock(value):
    match = re.fullmatch(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', str(value or '').strip())
    if not match:
        raise ValueError('时间须为HH:MM或HH:MM:SS')
    hour, minute, second = (int(part or 0) for part in match.groups())
    if hour >= 24 or minute >= 60 or second >= 60:
        raise ValueError('时间超出24小时范围')
    return hour * 60 + minute + second / 60


def _interval(value):
    text = str(value or '').strip()
    # Ranges such as 5-10 are not a fixed interval and must not be averaged.
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:分钟|分|min)?', text, re.I)
    if not match:
        raise ValueError('固定间隔必须为正数分钟，不能把区间或描述猜成单值')
    number = float(match[1])
    if not math.isfinite(number) or number <= 0:
        raise ValueError('固定间隔必须为正数分钟')
    return number


def _rules(payload, day_type):
    groups = payload.get('rule_group') or []
    # Do not silently import a weekend-only schedule as a weekday schedule.
    chosen = [rule for rule in groups if day_type in (rule.get('date', {}).get('day_type') or [])]
    chosen = chosen or [rule for rule in groups if 'all' in (rule.get('date', {}).get('day_type') or [])]
    if groups:
        if not chosen:
            raise ValueError('没有匹配所选日期类型的时刻规则')
        if len(chosen) > 1:
            raise ValueError('存在多组日期规则，请先按实际运营日期筛选，不能合并猜测')
        date = chosen[0].get('date') or {}
        # Date-specific validity requires caller-side date filtering.
        if any(date.get(key) for key in ('start_date', 'end_date', 'date_list')):
            raise ValueError('规则带日期范围，请先按运营日期筛选')
        return chosen[0].get('time_group') or []
    return [item for key in ('peak_time_group', 'flat_time_group', 'low_time_group') for item in payload.get(key) or []]


def _time_items(row, mode, day_type):
    if mode == 'amap':
        payload = json.loads(row.get('timedesc_json') or '{}')
        if not isinstance(payload, dict):
            raise ValueError('timedesc_json必须是对象')
        return _rules(payload, day_type)
    if mode == 'discrete':
        value = row.get('departure_time') or ''
        times = row.get('departure_times') or ''
        if value and times:
            raise ValueError('departure_time与departure_times只能填写一个')
        values = [value] if value else re.split(r'[;；\s]+', times.strip())
        if not any(values):
            raise ValueError('缺少离散发车时间')
        return [{'start_time': value, 'end_time': value} for value in values if value]
    if mode == 'fixed':
        return [{'start_time': row.get('start_time'), 'end_time': row.get('end_time'), 'interval_time': row.get('interval_min')}]
    raise ValueError('不支持的时刻表格式')


def _ranges(start, end):
    if end > start:
        return [(start, end)]
    if end < start:
        return [(start, 1440), (0, end)]
    raise ValueError('固定间隔起终时间相同，无法区分全天与单班')


def parse_timetable_csv(content, *, mode='auto', day_type='weekday'):
    """Return {csv, rows, summary}; blanks are pending, never default frequencies.

    CSV schemas: route_id + timedesc_json; route_id + departure_time (one row/trip)
    or departure_times (semicolon list); route_id + start_time,end_time,interval_min.
    Fixed intervals are duration-weighted through implied departures/hour, not
    expanded to fictitious departures. Mixed discrete/fixed periods stay pending.
    """
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig')
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_CSV_BYTES:
        raise ValueError('时刻表CSV必须为UTF-8且不超过2 MB')
    reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
    fields = set(reader.fieldnames or [])
    if 'route_id' not in fields:
        raise ValueError('时刻表缺少route_id（线路方向ID）')
    if mode == 'auto':
        mode = 'amap' if 'timedesc_json' in fields else 'discrete' if fields & {'departure_time', 'departure_times'} else 'fixed'
    required = {'amap': {'timedesc_json'}, 'fixed': {'start_time', 'end_time', 'interval_min'}, 'discrete': set()}
    if mode not in required or not required[mode] <= fields or (mode == 'discrete' and not fields & {'departure_time', 'departure_times'}):
        raise ValueError('CSV字段与所选时刻表格式不符')
    routes, issues = {}, []
    for index, row in enumerate(reader, 2):
        route = str(row.get('route_id') or '').strip()
        if not route:
            raise ValueError(f'第{index}行缺少route_id')
        state = routes.setdefault(route, {'points': [], 'fixed': [], 'errors': []})
        try:
            items = _time_items(row, mode, day_type)
            if not items:
                raise ValueError('没有可解析的时间项')
            for item in items:
                start, end = _clock(item.get('start_time')), _clock(item.get('end_time'))
                interval = item.get('interval_time')
                if interval not in (None, '', []):
                    minutes = _interval(interval)
                    state['fixed'].extend((left, right, minutes) for left, right in _ranges(start, end))
                elif start == end:
                    state['points'].append(start)
                else:
                    raise ValueError('只有首末班时间，没有固定间隔或完整发车序列')
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            state['errors'].append(f'第{index}行：{error}')
    rows = []
    for route, state in routes.items():
        output = {'route_id': route}
        for period, (left, right) in PERIODS.items():
            points = sorted(set(point for point in state['points'] if left <= point < right))
            fixed = sorted((max(start, left), min(end, right), interval) for start, end, interval in state['fixed'] if max(start, left) < min(end, right))
            reason, value, source = '', '', ''
            if state['errors']:
                reason = '; '.join(state['errors'])
            elif fixed and points:
                reason = '同一时段混合离散和固定间隔，需明确完整时刻表'
            elif fixed:
                if any(previous[1] > following[0] for previous, following in zip(fixed, fixed[1:])):
                    reason = '固定间隔规则重叠，需先消除冲突'
                else:
                    duration = sum(end - start for start, end, _ in fixed)
                    value = duration / sum((end - start) / interval for start, end, interval in fixed)
                    source = 'timetable_fixed'
            elif len(points) >= 2:
                value = sum(b - a for a, b in zip(points, points[1:])) / (len(points) - 1)
                source = 'timetable_discrete'
            else:
                reason = '时段内仅一班，无法计算相邻间隔' if points else '时段内没有足够的可用发车数据'
            output[period + '_headway_min'] = value
            output[period + '_headway_source'] = source
            output[period + '_departure_count'] = len(points) if not fixed else ''
            output[period + '_pending_reason'] = reason
            if reason:
                issues.append({'route_id': route, 'period': period, 'reason': reason})
        rows.append(output)
    if not rows:
        raise ValueError('CSV没有线路记录')
    buffer = io.StringIO()
    columns = ['route_id'] + [period + suffix for period in PERIODS for suffix in ('_headway_min', '_headway_source', '_departure_count', '_pending_reason')]
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return {'csv': buffer.getvalue(), 'rows': rows, 'summary': {
        'mode': mode, 'day_type': day_type, 'routes': len(rows), 'pending_cells': len(issues),
        'issues': issues, 'windows': {'am': '07:00-09:00', 'flat': '09:00-17:00', 'pm': '17:00-19:00'},
        'boundary': '[start,end)', 'duplicate_departures': sum(len(state['points']) - len(set(state['points'])) for state in routes.values())}}


def import_timetable(city, bus_dir, output_root, content, *, mode='auto', day_type='weekday'):
    """Persist via existing frequency importer, including all-pending schedules."""
    from .city_service import import_city_service
    parsed = parse_timetable_csv(content, mode=mode, day_type=day_type)
    return import_city_service(city, bus_dir, output_root, parsed['csv'],
                               provenance={'kind': 'timetable', 'summary': parsed['summary'],
                                           'original_content': content.decode('utf-8-sig') if isinstance(content, bytes) else content})
