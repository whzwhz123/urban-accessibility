import React, { useEffect, useMemo, useRef, useState } from 'react';
import DeckGL from '@deck.gl/react';
import { GeoJsonLayer } from '@deck.gl/layers';
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { ImageExportControls } from '../Sidebar/ResultExports';
import { composeMapCanvas } from '../../utils/mapExport';

const BASEMAP = {
  version: 8,
  sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
    tileSize: 256, attribution: '© OpenStreetMap contributors' } },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};
const GRAY = [120, 125, 132, 210];
const MISSING = [178, 117, 33, 230];
const runId = run => typeof run === 'string' ? run : run?.run_id;
const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
const canonical = value => JSON.stringify(value && typeof value === 'object'
  ? (Array.isArray(value) ? value.map(v => JSON.parse(canonical(v)))
    : Object.fromEntries(Object.keys(value).sort().map(k => [k, JSON.parse(canonical(value[k]))])))
  : value ?? null);
const geometryKey = feature => feature.geometry?.coordinates ? canonical({ type: feature.geometry.type, coordinates: feature.geometry.coordinates }) : null;
const edgeKey = feature => feature.properties?.edge_idx == null ? null : String(feature.properties.edge_idx);

function indexEdges(features) {
  const index = new globalThis.Map();
  features.forEach(feature => {
    const key = edgeKey(feature);
    if (key !== null) index.set(key, index.has(key) ? null : feature);
  });
  return index;
}

// Never pair duplicates, absent IDs, or changed geometry; no tolerance or spatial guess.
function matchEdges(left, right) {
  const li = indexEdges(left), ri = indexEdges(right);
  const matched = new Set();
  li.forEach((feature, key) => {
    const other = ri.get(key);
    if (feature && other && geometryKey(feature) && geometryKey(feature) === geometryKey(other)) matched.add(key);
  });
  return { li, ri, matched };
}

function sameConditions(left, right, cityId) {
  const keys = ['city_id', 'period', 'demand_run_id', 'shared_demand_signature', 'poi_count',
    'hourly_demand_pax', 'algorithm', 'headway_basis', 'parameters'];
  const issues = keys.filter(key => left?.[key] == null || right?.[key] == null || canonical(left[key]) !== canonical(right[key]));
  if (left?.city_id !== cityId || right?.city_id !== cityId) issues.push('城市未核实');
  for (const key of ['headway_upload_sha256', 'default_headway_min', 'assumed_headway_min', 'headway_source_counts']) {
    if (canonical(left?.[key]) !== canonical(right?.[key])) issues.push(key);
  }
  if (!left?.headway_upload_sha256 && left?.assumed_headway_min == null) issues.push('频率输入一致性未核实');
  return issues;
}

function color(value, extent, difference) {
  if (value === null) return MISSING;
  if (!difference) {
    const t = Math.max(0, Math.min(1, extent ? value / extent : 0));
    if(value===0)return [160,175,176,180];
    const from=t<.5?[30,178,106]:[246,208,44],to=t<.5?[246,208,44]:[218,52,51],fraction=t<.5?t*2:(t-.5)*2;
    return from.map((v,index)=>Math.round(v+(to[index]-v)*fraction)).concat(230);
  }
  const t = Math.min(1, extent ? Math.abs(value) / extent : 0);
  const target = value < 0 ? [35, 102, 190] : [195, 53, 47];
  return target.map(v => Math.round(240 + (v - 240) * t)).concat(245);
}

function initialView(features) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const visit = coordinates => {
    if (!Array.isArray(coordinates)) return;
    if (typeof coordinates[0] === 'number') {
      const [x, y] = coordinates;
      if (!Number.isFinite(x) || !Number.isFinite(y)) return;
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    } else coordinates.forEach(visit);
  };
  features.forEach(feature => visit(feature.geometry?.coordinates));
  if (!Number.isFinite(minX)) return null;
  return { longitude: (minX + maxX) / 2, latitude: (minY + maxY) / 2,
    zoom: Math.max(2, Math.min(15, Math.log2(220 / Math.max(maxX-minX, (maxY-minY)*1.6, 0.01)))),
    pitch: 0, bearing: 0 };
}

/** Independent map: city = ID or {id,name}; comparison.left/right = saved summary
 * objects or run ID strings. Strings are supported but cannot prove comparability.
 * Only the existing /runs/{runId}/flow endpoint is fetched; no computation starts.
 */
export default function AssignmentCompareMap({ city, comparison, onClose }) {
  const cityId = typeof city === 'string' ? city : city?.id;
  const leftId = runId(comparison?.left), rightId = runId(comparison?.right);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [metric, setMetric] = useState('flow');
  const [variant, setVariant] = useState('served');
  const [difference, setDifference] = useState(false);
  const [viewState, setViewState] = useState({ longitude: 105, latitude: 35, zoom: 3, pitch: 0, bearing: 0 });
  const leftContainer = useRef(null), rightContainer = useRef(null);
  const requestKey = `${cityId}/${leftId}/${rightId}`;
  useEffect(() => {
    const controller = new AbortController();
    setData(null); setError('');
    if (!cityId || !leftId || !rightId) { setError('请选择城市及左右结果版本'); return () => controller.abort(); }
    const load = async id => {
      const response = await fetch(`/api/cities/${encodeURIComponent(cityId)}/runs/${encodeURIComponent(id)}/flow`, { signal: controller.signal });
      if (!response.ok) throw new Error(`读取 ${id} 失败 (${response.status})`);
      const result = await response.json();
      if (result.type !== 'FeatureCollection' || !Array.isArray(result.features)) throw new Error('路段接口未返回 GeoJSON FeatureCollection');
      return result.features;
    };
    Promise.all([load(leftId), load(rightId)]).then(([left, right]) => {
      if (controller.signal.aborted) return;
      setData({ key: requestKey, left, right });
      const view = initialView([...left, ...right]);
      if (view) setViewState(view);
    }).catch(failure => { if (!controller.signal.aborted) setError(failure.message); });
    return () => controller.abort();
  }, [cityId, leftId, rightId, requestKey]);
  const field = `${variant}_${metric === 'flow' ? 'flow_pax_per_hour' : 'vc'}`;
  const units = metric === 'flow' ? '人次/小时' : 'V/C（无量纲）';
  const active = data?.key === requestKey ? data : null;
  const prepared = useMemo(() => {
    if (!active) return null;
    const { li, ri, matched } = matchEdges(active.left, active.right);
    const decorate = (feature, side) => {
      const key = edgeKey(feature), paired = matched.has(key);
      const leftValue = paired ? finite(li.get(key).properties?.[field]) : null;
      const rightValue = paired ? finite(ri.get(key).properties?.[field]) : null;
      const value = difference ? (leftValue !== null && rightValue !== null ? rightValue-leftValue : null)
        : finite(feature.properties?.[field]);
      return { ...feature, properties: { ...feature.properties, _paired: paired, _value: value,
        _left: leftValue, _right: rightValue, _side: side,
        _status: !paired ? '未匹配：缺失/重复 edge_idx 或几何不同' : value === null ? '已匹配但指标缺失' : '已匹配' } };
    };
    const left = active.left.map(f => decorate(f, '左'));
    const right = active.right.map(f => decorate(f, '右'));
    // Difference renders each matched segment once, plus both unmatched geometries.
    const delta = [...left, ...right.filter(f => !f.properties._paired)];
    const visible = difference ? delta : [...left, ...right];
    let extent = 0, minimum = 0;
    visible.forEach(feature => {
      const value = feature.properties._value;
      if (feature.properties._paired && value !== null) { extent = Math.max(extent, Math.abs(value)); minimum = Math.min(minimum, value); }
    });
    return { left, right, delta, extent, minimum, matched: matched.size,
      unmatchedLeft: left.filter(f => !f.properties._paired).length,
      unmatchedRight: right.filter(f => !f.properties._paired).length,
      missing: left.filter(f => f.properties._paired && (f.properties._left === null || f.properties._right === null)).length };
  }, [active, field, difference]);
  const issues = sameConditions(comparison?.left, comparison?.right, cityId);
  const extent = prepared?.extent || 0;
  const label = `${variant === 'served' ? '已服务' : '期望'}${metric === 'flow' ? '流量' : ' V/C'}`;
  const legendMin = difference ? -extent : 0;
  const tooltip = ({ object }) => {
    if (!object) return null;
    const p = object.properties;
    const fmt = value => value === null ? '缺失（非零）' : value.toFixed(4);
    const displayed = difference && !p._paired ? '不计算（未匹配）' : `${fmt(p._value)} ${units}`;
    return { text: `edge_idx：${p.edge_idx ?? '未记录'}\n来源：${p._side}侧\n${p._status}\n${label}${difference ? '差值' : ''}：${displayed}`
      + (difference && p._paired ? `\n左：${fmt(p._left)}\n右：${fmt(p._right)}\n差值 = 右 - 左` : '') };
  };
  const pane = (features, title, reference, id) => <section style={{ minWidth: 0 }} key={id}>
    <h4 style={{ margin: '8px 0', overflowWrap: 'anywhere' }}>{title}</h4>
    <div ref={reference} style={{ position: 'relative', height: 'min(60vh, 600px)', minHeight: 300, overflow: 'hidden' }}>
      <DeckGL viewState={viewState} onViewStateChange={({ viewState: next }) => setViewState(next)} controller
        glOptions={{ preserveDrawingBuffer: true }} getTooltip={tooltip}
        layers={[new GeoJsonLayer({ id: `assignment-compare-${id}`, data: features,
          pickable: true, filled: false, stroked: true, lineWidthUnits: 'pixels', getLineWidth: 3,
          getLineColor: feature => !feature.properties._paired ? GRAY : color(feature.properties._value, extent, difference),
          updateTriggers: { getLineColor: [extent, difference, field] } })]}>
        <Map mapStyle={BASEMAP} preserveDrawingBuffer attributionControl />
      </DeckGL>
    </div>
    <ImageExportControls canvas={() => composeMapCanvas(reference.current)} metadata={{
      city: typeof city === 'string' ? city : city?.name || cityId,
      runId: difference ? `${leftId} → ${rightId}` : (id === 'left' ? leftId : rightId),
      title, units, legendLabel: difference ? `${label} 差值（右 - 左）` : label,
      legendMin, legendMax: extent, coordinateSystem: 'EPSG:4326 数据；Web Mercator 底图',
      legendItems: [{ label: '底图 © OpenStreetMap contributors', color: '#182c35' }, { label: '未匹配路段（不计算差值）', color: '#787d84' }, { label: '指标缺失（非零）', color: '#b27521' },
        ...(difference ? [{ label: '蓝色：减少；红色：增加', color: '#c3352f' }] : [])],
    }} />
  </section>;
  return <section aria-label="分配结果地图对比" className="assignment-comparison-view" style={{ height:'100%',overflowY:'auto',padding: 16,paddingLeft:332, background: '#f5f4ef', color: '#182c35', width: '100%', boxSizing: 'border-box' }}>
    <header style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}><h3 style={{ marginTop: 0 }}>分配结果地图对比</h3>{onClose && <button type="button" onClick={onClose}>关闭</button>}</header>
    <p role="note">{issues.length ? `情景比较：同条件无法保证（${issues.join('、')}）。差异不能归因于单一因素。`
      : '记录的需求、频率及模型参数一致；仅说明已记录条件一致，不代表已验证全部输入一致。'}</p>
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
      <label>指标 <select value={metric} onChange={event => setMetric(event.target.value)}><option value="flow">流量</option><option value="vc">V/C</option></select></label>
      <label>口径 <select value={variant} onChange={event => setVariant(event.target.value)}><option value="served">已服务</option><option value="desired">期望</option></select></label>
      <button type="button" onClick={() => setDifference(value => !value)}>{difference ? '切换双图联动' : '切换差值图（右 - 左）'}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {!active && !error && <p role="status">正在读取已保存路段结果…</p>}
    {prepared && <>
      {active.left.length === 0 && active.right.length === 0 && <p role="status">两个版本均没有可显示的路段数据，不能计算差值。</p>}
      <p>匹配 {prepared.matched} 段；左侧未匹配 {prepared.unmatchedLeft} 段；右侧未匹配 {prepared.unmatchedRight} 段；匹配段指标缺失 {prepared.missing} 段。灰色为未匹配，不填零。</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 12 }}>
        <span><i style={{ display: 'inline-block', width: 14, height: 4, background: '#787d84', marginRight: 6 }} />灰色：未匹配，不计算差值</span>
        <span><i style={{ display: 'inline-block', width: 14, height: 4, background: '#b27521', marginRight: 6 }} />橙色：已匹配但指标缺失，不填零</span>
      </div>
      <div aria-label="统一色标" style={{ maxWidth: 440, margin: '12px 0' }}>
        <div>{difference ? `${label} 差值（右 - 左）` : `${label}（左右统一范围）`} · {units}</div>
        <div style={{ height: 12, margin: '6px 0', background: difference ? 'linear-gradient(to right,#2366be,#f0f0f0,#c3352f)' : 'linear-gradient(to right,#1eb26a,#f6d02c,#da3433)' }} />
        <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>{legendMin.toPrecision(4)}</span>{difference && <span>0</span>}<span>{extent.toPrecision(4)}</span></div>
      </div>
      {prepared.minimum < 0 && !difference && <p>检测到负值：顺序色标在 0 以下截色，悬停保留原值。</p>}
      <div style={{ display: 'grid', gridTemplateColumns: difference ? 'minmax(0,1fr)' : 'repeat(auto-fit,minmax(min(100%,360px),1fr))', gap: 16 }}>
        {difference ? pane(prepared.delta, `差值：${rightId} 减 ${leftId}`, leftContainer, 'delta')
          : [pane(prepared.left, `左：${leftId}`, leftContainer, 'left'), pane(prepared.right, `右：${rightId}`, rightContainer, 'right')]}
      </div>
      <p style={{ fontSize: 12 }}>仅 edge_idx 唯一且 geometry 类型和坐标完全相同才匹配。地图共享视角；截图仅包含画布，城市、版本、单位和图例由导出控件追加。容量可行结果不等同于严格容量约束用户均衡。</p>
    </>}
  </section>;
}


