import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import DemandPanel from './DemandPanel';
jest.mock('antd',()=>({
 Alert:({message})=><div>{message}</div>,Button:({children,onClick,disabled})=><button onClick={onClick} disabled={disabled}>{children}</button>,
 Collapse:({items})=><div>{items.map(i=><div key={i.key}>{i.children}</div>)}</div>,
 InputNumber:({value,onChange,'aria-label':label})=><input aria-label={label} value={value??''} onChange={e=>onChange(Number(e.target.value))}/>,
 Select:({options,value,onChange})=><select value={value??''} onChange={e=>onChange(e.target.value)}>{options.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}</select>
}));
const settings={grid_m:500,smooth_r:800,smooth_sigma:400,kde_weight:1,poi_weights:{其他:1},
 od_period_weights:Object.fromEntries(['am_peak','offpeak','pm_peak'].map(p=>[p,{production:{其他:1},attraction:{其他:1}}])),
 hourly_demands:{am_peak:null,offpeak:null,pm_peak:null}};
const run={city_id:'Linyi',run_id:'d1',parameters:settings,poi_count:2,grid_count:2};
const props={city:{id:'Linyi'},demandRun:run,onDemandChange:jest.fn(),onToggleVis:jest.fn(),active:true};
beforeEach(()=>{global.fetch=jest.fn(async url=>({ok:true,json:async()=>url.includes('/demand-runs')?{runs:[run],defaults:settings}:url.includes('/jobs')?{jobs:[]}:{poi:{}}}));});
test('未保存设置可以恢复，恢复后清除草稿提示',async()=>{
 const dirty=jest.fn();render(<DemandPanel {...props} onDirtyChange={dirty}/>);
 await screen.findByLabelText('格网尺寸（米）');fireEvent.change(screen.getByLabelText('格网尺寸（米）'),{target:{value:'600'}});
 await waitFor(()=>expect(dirty).toHaveBeenLastCalledWith(true));
 fireEvent.click(screen.getByText('放弃修改，恢复已保存配置'));
 await waitFor(()=>expect(dirty).toHaveBeenLastCalledWith(false));
});
test('外部切换历史需求版本时回显对应设置，不保留另一版本参数',async()=>{
 const view=render(<DemandPanel {...props}/>);await screen.findByLabelText('格网尺寸（米）');
 view.rerender(<DemandPanel {...props} demandRun={{...run,run_id:'d2',parameters:{...settings,grid_m:700}}}/>);
 await waitFor(()=>expect(screen.getByLabelText('格网尺寸（米）').value).toBe('700'));
});
