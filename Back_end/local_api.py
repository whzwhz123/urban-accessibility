import sys

# The assignment runner imports this module's live city registry.
# Running with -m must not create a second, stale registry under its package name.
if __name__ == '__main__':
    sys.modules[f'{__package__}.local_api'] = sys.modules[__name__]

import csv
import json
import re
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from uuid import uuid4
from datetime import datetime

import geopandas as gpd
import pyogrio
from shapely.geometry import mapping
from shapely import wkt

from .city_poi import cached_city_poi as _cached_poi, prepare_city_poi as _prepare_poi
from .city_network import cached_city_network as _cached_network, prepare_city_network as _prepare_network
from .city_service import cached_city_service as _cached_service, import_city_service as _import_service
from .city_evaluation import cached_city_evaluation, prepare_city_evaluation
from .task_store import TaskStore, TaskCancelled, assignment_preflight
from .custom_inputs import source_directory_override, cached_custom_city_poi, prepare_custom_city_poi
from .amap_sampling import collect_snapshot, sample_grid_pairs

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('URBAN_DATA_ROOT', ROOT / 'data'))
BUS = DATA / 'CPTOND-2025' / 'dataset' / 'bus' / 'shapefiles'
CITY_CODES = DATA / 'CPTOND-2025' / 'city_list' / 'AMap_adcode_citycode.csv'
POI = DATA / 'POI'
PROVINCES = {
    '11': '北京市', '12': '天津市', '13': '河北省', '14': '山西省',
    '15': '内蒙古自治区', '21': '辽宁省', '22': '吉林省', '23': '黑龙江省',
    '31': '上海市', '32': '江苏省', '33': '浙江省', '34': '安徽省',
    '35': '福建省', '36': '江西省', '37': '山东省', '41': '河南省',
    '42': '湖北省', '43': '湖南省', '44': '广东省', '45': '广西壮族自治区',
    '46': '海南省', '50': '重庆市', '51': '四川省', '52': '贵州省',
    '53': '云南省', '54': '西藏自治区', '61': '陕西省', '62': '甘肃省',
    '63': '青海省', '64': '宁夏回族自治区', '65': '新疆维吾尔自治区',
    '81': '香港-澳门特别行政区', '82': '香港-澳门特别行政区',
}


def cached_city_network(city,bus_dir,output_root):
    return _cached_network(city,source_directory_override(city,bus_dir,POI)['bus_dir'],output_root)
def prepare_city_network(city,bus_dir,output_root,progress=None):
    return _prepare_network(city,source_directory_override(city,bus_dir,POI)['bus_dir'],output_root,progress)
def cached_city_poi(city,poi_dir,output_root):
    directory=source_directory_override(city,BUS,poi_dir)['poi_dir']
    return cached_custom_city_poi(city,directory,output_root) if city.get('poi_format')=='custom_standard_v1' else _cached_poi(city,directory,output_root)
def prepare_city_poi(city,poi_dir,output_root,progress=None):
    directory=source_directory_override(city,BUS,poi_dir)['poi_dir']
    return prepare_custom_city_poi(city,directory,output_root,progress) if city.get('poi_format')=='custom_standard_v1' else _prepare_poi(city,directory,output_root,progress)
def cached_city_service(city,bus_dir,output_root):
    return _cached_service(city,source_directory_override(city,bus_dir,POI)['bus_dir'],output_root)
def import_city_service(city,bus_dir,output_root,content):
    return _import_service(city,source_directory_override(city,bus_dir,POI)['bus_dir'],output_root,content)

BUILTIN_CITIES = {}


def city_catalog():
    with CITY_CODES.open(encoding='utf-8-sig', newline='') as stream:
        names = {row['citycode']: row for row in csv.DictReader(stream)}
    cities = []
    for folder in sorted(BUS.iterdir()):
        if not folder.is_dir():
            continue
        files = {}
        for kind in ('routes', 'segments', 'stops'):
            path = next(folder.glob(f'*_bus_{kind}.shp'), None)
            files[kind] = path if path and all(
                path.with_suffix(ext).is_file() for ext in ('.shx', '.dbf', '.prj')
            ) else None
        if not files['routes']:
            continue
        info = pyogrio.read_info(files['routes'])
        code = str(gpd.read_file(files['routes'], rows=1)['city_code'].iloc[0])
        record = names.get(code)
        if not record:
            continue
        province = PROVINCES.get(record['adcode'][:2])
        poi_file = next((p for ext in ('.csv', '.xlsx') if (p := POI / f'{province}{ext}').is_file()), None) if province else None
        bounds = info['total_bounds']
        cities.append({
            'id': folder.name,
            'name': record['中文名'],
            'province': province,
            'city_code': code,
            'bounds': [round(float(value), 6) for value in bounds],
            'center': [round(float((bounds[0] + bounds[2]) / 2), 6),
                       round(float((bounds[1] + bounds[3]) / 2), 6)],
            'route_count': int(info['features']),
            'network_complete': all(files.values()),
            'poi_available': poi_file is not None,
            'poi_file': poi_file.name if poi_file else None,
            'has_evaluation': folder.name.casefold() == 'beijing',
        })
    for city in cities:
        BUILTIN_CITIES[city['id']] = dict(city)
        pointer=DATA/'custom_inputs'/city['id']/'active.json'
        if pointer.is_file():
            city.update(json.loads(pointer.read_text(encoding='utf-8'))['city']);city['has_evaluation']=False
        city['poi_prepared'] = cached_city_poi(city, POI, DATA / 'processed') is not None
        city['network_prepared'] = cached_city_network(city, BUS, DATA / 'processed') is not None
    return sorted(cities, key=lambda city: city['name'])


CITIES = city_catalog()
BY_ID = {city['id']: city for city in CITIES}
TASKS = TaskStore(DATA/'tasks')
class DurableJob(dict):
    def update(self,*args,**kwargs):
        super().update(*args,**kwargs)
        TASKS.update(self['id'],**self)
class JobRegistry(dict):
    def __setitem__(self,key,value):
        record=DurableJob(value);record.pop('task_id',None);super().__setitem__(key,record);TASKS.update(key,**record)
JOBS = JobRegistry()
for saved_task in TASKS.list():
    identity=saved_task.get('id',saved_task['task_id'])
    if saved_task.get('status') in ('queued','running'):
        saved_task.update(status='interrupted',phase='上次运行中断，可恢复')
    JOBS[identity]=saved_task

ACTIVE_JOBS = {}
ACTIVE_DEMANDS = {}
ACTIVE_ASSIGNMENTS = {}
ACTIVE_EVALUATIONS = {}
ACTIVE_NAVIGATION = {}
POOL = ThreadPoolExecutor(max_workers=1)


def run_input_job(job_id, city):
    job = JOBS[job_id]
    job['status'] = 'running'

    try:
        job['phase'] = '整理公交线网'
        network = prepare_city_network(
            city, BUS, DATA / 'processed', progress=lambda phase: job.update(phase=phase)
        )
        job['phase'] = '筛选本市 POI'

        def progress(scanned, kept):
            job['scanned_rows'] = scanned
            job['poi_count'] = kept

        poi = prepare_city_poi(city, POI, DATA / 'processed', progress=progress)
        city['network_prepared'] = True
        city['poi_prepared'] = True
        job.update(status='completed', phase='完成', result={'network': network, 'poi': poi})
    except Exception as error:
        job.update(status='failed', error=str(error))
    finally:
        ACTIVE_JOBS.pop(city['id'], None)


def run_assignment_job(job_id, city_id, options):
    from .assignment.run_city import run_city

    job = JOBS[job_id]
    job['status'] = 'running'
    try:
        result = run_city(
            city_id, options['period'], options['poi_count'], options['total_demand'],
            options.get('assumed_headway_min'), default_headway_min=options.get('default_headway_min',15), progress=lambda phase: job.update(phase=phase),
            parameters=options.get('parameters'), demand_run_id=options.get('demand_run_id'),
            cancel_check=lambda: bool(JOBS[job_id].get('cancel_requested')), resume_run_id=options.get('resume_run_id'),
            on_run_created=lambda identity: job.update(run_id=identity),
        )
        job.update(status='completed', phase='完成', result=result)
    except TaskCancelled as error:
        job.update(status='cancelled',phase='已停止，保留断点',run_id=getattr(error,'run_id',job.get('run_id')),error=str(error))
    except Exception as error:
        job.update(status='failed', error=str(error))
    finally:
        ACTIVE_ASSIGNMENTS.pop(city_id, None)


def run_evaluation_job(job_id, city_id):
    job = JOBS[job_id]
    job['status'] = 'running'
    try:
        result = prepare_city_evaluation(
            BY_ID[city_id], BUS, POI, DATA / 'processed',
            progress=lambda phase: job.update(phase=phase),
        )
        job.update(status='completed', phase='completed', result=result)
    except Exception as error:
        job.update(status='failed', error=str(error))
    finally:
        ACTIVE_EVALUATIONS.pop(city_id, None)


def run_opportunity_job(job_id, source, sigma):
    from .data_process.evaluation_pipeline import run
    job = JOBS[job_id]
    job['status'] = 'running'
    try:
        result = run(source, DATA, sigma, lambda phase: job.update(phase=phase))
        job.update(status='completed', phase='完成', result=result)
    except Exception as error:
        job.update(status='failed', error=str(error))
    finally:
        ACTIVE_EVALUATIONS.pop('opportunity:Beijing', None)


def run_shared_job(job_id,city,kind,options):
    job=JOBS[job_id];job['status']='running'
    try:
        if kind=='demand':
            from .city_demand import build
            result=build(city,DATA,options,lambda phase:job.update(phase=phase))
        else:
            from .city_workflow import compute
            result=compute(city,DATA,options['demand_run_id'],options.get('parameters'),lambda phase:job.update(phase=phase),input_files=options.get('input_files'))
        job.update(status='completed',phase='完成',result=result)
    except Exception as error:job.update(status='failed',error=str(error))
    finally:
        (ACTIVE_DEMANDS if kind=='demand' else ACTIVE_EVALUATIONS).pop(city['id'],None)


def run_navigation_job(job_id, city_id, preview):
    job = JOBS[job_id]
    job['status'] = 'running'
    try:
        run_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:8]
        output = DATA / 'runs' / city_id / 'navigation' / run_id
        result = collect_snapshot(BY_ID[city_id], preview, output,
                                  progress=lambda phase: job.update(phase=phase))
        result['run_id'] = run_id
        job.update(status='completed', phase='完成', result=result)
    except Exception as error:
        job.update(status='failed', error=str(error))
    finally:
        ACTIVE_NAVIGATION.pop(city_id, None)


def city_runs(city_id):
    folder = DATA / 'runs' / city_id
    runs = []
    for path in sorted(folder.glob('*/summary.json'), reverse=True)[:30]:
        from .run_history import recorded_run
        runs.append(recorded_run(path.parent))
    return runs


@lru_cache(maxsize=2)
def run_flow(city_id, run_id):
    import pandas as pd

    path = DATA / 'runs' / city_id / run_id / 'physical_segment_flows.csv'
    frame = pd.read_csv(path, usecols=[
        'edge_idx', 'geometry_wkt', 'from_name', 'to_name',
        'capacity_pax_per_hour', 'desired_flow_pax_per_hour',
        'served_flow_pax_per_hour', 'desired_vc', 'served_vc',
    ])
    features = []
    for row in frame.itertuples(index=False):
        if not row.geometry_wkt or pd.isna(row.geometry_wkt):
            continue
        features.append({
            'type': 'Feature', 'geometry': mapping(wkt.loads(row.geometry_wkt)),
            'properties': {
                'edge_idx': int(row.edge_idx), 'from_name': str(row.from_name),
                'to_name': str(row.to_name),
                'capacity_pax_per_hour': float(row.capacity_pax_per_hour),
                'desired_flow_pax_per_hour': max(0.0, float(row.desired_flow_pax_per_hour)),
                'served_flow_pax_per_hour': max(0.0, float(row.served_flow_pax_per_hour)),
                'desired_vc': max(0.0, float(row.desired_vc)), 'served_vc': max(0.0, float(row.served_vc)),
            },
        })
    return {'type': 'FeatureCollection', 'features': features}


@lru_cache(maxsize=4)
def city_preview(city_id):
    folder = source_directory_override(BY_ID[city_id],BUS,POI)['bus_dir'] / city_id
    features = []
    counts = {}
    for kind, limit in (('routes', 600), ('stops', 1200)):
        path = next(folder.glob(f'*_bus_{kind}.shp'), None)
        if not path:
            counts[kind] = {'shown': 0, 'total': 0}
            continue
        frame = gpd.read_file(path)
        total = len(frame)
        sample = frame.iloc[::max(1, (total + limit - 1) // limit)].head(limit)
        if sample.crs and sample.crs.to_epsg() != 4326:
            sample = sample.to_crs(4326)
        label = 'route_en' if kind == 'routes' else 'name_en'
        for _, row in sample.iterrows():
            if row.geometry is not None and not row.geometry.is_empty:
                features.append({'type': 'Feature', 'geometry': mapping(row.geometry),
                                 'properties': {'kind': kind, 'name': str(row.get(label) or '')}})
        counts[kind] = {'shown': len(sample), 'total': total}
    return {'type': 'FeatureCollection', 'features': features, 'counts': counts}


@lru_cache(maxsize=4)
def full_network_map(city_id, version):
    import pandas as pd
    folder=DATA/'processed'/city_id/'network'
    edges=pd.read_csv(folder/'bus_edges_topology.csv');stops=pd.read_csv(folder/'bus_stations_merged.csv')
    routes=[];stations=[]
    for row in edges.to_dict('records'):
        geometry=row.pop('geometry_wkt',None)
        if not isinstance(geometry,str):continue
        props={k:v for k,v in row.items() if isinstance(v,(str,int,float)) and not (isinstance(v,float) and not __import__('math').isfinite(v))}
        routes.append({'type':'Feature','geometry':mapping(wkt.loads(geometry)),'properties':props})
    for row in stops.to_dict('records'):
        stations.append({'type':'Feature','geometry':{'type':'Point','coordinates':[float(row['lon']),float(row['lat'])]},
            'properties':{'name':row['station_name'],'station_id':row['station_id'],'line_count':int(row['n_routes'])}})
    return {'routes':{'type':'FeatureCollection','features':routes},'stops':{'type':'FeatureCollection','features':stations}}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/cities':
            self.respond({'cities': CITIES})
        elif path == '/api/jobs':
            city_id=parse_qs(urlparse(self.path).query).get('city_id',[None])[0]
            self.respond({'jobs':[job for job in JOBS.values() if city_id is None or job.get('city_id')==city_id]})
        elif re.fullmatch(r'/api/cities/[^/]+/custom-inputs',path):
            from .input_versions import list_versions
            city_id=path.split('/')[3]
            if city_id not in BY_ID:self.respond({'error':'城市不存在'},404)
            else:self.respond({'active_version':BY_ID[city_id].get('custom_input_version'),'versions':list_versions(DATA,city_id)})
        elif re.fullmatch(r'/api/cities/[^/]+/custom-input-template',path):
            from .custom_inputs import build_template_package
            body=build_template_package();self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition','attachment; filename="custom_city_template.zip"');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        elif re.fullmatch(r'/api/cities/[^/]+/network-map',path):
            city_id=path.split('/')[3];file=DATA/'processed'/city_id/'network/bus_edges_topology.csv'
            if city_id not in BY_ID or not file.is_file():self.respond({'error':'请先准备城市线网'},409)
            else:self.respond(full_network_map(city_id,file.stat().st_mtime_ns))
        elif re.fullmatch(r'/api/cities/[^/]+/demand-runs', path):
            from .city_demand import defaults,population_file,ALIASES
            city_id=path.split('/')[3];city=BY_ID.get(city_id)
            if city is None:self.respond({'error':'城市不存在'},404);return
            runs=[json.loads(p.read_text(encoding='utf-8')) for p in sorted((DATA/'demand_runs'/city_id).glob('*/summary.json'),reverse=True)]
            from .city_demand import ensure_current_poi
            prepared=cached_city_poi(city,POI,DATA/'processed') is not None
            for run in runs:
                try:
                    if not prepared:raise ValueError('输入尚未准备')
                    ensure_current_poi(DATA,run);run['current_inputs']=True
                except ValueError:run['current_inputs']=False
            self.respond({'runs':runs,'defaults':defaults(city),'category_aliases':ALIASES,'population_available':population_file(DATA,city_id) is not None,'job':JOBS.get(ACTIVE_DEMANDS.get(city_id))})
        elif re.fullmatch(r'/api/cities/[^/]+/demand-runs/[0-9]{8}_[0-9]{6}_[0-9a-f]{8}/(grid|summary)', path):
            city_id,run_id,kind=path.split('/')[3],path.split('/')[5],path.split('/')[6]
            file=DATA/'demand_runs'/city_id/run_id/('demand_grid.geojson' if kind=='grid' else 'summary.json')
            if city_id not in BY_ID or not file.is_file():self.respond({'error':'需求版本不存在'},404)
            else:self.respond(json.loads(file.read_text(encoding='utf-8')))
        elif re.fullmatch(r'/api/cities/[^/]+/workflow-settings', path):
            from .city_workflow import DEFAULTS,sources,input_versions
            city_id=path.split('/')[3]
            if city_id not in BY_ID:self.respond({'error':'城市不存在'},404);return
            try:
                query=parse_qs(urlparse(self.path).query);demand_id=query.get('demand_run_id',[None])[0];minutes=int(query.get('minutes',['45'])[0])
                inputs=sources(DATA,city_id,demand_id,minutes)
                self.respond({'defaults':DEFAULTS,'inputs':{k:v.name if v else None for k,v in inputs.items()},'input_versions':input_versions(DATA,city_id,demand_id,minutes),'job':JOBS.get(ACTIVE_EVALUATIONS.get(city_id))})
            except (ValueError,TypeError) as error:self.respond({'error':str(error)},400)
        elif re.fullmatch(r'/api/cities/[^/]+/workflow-inputs/(isochrone|tci)/[a-z0-9_]+\.(geojson|json)',path):
            city_id,kind,name=path.split('/')[3],path.split('/')[5],path.split('/')[6]
            file=DATA/'evaluation_inputs'/city_id/name
            if city_id not in BY_ID or not name.startswith(kind+'_') or not file.is_file():self.respond({'error':'采集版本不存在'},404)
            else:self.respond(json.loads(file.read_text(encoding='utf-8')))
        elif re.fullmatch(r'/api/cities/[^/]+/collection-runs(/[0-9a-f]{32})?',path):
            from .collection_store import read
            city_id=path.split('/')[3]
            try:
                if len(path.split('/'))==6:self.respond(read(DATA,city_id,path.split('/')[5]))
                else:self.respond({'runs':[json.loads(p.read_text(encoding='utf-8')) for p in sorted((DATA/'collection_runs'/city_id).glob('*/state.json'),reverse=True)]})
            except ValueError as error:self.respond({'error':str(error)},400)
        elif re.fullmatch(r'/api/cities/[^/]+/amap-plan', path):
            try:
                from .amap_collection import plan
                query=parse_qs(urlparse(self.path).query);city_id=path.split('/')[3]
                city=BY_ID[city_id]
                self.respond(plan(city,DATA,query['demand_run_id'][0],query.get('kind',['isochrone'])[0],int(query.get('count',['20'])[0]),int(query.get('minutes',['45'])[0]),destinations=int(query.get('destinations',['3'])[0]),method=query.get('method',['cumulative'])[0],interval_ms=int(query.get('interval_ms',['1000'])[0]),request_budget=int(query.get('budget',['500'])[0]),session=query.get('session',['snapshot'])[0]))
            except (ValueError,KeyError,FileNotFoundError) as error:self.respond({'error':str(error)},400)
        elif path == '/api/amap/arrival-collector':
            body=(ROOT/'Back_end/data_process/arrival_range_collector.js').read_bytes()
            self.send_response(200);self.send_header('Content-Type','application/javascript; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        elif re.fullmatch(r'/api/cities/[^/]+/arrival-script', path):
            from .data_process.isochrone_AmapJS import make_config, generate_script
            city_id = unquote(path.split('/')[3])
            query = parse_qs(urlparse(self.path).query)
            demand_identity = query.get('demand_run_id', [None])[0]
            if city_id != 'Beijing' and not demand_identity:
                self.respond({'error': '该城市尚未准备评价格网，请使用命令行指定格网文件'}, 409)
                return
            try:
                query = parse_qs(urlparse(self.path).query)
                limit = int(query['limit'][0]) if 'limit' in query else None
                if limit is not None and not 1 <= limit <= 100000:
                    raise ValueError('采集点数须为正整数')
                sample_source = DATA / 'isochrone_45min_6districts.geojson'
                if not sample_source.is_file():
                    sample_source = ROOT / 'public/data/output/isochrone_grid_beijing.geojson'
                if demand_identity:
                    from .city_demand import load_run
                    folder,_=load_run(DATA,city_id,demand_identity)
                    sample_source=folder/'demand_grid.geojson'
                minutes=int(query.get('minutes',['45'])[0])
                if not 1<=minutes<=120:raise ValueError('等时圈上限为1–120分钟')
                config = make_config(sample_source, minutes=(minutes,), limit=limit)
                body = generate_script(config).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/javascript; charset=utf-8')
                self.send_header('Content-Disposition', f'attachment; filename="{config["collection_id"]}.js"')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, FileNotFoundError) as error:
                self.respond({'error': str(error)}, 400)
        elif re.fullmatch(r'/api/cities/[^/]+/composite-runs', path):
            city_id = unquote(path.split('/')[3])
            if city_id not in BY_ID:
                self.respond({'error': '城市不存在'}, 404)
            else:
                folder = DATA / 'evaluation_runs' / city_id
                runs = [json.loads(p.read_text(encoding='utf-8')) for p in sorted(folder.glob('*/summary.json'), reverse=True)]
                self.respond({'runs': runs})
        elif re.fullmatch(r'/api/cities/[^/]+/composite-runs/[0-9]{8}_[0-9]{6}_[0-9a-f]{8}/(grid|summary|parameters|opportunity|isochrones|demand|coverage|tci)', path):
            _, _, _, city_id, _, run_id, kind = path.split('/')
            filename = {'grid': 'composite.geojson', 'summary': 'summary.json', 'parameters': 'parameters.json', 'opportunity': 'opportunity_grid.geojson', 'isochrones': 'isochrone_polys.geojson', 'demand': 'demand_grid.geojson', 'coverage': 'coverage_grid.geojson', 'tci': 'tci_grid.geojson'}[kind]
            file_path = DATA / 'evaluation_runs' / city_id / run_id / filename
            if city_id not in BY_ID or not file_path.is_file():
                self.respond({'error': '综合评价结果不存在'}, 404)
            else:
                self.respond(json.loads(file_path.read_text(encoding='utf-8')))
        elif re.fullmatch(r'/api/cities/[^/]+/(runs|composite-runs)/[0-9]{8}_[0-9]{6}_[0-9a-f]{8}/export',path):
            from .artifact_exports import package
            city_id,kind,identity=path.split('/')[3:6]
            try:
                if city_id not in BY_ID:raise ValueError('城市不存在')
                file=package(DATA,city_id,identity,'assignment' if kind=='runs' else 'evaluation')
                self.send_response(200);self.send_header('Content-Type','application/zip');self.send_header('Content-Disposition',f'attachment; filename="{city_id}_{identity}.zip"');self.send_header('Content-Length',str(file.stat().st_size));self.end_headers()
                with file.open('rb') as stream:shutil.copyfileobj(stream,self.wfile)
            except ValueError as error:self.respond({'error':str(error)},400)
        elif re.fullmatch(r'/api/cities/[^/]+/preview', path):
            city_id = unquote(path[len('/api/cities/'):-len('/preview')]).strip('/')
            if city_id not in BY_ID:
                self.respond({'error': '城市不存在'}, 404)
            else:
                self.respond(city_preview(city_id))
        elif re.fullmatch(r'/api/cities/[^/]+/navigation/latest', path):
            city_id = path.split('/')[3]
            if city_id not in BY_ID:
                self.respond({'error': '城市不存在'}, 404)
            else:
                summaries = sorted((DATA / 'runs' / city_id / 'navigation').glob('*/summary.json'), reverse=True)
                self.respond({'result': json.loads(summaries[0].read_text(encoding='utf-8')) if summaries else None})
        elif re.fullmatch(r'/api/cities/[^/]+/navigation/preview', path):
            city_id = path.split('/')[3]
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': '城市不存在'}, 404)
            else:
                try:
                    count = int(parse_qs(urlparse(self.path).query).get('pairs', ['10'])[0])
                    preview = sample_grid_pairs(city, DATA / 'processed', count)
                    self.respond({'preview': preview, 'key_configured': bool(os.environ.get('AMAP_API_KEY')),
                                  'job': JOBS.get(ACTIVE_NAVIGATION.get(city_id))})
                except (ValueError, TypeError) as error:
                    self.respond({'error': str(error)}, 400)
        elif re.fullmatch(r'/api/cities/[^/]+/evaluations/grid', path):
            city_id = path.split('/')[3]
            city = BY_ID.get(city_id)
            saved = cached_city_evaluation(city, BUS, POI, DATA / 'processed') if city else None
            if saved is None:
                self.respond({'error': 'City evaluation is not ready'}, 404)
            else:
                file_path = DATA / 'processed' / city_id / 'evaluation' / 'poi_coverage_grid.geojson'
                self.send_response(200)
                self.send_header('Content-Type', 'application/geo+json; charset=utf-8')
                self.send_header('Content-Length', str(file_path.stat().st_size))
                self.end_headers()
                try:
                    with file_path.open('rb') as source:
                        shutil.copyfileobj(source, self.wfile)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        elif re.fullmatch(r'/api/cities/[^/]+/evaluations', path):
            city_id = path.split('/')[3]
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': 'City not found'}, 404)
            else:
                self.respond({
                    'result': cached_city_evaluation(city, BUS, POI, DATA / 'processed'),
                    'job': JOBS.get(ACTIVE_EVALUATIONS.get(city_id)),
                })
        elif path.startswith('/api/cities/') and path.endswith('/headway-template'):
            city_id = unquote(path[len('/api/cities/'):-len('/headway-template')]).strip('/')
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': '城市不存在'}, 404)
            elif cached_city_network(city, BUS, DATA / 'processed') is None:
                self.respond({'error': '请先准备该城市的公交线网'}, 409)
            else:
                template = DATA / 'processed' / city_id / 'network' / 'bus_headway_template.csv'
                body = template.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', f'attachment; filename="{city_id}_bus_headways.csv"')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        elif path.startswith('/api/cities/') and path.endswith('/runs'):
            city_id = unquote(path[len('/api/cities/'):-len('/runs')]).strip('/')
            self.respond({'runs': city_runs(city_id), 'job': JOBS.get(ACTIVE_ASSIGNMENTS.get(city_id))} if city_id in BY_ID else {'error': '城市不存在'},
                         200 if city_id in BY_ID else 404)
        elif re.fullmatch(r'/api/cities/[^/]+/runs/[^/]+/flow-scale',path):
            import pandas as pd
            city_id,identity=path.split('/')[3],path.split('/')[5]
            if city_id not in BY_ID or not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}',identity):self.respond({'error':'实验无效'},400);return
            base_file=DATA/'runs'/city_id/identity/'summary.json'
            if not base_file.is_file():self.respond({'error':'实验不存在'},404);return
            from .run_history import recorded_run
            reference=recorded_run(base_file.parent);group=[]
            keys=['demand_run_id','poi_count','hourly_demand_pax','default_headway_min','headway_upload_sha256','shared_demand_signature','algorithm']
            for candidate in city_runs(city_id):
                if all(candidate.get(k)==reference.get(k) for k in keys) and {k:v for k,v in candidate.get('parameters',{}).items() if k!='period'}=={k:v for k,v in reference.get('parameters',{}).items() if k!='period'}:group.append(candidate['run_id'])
            maxima={k:0. for k in ['desired_flow_pax_per_hour','served_flow_pax_per_hour','desired_vc','served_vc']}
            for run in group:
                file=DATA/'runs'/city_id/run/'physical_segment_flows.csv'
                if file.is_file():
                    frame=pd.read_csv(file,usecols=list(maxima))
                    for k in maxima:maxima[k]=max(maxima[k],float(frame[k].max()))
            maxima['served_vc']=1.;maxima['desired_vc']=max(1.,maxima['desired_vc'])
            self.respond({'maxima':maxima,'runs':group,'basis':'same city, shared demand, POI count, hourly demand, headway provenance and algorithm; period comparisons use common scale'})
        elif re.fullmatch(r'/api/cities/[^/]+/runs/[^/]+/flow', path):
            _, _, _, city_id, _, run_id, _ = path.split('/')
            if city_id not in BY_ID or not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}', run_id):
                self.respond({'error': '结果不存在'}, 404)
            elif not (DATA / 'runs' / city_id / run_id / 'summary.json').is_file():
                self.respond({'error': '结果不存在'}, 404)
            else:
                self.respond(run_flow(city_id, run_id))
        elif re.fullmatch(r'/api/cities/[^/]+/runs/[^/]+/files/[^/]+', path):
            _, _, _, city_id, _, run_id, _, filename = path.split('/')
            allowed = {
                'summary.json', 'physical_segment_flows.csv',
                'route_section_flows.csv', 'assignment_iterations.csv',
                'capacity_reallocation.csv', 'parameters.json', 'resolved_headways.csv', 'preflight.json', 'preflight_measured.json', 'preflight_final.json',
            }
            file_path = DATA / 'runs' / city_id / run_id / filename
            if (city_id not in BY_ID or
                    not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}', run_id) or
                    filename not in allowed or not file_path.is_file()):
                self.respond({'error': 'Result file not found'}, 404)
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8' if filename.endswith('.json') else 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', f'attachment; filename="{city_id}_{run_id}_{filename}"')
                self.send_header('Content-Length', str(file_path.stat().st_size))
                self.end_headers()
                try:
                    with file_path.open('rb') as source:
                        shutil.copyfileobj(source, self.wfile)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        elif path.startswith('/api/cities/') and path.endswith('/inputs'):
            city_id = unquote(path[len('/api/cities/'):-len('/inputs')]).strip('/')
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': '城市不存在'}, 404)
            else:
                poi = cached_city_poi(city, POI, DATA / 'processed')
                network = cached_city_network(city, BUS, DATA / 'processed')
                service = cached_city_service(city, BUS, DATA / 'processed')
                self.respond({'poi': poi, 'network': network, 'service': service,
                              'job': JOBS.get(ACTIVE_JOBS.get(city_id))})
        elif path.startswith('/api/jobs/'):
            job = JOBS.get(path[len('/api/jobs/'):])
            self.respond(job if job else {'error': '任务不存在'}, 200 if job else 404)
        else:
            self.respond({'error': '接口不存在'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if re.fullmatch(r'/api/jobs/[0-9a-f]{32}/(cancel|resume)',path):
            identity,action=path.split('/')[3:5];job=JOBS.get(identity)
            if not job or job.get('kind')!='assignment':self.respond({'error':'仅分配任务支持此控制'},400);return
            if action=='cancel':
                job.update(cancel_requested=True);self.respond(job);return
            if job.get('status') not in ('cancelled','interrupted','failed'):
                self.respond({'error':'任务不是可恢复状态'},400);return
            city_id=job['city_id']
            if ACTIVE_ASSIGNMENTS.get(city_id):self.respond({'error':'本城市另有分配任务运行'},409);return
            options={**job['options'],'resume_run_id':job.get('run_id')}
            job.update(status='queued',phase='准备恢复',cancel_requested=False);ACTIVE_ASSIGNMENTS[city_id]=identity
            POOL.submit(run_assignment_job,identity,city_id,options);self.respond(job,202);return
        if re.fullmatch(r'/api/cities/[^/]+/assignment-preflight',path):
            try:
                size=int(self.headers.get('Content-Length','0'));value=json.loads(self.rfile.read(size));parameters=__import__('Back_end.assignment.parameters',fromlist=['normalize_parameters']).normalize_parameters(value.get('parameters'))
                count=int(value['poi_count']);city_id=path.split('/')[3];poi=cached_city_poi(BY_ID[city_id],POI,DATA/'processed')
                if count<2 or not poi or count>poi['poi_count']:raise ValueError('POI数量超过本市可用数据或尚未准备')
                estimate=assignment_preflight(count,parameters)
                references=[run for run in city_runs(city_id) if run.get('total_seconds') is not None and (not value.get('period') or run.get('period')==value['period'])]
                references.sort(key=lambda run:abs(run.get('poi_count',0)-count))
                estimate['historical_references']=[{k:run.get(k) for k in ['run_id','poi_count','hourly_demand_pax','total_seconds','candidate_paths','converged','period']} for run in references[:3]]
                estimate['runtime_note']='Reference measurements only; parameters, solver version, loading and cache warmness may differ; not a guaranteed runtime forecast'
                self.respond(estimate)
            except (ValueError,KeyError) as error:self.respond({'error':str(error)},400)
            return
        if re.fullmatch(r'/api/cities/[^/]+/custom-inputs/activate',path):
            from .input_versions import activate
            city_id=path.split('/')[3]
            try:
                if city_id not in BY_ID:raise ValueError('城市不存在')
                if any(registry.get(city_id) for registry in (ACTIVE_ASSIGNMENTS,ACTIVE_JOBS,ACTIVE_DEMANDS,ACTIVE_EVALUATIONS)):raise ValueError('本市任务运行中，请结束后再切换数据源')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1000:raise ValueError('请求内容无效')
                version=json.loads(self.rfile.read(size)).get('version')
                city=activate(DATA,BUILTIN_CITIES[city_id],version)
                city['network_prepared']=cached_city_network(city,BUS,DATA/'processed') is not None
                city['poi_prepared']=cached_city_poi(city,POI,DATA/'processed') is not None
                BY_ID[city_id].clear();BY_ID[city_id].update(city);city_preview.cache_clear()
                self.respond({'city':city,'version':version,'message':'已切换输入版本；原始数据和所有实验均保留，请检查数据准备状态。'})
            except (ValueError,KeyError,json.JSONDecodeError) as error:self.respond({'error':str(error)},400)
            return
        if re.fullmatch(r'/api/cities/[^/]+/custom-inputs',path):
            from .custom_inputs import import_package
            city_id=path.split('/')[3]
            try:
                if city_id not in BY_ID:raise ValueError('城市不存在')
                if any(registry.get(city_id) for registry in (ACTIVE_ASSIGNMENTS,ACTIVE_JOBS,ACTIVE_DEMANDS,ACTIVE_EVALUATIONS)):raise ValueError('本市任务运行中，请结束后再切换数据源')
                size=int(self.headers.get('Content-Length','0'));query=parse_qs(urlparse(self.path).query)
                if not 0<size<=100*1024*1024:raise ValueError('ZIP最大100MiB')
                result=import_package(BY_ID[city_id],DATA,self.rfile.read(size),query.get('filename',['inputs.zip'])[0],query['coordinate_system'][0])
                result['city']['has_evaluation']=False
                result['city']['source_directory_override']={k:Path(v).resolve().relative_to(DATA.resolve()).as_posix() for k,v in result['source_overrides'].items()}
                result['city']['custom_input_manifest']=Path(result['manifest']).resolve().relative_to(DATA.resolve()).as_posix()
                BY_ID[city_id].update(result['city']);city_preview.cache_clear()
                from .collection_store import save
                save(DATA/'custom_inputs'/city_id/'active.json',{'city':result['city']})
                self.respond(result)
            except (ValueError,KeyError) as error:self.respond({'error':str(error)},400)
            return
        if re.fullmatch(r'/api/cities/[^/]+/collection-runs(/[0-9a-f]{32}/(chunks|finish))?',path):
            from .collection_store import create,append,finish
            parts=path.split('/');city_id=parts[3]
            try:
                if city_id not in BY_ID:raise ValueError('城市不存在')
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=10*1024*1024:raise ValueError('批次最多10MB')
                payload=json.loads(self.rfile.read(size))
                if len(parts)==5:
                    from .amap_collection import plan
                    result=create(DATA,plan(BY_ID[city_id],DATA,payload['demand_run_id'],payload['kind'],int(payload.get('count',20)),int(payload.get('minutes',45)),destinations=int(payload.get('destinations',3)),method=payload.get('method','cumulative'),request_budget=int(payload.get('budget',500)),session=payload.get('session','snapshot'),interval_ms=int(payload.get('interval_ms',1000))))
                elif parts[6]=='chunks':result=append(DATA,city_id,parts[5],payload)
                else:result=finish(DATA,city_id,parts[5],payload.get('metadata'))
                self.respond(result)
            except (ValueError,KeyError,TypeError) as error:self.respond({'error':str(error)},400)
            return
        if re.fullmatch(r'/api/cities/[^/]+/(demand-runs|workflows|population|workflow-inputs/(isochrone|tci))',path):
            city_id=path.split('/')[3];city=BY_ID.get(city_id)
            if city is None:self.respond({'error':'城市不存在'},404);return
            action=path.split('/')[4];size=int(self.headers.get('Content-Length','0'))
            maximum=512*1024*1024 if action=='population' else 50*1024*1024
            if not 0<size<=maximum:self.respond({'error':'文件或请求内容超过限制'},400);return
            try:
                if action=='population':
                    from .city_demand import run_id
                    folder=DATA/'population'/city_id;folder.mkdir(parents=True,exist_ok=True)
                    file=folder/(run_id()+'.tif')
                    with file.open('wb') as stream:
                        remaining=size
                        while remaining:
                            block=self.rfile.read(min(1024*1024,remaining))
                            if not block:raise ValueError('人口栅格上传中断')
                            stream.write(block);remaining-=len(block)
                    try:
                        import rasterio
                        with rasterio.open(file) as raster:
                            if raster.crs is None:raise ValueError('人口栅格缺少坐标系')
                    except Exception:
                        file.rename(file.with_suffix('.invalid'))
                        raise ValueError('人口栅格无法读取或缺少坐标系，未启用该文件')
                    self.respond({'file':file.name,'population_available':True});return
                payload=json.loads(self.rfile.read(size))
                if action=='workflow-inputs':
                    from .city_demand import run_id
                    kind=path.split('/')[5]
                    metadata=payload.get('metadata',{}) if isinstance(payload,dict) else {}
                    if metadata.get('city_id') and metadata['city_id']!=city_id:raise ValueError('采集数据与当前城市不一致')
                    if metadata.get('demand_run_id'):
                        from .city_demand import load_run
                        load_run(DATA,city_id,metadata['demand_run_id'])
                    if kind=='isochrone' and (payload.get('type')!='FeatureCollection' or not payload.get('features')):raise ValueError('请输入非空等时圈GeoJSON')
                    if kind=='tci' and not isinstance(payload,list) and not isinstance(payload.get('records'),list):raise ValueError('请输入导航记录数组或records数组')
                    folder=DATA/'evaluation_inputs'/city_id;folder.mkdir(parents=True,exist_ok=True)
                    file=folder/(kind+'_'+run_id()+('.geojson' if kind=='isochrone' else '.json'))
                    file.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
                    self.respond({'file':file.name,'kind':kind});return
                kind='demand' if action=='demand-runs' else 'workflow'
                from .city_demand import normalize,load_run
                if kind=='demand':payload=normalize(city,payload)
                else:
                    from .city_workflow import normalize as normalize_workflow
                    load_run(DATA,city_id,payload['demand_run_id']);payload['parameters']=normalize_workflow(payload.get('parameters'))
                    from .city_workflow import sources
                    chosen=sources(DATA,city_id,payload['demand_run_id'],payload['parameters']['isochrone']['max_time'],payload.get('input_files'))
                    payload['input_files']={k:v.name if v else None for k,v in chosen.items()}
                registry=ACTIVE_DEMANDS if kind=='demand' else ACTIVE_EVALUATIONS
                active=registry.get(city_id)
                if active:self.respond(JOBS[active],202);return
                identity=uuid4().hex;job={'id':identity,'city_id':city_id,'kind':kind,'status':'queued','phase':'等待计算','options':payload}
                JOBS[identity]=job;registry[city_id]=identity
                POOL.submit(run_shared_job,identity,city,kind,payload);self.respond(job,202)
            except (ValueError,TypeError,KeyError,json.JSONDecodeError) as error:self.respond({'error':str(error)},400)
            return
        if path == '/api/cities/Beijing/opportunity-evaluations':
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 50 * 1024 * 1024:
                self.respond({'error': '采集结果必须为50MB以内的GeoJSON'}, 400)
                return
            try:
                payload = json.loads(self.rfile.read(size))
                collection = payload['collection']
                sigma = float(payload.get('sigma', 15))
                if collection.get('type') != 'FeatureCollection' or not collection.get('features'):
                    raise ValueError('请输入非空GeoJSON FeatureCollection')
                if not 0 < sigma <= 100:
                    raise ValueError('时间衰减参数必须在0到100分钟之间')
            except (KeyError, TypeError, ValueError) as error:
                self.respond({'error': str(error)}, 400)
                return
            active = ACTIVE_EVALUATIONS.get('opportunity:Beijing')
            if active:
                self.respond({'error': '已有机会评价任务运行中，请等待完成'}, 409)
                return
            job_id = uuid4().hex
            folder = DATA / 'arrival_uploads' / job_id
            folder.mkdir(parents=True, exist_ok=False)
            source = folder / 'raw.geojson'
            source.write_text(json.dumps(collection, ensure_ascii=False), encoding='utf-8')
            job = {'id': job_id, 'city_id': 'Beijing', 'kind': 'opportunity', 'status': 'queued', 'phase': '等待处理等时圈'}
            JOBS[job_id] = job
            ACTIVE_EVALUATIONS['opportunity:Beijing'] = job_id
            POOL.submit(run_opportunity_job, job_id, source, sigma)
            self.respond(job, 202)
        elif re.fullmatch(r'/api/cities/[^/]+/navigation/samples', path):
            city_id = path.split('/')[3]
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': '城市不存在'}, 404)
                return
            if not os.environ.get('AMAP_API_KEY'):
                self.respond({'error': '后端未配置 AMAP_API_KEY；未发出请求'}, 409)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 1000:
                self.respond({'error': '请求内容无效'}, 400)
                return
            try:
                count = int(json.loads(self.rfile.read(size))['pairs'])
                preview = sample_grid_pairs(city, DATA / 'processed', count)
                if preview['pair_count'] != count:
                    raise ValueError('符合距离条件的 OD 数量不足')
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                self.respond({'error': str(error)}, 400)
                return
            active = ACTIVE_NAVIGATION.get(city_id)
            if active:
                self.respond(JOBS[active], 202)
                return
            job_id = uuid4().hex
            job = {'id': job_id, 'city_id': city_id, 'kind': 'navigation',
                   'status': 'queued', 'phase': '等待开始', 'options': {'pairs': count}}
            JOBS[job_id] = job
            ACTIVE_NAVIGATION[city_id] = job_id
            POOL.submit(run_navigation_job, job_id, city_id, preview)
            self.respond(job, 202)
            return
        if re.fullmatch(r'/api/cities/[^/]+/evaluations', path):
            city_id = path.split('/')[3]
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': 'City not found'}, 404)
                return
            if not cached_city_network(city, BUS, DATA / 'processed') or not cached_city_poi(city, POI, DATA / 'processed'):
                self.respond({'error': 'Prepare the city network and POI first'}, 409)
                return
            existing = ACTIVE_EVALUATIONS.get(city_id)
            if existing:
                self.respond(JOBS[existing], 202)
                return
            job_id = uuid4().hex
            job = {'id': job_id, 'city_id': city_id, 'kind': 'evaluation', 'status': 'queued', 'phase': 'waiting'}
            JOBS[job_id] = job
            ACTIVE_EVALUATIONS[city_id] = job_id
            POOL.submit(run_evaluation_job, job_id, city_id)
            self.respond(job, 202)
            return
        if path.startswith('/api/cities/') and path.endswith('/assignments'):
            city_id = unquote(path[len('/api/cities/'):-len('/assignments')]).strip('/')
            if city_id not in BY_ID:
                self.respond({'error': '城市不存在'}, 404)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 10_000:
                self.respond({'error': '请求内容无效'}, 400)
                return
            try:
                from .assignment.parameters import normalize_parameters
                options = json.loads(self.rfile.read(size))
                parameters = normalize_parameters(options.get('parameters'))
                options = {
                    'parameters': parameters, 'demand_run_id': options.get('demand_run_id'),
                    'default_headway_min': float(options.get('default_headway_min',15)),
                    'period': options['period'], 'poi_count': int(options['poi_count']),
                    'total_demand': float(options['total_demand']),
                    'assumed_headway_min': (
                        float(options['assumed_headway_min'])
                        if options.get('assumed_headway_min') is not None else None
                    ),
                }
                if options['period'] not in ('am_peak', 'offpeak', 'pm_peak'):
                    raise ValueError('请选择早高峰、平峰或晚高峰')
                if options.get('demand_run_id'):
                    from .city_demand import load_run
                    load_run(DATA,city_id,options['demand_run_id'])
                if options['poi_count'] < 2 or options['total_demand'] <= 0:
                    raise ValueError('POI 数量和小时客流须为正数')
                if not 0 < options['default_headway_min'] < float('inf'):
                    raise ValueError('默认发车间隔须为正数')
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
                self.respond({'error': str(error)}, 400)
                return
            active = ACTIVE_ASSIGNMENTS.get(city_id)
            if active:
                self.respond(JOBS[active], 202)
                return
            job_id = uuid4().hex
            job = {'id': job_id, 'city_id': city_id, 'kind': 'assignment', 'status': 'queued', 'phase': '等待开始','options':options}
            JOBS[job_id] = job
            ACTIVE_ASSIGNMENTS[city_id] = job_id
            POOL.submit(run_assignment_job, job_id, city_id, options)
            self.respond(job, 202)
            return
        if path.startswith('/api/cities/') and path.endswith('/headways'):
            city_id = unquote(path[len('/api/cities/'):-len('/headways')]).strip('/')
            city = BY_ID.get(city_id)
            if city is None:
                self.respond({'error': '城市不存在'}, 404)
                return
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 2_000_000:
                self.respond({'error': 'CSV 为空或超过 2 MB'}, 413)
                return
            try:
                content = self.rfile.read(size).decode('utf-8-sig')
                self.respond(import_city_service(city, BUS, DATA / 'processed', content))
            except (ValueError, UnicodeError) as error:
                self.respond({'error': str(error)}, 400)
            return
        if not path.startswith('/api/cities/') or not path.endswith('/prepare-inputs'):
            self.respond({'error': '接口不存在'}, 404)
            return
        city_id = unquote(path[len('/api/cities/'):-len('/prepare-inputs')]).strip('/')
        city = BY_ID.get(city_id)
        if city is None:
            self.respond({'error': '城市不存在'}, 404)
        elif not city['poi_available'] or not city['network_complete']:
            self.respond({'error': '缺少所需的 POI 或线网文件'}, 409)
        else:
            active = ACTIVE_JOBS.get(city_id)
            if active:
                self.respond(JOBS[active], 202)
                return
            job_id = uuid4().hex
            job = {'id': job_id, 'city_id': city_id, 'status': 'queued',
                   'scanned_rows': 0, 'poi_count': 0}
            JOBS[job_id] = job
            ACTIVE_JOBS[city_id] = job_id
            POOL.submit(run_input_job, job_id, city)
            self.respond(job, 202)

    def respond(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    port = int(os.environ.get('URBAN_API_PORT', '8765'))
    print(f'City API: http://127.0.0.1:{port} ({len(CITIES)} cities)', flush=True)
    # City switches and React development effects can open more than five simultaneous connections.
    ThreadingHTTPServer.request_queue_size = 64
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
