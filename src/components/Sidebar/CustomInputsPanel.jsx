import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Select } from 'antd';
import { readJson } from '../../utils/localApi';

// API receives raw ZIP bytes, filename and an explicit coordinate_system query.
const CustomInputsPanel = ({ city, onImported }) => {
  const [file, setFile] = useState(null);
  const [coordinates, setCoordinates] = useState('');
  const [epsg, setEpsg] = useState('');
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [versions, setVersions] = useState([]);
  const [selectedVersion, setSelectedVersion] = useState('builtin');
  const [switching, setSwitching] = useState(false);
  const input = useRef(null);
  const selectedCity = useRef(city?.id);
  selectedCity.current = city?.id;

  useEffect(() => {
    setFile(null);
    setResult(null);
    setError('');
    if (input.current) input.current.value = '';
  }, [city?.id]);

  useEffect(() => {
    const controller = new AbortController(); setVersions([]);
    setSelectedVersion(city?.custom_input_version || 'builtin');
    if (city?.id) fetch(`/api/cities/${city.id}/custom-inputs`, { signal: controller.signal }).then(readJson).then(value => setVersions(value.versions)).catch(error => { if (error.name !== 'AbortError') setError(error.message); });
    return () => controller.abort();
  }, [city?.id, city?.custom_input_version]);
  const switchVersion = async () => {
    const own = city.id; setSwitching(true); setError('');
    try {
      const result = await fetch(`/api/cities/${own}/custom-inputs/activate`, { method: 'POST', headers: { 'Content-Type':'application/json' }, body: JSON.stringify({ version: selectedVersion === 'builtin' ? null : selectedVersion }) }).then(readJson);
      if (selectedCity.current !== own) return;
      onImported?.(result); setResult(null); setFile(null); if (input.current) input.current.value = '';
    } catch (error) { if (selectedCity.current === own) setError(error.message); }
    finally { setSwitching(false); }
  };
  const upload = async () => {
    const cityId = city?.id;
    if (!cityId || !file || !coordinates) return;
    const declaration = coordinates === 'projected' ? epsg.trim() : coordinates;
    if (coordinates === 'projected' && !/^EPSG:\d+$/i.test(declaration)) {
      setError('请填写投影坐标系代码，例如 EPSG:32650；不能仅填写“投影坐标”。');
      return;
    }
    if (file.size > 100 * 1024 * 1024) {
      setError('数据包超过 100 MiB，请缩小后上传。');
      return;
    }
    setUploading(true);
    setError('');
    setResult(null);
    try {
      const query = new URLSearchParams({ filename: file.name, coordinate_system: declaration });
      const response = await fetch(`/api/cities/${encodeURIComponent(cityId)}/custom-inputs?${query}`, {
        method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file,
      });
      const imported = await response.json();
      if (!response.ok) throw new Error(imported.error || '自有数据导入失败');
      if (selectedCity.current !== cityId) return;
      setResult(imported);
      if (onImported) onImported(imported);
    } catch (failure) {
      if (selectedCity.current === cityId) setError(failure.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div style={{ marginBottom: 16, padding: 10, borderRadius: 8, background: 'rgba(255,255,255,0.06)', color: '#c8d7e4', fontSize: 12, lineHeight: 1.8 }}>
      <div style={{ color: '#e3edf4', fontWeight: 700, fontSize: 14 }}>导入自有计算数据</div>
      <div>当前城市：{city?.name || '请先从城市目录选择'}</div>
      <div>ZIP 内同时包含站点、路段、线路与 POI。支持 CPTOND Shapefile 或标准 GeoJSON + UTF-8 POI CSV；导入后需准备数据才能计算，并非仅添加展示图层。</div>
      {city?.id && <a href={`/api/cities/${encodeURIComponent(city.id)}/custom-input-template`} download style={{ color: '#80d7a5' }}>下载标准数据包模板（含必需字段与非研究数据示例）</a>}
      <div style={{ marginTop: 10 }}>
        <label>当前计算数据源<Select aria-label="城市计算数据版本" style={{ width:'100%', marginTop: 4 }} value={selectedVersion} disabled={uploading || switching}
          onChange={setSelectedVersion} options={[{value:'builtin',label:'原始全国数据集'},...versions.map(row=>({value:row.version,disabled:row.status!=='completed',label:`${row.version} · ${row.counts?.poi ?? '?'} POI · ${row.status==='completed'?'已保留':'导入失败'}`}))]} /></label>
        <Button size="small" disabled={uploading || switching || selectedVersion === (city?.custom_input_version || 'builtin')} loading={switching} onClick={switchVersion} style={{marginTop:6}}>切换计算数据源</Button>
        <div style={{color:'#ffca83',fontSize:11}}>切换后需核对并重新准备数据。旧需求与结果不会删除，但不会与新输入混用。</div>
      </div>
      <label htmlFor="custom-input-package" style={{ display: 'block', marginTop: 8 }}>数据包（ZIP，最大 100 MiB）</label>
      <input ref={input} id="custom-input-package" type="file" accept=".zip,application/zip" disabled={uploading || !city?.id}
        onChange={event => { setFile(event.target.files?.[0] || null); setError(''); setResult(null); }} style={{ width: '100%' }} />
      <label htmlFor="custom-input-coordinates" style={{ display: 'block', marginTop: 8 }}>包内原始坐标系统（必须明确选择）</label>
      <Select id="custom-input-coordinates" value={coordinates || undefined} placeholder="请选择真实坐标系统" disabled={uploading} style={{ width: '100%' }}
        onChange={setCoordinates} options={[
          { value: 'WGS84', label: 'WGS84 经纬度' },
          { value: 'GCJ02', label: 'GCJ-02 高德经纬度' },
          { value: 'projected', label: '投影 CRS（填写 EPSG）' },
        ]} />
      {coordinates === 'projected' && <><label htmlFor="custom-input-epsg" style={{ display: 'block', marginTop: 8 }}>投影坐标系代码</label>
        <Input id="custom-input-epsg" value={epsg} onChange={event => setEpsg(event.target.value)} placeholder="例如 EPSG:32650" disabled={uploading} /></>}
      <div style={{ marginTop: 6, color: '#9babb8' }}>此处选择适用于整个包；不同图层使用不同坐标系统时，请先统一，或通过 API 分图层声明。投影 CSV 的 lon/lat 分别填写 X/Y。WGS84 不会再次执行高德偏移转换。</div>
      <Button size="small" style={{ marginTop: 8 }} loading={uploading} disabled={!city?.id || !file || !coordinates || (coordinates === 'projected' && !epsg.trim())} onClick={upload}>
        {uploading ? '正在标准化数据' : '导入为新的计算版本'}
      </Button>
      {error && <Alert type="error" message={error} style={{ marginTop: 8 }} />}
      {result && <Alert type="success" style={{ marginTop: 8 }} message={`已导入版本 ${result.version}`} description={`站点记录 ${result.counts?.stops ?? '-'}，路段 ${result.counts?.segments ?? '-'}，线路方向 ${result.counts?.routes ?? '-'}，POI ${result.counts?.poi ?? '-'}。原始数据已保留，未覆盖全国源文件；请准备该城市数据后再计算。`} />}
      {result?.warnings?.map((warning, index) => <Alert key={index} type="warning" message={warning} style={{ marginTop: 8 }} />)}
    </div>
  );
};

export default CustomInputsPanel;
