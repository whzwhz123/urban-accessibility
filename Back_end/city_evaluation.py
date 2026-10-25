import json
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

from .city_network import cached_city_network
from .city_poi import cached_city_poi


GRID_M = 1000
SCHEMA = 1


def _signature(city, bus_dir, poi_dir, output_root):
    network = cached_city_network(city, bus_dir, output_root)
    poi = cached_city_poi(city, poi_dir, output_root)
    if not network or not poi:
        return None
    return {'network': network['signature'], 'poi': poi['signature'], 'schema': SCHEMA}


def cached_city_evaluation(city, bus_dir: Path, poi_dir: Path, output_root: Path):
    signature = _signature(city, bus_dir, poi_dir, output_root)
    folder = output_root / city['id'] / 'evaluation'
    manifest = folder / 'evaluation_manifest.json'
    if signature is None or not manifest.is_file() or not (folder / 'poi_coverage_grid.geojson').is_file():
        return None
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    return saved if saved.get('signature') == signature else None


def prepare_city_evaluation(city, bus_dir: Path, poi_dir: Path, output_root: Path, progress=None):
    signature = _signature(city, bus_dir, poi_dir, output_root)
    if signature is None:
        raise ValueError('Prepare the city network and POI before evaluation')
    saved = cached_city_evaluation(city, bus_dir, poi_dir, output_root)
    if saved:
        return saved

    folder = output_root / city['id'] / 'evaluation'
    folder.mkdir(parents=True, exist_ok=True)
    poi_path = output_root / city['id'] / 'poi_clustered.csv'
    station_path = output_root / city['id'] / 'network' / 'bus_stations_merged.csv'
    counts = Counter()
    scanned = 0
    for chunk in pd.read_csv(poi_path, usecols=['x_m', 'y_m'], chunksize=100000):
        xy = chunk.to_numpy(float)
        xy = xy[np.isfinite(xy).all(axis=1)]
        cells, frequency = np.unique(np.floor(xy / GRID_M).astype(np.int32), axis=0, return_counts=True)
        counts.update({(int(i), int(j)): int(n) for (i, j), n in zip(cells, frequency)})
        scanned += len(chunk)
        if progress:
            progress(f'POI 网格统计：{scanned:,} 条')

    if not counts:
        raise ValueError('No valid city POIs for grid evaluation')
    keys = sorted(counts)
    xy = (np.asarray(keys, dtype=float) + 0.5) * GRID_M
    poi_count = np.asarray([counts[key] for key in keys], dtype=np.int32)
    stations = pd.read_csv(station_path, usecols=['x_m', 'y_m', 'route_ids'])
    station_xy = stations[['x_m', 'y_m']].to_numpy(float)
    tree = cKDTree(station_xy)
    nearest, _ = tree.query(xy, k=1)
    walk_score = np.clip((800.0 - nearest) / 500.0, 0, 1) * 100.0
    route_sets = [set(str(value).split('|')) - {''} for value in stations.route_ids.fillna('')]
    diversity = np.fromiter(
        (len(set().union(*(route_sets[index] for index in nearby))) if nearby else 0
         for nearby in tree.query_ball_point(xy, 500.0)),
        dtype=np.int32, count=len(keys),
    )
    positive = diversity[diversity > 0]
    p95 = float(np.percentile(positive, 95)) if len(positive) else 1.0
    diversity_score = np.clip(diversity / p95, 0, 1) * 100.0
    coverage_score = 0.6 * walk_score + 0.4 * diversity_score
    density_p98 = float(np.percentile(poi_count, 98))
    density_score = np.clip(poi_count / max(density_p98, 1), 0, 1) * 100.0

    if progress:
        progress('生成评价网格')
    transformer = Transformer.from_crs(city['metric_crs'] if 'metric_crs' in city else
                                       cached_city_network(city, bus_dir, output_root)['metric_crs'],
                                       'EPSG:4326', always_xy=True)
    features = []
    for index, (i, j) in enumerate(keys):
        corners_x = [i * GRID_M, (i + 1) * GRID_M, (i + 1) * GRID_M, i * GRID_M, i * GRID_M]
        corners_y = [j * GRID_M, j * GRID_M, (j + 1) * GRID_M, (j + 1) * GRID_M, j * GRID_M]
        longitude, latitude = transformer.transform(corners_x, corners_y)
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [list(map(list, zip(longitude, latitude)))]},
            'properties': {
                'cell_x': i, 'cell_y': j, 'poi_count': int(poi_count[index]),
                'poi_density_norm': round(float(density_score[index]), 2),
                'nearest_station_m': round(float(nearest[index]), 1),
                'walk_score': round(float(walk_score[index]), 2),
                'diversity_count': int(diversity[index]),
                'diversity_score': round(float(diversity_score[index]), 2),
                'coverage_score': round(float(coverage_score[index]), 2),
            },
        })
    output = folder / 'poi_coverage_grid.geojson'
    temporary = folder / 'poi_coverage_grid.tmp'
    temporary.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}, ensure_ascii=False), encoding='utf-8')
    temporary.replace(output)
    result = {
        'city_id': city['id'], 'city': city['name'], 'signature': signature,
        'grid_size_m': GRID_M, 'scope': 'POI-occupied cells only',
        'poi_count': int(poi_count.sum()), 'occupied_grid_count': len(keys),
        'station_count': len(stations),
        'walk_score_positive_share': round(float(np.mean(walk_score > 0)), 4),
        'mean_coverage_score': round(float(coverage_score.mean()), 2),
        'density_p98': density_p98, 'diversity_p95': p95,
        'grid_file': output.relative_to(output_root).as_posix(),
    }
    (folder / 'evaluation_manifest.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    return result
