"""Reproducible result packages, excluding executable model checkpoints and keys."""
import json
import re
import zipfile
from pathlib import Path

def package(data,city,identity,kind='assignment'):
    if not re.fullmatch(r'[0-9]{8}_[0-9]{6}_[0-9a-f]{8}',identity):raise ValueError('实验版本无效')
    root=Path(data).resolve();folder=root/('runs' if kind=='assignment' else 'evaluation_runs')/city/identity
    summary_file=folder/'summary.json'
    if not summary_file.is_file():raise ValueError('实验结果不存在')
    summary=json.loads(summary_file.read_text(encoding='utf-8'));output=root/'exports'/city/(identity+'_'+kind+'.zip');output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists():return output
    files=[(file,'results/'+file.name) for file in folder.iterdir() if file.suffix in ('.csv','.json','.geojson','.png','.svg')]
    for label,value in summary.get('source_files',{}).items():
        if value:
            source=(root/value).resolve();source.relative_to(root)
            if source.is_file():files.append((source,'inputs/'+source.name))
    demand_id=summary.get('demand_run_id')
    if demand_id:
        for name in ['summary.json','parameters.json','demand_grid.geojson']:
            file=root/'demand_runs'/city/demand_id/name
            if file.is_file():files.append((file,'shared_demand/'+name))
    manifest={'city_id':city,'run_id':identity,'kind':kind,'demand_run_id':demand_id,'contents':[name for _,name in files],
              'limitations':summary.get('warnings',[]),'original_city_datasets':'not duplicated; retain source datasets listed in parameters/summary','checkpoints':'excluded; local-only trusted model state'}
    temporary=output.with_suffix('.zip.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
        for file,name in files:archive.write(file,name)
        archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    temporary.replace(output);return output
