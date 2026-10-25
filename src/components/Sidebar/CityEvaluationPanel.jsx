import React, { useEffect, useState } from 'react';
import { Alert, Button } from 'antd';

const CityEvaluationPanel = ({ city, metric, onMetricChange }) => {
  const [result, setResult] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setJob(null);
    setError('');
    fetch(`/api/cities/${encodeURIComponent(city.id)}/evaluations`, { signal: controller.signal })
      .then(response => response.json())
      .then(data => {
        setResult(data.result);
        setJob(data.job);
      })
      .catch(e => { if (e.name !== 'AbortError') setError('城市评价状态读取失败'); });
    return () => controller.abort();
  }, [city.id]);

  useEffect(() => {
    if (!job || !['queued', 'running'].includes(job.status)) return undefined;
    const timer = setInterval(() => {
      fetch(`/api/jobs/${job.id}`)
        .then(response => response.json())
        .then(next => {
          setJob(next);
          if (next.status === 'completed') {
            setResult(next.result);
            onMetricChange('density');
          }
          if (next.status === 'failed') setError(next.error || '城市评价失败');
        })
        .catch(() => setError('任务状态读取失败'));
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.id, job?.status, onMetricChange]);

  const start = async () => {
    setError('');
    try {
      const response = await fetch(`/api/cities/${encodeURIComponent(city.id)}/evaluations`, { method: 'POST' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '无法启动评价');
      setJob(payload);
    } catch (e) {
      setError(e.message);
    }
  };
  const running = job && ['queued', 'running'].includes(job.status);
  const control = active => ({
    border: active ? '1px solid #80d7a5' : '1px solid #657889',
    background: active ? '#183c38' : 'transparent',
    color: active ? '#a3f0c1' : '#c8d7e4',
    borderRadius: 6, padding: '6px 8px', cursor: 'pointer',
  });

  return <div className="sidebar-content-wrapper" style={{ padding: 14, color: '#c8d7e4', fontSize: 12, lineHeight: 1.7 }}>
    <div style={{ fontSize: 15, fontWeight: 700, color: '#e3edf4' }}>{city.name} · 城市指标</div>
    <p>使用本市已准备的 POI 和公交站点，在有 POI 的 1 公里网格上计算。无需发车间隔。</p>
    <Button block loading={running} onClick={start}>{running ? '正在计算' : result ? '检查或更新结果' : '计算 POI 分布与公交覆盖度'}</Button>
    {running && <div>当前步骤：{job.phase || '等待开始'}</div>}
    {error && <Alert style={{ marginTop: 8 }} type="error" message={error} />}
    {result && <>
      <div style={{ marginTop: 12 }}>POI：{result.poi_count.toLocaleString()} 个；有 POI 网格：{result.occupied_grid_count.toLocaleString()} 个。</div>
      <div>最近公交站不超过 800 米的有 POI 网格占比：{(result.walk_score_positive_share * 100).toFixed(1)}%。</div>
      <div>平均覆盖度得分：{result.mean_coverage_score.toFixed(1)} / 100。</div>
      <div style={{ display: 'flex', gap: 6, marginTop: 10, flexWrap: 'wrap' }}>
        <button type="button" style={control(metric === 'density')} onClick={() => onMetricChange('density')}>POI 密度</button>
        <button type="button" style={control(metric === 'coverage')} onClick={() => onMetricChange('coverage')}>公交覆盖度</button>
        <button type="button" style={control(!metric)} onClick={() => onMetricChange(null)}>关闭指标</button>
      </div>
      <a style={{ color: '#80d7a5', display: 'block', marginTop: 9 }}
        href={`/api/cities/${encodeURIComponent(city.id)}/evaluations/grid`} download>下载网格 GeoJSON</a>
    </>}
    <div style={{ color: '#ffca83', marginTop: 12 }}>
      POI 密度是原始点数，不是经人口校准的出行需求；覆盖度沿用原北京脚本的步行距离与线路多样性公式，仅评价有 POI 的网格，不代表全市面积覆盖率。
    </div>
    <div style={{ color: '#9babb8', marginTop: 8 }}>
      等时圈、公交/小汽车竞争力和综合评分需要额外数据，目前仅北京有历史结果，其他城市不显示这些指标。
    </div>
  </div>;
};

export default CityEvaluationPanel;
