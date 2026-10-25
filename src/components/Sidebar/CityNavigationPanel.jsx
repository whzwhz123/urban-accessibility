import React, { useEffect, useState } from 'react';
import { Alert, Button, InputNumber } from 'antd';

const CityNavigationPanel = ({ city }) => {
  const [pairs, setPairs] = useState(10);
  const [preview, setPreview] = useState(null);
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setPreview(null);
    setResult(null);
    setError('');
    fetch(`/api/cities/${encodeURIComponent(city.id)}/navigation/preview?pairs=${pairs}`, { signal: controller.signal })
      .then(async response => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '无法生成抽样预览');
        setPreview(data.preview);
        setKeyConfigured(data.key_configured);
        setJob(data.job);
      })
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [city.id, pairs]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/cities/${encodeURIComponent(city.id)}/navigation/latest`, { signal: controller.signal })
      .then(response => response.json()).then(data => setResult(data.result))
      .catch(() => {});
    return () => controller.abort();
  }, [city.id]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const timer = setInterval(() => {
      fetch(`/api/jobs/${job.id}`)
        .then(response => response.json())
        .then(next => {
          setJob(next);
          if (next.status === 'completed') setResult(next.result);
          if (next.status === 'failed') setError(next.error || '采样失败');
        })
        .catch(() => setError('任务状态读取失败'));
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]);

  const start = async () => {
    setError('');
    setResult(null);
    try {
      const response = await fetch(`/api/cities/${encodeURIComponent(city.id)}/navigation/samples`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pairs }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '无法启动采样');
      setJob(data);
    } catch (e) { setError(e.message); }
  };
  const running = job && ['queued', 'running'].includes(job.status);

  return <div className="sidebar-content-wrapper" style={{ padding: 14, color: '#c8d7e4', fontSize: 12, lineHeight: 1.7 }}>
    <div style={{ fontSize: 15, fontWeight: 700, color: '#e3edf4' }}>{city.name} · 导航抽样</div>
    <p>先预览 1–20 公里的网格 OD，再决定是否调用高德。每对 OD 查询常规公交与驾车各一次。</p>
    <label>抽样 OD 对数：<InputNumber min={1} max={20} value={pairs} onChange={value => setPairs(value || 1)} /></label>
    {preview && <>
      <div style={{ marginTop: 10 }}>符合条件：{preview.pair_count} 对；预计调用：{preview.planned_api_calls} 次。</div>
      <div style={{ color: '#9babb8' }}>{preview.sampling}</div>
      <div style={{ maxHeight: 118, overflow: 'auto', marginTop: 8 }}>
        {preview.pairs.slice(0, 5).map(pair =>
          <div key={pair.id}>{pair.id} · {(pair.distance_m / 1000).toFixed(1)} km</div>)}
      </div>
      <Button block style={{ marginTop: 10 }} disabled={!keyConfigured || running || !preview.pair_count}
        loading={running} onClick={start}>{running ? '正在采集' : '开始即时导航采样'}</Button>
    </>}
    {!keyConfigured && <Alert style={{ marginTop: 8 }} type="info" message="未配置密钥：在项目根目录的 .env 中设置 AMAP_API_KEY 并重启服务；目前不会调用高德。" />}
    {running && <div>当前步骤：{job.phase}</div>}
    {error && <Alert style={{ marginTop: 8 }} type="error" message={error} />}
    {result && <div style={{ marginTop: 10 }}>
      <div>最近采样 {result.run_id}：完成配对 {result.paired_success_count} / {result.requested_pairs} 对。</div>
      {result.paired_results.slice(0, 5).map(row =>
        <div key={row.pair_id}>{row.pair_id}：公交 {row.transit_min} 分钟 / 驾车 {row.driving_min} 分钟</div>)}
      <div>请求状态：{Object.entries(result.status_counts || {}).map(([status, count]) => `${status} ${count}`).join('；')}</div>
      <div style={{ color: '#ffca83' }}>{result.comparability}</div>
    </div>}
    <div style={{ color: '#ffca83', marginTop: 12 }}>
      公交接口采用“不乘地铁”策略；驾车为调用时刻的路线规划。此抽样不是全市竞争力指标，不代表历史早晚高峰，也不进入客流分配模型。
    </div>
  </div>;
};

export default CityNavigationPanel;
