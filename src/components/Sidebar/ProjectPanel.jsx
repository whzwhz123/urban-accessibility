import React, { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Select } from 'antd';
import { readJson } from '../../utils/localApi';

// Export: JSON -> ZIP. Import: raw ZIP -> staged project. No automatic restore.
const ProjectPanel = ({ city, demandRun, selectedRun, compositeRun, workspace, onImported, onRestore }) => {
  const [name, setName] = useState('');
  const [includeInputs, setIncludeInputs] = useState(false);
  const [includeResults, setIncludeResults] = useState(false);
  const [restoreInputs, setRestoreInputs] = useState(false);
  const [projects, setProjects] = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const currentCity = useRef(city?.id);
  const operation = useRef(0);
  currentCity.current = JSON.stringify([city?.id, city?.custom_input_version]);

  useEffect(() => {
    const token = ++operation.current;
    setRestoreInputs(false); setSelected(null); setProjects([]); setResult(null); setError(''); setBusy(false);
    const controller = new AbortController();
    if (city?.id) fetch(`/api/cities/${encodeURIComponent(city.id)}/projects`, { signal: controller.signal })
      .then(readJson).then(value => { if (!controller.signal.aborted && operation.current === token) setProjects(value.projects || []); })
      .catch(failure => { if (!controller.signal.aborted && operation.current === token && failure.name !== 'AbortError') setError(failure.message); });
    return () => controller.abort();
  }, [city?.id, city?.custom_input_version]);

  const perform = async action => {
    const ownCity = currentCity.current;
    const token = ++operation.current;
    const isCurrent = () => currentCity.current === ownCity && operation.current === token;
    setBusy(true); setError('');
    try { await action(isCurrent); }
    catch (failure) { if (isCurrent()) setError(failure.message); }
    finally { if (isCurrent()) setBusy(false); }
  };

  const save = () => perform(async isCurrent => {
    const response = await fetch('/api/projects/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ city_id: city.id, include_inputs: includeInputs, include_results: includeResults,
        payload: { name: name.trim(), workspace, demand_run: demandRun, assignment_run: selectedRun,
          evaluation_run: compositeRun, demand_config: workspace?.demand_config || demandRun?.parameters,
          assignment_parameters: workspace?.assignment_parameters || selectedRun?.parameters,
          analysis_parameters: workspace?.analysis_parameters || compositeRun?.workflow_parameters,
          assignment_scenario:workspace?.assignment_scenario,
          display_settings: { vis: workspace?.vis, active_tab: workspace?.active_tab, tci_session: workspace?.tci_session },
          version_references: { input_version: city.custom_input_version || null,
            demand_id: demandRun?.run_id || null, assignment_id: selectedRun?.run_id || null,
            evaluation_id: compositeRun?.run_id || null } } }),
    });
    if (!response.ok) { await readJson(response); return; }
    const blob = await response.blob();
    if (!isCurrent()) return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `${city.id}_project.zip`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setResult({ message: '已发起工程文件下载。是否完整恢复以导入后的依赖清单为准。' });
  });

  const openFile = file => {
    if (!file) return;
    perform(async isCurrent => {
      const imported = await fetch('/api/projects/import', {
        method: 'POST', headers: { 'Content-Type': 'application/zip' }, body: file,
      }).then(readJson);
      if (!isCurrent()) return;
      setResult(imported);setProjects(previous=>[imported,...previous.filter(row=>row.project_id!==imported.project_id)]);setSelected(imported.project_id);
      await onImported?.(imported);
      if (isCurrent()){const restored=await onRestore?.(imported,{restoreInputs});if(isCurrent() && restored)setResult(restored);}
    });
  };
  const openSaved = () => perform(async isCurrent => {
    const project = projects.find(item => item.project_id === selected);
    if (project && isCurrent()) { setResult(project); const restored=await onRestore?.(project,{restoreInputs});if(isCurrent() && restored)setResult(restored); }
  });

  return <div style={{ marginBottom: 16, padding: 10, color: '#c8d7e4', fontSize: 12, lineHeight: 1.8 }}>
    <strong>保存与打开工程</strong>
    <div>默认只保存配置和版本引用。跨电脑还原需附带当前城市输入与结果，缺失依赖会明确提示。</div>
    <Input aria-label="工程名称" placeholder="工程名称（可选）" value={name} onChange={event => setName(event.target.value)} disabled={busy} />
    <Checkbox checked={includeInputs} onChange={event => setIncludeInputs(event.target.checked)} disabled={busy}>附带当前城市输入</Checkbox>
    <Checkbox checked={includeResults} onChange={event => setIncludeResults(event.target.checked)} disabled={busy}>附带所选结果</Checkbox>
    <div><Button size="small" onClick={save} disabled={!city?.id || busy}>保存工程文件</Button></div>
    <Checkbox checked={restoreInputs} onChange={event => setRestoreInputs(event.target.checked)} disabled={busy}>恢复工程输入（注册缺失城市并启用新输入版本，保留既有数据）</Checkbox>
    <label style={{ display: 'block', marginTop: 8 }}>手动打开工程文件（ZIP）
      <input type="file" accept=".zip,application/zip" disabled={busy || !onRestore}
        onChange={event => { openFile(event.target.files?.[0]); event.target.value = ''; }} />
    </label>
    <Select aria-label="已导入工程" placeholder="选择已导入工程" value={selected || undefined} disabled={busy}
      style={{ width: '100%', marginTop: 8 }} onChange={setSelected}
      options={projects.map(item => ({ value: item.project_id, label: item.payload?.name || item.project_id }))} />
    <Button size="small" onClick={openSaved} disabled={busy || !selected || !onRestore}>手动恢复所选工程</Button>
    <div>不会自动切换启动城市；不导入可执行模型断点，不启动计算或采集。</div>
    {error && <Alert type="error" message={error} />}
    {result && <Alert type={result.input_status==='failed' || result.missing_dependencies?.length || result.restore_warnings?.length ? 'warning' : 'info'}
      message={result.message || (result.missing_dependencies?.length ? '工程已打开，但依赖不完整' : '工程附件已导入，仍需校验历史版本与签名')}
      description={[...(result.missing_dependencies || []),...(result.restore_warnings || [])].map((item,index)=><div key={index}>{item.path || item.reference}：{item.message || item.reason}</div>)} />}
  </div>;
};

export default ProjectPanel;
