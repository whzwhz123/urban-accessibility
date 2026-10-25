import AssignmentCompareMap from '../components/Map/AssignmentCompareMap';
import React, { useState, useRef, useEffect, useCallback } from 'react';
import MapContainer from '../components/Map/MapContainer';
import CityPreviewMap from '../components/Map/CityPreviewMap';
import SidebarPanel from '../components/Sidebar/SidebarPanel';
import '../styles/MainPage.css';
import { WORKSPACE_KEY, DEFAULT_VIS, TAB_KEYS, workspaceSnapshot, readWorkspace, restoredVisibility } from './workspaceState';

const MainPage = () => {
    const [geoData, setGeoData] = useState({ type: 'FeatureCollection', features: [] });
    const [city, setCity] = useState({ id: 'Beijing', name: '北京市', center: [116.4074, 39.920], has_evaluation: true });
    const [selectedRun, setSelectedRun] = useState(null);
    const [compositeRun, setCompositeRun] = useState(null);
    const [demandRun, setDemandRun] = useState(null);
    const [vis, setVis] = useState({ ...DEFAULT_VIS });
    const [activeTab, setActiveTab] = useState('1');
    const [booting, setBooting] = useState(true);
    const [bootError, setBootError] = useState('');
    const [bootRevision, setBootRevision] = useState(0);
    const [notice, setNotice] = useState('');
    const [drafts, setDrafts] = useState({ demand: false, analysis: false });
    const [formConfigs,setFormConfigs]=useState({});
    const configChanged=useCallback((key,value)=>setFormConfigs(previous=>JSON.stringify(previous[key])===JSON.stringify(value)?previous:{...previous,[key]:value}),[]);
    const [comparison,setComparison]=useState(null);
    const [sidebarOpen,setSidebarOpen]=useState(true);
    const [restoredConfig,setRestoredConfig]=useState(null);
    const [tciSession, setTciSession] = useState('morning');
    const scope = useRef(null); scope.current = { city: city.id, demand: demandRun?.run_id, inputVersion: city.custom_input_version };

    useEffect(() => {
        const controller = new AbortController();
        const read = async url => { const response = await fetch(url, { signal: controller.signal }); if (!response.ok) throw new Error('本地数据服务未连接，请启动服务后重试。'); return response.json(); };
        setBooting(true); setBootError('');
        (async () => {
            const saved = readWorkspace(window.sessionStorage);
            const catalog = await read('/api/cities');
            const nextCity = catalog.cities.find(value => value.id === saved?.city_id) || catalog.cities.find(value => value.id === 'Beijing') || catalog.cities[0];
            if (!nextCity) { setNotice('尚无本地城市数据，请先注册城市并上传线网与POI。'); setBooting(false); return; }
            const sameSource = saved && saved.city_id === nextCity.id && saved.input_version === (nextCity.custom_input_version || null);
            let demand = null, assignment = null, evaluation = null;
            if (sameSource && saved.demand_id) {
                const history = await read(`/api/cities/${nextCity.id}/demand-runs`);
                demand = history.runs.find(value => value.run_id === saved.demand_id && value.current_inputs !== false) || null;
            }
            if (sameSource && saved.assignment_id) {
                const history = await read(`/api/cities/${nextCity.id}/runs`);
                let recorded = history.runs.find(value => value.run_id === saved.assignment_id);
                if (!recorded) { try { recorded = await read(`/api/cities/${nextCity.id}/runs/${saved.assignment_id}/summary`); } catch (error) { if (error.name === 'AbortError') throw error; } }
                assignment = recorded && (!recorded.demand_run_id || recorded.demand_run_id === demand?.run_id) ? recorded : null;
            }
            if (sameSource && saved.evaluation_id) {
                const history = await read(`/api/cities/${nextCity.id}/composite-runs`);
                evaluation = history.runs.find(value => value.run_id === saved.evaluation_id && (!value.demand_run_id || value.demand_run_id === demand?.run_id)) || null;
            }
            if (controller.signal.aborted) return;
            setCity(nextCity); setDemandRun(demand); setSelectedRun(assignment); setCompositeRun(evaluation);
            if (sameSource) {
                setActiveTab(TAB_KEYS.includes(saved.active_tab) ? saved.active_tab : '1');
                setTciSession(saved.tci_session || 'morning'); setVis(restoredVisibility(saved, demand, evaluation));
                setNotice(saved.demand_id && !demand ? '原需求输入已变化或版本不可用，已取消旧结果选择；请重新准备并保存需求。' : '已恢复本次窗口的城市和结果版本，未自动启动计算或采集。');
            } else if (saved) setNotice('城市输入版本已变化，未恢复旧需求或旧结果。');
            setBooting(false);
        })().catch(error => { if (error.name !== 'AbortError') { setBootError(error.message); setBooting(false); } });
        return () => controller.abort();
    }, [bootRevision]);

    useEffect(() => {
        if (booting || bootError) return;
        try { window.sessionStorage.setItem(WORKSPACE_KEY, JSON.stringify(workspaceSnapshot({ city, demandRun, selectedRun, compositeRun, activeTab, vis, tciSession }))); }
        catch { /* Browsing can continue when session storage is unavailable. */ }
    }, [booting, bootError, city, demandRun, selectedRun, compositeRun, activeTab, vis, tciSession]);

    useEffect(() => {
        if (!drafts.demand && !drafts.analysis) return undefined;
        const warn = event => { event.preventDefault(); event.returnValue = ''; };
        window.addEventListener('beforeunload', warn);
        return () => window.removeEventListener('beforeunload', warn);
    }, [drafts.demand, drafts.analysis]);

    const draftChanged = (kind, dirty) => setDrafts(previous => previous[kind] === dirty ? previous : { ...previous, [kind]: dirty });

    const toggleVis = (key, enabled) => {
        setComparison(null);
        setSelectedRun(null);
        setVis(previous => {
            const next = { ...previous, [key]: enabled ?? !previous[key] };
            const heatmaps = ['demand', 'isochrone', 'coverage', 'tci', 'composite', 'imbalance'];
            if (next[key] && heatmaps.includes(key)) heatmaps.forEach(other => { if (other !== key) next[other] = false; });
            return next;
        });
    };
    const changeCity = next => {
        const previous = scope.current.city;
        const sameSource = next.id === previous && next.custom_input_version === scope.current.inputVersion;
        scope.current = { city: next.id, demand: sameSource ? scope.current.demand : undefined, inputVersion: next.custom_input_version };
        setComparison(null);
        setCity(next);
        if (sameSource) return;
        setFormConfigs({});
        setDemandRun(null); setDrafts({ demand: false, analysis: false });
        setNotice('已切换城市或数据版本，请准备数据并选择对应的需求版本。');
        setVis({ demand: false, isochrone: false, coverage: false, tci: false, lines: true, stops: true, composite: false, imbalance: false });
        setCompositeRun(null);
        setSelectedRun(null);
        setGeoData({ type: 'FeatureCollection', features: [] });
    };

    const changeDemand = next => {
        if (next && next.city_id !== scope.current.city) return;
        setComparison(null);
        setNotice('');
        scope.current = { ...scope.current, demand: next?.run_id };
        setDemandRun(next);
        setSelectedRun(null);
        setVis(previous => ({ ...previous, coverage: false, isochrone: false, tci: false, composite: false, imbalance: false }));
        setCompositeRun(previous => previous?.demand_run_id === next?.run_id ? previous : null);
    };

    const selectRun = next => {
        if (next && (next.city_id !== scope.current.city || next.demand_run_id && next.demand_run_id !== scope.current.demand)) { setNotice('结果与当前城市或需求版本不一致，请先切换到该实验的需求版本。'); return; }
        setComparison(null);
        setSelectedRun(next);
    };
    const selectEvaluation = next => {
        if (next && (next.city_id !== scope.current.city || next.demand_run_id && next.demand_run_id !== scope.current.demand)) { setNotice('结果与当前城市或需求版本不一致，请先切换到该实验的需求版本。'); return; }
        setComparison(null);
        setSelectedRun(null);
        setCompositeRun(next);
    };

    const viewHistorical = run => {
        if(run?.city_id!==scope.current.city)return;
        setComparison(null);setSelectedRun(run);setActiveTab('assignment');
        setNotice('只读展示历史实验图；当前共享需求与计算输入未切换，历史结果不代表新输入的分配结果。');
    };

    const restoreProject = async (project,options={}) => {
        const response=await fetch(`/api/projects/${project.project_id}/restore`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({restore_inputs:!!options.restoreInputs})});
        const restored=await response.json();
        if(!response.ok) throw new Error(restored.error || '工程恢复失败');
        const catalog=await fetch('/api/cities').then(value=>value.json());
        const nextCity=catalog.cities.find(value=>value.id===project.city.id);
        if(!nextCity) throw new Error('工程已保留。请先注册该城市并导入线网与POI，再手动打开工程。');
        const payload=restored.payload || project.payload || {}, saved=payload.workspace || {};
        const references=payload.version_references || {};
        const sameInput=restored.input_status!=='restored' && (references.input_version || null)===(nextCity.custom_input_version || null);
        const load=async (kind,id) => {
            if(!id)return null;
            const result=await fetch(`/api/cities/${nextCity.id}/${kind}/${id}/summary`);
            return result.ok ? result.json() : null;
        };
        const demandId=references.demand_id || saved.demand_id;
        let demand=null;
        if(demandId){
            const history=await fetch(`/api/cities/${nextCity.id}/demand-runs`).then(value=>value.json());
            demand=history.runs?.find(value=>value.run_id===demandId && value.current_inputs!==false) || null;
        }
        const assignment=await load('runs',references.assignment_id || saved.assignment_id);
        const evaluation=await load('composite-runs',references.evaluation_id || saved.evaluation_id);
        changeCity(nextCity);
        setDemandRun(sameInput ? demand : null);
        scope.current={city:nextCity.id,demand:sameInput ? demand?.run_id : undefined,inputVersion:nextCity.custom_input_version};
        const compatible=result=>sameInput && result && (!result.demand_run_id || result.demand_run_id===demand?.run_id);
        setSelectedRun(compatible(assignment) ? assignment : null);
        setCompositeRun(compatible(evaluation) ? evaluation : null);
        const display=payload.display_settings || saved;
        setVis(restoredVisibility(display,sameInput ? demand : null,compatible(evaluation) ? evaluation : null));
        setActiveTab(TAB_KEYS.includes(display.active_tab) ? display.active_tab : '1');
        setTciSession(display.tci_session || 'morning');
        setRestoredConfig({...payload,id:project.project_id+':'+Date.now(),city_id:nextCity.id});
        const warnings=[...(restored.missing_dependencies || []),...(restored.restore_warnings || [])];
        setNotice('已打开工程配置，未启动计算。'+(restored.input_status==='restored'?'输入已恢复为新版本，请准备数据并生成新需求；导入的历史结果可只读查看。':!sameInput?'输入版本不同，未把历史需求当作当前需求。':'')+(restored.input_status==='failed'?'输入恢复失败，原因见工程清单。':'')+(warnings.length ? ` ${warnings.length}项依赖或状态说明，请查看工程清单。` : ''));
        return restored;
    };

    return (
        <div className={`dashboard-container${sidebarOpen?'':' sidebar-collapsed'}`}>
            <div className="map-view">
                {comparison ? <AssignmentCompareMap city={city} comparison={comparison} onClose={()=>setComparison(null)} /> : (city.has_evaluation || demandRun) && !selectedRun ? (
                    <MapContainer key={`${city.id}:${city.custom_input_version || 'builtin'}:${city.input_revision || 0}`}
                        city={city}
                        demandRun={demandRun}
                        data={geoData}
                        compositeRun={compositeRun}
                        vis={vis}
                        onToggleVis={toggleVis}
                        tciSession={tciSession}
                        onSessionChange={setTciSession}
                    />
                ) : <CityPreviewMap city={city} data={geoData} run={selectedRun} />}
            </div>
            <button type="button" className="sidebar-toggle" aria-expanded={sidebarOpen} aria-controls="workspace-sidebar" onClick={()=>setSidebarOpen(value=>!value)}>{sidebarOpen?'收起操作面板':'打开操作面板'}</button>
            <div id="workspace-sidebar" className="floating-sidebar" style={{display:sidebarOpen?undefined:'none'}}>
                {booting ? <div style={{ padding: 18, color: '#dbe6ef' }}>正在检查城市与工作状态…</div> : bootError ? <div role="alert" style={{ padding: 18, color: '#ffca83' }}>{bootError}<button onClick={() => setBootRevision(value => value + 1)} style={{ display: 'block', marginTop: 12 }}>重新连接本地服务</button></div> : <SidebarPanel
                    restoredConfig={restoredConfig}
                    workspace={{...workspaceSnapshot({city,demandRun,selectedRun,compositeRun,activeTab,vis,tciSession}),...formConfigs}}
                    onConfigChange={configChanged}
                    onRestoreProject={restoreProject}
                    onCompare={setComparison}
                    onHistoricalView={viewHistorical}
                    activeTab={activeTab}
                    onTabChange={key=>{setComparison(null);setActiveTab(key);}}
                    notice={notice}
                    onNoticeClose={() => setNotice('')}
                    drafts={drafts}
                    onDraftChange={draftChanged}
                    city={city}
                    demandRun={demandRun}
                    onDemandChange={changeDemand}
                    onCityChange={changeCity}
                    compositeRun={compositeRun}
                    onCompositeRunChange={selectEvaluation}
                    selectedRun={selectedRun}
                    onRunSelect={selectRun}
                    onDataUpdate={setGeoData}
                    geoData={geoData}
                    vis={vis}
                    onToggleVis={toggleVis}
                    tciSession={tciSession}
                    onSessionChange={setTciSession}
                />}
            </div>
        </div>
    );
};

export default MainPage;
