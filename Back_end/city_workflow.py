"""Shared-demand -> coverage/opportunity/TCI -> composite, for any prepared city."""
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree
from shapely import STRtree, contains_xy
from shapely.geometry import Point, shape
from shapely.ops import transform

from .city_demand import load_run, importance, norm, run_id, save, ensure_current_poi
from .data_process.process_arrival_range import process
from .data_process.rebuild_composite import rebuild

DEFAULTS={'coverage':{'walk_full':300,'walk_zero':800,'walk_weight':.6,'diversity_r':500},
    'isochrone':{'sigma':15,'max_time':45,'method':'cumulative'},'tci':{'alpha':8,'bus_max':70,'car_max':30}}


def normalize(values):
    result={k:{**v,**(values or {}).get(k,{})} for k,v in DEFAULTS.items()}
    for group in result.values():
        for k,v in group.items():
            if k=='method':continue
            if not np.isfinite(float(v)):raise ValueError('评价参数必须为有限数值')
            group[k]=float(v)
    c=result['coverage'];i=result['isochrone'];t=result['tci']
    if i['method'] not in ('cumulative','time_bands','legacy_distance'):raise ValueError('未知机会评分方法')
    if not 0<=c['walk_full']<c['walk_zero'] or not 0<=c['walk_weight']<=1 or c['diversity_r']<=0:raise ValueError('覆盖度阈值和权重不合法')
    if i['sigma']<=0 or not 1<=i['max_time']<=120 or not i['max_time'].is_integer():raise ValueError('等时圈时间必须为1–120的整数，带宽大于0')
    if t['alpha']<0 or t['bus_max']<=0 or t['car_max']<=0:raise ValueError('竞争力参数不合法')
    return result


def input_versions(data, city_id, demand_id=None, minutes=None):
    folder=Path(data)/'evaluation_inputs'/city_id
    result={'isochrone':[], 'tci':[]}
    for kind,suffix in [('isochrone','geojson'),('tci','json')]:
        for file in sorted(folder.glob(kind+'_*.'+suffix),reverse=True):
            value=json.loads(file.read_text(encoding='utf-8-sig'))
            metadata=value.get('metadata',{}) if isinstance(value,dict) else {}
            if metadata.get('city_id') not in (None,city_id):continue
            if demand_id and metadata.get('demand_run_id') not in (None,demand_id):continue
            rows=value.get('features' if kind=='isochrone' else 'records',[]) if isinstance(value,dict) else value
            horizons=set(int(v) for v in metadata.get('minutes',[]))
            if kind=='isochrone':
                horizons.update(int(r['properties']['minute']) for r in rows if r.get('properties',{}).get('minute') is not None)
                if minutes is not None and horizons and int(minutes) not in horizons:continue
            result[kind].append({'file':file.name,'records':len(rows),'minutes':sorted(horizons),
                'source':metadata.get('source','user_upload'),'subset_only':bool(metadata.get('subset_only')),
                'demand_run_id':metadata.get('demand_run_id'),'complete':metadata.get('complete'),
                'time_basis':metadata.get('time_basis'),'exported_at':metadata.get('exported_at')})
    return result


def sources(data,city_id,demand_id=None,minutes=None,input_files=None):
    versions=input_versions(data,city_id,demand_id,minutes)
    result={}
    for kind,rows in versions.items():
        if input_files is not None and kind in input_files:
            name=input_files[kind]
            if name is not None and name not in {row['file'] for row in rows}:
                raise ValueError('所选采集版本不属于当前城市/需求，或等时圈时间上限不匹配：'+str(name))
        else:name=rows[0]['file'] if rows else None
        result[kind]=Path(data)/'evaluation_inputs'/city_id/name if name else None
    return result


def coverage(grids,stations,crs,settings):
    transformer=Transformer.from_crs(4326,crs,always_xy=True)
    centers=np.array([[f['properties']['cx'],f['properties']['cy']] for f in grids])
    x,y=transformer.transform(centers[:,0],centers[:,1]);xy=np.column_stack([x,y])
    tree=cKDTree(stations[['x_m','y_m']].to_numpy(float))
    distance,_=tree.query(xy,workers=-1)
    walk=np.clip((settings['walk_zero']-distance)/(settings['walk_zero']-settings['walk_full']),0,1)*100
    route_sets=[set(str(v).split('|'))-{''} for v in stations.route_ids.fillna('')]
    nearby=tree.query_ball_point(xy,settings['diversity_r'])
    diversity=np.array([len(set().union(*(route_sets[j] for j in ids))) if ids else 0 for ids in nearby])
    positive=diversity[diversity>0];scale=float(np.percentile(positive,95)) if len(positive) else 1.
    score=np.clip(diversity/scale,0,1)*100;combined=settings['walk_weight']*walk+(1-settings['walk_weight'])*score
    features=[]
    for f,d,w,n,s,c in zip(grids,distance,walk,diversity,score,combined):
        features.append({**f,'properties':{**f['properties'],'nearest_station_m':float(d),'walk_score':float(w),
            'diversity_count':int(n),'diversity_score':float(s),'coverage_score':float(c)}})
    return features


def opportunity(grids,polygons,poi,crs,weights,settings,progress):
    method=settings.get('method','cumulative')
    grid_tree=STRtree([shape(f['geometry']) for f in grids])
    project=Transformer.from_crs(4326,crs,always_xy=True)
    xy=poi[['x_m','y_m']].to_numpy(float);tree=cKDTree(xy)
    factors=importance(poi.class_1.fillna('其他'),weights)
    origins={};accumulated={};iso_features=[];unmatched=0;incomplete=0
    for feature in polygons:
        properties=feature['properties'];minute=int(properties['minute'])
        if minute<=settings['max_time']:
            origin=tuple(properties['origin_wgs84']);origins.setdefault(origin,{})[minute]=feature
    for n,(origin,records) in enumerate(origins.items()):
        horizon=int(settings['max_time'])
        if horizon not in records:incomplete+=1;continue
        required=list(range(15,horizon,15))+[horizon] if method=='time_bands' else [horizon]
        if any(t not in records for t in required):incomplete+=1;continue
        ids=grid_tree.query(Point(origin),predicate='within')
        if len(ids)!=1:unmatched+=1;continue
        observed=set();score=0.;upper=0.;previous=0;non_nested=0;final_feature=records[horizon]
        for minute in required:
            polygon=transform(project.transform,shape(records[minute]['geometry']));x0,y0,x1,y1=polygon.bounds
            if polygon.is_empty:continue
            candidate=np.asarray(tree.query_ball_point([(x0+x1)/2,(y0+y1)/2],np.hypot(x1-x0,y1-y0)/2+1e-8),dtype=int)
            found=set(candidate[contains_xy(polygon,xy[candidate,0],xy[candidate,1])].tolist())
            if method=='time_bands':
                non_nested+=len(observed-found);added=np.asarray(sorted(found-observed),dtype=int)
                mass=float(factors[added].sum());score+=mass*np.exp(-minute**2/(2*settings['sigma']**2))
                upper+=mass*np.exp(-previous**2/(2*settings['sigma']**2));observed|=found;previous=minute
            else:
                added=np.asarray(sorted(found),dtype=int);observed=found
                if method=='legacy_distance':
                    radius=max(x1-x0,y1-y0)/2
                    proxy=np.minimum(np.linalg.norm(xy[added]-np.asarray(project.transform(*origin)),axis=1)/max(radius,1e-12),1)*horizon
                    score=float(np.sum(factors[added]*np.exp(-proxy**2/(2*settings['sigma']**2))))
                else:score=float(factors[added].sum())
                upper=score
        gi=int(ids[0]);iso_index=len(iso_features);iso_features.append(final_feature)
        accumulated.setdefault(gi,[]).append((score,len(observed),iso_index,upper,non_nested))
        if n%100==0:progress(f'机会评分 {n+1}/{len(origins)}')
    raw=np.array([np.mean([v[0] for v in values]) for values in accumulated.values()]);normalized=norm(raw);features=[]
    for (gi,values),score,value in zip(accumulated.items(),raw,normalized):
        f=grids[gi];props={**f['properties'],'iso_score_raw':float(score),'iso_score_combined':float(value),
            'iso_score_upper_bound':float(np.mean([v[3] for v in values])),'iso_method':method,
            'iso_minutes':int(settings['max_time']),'iso_index':values[0][2],'iso_indices':[v[2] for v in values],
            'sample_origin_count':len(values),'reachable_poi_count_mean':float(np.mean([v[1] for v in values])), 'has_isochrone':True}
        if settings['max_time']==45:props['iso_score_45']=float(value)
        features.append({**f,'properties':props})
    estimator={'cumulative':'sum of POI importance within measured horizon; no invented travel time',
        'time_bands':'first observed reachable time interval; score uses upper endpoint (lower opportunity bound), upper score uses lower endpoint; not precise OD times',
        'legacy_distance':'historical program: metric distance / isochrone bbox half-width * horizon; approximate, not observed duration'}[method]
    return features,iso_features,{'scored_grids':len(features),'unmatched_origins':unmatched,'incomplete_time_band_origins':incomplete,
        'method':method,'time_estimator':estimator,'non_nested_observations':sum(v[4] for values in accumulated.values() for v in values),
        'multiple_origins_in_one_grid':'mean opportunity score; iso_indices retain all source boundaries'}


def competitiveness(grids,records,settings):
    tree=STRtree([shape(f['geometry']) for f in grids]);by_id={f['properties']['grid_id']:i for i,f in enumerate(grids)}
    grouped={};valid=0;invalid=0;excluded={}
    sessions={'am_peak':'morning','offpeak':'midday','pm_peak':'evening','morning':'morning','midday':'midday','evening':'evening','snapshot':'snapshot'}
    for row in records:
        try:
            if row.get('origin_wgs84'):
                ids=tree.query(Point(row['origin_wgs84']),predicate='within')
                if len(ids)!=1:raise ValueError('unmatched origin')
                index=int(ids[0])
            else:index=by_id[row['grid_id']]
            bus=float(row['bus_time']);car=float(row['car_time']);transfers=float(row['transfer']);session=sessions[row['session']]
            if not all(np.isfinite([bus,car,transfers])) or bus<=0 or car<=0 or transfers<0 or not transfers.is_integer():raise ValueError('invalid travel time')
            if bus>settings['bus_max'] or car>settings['car_max']:raise ValueError('travel_time_filter')
            grouped.setdefault((index,session),[]).append(car/(bus+settings['alpha']*transfers));valid+=1
        except (KeyError,ValueError,TypeError) as error:
            invalid+=1;reason=str(error) if isinstance(error,ValueError) else 'missing_or_invalid_fields';excluded[reason]=excluded.get(reason,0)+1
    features=[]
    for index in sorted({k[0] for k in grouped}):
        p=dict(grids[index]['properties']);values=[]
        for session in ('morning','midday','evening','snapshot'):
            observations=grouped.get((index,session),[]);value=float(np.mean(observations)) if observations else None
            p['tci_'+session]=value;p['tci_'+session+'_samples']=len(observations)
            if value is not None:values.append(value)
        p['tci_mean']=float(np.mean(values));
        for session in ('morning','midday','evening','snapshot'):
            sample=grouped.get((index,session),[])
            p['tci_'+session+'_std']=float(np.std(sample,ddof=1)) if len(sample)>1 else None
        features.append({**grids[index],'properties':p})
    for session in ('morning','midday','evening','snapshot'):
        known=[f for f in features if f['properties']['tci_'+session] is not None]
        if not known:continue
        values=np.array([f['properties']['tci_'+session] for f in known]);lo,hi=np.percentile(values,[2,98])
        scaled=np.clip((values-lo)/(hi-lo),0,1) if hi>lo else np.full(len(values),.5)
        for feature,value in zip(known,scaled):feature['properties']['tci_'+session+'_norm']=float(value * 100)
    return features,{'visual_normalization':'per session, sample 2nd/98th percentiles to [0,100]; constant sample -> 50; not absolute city ranking','valid_records':valid,'excluded_records':invalid,'excluded_reasons':excluded,'grid_count':len(features),'formula':'mean(car_minutes/(bus_minutes+alpha*transfers)) per grid and period'}


def compute(city,data,demand_id,parameters=None,progress=lambda text:None, *, input_files=None):
    data=Path(data);demand_folder,demand=load_run(data,city['id'],demand_id);ensure_current_poi(data,demand);settings=normalize(parameters)
    source=sources(data,city['id'],demand_id,settings['isochrone']['max_time'],input_files);stations_file=data/'processed'/city['id']/'network/bus_stations_merged.csv'
    from .city_network import cached_city_network
    from .custom_inputs import source_directory_override
    bus=data/'CPTOND-2025/dataset/bus/shapefiles';poi_dir=data/'POI'
    if cached_city_network(city,source_directory_override(city,bus,poi_dir)['bus_dir'],data/'processed') is None:raise ValueError('当前数据源的线网尚未准备，请先重新准备城市输入')
    if not stations_file.is_file():raise ValueError('请先准备本市公交线网')
    signature={'schema':6,'demand_id':demand_id,'parameters':settings,'stations':[stations_file.stat().st_size,stations_file.stat().st_mtime_ns],
        'sources':{k:hashlib.sha256(v.read_bytes()).hexdigest() if v else None for k,v in source.items()}}
    digest=hashlib.sha256(json.dumps(signature,sort_keys=True).encode()).hexdigest()
    for p in sorted((data/'evaluation_runs'/city['id']).glob('*/summary.json'),reverse=True):
        saved=json.loads(p.read_text(encoding='utf-8'))
        if saved.get('workflow_signature')==digest:return {**saved,'cache_hit':True}
    working=data/'evaluation_work'/city['id']/run_id();working.mkdir(parents=True)
    grids=json.loads((demand_folder/'demand_grid.geojson').read_text(encoding='utf-8'))['features']
    progress('按当前参数计算覆盖度')
    stations=pd.read_csv(stations_file,usecols=['x_m','y_m','route_ids']);cov=coverage(grids,stations,demand['metric_crs'],settings['coverage'])
    shutil.copy2(demand_folder/'demand_grid.geojson',working/'demand_grid.geojson')
    save(working/'coverage_grid.geojson',{'type':'FeatureCollection','features':cov})
    available=['demand','coverage'];warnings=[];scope={};opp=[]
    if source['isochrone']:
        progress('检查等时圈状态和坐标')
        imported,scope=process(source['isochrone'],data/'isochrone_imports')
        polygons=json.loads((imported/'isochrone_polygons.geojson').read_text(encoding='utf-8'))['features']
        poi=pd.read_csv(data/demand['source_poi'],usecols=['x_m','y_m','class_1'])
        opp,iso,stats=opportunity(grids,polygons,poi,demand['metric_crs'],demand['parameters']['poi_weights'],settings['isochrone'],progress)
        save(working/'opportunity_grid.geojson',{'type':'FeatureCollection','features':opp});save(working/'isochrone_polys.geojson',{'type':'FeatureCollection','features':iso})
        scope.update(stats)
        if opp:available.append('isochrone')
        else:warnings.append('没有与所选时间上限一致、且可匹配本市格网的等时圈；请采集后导入')
    else:warnings.append('尚未导入本市等时圈：机会评分和综合评价未计算')
    if source['tci']:
        records=json.loads(source['tci'].read_text(encoding='utf-8-sig'));scope['navigation_collection']=records.get('metadata',{}) if isinstance(records,dict) else {};records=records.get('records',[]) if isinstance(records,dict) else records
        from .sample_analysis import navigation_stability
        save(working/'navigation_stability.json',navigation_stability(records,settings['tci']))
        tci,stats=competitiveness(grids,records,settings['tci']);save(working/'tci_grid.geojson',{'type':'FeatureCollection','features':tci});scope['tci']=stats
        if stats['excluded_records']:warnings.append(f"导航原始{len(records)}条，有效{stats['valid_records']}条，排除{stats['excluded_records']}条（耗时上限排除{stats['excluded_reasons'].get('travel_time_filter',0)}条）；不对缺失值补零")
        if tci:available.append('tci')
        else:warnings.append('导入的导航记录没有有效匹配样本')
    else:warnings.append('缺少原始公交/驾车时间：公交竞争力未重算，不修改历史聚合结果')
    if scope.get('subset_only'):
        warnings.append(f'等时圈仅采集样本；机会评分覆盖{len(opp)}/{len(grids)}个需求格网，不能视为全城评价')
    if scope.get('navigation_collection',{}).get('subset_only'):
        warnings.append('公交竞争力基于抽样导航，不代表全城普查；实时快照不代表历史高峰')
    composite_ready=False
    if len(opp)>=2:
        try:
            progress('计算熵权综合评价与供需分类')
            output,result=rebuild(working/'demand_grid.geojson',working/'coverage_grid.geojson',working/'opportunity_grid.geojson',data/'evaluation_runs',city=city['id'])
            composite_ready=True;available+=['composite','imbalance']
        except ValueError as error:warnings.append('综合评价未生成：'+str(error))
    if not composite_ready:
        identity=run_id();output=data/'evaluation_runs'/city['id']/identity;output.mkdir(parents=True)
        result={'run_id':identity,'city_id':city['id'],'grid_count':len(grids),'weights':None,'parameters':settings}
    for p in working.iterdir():shutil.copy2(p,output/p.name)
    result.update(pipeline='city_workflow_v1',workflow_signature=digest,demand_run_id=demand_id,city=city['name'],available_layers=available,
        composite_ready=composite_ready,has_opportunity='isochrone' in available,cache_hit=False,warnings=warnings,
        source_scope={'record_count':scope.get('processed_records',0),'valid_polygons':scope.get('valid_polygons',0),
            'failed_records':scope.get('failed_records',0),'sdk_compatibility_records':scope.get('sdk_compatibility_records',0),
            'pilot_only':bool(scope.get('subset_only')),'live_collection':(scope.get('collection_metadata') or {}).get('source')=='AMap.ArrivalRange',
            'legacy_policy_assumption':scope.get('legacy_policy_assumption',False),'scored_grid_count':len(opp),'demand_grid_count':len(grids)},
        workflow_parameters=settings,source_stats=scope,source_files={k:str(v.relative_to(data)) if v else None for k,v in source.items()})
    parameters_file=json.loads((output/'parameters.json').read_text(encoding='utf-8')) if (output/'parameters.json').is_file() else {}
    parameters_file.update(shared_demand=demand,workflow=settings,sources=signature['sources'],input_files={k:v.name if v else None for k,v in source.items()})
    save(output/'parameters.json',parameters_file);save(output/'summary.json',result)
    return result
