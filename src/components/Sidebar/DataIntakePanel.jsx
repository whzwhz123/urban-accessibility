import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, ConfigProvider, Input, Select, theme } from 'antd';
import { readJson } from '../../utils/localApi';

const panelTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: '#80d7a5', colorBgContainer: '#162636', colorBgElevated: '#1c3042',
    colorBgContainerDisabled: '#203044', colorBorder: '#52697e', colorText: '#e3edf4',
    colorTextSecondary: '#b5c6d5', colorTextPlaceholder: '#a2b5c6', colorTextDisabled: '#92a6b8',
    fontSize: 12, borderRadius: 6,
  },
};
const actionStyle = { maxWidth: '100%', minHeight: 28, height: 'auto', whiteSpace: 'normal', padding: '4px 10px' };

// POST raw ZIP to /custom-inputs/inspect or /custom-inputs.
// Query JSON: field_mapping={layer:{canonical:source}}, category_mapping={source:target}.
export default function DataIntakePanel({ city, onImported, onInspected, disabled = false, apiBase = '/api' }) {
  const [file, setFile] = useState(null);
  const [coordinateSystem, setCoordinateSystem] = useState('');
  const [inspection, setInspection] = useState(null);
  const [fieldMapping, setFieldMapping] = useState({});
  const [categoryMapping, setCategoryMapping] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const input = useRef(null);
  const scope = `${city?.id || ''}:${city?.custom_input_version || ''}`;
  const current = useRef(scope);
  current.current = scope;
  const request = useRef(0);
  useEffect(() => {
    request.current += 1;
    setFile(null); setInspection(null); setFieldMapping({}); setCategoryMapping({});
    setResult(null); setError(''); setBusy(false);
    if (input.current) input.current.value = '';
  }, [scope]);

  const changeFile = event => {
    setFile(event.target.files?.[0] || null);
    setInspection(null); setFieldMapping({}); setCategoryMapping({}); setResult(null); setError('');
  };
  const send = async (action, initial = false) => {
    if (!file || !city?.id) return;
    if (file.size > 100 * 1024 * 1024) { setError('ZIP不能超过100 MiB'); return; }
    const own = scope;
    const id = ++request.current;
    const valid = () => current.current === own && request.current === id;
    setBusy(true); setError(''); setResult(null);
    try {
      const query = new URLSearchParams({ filename: file.name });
      if (coordinateSystem.trim()) query.set('coordinate_system', coordinateSystem.trim());
      if (!initial) {
        query.set('field_mapping', JSON.stringify(fieldMapping));
        query.set('category_mapping', JSON.stringify(categoryMapping));
      }
      const suffix = action === 'import' ? '' : '/inspect';
      const value = await fetch(`${apiBase}/cities/${encodeURIComponent(city.id)}/custom-inputs${suffix}?${query}`, {
        method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file,
      }).then(readJson);
      if (!valid()) return;
      if (action === 'import') { setResult(value); onImported?.(value); }
      else {
        setInspection(value);
        if (initial) {
          const defaults = {};
          Object.entries(value.layers).forEach(([kind, layer]) => {
            defaults[kind] = Object.fromEntries(layer.fields.filter(field => [
              ...layer.canonical.required, ...(layer.canonical.optional || []), ...(layer.canonical.alternative || []),
            ].includes(field)).map(field => [field, field]));
          });
          setFieldMapping(defaults);
        }
        onInspected?.(value);
      }
    } catch (failure) { if (valid()) setError(failure.message); }
    finally { if (valid()) setBusy(false); }
  };
  const locked = disabled || busy;
  const displayCount = value => value == null ? '未评估' : value;
  const quality = result?.quality || inspection?.quality;
  return (
    <ConfigProvider theme={panelTheme}>
    <section className="intake-dark-panel" style={{ padding: 12, marginBottom: 12, color: '#c8d7e4', background: 'rgba(255,255,255,0.06)', border: '1px solid #ffffff18', borderRadius: 8, fontSize: 12, lineHeight: 1.8, overflowWrap: 'anywhere', minWidth: 0 }}>
      <style>{`
        .intake-dark-panel input[type="file"] { box-sizing: border-box; padding: 6px; border: 1px solid #52697e; border-radius: 6px; background: #162636; color: #e3edf4; font: inherit; }
        .intake-dark-panel input[type="file"]::file-selector-button { margin-right: 8px; padding: 4px 8px; border: 1px solid #52697e; border-radius: 4px; background: #243c50; color: #e3edf4; cursor: pointer; font: inherit; }
        .intake-dark-panel input[type="file"]:disabled, .intake-dark-panel input[type="file"]:disabled::file-selector-button { background: #203044; color: #92a6b8; cursor: not-allowed; }
        .intake-dark-panel input[type="file"]:focus-visible { outline: 2px solid #80d7a5; outline-offset: 2px; }
        .intake-dark-panel summary { color: #b5dfcc; cursor: pointer; }
        .intake-dark-panel .ant-alert { overflow-wrap: anywhere; }
      `}</style>
      <h4 style={{ color: 'inherit' }}>字段映射导入向导</h4>
      <p>先探测ZIP中的站点、路段、线路和POI字段，再映射到模型已有字段。不会猜测坐标系或修改模型。</p>
      <input ref={input} aria-label="选择数据包" type="file" accept=".zip" disabled={locked || !city?.id} onChange={changeFile} style={{ width: '100%' }} />
      <label style={{ display: 'block', marginTop: 8 }}>原始坐标系（可先留空探测）
        <Input style={{ marginTop: 4 }} value={coordinateSystem} disabled={locked} placeholder="WGS84 / GCJ02 / EPSG:32650" onChange={event => {
          setCoordinateSystem(event.target.value); setInspection(null); setResult(null);
        }} />
      </label>
      <Button size="small" style={{ ...actionStyle, marginTop: 10 }} disabled={locked || !file || !city?.id} onClick={() => send('inspect', true)}>1. 探测字段</Button>
      {inspection && Object.entries(inspection.layers).map(([kind, layer]) => (
        <div key={kind} style={{ marginTop: 10 }}>
          <b>{kind} · {layer.rows}条</b>
          <div style={{ overflowWrap: 'anywhere', color: '#b5c6d5' }}>{layer.file}</div>
          <details><summary>查看字段样例</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 180, overflow: 'auto' }}>{JSON.stringify(layer.samples, null, 2)}</pre></details>
          {[...new Set([...layer.canonical.required, ...(layer.canonical.optional || []), ...(layer.canonical.alternative || [])])].map(target => (
            <label key={target} style={{ display: 'block', marginTop: 8 }}>
              {target}{layer.canonical.required.includes(target) ? ' *' : ''}
              <Select size="small" allowClear showSearch style={{ width: '100%', marginTop: 4 }} disabled={locked}
                placeholder="选择源字段；不映射则保留原字段" value={fieldMapping[kind]?.[target] || undefined}
                options={layer.fields.map(field => ({ value: field, label: field }))}
                onChange={source => {
                  setFieldMapping(previous => ({ ...previous, [kind]: { ...previous[kind], [target]: source || '' } }));
                  setInspection(previous => ({ ...previous, quality: null })); setResult(null);
                }} />
            </label>
          ))}
          <div style={{ color: '#b5c6d5', marginTop: 4 }}>{layer.canonical.note}</div>
        </div>
      ))}
      {inspection && <>
        <p>高德POI可使用type/location/cityname替代class_1/lon/lat；星号字段也可通过该替代格式生成。</p>
        <Button size="small" style={actionStyle} disabled={locked} onClick={() => send('inspect')}>2. 应用映射并刷新质量摘要</Button>
        <div style={{ marginTop: 10 }}><b>POI类别映射</b><div>留空保留原类别，不自动推断。</div></div>
        {(inspection.source_poi_categories || inspection.poi_categories || []).map(category => (
          <label key={category.value} style={{ display: 'block', marginTop: 8, overflowWrap: 'anywhere' }}>{category.value}（{category.count}条）
            <Input size="small" style={{ marginTop: 4 }} value={categoryMapping[category.value] || ''} disabled={locked} placeholder="目标class_1类别"
              onChange={event => {
                const value = event.target.value;
                setCategoryMapping(previous => {
                  const next = { ...previous };
                  if (value.trim()) next[category.value] = value.trim(); else delete next[category.value];
                  return next;
                });
                setInspection(previous => ({ ...previous, quality: null })); setResult(null);
              }} />
          </label>
        ))}
        <Button size="small" style={{ ...actionStyle, marginTop: 12 }} disabled={locked || !coordinateSystem.trim() || !quality} loading={busy}
          onClick={() => send('import')}>3. 导入为新的计算版本</Button>
      </>}
      {quality && <div role="status" style={{ marginTop: 12, padding: 10, border: '1px solid #52697e', background: '#162636', borderRadius: 6 }}>
        <b style={{ color: '#e3edf4' }}>数据质量摘要（映射后全量记录）</b>
        <div style={{ color: '#b5c6d5', marginTop: 4 }}>0表示本项检查未发现问题；未评估表示缺少字段或坐标声明，不能视为0。缺字段记录包含缺少必填字段或必填值为空的记录，每条只计一次；重复数按各层业务键统计多余记录。</div>
        {Object.entries(quality.layers).map(([kind, value]) => <div key={kind} style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid #ffffff18' }}>
          {kind}：无效坐标 {displayCount(value.invalid_coordinates)}；缺字段记录 {value.missing_field_rows}；重复 {displayCount(value.duplicate_rows)}
          {value.missing_fields.length > 0 && <div>缺失字段：{value.missing_fields.join('、')}</div>}
          {value.invalid_geometries != null && <div>无效几何 {value.invalid_geometries}</div>}
        </div>)}
        <div>断裂诊断项 {displayCount(quality.broken_segments)}：悬空/自环 {displayCount(quality.dangling_segments)}；缺失邻接路段 {displayCount(quality.missing_adjacent_segments)}；不连通多段线 {displayCount(quality.disconnected_multilines)}</div>
        {(quality.notes || []).map(note => <div key={note} style={{ color: '#b5c6d5' }}>{note}</div>)}
      </div>}
      {error && <Alert style={{ marginTop: 8 }} type="error" message={error} />}
      {result && <Alert style={{ marginTop: 8 }} type="success" message={`已导入版本 ${result.version}，请准备城市数据后计算`} />}
    </section>
    </ConfigProvider>
  );
}
