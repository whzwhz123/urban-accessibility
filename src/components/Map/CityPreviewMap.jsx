import {ImageExportControls} from '../Sidebar/ResultExports';
import {composeMapCanvas as captureMap} from '../../utils/mapExport';
import React, { useEffect, useMemo, useState, useRef } from 'react';
import DeckGL from '@deck.gl/react';
import { GeoJsonLayer } from '@deck.gl/layers';
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

const BASEMAP = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
};

const numeric=value=>value!=null && value!=='' && Number.isFinite(Number(value))?Number(value):null;
const textNumber=(value,digits=1)=>numeric(value)===null?'未记录':Number(value).toFixed(digits);

const cityZoom = city => {
  if (!city.bounds) return 10;
  const [west,south,east,north]=city.bounds;
  const width=Math.max(280,window.innerWidth-400),height=Math.max(280,window.innerHeight-180);
  const mercator=lat=>Math.log(Math.tan(Math.PI/4+Math.max(-85,Math.min(85,lat))*Math.PI/360));
  const zx=Math.log2(width*360/(512*Math.max(east-west,.001)));
  const zy=Math.log2(height*2*Math.PI/(512*Math.max(mercator(north)-mercator(south),.00001)));
  return Math.max(4,Math.min(14,zx,zy));
};

const ramp = (value, maximum) => {
  if (value <= 0 || maximum <= 0) return [160, 175, 176, 110];
  const t = Math.min(value / maximum, 1);
  const first = t < 0.5 ? [30, 178, 106] : [246, 208, 44];
  const last = t < 0.5 ? [246, 208, 44] : [218, 52, 51];
  const blend = t < 0.5 ? t * 2 : (t - 0.5) * 2;
  return first.map((channel, index) => Math.round(channel + (last[index] - channel) * blend)).concat(230);
};

const CityPreviewMap = ({ city, data, run, evaluationMetric }) => {
  const captureRoot=useRef(null);
  const [preview, setPreview] = useState(null);
  const [flow, setFlow] = useState(null);
  const [evaluation, setEvaluation] = useState(null);
  const [error, setError] = useState('');
  const [hover, setHover] = useState(null);
  const [showRoutes, setShowRoutes] = useState(true);
  const [showStops, setShowStops] = useState(true);
  const [variant, setVariant] = useState('served');
  const [metric, setMetric] = useState('flow');
  const [commonScale, setCommonScale] = useState(null);
  const [opacity, setOpacity] = useState(1);
  const [selectedSegment, setSelectedSegment] = useState(null);
  const [viewState, setViewState] = useState({ longitude: city.center[0], latitude: city.center[1], zoom: cityZoom(city) });

  useEffect(() => {
    const controller = new AbortController();
    setPreview(null);
    setError('');
    setHover(null);
    setViewState({ longitude: city.center[0], latitude: city.center[1], zoom: cityZoom(city) });
    fetch(`/api/cities/${encodeURIComponent(city.id)}/preview`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('线网预览读取失败');
        return response.json();
      })
      .then(setPreview)
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [city.id, city.custom_input_version]);

  useEffect(() => {
    const controller = new AbortController();
    setFlow(null);
    setHover(null);
    setError('');
    setSelectedSegment(null);
    if (!run?.run_id) { setShowRoutes(true); setShowStops(true); return () => controller.abort(); }
    setShowRoutes(false); setShowStops(false);
    fetch(`/api/cities/${encodeURIComponent(city.id)}/runs/${encodeURIComponent(run.run_id)}/flow`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('路段客流读取失败');
        return response.json();
      })
      .then(setFlow)
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [city.id, run?.run_id]);

  useEffect(() => {
    const controller = new AbortController();
    setEvaluation(null);
    if (!evaluationMetric) return () => controller.abort();
    fetch(`/api/cities/${encodeURIComponent(city.id)}/evaluations/grid`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('城市指标网格读取失败');
        return response.json();
      })
      .then(setEvaluation)
      .catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [city.id, evaluationMetric]);

  useEffect(() => {
    const controller = new AbortController(); setCommonScale(null);
    if (run) fetch(`/api/cities/${city.id}/runs/${run.run_id}/flow-scale`, { signal: controller.signal }).then(response => response.json()).then(setCommonScale).catch(() => {});
    return () => controller.abort();
  }, [city.id, run?.run_id]);

  const field = `${variant}_${metric === 'flow' ? 'flow_pax_per_hour' : 'vc'}`;
  const maximum = useMemo(() => commonScale?.maxima?.[field] ?? (flow?.features?.reduce(
    (largest, feature) => Math.max(largest, Number(feature.properties[field]) || 0), 0,
  ) || 0), [flow, field, commonScale]);

  const layers = useMemo(() => {
    const result = [];
    if (preview && showRoutes) result.push(new GeoJsonLayer({
      id: 'city-preview-routes',
      data: { type: 'FeatureCollection', features: preview.features.filter(f => f.properties.kind === 'routes') },
      pickable: true, stroked: true, filled: false,
      getLineColor: [15, 125, 111, 140], getLineWidth: 2, lineWidthMinPixels: 1,
      onHover: setHover,
    }));
    if (evaluation && evaluationMetric) result.push(new GeoJsonLayer({
      id: 'city-evaluation-grid', data: evaluation, pickable: true, stroked: true, filled: true,
      getFillColor: feature => ramp(Number(feature.properties[
        evaluationMetric === 'density' ? 'poi_density_norm' : 'coverage_score'
      ]) || 0, 100),
      getLineColor: [60, 75, 75, 55], getLineWidth: 1, lineWidthMinPixels: 0.3,
      onHover: setHover,
      updateTriggers: { getFillColor: [evaluationMetric] },
    }));
    if (flow) result.push(new GeoJsonLayer({
      id: 'assignment-flow', data: flow, pickable: true, stroked: true, filled: false, opacity,
      autoHighlight: true, highlightColor: [255, 214, 80, 180],
      onClick: info => setSelectedSegment(info.object?.properties || null),
      getLineColor: feature => numeric(feature.properties[field])===null?[178,117,33,230]:ramp(Number(feature.properties[field]),maximum),
      getLineWidth: feature => 1.5 + 4 * Math.sqrt(Math.max(0, Number(feature.properties[field]) || 0) / Math.max(maximum, 1)),
      lineWidthUnits: 'pixels', lineWidthMinPixels: 1,
      onHover: setHover,
      updateTriggers: { getLineColor: [field, maximum], getLineWidth: [field, maximum] },
    }));
    if (preview && showStops) result.push(new GeoJsonLayer({
      id: 'city-preview-stops',
      data: { type: 'FeatureCollection', features: preview.features.filter(f => f.properties.kind === 'stops') },
      pickable: true, pointType: 'circle',
      getFillColor: [245, 154, 42, 215], getPointRadius: 3, pointRadiusUnits: 'pixels',
      onHover: setHover,
    }));
    if (data?.features?.length) result.push(new GeoJsonLayer({
      id: 'city-uploaded-data', data, pickable: true, pointType: 'circle',
      getLineColor: [225, 63, 64, 220], getLineWidth: 2, lineWidthMinPixels: 2,
      getFillColor: [225, 63, 64, 220], getPointRadius: 5, pointRadiusUnits: 'pixels',
      onHover: setHover,
    }));
    return result;
  }, [preview, flow, evaluation, evaluationMetric, field, maximum, showRoutes, showStops, data, opacity]);

  const toggleStyle = active => ({
    padding: '6px 9px', borderRadius: 7, cursor: 'pointer',
    border: active ? '1px solid #0f907f' : '1px solid #c5d6d2',
    background: active ? '#e2f5ef' : 'white', color: '#183c38',
  });
  const property = hover?.object?.properties;
  const isFlow = property?.edge_idx != null;
  const isEvaluation = property?.cell_x != null;
  const value = isFlow ? numeric(property[field]) : 0;

  return (
    <div ref={captureRoot} style={{ position: 'relative', width: '100%', height: '100%' }}>
      <DeckGL glOptions={{preserveDrawingBuffer:true}} viewState={viewState} onViewStateChange={({ viewState: next }) => setViewState(next)} controller layers={layers}>
        <Map preserveDrawingBuffer mapStyle={BASEMAP} />
      </DeckGL>
      <div style={{ position: 'absolute', top: 16, right: 16, maxWidth: 340, padding: '12px 15px', borderRadius: 10, background: 'rgba(255,255,255,.95)', color: '#183c38', boxShadow: '0 5px 20px #0002', fontSize: 12 }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 5 }}>{city.name} · {run ? '路段客流预测' : evaluationMetric ? '城市指标' : '公交线网预览'}</div>
        {error && <div style={{ color: '#a33' }}>{error}</div>}
        {run ? <>
          {run.poi_coordinate_system !== 'WGS84' && <div style={{ color: '#a33', fontWeight: 700 }}>历史实验未记录坐标转换来源；当前按原实验几何展示</div>}
          <div>{{am_peak:'早高峰',offpeak:'平峰',pm_peak:'晚高峰'}[run.period] || run.period} · {run.poi_count.toLocaleString()} POI · {run.hourly_demand_pax.toLocaleString()} 人次/小时</div>
          <div style={{ color: '#87572e' }}>{run.headway_basis === 'explicit_uniform_assumption' ? `统一 ${run.assumed_headway_min} 分钟间隔：测试假设，不是实测频率` : run.headway_basis === 'observed_with_empirical_imputation' ? `原实验频率：实测及经验补全，补全比例 ${((run.headway_empirical_imputation_share || 0) * 100).toFixed(1)}%` : run.headway_basis?.includes('fallback') ? `上传值优先；缺失默认 ${run.default_headway_min} 分钟，补全比例 ${(100 * run.headway_default_share).toFixed(1)}%` : '逐线路发车间隔：用户导入数据'}</div>
          <div style={{ display: 'flex', gap: 5, marginTop: 10, flexWrap: 'wrap' }}>
            <button type="button" style={toggleStyle(variant === 'desired')} onClick={() => setVariant('desired')}>期望分配</button>
            <button type="button" style={toggleStyle(variant === 'served')} onClick={() => setVariant('served')}>容量可行</button>
            <button type="button" style={toggleStyle(metric === 'flow')} onClick={() => setMetric('flow')}>绝对客流</button>
            <button type="button" style={toggleStyle(metric === 'vc')} onClick={() => setMetric('vc')}>V/C</button>
          </div>
          {!flow && !error && <div style={{ marginTop: 7 }}>正在读取完整路段结果…</div>}
          {flow && <div style={{ marginTop: 6 }}><label>客流图层不透明度 {Math.round(opacity * 100)}%<input aria-label="客流图层不透明度" type="range" min="0" max="100" value={Math.round(opacity * 100)} onChange={event => setOpacity(Number(event.target.value) / 100)} style={{ width: '100%' }} /></label><div>{commonScale?.runs?.length ? `${commonScale.mode==='scenario'?'分时情景':'同条件'}${commonScale.runs.length}组实验统一色标` : '当前实验色标'}；点击路段可固定查看详情。</div></div>}
          {flow && <div style={{ marginTop: 8 }}>{flow.features.length.toLocaleString()} 条物理路段；色标上限 {maximum.toLocaleString(undefined, { maximumFractionDigits: metric === 'vc' ? 2 : 0 })}{metric === 'flow' ? ' 人次/小时' : ''}。灰色为零客流，橙褐色为指标缺失。</div>}
        </> : evaluationMetric ? <div>
          {evaluationMetric === 'density' ? 'POI 密度（每平方公里 POI 个数，色彩按本市第 98 百分位标准化）' : '公交覆盖度（步行便捷度 60% + 线路多样性 40%）'}
          {!evaluation && !error && <div>正在读取评价网格…</div>}
          {evaluation && <div>{evaluation.features.length.toLocaleString()} 个有 POI 网格；此图不包括无人 POI 的区域。</div>}
        </div> : !preview ? <div>正在读取本地线网…</div> : (
          <div>线路抽样展示 {preview.counts.routes.shown.toLocaleString()} / {preview.counts.routes.total.toLocaleString()} 条；站点抽样展示 {preview.counts.stops.shown.toLocaleString()} / {preview.counts.stops.total.toLocaleString()} 个。尚未进行交通分配。</div>
        )}
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <button type="button" style={toggleStyle(showRoutes)} onClick={() => setShowRoutes(value => !value)}>原始线路</button>
          <button type="button" style={toggleStyle(false)} onClick={() => setViewState({ longitude: city.center[0], latitude: city.center[1], zoom: cityZoom(city) })}>回到本市</button>
          <button type="button" style={toggleStyle(showStops)} onClick={() => setShowStops(value => !value)}>站点</button>
        </div>
      </div>
      <div style={{position:'absolute',right:16,bottom:170,padding:8,zIndex:120,borderRadius:7,background:'rgba(255,255,255,.94)',color:'#183c38',maxWidth:290,fontSize:11}}>
        <ImageExportControls canvas={()=>captureMap(captureRoot.current)} metadata={{city:city.name,runId:run?.run_id,title:run?'公交路段客流':'城市公交线网',units:metric==='vc'?'无量纲':'人次/小时',legendMin:0,legendMax:maximum,legendLabel:metric==='vc'?'V/C':'小时客流',coordinateSystem:'WGS84 / Web Mercator显示',legendItems:[{label:'低：绿色；中：黄色；高：红色',color:'#1eb26a'},{label:'灰色：零客流；橙褐：指标缺失',color:'#b27521'},{label:'底图 © OpenStreetMap contributors',color:'#183c38'}]}} />
      </div>
      {evaluation && <div style={{ position: 'absolute', bottom: 24, right: 16, padding: 10, borderRadius: 9, background: 'rgba(255,255,255,.95)', color: '#183c38', fontSize: 11 }}>
        <div>{evaluationMetric === 'density' ? 'POI 密度（本市相对色标）' : '公交覆盖度（0-100）'}</div>
        <div style={{ width: 160, height: 10, margin: '7px 0', background: 'linear-gradient(to right, #1eb26a, #f6d02c, #da3433)' }} />
        <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>低</span><span>高</span></div>
      </div>}
      {flow && <div style={{ position: 'absolute', bottom: 24, right: 16, padding: 10, borderRadius: 9, background: 'rgba(255,255,255,.95)', color: '#183c38', fontSize: 11 }}>
        <div>{metric === 'flow' ? '客流（人次/小时）' : 'V/C（小时平均）'}</div>
        <div style={{ width: 160, height: 10, margin: '7px 0', background: 'linear-gradient(to right, #1eb26a, #f6d02c, #da3433)' }} />
        <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>0</span><span>{maximum.toLocaleString(undefined, { maximumFractionDigits: metric === 'vc' ? 2 : 0 })}</span></div>
      </div>}
      {selectedSegment && <div role="region" aria-label="所选路段详情" style={{ position: 'absolute', right: 16, bottom: 110, maxWidth: 300, padding: 12, borderRadius: 9, background: 'rgba(255,255,255,.97)', color: '#183c38', fontSize: 12, lineHeight: 1.8 }}>
        <button aria-label="关闭路段详情" onClick={() => setSelectedSegment(null)} style={{ float: 'right' }}>关闭</button>
        <b>{selectedSegment.from_name} → {selectedSegment.to_name}</b>
        <div>期望客流：{textNumber(selectedSegment.desired_flow_pax_per_hour,1)} 人次/小时</div>
        <div>容量可行客流：{textNumber(selectedSegment.served_flow_pax_per_hour,1)} 人次/小时</div>
        <div>小时容量：{textNumber(selectedSegment.capacity_pax_per_hour,0)} 人次</div>
        <div>期望V/C：{textNumber(selectedSegment.desired_vc,3)}；容量可行V/C：{textNumber(selectedSegment.served_vc,3)}</div>
        <div style={{ fontSize: 11 }}>以上为路段通过量，不等于城市OD出行总人次。</div>
      </div>}
      {property && <div style={{ position: 'absolute', left: hover.x + 12, top: hover.y + 12, padding: '6px 9px', borderRadius: 6, background: '#17312dec', color: 'white', fontSize: 12, pointerEvents: 'none' }}>
        {isFlow ? <>
          <div>{property.from_name} → {property.to_name}</div>
          <div>{value===null?'未记录（不按零客流处理）':metric === 'flow' ? `${value.toFixed(1)} 人次/小时` : `V/C ${value.toFixed(2)}`}</div>
          <div>小时容量：{textNumber(property.capacity_pax_per_hour,0)} 人次</div>
        </> : isEvaluation ? <>
          <div>{property.poi_count} 个 POI / 平方公里</div>
          <div>最近站点 {Number(property.nearest_station_m).toFixed(0)} 米；覆盖度 {Number(property.coverage_score).toFixed(1)} 分</div>
          <div>500 米内线路数：{property.diversity_count}</div>
        </> : (property.name || (property.kind === 'routes' ? '公交线路' : '公交站点'))}
      </div>}
    </div>
  );
};

export default CityPreviewMap;