import React, { useState } from 'react';
import { Upload, Button, Progress, message, Space } from 'antd';
import { UploadOutlined, DeleteOutlined } from '@ant-design/icons';
import CitySelector from './CitySelector';

const DataUpload = ({ city, onCityChange, geoData, onDataUpdate }) => {
  const [loading, setLoading] = useState(false);
  const pendingFiles = React.useRef([]);
  const pendingTimer = React.useRef(null);
  const [percent, setPercent] = useState(0);

  const routes = geoData.features.filter(f => f.geometry?.type?.includes('Line')).length;
  const stops = geoData.features.filter(f => f.geometry?.type === 'Point').length;

  const processFiles = async (fileList) => {
    console.log('processFiles called, files:', fileList.length);
    setLoading(true);
    setPercent(0);
    let newFeatures = [];
    try {
      for (let i = 0; i < fileList.length; i++) {
        const text = await fileList[i].text();
        const json = JSON.parse(text);
        const features = json.type === 'FeatureCollection' ? json.features : [json];
        newFeatures = [...newFeatures, ...features];
        setPercent(Math.round(((i + 1) / fileList.length) * 100));
      }
      console.log('onDataUpdate called, features:', newFeatures.length);
      onDataUpdate(prev => ({
        type: 'FeatureCollection',
        features: [...(prev?.features || []), ...newFeatures],
      }));
      message.success(`已导入 ${newFeatures.length} 个要素`);
    } catch (e) {
      console.error('上传错误:', e);
      message.error('文件解析失败，请检查 GeoJSON 格式');
    } finally {
      setLoading(false);
      setPercent(0);
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

      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        <Upload
          multiple
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
            导入 .geojson
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
          清空视图
        </Button>
      </Space>
    </div>
  );
};

export default DataUpload;
