"""User-registered city metadata, independent of the national dataset catalog."""
import json
import math
from pathlib import Path
import re


def registered(data):
    return [json.loads(path.read_text(encoding='utf-8')) for path in sorted((Path(data)/'cities').glob('*.json'))]


def register(data, values, existing):
    if not isinstance(values,dict):raise ValueError('城市信息必须为JSON对象')
    identity=str(values.get('id','')).strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{1,63}',identity):raise ValueError('城市ID用英文字母开头，2–64位字母、数字、下划线或短横线')
    if identity in existing:raise ValueError('城市ID已存在，请直接选择该城市')
    name=str(values.get('name','')).strip()
    if not name:raise ValueError('请填写城市名称')
    center=[float(values.get('longitude')),float(values.get('latitude'))]
    if not all(math.isfinite(v) for v in center) or not -180<=center[0]<=180 or not -85<=center[1]<=85:raise ValueError('请填写有效的WGS84城市中心经纬度')
    city={'id':identity,'name':name,'province':str(values.get('province','')).strip(),'city_code':str(values.get('city_code') or identity),
          'center':center,'route_count':0,'network_complete':False,'poi_available':False,'poi_file':None,
          'has_evaluation':False,'poi_prepared':False,'network_prepared':False,'user_registered':True}
    folder=Path(data)/'cities';folder.mkdir(parents=True,exist_ok=True)
    with (folder/(identity+'.json')).open('x',encoding='utf-8') as stream:json.dump(city,stream,ensure_ascii=False,indent=2)
    return city
