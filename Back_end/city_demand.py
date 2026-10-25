"""Versioned shared city demand inputs; no city-specific absolute paths."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree
from scipy.sparse import csr_matrix

from .city_poi import metric_crs

IMPORTANCE = {'医疗保健':3, '科教文化':2.5, '购物消费':2, '商务住宅':2, '公司企业':2,
    '运动健身':1.5, '休闲娱乐':1.5, '生活服务':1.5, '金融机构':1.2, '酒店住宿':1.2,
    '旅游景点':1, '餐饮美食':1, '交通设施':.5, '汽车相关':.3}
ALIASES = {'医疗保健服务':'医疗保健','科教文化服务':'科教文化','购物服务':'购物消费',
    '体育休闲服务':'休闲娱乐','金融保险服务':'金融机构','住宿服务':'酒店住宿',
    '风景名胜':'旅游景点','餐饮服务':'餐饮美食','交通设施服务':'交通设施',
    '汽车服务':'汽车相关','汽车销售':'汽车相关','汽车维修':'汽车相关'}


def importance(classes, weights):
    return classes.map(lambda c: weights.get(c, weights.get(ALIASES.get(c,c), 1.0))).to_numpy(float)


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def run_id():
    return datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]


def load_run(data, city_id, identity):
    import re
    if not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}', str(identity)):
        raise ValueError('请选择有效的需求版本')
    path=Path(data)/'demand_runs'/city_id/identity
    if not (path/'summary.json').is_file():
        raise ValueError('该城市的需求版本不存在')
    return path,json.loads((path/'summary.json').read_text(encoding='utf-8'))


def ensure_current_poi(data, summary):
    path=Path(data)/summary['source_poi'];snapshot=summary['source_signature']
    if not path.is_file() or path.stat().st_size!=snapshot['poi_size'] or path.stat().st_mtime_ns!=snapshot['poi_mtime']:
        raise ValueError('POI输入已变化，请生成新的共享需求版本，不能混用旧密度与新OD输入')


def defaults(city):
    from .assignment.core import PERIOD_WEIGHTS
    return {'grid_m':500, 'smooth_r':800, 'smooth_sigma':400, 'kde_weight':1.0,
        'poi_weights':dict(IMPORTANCE), 'od_period_weights':{p:{'production':dict(a),'attraction':dict(b)} for p,(a,b) in PERIOD_WEIGHTS.items()},
        'hourly_demands':{'am_peak':863000 if city['id']=='Beijing' else None,
            'offpeak':284000 if city['id']=='Beijing' else None,'pm_peak':863000 if city['id']=='Beijing' else None}}


def normalize(city, supplied):
    result=defaults(city)
    for key,value in (supplied or {}).items():
        if key not in result:raise ValueError('未知需求参数：'+key)
        result[key]=value
    for key in ('grid_m','smooth_r','smooth_sigma','kde_weight'):
        result[key]=float(result[key])
        if not np.isfinite(result[key]):raise ValueError('需求参数必须为有限数值')
    if not 100<=result['grid_m']<=2000 or not 0<=result['smooth_r']<=5000 or result['smooth_sigma']<=0 or not 0<=result['kde_weight']<=1:
        raise ValueError('网格100–2000米，平滑半径0–5000米，带宽大于0，POI占比0–1')
    for mapping in [result['poi_weights']]+[m for p in result['od_period_weights'].values() for m in (p['production'],p['attraction'])]:
        if not mapping or any(not np.isfinite(float(v)) or float(v)<0 for v in mapping.values()):raise ValueError('POI权重必须非负且有限')
    for value in result['hourly_demands'].values():
        if value is not None and (not np.isfinite(float(value)) or float(value)<=0):raise ValueError('小时需求必须为正数或留空')
    return result


def norm(values):
    a=np.asarray(values,dtype=float)
    good=a[np.isfinite(a)&(a>0)]
    if not len(good):return np.zeros_like(a)
    lo,hi=np.percentile(good,[2,98])
    return np.clip((a-lo)/(hi-lo),0,1)*100 if hi>lo else np.where(a>0,100.,0.)


def population_file(data,city_id):
    paths=sorted((Path(data)/'population'/city_id).glob('*.tif'))
    return paths[-1] if paths else None


def build(city,data,settings,progress=lambda text:None):
    data=Path(data);settings=normalize(city,settings)
    poi_file=data/'processed'/city['id']/'poi_clustered.csv'
    if not poi_file.is_file():raise ValueError('请先在数据管理中准备本市POI')
    if city.get('poi_file'):
        from .city_poi import cached_city_poi
        from .custom_inputs import source_directory_override, cached_custom_city_poi
        sources = source_directory_override(city, data/'CPTOND-2025/dataset/bus/shapefiles', data/'POI')
        loader = cached_custom_city_poi if city.get('poi_format') == 'custom_standard_v1' else cached_city_poi
        if loader(city,sources['poi_dir'],data/'processed') is None:raise ValueError('原始POI已更新，请先重新准备本市POI')
    population=population_file(data,city['id'])
    if settings['kde_weight']<1 and population is None:raise ValueError('请先导入人口栅格，不能用零人口代替缺失人口')
    signature={'schema':1,'poi_size':poi_file.stat().st_size,'poi_mtime':poi_file.stat().st_mtime_ns,'parameters':settings,
        'population': [population.name,population.stat().st_size,population.stat().st_mtime_ns] if population else None,'metric_crs':metric_crs(city)}
    digest=hashlib.sha256(json.dumps(signature,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    for path in sorted((data/'demand_runs'/city['id']).glob('*/summary.json'),reverse=True):
        saved=json.loads(path.read_text(encoding='utf-8'))
        if saved.get('signature')==digest:return {**saved,'cache_hit':True}
    progress('统计本市POI及共享权重')
    poi=pd.read_csv(poi_file,usecols=['x_m','y_m','class_1'])
    valid=np.isfinite(poi[['x_m','y_m']].to_numpy(float)).all(axis=1)
    poi=poi[valid];xy=poi[['x_m','y_m']].to_numpy(float);factors=importance(poi.class_1.fillna('其他'),settings['poi_weights'])
    if factors.sum()<=0:raise ValueError('全部POI权重为零，无法形成需求')
    size=settings['grid_m'];cells=np.floor(xy/size).astype(np.int64)
    occupied,inverse=np.unique(cells,axis=0,return_inverse=True)
    raw=np.bincount(inverse,weights=factors);counts=np.bincount(inverse)
    pad=int(np.ceil(settings['smooth_r']/size))
    # Include a halo so empty neighboring cells can receive smoothed demand.
    all_keys=set(map(tuple,occupied.tolist()))
    for dx in range(-pad,pad+1):
        for dy in range(-pad,pad+1):
            all_keys.update(map(tuple,(occupied+[dx,dy]).tolist()))
    keys=np.array(sorted(all_keys),dtype=np.int64)
    if len(keys)>250000:raise ValueError('网格超过25万个，请增大网格尺寸后重试')
    index={tuple(k):i for i,k in enumerate(keys)};base=np.zeros(len(keys));npoi=np.zeros(len(keys),dtype=int)
    ids=np.array([index[tuple(k)] for k in occupied]);base[ids]=raw;npoi[ids]=counts
    centers=(keys+.5)*size;tree=cKDTree(centers)
    progress('按参数计算需求平滑')
    neighbors=tree.query_ball_point(centers,settings['smooth_r'])
    lengths=np.array([len(n) for n in neighbors]);rows=np.repeat(np.arange(len(keys)),lengths)
    cols=np.concatenate(neighbors).astype(int)
    kernel=np.exp(-np.sum((centers[rows]-centers[cols])**2,axis=1)/(2*settings['smooth_sigma']**2))
    matrix=csr_matrix((kernel,(rows,cols)),shape=(len(keys),len(keys)))
    smooth=np.asarray(matrix@base)/np.asarray(matrix.sum(axis=1)).ravel()
    density=norm(smooth);pop=np.full(len(keys),np.nan);pop_score=np.full(len(keys),np.nan)
    if population and settings['kde_weight']<1:
        import rasterio
        with rasterio.open(population) as raster:
            if raster.crs is None:raise ValueError('人口栅格缺少坐标系')
            project=Transformer.from_crs(metric_crs(city),raster.crs,always_xy=True)
            rx,ry=project.transform(centers[:,0],centers[:,1])
            sample=np.array([float(v[0]) for v in raster.sample(zip(rx,ry),masked=True)])
            known=np.isfinite(sample)&(sample>=0)
            if raster.nodata is not None:known &= sample!=raster.nodata
            pop[known]=sample[known];pop_score[known]=norm(sample[known])
    known=np.isfinite(pop_score)
    combined=density.copy();combined[known]=settings['kde_weight']*density[known]+(1-settings['kde_weight'])*pop_score[known]
    final=norm(combined)
    project=Transformer.from_crs(metric_crs(city),4326,always_xy=True)
    lon,lat=project.transform(centers[:,0],centers[:,1]);features=[]
    for i,(cx,cy) in enumerate(keys):
        x=np.array([cx,cx+1,cx+1,cx,cx])*size;y=np.array([cy,cy,cy+1,cy+1,cy])*size
        lx,ly=project.transform(x,y)
        features.append({'type':'Feature','geometry':{'type':'Polygon','coordinates':[np.column_stack([lx,ly]).tolist()]},'properties':{
            'grid_id':f'{city["id"]}_{int(cx)}_{int(cy)}','cell_x':int(cx),'cell_y':int(cy),'cx':float(lon[i]),'cy':float(lat[i]),
            'poi_count':int(npoi[i]),'poi_weighted':float(base[i]),'demand_kde':float(smooth[i]),'demand_kde_norm':float(density[i]),
            'population':float(pop[i]) if known[i] else None,'pop_norm':float(pop_score[i]) if known[i] else None,'demand_final':float(final[i])}})
    identity=run_id();output=data/'demand_runs'/city['id']/identity;output.mkdir(parents=True)
    summary={'run_id':identity,'city_id':city['id'],'city':city['name'],'signature':digest,'parameters':settings,'poi_count':len(poi),
        'grid_count':len(keys),'grid_size_m':size,'metric_crs':metric_crs(city),'category_counts':dict(Counter(poi.class_1.fillna('其他'))),
        'scope':'POI-occupied square cells plus smoothing halo; not an administrative boundary',
        'population_file':population.name if population else None,'population_known_grids':int(known.sum()),
        'density_is_relative_index':True,'source_poi':str(poi_file.relative_to(data)),
        'source_signature':signature,'output':output.relative_to(data).as_posix(),'cache_hit':False,
        'weight_semantics':'POI importance multiplies both OD period production and attraction; total trips are separately specified.'}
    save(output/'demand_grid.geojson',{'type':'FeatureCollection','metadata':{'city_id':city['id'],'run_id':identity},'features':features})
    save(output/'parameters.json',settings);save(output/'summary.json',summary)
    return summary
