import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, InputNumber, Progress, Select } from 'antd';
import { readJson } from '../../utils/localApi';

const PERIODS = { am_peak: '早高峰', offpeak: '平峰', pm_peak: '晚高峰' };
const STATUS = { queued: '等待中', pending: '未开始', running: '运行中', completed: '已完成', partial: '部分完成（评价不完整）', cancel_requested: '等待停止', failed: '失败', cancelled: '已停止', interrupted: '已中断' };
const active = job => ['queued', 'running', 'cancel_requested'].includes(job?.status);
const resumable = job => ['failed', 'cancelled', 'interrupted'].includes(job?.status);
const field = { display: 'block', margin: '12px 0 5px' };
const request = (url, options) => fetch(url, options).then(readJson);
const post = payload => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

// GET batches -> {batches: Job[]}; POST/cancel/resume and GET jobs/:id -> Job.
// Result callbacks use existing demand/assignment/workflow summary objects.
export default function BatchWorkflowPanel({ city, demandRun, onDemandChange, onRunSelect, onEvaluationComplete, assignmentParameters, assignmentScenario, analysisParameters }) {
  const [jobs, setJobs] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [settings, setSettings] = useState(null);
  const [evaluationSettings, setEvaluationSettings] = useState(null);
  const [versions, setVersions] = useState({ isochrone: [], tci: [] });
  const [files, setFiles] = useState({ isochrone: null, tci: null });
  const [inputs, setInputs] = useState({ network: true, poi: true });
  const [periods, setPeriods] = useState(Object.keys(PERIODS));
  const [totals, setTotals] = useState({});
  const [poiCount, setPoiCount] = useState(500);
  const [headway, setHeadway] = useState(15);
  const [pendingResult, setPendingResult] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const scope = useRef(null);
  const key = JSON.stringify([city?.id, city?.custom_input_version]);
  if (scope.current?.key !== key) scope.current = { key };
  const scopeToken = scope.current;
  const cityUrl = `/api/cities/${encodeURIComponent(city?.id || '')}`;
  const selected = jobs.find(job => job.id === selectedId) || null;
  const anyActive = jobs.some(active);
  // Accept both the existing parameter map and a parameters/scenario snapshot.
  const parameters = assignmentParameters?.parameters || assignmentParameters;
  const scenario = assignmentScenario || assignmentParameters?.scenario || {};
  useEffect(() => {
    setPoiCount(scenario.poi_count === undefined ? 500 : scenario.poi_count);
    setHeadway(scenario.default_headway_min === undefined ? 15 : scenario.default_headway_min);
  }, [scopeToken, scenario.poi_count, scenario.default_headway_min]);

  useEffect(() => {
    if (!pendingResult || pendingResult.scope !== scopeToken) return;
    if (demandRun?.run_id !== pendingResult.demandId) return;
    setPendingResult(null);
    if (pendingResult.kind === 'assignment') onRunSelect?.(pendingResult.run);
    else onEvaluationComplete?.(pendingResult.run);
  }, [pendingResult, demandRun?.run_id, scopeToken, onRunSelect, onEvaluationComplete]);

  useEffect(() => {
    const controller = new AbortController();
    const own = scopeToken;
    setPendingResult(null); setJobs([]); setSelectedId(null); setError(''); setSettings(null);
    setEvaluationSettings(null); setFiles({ isochrone: null, tci: null });
    setVersions({ isochrone: [], tci: [] }); setTotals({}); setBusy(false); setLoading(true);
    if (!city?.id) { setLoading(false); return () => controller.abort(); }
    Promise.all([
      request(`${cityUrl}/batches`, { signal: controller.signal }),
      request(`${cityUrl}/demand-runs`, { signal: controller.signal }),
      request(`${cityUrl}/workflow-settings`, { signal: controller.signal }),
    ]).then(([history, demand, evaluation]) => {
      if (controller.signal.aborted || own !== scope.current) return;
      const rows = history.batches || [];
      setJobs(rows); setSelectedId((rows.find(active) || rows[0])?.id || null);
      setSettings(demand.defaults); setTotals({});
      setEvaluationSettings(evaluation.defaults);
      setVersions(evaluation.input_versions || { isochrone: [], tci: [] });
    }).catch(e => {
      if (!controller.signal.aborted && own === scope.current) setError(e.message);
    }).finally(() => {
      if (!controller.signal.aborted && own === scope.current) setLoading(false);
    });
    return () => controller.abort();
  }, [scopeToken, cityUrl, city?.id]);

  const liveIds = jobs.filter(active).map(job => job.id).join(',');
  useEffect(() => {
    if (!liveIds) return undefined;
    const controller = new AbortController();
    const own = scopeToken;
    let inFlight = false;
    const poll = async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const next = await Promise.all(liveIds.split(',').map(id => request(`/api/jobs/${encodeURIComponent(id)}`, { signal: controller.signal })));
        if (own === scope.current && !controller.signal.aborted) {
          setJobs(previous => previous.map(row => next.find(job => job.id === row.id) || row));
        }
      } catch (e) {
        if (own === scope.current && !controller.signal.aborted) setError(e.message);
      } finally { inFlight = false; }
    };
    const timer = setInterval(poll, 1500);
    return () => { controller.abort(); clearInterval(timer); };
  }, [liveIds, scopeToken]);

  const perform = async (url, payload) => {
    const own = scopeToken;
    setBusy(true); setError('');
    try {
      const job = await request(url, post(payload));
      if (own !== scope.current) return;
      setJobs(previous => [job, ...previous.filter(row => row.id !== job.id)]);
      setSelectedId(job.id);
    } catch (e) { if (own === scope.current) setError(e.message); }
    finally { if (own === scope.current) setBusy(false); }
  };
  const refresh = async () => {
    const own = scopeToken;
    setBusy(true); setError('');
    try {
      const value = await request(`${cityUrl}/batches`);
      if (own === scope.current) setJobs(value.batches || []);
    } catch (e) { if (own === scope.current) setError(e.message); }
    finally { if (own === scope.current) setBusy(false); }
  };
  const valid = periods.length > 0 && periods.every(period => totals[period] != null && Number.isFinite(Number(totals[period])) && Number(totals[period]) > 0)
    && Number.isInteger(poiCount) && poiCount >= 2 && Number.isFinite(headway) && headway > 0;
  const start = () => perform(`${cityUrl}/batches`, {
    inputs: { ...inputs, custom_input_version: city.custom_input_version ?? null },
    demand: { parameters: settings },
    periods: Object.keys(PERIODS).filter(period => periods.includes(period)).map(period => ({ period, total_demand: totals[period] })),
    assignment: { poi_count: poiCount, default_headway_min: headway, parameters, assumed_headway_min: scenario.assumed_headway_min ?? null },
    evaluation: { parameters: analysisParameters || evaluationSettings, input_files: files },
  });
  const results = selected?.state || {};
  const matchingDemand = !!results.demand && demandRun?.run_id === results.demand.run_id;
  const evaluation = results.evaluation;
  const usable = !!selected && selected.city_id === city?.id && selected.options?.inputs?.custom_input_version === (city?.custom_input_version ?? null);
  const selectResult = (kind, run) => {
    if (!usable || !results.demand) return;
    if (matchingDemand) {
      if (kind === 'assignment') onRunSelect?.(run);
      else onEvaluationComplete?.(run);
      return;
    }
    // Wait for the parent's demand selection to commit before selecting a result.
    setPendingResult({ scope: scopeToken, demandId: results.demand.run_id, kind, run });
    onDemandChange?.(results.demand);
  };

  if (!city?.id) return <Alert type="info" message="请先选择城市。" />;
  return <section style={{ padding: 14, color: '#c8d7e4', fontSize: 12 }} aria-label="批量全流程任务">
    <h3 style={{ color: 'inherit', margin: '0 0 8px' }}>批量 / 全流程任务</h3>
    <p>顺序执行：准备输入 → 共享需求 → 多时段分配 → 指标评价。不会自动采集高德；缺失数据只生成可计算的指标。</p>
    {error && <Alert type="error" message={error} style={{ marginBottom: 10 }} />}
    <fieldset disabled={busy || anyActive || loading} style={{ border: '1px solid #ffffff24', borderRadius: 6, padding: 10 }}>
      <legend>新批次配置</legend>
      <div style={field}>准备所选城市的输入（取消勾选则要求已有有效缓存）</div>
      {['network', 'poi'].map(name => <Checkbox key={name} checked={inputs[name]} disabled={busy || anyActive || loading}
        onChange={e => setInputs(previous => ({ ...previous, [name]: e.target.checked }))}>{name === 'network' ? '公交线网' : 'POI'}</Checkbox>)}
      <div style={field}>共享需求参数</div>
      <Select aria-label="批次共享需求参数" style={{ width: '100%' }} value="current" disabled={busy || anyActive || loading}
        options={[{ value: 'current', label: settings === demandRun?.parameters ? '使用当前需求参数' : '使用城市默认参数' }]} />
      <Button size="small" style={{ marginTop: 6 }} disabled={!demandRun?.parameters || busy || anyActive || loading}
        onClick={() => { setSettings(demandRun.parameters); }}>沿用当前需求参数</Button>
      <div style={field}>分配时段及总需求（人次/小时，必须逐项明确填写）</div>
      {Object.entries(PERIODS).map(([period, label]) => <div key={period} style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, gap: 8 }}>
        <Checkbox checked={periods.includes(period)} disabled={busy || anyActive || loading}
          onChange={e => setPeriods(previous => e.target.checked ? [...previous, period] : previous.filter(value => value !== period))}>{label}</Checkbox>
        <InputNumber aria-label={`${label}批次总需求`} min={1} value={totals[period]} style={{ width: 135 }}
          disabled={!periods.includes(period) || busy || anyActive || loading}
          onChange={value => setTotals(previous => ({ ...previous, [period]: value }))} />
      </div>)}
      <p>各时段总需求不从城市默认值或POI数量自动填充。其他分配参数沿用下方客流分配设置；指标参数沿用分析设置。创建后按快照固定。</p>
      <label style={field}>分配POI数量</label>
      <InputNumber aria-label="批次POI数量" min={2} precision={0} value={poiCount} onChange={setPoiCount} disabled={busy || anyActive || loading} />
      <label style={field}>缺失发车间隔时的缺省值（分钟）</label>
      <InputNumber aria-label="批次假设发车间隔" min={0.1} value={headway} onChange={setHeadway} disabled={busy || anyActive || loading} />
      {scenario.assumed_headway_min != null && <p>当前情景为统一间隔假设：{scenario.assumed_headway_min} 分钟，优先于缺省值。</p>}
      {['isochrone', 'tci'].map(kind => <div key={kind}>
        <label style={field}>{kind === 'isochrone' ? '等时圈原始输入' : '公交 / 驾车时间原始输入'}</label>
        <Select aria-label={`批次${kind}输入`} style={{ width: '100%' }} value={files[kind] ?? ''} disabled={busy || anyActive || loading}
          onChange={value => setFiles(previous => ({ ...previous, [kind]: value || null }))}
          options={[{ value: '', label: '不使用（仅计算其余指标）' }, ...(versions[kind] || []).filter(row => !row.demand_run_id).map(row => ({ value: row.file, label: row.file }))]} />
      </div>)}
      <p>只可选未绑定其他需求版本的本地评价输入；文件选择会固定保存，不会随“最新数据”变化。</p>
      <Button type="primary" block loading={busy} disabled={!settings || !evaluationSettings || !valid || busy || anyActive || loading} onClick={start}>创建新批次并运行</Button>
    </fieldset>
    <div style={{ ...field, display: 'flex', justifyContent: 'space-between' }}><strong>批次历史</strong><Button size="small" disabled={busy || loading} onClick={refresh}>刷新</Button></div>
    <Select aria-label="选择批任务" style={{ width: '100%' }} value={selectedId || undefined} placeholder={loading ? '读取中…' : '暂无批任务'} onChange={setSelectedId}
      options={jobs.map(job => ({ value: job.id, label: `${job.id.slice(0, 8)} · ${STATUS[job.status] || job.status}` }))} />
    {selected && <div style={{ marginTop: 12 }}>
      <Progress percent={selected.progress || 0} status={selected.status === 'failed' ? 'exception' : selected.status === 'running' ? 'active' : 'normal'} />
      <div role="status">{selected.phase} · {STATUS[selected.status] || selected.status}</div>
      {selected.status === 'partial' && <Alert type="warning" message="执行已结束，但评价输入缺失或仅为样本；需补齐输入后新建批次，不会通过恢复重复已完成步骤。" />}
      {selected.error && <Alert type="error" message={selected.error} style={{ marginTop: 8 }} />}
      <ol style={{ paddingLeft: 20 }}>{(selected.steps || []).map(step => <li key={step.id} style={{ marginTop: 6 }}>
        {step.kind === 'assignment' ? `分配：${PERIODS[step.period] || step.period}` : step.label} · {STATUS[step.status] || step.status}
        {step.error && <div style={{ color: '#ffca83' }}>{step.error}</div>}
      </li>)}</ol>
      {active(selected) && <Button danger disabled={busy || selected.cancel_requested || selected.status === 'cancel_requested'} onClick={() => perform(`/api/jobs/${selected.id}/cancel`, {})}>
        {selected.cancel_requested || selected.status === 'cancel_requested' ? '等待安全边界停止' : '停止批任务'}</Button>}
      {resumable(selected) && <Button disabled={busy || anyActive || !usable} onClick={() => perform(`/api/jobs/${selected.id}/resume`, {})}>
        {selected.status === 'failed' ? '仅重试未完成步骤' : '从未完成步骤恢复'}</Button>}
      <Button style={{ marginLeft: 6 }} disabled={busy || anyActive || !usable} onClick={() => perform(`${cityUrl}/batches`, selected.options)}>按原配置新建批次</Button>
      {!usable && <p style={{ color: '#ffca83' }}>此批次属于其他输入版本；保留历史，不在当前输入下恢复或加载。</p>}
      {results.demand && <Button block style={{ marginTop: 10 }} disabled={!usable} onClick={() => onDemandChange?.(results.demand)}>使用此批共享需求</Button>}
      {results.demand && !matchingDemand && <p>选择结果会先切换到此批共享需求，再加载对应结果。</p>}
      {Object.entries(results.assignments || {}).map(([period, run]) => <Button key={period} block style={{ marginTop: 6 }} disabled={!results.demand || !usable || !!pendingResult}
        onClick={() => selectResult('assignment', run)}>查看{PERIODS[period] || period}客流</Button>)}
      {evaluation && <>
        <Button block style={{ marginTop: 6 }} disabled={!results.demand || !usable || !!pendingResult} onClick={() => selectResult('evaluation', evaluation)}>查看指标评价</Button>
        {(evaluation.warnings || []).map((warning, index) => <p key={index} style={{ color: '#ffca83' }}>{warning}</p>)}
        <div>可用指标：{(evaluation.available_layers || []).join('、') || '见评价结果'}</div>
      </>}
    </div>}
  </section>;
}
