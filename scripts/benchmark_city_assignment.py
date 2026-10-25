"""Versioned local API benchmark; never collects external routing data."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import time
from uuid import uuid4
import urllib.request

import psutil


def request(base, path, value=None):
    payload = None if value is None else json.dumps(value).encode()
    req = urllib.request.Request(base+path, payload, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.load(response)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--city', required=True)
    parser.add_argument('--demand-run', required=True)
    parser.add_argument('--total-demand', type=float, required=True)
    parser.add_argument('--counts', nargs='+', type=int, default=[500,1000,10000])
    parser.add_argument('--periods', nargs='+', default=['am_peak','offpeak','pm_peak'])
    parser.add_argument('--base', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    from sys import path as python_path
    python_path.insert(0,str(Path(__file__).resolve().parents[1]))
    from Back_end.assignment.parameters import DEFAULT_PARAMETERS
    folder = Path(__file__).resolve().parents[1]/'data'/'workflow_validation'/('scale_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    folder.mkdir(parents=True)
    process = next((psutil.Process(c.pid) for c in psutil.net_connections(kind='tcp') if c.status=='LISTEN' and c.laddr.port==8765),None)
    save(folder/'config.json', {**vars(args),'parameters':DEFAULT_PARAMETERS,'default_headway_min':15,
        'memory_measurement':'API process RSS sampled every second; includes retained caches, not isolated incremental solver memory',
        'timing':'API wall time includes hashing and polling; model total_seconds is reported separately'})
    rows=[];records=[]
    for count in args.counts:
        for period in args.periods:
            options={'poi_count':count,'period':period,'total_demand':args.total_demand,'demand_run_id':args.demand_run,
                     'parameters':DEFAULT_PARAMETERS,'default_headway_min':15}
            preflight=request(args.base,f'/api/cities/{args.city}/assignment-preflight',options)
            start=time.perf_counter();peak=0;phase=None;last_write=0
            job=request(args.base,f'/api/cities/{args.city}/assignments',options)
            while job['status'] in ('running','queued'):
                elapsed=time.perf_counter()-start
                if process:
                    peak=max(peak,process.memory_info().rss)
                state={'poi_count':count,'period':period,'job_id':job['id'],'run_id':job.get('run_id'),
                       'elapsed_seconds':elapsed,'peak_api_rss_bytes':peak,'status':job['status'],'phase':job.get('phase')}
                if phase!=job.get('phase') or elapsed-last_write>=30:
                    print(json.dumps(state,ensure_ascii=False),flush=True);save(folder/'progress.json',state)
                    phase=job.get('phase');last_write=elapsed
                # Protect the workstation, without changing any mathematical stopping threshold.
                if psutil.virtual_memory().available < 1024**3:
                    request(args.base,f"/api/jobs/{job['id']}/cancel",{})
                    raise RuntimeError('Less than 1 GiB available RAM; safe cancellation requested')
                time.sleep(1)
                job=request(args.base,'/api/jobs/'+job['id'])
            if job['status']!='completed':
                save(folder/'failed_job.json',job)
                raise RuntimeError(str(job.get('error',job['status'])))
            result=job['result'];record={'request':options,'job_id':job['id'],'preflight':preflight,'summary':result}
            row={key:result.get(key) for key in ['run_id','period','poi_count','hourly_demand_pax','candidate_od_pairs',
                'sampled_nonzero_od_pairs','candidate_paths','converged','relative_gap','assignment_iterations',
                'total_seconds','preparation_seconds','assignment_seconds','reallocation_seconds',
                'served_demand_share','capacity_violation','cache_hit']}
            row.update(api_wall_seconds=time.perf_counter()-start,peak_api_rss_mib=peak/1024**2)
            records.append(record);rows.append(row)
            save(folder/'results.json',records)
            with (folder/'comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(row));writer.writeheader();writer.writerows(rows)
            print('COMPLETED '+json.dumps(row,ensure_ascii=False),flush=True)
    completed={'status':'completed','experiments':len(rows),'folder':str(folder)}
    save(folder/'completion.json',completed)
    save(folder/'progress.json',completed)
    print('OUTPUT '+str(folder),flush=True)


if __name__=='__main__':main()
