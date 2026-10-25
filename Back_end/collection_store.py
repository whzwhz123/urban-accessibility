"""Versioned, idempotent collection chunks; never persist SDK credentials."""
import hashlib
import json
import time
from pathlib import Path
from datetime import datetime
from uuid import uuid4

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');temporary.replace(path)

def folder(data,city,identity):
    import re
    if not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('采集任务ID无效')
    path=Path(data)/'collection_runs'/city/identity
    if not (path/'plan.json').is_file():raise ValueError('采集任务不存在')
    return path

def create(data,plan):
    identity=uuid4().hex;path=Path(data)/'collection_runs'/plan['city_id']/identity
    save(path/'plan.json',plan)
    state={'id':identity,'city_id':plan['city_id'],'demand_run_id':plan['demand_run_id'],'kind':plan['kind'],'status':'ready','created_at':datetime.now().astimezone().isoformat(),'records':0,'published_file':None}
    save(path/'state.json',state);return state

def rows(data,city,identity):
    path=folder(data,city,identity);plan=json.loads((path/'plan.json').read_text(encoding='utf-8'));result={}
    for file in sorted((path/'chunks').glob('*.json')):
        payload=json.loads(file.read_text(encoding='utf-8'));items=payload.get('features',[]) if plan['kind']=='isochrone' else payload.get('records',[])
        for row in items:
            properties=row.get('properties',{}) if plan['kind']=='isochrone' else row
            key=properties.get('grid_id','')+':'+str(properties.get('minute','')) if plan['kind']=='isochrone' else properties.get('pair_id') or properties.get('grid_id','')+':'+str(properties.get('destination_wgs84',''))
            result[key]=row
    return plan,result

def summary(plan, records):
    statuses = [(row.get('properties', {}) if plan['kind'] == 'isochrone' else row).get('status') for row in records.values()]
    expected = len(plan['points']) * (len(plan['minutes']) if plan['kind'] == 'isochrone' else 1)
    valid = statuses.count('ok')
    no_route = statuses.count('no_route')
    return {'expected_records': expected, 'valid_records': valid, 'no_route_records': no_route,
            'failed_records': len(statuses) - valid - no_route, 'completed_records': valid + no_route,
            'complete': expected > 0 and valid + no_route == expected}


def read(data,city,identity):
    path=folder(data,city,identity);plan,records=rows(data,city,identity);state=json.loads((path/'state.json').read_text(encoding='utf-8'))
    state['plan']=plan;state['records']=len(records)
    state['stored_status'] = state['status']
    state.update(summary(plan, records))
    # Legacy collecting/completed flags are not proof of a live worker or completion.
    state['status'] = 'completed' if state['complete'] else ('ready' if state['status'] == 'ready' and not records else 'paused')
    state['completed_keys']=[key for key,row in records.items() if (row.get('properties',{}) if plan['kind']=='isochrone' else row).get('status') in ('ok', 'no_route')]
    return state

def append(data,city,identity,payload):
    path=folder(data,city,identity);plan=json.loads((path/'plan.json').read_text(encoding='utf-8'))
    metadata=payload.get('metadata',{})
    if metadata.get('city_id')!=city or metadata.get('demand_run_id')!=plan['demand_run_id']:raise ValueError('采集批次与任务城市或需求版本不一致')
    content=json.dumps(payload,sort_keys=True).encode();hash_value=hashlib.sha256(content).hexdigest();target=path/'chunks'/(str(time.time_ns())+'_'+hash_value+'.json')
    if not list((path/'chunks').glob('*_'+hash_value+'.json')):save(target,payload)
    state=json.loads((path/'state.json').read_text(encoding='utf-8'));plan,values=rows(data,city,identity);state.update(status='collecting',records=len(values),updated_at=datetime.now().astimezone().isoformat());save(path/'state.json',state)
    return {'saved':True,'chunk_id':hash_value}

def finish(data,city,identity,metadata=None):
    path=folder(data,city,identity);plan,records=rows(data,city,identity);state=json.loads((path/'state.json').read_text(encoding='utf-8'))
    items=list(records.values());expected=len(plan['points'])*(len(plan['minutes']) if plan['kind']=='isochrone' else 1)
    counts = summary(plan, records)
    valid = counts['valid_records']
    info={**(metadata or {}),'city_id':city,'demand_run_id':plan['demand_run_id'],'collection_job_id':identity,'source':'AMap.ArrivalRange' if plan['kind']=='isochrone' else 'AMap.Transfer + AMap.Driving','bounds_crs':'GCJ-02','policy':'BUS' if plan['kind']=='isochrone' else 'NO_SUBWAY / LEAST_TIME','sampling':plan['sampling'],'minutes':plan['minutes'],'subset_only':plan['subset_only'],'expected_records':expected,'complete':valid==expected,'valid_records':valid,'exported_at':datetime.now().astimezone().isoformat()}
    info.update(counts)
    payload={'type':'FeatureCollection','metadata':info,'features':items} if plan['kind']=='isochrone' else {'metadata':info,'records':items}
    name=plan['kind']+'_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]+('.geojson' if plan['kind']=='isochrone' else '.json')
    save(path/'latest_records.json',payload)
    if valid:save(Path(data)/'evaluation_inputs'/city/name,payload)
    state.update(counts)
    state.update(status='completed' if counts['complete'] else 'paused',records=len(items),published_file=name if valid else None);save(path/'state.json',state)
    return {'file':state['published_file'],'kind':plan['kind'],'valid_records':valid,'state':state}
