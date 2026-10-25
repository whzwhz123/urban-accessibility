import TaskControls from './TaskControls';
import React, { useEffect, useState, useRef } from 'react';
import { Alert, Button, Collapse, InputNumber, Select } from 'antd';

const PERIODS = { am_peak: '早高峰', offpeak: '平峰', pm_peak: '晚高峰' };
const label = { fontSize: 12, margin: '12px 0 5px', color: '#c8d7e4' };
import { readJson as read } from '../../utils/localApi';

export default function DemandPanel({ city, demandRun, onDemandChange, onToggleVis, active, onDirtyChange, restoredConfig, onConfigChange, restoreKey }) {
  const [settings, setSettings] = useState(null);
  const [runs, setRuns] = useState([]);
  const [aliases, setAliases] = useState({});
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [population, setPopulation] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [period, setPeriod] = useState('pm_peak');
  const [ready, setReady] = useState(false);
  const [taskHistory, setTaskHistory] = useState([]);
  const current = useRef(null);
  const contextKey = JSON.stringify([city.id, city.custom_input_version, restoreKey]);
  if (current.current?.key !== contextKey || current.current?.restore !== restoredConfig) current.current = { key: contextKey, restore: restoredConfig };
  const context = current.current;
  const matches = own => own === current.current;
  const latestDemand = useRef(demandRun); latestDemand.current = demandRun;
  const [configOwner, setConfigOwner] = useState(null);
  const automaticSelection = useRef(null);
  useEffect(() => {
    const controller = new AbortController();
    const own = context;
    setSettings(null); setRuns([]); setJob(null); setReady(false); setError(''); setUploading(false);
    Promise.all([
      fetch(`/api/cities/${city.id}/demand-runs`, { signal: controller.signal }).then(read),
      fetch(`/api/cities/${city.id}/inputs`, { signal: controller.signal }).then(read)
    ]).then(([result, inputs]) => {
      if (controller.signal.aborted || !matches(own)) return;
      setRuns(result.runs); setAliases(result.category_aliases || {}); setPopulation(result.population_available); setReady(!!inputs.poi);
      setJob(result.job); setError('');
      const selected = result.runs.find(run => run.run_id === latestDemand.current?.run_id && run.current_inputs !== false) || result.runs.find(run => run.current_inputs !== false);
      setSettings(previous => previous ?? (restoredConfig?.city_id === city.id && restoredConfig.demand_config ? restoredConfig.demand_config : selected?.parameters || result.defaults));
      setConfigOwner(own);
      if (selected && latestDemand.current?.run_id !== selected.run_id) { automaticSelection.current = { context: own, id: selected.run_id }; onDemandChange(selected); }
    }).catch(e => { if (!controller.signal.aborted && matches(own) && e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [context]);
  useEffect(() => {
    const automatic = automaticSelection.current;
    if (automatic?.context === context && automatic.id === demandRun?.run_id) {
      automaticSelection.current = null; return;
    }
    if (demandRun?.parameters && demandRun.city_id === city.id) { setSettings(demandRun.parameters); setConfigOwner(context); }
  }, [context, demandRun]);
  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    const own = context;
    fetch(`/api/cities/${city.id}/inputs`, { signal: controller.signal }).then(read).then(value => { if (!controller.signal.aborted && matches(own)) setReady(!!value.poi); }).catch(e => { if (!controller.signal.aborted && matches(own) && e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [active, city.id, city.custom_input_version]);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const own = context;
    let disposed = false;
    const timer = setInterval(async () => {
      try {
        const next = await fetch(`/api/jobs/${job.id}`).then(read); if (disposed || !matches(own)) return; setJob(next);
        if (next.status === 'completed') {
          onDemandChange(next.result); setSettings(next.result.parameters);
          setRuns(previous => [next.result, ...previous.filter(r => r.run_id !== next.result.run_id)]);
        } else if (next.status === 'failed') setError(next.error);
      } catch (e) { if (matches(own)) setError(e.message); }
    }, 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, [job?.id, job?.status, context]);
  useEffect(() => {
    const controller = new AbortController();
    const own = context; setTaskHistory([]);
    fetch(`/api/jobs?city_id=${city.id}`, { signal: controller.signal }).then(read).then(value => { if (!controller.signal.aborted && matches(own)) setTaskHistory(value.jobs.filter(row => row.kind === 'demand')); }).catch(error => { if (!controller.signal.aborted && matches(own) && error.name !== 'AbortError') setError(error.message); });
    return () => controller.abort();
  }, [context, job?.status]);
  const retry = async row => {
    const own = context; setError('');
    try {
      const next = await fetch(`/api/cities/${city.id}/demand-runs`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(row.options) }).then(read);
      if (matches(own)) setJob(next);
    } catch (error) { if (matches(own)) setError(error.message); }
  };
  const busy = uploading || ['queued', 'running'].includes(job?.status);
  const dirty = !!settings && (!demandRun || JSON.stringify(settings) !== JSON.stringify(demandRun.parameters));
  const numeric = value => value != null && Number.isFinite(Number(value));
  const weights = settings ? [settings.poi_weights, ...Object.values(settings.od_period_weights || {}).flatMap(value => [value.production, value.attraction])] : [];
  const validSettings = settings && settings.poi_weights && settings.od_period_weights && settings.hourly_demands && Object.keys(PERIODS).every(key => settings.od_period_weights[key]?.production && settings.od_period_weights[key]?.attraction) && numeric(settings.grid_m) && settings.grid_m >= 100 && settings.grid_m <= 2000 && numeric(settings.smooth_r) && settings.smooth_r >= 0 && numeric(settings.smooth_sigma) && settings.smooth_sigma > 0 && weights.every(values => Object.values(values || {}).every(value => numeric(value) && value >= 0)) && Object.values(settings.hourly_demands || {}).every(value => value == null || numeric(value) && value > 0);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    if (restoredConfig?.city_id === city.id && restoredConfig.demand_config) {
      setSettings(restoredConfig.demand_config); setConfigOwner(context);
    }
  }, [context]);
  useEffect(() => { if (settings && configOwner === context) onConfigChange?.(settings); }, [settings, configOwner, context, onConfigChange]);
  const change = (key, value) => setSettings(old => ({ ...old, [key]: value }));
  const start = async () => {
    setError('');
    const own = context;
    try { const next = await fetch(`/api/cities/${city.id}/demand-runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings) }).then(read); if (matches(own)) setJob(next); }
    catch (e) { if (matches(own)) setError(e.message); }
  };
  const upload = async event => {
    const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
    const own = context;
    setUploading(true); setError('');
    try { await fetch(`/api/cities/${city.id}/population`, { method: 'POST', body: file }).then(read); if (matches(own)) setPopulation(true); }
    catch (e) { if (matches(own)) setError(e.message); } finally { if (matches(own)) setUploading(false); }
  };
  if (!settings) return <div style={{ padding: 14, color: '#c8d7e4' }}>{error || '正在读取需求配置…'}</div>;
  const categories = Object.keys(settings.od_period_weights?.[period]?.production || {});
  return <div style={{ padding: 14, color: '#c8d7e4', fontSize: 12 }}>
    <div style={{ fontSize: 15, fontWeight: 700 }}>共享需求配置</div>
    <p>先保存需求版本，再用于客流分配、指标计算和综合评价。需求密度是相对指数，不是观测客流。</p>
    {!ready && <Alert type="warning" message="请先在数据管理中准备本市POI。" />}
    {error && <Alert type="error" message={error} style={{ marginBottom: 8 }} />}
    <div style={label}>已保存的需求版本</div>
    <Select aria-label="需求版本" style={{ width: '100%' }} value={demandRun?.run_id} placeholder="尚未生成" onChange={id => { const run = runs.find(r => r.run_id === id); setSettings(run.parameters); onDemandChange(run); }}
      options={runs.map(r => ({ value: r.run_id, disabled: r.current_inputs === false, label: `${r.run_id.slice(0, 15)} · ${r.grid_count}格网${r.current_inputs === false ? ' · 旧输入（需重建）' : ''}` }))} />
    {dirty && demandRun && <Button size="small" onClick={() => setSettings(demandRun.parameters)} disabled={busy}>放弃修改，恢复已保存配置</Button>}
    <div style={{ color: dirty ? '#ffca83' : '#80d7a5', marginTop: 8 }}>{dirty ? '参数有未保存修改；后续模块仍使用已保存版本。' : '当前参数与所选需求版本一致。'}</div>
    {[['grid_m', '格网尺寸（米）', 100, 2000], ['smooth_r', '平滑半径（米）', 0, 5000], ['smooth_sigma', '高斯带宽（米）', 1, 5000]].map(([key, text, min, max]) => <div key={key}>
      <div style={label}>{text}</div><InputNumber aria-label={text} style={{ width: '100%' }} min={min} max={max} value={settings[key]} disabled={busy} onChange={v => change(key, v)} />
    </div>)}
    <Collapse style={{ marginTop: 12 }} items={[
      { key: 'importance', label: 'POI重要性权重（共享）', children: <><p>该系数用于密度与资源机会评分；在OD中乘以分时发生/吸引权重。不直接当作人次。平滑和人口校准作用于评价格网，不直接改写OD发生吸引强度。</p>{[...new Set([...Object.keys(settings.poi_weights || {}), ...Object.keys(demandRun?.category_counts || {}).map(c => aliases[c] || c)])].map(category => { const value = settings.poi_weights?.[category] ?? (restoredConfig?.city_id === city.id && restoredConfig.demand_config ? null : 1); return <div key={category} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
        <label>{category}</label><InputNumber aria-label={`${category}重要性`} min={0} max={100} step={0.1} value={value} disabled={busy} onChange={v => change('poi_weights', { ...settings.poi_weights, [category]: v })} />
      </div>; })}</> },
      { key: 'od', label: '分时OD发生与吸引权重', children: <><p>发生和吸引可以不同。默认值为研究情景假设，未用实测出行调查标定；早高峰默认反转晚高峰方向。</p>
        <Select style={{ width: '100%', marginBottom: 10 }} value={period} onChange={setPeriod} options={Object.entries(PERIODS).map(([value, text]) => ({ value, label: text }))} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 62px 62px', gap: 5 }}><span>类型</span><span>发生</span><span>吸引</span>
          {categories.map(category => <React.Fragment key={category}><span>{category}</span>{['production', 'attraction'].map(role => <InputNumber key={role} aria-label={`${PERIODS[period]}${category}${role === 'production' ? '发生' : '吸引'}`} min={0} step={0.5} style={{ width: 62 }} value={settings.od_period_weights?.[period]?.[role]?.[category]} disabled={busy}
            onChange={v => change('od_period_weights', { ...settings.od_period_weights, [period]: { ...settings.od_period_weights?.[period], [role]: { ...settings.od_period_weights?.[period]?.[role], [category]: v } } })} />)}</React.Fragment>)}
        </div></> },
      { key: 'total', label: '分时城市公交总需求', children: <><p>单位：人次/小时。北京默认沿用研究给定值；其他城市需用户填写，不能从POI数量推断总人次。</p>{Object.entries(PERIODS).map(([p, text]) => <div key={p}><div style={label}>{text}</div><InputNumber min={1} style={{ width: '100%' }} value={settings.hourly_demands?.[p]} onChange={v => change('hourly_demands', { ...settings.hourly_demands, [p]: v })} /></div>)}</> },
      { key: 'population', label: '可选人口校准', children: <><label>导入有坐标系的人口GeoTIFF<input aria-label="人口栅格" type="file" accept=".tif,.tiff" onChange={upload} disabled={busy} /></label>
        <div style={label}>POI占比（其余为人口）</div><InputNumber min={0} max={1} step={0.05} disabled={!population || busy} value={settings.kde_weight} onChange={v => change('kde_weight', v)} />
        <p>{population ? '已提供人口栅格。栅格缺失的格网退回POI指标，结果中记录范围。' : '未提供人口栅格，仅用POI，不把缺失人口当零。'}</p></> }
    ]} />
    {!validSettings && <Alert type="warning" message="请填写有效数值：权重非负，小时需求为正数或留空，格网100–2000米。" />}
    <Button type="primary" block style={{ marginTop: 14 }} disabled={!ready || busy || !validSettings} loading={busy} onClick={start}>保存配置并生成需求</Button>
    {job && <div style={{ marginTop: 8 }}>{job.phase}{job.status === 'completed' && (job.result.cache_hit ? '（复用相同条件缓存）' : '（已生成新版本）')}</div>}
    <TaskControls key={contextKey} job={job} onChange={value => { if (matches(context)) setJob(value); }} />
    {!!taskHistory.length && <details style={{ marginTop:12 }}><summary>本市需求生成任务记录（{taskHistory.length}）</summary>
      {taskHistory.slice(-8).reverse().map(row => {
        const saved = runs.find(value => value.run_id === row.result?.run_id && value.current_inputs !== false);
        return <div key={row.id} style={{ marginTop:8, borderTop:'1px solid #ffffff18', paddingTop:6 }}>
          {row.id.slice(0,8)} · {({ completed:'已完成', failed:'失败', interrupted:'已中断', queued:'排队中', running:'运行中' })[row.status] || row.status}
          {row.error && <div style={{ color:'#ffca83' }}>{row.error}</div>}
          {saved && <button onClick={() => { setSettings(saved.parameters); onDemandChange(saved); }}>使用此需求版本</button>}
          {row.result && !saved && <div style={{ color:'#ffca83' }}>此任务使用的输入已过期，请重新生成。</div>}
          {['failed','interrupted'].includes(row.status) && row.options && <button disabled={busy || !ready} onClick={() => retry(row)}>按原配置重试（使用当前POI）</button>}
        </div>;
      })}
    </details>}
    {demandRun && <><p>{demandRun.poi_count.toLocaleString()}个POI · {demandRun.grid_count.toLocaleString()}个格网。范围为POI格网及平滑缓冲，不等同于行政区全境。</p>
      <Button block onClick={() => onToggleVis('demand', true)}>查看需求密度</Button>
      <a href={`/api/cities/${city.id}/demand-runs/${demandRun.run_id}/grid`} target="_blank" rel="noreferrer" style={{ color: '#80d7a5', display: 'block', marginTop: 8 }}>查看需求GeoJSON</a>
    </>}
  </div>;
}
