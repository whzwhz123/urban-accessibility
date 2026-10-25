import React, { useState, useEffect, useRef } from 'react';
import { Upload, Button, Progress, message, Space } from 'antd';
import { UploadOutlined, DeleteOutlined } from '@ant-design/icons';
import CitySelector from './CitySelector';
import {overlayFeatures} from '../../utils/coordinates';

const DataUpload = ({ city, onCityChange, geoData, onDataUpdate }) => {
  const [loading, setLoading] = useState(false);
  const [coordinates,setCoordinates]=useState('WGS84');
  const cityScope=useRef(null);
  const cityKey=`${city.id}:${city.custom_input_version || 'builtin'}`;
  cityScope.current=cityKey;
  const pendingFiles = React.useRef([]);
  const pendingTimer = React.useRef(null);
  const [percent, setPercent] = useState(0);

  const routes = geoData.features.filter(f => f.geometry?.type?.includes('Line')).length;
  const stops = geoData.features.filter(f => f.geometry?.type === 'Point').length;

  useEffect(()=>{pendingFiles.current=[];clearTimeout(pendingTimer.current);setLoading(false);setPercent(0);return()=>clearTimeout(pendingTimer.current);},[cityKey]);

  const processFiles = async (fileList) => {
    const own=cityScope.current;
    const declaration=coordinates;
    setLoading(true);
    setPercent(0);
    let newFeatures = [];
    try {
      for (let i = 0; i < fileList.length; i++) {
        const text = await fileList[i].text();
        const json = JSON.parse(text);
        if(own!==cityScope.current)return;
        const features = overlayFeatures(json,declaration);
        newFeatures = [...newFeatures, ...features];
        setPercent(Math.round(((i + 1) / fileList.length) * 100));
      }
      if(own!==cityScope.current)return;
      onDataUpdate(prev => ({
        type: 'FeatureCollection',
        features: [...(prev?.features || []), ...newFeatures],
      }));
      message.success(`已导入 ${newFeatures.length} 个要素`);
    } catch (e) {
      if(own===cityScope.current)message.error(e.message || '文件解析失败，请检查GeoJSON格式');
    } finally {
      if(own===cityScope.current){setLoading(false);setPercent(0);}
    }
  };

  return (
    <div className="sidebar-content-wrapper" style={{ padding: '14px 14px' }}>
      <CitySelector city={city} onCityChange={onCityChange} />
      {/* 统计行 */}
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        padding: '7px 0', borderBottom: '1px solid rgba(255,255,255,0.06)',
        fontSize: 12, color: 'rgba(255,255,255,0.45)',
      }}>
        <span>手动导入线路</span>
        <span style={{ color: 'rgba(255,255,255,0.75)', fontWeight: 600 }}>{routes} 条</span>
      </div>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        padding: '7px 0', marginBottom: 14,
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        fontSize: 12, color: 'rgba(255,255,255,0.45)',
      }}>
        <span>手动导入站点</span>
        <span style={{ color: 'rgba(255,255,255,0.75)', fontWeight: 600 }}>{stops} 个</span>
      </div>

      <div style={{color:'#c8d7e4',fontSize:12,marginBottom:8}}>
        <b>临时叠加图层</b><div>只用于地图展示，不参与需求或分配。计算数据请使用上方导入向导。</div>
        <label>文件坐标 <select aria-label="叠加图层坐标系统" value={coordinates} onChange={event=>setCoordinates(event.target.value)} disabled={loading}><option value="WGS84">WGS84经纬度</option><option value="GCJ02">GCJ-02高德经纬度</option></select></label>
      </div>
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Upload
          multiple
          accept=".geojson,.json"
          disabled={loading}
          beforeUpload={(file) => {
            pendingFiles.current.push(file);
            clearTimeout(pendingTimer.current);
            pendingTimer.current = setTimeout(() => {
              processFiles([...pendingFiles.current]);
              pendingFiles.current = [];
            }, 50);
            return false;
          }}
          showUploadList={false}
          style={{ display: 'block', width: '100%' }}
        >
          <Button
            block
            type="primary"
            icon={<UploadOutlined />}
            loading={loading}
            style={{ width: '100%', fontSize: 12, height: 32 }}
          >
            添加地图叠加图层
          </Button>
        </Upload>

        {loading && (
          <Progress
            percent={percent}
            size="small"
            strokeColor="#3b82f6"
            trailColor="rgba(255,255,255,0.06)"
          />
        )}

        <Button
          block
          danger
          ghost
          icon={<DeleteOutlined />}
          onClick={() => onDataUpdate({ type: 'FeatureCollection', features: [] })}
          style={{ width: '100%', fontSize: 12, height: 32 }}
        >
          移除临时叠加图层
        </Button>
      </Space>
    </div>
  );
};

export default DataUpload;
