import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, ConfigProvider, Select, theme } from 'antd';
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

// POST text/csv to /timetable/inspect (parse only), /timetable (persist).
// Query: mode=auto|amap|discrete|fixed, day_type=weekday|weekend|all.
export default function TimetableImportPanel({ city, onImported, onPreview, disabled = false, apiBase = '/api' }) {
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState('auto');
  const [dayType, setDayType] = useState('weekday');
  const [preview, setPreview] = useState(null);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const input = useRef(null);
  const scope = `${city?.id || ''}:${city?.custom_input_version || ''}`;
  const current = useRef(scope);
  current.current = scope;
  const request = useRef(0);
  useEffect(() => {
    request.current += 1;
    setFile(null); setPreview(null); setResult(null); setBusy(false); setError('');
    if (input.current) input.current.value = '';
  }, [scope]);
  const send = async save => {
    if (!city?.id || !file) return;
    if (file.size > 2_000_000) { setError('CSV不能超过2 MB'); return; }
    const own = scope;
    const id = ++request.current;
    const valid = () => current.current === own && request.current === id;
    setBusy(true); setError(''); setResult(null);
    try {
      const query = new URLSearchParams({ mode, day_type: dayType });
      const value = await fetch(`${apiBase}/cities/${encodeURIComponent(city.id)}/timetable${save ? '' : '/inspect'}?${query}`, {
        method: 'POST', headers: { 'Content-Type': 'text/csv; charset=utf-8' }, body: await file.text(),
      }).then(readJson);
      if (!valid()) return;
      if (save) { setResult(value); onImported?.(value); }
      else { setPreview(value); onPreview?.(value); }
    } catch (failure) { if (valid()) setError(failure.message); }
    finally { if (valid()) setBusy(false); }
  };
  const locked = busy || disabled;
  const reset = () => { setPreview(null); setResult(null); setError(''); };
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
      <h4 style={{ color: 'inherit' }}>导入运营时刻表</h4>
      <p>早高峰07:00-09:00、平峰09:00-17:00、晚高峰17:00-19:00独立计算，均含起点、不含终点。离散发车取时段内相邻差均值；不足两班保留待补。</p>
      <input ref={input} type="file" accept=".csv,text/csv" aria-label="选择时刻表CSV" disabled={locked || !city?.id}
        onChange={event => { setFile(event.target.files?.[0] || null); reset(); }} style={{ width: '100%' }} />
      <label style={{ display: 'block', marginTop: 8 }}>CSV格式
        <Select style={{ width: '100%', marginTop: 4 }} disabled={locked} value={mode} onChange={value => { setMode(value); reset(); }} options={[
          { value: 'auto', label: '自动识别字段' }, { value: 'amap', label: '原始高德timedesc_json' },
          { value: 'discrete', label: '离散发车时刻' }, { value: 'fixed', label: '固定间隔时刻表' },
        ]} />
      </label>
      <label style={{ display: 'block', marginTop: 8 }}>日期类型（用于高德规则选择）
        <Select style={{ width: '100%', marginTop: 4 }} value={dayType} disabled={locked} onChange={value => { setDayType(value); reset(); }} options={[
          { value: 'weekday', label: '工作日' }, { value: 'weekend', label: '周末' }, { value: 'all', label: '通用日' },
        ]} />
      </label>
      <details style={{ marginTop: 8 }}><summary>CSV字段约定</summary>
        <p>均需route_id，必须使用准备后的线路方向ID。</p>
        <p>高德：timedesc_json（原始JSON字符串）。</p>
        <p>离散：departure_time（一班一行），或departure_times（分号分隔，如07:00;07:20）。</p>
        <p>固定：start_time、end_time、interval_min，单位分钟；多段规则可分行。只有首末班、间隔范围或冲突规则时不猜测。</p>
        <p>旧频率CSV仍通过原导入入口；支持peak/flat，或am/pm/flat的_headway_min或_frequency_per_hour列。</p>
      </details>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 12 }}>
      <Button size="small" style={actionStyle} disabled={locked || !file || !city?.id} onClick={() => send(false)}>解析预览（不保存）</Button>
      <Button size="small" style={actionStyle} disabled={locked || !preview || !city?.network_prepared} onClick={() => send(true)}>保存频率输入</Button>
      </div>
      {!city?.network_prepared && <div style={{ color: '#ffca83' }}>保存前需先准备当前城市线网。</div>}
      {preview && <div role="status" style={{ marginTop: 12, padding: 10, border: '1px solid #52697e', background: '#162636', borderRadius: 6 }}>
        <b>{preview.summary.routes}条线路方向 · {preview.summary.pending_cells}个时段单元待补</b>
        <div style={{ color: '#b5c6d5', marginTop: 4 }}>待补指无法从当前数据计算间隔，不等于0班或停运；具体原因见各时段。保存时保留空值，计算时若使用默认间隔，将明确标记默认来源。</div>
        <div style={{ maxHeight: 240, overflow: 'auto', marginTop: 6 }}>
          {preview.rows.map(row => <div key={row.route_id} style={{ marginBottom: 8, overflowWrap: 'anywhere' }}>
            <b>{row.route_id}</b>
            {[['am', '早高峰'], ['flat', '平峰'], ['pm', '晚高峰']].map(([key, label]) => <div key={key}>
              {label}：{row[`${key}_headway_min`] ? `${Number(row[`${key}_headway_min`]).toFixed(2)}分钟（${row[`${key}_headway_source`]}）` : `待补：${row[`${key}_pending_reason`]}`}
            </div>)}
          </div>)}
        </div>
      </div>}
      {busy && <div role="status">正在处理本地上传文件…</div>}
      {error && <Alert style={{ marginTop: 8 }} type="error" message={error} />}
      {result && <Alert style={{ marginTop: 8 }} type="success" message={`已保存 ${result.upload_id}`} description={`早/平/晚已覆盖 ${result.am_headway_routes}/${result.flat_headway_routes}/${result.pm_headway_routes} 条可用线路；其余保留待补。`} />}
    </section>
    </ConfigProvider>
  );
}
