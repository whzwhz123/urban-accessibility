"""City-bound AMap collection plans; SDK credentials never enter result files."""
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from pyproj import Transformer
from .city_demand import load_run, ensure_current_poi
from .city_poi import metric_crs
from .data_process.isochrone_AmapJS import wgs84_to_gcj02

def plan(city, data, demand_id, kind, count=20, minutes=45, destinations=3, method="cumulative", interval_ms=1000, request_budget=500, session="snapshot"):
    if kind not in ('isochrone', 'tci') or not 1 <= count <= 100000 or not 1 <= minutes <= 120:
        raise ValueError('请选择有效采集类型、点数和等时圈时间')
    if not 1<=destinations<=20 or interval_ms<1000 or not 1<=request_budget<=5000 or session not in ('snapshot','morning','midday','evening'):raise ValueError('采集数量、请求预算或时段无效')
    folder, demand = load_run(data, city['id'], demand_id)
    ensure_current_poi(data, demand)
    features = json.loads((folder / 'demand_grid.geojson').read_text(encoding='utf-8'))['features']
    occupied = [f['properties'] for f in features if f['properties'].get('poi_count', 0) > 0]
    if not occupied:
        raise ValueError('需求版本没有含POI的格网')
    rng = np.random.default_rng(20261012)
    indices = rng.permutation(len(occupied))[:count]
    points = []
    for index in indices:
        row = occupied[int(index)]; lon, lat = row['cx'], row['cy']
        gx, gy = wgs84_to_gcj02(lon, lat)
        points.append({'grid_id': row['grid_id'], 'grid_idx': int(index), 'wgs_lon': lon, 'wgs_lat': lat, 'gcj_lon': gx, 'gcj_lat': gy})
    result = {'city_id': city['id'], 'city_name': city['name'], 'demand_run_id': demand_id, 'kind': kind,
              'points': points, 'minutes': list(range(15,minutes,15))+[minutes] if method=='time_bands' else [minutes], 'interval_ms': interval_ms, 'timeout_ms': 45000, 'max_attempts': 3,
              'source_origin_count': len(occupied), 'subset_only': len(points) < len(occupied),
              'sampling': 'fixed-seed uniform sample of POI-containing grid centres', 'seed': 20261012, 'auto_export': False, 'session': session, 'request_budget': request_budget}
    if kind == 'tci':
        transformer = Transformer.from_crs(4326, metric_crs(city), always_xy=True)
        xy = np.column_stack(transformer.transform([r['cx'] for r in occupied], [r['cy'] for r in occupied]))
        tree = cKDTree(xy); pairs = []
        for point, index in zip(points, indices):
            candidates = np.asarray(tree.query_ball_point(xy[int(index)], 20000), dtype=int)
            candidates = candidates[np.linalg.norm(xy[candidates] - xy[int(index)], axis=1) >= 1000]
            if not len(candidates):
                continue
            distance=np.linalg.norm(xy[candidates]-xy[int(index)],axis=1);selected=[]
            for low,high in [(1000,5000),(5000,10000),(10000,20001)]:
                band=candidates[(distance>=low)&(distance<high)]
                if len(band) and len(selected)<destinations:selected.append(int(rng.choice(band)))
            remaining=np.asarray([int(v) for v in candidates if int(v) not in selected])
            if len(selected)<destinations and len(remaining):selected.extend(rng.choice(remaining,size=min(destinations-len(selected),len(remaining)),replace=False).tolist())
            for di in selected:
                destination=occupied[di];gx,gy=wgs84_to_gcj02(destination['cx'],destination['cy'])
                pairs.append({**point,'pair_id':point['grid_id']+':'+destination['grid_id'],'destination_wgs84':[destination['cx'],destination['cy']],'destination_gcj02':[gx,gy]})
        if not pairs:
            raise ValueError('没有1至20公里内的有效导航样本')
        result['points'] = pairs; result['subset_only'] = True; result['distance_range_km'] = [1, 20];result['destinations_per_origin']=destinations;result['sampling']='fixed-seed origins, unique destinations stratified at 1-5/5-10/10-20 km; spatial diagnostics, not demand-weighted OD forecast'
    digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:20]
    result['collection_id'] = kind + '_' + digest
    result['planned_requests'] = len(result['points']) * (2 if kind == 'tci' else len(result['minutes']))
    if result['planned_requests']>request_budget:raise ValueError(f"计划请求{result['planned_requests']}次，超过本次预算{request_budget}，请减少采集点或增加已确认预算")
    result['estimated_min_seconds']=result['planned_requests']*interval_ms/1000
    return result
