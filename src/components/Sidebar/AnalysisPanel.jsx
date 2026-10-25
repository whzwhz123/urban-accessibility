import React, { useState, useRef, useEffect } from 'react';
import AmapCollectionPanel from './AmapCollectionPanel';

// ── 通用组件 ──────────────────────────────────

function SectionLabel({ children }) {
  return (
    <div style={{
      fontSize: 10, fontWeight: 600,
      color: 'rgba(255,255,255,0.22)',
      letterSpacing: '0.1em', textTransform: 'uppercase',
      marginBottom: 8, marginTop: 14,
    }}>{children}</div>
  );
}

// 问号 tooltip
function HintIcon({ text }) {
  const [show, setShow] = useState(false);
  const ref = useRef(null);
  return (
    <div style={{ position: 'relative', display: 'inline-flex' }} ref={ref}>
      <div
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        style={{
          width: 14, height: 14, borderRadius: '50%', flexShrink: 0,
          border: '1px solid rgba(255,255,255,0.2)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          cursor: 'help', color: 'rgba(255,255,255,0.3)', fontSize: 9, fontWeight: 700,
          userSelect: 'none',
        }}
      >?</div>
      {show && (
        <div style={{
          position: 'fixed',
          marginLeft: 20,
          background: 'rgba(15,20,35,0.98)',
          border: '1px solid rgba(255,255,255,0.1)',
          borderRadius: 7, padding: '7px 10px',
          fontSize: 11, color: 'rgba(200,215,235,0.8)',
          lineHeight: 1.65, whiteSpace: 'pre-line',
          zIndex: 9999, width: 180,
          boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
          pointerEvents: 'none',
        }}>{text}</div>
      )}
    </div>
  );
}

function SliderRow({ label, value, min, max, step = 1, unit = '', onChange, hint, disabled }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)' }}>{label}</span>
          {hint && <HintIcon text={hint} />}
        </div>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,0.7)', fontVariantNumeric: 'tabular-nums' }}>
          {value}{unit}
        </span>
      </div>
      <input type="range" disabled={disabled} min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ width: '100%', height: 3, cursor: 'pointer', accentColor: 'rgba(255,255,255,0.5)' }}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
        <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.18)' }}>{min}{unit}</span>
        <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.18)' }}>{max}{unit}</span>
      </div>
    </div>
  );
}

function DualSlider({ labelA, labelB, valueA, onChange, hint, disabled }) {
  const pct = Math.round(valueA * 100);
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)' }}>{labelA}</span>
          {hint && <HintIcon text={hint} />}
        </div>
        <span style={{ fontSize: 11, fontWeight: 600, color: 'rgba(255,255,255,0.55)', fontVariantNumeric: 'tabular-nums' }}>
          {pct}% · {100 - pct}%
        </span>
        <span style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)' }}>{labelB}</span>
      </div>
      <div style={{ height: 3, borderRadius: 2, overflow: 'hidden', background: 'rgba(255,255,255,0.08)', display: 'flex', marginBottom: 4 }}>
        <div style={{ width: `${pct}%`, background: 'rgba(255,255,255,0.4)', transition: 'width 0.1s' }} />
        <div style={{ flex: 1, background: 'rgba(255,255,255,0.12)' }} />
      </div>
      <input type="range" min={0} max={100} step={5} value={pct}
        onChange={e => onChange(Number(e.target.value) / 100)}
        style={{ width: '100%', height: 3, cursor: 'pointer', accentColor: 'rgba(255,255,255,0.5)' }}
      />
    </div>
  );
}

function Toggle({ active, onClick }) {
  return (
    <div onClick={e => { e.stopPropagation(); onClick(); }} style={{
      width: 28, height: 15, borderRadius: 8, flexShrink: 0, cursor: 'pointer',
      background: active ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.1)',
      position: 'relative', transition: 'background 0.2s',
    }}>
      <div style={{
        position: 'absolute', top: 2, left: active ? 15 : 2,
        width: 11, height: 11, borderRadius: '50%',
        background: active ? '#111' : 'rgba(255,255,255,0.5)',
        transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
      }} />
    </div>
  );
}

const CONTROL = { background: '#111a28', color: '#dbe6ef', border: '1px solid #ffffff33', borderRadius: 4, padding: '5px 6px', fontSize: 12 };
const INITIAL = { coverage: { walk_full: 300, walk_zero: 800, walk_weight: 0.6, diversity_r: 500 }, isochrone: { sigma: 15, max_time: 45, method: 'cumulative' }, tci: { alpha: 8, bus_max: 70, car_max: 30 } };
const LAYERS = [{ key: 'coverage', label: '网络覆盖度' }, { key: 'isochrone', label: '机会可达性' }, { key: 'tci', label: '公交竞争力' }];
import { readJson as read } from '../../utils/localApi';

export default function AnalysisPanel({ city, demandRun, selectedEvaluation, vis, onToggleVis, tciSession, onSessionChange, onEvaluationComplete, onDirtyChange }) {
  const [openKey, setOpenKey] = useState('coverage');
  const [params, setParams] = useState(INITIAL);
  const [inputs, setInputs] = useState({});
  const [job, setJob] = useState(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [tciData, setTciData] = useState(null);
  const [credentials, setCredentials] = useState({ key: process.env.REACT_APP_AMAP_KEY || '', security: process.env.REACT_APP_AMAP_SECURITY_CODE || '' });
  const [collecting, setCollecting] = useState({});
  const [versions, setVersions] = useState({ isochrone: [], tci: [] });
  const [inputsLoading, setInputsLoading] = useState(false);
  const [inputRevision, setInputRevision] = useState(0);
  const [applied, setApplied] = useState(null);
  const [taskHistory, setTaskHistory] = useState([]);
  const dirty = applied != null && JSON.stringify({ parameters: params, inputs }) !== JSON.stringify(applied);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  const scope = useRef(null); scope.current = { city: city.id, demand: demandRun?.run_id };
  const matches = own => scope.current.city === own.city && scope.current.demand === own.demand;
  const collected = (kind, file) => { setInputs(old => ({ ...old, [kind]: file })); setInputRevision(old => old + 1); };
  const collectingChanged = (kind, value) => setCollecting(old => old[kind] === value ? old : ({ ...old, [kind]: value }));
  useEffect(() => {
    const controller = new AbortController();
    setMessage(''); setJob(null); setBusy(false); setInputs({}); setVersions({ isochrone: [], tci: [] }); setCollecting({}); setApplied(null);
    fetch(`/api/cities/${city.id}/workflow-settings?demand_run_id=${demandRun?.run_id || ''}`, { signal: controller.signal }).then(read).then(result => {
      const settings = selectedEvaluation?.workflow_parameters || result.defaults;
      setInputs(result.inputs); setJob(result.job); setParams(settings);
      setApplied({ parameters: settings, inputs: result.inputs });
    }).catch(e => { if (e.name !== 'AbortError') setMessage(e.message); });
    return () => controller.abort();
  }, [city.id, demandRun?.run_id]);
  useEffect(() => {
    const controller = new AbortController(); setInputsLoading(true);
    fetch(`/api/cities/${city.id}/workflow-settings?demand_run_id=${demandRun?.run_id || ''}&minutes=${params.isochrone.max_time}`, { signal: controller.signal }).then(read).then(result => {
      setVersions(result.input_versions);
      setInputs(old => Object.fromEntries(['isochrone', 'tci'].map(kind => [kind, result.input_versions[kind].some(row => row.file === old[kind]) ? old[kind] : result.inputs[kind]])));
      setInputsLoading(false);
    }).catch(error => { if (error.name !== 'AbortError') { setMessage(error.message); setInputsLoading(false); } });
    return () => controller.abort();
  }, [city.id, demandRun?.run_id, params.isochrone.max_time, inputRevision]);
  useEffect(() => {
    if (selectedEvaluation?.workflow_parameters) {
      const settings = { ...selectedEvaluation.workflow_parameters, isochrone: { ...selectedEvaluation.workflow_parameters.isochrone, method: selectedEvaluation.workflow_parameters.isochrone.method || 'legacy_distance' } };
      setParams(settings);
      const files = Object.fromEntries(['isochrone','tci'].map(kind => [kind, selectedEvaluation.source_files?.[kind]?.split(/[\\/]/).pop() || null]));
      setInputs(files); setApplied({ parameters: settings, inputs: files });
    }
  }, [selectedEvaluation?.run_id]);
  useEffect(() => {
    if (!selectedEvaluation?.available_layers?.includes('tci')) { setTciData(null); return undefined; }
    const controller = new AbortController();
    fetch(`/api/cities/${city.id}/composite-runs/${selectedEvaluation.run_id}/tci`, { signal: controller.signal }).then(read).then(setTciData).catch(() => {});
    return () => controller.abort();
  }, [selectedEvaluation?.run_id]);
  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const own = { ...scope.current };
    const timer = setInterval(async () => {
      try {
        const next = await fetch(`/api/jobs/${job.id}`).then(read); if (!matches(own)) return; setJob(next);
        if (next.status === 'completed') {
          if (next.result.city_id !== own.city || next.result.demand_run_id !== own.demand) { setMessage('其他需求版本的任务已完成，请为当前需求版本重新计算。'); return; }
          onEvaluationComplete(next.result);
          setMessage(next.result.warnings?.join('；') || '评价流程已完成');
          onToggleVis(next.result.available_layers.includes(openKey) ? openKey : 'coverage', true);
        } else if (next.status === 'failed') setMessage(next.error);
      } catch (e) { if (matches(own)) setMessage(e.message); }
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]);
  useEffect(() => {
    const controller = new AbortController(); setTaskHistory([]);
    fetch(`/api/jobs?city_id=${city.id}`, { signal: controller.signal }).then(read).then(value => setTaskHistory(value.jobs.filter(row => row.kind === 'workflow' && row.options?.demand_run_id === demandRun?.run_id))).catch(error => { if (error.name !== 'AbortError') setMessage(error.message); });
    return () => controller.abort();
  }, [city.id, demandRun?.run_id, job?.status]);
  const retry = async row => {
    const own = { ...scope.current };
    try {
      const next = await fetch(`/api/cities/${city.id}/workflows`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(row.options) }).then(read);
      if (matches(own)) { setJob(next); setMessage('按原参数重新计算，历史结果不会覆盖。'); }
    } catch (error) { if (matches(own)) setMessage(error.message); }
  };
  const change = (group, key, value) => setParams(old => ({ ...old, [group]: { ...old[group], [key]: value } }));
  const run = async () => {
    if (!demandRun) { setMessage('请先生成共享需求版本'); return; }
    setMessage('');
    const own = { ...scope.current };
    try { const next = await fetch(`/api/cities/${city.id}/workflows`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ demand_run_id: demandRun.run_id, parameters: params, input_files: inputs }) }).then(read); if (matches(own)) setJob(next); }
    catch (e) { if (matches(own)) setMessage(e.message); }
  };
  const upload = async (event, kind) => {
    const file = event.target.files?.[0]; event.target.value = ''; if (!file) return;
    const own = { ...scope.current };
    setBusy(true); setMessage('');
    try {
      let payload = JSON.parse(await file.text());
      const metadata = payload.metadata || {};
      if (metadata.city_id && metadata.city_id !== city.id) throw new Error('文件与当前城市不一致');
      if (metadata.demand_run_id && metadata.demand_run_id !== demandRun?.run_id) throw new Error('文件与当前需求版本不一致');
      const provenance = { ...metadata, city_id: city.id, demand_run_id: demandRun?.run_id, source: metadata.source || 'user_upload' };
      payload = Array.isArray(payload) ? { metadata: provenance, records: payload } : { ...payload, metadata: provenance };
      const result = await fetch(`/api/cities/${city.id}/workflow-inputs/${kind}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(read);
      if (!matches(own)) return;
      collected(kind, result.file);
      setMessage('已导入，点击“应用参数并计算评价流程”生成新结果。');
    } catch (e) { if (matches(own)) setMessage(e.message); } finally { if (matches(own)) setBusy(false); }
  };
  const template = () => {
    const blob = new Blob([JSON.stringify([{ origin_wgs84: city.center, session: 'evening', bus_time: 35, car_time: 20, transfer: 1 }], null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `${city.id}_navigation_template.json`; link.click(); URL.revokeObjectURL(url);
  };
  const evaluationRunning = ['queued', 'running'].includes(job?.status);
  const collectionRunning = Object.values(collecting).some(Boolean);
  const running = busy || evaluationRunning || collectionRunning || inputsLoading;
  const available = selectedEvaluation?.available_layers;
  const validParameters = params.coverage.walk_full < params.coverage.walk_zero;
  const restoreApplied = () => { if (applied) { setParams(applied.parameters); setInputs(applied.inputs); } };
  const values = tciData?.features.map(f => f.properties[`tci_${tciSession}`]).filter(v => typeof v === 'number') || [];
  return <div style={{ color: '#c8d7e4', fontSize: 12, padding: '8px 0' }}>
    <div style={{ padding: '8px 14px', lineHeight: 1.7 }}>
      <div>共享需求：{demandRun ? demandRun.run_id : '尚未生成，请先到需求配置'}</div>
      <div>滑块修改后点击计算才生效；每次结果记录需求版本及全部参数，相同条件复用缓存。</div>
    </div>
    <details style={{ margin: '4px 14px 12px', border: '1px solid #ffffff22', borderRadius: 6, padding: 8 }} open={!credentials.key || !credentials.security}>
      <summary style={{ cursor: 'pointer' }}>高德采集凭证（等时圈与导航共用）</summary>
      <label style={{ display: 'block', marginTop: 6 }}>JS API Key<input aria-label="高德JS API Key" type="password" autoComplete="off" value={credentials.key} onChange={e => setCredentials(old => ({ ...old, key: e.target.value }))} disabled={collectionRunning} style={{ ...CONTROL, width: '100%' }} /></label>
      <label style={{ display: 'block', marginTop: 6 }}>JS安全密钥<input aria-label="高德JS安全密钥" type="password" autoComplete="off" value={credentials.security} onChange={e => setCredentials(old => ({ ...old, security: e.target.value }))} disabled={collectionRunning} style={{ ...CONTROL, width: '100%' }} /></label>
      <div style={{ fontSize: 11, marginTop: 6 }}>仅保留在当前页面内存，不写入结果；请使用JS类型Key。</div>
    </details>
    {LAYERS.map(layer => <div key={layer.key}>
      <div onClick={() => setOpenKey(openKey === layer.key ? null : layer.key)} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 14px', borderBottom: '1px solid #ffffff12', cursor: 'pointer' }}>
        <span>{layer.label}</span>{((!available && city.id === 'Beijing' && !demandRun) || available?.includes(layer.key)) && <Toggle active={vis?.[layer.key]} onClick={() => onToggleVis(layer.key)} />}
      </div>
      {<div style={{ display: openKey === layer.key ? undefined : 'none', padding: '0 14px 12px' }}>
        {layer.key !== 'coverage' && <div style={{ marginTop: 8 }}>
          <label>使用的采集版本<select aria-label={layer.key === 'isochrone' ? '等时圈采集版本' : '导航采集版本'} style={{ ...CONTROL, width: '100%', marginTop: 4 }} disabled={running} value={inputs[layer.key] || ''} onChange={e => setInputs(old => ({ ...old, [layer.key]: e.target.value || null }))}>
            <option value="">不使用（尚未选择或暂无记录）</option>
            {(versions[layer.key] || []).map(row => <option key={row.file} value={row.file}>{row.file.replace(layer.key + '_', '')} · {row.records}条 · {row.subset_only ? '抽样' : '上传记录'}{row.minutes.length ? ' · ' + row.minutes.join('/') + '分钟' : ''}</option>)}
          </select></label>
          {inputs[layer.key] && <a style={{ color: '#80d7a5', fontSize: 11 }} href={`/api/cities/${city.id}/workflow-inputs/${layer.key}/${inputs[layer.key]}`} download>下载所选原始记录</a>}
        </div>}
        {layer.key === 'coverage' && <>
          <SectionLabel>步行到站便捷度</SectionLabel>
          <SliderRow disabled={evaluationRunning || collectionRunning} label="满分距离" value={params.coverage.walk_full} min={0} max={1000} step={50} unit="米" onChange={v => change('coverage', 'walk_full', v)} />
          <SliderRow disabled={evaluationRunning || collectionRunning} label="零分距离" value={params.coverage.walk_zero} min={100} max={2000} step={50} unit="米" onChange={v => change('coverage', 'walk_zero', v)} />
          <DualSlider disabled={evaluationRunning || collectionRunning} labelA="步行便捷度" labelB="线路多样性" valueA={params.coverage.walk_weight} onChange={v => change('coverage', 'walk_weight', v)} />
          <SliderRow disabled={evaluationRunning || collectionRunning} label="线路统计半径" value={params.coverage.diversity_r} min={100} max={2000} step={50} unit="米" onChange={v => change('coverage', 'diversity_r', v)} />
        </>}
        {layer.key === 'isochrone' && <>
          <SectionLabel>等时圈机会评分</SectionLabel>
          <label>机会评分模型<select aria-label="机会评分模型" disabled={evaluationRunning || collectionRunning} style={{ ...CONTROL, width: '100%' }} value={params.isochrone.method || 'cumulative'} onChange={e => change('isochrone', 'method', e.target.value)}>
            <option value="cumulative">累计机会：不估造出行时间</option><option value="time_bands">多时间档：衰减区间上下界</option><option value="legacy_distance">历史程序：距离近似时间</option>
          </select></label>
          <p>{params.isochrone.method === 'time_bands' ? '采集15分钟步长的所有档位；结果为衰减评分上下界，不是精确OD耗时。' : params.isochrone.method === 'legacy_distance' ? '保留历史程序的距离比例近似；不是精确公交耗时，也不称为论文逐公式复现。' : '统计时间预算内可达POI的重要性总和；σ在此模式不参与计算。'}</p>

          <SliderRow disabled={evaluationRunning || collectionRunning || params.isochrone.method === 'cumulative'} label="时间衰减带宽 σ" value={params.isochrone.sigma} min={1} max={60} unit="分钟" onChange={v => change('isochrone', 'sigma', v)} />
          <SliderRow disabled={evaluationRunning || collectionRunning} label="等时圈时间上限" value={params.isochrone.max_time} min={5} max={90} step={5} unit="分钟" onChange={v => change('isochrone', 'max_time', v)} />
          <p>上限改变时需采集对应时间的等时圈，不能把45分钟边界当作30分钟边界。</p>
          <AmapCollectionPanel city={city} demandRun={demandRun} kind="isochrone" minutes={params.isochrone.max_time} method={params.isochrone.method || 'cumulative'} credentials={credentials} onBusyChange={collectingChanged} disabled={evaluationRunning || busy} onCollected={collected} onSessionChange={onSessionChange} />
          <label>导入原始等时圈<input aria-label="等时圈记录文件" type="file" accept=".json,.geojson" onChange={e => upload(e, 'isochrone')} disabled={running || !demandRun} /></label>
          <p>当前来源：{inputs.isochrone || '尚未采集本市等时圈'}。历史结果不会自动充当新城市的采集结果。</p>
        </>}
        {layer.key === 'tci' && <>
          <SectionLabel>导航竞争力参数</SectionLabel>
          <SliderRow disabled={evaluationRunning || collectionRunning} label="额外换乘惩罚 α" value={params.tci.alpha} min={0} max={30} unit="分钟/次" onChange={v => change('tci', 'alpha', v)} hint="导航耗时已含步行和候车；此处仅表示额外感知惩罚。" />
          <SliderRow disabled={evaluationRunning || collectionRunning} label="公交耗时上限" value={params.tci.bus_max} min={10} max={180} unit="分钟" onChange={v => change('tci', 'bus_max', v)} />
          <SliderRow disabled={evaluationRunning || collectionRunning} label="驾车耗时上限" value={params.tci.car_max} min={5} max={120} unit="分钟" onChange={v => change('tci', 'car_max', v)} />
          <AmapCollectionPanel city={city} demandRun={demandRun} kind="tci" minutes={params.isochrone.max_time} method={params.isochrone.method || 'cumulative'} credentials={credentials} onBusyChange={collectingChanged} disabled={evaluationRunning || busy} onCollected={collected} onSessionChange={onSessionChange} />
          <label>导入原始导航记录<input aria-label="导航记录文件" type="file" accept=".json" onChange={e => upload(e, 'tci')} disabled={running || !demandRun} /></label>
          <button onClick={template}>下载记录格式示例</button><p>示例数值仅用于说明格式，不能直接用于研究。当前来源：{inputs.tci || '缺少原始时间，无法重算历史聚合值'}。</p>
          <select aria-label="竞争力时段" style={CONTROL} value={tciSession} onChange={e => onSessionChange(e.target.value)}><option value="snapshot">实时采集快照</option><option value="morning">早高峰</option><option value="midday">平峰</option><option value="evening">晚高峰</option></select>
          <p>{values.length ? `${values.length}个有效格网，均值 ${(values.reduce((a, b) => a + b, 0) / values.length).toFixed(3)}` : '本版本当前时段暂无原始记录计算结果。'}</p>
        </>}
      </div>}
    </div>)}
    <div style={{ padding: 14 }}>
      {!validParameters && <div role="alert" style={{ color: '#ffca83' }}>满分距离必须小于零分距离，请先修正步行阈值。</div>}
      {dirty && <button onClick={restoreApplied} disabled={running}>放弃修改，恢复已应用设置</button>}
      <button disabled={!demandRun || running || !validParameters} onClick={run} style={{ width: '100%', padding: '10px 4px', cursor: 'pointer' }}>应用参数并计算评价流程</button>
      {collectionRunning && <div style={{ marginTop: 8 }}>采集中；完成并保存后再计算评价，避免使用未完成的数据。</div>}
      {selectedEvaluation && <div style={{ marginTop: 8, display: 'flex', gap: 12 }}><a href={`/api/cities/${city.id}/composite-runs/${selectedEvaluation.run_id}/export`} download>完整评价包</a><a href={`/api/cities/${city.id}/composite-runs/${selectedEvaluation.run_id}/summary`} download>下载评价摘要</a><a href={`/api/cities/${city.id}/composite-runs/${selectedEvaluation.run_id}/parameters`} download>下载实际参数</a></div>}
      {!inputs.isochrone && <div style={{ color: '#ffca83', marginTop: 8 }}>缺少等时圈：可计算需求和覆盖度，暂不能生成机会评分、综合评价及供需分类。</div>}
      {!inputs.tci && <div style={{ color: '#9babb8', marginTop: 8 }}>缺少导航：公交竞争力暂不可用；不会将未采集值补为0。</div>}
      {!!taskHistory.length && <details style={{ marginTop: 12 }}><summary>本需求版本的评价任务记录（{taskHistory.length}）</summary>
        {taskHistory.slice(-8).reverse().map(row => <div key={row.id} style={{ marginTop: 8, borderTop: '1px solid #ffffff18', paddingTop: 6 }}>
          {row.id.slice(0,8)} · {({ completed: '已完成', failed: '失败', interrupted: '已中断', queued: '排队中', running: '运行中' })[row.status] || row.status}
          {row.error && <div style={{ color: '#ffca83' }}>{row.error}</div>}
          {row.result && <button onClick={() => onEvaluationComplete(row.result)}>查看此结果</button>}
          {['failed','interrupted'].includes(row.status) && row.options && <button disabled={running} onClick={() => retry(row)}>按原设置重试</button>}
        </div>)}
      </details>}
      {job && <div style={{ marginTop: 8 }}>{job.status === 'failed' ? '评价失败，可修正输入后重新计算：' : job.status === 'interrupted' ? '上次任务已中断，可重新计算：' : ''}{job.phase}{job.result?.cache_hit ? '（复用缓存）' : ''}</div>}
      {message && <div style={{ color: '#ffca83', marginTop: 8, lineHeight: 1.7 }}>{message}</div>}
    </div>
  </div>;
}
