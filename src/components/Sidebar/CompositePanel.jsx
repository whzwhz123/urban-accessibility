import React, { useEffect, useState } from 'react';
import { Alert, Select } from 'antd';

const STATUS_COLORS = {
    '供给不足': '#e84c3d',
    '供需匹配': '#4ade80',
    '供给过剩': '#60a5fa',
    '低效区域': '#888888',
};

const STATUS_DESC = {
    '供给不足': '需求高、供给低，优先改善区域',
    '供需匹配': '需求与供给均衡',
    '供给过剩': '需求低、供给高，资源配置偏冗余',
    '低效区域': '需求低、供给低，次要关注区域',
};

function SectionLabel({ children }) {
    return (
        <div style={{
            fontSize: 10, fontWeight: 600,
            color: 'rgba(255,255,255,0.22)',
            letterSpacing: '0.1em', textTransform: 'uppercase',
            marginBottom: 8, marginTop: 14,
        }}>{children}</div>
    );
}

function Toggle({ active, onClick, disabled }) {
    return (
        <div onClick={e => { e.stopPropagation(); if (!disabled) onClick(); }} style={{
            width: 28, height: 15, borderRadius: 8, flexShrink: 0, cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1,
            background: active ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.1)',
            position: 'relative', transition: 'background 0.2s',
        }}>
            <div style={{
                position: 'absolute', top: 2, left: active ? 15 : 2,
                width: 11, height: 11, borderRadius: '50%',
                background: active ? '#111' : 'rgba(255,255,255,0.5)',
                transition: 'left 0.2s', boxShadow: '0 1px 3px rgba(0,0,0,0.3)',
            }} />
        </div>
    );
}

const CompositePanel = ({ city, selectedRun, onRunChange, vis, onToggleVis, demandRun }) => {
    const [runs, setRuns] = useState([]);
    const [error, setError] = useState('');
    const [revision, setRevision] = useState(0);
    useEffect(() => {
        const controller = new AbortController();
        setRuns([]); setError('');
        fetch(`/api/cities/${encodeURIComponent(city.id)}/composite-runs`, { signal: controller.signal })
            .then(response => { if (!response.ok) throw new Error('无法读取重建评价结果'); return response.json(); })
            .then(result => {
                const matching = demandRun ? result.runs.filter(run => run.demand_run_id === demandRun.run_id) : result.runs.filter(run => !run.demand_run_id);
                setRuns(matching);
                if (matching.length && !selectedRun) onRunChange(matching.find(run => !run.source_scope?.pilot_only) || matching[0]);
            })
            .catch(error => { if (error.name !== 'AbortError') setError(error.message); });
        return () => controller.abort();
    }, [city.id, demandRun?.run_id, revision]);
    useEffect(() => {
        if (selectedRun) setRuns(previous => previous.some(run => run.run_id === selectedRun.run_id) ? previous : [selectedRun, ...previous]);
    }, [selectedRun?.run_id]);
    return (
        <div style={{ fontFamily: 'system-ui,-apple-system,sans-serif', padding: '8px 0' }}>

            <div style={{ padding: '8px 14px 14px', color: '#c8d7e4', fontSize: 12 }}>
                <div style={{ marginBottom: 7 }}>评价结果来源</div>
                {selectedRun?.source_scope?.pilot_only && <div style={{ color: '#ffca83', lineHeight: 1.7, marginBottom: 8 }}>本版本为抽样评价：机会评分覆盖 {selectedRun.source_scope.scored_grid_count}/{selectedRun.source_scope.demand_grid_count} 个需求格网。颜色仅用于样本比较，不能解释为全城排名。</div>}
                <Select style={{ width: '100%' }} placeholder="尚无本需求版本结果" value={selectedRun?.run_id || (city.id === 'Beijing' && !demandRun ? 'historical' : undefined)}
                    onChange={id => onRunChange(runs.find(run => run.run_id === id) || null)}
                    options={[...runs.map(run => ({ value: run.run_id, label: `${run.pipeline === 'city_workflow_v1' ? (run.composite_ready ? '统一需求评价 · ' : '基础指标 · ') : ''}${run.has_opportunity ? (run.source_scope?.pilot_only ? '试采评价 · ' : '机会重算 · ') : ''}${run.pipeline === 'city_workflow_v1' ? (run.composite_ready ? '三指标熵权' : '待补齐输入') : run.method === 'three_indicator_entropy_unique_grid_v2' ? '唯一格网修正' : '首版重建'} · ${run.grid_count}格网 · ${run.run_id.slice(0, 15)}` })), ...(city.id === 'Beijing' && !demandRun ? [{ value: 'historical', label: '历史原结果（存在重复记录）' }] : [])]} />
                {error && <Alert style={{ marginTop: 8 }} type="warning" message={error} action={<button onClick={() => setRevision(value => value + 1)}>重试读取</button>} />}
                {selectedRun?.warnings?.map((warning, index) => <Alert key={index} type="warning" message={warning} style={{ marginTop: 8 }} />)}
                {selectedRun?.pipeline === 'city_workflow_v1' && <div style={{ marginTop: 8, lineHeight: 1.8 }}>
                    已有指标：{(selectedRun.available_layers || []).map(key => ({ demand: '需求', coverage: '覆盖度', isochrone: '机会评分', tci: '公交竞争力', composite: '综合评价', imbalance: '供需分类' })[key]).filter(Boolean).join('、') || '暂无'}。
                    <div style={{ color: '#ffca83' }}>不同城市或不同样本范围的独立归一化分数不能直接当作跨城市绝对排名。</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}><a href={`/api/cities/${city.id}/composite-runs/${selectedRun.run_id}/export`} download>完整评价包</a><a href={`/api/cities/${city.id}/composite-runs/${selectedRun.run_id}/parameters`} download>参数快照</a></div>
                </div>}
                {!selectedRun && <div style={{ marginTop: 8 }}>请先到分析设置计算本需求版本的评价。</div>}
                {selectedRun?.pipeline === 'city_workflow_v1' && !selectedRun.weights && <div style={{ marginTop: 8 }}>已计算需求与覆盖度；综合评价需至少两个具有有效机会评分的格网。缺失指标没有补零。</div>}
                {selectedRun?.weights && <div style={{ fontSize: 11, lineHeight: 1.8, marginTop: 9 }}>
                    <div>{selectedRun.method === 'three_indicator_entropy_unique_grid_v2' ? '已核验历史算法口径；修正重复关联，每格一条结果。' : '首版重建：归一化与供给口径不同，建议选择最新修正版。'}</div>
                    <div>范围：{selectedRun.source_scope?.pilot_only ? '局部试采，仅验证流程' : selectedRun.reference_footprint ? '原结果的唯一格网范围' : '三项指标共同覆盖范围'}；{selectedRun.grid_count}个有效格网。</div>
                    <div>熵权：需求 {selectedRun.weights.demand.toFixed(4)}；覆盖 {selectedRun.weights.coverage.toFixed(4)}；机会 {selectedRun.weights.opportunity.toFixed(4)}。</div>
                    {selectedRun.supply_weights && <div>供给仅含覆盖与机会：覆盖 {selectedRun.supply_weights.coverage.toFixed(4)}；机会 {selectedRun.supply_weights.opportunity.toFixed(4)}。</div>}
                    <div>分类中位数：需求 {selectedRun.demand_median.toFixed(2)}；供给 {selectedRun.supply_median.toFixed(2)}。</div>
                    <div style={{ color: '#ffca83' }}>{selectedRun.pipeline === 'city_workflow_v1' ? '需求与各指标使用同一共享需求版本，参数见结果快照。' : selectedRun.has_opportunity ? '机会评分已重算；需求与覆盖仍用历史指标。时间衰减采用原程序距离近似。' : '输入仍为历史指标，未重新采集；不保证与论文报告数值一致。'}</div>
                    {selectedRun.source_scope && <div>采集成功{selectedRun.source_scope.valid_polygons}条；失败{selectedRun.source_scope.failed_records}条。{selectedRun.source_scope.legacy_policy_assumption ? '历史采集未记录策略，BUS来源未经独立核验。' : ''}</div>}
                    {!!selectedRun.source_scope?.sdk_compatibility_records && <div style={{ color: '#ffca83' }}>其中{selectedRun.source_scope.sdk_compatibility_records}条按已核验的高德SDK状态兼容规则接收；保留原始状态，并已通过几何检查。</div>}
                    <a href={`/api/cities/${city.id}/composite-runs/${selectedRun.run_id}/grid`} target="_blank" rel="noreferrer" style={{ color: '#80d7a5' }}>查看本次GeoJSON</a>
                </div>}
            </div>
            {/* 综合可达性得分 */}
            <div style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '9px 14px',
                }}>
                    <div>
                        <div style={{ fontSize: 12, fontWeight: vis?.composite ? 600 : 400, color: vis?.composite ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.38)' }}>综合可达性得分</div>
                        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.22)', marginTop: 2 }}>{selectedRun ? (selectedRun.method === 'three_indicator_entropy_unique_grid_v2' ? '唯一格网修正 · 样本Min-Max归一化' : '首版三指标重建') : '原项目熵权法结果 · 同格网原记录均值'}</div>
                    </div>
                    <Toggle disabled={selectedRun?.composite_ready === false || (!selectedRun && !(city.id === 'Beijing' && !demandRun))} active={vis?.composite ?? false} onClick={() => { if (selectedRun?.composite_ready !== false && (selectedRun || city.id === 'Beijing')) onToggleVis('composite'); }} />
                </div>
                {vis?.composite && (
                    <div style={{ padding: '0 14px 10px' }}>
                        <div style={{ height: 10, borderRadius: 3, background: 'linear-gradient(to right, #1e3a5f, #2563eb, #22c55e, #fbbf24, #ef4444)', marginBottom: 4 }} />
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>低</span>
                            <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.25)' }}>高</span>
                        </div>
                        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.2)', marginTop: 6, lineHeight: 1.7 }}>
                            {selectedRun ? '基于本次三项输入指标重新计算熵权，每个格网仅一条结果；权重由本次有效样本计算，不使用固定论文权重。' : '原文件含重复格网与得分冲突，综合图显示原记录均值；具体数量和得分范围见地图提示。'}
                        </div>
                    </div>
                )}
            </div>

            {/* 供需失衡识别 */}
            <div style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <div style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '9px 14px',
                }}>
                    <div>
                        <div style={{ fontSize: 12, fontWeight: vis?.imbalance ? 600 : 400, color: vis?.imbalance ? 'rgba(255,255,255,0.85)' : 'rgba(255,255,255,0.38)' }}>供需失衡识别</div>
                        <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.22)', marginTop: 2 }}>{selectedRun ? '中位数象限法 · 唯一格网' : '象限分析法 · 历史西城区格网'}</div>
                    </div>
                    <Toggle disabled={selectedRun?.composite_ready === false || (!selectedRun && !(city.id === 'Beijing' && !demandRun))} active={vis?.imbalance ?? false} onClick={() => { if (selectedRun?.composite_ready !== false && (selectedRun || city.id === 'Beijing')) onToggleVis('imbalance'); }} />
                </div>
                {vis?.imbalance && (
                    <div style={{ padding: '0 14px 10px' }}>
                        {Object.entries(STATUS_COLORS).map(([k, c]) => (
                            <div key={k} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 7 }}>
                                <div style={{ width: 10, height: 10, borderRadius: 2, background: c, flexShrink: 0, marginTop: 2 }} />
                                <div>
                                    <div style={{ fontSize: 11, color: 'rgba(255,255,255,0.6)', fontWeight: 600 }}>{k}</div>
                                    <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.22)', lineHeight: 1.5 }}>{STATUS_DESC[k]}</div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            <div style={{ padding: '12px 14px' }}>
                <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.18)', lineHeight: 1.8 }}>
                    {selectedRun?.composite_ready === false ? '补齐等时圈后才能生成综合评价与供需分类；当前没有用缺失值补零。' : selectedRun?.method === 'three_indicator_entropy_unique_grid_v2' ? '以归一化需求和纯供给分中位数分类；供给分只包含覆盖度与机会可达性，不包含需求。等于中位数归入高值，零需求时供需比不可计算。' : '首版重建采用需求与综合得分分类；历史原结果采用需求与纯供给分分类。'}
                </div>
            </div>

        </div>
    );
};

export default CompositePanel;