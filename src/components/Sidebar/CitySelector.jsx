import CustomInputsPanel from './CustomInputsPanel';
import React, { useEffect, useState, useRef } from 'react';
import { Alert, Button, Select, Spin } from 'antd';

const CitySelector = ({ city, onCityChange }) => {
  const [cities, setCities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [inputs, setInputs] = useState(null);
  const [job, setJob] = useState(null);
  const [taskError, setTaskError] = useState('');
  const [service, setService] = useState(null);
  const [uploading, setUploading] = useState(false);
  const current = useRef(city); current.current = city;
  const sameCity = own => current.current.id === own.id && current.current.custom_input_version === own.custom_input_version;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    fetch('/api/cities', { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('城市目录读取失败');
        return response.json();
      })
      .then(({ cities: catalog }) => {
        setCities(catalog);
        setError('');
        const selected = catalog.find(item => item.id === current.current.id);
        if (selected) onCityChange(selected);
      })
      .catch(e => { if (e.name !== 'AbortError') setError('本地数据服务未启动。启动后即可选择城市。'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    setInputs(null);
    setJob(null);
    setTaskError('');
    setService(null);
    fetch(`/api/cities/${encodeURIComponent(city.id)}/inputs`, { signal: controller.signal })
      .then(response => response.json())
      .then(data => {
        setInputs({ poi: data.poi, network: data.network });
        setService(data.service);
        setJob(data.job);
      })
      .catch(() => {});
    return () => controller.abort();
  }, [city.id, city.custom_input_version, revision]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const own = { ...current.current };
    const timer = setInterval(() => {
      fetch(`/api/jobs/${job.id}`)
        .then(response => response.json())
        .then(next => {
          if (!sameCity(own)) return;
          setJob(next);
          if (next.status === 'completed') { setInputs(next.result); onCityChange({ ...current.current, network_prepared: !!next.result.network, poi_prepared: !!next.result.poi, input_revision: (current.current.input_revision || 0) + 1 }); }
          if (next.status === 'failed') setTaskError(next.error || '数据处理失败');
        })
        .catch(() => setTaskError('任务状态读取失败，请重试'));
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]);

  const selected = cities.find(item => item.id === city.id) || city;
  const options = cities.map(item => ({
    value: item.id,
    label: item.province === item.name ? item.name : `${item.name} · ${item.province}`,
    search: `${item.name} ${item.province || ''} ${item.id}`.toLowerCase(),
  }));
  const preparing = job && ['queued', 'running'].includes(job.status);

  const prepareInputs = () => {
    const own = { ...current.current };
    setTaskError('');
    fetch(`/api/cities/${encodeURIComponent(city.id)}/prepare-inputs`, { method: 'POST' })
      .then(async response => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '无法开始处理');
        return result;
      })
      .then(value => { if (sameCity(own)) setJob(value); })
      .catch(e => { if (sameCity(own)) setTaskError(e.message); });
  };

  const uploadHeadways = async event => {
    const file = event.target.files?.[0];
    if (!file) return;
    const own = { ...current.current };
    setUploading(true);
    setTaskError('');
    try {
      const response = await fetch(`/api/cities/${encodeURIComponent(city.id)}/headways`, {
        method: 'POST', headers: { 'Content-Type': 'text/csv; charset=utf-8' }, body: await file.text(),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '发车间隔导入失败');
      if (sameCity(own)) setService(result);
    } catch (error) {
      if (sameCity(own)) setTaskError(error.message);
    } finally {
      if (sameCity(own)) setUploading(false);
      event.target.value = '';
    }
  };

  return (
    <div style={{ marginBottom: 18 }}>
      <CustomInputsPanel city={city} onImported={result => { onCityChange(result.city); setRevision(previous => previous + 1); }} />
      <div style={{ color: '#e3edf4', fontSize: 14, fontWeight: 700, marginBottom: 8 }}>选择城市</div>
      <Select
        showSearch
        value={selected.id}
        options={options}
        filterOption={(input, option) => option.search.includes(input.toLowerCase())}
        onChange={id => onCityChange(cities.find(item => item.id === id))}
        placeholder="搜索城市名或英文名"
        disabled={loading || !!error}
        style={{ width: '100%' }}
      />
      {loading && <div style={{ marginTop: 8, color: '#a9b9c8' }}><Spin size="small" /> 正在读取本地城市目录</div>}
      {error && <Alert style={{ marginTop: 10 }} type="warning" message={error} action={<Button size="small" onClick={() => setRevision(value => value + 1)}>重试</Button>} />}
      {!error && !loading && (
        <div style={{ marginTop: 10, padding: 10, borderRadius: 8, background: 'rgba(255,255,255,0.06)', color: '#c8d7e4', fontSize: 12, lineHeight: 1.8 }}>
          <div>线路记录：{selected.route_count?.toLocaleString() ?? '待检查'} 条</div>
          <div>线网文件：{selected.network_complete ? '齐全，拓扑待校验' : '不完整'}</div>
          <div>省级 POI：{selected.poi_available ? selected.poi_file : '未找到对应省份数据'}</div>
          {inputs?.network && <div style={{ color: '#80d7a5' }}>线网已准备：{inputs.network.merged_stations.toLocaleString()} 站，{inputs.network.topology_edges.toLocaleString()} 条拓扑边，路段映射率 {(inputs.network.mapped_segment_share * 100).toFixed(1)}%</div>}
          {inputs?.network && <div>线路可沿拓扑走通：{inputs.network.route_directions_ready.toLocaleString()} / {inputs.network.route_directions.toLocaleString()} 个方向；{inputs.network.route_directions_missing_edge.toLocaleString()} 个方向缺少物理边。</div>}
          {inputs?.network && <div>当前线网没有逐线路发车间隔。<a href={`/api/cities/${encodeURIComponent(city.id)}/headway-template`} download style={{ color: '#80d7a5' }}>下载发车间隔模板</a>，按分钟填写高峰和平峰间隔；也支持peak_frequency_per_hour、flat_frequency_per_hour（班次/小时）。缺失线路使用可设置的默认值。</div>}
          {inputs?.network && <div style={{ marginTop: 6 }}>
            <label style={{ cursor: 'pointer', color: '#80d7a5' }}>
              {uploading ? '正在导入发车间隔…' : '导入已填写的发车间隔 CSV'}
              <input type="file" accept=".csv,text/csv" onChange={uploadHeadways} disabled={uploading} style={{ display: 'none' }} />
            </label>
          </div>}
          {service && <div>已提供发车间隔：高峰 {service.peak_headway_routes.toLocaleString()} / {service.topology_ready_routes.toLocaleString()}，平峰 {service.flat_headway_routes.toLocaleString()} / {service.topology_ready_routes.toLocaleString()} 个可用线路方向。{!service.all_ready_routes_covered && '缺失线路将使用可设置的默认间隔，结果会记录补全比例。'}</div>}
          {inputs?.poi && <div style={{ color: '#80d7a5' }}>本市 POI 已准备：{inputs.poi.poi_count.toLocaleString()} 个</div>}
          {inputs?.poi?.scanned_rows === 1048575 && <div style={{ color: '#ffca83' }}>省级源文件恰有 1,048,575 条记录，可能受 Excel 行数上限截断；请核实数据完整性。</div>}
          {preparing && <div>正在处理：{job.phase || '等待开始'}；已扫描 {job.scanned_rows.toLocaleString()} 条 POI</div>}
          {taskError && <div style={{ color: '#ffad99' }}>{taskError}</div>}
          {selected.poi_available && selected.network_complete && !(inputs?.network && inputs?.poi) && (
            <Button size="small" loading={preparing} onClick={prepareInputs} style={{ marginTop: 7 }}>
              {preparing ? '正在准备城市数据' : '准备该城市数据'}
            </Button>
          )}
          <div style={{ marginTop: 7, color: selected.has_evaluation ? '#80d7a5' : '#ffca83' }}>
            {selected.has_evaluation ? '北京现有图层为历史预计算结果，与此处新数据准备不是同一次实验' : '暂无原系统的历史评价图层；请到“客流分配”查看或计算本市公交客流'}
          </div>
          {!selected.has_evaluation && <div style={{ color: '#9babb8' }}>输入准备不等于完成公交分配；仍需核验线路发车频率。{city.poi_format === 'custom_standard_v1' ? '自有数据按上传时声明的坐标系标准化为WGS84，不重复转换。' : '全国POI按原始高德GCJ-02转换为WGS84。'}</div>}
        </div>
      )}
    </div>
  );
};

export default CitySelector;
