import BatchWorkflowPanel from './BatchWorkflowPanel';
import ProjectPanel from './ProjectPanel';
import React from 'react';
import { Tabs } from 'antd';
import DataUpload from './DataUpload';
import AnalysisPanel from './AnalysisPanel';
import CompositePanel from './CompositePanel';
import CityAssignmentPanel from './CityAssignmentPanel';
import DemandPanel from './DemandPanel';

const SidebarPanel = ({ city, onCityChange, onDataUpdate, geoData, vis, onToggleVis, tciSession, onSessionChange, selectedRun, onRunSelect, compositeRun, onCompositeRunChange, demandRun, onDemandChange, activeTab = '1', onTabChange, notice, onNoticeClose, drafts = {}, onDraftChange, workspace, onRestoreProject, restoredConfig, onCompare, onConfigChange, onHistoricalView }) => {
    const activeKey = activeTab;
    const items = [
        {
            key: '1',
            label: '数据管理',
            children: (
                <>
                <ProjectPanel city={city} demandRun={demandRun} selectedRun={selectedRun} compositeRun={compositeRun} workspace={workspace} onRestore={onRestoreProject} />
                <DataUpload
                    city={city}
                    onCityChange={onCityChange}
                    onDataUpdate={onDataUpdate}
                    geoData={geoData}
                />
                </>
            ),
        },
        { key: 'demand', label: '需求配置', children: <DemandPanel onConfigChange={value=>onConfigChange?.('demand_config',value)} restoredConfig={restoredConfig} city={city} demandRun={demandRun} onDemandChange={onDemandChange} onDirtyChange={dirty => onDraftChange('demand', dirty)} onToggleVis={onToggleVis} active={activeKey === 'demand'} /> },
        {
            key: 'assignment',
            label: '客流分配',
            children: <><details style={{color:'#c8d7e4',padding:10}}><summary>批量运行多个时段 / 完整流程</summary><BatchWorkflowPanel assignmentScenario={workspace?.assignment_scenario} assignmentParameters={workspace?.assignment_parameters} analysisParameters={workspace?.analysis_parameters} city={city} demandRun={demandRun} onDemandChange={onDemandChange} onRunSelect={onRunSelect} onEvaluationComplete={onCompositeRunChange} /></details><CityAssignmentPanel onHistoricalView={onHistoricalView} onScenarioChange={value=>onConfigChange?.('assignment_scenario',value)} onConfigChange={value=>onConfigChange?.('assignment_parameters',value)} onCompare={onCompare} restoredConfig={restoredConfig} demandRun={demandRun} city={city} selectedRun={selectedRun} onRunSelect={onRunSelect} onDemandChange={onDemandChange} active={activeKey === 'assignment'} /></>,
        },
        {
            key: '2',
            label: '分析设置',
            children: (
                <AnalysisPanel
                    onConfigChange={value=>onConfigChange?.('analysis_parameters',value)}
                    restoredConfig={restoredConfig}
                    city={city}
                    demandRun={demandRun}
                    selectedEvaluation={compositeRun}
                    vis={vis}
                    onToggleVis={onToggleVis}
                    tciSession={tciSession}
                    onSessionChange={onSessionChange}
                    onEvaluationComplete={onCompositeRunChange}
                    onDirtyChange={dirty => onDraftChange('analysis', dirty)}
                />
            ),
        },
        {
            key: '3',
            label: '综合评价',
            children: (
                <CompositePanel
                    demandRun={demandRun}
                    city={city}
                    selectedRun={compositeRun}
                    onRunChange={onCompositeRunChange}
                    vis={vis}
                    onToggleVis={onToggleVis}
                />
            ),
        },
    ];

    return (
        <div className="sidebar-panel" style={{ height: 'calc(100vh - 32px)', display: 'flex', flexDirection: 'column', background: 'transparent' }}>
            <div style={{ padding: '10px 12px', borderBottom: '1px solid #ffffff18', color: '#c8d7e4', fontSize: 11, lineHeight: 1.6 }}>
                <b style={{ color: '#e3edf4' }}>{city.name}</b> · {demandRun ? '已保存共享需求' : city.network_prepared && city.poi_prepared ? '数据已准备，下一步配置需求' : '请先检查并准备城市数据'}
                {!demandRun && <button style={{ marginLeft: 6 }} onClick={() => onTabChange(city.network_prepared && city.poi_prepared ? 'demand' : '1')}>{city.network_prepared && city.poi_prepared ? '配置需求' : '准备数据'}</button>}
                {notice && <div role="status" style={{ color: '#ffca83', marginTop: 5 }}>{notice} <button aria-label="关闭状态提示" onClick={onNoticeClose}>关闭</button></div>}
                {drafts.demand && <div style={{ color: '#ffca83', marginTop: 5 }}>需求有未保存修改；后续计算仍使用已保存版本。<button onClick={() => onTabChange('demand')}>去保存</button></div>}
                {drafts.analysis && <div style={{ color: '#ffca83', marginTop: 5 }}>分析设置尚未应用，地图仍显示已计算的结果。<button onClick={() => onTabChange('2')}>去计算</button></div>}
            </div>
            <Tabs
                activeKey={activeKey}
                onChange={key => { onTabChange(key); if (key !== 'assignment') onRunSelect(null); }}
                size="small"
                tabPosition="left"
                items={items}
                style={{ flex: 1, minHeight: 0 }}
                className="custom-sidebar-tabs"
            />
        </div>
    );
};

export default SidebarPanel;
