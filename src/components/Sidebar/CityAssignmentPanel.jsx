import ResultExports from './ResultExports';
import React, { useEffect, useState, useRef } from 'react';
import { Alert, Button, InputNumber, Select } from 'antd';
import { serverPeriodGroup, comparisonCsv } from './assignmentComparison';

const PARAMETER_FIELDS = [
  { key: 'paths_per_transfer', label: 'K：每个换乘次数的候选路径数', value: 3, min: 1, integer: true },
  { key: 'access_station_count', label: '最多接入站点数', value: 3, min: 1, integer: true },
  { key: 'max_walk_distance_m', label: '最大步行距离（公里）', value: 1000, min: 0.001, scale: 1000, step: 0.1 },
  { key: 'min_od_distance_km', label: 'OD最小距离（公里）', value: 1, min: 0, step: 0.1 },
  { key: 'max_od_distance_km', label: 'OD最大距离（公里）', value: 20, min: 0.001, step: 1 },
  { key: 'max_transfers', label: '最多换乘次数', value: 2, min: 0, integer: true },
  { key: 'transfer_penalty_min', label: '换乘惩罚（分钟/次）', value: 2, min: 0, step: 0.5 },
  { key: 'bus_speed_kmh', label: '公交速度（公里/小时）', value: 18, min: 0.1, step: 1 },
  { key: 'vehicle_capacity', label: '车辆载客量（人/辆）', value: 80, min: 1, step: 1 },
  { key: 'max_iterations', label: '最大迭代次数', value: 500, min: 1, integer: true },
  { key: 'relative_gap_tol', label: '相对间隙停止阈值', value: 1e-4, min: 1e-12, max: 0.999999, step: 1e-4 },
];
const DEFAULT_PARAMETERS = Object.fromEntries(PARAMETER_FIELDS.map(field => [field.key, field.value]));
const parameterText = (values, field) => {
  const value = values?.[field.key];
  if (value == null) return '未记录';
  return field.key === 'relative_gap_tol' ? Number(value).toExponential(2) : String(value / (field.scale || 1));
};
const parameterSignature = run => JSON.stringify(PARAMETER_FIELDS.map(field => run.parameters?.[field.key] ?? null));

const PERIODS = { am_peak: '早高峰', offpeak: '平峰', pm_peak: '晚高峰' };
const STATUS = { pending: '等待中', queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', interrupted: '中断', cancelled: '已停止' };
const read = async response => { const value = await response.json(); if (!response.ok) throw new Error(value.error || '请求失败'); return value; };

const CityAssignmentPanel = ({ city, selectedRun: externalRun, onRunSelect, onDemandChange, active, demandRun, restoredConfig, onCompare, onConfigChange, onScenarioChange, onHistoricalView, restoreKey }) => {
  const [inputs, setInputs] = useState(null);
  const [runs, setRuns] = useState([]);
  const [compareRunId, setCompareRunId] = useState(null);
  const [comparisonMode,setComparisonMode]=useState('scenario');
  const [historyQuery,setHistoryQuery]=useState('');
  const [appliedQuery,setAppliedQuery]=useState('');
  const [hasMore,setHasMore]=useState(false);
  const [historyTotal,setHistoryTotal]=useState(0);
  const [historyBusy,setHistoryBusy]=useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');
  const [period, setPeriod] = useState('pm_peak');
  const [poiCount, setPoiCount] = useState(500);
  const [demand, setDemand] = useState(100000);
  const [headway, setHeadway] = useState(15);
  const [preflight, setPreflight] = useState(null);
  const [preflightBusy, setPreflightBusy] = useState(false);
  const [taskHistory, setTaskHistory] = useState([]);
  const [parameters, setParameters] = useState({ ...DEFAULT_PARAMETERS });
  const [inspectedRun, setInspectedRun] = useState(null);
  const [notice, setNotice] = useState('');
  const [controlBusy, setControlBusy] = useState(false);
  const pendingMap = useRef(null);
  const detailRequest = useRef(0);
  const configScope = useRef(null);
  const configKey = JSON.stringify([city.id, city.custom_input_version, restoreKey]);
  if (configScope.current?.key !== configKey || configScope.current?.restore !== restoredConfig) configScope.current = { key: configKey, restore: restoredConfig };
  const configContext = configScope.current;
  const [configOwner, setConfigOwner] = useState(null);
  const savedScenario = restoredConfig?.city_id === city.id ? restoredConfig.assignment_scenario : null;
  const scenarioDemand = useRef(null);
  const selectedRun = (inspectedRun?.city_id === city.id ? inspectedRun : null) || (externalRun?.city_id === city.id ? externalRun : null);
  const reusedDemand = useRef(null);
  const scope = useRef(null);
  const scopeKey = JSON.stringify([city.id, city.custom_input_version, demandRun?.run_id, restoreKey]);
  if (scope.current?.key !== scopeKey || scope.current?.config !== configContext) scope.current = { key: scopeKey, config: configContext };
  const context = scope.current;
  const matches = own => own === scope.current;

  useEffect(() => {
    setInspectedRun(null); setCompareRunId(null); setPreflight(null); setPreflightBusy(false);
    setControlBusy(false); setHistoryBusy(false); setNotice(''); setError(''); setTaskHistory([]);
    const pending = pendingMap.current;
    pendingMap.current = null;
    if (pending?.city_id === city.id && pending.demand_run_id === demandRun?.run_id) {
      setInspectedRun(pending); onRunSelect(pending);
      setNotice('已切换到历史需求版本，并显示该实验客流图。');
    }
  }, [context]);

  useEffect(() => {
    const controller = new AbortController();
    const own = scope.current;
    setInputs(null);
    setRuns([]);
    setCompareRunId(null);
    setJob(null); setHistoryBusy(false);
    setError('');
    Promise.all([
      fetch(`/api/cities/${encodeURIComponent(city.id)}/inputs`, { signal: controller.signal }).then(read),
      fetch(`/api/cities/${encodeURIComponent(city.id)}/runs?q=${encodeURIComponent(appliedQuery)}`, { signal: controller.signal }).then(read),
    ]).then(([prepared, history]) => {
      if (controller.signal.aborted || !matches(own)) return;
      setInputs(prepared);
      setRuns(history.runs || []); setHasMore(!!history.has_more); setHistoryTotal(history.total ?? history.runs?.length ?? 0);
      setJob(history.job || null);
      if (history.job && ['queued', 'running'].includes(history.job.status) && history.job.options?.demand_run_id !== demandRun?.run_id) setNotice('本市既有分配任务使用不同的需求版本；不会将其结果加载到当前需求，可在任务历史查看或停止。');

    }).catch(e => { if (!controller.signal.aborted && matches(own) && e.name !== 'AbortError') setError(e.message || '无法读取城市数据或实验记录'); });
    return () => controller.abort();
  }, [context, active, appliedQuery]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const own = scope.current;
    let disposed = false;
    const timer = setInterval(() => {
      fetch(`/api/jobs/${job.id}`)
        .then(read)
        .then(next => {
          if (disposed || !matches(own)) return;
          setJob(next);
          setTaskHistory(previous => [next, ...previous.filter(row => row.id !== next.id)]);
          if (next.status === 'completed' && next.result) {
            if (next.result.city_id === city.id && next.result.demand_run_id && next.result.demand_run_id === demandRun?.run_id) { setInspectedRun(null); onRunSelect(next.result); }
            else setNotice('任务已完成，但需求版本不同；请在任务历史中查看结果。');
            setRuns(previous => [next.result, ...previous.filter(run => run.run_id !== next.result.run_id)]);
          }
          if (next.status === 'failed') setError(next.error || '分配计算失败');
        })
        .catch(() => { if (!disposed && matches(own)) setError('无法读取实验进度'); });
    }, 1500);
    return () => { disposed = true; clearInterval(timer); };
  }, [job?.id, job?.status, context]);

  useEffect(() => {
    const key = `${demandRun?.run_id}:${period}`;
    const restored = scenarioDemand.current;
    const preserve = restored?.context === configContext && restored.period === period && (!restored.demandId || restored.demandId === demandRun?.run_id);
    const value = preserve ? restored.value : reusedDemand.current?.key === key ? reusedDemand.current.value : demandRun?.parameters?.hourly_demands?.[period];
    if (preserve && demandRun) restored.demandId = demandRun.run_id;
    reusedDemand.current = null;
    if (demandRun) setDemand(value ?? null);
  }, [demandRun?.run_id, period, configContext]);
  useEffect(() => {
    const own = scope.current;
    let disposed = false;
    const refresh = () => fetch(`/api/jobs?city_id=${encodeURIComponent(city.id)}`).then(read).then(value => {
      if (!disposed && matches(own)) setTaskHistory((value.jobs || []).filter(row => row.kind === 'assignment'));
    }).catch(error => { if (!disposed && matches(own)) setError('无法读取任务历史：' + error.message); });
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => { disposed = true; clearInterval(timer); };
  }, [context, job?.status, active]);
  const checkScale = async () => {
    const own = scope.current;
    setPreflightBusy(true); setError('');
    try {
      const value = await fetch(`/api/cities/${encodeURIComponent(city.id)}/assignment-preflight`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poi_count: poiCount, parameters, period }) }).then(read);
      if (matches(own)) setPreflight(value);
    } catch (error) { if (matches(own)) setError(error.message); }
    finally { if (matches(own)) setPreflightBusy(false); }
  };
  const control = async (identity, action) => {
    const own = scope.current;
    setControlBusy(true); setError('');
    try {
      const value = await fetch(`/api/jobs/${identity}/${action}`, { method: 'POST' }).then(read);
      if (!matches(own)) return;
      setJob(value);
      setTaskHistory(previous => [value, ...previous.filter(row => row.id !== value.id)]);
      setNotice(action === 'cancel' ? '已请求停止；需等待安全边界，停止后才能恢复。' : '已提交原任务选项；未使用当前表单参数。');
    } catch (error) { if (matches(own)) setError(error.message); }
    finally { if (matches(own)) setControlBusy(false); }
  };
  const viewRun = async run => {
    if (!run) { setError('任务未记录可查看的结果，请检查已有实验列表。'); return; }
    const own = scope.current;
    const request = ++detailRequest.current;
    onRunSelect(null); setError('');
    try {
      const detail = await fetch('/api/cities/' + encodeURIComponent(city.id) + '/runs/' + encodeURIComponent(run.run_id) + '/summary').then(read);
      if (!matches(own) || request !== detailRequest.current) return;
      if (detail.city_id !== city.id) throw new Error('实验与当前城市不一致');
      setInspectedRun(detail);
      setNotice('已打开完整历史详情，未修改当前需求草稿。请显式选择只读查看，或加载对应有效需求后显示客流图。新计算仍使用当前共享需求。');
    } catch (error) { if (matches(own) && request === detailRequest.current) setError('无法读取历史详情：' + error.message); }
  };
  const switchHistoricalDemand = async () => {
    const own = scope.current;
    const run = selectedRun;
    const request = detailRequest.current;
    try {
      const summary = await fetch(`/api/cities/${encodeURIComponent(city.id)}/demand-runs/${encodeURIComponent(run.demand_run_id)}/summary`).then(read);
      if (!matches(own)) return;
      if (request !== detailRequest.current) return;
      const history = await fetch('/api/cities/' + encodeURIComponent(city.id) + '/demand-runs').then(read);
      if (!matches(own) || request !== detailRequest.current) return;
      const effective = history.runs?.find(row => row.run_id === summary.run_id && row.current_inputs !== false);
      if (!effective || summary.current_inputs === false) throw new Error('历史需求对应旧输入或版本不可用，不能作为当前共享需求；请使用历史只读查看');
      if (summary.city_id !== city.id || summary.run_id !== run.demand_run_id) throw new Error('历史需求版本与实验不一致');
      if (demandRun?.run_id === summary.run_id) { onRunSelect(run); setNotice('已加载对应有效需求并显示客流图；当前需求草稿未修改。'); return; }
      pendingMap.current = run;
      onDemandChange(effective);
      setNotice('正在切换历史需求版本…');
    } catch (error) { if (matches(own)) setError('无法切换历史需求：' + error.message); }
  };
  const start = async () => {
    const own = scope.current;
    setError('');
    try {
      const check = await fetch(`/api/cities/${city.id}/assignment-preflight`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ poi_count: poiCount, parameters, period }) });
      const bounds = await check.json(); if (!check.ok) throw new Error(bounds.error);
      if (!matches(own)) return;
      setPreflight(bounds);
      const response = await fetch(`/api/cities/${encodeURIComponent(city.id)}/assignments`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          period, poi_count: poiCount, total_demand: demand, parameters, demand_run_id: demandRun?.run_id,
          default_headway_min: headway,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '无法启动分配');
      if (matches(own)) {
        setJob(result);
        if (result.options?.demand_run_id !== demandRun?.run_id) setNotice('服务器返回了本市既有任务，其需求版本与当前不同；本次没有按当前表单新建任务。请在任务历史查看或停止原任务。');
      }
    } catch (e) {
      if (matches(own)) setError(e.message);
    }
  };

  const ready = !!inputs?.network && !!inputs?.poi && !!demandRun && demandRun.current_inputs !== false && demandRun.city_id === city.id;
  const running = [job, ...taskHistory].some(row => ['queued', 'running'].includes(row?.status));
  const parametersValid = PARAMETER_FIELDS.every(field => {
    const value = parameters[field.key];
    return value != null && Number.isFinite(value) && value / (field.scale || 1) >= field.min &&
      (!field.integer || Number.isInteger(value)) && (field.max == null || value <= field.max);
  }) && parameters.min_od_distance_km < parameters.max_od_distance_km;
  const reuseParameters = () => {
    setParameters(Object.fromEntries(PARAMETER_FIELDS.map(field => [field.key, selectedRun.parameters[field.key]])));
    reusedDemand.current = { key: `${demandRun?.run_id}:${selectedRun.period}`, value: selectedRun.hourly_demand_pax };
    setPeriod(selectedRun.period);
    setPoiCount(selectedRun.poi_count);
    setDemand(selectedRun.hourly_demand_pax);

    setHeadway(selectedRun.default_headway_min ?? selectedRun.assumed_headway_min ?? null);
  };
  const [comparisonRuns, setComparisonRuns] = useState(null);
  const comparisonRequest = useRef(null);
  const comparisonKey = JSON.stringify([scopeKey, selectedRun?.run_id, comparisonMode]);
  useEffect(() => {
    const controller = new AbortController();
    const own = context;
    setComparisonRuns(null);
    if (!selectedRun?.run_id) return () => controller.abort();
    fetch('/api/cities/' + encodeURIComponent(city.id) + '/runs/' + encodeURIComponent(selectedRun.run_id) + '/comparison?mode=' + comparisonMode, { signal: controller.signal })
      .then(read).then(value => { if (!controller.signal.aborted && matches(own)) setComparisonRuns({ key: comparisonKey, runs: value.runs || [] }); })
      .catch(error => { if (!controller.signal.aborted && matches(own)) { setComparisonRuns(null); setError('无法读取完整历史分时对照：' + error.message); } });
    return () => controller.abort();
  }, [context, selectedRun?.run_id, comparisonMode]);
  useEffect(() => {
    const saved = restoredConfig?.city_id === city.id ? restoredConfig : null;
    setParameters(saved?.assignment_parameters ? Object.fromEntries(PARAMETER_FIELDS.map(field => [field.key, saved.assignment_parameters[field.key] ?? null])) : { ...DEFAULT_PARAMETERS });
    const scenario = saved?.assignment_scenario;
    setPeriod(scenario ? scenario.period ?? null : saved?.assignment_run?.period ?? 'pm_peak');
    setPoiCount(scenario ? scenario.poi_count ?? null : 500);
    setDemand(scenario ? scenario.total_demand ?? null : demandRun ? demandRun.parameters?.hourly_demands?.pm_peak ?? null : 100000);
    setHeadway(scenario ? scenario.default_headway_min ?? null : 15);
    scenarioDemand.current = scenario ? { context: configContext, period: scenario.period, value: scenario.total_demand ?? null, demandId: demandRun?.run_id } : null;
    setConfigOwner(configContext);
  }, [configContext]);
  useEffect(() => { if (configOwner === configContext) onConfigChange?.(parameters); }, [parameters, configOwner, configContext, onConfigChange]);
  useEffect(() => {
    if (configOwner === configContext) onScenarioChange?.({ period, poi_count: poiCount, total_demand: demand, default_headway_min: headway });
  }, [period, poiCount, demand, headway, configOwner, configContext, onScenarioChange]);
  const periodGroup = comparisonRuns?.key === comparisonKey ? serverPeriodGroup(selectedRun, comparisonRuns.runs) : {};
  const latestQuery = useRef(appliedQuery); latestQuery.current = appliedQuery;
  const loadMore=async()=>{const query=appliedQuery;const own=scope.current;setHistoryBusy(true);try{const value=await fetch(`/api/cities/${city.id}/runs?q=${encodeURIComponent(appliedQuery)}&offset=${runs.length}`).then(read);if(matches(own) && latestQuery.current === query){setRuns(previous=>[...previous,...value.runs.filter(row=>!previous.some(old=>old.run_id===row.run_id))]);setHasMore(value.has_more);setHistoryTotal(value.total);}}catch(error){if(matches(own))setError(error.message);}finally{if(matches(own) && latestQuery.current === query)setHistoryBusy(false);}};
  const exportPeriods = () => {
    const url = URL.createObjectURL(new Blob([comparisonCsv(periodGroup)], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `${city.id}_${selectedRun.run_id}_period_comparison.csv`; link.click(); URL.revokeObjectURL(url);
  };
  const compareRun = runs.find(run => run.run_id === compareRunId && run.run_id !== selectedRun?.run_id);
  const formatNumber = (value, digits = 1) => (
    typeof value === 'number' && Number.isFinite(value)
      ? value.toLocaleString(undefined, { maximumFractionDigits: digits }) : '未记录'
  );
  const comparisonRows = [
    ['发车间隔补全比例', run => run.headway_default_share == null ? '未记录' : `${(100 * run.headway_default_share).toFixed(1)}%`],
    ['缺省发车间隔（分钟）', run => formatNumber(run.default_headway_min)],
    ['需求版本', run => run.demand_run_id || '未记录'],
    ['时段', run => PERIODS[run.period] || '未记录'],
    ['需求（人次/小时）', run => formatNumber(run.hourly_demand_pax, 0)],
    ['POI 数', run => formatNumber(run.poi_count, 0)],
    ...PARAMETER_FIELDS.map(field => [field.label, run => parameterText(run.parameters, field)]),
    ['候选路径', run => formatNumber(run.candidate_paths, 0)],
    ['准备耗时（秒）', run => formatNumber(run.preparation_seconds)],
    ['求解耗时（秒）', run => formatNumber(run.assignment_seconds)],
    ['容量再分配（秒）', run => formatNumber(run.reallocation_seconds)],
    ['全程耗时（秒）', run => formatNumber(run.total_seconds)],
    ['迭代次数', run => formatNumber(run.assignment_iterations, 0)],
    ['收敛', run => run.converged === 'yes' ? '是' : run.converged === 'no' ? '否' : '未记录'],
    ['相对间隙', run => typeof run.relative_gap === 'number' ? run.relative_gap.toExponential(2) : '未记录'],
    ['可服务比例', run => typeof run.served_demand_share === 'number' ? `${(run.served_demand_share * 100).toFixed(1)}%` : '未记录'],
    ['容量超限', run => formatNumber(run.capacity_violation, 3)],
  ];
  const resultFiles = [
    ['实验摘要', 'summary.json'],
    ['参数快照', 'parameters.json'],
    ...(!selectedRun?.source_experiment && selectedRun?.headway_basis?.includes('fallback') ? [['实际发车间隔', 'resolved_headways.csv']] : []),
    ['物理路段客流', 'physical_segment_flows.csv'],
    ['线路区段客流', 'route_section_flows.csv'],
    ['分配迭代', 'assignment_iterations.csv'],
    ['容量再分配', 'capacity_reallocation.csv'],
  ];
  const label = { color: '#c8d7e4', fontSize: 12, margin: '12px 0 5px' };
  const field = { width: '100%' };

  return (
    <div className="sidebar-content-wrapper" style={{ padding: 14, color: '#c8d7e4' }}>
      <div style={{ fontSize: 15, fontWeight: 700, color: '#e3edf4' }}>路段客流预测</div>
      <div style={{ fontSize: 11, color: '#9babb8', marginTop: 5 }}>共同线路候选路径分配 + 容量再分配；单位为人次/小时。</div>
      <div style={{ fontSize: 11, color: '#9babb8', marginTop: 5 }}>K作用于每个换乘次数的路径集合，不是每个OD总共只保留K条路径。</div>
      <div style={{ fontSize: 11, color: '#ffca83', marginTop: 5 }}>源线网没有逐时段运营状态；模型暂将可用线路视为运营，不能据此判断停运线路客流。</div>
      <div style={{ marginTop: 10, fontSize: 12 }}>共享需求：{demandRun?.run_id || '请先在需求配置中生成版本'}</div>
      {selectedRun && <div style={{ color: '#ffca83', fontSize: 11 }}>所选实验需求版本：{selectedRun.demand_run_id || '旧实验未引用共享需求版本'}</div>}
      <div style={label}>分析时段</div>
      <Select style={field} value={period} onChange={setPeriod} options={[
        { value: 'am_peak', label: '早高峰' }, { value: 'offpeak', label: '平峰' }, { value: 'pm_peak', label: '晚高峰' },
      ]} />
      <div style={label}>用于代表城市的 POI 数量</div>
      <InputNumber aria-label="用于代表城市的POI数量" style={field} min={2} precision={0} value={poiCount} onChange={setPoiCount} />
      {poiCount > 5000 && <Alert style={{ marginTop: 8 }} type="warning" message="耗时取决于线网、需求和候选路径规模，请先检查计算规模并参考同城实测；可在安全边界停止并保留断点。" />}
      <div style={label}>城市总公交需求（人次/小时）</div>
      <InputNumber aria-label="城市总公交需求（人次/小时）" style={field} min={1} precision={0} value={demand} onChange={setDemand} />
      <div style={{ ...label, marginTop: 18 }}>模型参数</div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px 8px' }}>
        {PARAMETER_FIELDS.map(parameter => (
          <div key={parameter.key}>
            <div style={{ fontSize: 11, marginBottom: 4 }}>{parameter.label}</div>
            <InputNumber aria-label={parameter.label} style={field} disabled={running}
              min={parameter.min} max={parameter.max} step={parameter.step || 1} precision={parameter.integer ? 0 : undefined}
              value={parameters[parameter.key] == null ? null : parameters[parameter.key] / (parameter.scale || 1)}
              onChange={value => setParameters(previous => ({ ...previous, [parameter.key]: value == null ? null : value * (parameter.scale || 1) }))} />
          </div>
        ))}
      </div>
      <Button size="small" disabled={running} style={{ marginTop: 10 }} onClick={() => setParameters({ ...DEFAULT_PARAMETERS })}>恢复默认模型参数</Button>
      <div style={{ color: '#9babb8', fontSize: 11, marginTop: 6 }}>迭代上限作用于每一轮候选路径扩充后的分配求解；历史摘要的迭代次数为各轮累计值。收敛间隙对应容量再分配前的期望分配结果。</div>
      {!parametersValid && <div style={{ color: '#ffca83', fontSize: 11, marginTop: 8 }}>请填写有效参数；OD最小距离必须小于最大距离，间隙阈值必须在0与1之间。</div>}
      <div style={label}>缺失线路默认发车间隔（分钟）</div>
      <InputNumber aria-label="缺失线路默认发车间隔（分钟）" style={field} min={0.1} value={headway} onChange={setHeadway} disabled={running} />
      <div style={{ color: '#ffca83', fontSize: 11, marginTop: 5 }}>上传值优先，仅缺失线路使用此缺省值。默认15分钟相当于4班/小时；这部分是情景假设，不是实测。</div>
      <div style={{ marginTop: 6, fontSize: 11 }}>已上传：{inputs?.service?.[({ am_peak: 'am_headway_routes', pm_peak: 'pm_headway_routes', offpeak: 'flat_headway_routes' })[period]] ?? inputs?.service?.peak_headway_routes ?? 0} 个线路方向。<a href={`/api/cities/${city.id}/headway-template`} download>下载模板</a>；在“数据管理”上传。</div>
      <Button size="small" onClick={checkScale} loading={preflightBusy} disabled={!ready || running || !parametersValid}>检查计算规模</Button>
      {preflight && <div style={{ fontSize: 11, lineHeight: 1.7, marginTop: 7 }}>理论OD上界 {preflight.upper_bounds.ordered_od_pairs.toLocaleString()}；路径上界 {preflight.upper_bounds.candidate_paths.toLocaleString()}；仅流量向量上界 {(preflight.upper_bounds.path_flow_float64_bytes / 1024 ** 2).toFixed(1)} MiB。这是筛选前上界，不是实际内存或耗时预测；生成路径后会记录实测规模。</div>}
      {preflight?.historical_references?.length > 0 && <div style={{ fontSize: 11, marginTop: 6 }}>历史实测参考（不保证本次耗时）：{preflight.historical_references.map(reference => `${reference.poi_count} POI约${reference.total_seconds.toFixed(1)}秒`).join('；')}。参数、版本和缓存状态可能不同，未对全城规模作未经验证的外推。</div>}
      <Button type="primary" block style={{ marginTop: 15 }} loading={running} disabled={!parametersValid || !ready || !PERIODS[period] || !poiCount || !demand || !headway} onClick={start}>
        {running ? '正在计算' : '开始交通分配'}
      </Button>
      {job && ['queued', 'running'].includes(job.status) && <Button size="small" danger disabled={controlBusy || job.cancel_requested} style={{ marginTop: 8 }} onClick={() => control(job.id, 'cancel')}>停止并保存断点</Button>}
      {job && running && <div style={{ fontSize: 11, marginTop: 8 }}>当前步骤：{job.phase || '等待开始'}。计算期间可切换页面。</div>}
      {!ready && <div style={{ fontSize: 11, color: '#ffca83', marginTop: 8 }}>请先准备城市线网、POI，并在“需求配置”生成共享版本。</div>}
      {notice && <Alert style={{ marginTop: 10 }} type="info" message={notice} />}
      {error && <Alert style={{ marginTop: 10 }} type="error" message={error} />}
      <div style={{ ...label, marginTop: 18 }}>任务历史</div>
      {!taskHistory.length && <div style={{ fontSize: 11 }}>暂无分配任务记录</div>}
      {taskHistory.map(row => <div key={row.id} style={{ fontSize: 11, marginTop: 8, lineHeight: 1.8 }}>
        <div>任务 {row.id}：{STATUS[row.status] || row.status} · {row.phase || '未记录步骤'}</div>
        <div>实验编号：{row.run_id || row.result?.run_id || '尚未记录'}；更新时间：{row.updated_at || '未记录'}</div>
        <div>需求版本：{row.options?.demand_run_id || '未记录'}；时段：{PERIODS[row.options?.period] || '未记录'}；POI：{row.options?.poi_count ?? '未记录'}；需求：{row.options?.total_demand ?? '未记录'}；缺省间隔：{row.options?.default_headway_min ?? '未记录'}</div>
        <details><summary>查看任务参数</summary>{PARAMETER_FIELDS.map(field => <div key={field.key}>{field.label}：{parameterText(row.options?.parameters, field)}</div>)}</details>
        {row.error && <div style={{ color: '#ffca83' }}>任务信息：{row.error}</div>}
        {['queued', 'running'].includes(row.status) && <Button size="small" disabled={controlBusy || row.cancel_requested} onClick={() => control(row.id, 'cancel')}>{row.cancel_requested ? '等待安全停止' : '停止任务并保留进度'}</Button>}
        {['cancelled', 'interrupted', 'failed'].includes(row.status) && <>
          <div>{row.run_id ? '已记录实验目录；恢复时由后端校验可用断点。' : '尚无实验目录，重试将按原任务参数从头执行。'}</div>
          <Button size="small" disabled={running || controlBusy} onClick={() => control(row.id, 'resume')}>{row.run_id ? '恢复断点任务' : '按原参数重试'}</Button>
        </>}
        {row.status === 'completed' && <Button size="small" onClick={() => viewRun(runs.find(run => run.run_id === (row.result?.run_id || row.run_id)) || row.result)}>查看任务结果</Button>}
      </div>)}
      <div style={{ ...label, marginTop: 22 }}>已有实验</div>
      <div style={{display:'flex',gap:5,marginBottom:7}}><input aria-label="检索历史实验" placeholder="编号、时段、需求或POI数量" value={historyQuery} onChange={event=>setHistoryQuery(event.target.value)} style={{minWidth:0,width:'100%'}}/><Button size="small" onClick={()=>setAppliedQuery(historyQuery)}>检索</Button></div>
      <div style={{fontSize:11}}>已显示{runs.length} / {historyTotal}组；历史文件不会因分页删除。</div>
      {runs.length ? (
        <Select style={field} value={selectedRun?.run_id} aria-label="已有实验" onChange={id => viewRun(runs.find(run => run.run_id === id))}
          options={runs.map(run => ({ value: run.run_id, label: `${PERIODS[run.period] || '未记录'} · ${run.poi_count} POI · ${run.source_experiment || run.run_id.slice(0, 15)}` }))} />
      ) : <div style={{ fontSize: 11, color: '#9babb8' }}>暂无分配结果</div>}
      {hasMore && <Button size="small" loading={historyBusy} onClick={loadMore}>加载更多历史实验</Button>}
      {selectedRun?.city_id === city.id && <div style={{ fontSize: 11, lineHeight: 1.8, marginTop: 10 }}>
        <Alert type="warning" message="历史详情不会覆盖当前需求草稿。只读查看不切换共享需求、不发起计算；新计算仍使用当前共享需求。切换历史需求将替换需求表单，请先保存草稿。" />
        {onHistoricalView && <Button size="small" onClick={() => { onHistoricalView(selectedRun); setNotice('历史只读查看：未切换当前共享需求，未修改需求草稿。新计算仍使用当前共享需求。'); }}>只读查看历史客流图（不切换当前需求）</Button>}
        {selectedRun.demand_run_id && onDemandChange && <Button size="small" onClick={switchHistoricalDemand}>加载对应有效需求并显示客流图</Button>}
        {!selectedRun.demand_run_id && <div>未记录对应需求版本；仅支持历史只读查看，不用于新计算。</div>}
        {selectedRun.poi_coordinate_system !== 'WGS84' && <Alert type="warning" message="历史实验未记录坐标转换来源；当前按原实验几何展示。" />}
        <div style={{ color: '#ffca83' }}>{selectedRun.converged === 'no' && '本次分配未达到间隙阈值，结果是迭代上限内的近似解，不应当作已收敛结果；'}{selectedRun.unserved_demand_pax_per_hour > 0 && `仍有 ${formatNumber(selectedRun.unserved_demand_pax_per_hour, 0)} 人次/小时未被服务；这是候选路径与容量约束下未分配的模型需求，不代表观测到的拒载人数。`}</div>
        {selectedRun.source_experiment && <div>来源实验：{selectedRun.source_experiment}</div>}
        <div style={{ marginTop: 12, fontWeight: 600 }}>本次实验实际参数（历史记录）</div>
        <div>需求版本：{selectedRun.demand_run_id || '未记录'}</div>
        <div>需求：{formatNumber(selectedRun.hourly_demand_pax, 0)} 人次/小时；POI：{formatNumber(selectedRun.poi_count, 0)}</div>
        <div>缺省发车间隔：{formatNumber(selectedRun.default_headway_min)} 分钟；上传数据指纹：{selectedRun.headway_upload_sha256 || '未记录'}</div>
        <div>算法：{selectedRun.algorithm || '未记录'}</div>
        <details><summary>查看完整实验摘要</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontSize: 11 }}>{JSON.stringify(selectedRun, null, 2)}</pre></details>
        <div style={{ color: '#9babb8' }}>仅显示实验摘要或参数快照中的原值；未记录项不使用当前默认值补齐。</div>
        {PARAMETER_FIELDS.map(parameter => <div key={parameter.key} style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
          <span>{parameter.label}</span><span>{parameterText(selectedRun.parameters, parameter)}</span>
        </div>)}
        <Button size="small" style={{ marginTop: 8 }} disabled={running || !PERIODS[selectedRun.period] || PARAMETER_FIELDS.some(parameter => selectedRun.parameters?.[parameter.key] == null) || selectedRun.hourly_demand_pax == null || selectedRun.poi_count == null || (selectedRun.default_headway_min == null && selectedRun.assumed_headway_min == null)} onClick={reuseParameters}>沿用本次参数创建新实验</Button>
        <div style={{ color: '#9babb8', fontSize: 11 }}>沿用参数不修改历史结果；发车频率按当前上传值及缺失默认值处理。</div>
        <div>有效 OD：{formatNumber(selectedRun.sampled_nonzero_od_pairs, 0)} 对；候选路径：{formatNumber(selectedRun.candidate_paths, 0)} 条</div>
        <div>分配收敛：{selectedRun.converged === 'yes' ? '是' : selectedRun.converged === 'no' ? '否' : '未记录'}；相对间隙：{typeof selectedRun.relative_gap === 'number' ? selectedRun.relative_gap.toExponential(2) : '未记录'}</div>
        <div>可服务客流：{formatNumber(selectedRun.served_demand_pax_per_hour, 0)} / {formatNumber(selectedRun.hourly_demand_pax, 0)} 人次/小时</div>
        <div style={{ color: selectedRun.headway_basis === 'explicit_uniform_assumption' ? '#ffca83' : '#80d7a5' }}>
          {selectedRun.headway_basis === 'explicit_uniform_assumption' ? `统一 ${selectedRun.assumed_headway_min} 分钟测试假设` : selectedRun.headway_basis === 'observed_with_empirical_imputation' ? `历史实验：实测频率及经验补全（补全比例 ${((selectedRun.headway_empirical_imputation_share || 0) * 100).toFixed(1)}%）` : selectedRun.headway_basis?.includes('fallback') ? `上传值优先，缺失默认 ${formatNumber(selectedRun.default_headway_min)} 分钟（补全 ${selectedRun.headway_default_share == null ? '未记录' : (100 * selectedRun.headway_default_share).toFixed(1) + '%'}）` : selectedRun.headway_basis === 'user_supplied_csv' ? '用户提供的逐线路发车间隔' : `频率来源：${selectedRun.headway_basis || '未记录'}`} 
        </div>
      </div>}
      {selectedRun?.city_id === city.id && <>
        <Button size="small" style={{ marginTop: 8 }} onClick={() => { detailRequest.current += 1; onRunSelect(null); setInspectedRun(null); setNotice('已关闭客流图。'); }}>关闭客流图</Button>
        <div style={label}>下载本次实验</div>
        <ResultExports city={city} runId={selectedRun.run_id} comparisonIds={compareRunId ? [compareRunId] : []} />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 12px', fontSize: 11 }}>
          {resultFiles.map(([name, filename]) => (
            <a key={filename} style={{ color: '#80d7a5' }}
              href={`/api/cities/${encodeURIComponent(city.id)}/runs/${encodeURIComponent(selectedRun.run_id)}/files/${filename}`} download>
              {name}
            </a>
          ))}
        </div>
      </>}
      <div style={{ ...label, marginTop: 18 }}>三时段对比口径</div>
      <Select aria-label="三时段比较口径" style={field} value={comparisonMode} onChange={setComparisonMode} options={[{value:'scenario',label:'分时情景对照：允许各时段总需求不同'},{value:'strict',label:'同条件分时对照：总需求与非时段参数相同'}]} />
      {comparisonMode==='scenario' && <Alert type="info" style={{marginTop:7}} message="各时段需求可不同；流量差异反映需求与出行方向情景，不能归因于算法性能。" />}
      <div style={{ fontSize: 11, color: '#ffca83' }}>分时匹配由服务端查询完整历史，不受列表每页30条或检索条件限制。同条件对照要求总需求及非时段条件相同；情景对照允许总需求不同。二者均不能把跨时段差异当成算法性能差异；历史缺失字段不补默认值。</div>
      {selectedRun && Object.keys(periodGroup).length > 0 && <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}>{comparisonMode==='strict'?'同条件三时段结果':'分时情景三时段结果'}</div>
        <div style={{ display:'grid', gridTemplateColumns:'minmax(0,1.3fr) repeat(3,minmax(0,1fr))', overflowWrap:'anywhere', gap:5, fontSize:11, marginTop:8 }}>
          <b>指标</b>{Object.values(PERIODS).map(name => <b key={name}>{name}</b>)}
          {[
            ['总需求（人次/h）', row => formatNumber(row.hourly_demand_pax,0)],
            ['模型耗时（秒）', row => formatNumber(row.total_seconds)],
            ['相对间隙', row => typeof row.relative_gap === 'number' ? row.relative_gap.toExponential(2) : '未记录'],
            ['达到阈值', row => row.converged === 'yes' ? '是' : row.converged === 'no' ? '否' : '未记录'],
            ['可服务比例', row => typeof row.served_demand_share === 'number' ? `${(100*row.served_demand_share).toFixed(2)}%` : '未记录'],
            ['未服务（人次/h）', row => formatNumber(row.unserved_demand_pax_per_hour,0)],
            ['候选路径数', row => formatNumber(row.candidate_paths,0)],
          ].map(([label,render]) => <React.Fragment key={label}><span>{label}</span>{Object.keys(PERIODS).map(period => <span key={period}>{periodGroup[period] ? render(periodGroup[period]) : comparisonMode === 'strict' ? '缺少同条件实验' : '缺少匹配情景实验'}</span>)}</React.Fragment>)}
        </div>
        <div style={{ display:'flex', flexWrap:'wrap', gap:6, marginTop:8 }}>{Object.keys(PERIODS).map(period => <Button key={period} size="small" disabled={!periodGroup[period]} onClick={() => viewRun(periodGroup[period])}>查看{PERIODS[period]}</Button>)}</div>
        <Button size="small" style={{marginTop:8}} onClick={exportPeriods}>导出三时段对照CSV</Button>
        <div style={{fontSize:11,color:'#9babb8',marginTop:5}}>按所选比较口径匹配记录；每个时段采用最近记录。情景对照允许总需求不同，不能将用时差异解释为算法优劣。</div>
      </div>}
      {selectedRun?.city_id === city.id && runs.length > 1 && <>
        <div style={label}>对比同城另一组实验（可检索或加载更多，不限最近30条）</div>
        <Select aria-label="对比实验" style={field} allowClear placeholder="选择对比实验" value={compareRunId}
          onChange={setCompareRunId}
          options={runs.filter(run => run.run_id !== selectedRun.run_id).map(run => ({
            value: run.run_id,
            label: `${PERIODS[run.period] || '未记录'} · ${run.poi_count} POI · ${run.run_id}`,
          }))} />
        {compareRun && <>
          {(compareRun.period !== selectedRun.period || compareRun.hourly_demand_pax !== selectedRun.hourly_demand_pax ||
            compareRun.poi_count !== selectedRun.poi_count || compareRun.headway_basis !== selectedRun.headway_basis ||
            compareRun.assumed_headway_min !== selectedRun.assumed_headway_min ||
            compareRun.default_headway_min !== selectedRun.default_headway_min ||
            compareRun.headway_upload_sha256 !== selectedRun.headway_upload_sha256 ||
            compareRun.demand_run_id !== selectedRun.demand_run_id ||
            compareRun.shared_demand_signature !== selectedRun.shared_demand_signature ||
            parameterSignature(compareRun) !== parameterSignature(selectedRun)) &&
            <Alert style={{ marginTop: 8 }} type="warning" message="两次实验的时段、需求、POI数量、模型参数或发车间隔假设不同；差异不能直接归因于求解算法。" />}
          <div style={{ display: 'grid', gridTemplateColumns: '1.15fr 1fr 1fr', gap: 4, marginTop: 9, fontSize: 11, color: '#c8d7e4' }}>
            <Button style={{gridColumn:'1 / -1'}} size="small" onClick={()=>onCompare?.({left:selectedRun,right:compareRun})}>联动地图 / 路段差值对照</Button>
            <b>指标</b><b>当前实验</b><b>对比实验</b>
            {comparisonRows.map(([name, render]) => <React.Fragment key={name}>
              <span>{name}</span><span>{render(selectedRun)}</span><span>{render(compareRun)}</span>
            </React.Fragment>)}
          </div>
        </>}
      </>}
    </div>
  );
};

export default CityAssignmentPanel;
