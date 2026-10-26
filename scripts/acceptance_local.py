"""Local API acceptance with labelled synthetic inputs; never calls external APIs."""
import csv
import io
import json
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'
OUT=ROOT/'data/workflow_validation'/('acceptance_'+datetime.now().strftime('%Y%m%d_%H%M%S'))
OUT.mkdir(parents=True,exist_ok=False)
RESULT={'scenario':'Synthetic acceptance only; not research evidence','checks':[]}

def save():
    (OUT/'summary.json').write_text(json.dumps(RESULT,ensure_ascii=False,indent=2),encoding='utf-8')

def request(path,payload=None,*,raw=None,status=200,headers=None):
    body=raw if raw is not None else json.dumps(payload).encode('utf-8') if payload is not None else None
    options={'Content-Type':'application/json' if raw is None else 'application/zip',**(headers or {})}
    req=Request(BASE+path,data=body,headers=options,method='POST' if body is not None else 'GET')
    try:
        response=urlopen(req,timeout=120);code=response.status;content=response.read();kind=response.headers.get('Content-Type','')
    except HTTPError as error:
        code=error.code;content=error.read();kind=error.headers.get('Content-Type','')
    if code!=status:raise AssertionError(f'{path}: expected {status}, got {code}: {content[:400]!r}')
    return json.loads(content) if 'json' in kind else content

def check(name,action):
    started=time.perf_counter()
    try:
        result=action();RESULT['checks'].append({'name':name,'status':'passed','seconds':round(time.perf_counter()-started,3)});save();print('PASS',name,flush=True);return result
    except Exception as error:
        RESULT['checks'].append({'name':name,'status':'failed','error':str(error)});save();raise

def wait(job,*,allow_partial=False):
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        value=request('/api/jobs/'+job['id'])
        if value['status']=='completed' or allow_partial and value['status']=='partial':return value
        if value['status'] not in ('pending','queued','running'):raise AssertionError({key:value.get(key) for key in ('id','status','phase','error')})
        time.sleep(.2)
    raise TimeoutError('Job did not complete: '+job['id'])

def fixture():
    stops=[];routes=[];segments=[];lons=[116.40,116.414,116.428]
    for route,order in [('ACCEPT_R1',[0,1,2]),('ACCEPT_R2',[2,1,0])]:
        for seq,index in enumerate(order,1):
            stops.append({'type':'Feature','geometry':{'type':'Point','coordinates':[lons[index],39.9]},'properties':{'stop_id':f'ACCEPT_S{index}','route_id':route,'sequence':seq,'name_cn':f'验收站{index}_合成非研究','route_cn':route}})
        routes.append({'type':'Feature','geometry':{'type':'LineString','coordinates':[[lons[index],39.9] for index in order]},'properties':{'route_id':route,'route_cn':route}})
        for left,right in zip(order,order[1:]):
            segments.append({'type':'Feature','geometry':{'type':'LineString','coordinates':[[lons[left],39.9],[(lons[left]+lons[right])/2,39.9001],[lons[right],39.9]]},'properties':{'s_stopid':f'ACCEPT_S{left}','e_stopid':f'ACCEPT_S{right}'}})
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,features in [('stops',stops),('routes',routes),('segments',segments)]:archive.writestr(name+'.geojson',json.dumps({'type':'FeatureCollection','features':features},ensure_ascii=False))
        text=io.StringIO();writer=csv.writer(text);writer.writerow(['id','name','class_1','lon','lat'])
        for i,lon in enumerate(lons):
            for j,category in enumerate(['公司企业','商务住宅']):writer.writerow([f'ACCEPT_P{i}{j}','验收POI_合成非研究',category,lon,39.9002+j*.0001])
        archive.writestr('poi.csv',text.getvalue())
    return output.getvalue()

def main():
    city='Accept'+datetime.now().strftime('%m%d')+'_'+uuid4().hex[:6]
    RESULT['city_id']=city
    meta={'id':city,'name':'合成验收城市_非研究','city_code':'010','province':'验收数据','longitude':116.414,'latitude':39.9}
    check('register_city',lambda:request('/api/cities',meta,status=201))
    check('duplicate_city_rejected',lambda:request('/api/cities',meta,status=400))
    check('unsafe_city_rejected',lambda:request('/api/cities',{**meta,'id':'../escape'},status=400))
    prefix='/api/cities/'+city
    content=fixture();(OUT/'synthetic_inputs.zip').write_bytes(content)
    check('bad_zip_rejected',lambda:request(prefix+'/custom-inputs?coordinate_system=WGS84',raw=b'not zip',status=400))
    check('inspect_inputs',lambda:request(prefix+'/custom-inputs/inspect?coordinate_system=WGS84',raw=content))
    imported=check('import_inputs',lambda:request(prefix+'/custom-inputs?coordinate_system=WGS84',raw=content))
    RESULT['input_version']=imported['version']
    before=request(prefix+'/custom-inputs')['active_version']
    check('invalid_crs_rejected',lambda:request(prefix+'/custom-inputs?coordinate_system=UNKNOWN',raw=content,status=400))
    assert request(prefix+'/custom-inputs')['active_version']==before
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as target:target.writestr('../escape.csv','no')
    check('zip_traversal_rejected',lambda:request(prefix+'/custom-inputs?coordinate_system=WGS84',raw=archive.getvalue(),status=400))
    prepared=check('prepare_inputs',lambda:wait(request(prefix+'/prepare-inputs',raw=b'',status=202)))
    assert prepared['result']['poi']['poi_count']==6
    template=check('headway_template',lambda:request(prefix+'/headway-template'))
    ids=[row['route_id'] for row in csv.DictReader(io.StringIO(template.decode('utf-8-sig')))]
    check('unknown_route_rejected',lambda:request(prefix+'/headways',raw=b'route_id,peak_headway_min\nNO_SUCH_ROUTE,8\n',status=400))
    timetable='route_id,start_time,end_time,interval_min\n'+''.join(f'{route},07:00,19:00,10\n' for route in ids)
    check('timetable_preview',lambda:request(prefix+'/timetable/inspect?mode=fixed',raw=timetable.encode()))
    service=check('timetable_import',lambda:request(prefix+'/timetable?mode=fixed',raw=timetable.encode()))
    assert service['am_headway_routes']==len(ids) and service['pm_headway_routes']==len(ids)
    settings=request(prefix+'/demand-runs')['defaults'];settings['hourly_demands']={'am_peak':200,'offpeak':80,'pm_peak':200}
    demand=check('generate_shared_demand',lambda:wait(request(prefix+'/demand-runs',settings,status=202)))['result']
    again=check('same_demand_uses_cache',lambda:wait(request(prefix+'/demand-runs',settings,status=202)))['result']
    assert demand['run_id']==again['run_id'] and again['cache_hit']
    options={'inputs':{'network':False,'poi':False},'demand':{'parameters':settings},'periods':[{'period':period,'total_demand':total} for period,total in settings['hourly_demands'].items()],'assignment':{'poi_count':6,'default_headway_min':15},'evaluation':{'input_files':{'isochrone':None,'tci':None}}}
    check('invalid_batch_total_rejected',lambda:request(prefix+'/batches',{**options,'periods':[{'period':'pm_peak','total_demand':0}]},status=400))
    batch=check('three_period_batch',lambda:wait(request(prefix+'/batches',options,status=202),allow_partial=True))
    RESULT['batch_id']=batch['id'];RESULT['demand_id']=demand['run_id']
    state=batch['state'];runs=state['assignments'];assert len(runs)==3
    for period,run in runs.items():
        assert abs(run['hourly_demand_pax']-settings['hourly_demands'][period])<1e-6
        assert abs(run['served_demand_pax_per_hour']+run['unserved_demand_pax_per_hour']-run['hourly_demand_pax'])<1e-6
        flow=check('flow_'+period,lambda run=run:request(prefix+'/runs/'+run['run_id']+'/flow'))
        assert flow['features'] and all(len(feature['geometry']['coordinates'])>=3 for feature in flow['features'])
    assert state['evaluation']['status']=='partial' and 'isochrone' in state['evaluation']['missing_layers']
    grids=request(prefix+'/demand-runs/'+demand['run_id']+'/grid')['features']
    metadata={'city_id':city,'demand_run_id':demand['run_id'],'minutes':[45],'source':'synthetic_acceptance_not_observed','complete':True,'subset_only':False,'bounds_crs':'GCJ-02'}
    polygons=[];navigation=[]
    for index,feature in enumerate(grids):
        props=feature['properties'];lon,lat=props['cx'],props['cy']
        polygons.append({'type':'Feature','geometry':None,'properties':{'wgsLon':lon,'wgsLat':lat,'minute':45,'status':'ok','policy':'BUS','bounds_crs':'GCJ-02','bounds':[[116.38,39.88],[116.46,39.88],[116.46,39.93],[116.38,39.93],[116.38,39.88]]}})
        for session in ('morning','midday','evening'):
            navigation.append({'grid_id':props['grid_id'],'origin_wgs84':[lon,lat],'session':session,'status':'ok','bus_time':20+index%5,'car_time':10+index%3,'transfer':index%2})
    iso=check('upload_synthetic_isochrones',lambda:request(prefix+'/workflow-inputs/isochrone',{'type':'FeatureCollection','metadata':metadata,'features':polygons}))
    tci=check('upload_synthetic_navigation',lambda:request(prefix+'/workflow-inputs/tci',{'metadata':metadata,'records':navigation}))
    full=check('all_evaluation_layers',lambda:wait(request(prefix+'/workflows',{'demand_run_id':demand['run_id'],'parameters':{'coverage':{'walk_full':150,'walk_zero':600},'tci':{'alpha':3}},'input_files':{'isochrone':iso['file'],'tci':tci['file']}},status=202)))['result']
    assert {'demand','coverage','isochrone','tci','composite','imbalance'}<=set(full['available_layers']),full
    assert full['workflow_parameters']['coverage']['walk_full']==150
    assert full['workflow_parameters']['tci']['alpha']==3
    check('composite_geometry',lambda:request(prefix+'/composite-runs/'+full['run_id']+'/grid'))
    state['evaluation']=full
    run=runs['pm_peak'];RESULT['run_ids']={key:value['run_id'] for key,value in runs.items()};RESULT['evaluation_id']=state['evaluation']['run_id']
    comparisons=check('scenario_comparison',lambda:request(prefix+'/runs/'+run['run_id']+'/comparison?mode=scenario'))
    assert len(comparisons['runs'])==3
    strict=check('strict_comparison',lambda:request(prefix+'/runs/'+run['run_id']+'/comparison?mode=strict'))
    assert {row['period'] for row in strict['runs']}=={'am_peak','pm_peak'}
    check('invalid_comparison_mode',lambda:request(prefix+'/runs/'+run['run_id']+'/comparison?mode=invalid',status=400))
    report=check('report_export',lambda:request(prefix+'/runs/'+run['run_id']+'/report'));(OUT/'report.html').write_bytes(report)
    for format in ('zip','gpkg','shp'):
        exported=check('export_'+format,lambda format=format:request(prefix+'/runs/'+run['run_id']+'/export?format='+format+'&report='+('0' if format=='gpkg' else '1')))
        (OUT/('result.'+('gpkg' if format=='gpkg' else format+'.zip'))).write_bytes(exported)
        if format!='gpkg':
            with zipfile.ZipFile(io.BytesIO(exported)) as z:assert z.testzip() is None
    project=check('project_export',lambda:request('/api/projects/export',{'city_id':city,'include_inputs':True,'include_results':True,'payload':{'name':'合成验收工程_非研究','demand_run':state['demand'],'assignment_run':run,'evaluation_run':state['evaluation'],'workspace':{'city_id':city,'demand_id':state['demand']['run_id'],'assignment_id':run['run_id'],'evaluation_id':state['evaluation']['run_id']}}}))
    (OUT/'project.zip').write_bytes(project)
    staged=check('project_import',lambda:request('/api/projects/import',raw=project,status=201));RESULT['project_id']=staged['project_id']
    check('project_results_restore',lambda:request('/api/projects/'+staged['project_id']+'/restore',{}))
    migrated=check('project_inputs_restore',lambda:request('/api/projects/'+staged['project_id']+'/restore',{'restore_inputs':True}))
    assert migrated['input_status']=='restored',migrated
    assert migrated['input_version']!=before
    check('migrated_inputs_prepare',lambda:wait(request(prefix+'/prepare-inputs',raw=b'',status=202)))
    assert request(prefix+'/inputs')['service']['am_headway_routes']==len(ids)
    RESULT['status']='passed';save();print('ACCEPTANCE_OUTPUT',OUT,flush=True)

if __name__=='__main__':main()
