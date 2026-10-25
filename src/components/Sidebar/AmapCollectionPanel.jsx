import React, { useCallback, useEffect, useRef, useState } from 'react';
import { collectorFrame } from './amapCollectorFrame';
const CONTROL = { background: '#111a28', color: '#dbe6ef', border: '1px solid #ffffff33', borderRadius: 4, padding: '5px 6px', fontSize: 12 };
const read = async response => { const value = await response.json(); if (!response.ok) throw new Error(value.error || '请求失败'); return value; };
const STATUS = { ready: '待开始', paused: '已暂停／可恢复', completed: '已完成', collecting: '历史采集中／需核验', unverified: '状态未核验' };
const SESSION = { snapshot: '实时快照', morning: '早高峰7–9时', midday: '平峰9–17时', evening: '晚高峰17–19时' };
const currentSession = () => { const hour = Number(new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Shanghai', hour: '2-digit', hour12: false }).format(new Date())) % 24; return hour >= 7 && hour < 9 ? 'morning' : hour >= 9 && hour < 17 ? 'midday' : hour >= 17 && hour < 19 ? 'evening' : 'snapshot'; };
const counts = job => `成功 ${job.valid_records ?? '未记录'} / 无法到达 ${job.no_route_records ?? '未记录'} / 失败 ${job.failed_records ?? '未记录'}`;

export default function AmapCollectionPanel({ city, demandRun, kind, minutes, method, onCollected, onSessionChange, credentials, onBusyChange, disabled }) {
  const { key, security } = credentials;
  const [count, setCount] = useState(20);
  const [destinations, setDestinations] = useState(3);
  const [session, setSession] = useState('snapshot');
  const [budget, setBudget] = useState(500);
  const [jobs, setJobs] = useState([]);
  const [resumeId, setResumeId] = useState('');
  const [waitForWindow, setWaitForWindow] = useState(false);
  const [refresh, setRefresh] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(null);
  const [message, setMessage] = useState('');
  const frame = useRef(null);
  const pending = useRef(null);
  const context = useRef(null);
  const callbacks = useRef({ onCollected, onSessionChange, onBusyChange });
  callbacks.current = { onCollected, onSessionChange, onBusyChange };
  const selected = useRef(null); selected.current = { city: city.id, demand: demandRun?.run_id, kind };
  const post = useCallback((type, value) => frame.current?.contentWindow?.postMessage({ channel: 'urban-amap', type, value }, window.location.origin), []);
  const isCurrent = useCallback(own => context.current === own && selected.current.city === own.city && selected.current.demand === own.demand && selected.current.kind === own.kind, []);
  useEffect(() => { callbacks.current.onBusyChange?.(kind, running); }, [kind, running]);
  useEffect(() => {
    const listener = event => {
      if (event.origin !== window.location.origin || event.source !== frame.current?.contentWindow || event.data?.channel !== 'urban-amap') return;
      const own = context.current;
      if (!own || !isCurrent(own)) return;
      const { type, value } = event.data;
      if (type === 'ready' && pending.current && !own.cancelled) {
        own.phase = 'collecting'; post('start', pending.current); pending.current = null;
      }
      if (!['collecting', 'stopping'].includes(own.phase)) return;
      if (type === 'progress') setProgress(value);
      if (type === 'error') { pending.current = null; context.current = null; setMessage(value); setRunning(false); }
      if (type === 'complete') {
        const result = value.stored_result;
        if (!result) setMessage('服务端分批保存尚未完成，请核对历史后恢复');
        else {
          if (result.file) callbacks.current.onCollected?.(kind, result.file);
          if (kind === 'tci') callbacks.current.onSessionChange?.(own.config.session);
          const state = result.state;
          setMessage(`${STATUS[state?.status] || '状态待核验'}；${state ? counts(state) : `成功 ${result.valid_records}`}。失败记录不作为零值；抽样不代表全城普查。`);
        }
        pending.current = null; context.current = null; setRunning(false);
      }
    };
    window.addEventListener('message', listener);
    return () => {
      window.removeEventListener('message', listener); post('stop');
      if (context.current) { context.current.cancelled = true; context.current.wake?.(); }
      context.current = null; pending.current = null; callbacks.current.onBusyChange?.(kind, false);
    };
  }, [kind, post, isCurrent]);
  useEffect(() => {
    post('stop');
    if (context.current) { context.current.cancelled = true; context.current.wake?.(); }
    context.current = null; pending.current = null;
    setResumeId(''); setJobs([]); setRefresh(false); setRunning(false); setProgress(null); setMessage('');
  }, [city.id, demandRun?.run_id, kind, post]);
  useEffect(() => {
    const controller = new AbortController();
    const base = `/api/cities/${city.id}/collection-runs`;
    const load = async () => {
      const value = await fetch(base, { signal: controller.signal }).then(read);
      const matching = value.runs.filter(row => row.kind === kind && row.demand_run_id === demandRun?.run_id);
      // The list API returns legacy state; derive counts/status read-only via detail.
      const details = [];
      for (const row of matching) {
        if (controller.signal.aborted) return;
        try { details.push(await fetch(`${base}/${row.id}`, { signal: controller.signal }).then(read)); }
        catch (error) { if (controller.signal.aborted) return; details.push({ ...row, status: 'unverified', detailError: true }); }
      }
      if (!controller.signal.aborted) setJobs(details);
    };
    load().catch(error => { if (!controller.signal.aborted) setMessage(`采集历史读取失败：${error.message}`); });
    return () => controller.abort();
  }, [city.id, demandRun?.run_id, running, kind]);
  const resumeJob = jobs.find(job => job.id === resumeId);
  const savedPlan = resumeJob?.detailError ? null : resumeJob?.plan;
  const historyStatus = job => running && context.current?.config?.server_job_id === job.id ? '本页任务运行中（采集／等待时段）' : STATUS[job.status] || job.status;
  const start = async () => {
    if (context.current) return;
    const own = { city: city.id, demand: demandRun.run_id, kind, phase: 'planning', cancelled: false }; context.current = own;
    const active = () => isCurrent(own) && !own.cancelled;
    setRunning(true); setMessage('正在读取有效采集计划'); setProgress(null);
    try {
      const base = `/api/cities/${own.city}/collection-runs`;
      const created = resumeId ? { id: resumeId } : await fetch(base, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ demand_run_id: own.demand, kind, count, minutes, method, destinations, session, budget }) }).then(read);
      if (!active()) return;
      const saved = await fetch(`${base}/${created.id}`).then(read);
      if (!active()) return;
      const config = { ...saved.plan, server_job_id: created.id, completed_keys: refresh ? [] : saved.completed_keys, force_refresh: refresh };
      if (config.city_id !== own.city || config.demand_run_id !== own.demand || config.kind !== kind) throw new Error('恢复任务与当前城市、需求或类型不一致');
      own.config = config;
      setResumeId(created.id); setJobs(previous => [saved, ...previous.filter(job => job.id !== saved.id)]);
      if (kind === 'tci' && config.session !== 'snapshot' && currentSession() !== config.session) {
        if (!waitForWindow) throw new Error('当前时间不属于保存计划时段，请启用等待时段；恢复不会使用当前表单覆盖原计划');
        own.phase = 'waiting';
        setMessage(`等待保存计划时段：${SESSION[config.session]}；需保持页面打开，凭证仅在内存。`);
        while (active() && currentSession() !== config.session) {
          await new Promise(resolve => {
            const timer = setTimeout(() => { own.wake = null; resolve(); }, 10000);
            own.wake = () => { clearTimeout(timer); own.wake = null; resolve(); };
          });
        }
        if (!active()) return;
      }
      if (!active()) return;
      own.phase = 'frame';
      setMessage(`保存计划：${config.points.length} 条抽样${kind === 'tci' ? 'OD' : '起点'}，最多 ${config.planned_requests} 次请求；已存 ${saved.records} 条。抽样不代表全城普查。`);
      pending.current = { key, security, config };
      frame.current.srcdoc = collectorFrame;
    } catch (error) {
      if (active()) { pending.current = null; context.current = null; setMessage(error.message); setRunning(false); }
    }
  };
  const stop = () => {
    const own = context.current;
    if (!own) return;
    own.cancelled = true; own.wake?.(); pending.current = null;
    if (own.phase === 'collecting' || own.phase === 'stopping') {
      own.phase = 'stopping'; post('stop'); setMessage('正在停止，当前请求结束后保存已完成记录。');
    } else {
      post('stop'); context.current = null; setRunning(false);
      setMessage('已停止等待／启动；未发起高德采集。已保存计划可手动恢复。');
    }
  };
  const locked = running || !!resumeId;
  return <div style={{ border: '1px solid #ffffff22', borderRadius: 6, padding: 8, margin: '10px 0', lineHeight: 1.7 }}>
    <div>高德实时采集 · {city.name}</div>
    <label>{kind === 'tci' ? '抽样起点数（非OD总数）' : '抽样起点数'} <input aria-label="高德采集样本数量" type="number" min={1} max={100000} value={count} disabled={locked} onChange={e => setCount(Number(e.target.value))} style={{ ...CONTROL, width: 80 }} /></label>
    {kind === 'tci' && <><label style={{ display: 'block' }}>每起点终点数 <input aria-label="每起点终点数" type="number" min={1} max={20} value={destinations} onChange={e => setDestinations(Number(e.target.value))} disabled={locked} style={{ ...CONTROL, width: 65 }} /></label>
      <select aria-label="采集时段" style={CONTROL} value={session} onChange={e => setSession(e.target.value)} disabled={locked}>{Object.entries(SESSION).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <label style={{ display: 'block' }}><input type="checkbox" checked={waitForWindow} onChange={e => setWaitForWindow(e.target.checked)} disabled={running} />等待实际时段采集（保持页面打开）</label></>}
    <label style={{ display: 'block' }}>本次请求预算 <input aria-label="采集请求预算" type="number" min={1} max={5000} value={budget} onChange={e => setBudget(Number(e.target.value))} disabled={locked} style={{ ...CONTROL, width: 75 }} /></label>
    <div style={{ fontSize: 11 }}>预算是软件保护上限，不是账号剩余额度；默认每秒最多1次串行请求，各服务实际配额以高德控制台为准。</div>
    <select aria-label="恢复采集任务" style={{ ...CONTROL, width: '100%' }} value={resumeId} onChange={e => { setResumeId(e.target.value); setRefresh(false); }} disabled={running}><option value="">新建采集任务</option>{jobs.map(row => <option key={row.id} value={row.id}>{STATUS[row.status] || row.status} · {row.records}条已存 · {row.id.slice(0,8)}</option>)}</select>
    {resumeId && <div aria-label="恢复有效计划" style={{ fontSize: 11 }}>
      {savedPlan ? <>
        <div>保存计划（只读）：{SESSION[savedPlan.session] || '时段未记录'}；时间上限 {Math.max(...savedPlan.minutes)} 分钟；方法 {savedPlan.method || (savedPlan.minutes.length > 1 ? '分时段（按保存时间序列）' : '单时间上限（旧计划未记录方法名）')}</div>
        <div>时间序列 {savedPlan.minutes.join('、')} 分钟；抽样{kind === 'tci' ? 'OD对' : '起点'} {savedPlan.points.length}；每起点终点数 {savedPlan.destinations_per_origin ?? '不适用／未记录'}；预算 {savedPlan.request_budget ?? '未记录'}；间隔 {savedPlan.interval_ms ?? '未记录'} 毫秒。</div>
        <div>{STATUS[resumeJob.status] || resumeJob.status}；{counts(resumeJob)}。当前表单与外部评价参数不覆盖保存计划。</div>
      </> : '计划未读取成功，不能恢复，请重新读取历史。'}
    </div>}
    <label style={{ display: 'block', fontSize: 11 }}><input type="checkbox" checked={refresh} onChange={e => setRefresh(e.target.checked)} disabled={running} />重新采集，不复用缓存（会重新消耗配额）</label>
    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
      <button style={CONTROL} disabled={disabled || running || !demandRun || !key || !security || (resumeId ? !savedPlan : !Number.isInteger(count) || count < 1 || count > 100000) || (!!resumeId && resumeJob?.status === 'completed' && !refresh)} onClick={start}>{resumeId ? '恢复保存计划' : `采集本市${kind === 'tci' ? '导航' : '等时圈'}`}</button>
      {running && <button style={CONTROL} onClick={stop}>停止并保存</button>}
    </div>
    {progress && <div>进度 {progress.done}/{progress.total}；请求 {progress.requests} 次；缓存 {progress.cache_hits} 条</div>}
    <div style={{ color: '#ffca83', fontSize: 11 }}>{message}</div>
    <div aria-label="采集历史" style={{ fontSize: 11 }}>采集历史（当前城市／需求版本）：{jobs.length ? jobs.map(job => <div key={job.id}>{job.id.slice(0,8)} · {historyStatus(job)} · {counts(job)}{job.detailError ? '（详情读取失败，状态未核验）' : ''}</div>) : '暂无记录'}</div>
    <div style={{ fontSize: 10, color: '#9babb8' }}>{kind === 'tci' ? '同一抽样OD比较常规公交与驾车实时耗时（含步行、候车）；记录真实采集时间，不冒充历史高峰。30分钟快照内复用缓存。' : '常规公交BUS等时圈；按保存城市、需求、时间上限和抽样点恢复，失败重试。'}100个等时圈起点、60对OD等均为抽样，不代表全城。刷新页面不会自动采集。</div>
    <iframe title="隔离的高德采集服务" ref={frame} style={{ display: 'none' }} />
  </div>;
}
