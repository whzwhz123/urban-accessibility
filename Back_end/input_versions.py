"""Activate retained input versions without deleting datasets or pointer history."""
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import uuid4


def list_versions(data, city_id):
    folder = Path(data)/'custom_inputs'/city_id
    rows = []
    for file in sorted(folder.glob('*/manifest.json'), reverse=True):
        record = json.loads(file.read_text(encoding='utf-8'))
        rows.append({key:record.get(key) for key in ['version','status','filename','counts','error']})
    return rows


def activate(data, original_city, version):
    root = Path(data).resolve();city_id = original_city['id'];folder = root/'custom_inputs'/city_id
    folder.mkdir(parents=True, exist_ok=True)
    pointer = folder/'active.json'
    if version is None:
        city = dict(original_city)
    else:
        if not re.fullmatch(r'[0-9]{8}T[0-9]{12}Z_[0-9a-f]{8}',str(version)):
            raise ValueError('输入版本编号无效')
        manifest = folder/version/'manifest.json'
        if not manifest.is_file():raise ValueError('输入版本不存在')
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if saved.get('status')!='completed':raise ValueError('只能启用完整导入的数据版本')
        # Standardized locations are derived from the retained version, not old absolute paths.
        network = folder/version/'standardized/network_root'
        poi = folder/version/'standardized/poi'
        if not (poi/'poi_wgs84.csv').is_file() or not list((network/city_id).glob('*_bus_stops.shp')):
            raise ValueError('该版本标准化文件不完整，请重新导入')
        city = {**original_city,**saved['city'], 'has_evaluation':False,
            'custom_input_manifest':manifest.relative_to(root).as_posix(),
            'source_directory_override':{'bus_dir':network.relative_to(root).as_posix(),'poi_dir':poi.relative_to(root).as_posix()}}
        # Readiness must be checked against this version by the API, not trusted from a prior UI state.
        city.update(network_prepared=False,poi_prepared=False)
    if pointer.is_file():
        history = folder/'activation_history';history.mkdir(exist_ok=True)
        destination = history/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]+'.json')
        pointer.resolve().relative_to(root);destination.resolve().relative_to(root)
        pointer.replace(destination)
    if version is not None:
        temporary = folder/('active_'+uuid4().hex+'.tmp')
        temporary.write_text(json.dumps({'city':city},ensure_ascii=False),encoding='utf-8')
        temporary.replace(pointer)
    return city
