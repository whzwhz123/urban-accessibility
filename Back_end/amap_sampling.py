import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

from .city_poi import gcj02_to_wgs84, metric_crs


AMAP_BASE = 'https://restapi.amap.com/v5/direction'
CHINA_TIME = ZoneInfo('Asia/Shanghai')
MAX_PAIRS = 20


def _to_gcj02(lon, lat):
    # Invert the existing GCJ-02 -> WGS-84 transform without introducing a second formula.
    gcj_lon, gcj_lat = lon, lat
    for _ in range(5):
        back_lon, back_lat = gcj02_to_wgs84(gcj_lon, gcj_lat)
        gcj_lon += lon - back_lon
        gcj_lat += lat - back_lat
    return [round(gcj_lon, 6), round(gcj_lat, 6)]


def sample_grid_pairs(city, processed_root: Path, pair_count=10, seed=2026):
    if not 1 <= pair_count <= MAX_PAIRS:
        raise ValueError(f'抽样 OD 数量应为 1–{MAX_PAIRS}')
    grid_file = processed_root / city['id'] / 'evaluation' / 'poi_coverage_grid.geojson'
    if not grid_file.is_file():
        raise ValueError('请先计算该城市的 POI 分布与公交覆盖度')
    features = json.loads(grid_file.read_text(encoding='utf-8'))['features']
    cells = [(int(f['properties']['cell_x']), int(f['properties']['cell_y'])) for f in features]
    if len(cells) < 2:
        raise ValueError('可用网格不足两个')
    xy = (np.asarray(cells, dtype=float) + 0.5) * 1000.0
    tree = cKDTree(xy)
    rng = random.Random(seed)
    origins = list(range(len(cells)))
    rng.shuffle(origins)
    transformer = Transformer.from_crs(metric_crs(city), 'EPSG:4326', always_xy=True)
    pairs = []
    for origin in origins:
        nearby = tree.query_ball_point(xy[origin], 20000.0)
        possible = [dest for dest in nearby if 1000.0 <= np.linalg.norm(xy[dest] - xy[origin]) <= 20000.0]
        if not possible:
            continue
        dest = rng.choice(possible)
        o_lon, o_lat = transformer.transform(*xy[origin])
        d_lon, d_lat = transformer.transform(*xy[dest])
        pairs.append({
            'id': f'{cells[origin][0]}_{cells[origin][1]}__{cells[dest][0]}_{cells[dest][1]}',
            'origin_wgs84': [round(o_lon, 6), round(o_lat, 6)],
            'destination_wgs84': [round(d_lon, 6), round(d_lat, 6)],
            'distance_m': round(float(np.linalg.norm(xy[dest] - xy[origin]))),
            'origin_poi_count': int(features[origin]['properties']['poi_count']),
            'destination_poi_count': int(features[dest]['properties']['poi_count']),
        })
        if len(pairs) == pair_count:
            break
    return {
        'city_id': city['id'], 'pair_count': len(pairs), 'planned_api_calls': len(pairs) * 2,
        'sampling': '从有 POI 的 1 km 网格均匀抽取起点；每个起点随机抽取 1–20 km 内的终点。不按人口或出行量加权。',
        'coordinate_source': '网格中心 WGS-84，调用前转换 GCJ-02',
        'pairs': pairs,
    }


def _api_request(mode, origin, destination, city_code, key, now):
    params = {
        'key': key, 'origin': ','.join(map(str, origin)),
        'destination': ','.join(map(str, destination)),
        'show_fields': 'cost',
    }
    if mode == 'transit':
        params.update({'city1': city_code, 'city2': city_code, 'strategy': '5',
                       'AlternativeRoute': '1', 'date': now.strftime('%Y-%m-%d'),
                       'time': now.strftime('%H-%M')})
        endpoint = 'transit/integrated'
    else:
        endpoint = 'driving'
    # Do not expose the URL in exceptions: its query string contains the Web-service key.
    try:
        with urlopen(f'{AMAP_BASE}/{endpoint}?{urlencode(params)}', timeout=15) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return {'status': 'transport_error'}
    if str(payload.get('status')) != '1':
        return {'status': 'api_error', 'infocode': str(payload.get('infocode', ''))}
    route = payload.get('route') or {}
    if mode == 'transit':
        options = route.get('transits') or []
        if not options:
            return {'status': 'no_route'}
        choice = options[0]
        legs = sum(bool((segment.get('bus') or {}).get('buslines')) for segment in choice.get('segments') or [])
        if not legs or any(segment.get('railway') for segment in choice.get('segments') or []):
            return {'status': 'no_bus_route'}
    else:
        options = route.get('paths') or []
        if not options:
            return {'status': 'no_route'}
        choice = options[0]
        legs = None
    duration = (choice.get('cost') or {}).get('duration') or choice.get('duration')
    try:
        duration_s = float(duration)
    except (TypeError, ValueError):
        return {'status': 'missing_duration'}
    if not math.isfinite(duration_s) or duration_s <= 0:
        return {'status': 'missing_duration'}
    return {'status': 'ok', 'duration_s': duration_s,
            'transfers': max(0, legs - 1) if legs is not None else None}


def collect_snapshot(city, preview, run_dir: Path, progress=None, request_fn=_api_request):
    key = os.environ.get('AMAP_API_KEY', '').strip()
    if not key:
        raise ValueError('未配置 AMAP_API_KEY；没有发出高德请求')
    run_dir.mkdir(parents=True, exist_ok=True)
    records_file = run_dir / 'navigation_samples.jsonl'
    completed = set()
    if records_file.is_file():
        for line in records_file.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row.get('status') in ('ok', 'no_route', 'no_bus_route'):
                completed.add((row['pair_id'], row['mode']))
    with records_file.open('a', encoding='utf-8') as stream:
        for index, pair in enumerate(preview['pairs'], 1):
            origin = _to_gcj02(*pair['origin_wgs84'])
            destination = _to_gcj02(*pair['destination_wgs84'])
            for mode in ('transit', 'driving'):
                if (pair['id'], mode) in completed:
                    continue
                now = datetime.now(CHINA_TIME)
                data = request_fn(mode, origin, destination, city['city_code'], key, now)
                record = {'pair_id': pair['id'], 'mode': mode, 'requested_at_china': now.isoformat(),
                          'origin_gcj02': origin, 'destination_gcj02': destination, **data}
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                stream.flush()
                if data['status'] == 'api_error':
                    raise RuntimeError(f'高德拒绝请求（infocode={data["infocode"]}），已停止后续调用')
                if progress:
                    progress(f'导航采样 {index}/{preview["pair_count"]} 对，{mode}')
                time.sleep(1.0)
    latest = {}
    for line in records_file.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        latest[(row['pair_id'], row['mode'])] = row
    paired = []
    for pair in preview['pairs']:
        transit = latest.get((pair['id'], 'transit'), {})
        driving = latest.get((pair['id'], 'driving'), {})
        if transit.get('status') == driving.get('status') == 'ok':
            paired.append({'pair_id': pair['id'], 'transit_min': round(transit['duration_s'] / 60, 2),
                           'driving_min': round(driving['duration_s'] / 60, 2),
                           'transit_to_driving_ratio': round(transit['duration_s'] / driving['duration_s'], 3)})
    status_counts = {}
    for row in latest.values():
        status_counts[row['status']] = status_counts.get(row['status'], 0) + 1
    result = {
        'run_id': run_dir.name, 'status_counts': status_counts,
        'city_id': city['id'], 'kind': 'live_navigation_snapshot', 'requested_pairs': preview['pair_count'],
        'paired_success_count': len(paired), 'paired_results': paired,
        'transit_scope': '高德不乘地铁策略；返回的公交总耗时包含候车时间',
        'driving_scope': '请求时刻的小汽车规划耗时；未设置历史出发时间',
        'comparability': '探索性同期样本，不是全市 TCI 或历史时段结果',
        'records_file': records_file.name,
    }
    (run_dir / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
