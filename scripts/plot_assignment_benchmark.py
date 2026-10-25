"""Plot recorded model scenarios; no external basemap or routing requests."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.cm import ScalarMappable
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import wkt
from shapely.ops import transform

LABELS = {'am_peak':'早高峰','offpeak':'平峰','pm_peak':'晚高峰'}
COLORS = {'am_peak':'#0072B2','offpeak':'#009E73','pm_peak':'#E69F00'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    parser.add_argument('--center',nargs=2,type=float,default=[118.35,35.1]);args=parser.parse_args()
    folder=args.folder;root=Path(__file__).resolve().parents[1]/'data'
    config=json.loads((folder/'config.json').read_text(encoding='utf-8'))
    rows=pd.read_csv(folder/'comparison.csv');out=folder/'figures';out.mkdir(exist_ok=True)
    plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'], 'axes.unicode_minus':False,
        'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':170})
    fig,axes=plt.subplots(2,3,figsize=(15,8.2),layout='constrained')
    panels=[('total_seconds','模型运行时间（秒）',1),('peak_api_rss_mib','API进程峰值RSS（MiB）',1),
        ('sampled_nonzero_od_pairs','抽样后非零OD对数',1),('candidate_paths','候选路径数',1),
        ('served_demand_share','容量可服务需求比例（%）',100),('relative_gap','最终相对间隙',1)]
    for ax,(field,title,multiplier) in zip(axes.flat,panels):
        for period in config['periods']:
            data=rows[rows.period==period].sort_values('poi_count')
            ax.plot(data.poi_count,data[field]*multiplier,'o-',label=LABELS[period],color=COLORS[period],lw=1.7)
        ax.set_xscale('log');ax.set_xticks(config['counts'],[f'{v:,}' for v in config['counts']]);ax.set_xlabel('代表POI数量');ax.set_title(title);ax.grid(alpha=.2)
        if field=='relative_gap':ax.axhline(config['parameters']['relative_gap_tol'],c='#777',ls='--',lw=1)
    axes[0,0].legend();fig.suptitle(f"{config['city']}：同一代码版本、同一总需求 {config['total_demand']:,.0f} 人次/小时\n单次运行；RSS含服务缓存，不是求解器独占内存")
    fig.savefig(out/'scale_comparison.png');plt.close(fig)
    records=[];frames={};geometry=None;reference=None
    poi_metadata=json.loads((root/'processed'/config['city']/'poi_manifest.json').read_text(encoding='utf-8'))
    metric_crs=poi_metadata['metric_crs']
    project=Transformer.from_crs(4326,metric_crs,always_xy=True).transform
    for row in rows.itertuples():
        run=root/'runs'/config['city']/row.run_id
        frame=pd.read_csv(run/'physical_segment_flows.csv').sort_values('edge_idx').reset_index(drop=True)
        if geometry is None:
            reference=frame.edge_idx.to_numpy();geometry=[]
            for value in frame.geometry_wkt:
                shape=transform(project,wkt.loads(value));parts=[shape] if shape.geom_type=='LineString' else list(shape.geoms)
                geometry.append([np.asarray(line.coords)[:,:2]/1000 for line in parts])
        else:assert np.array_equal(reference,frame.edge_idx.to_numpy())
        frames[(row.poi_count,row.period)]=frame
        log=pd.read_csv(run/'assignment_iterations.csv');rounds=[]
        for number,group in log.groupby('column_round'):
            rounds.append({'column_round':int(number),'iterations':len(group),'last_gap':float(group.relative_gap.iloc[-1]),
                'hit_iteration_limit':bool(len(group)>=config['parameters']['max_iterations'] and group.relative_gap.iloc[-1]>config['parameters']['relative_gap_tol'])})
        records.append({'run_id':row.run_id,'rounds':rounds,'note':'max_iterations limits each column-generation solve; summary iterations is the sum across rounds. Final gap applies to desired assignment before capacity reallocation.'})
    (folder/'convergence_details.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    comparisons=[]
    reference_count=max(config['counts'])
    for period in config['periods']:
        reference_frame=frames[(reference_count,period)]
        for count in config['counts']:
            sample_frame=frames[(count,period)]
            for metric in ['desired_flow_pax_per_hour','served_flow_pax_per_hour']:
                a=sample_frame[metric].to_numpy();b=reference_frame[metric].to_numpy()
                comparisons.append({'period':period,'poi_count':count,'reference_poi_count':reference_count,
                    'metric':metric,'pearson':float(np.corrcoef(a,b)[0,1]),
                    'relative_l1_difference':float(np.abs(a-b).sum()/np.abs(b).sum()),
                    'interpretation':'scenario sensitivity to representative endpoint count; larger sample is not observed ground truth'})
    pd.DataFrame(comparisons).to_csv(folder/'flow_sensitivity.csv',index=False,encoding='utf-8-sig')

    segments=[line for parts in geometry for line in parts];coordinates=np.vstack(segments)
    center=np.asarray(project(*args.center))/1000
    full=(coordinates[:,0].min(),coordinates[:,0].max(),coordinates[:,1].min(),coordinates[:,1].max())
    cmap=LinearSegmentedColormap.from_list('green_red',['#1eb26a','#f6d02c','#da3433'])
    for field,unit in [('served_flow_pax_per_hour','容量可行客流（人次/小时）'),('served_vc','容量可行V/C'),
                       ('desired_flow_pax_per_hour','期望客流（人次/小时）'),('desired_vc','期望V/C')]:
        upper=max(float(frame[field].max()) for frame in frames.values())
        if field=='served_vc':upper=1.
        elif field=='desired_vc':upper=max(upper,1.)
        scale=Normalize(0,max(upper,1e-12))
        for scope in ['city','center20km']:
            fig,axes=plt.subplots(len(config['counts']),len(config['periods']),figsize=(15,4.6*len(config['counts'])),squeeze=False,layout='constrained')
            for i,count in enumerate(config['counts']):
                for j,period in enumerate(config['periods']):
                    ax=axes[i,j];frame=frames[(count,period)];values=frame[field].to_numpy();order=np.argsort(values,kind='stable')
                    lines=[line for k in order for line in geometry[k]];v=np.array([values[k] for k in order for line in geometry[k]])
                    colors=cmap(scale(v));colors[v<=0]=[.72,.74,.75,.38]
                    ax.add_collection(LineCollection(lines,colors=colors,linewidths=np.where(v>0,.85,.35)))
                    bounds=full if scope=='city' else (center[0]-20,center[0]+20,center[1]-20,center[1]+20)
                    ax.set_xlim(bounds[:2]);ax.set_ylim(bounds[2:]);ax.set_aspect('equal');ax.set_title(f'{count:,} POI · {LABELS[period]}');ax.set_xticks([]);ax.set_yticks([])
            fig.colorbar(ScalarMappable(norm=scale,cmap=cmap),ax=axes.ravel().tolist(),shrink=.65,label=unit)
            scope_label='全线网' if scope=='city' else f'以({args.center[0]}, {args.center[1]})为中心的40×40公里窗口'
            fig.suptitle(f'{unit} · {scope_label}\n所有面板统一色标；灰色为零客流；相同几何重叠时高值优先显示')
            fig.savefig(out/(field+'_'+scope+'.png'));plt.close(fig)
    (folder/'visualization_metadata.json').write_text(json.dumps({'city':config['city'],'center_wgs84':args.center,
        'projected_crs':metric_crs,'focus_window_km':40,'maps':'desired and capacity-feasible flows shown separately; neither is observed congestion',
        'same_geometry_overlay':'ascending flow, largest drawn last','figures':[p.name for p in out.glob('*.png')]},ensure_ascii=False,indent=2),encoding='utf-8')
    print(out)


if __name__=='__main__':main()
