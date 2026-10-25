import React,{useEffect,useState,useRef} from 'react';
import {Alert,Button,Select} from 'antd';
import {readJson} from '../../utils/localApi';

// File inspection/mapping/upload lives in DataIntakePanel; this controls versions only.
export default function CustomInputsPanel({city,onImported}){
  const [versions,setVersions]=useState([]),[selected,setSelected]=useState('builtin');
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const current=useRef(city);current.current=city;
  useEffect(()=>{
    const controller=new AbortController();setVersions([]);setError('');setSelected(city?.custom_input_version || 'builtin');
    if(city?.id)fetch(`/api/cities/${city.id}/custom-inputs`,{signal:controller.signal}).then(readJson).then(value=>setVersions(value.versions || [])).catch(failure=>{if(failure.name!=='AbortError')setError(failure.message);});
    return()=>controller.abort();
  },[city?.id,city?.custom_input_version]);
  const activate=async()=>{
    const own=city;setBusy(true);setError('');
    try{
      const result=await fetch(`/api/cities/${own.id}/custom-inputs/activate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:selected==='builtin'?null:selected})}).then(readJson);
      if(current.current.id===own.id)onImported?.(result);
    }catch(failure){if(current.current.id===own.id)setError(failure.message);}finally{setBusy(false);}
  };
  return <section style={{padding:10,marginBottom:12,color:'#c8d7e4',fontSize:12,lineHeight:1.8}}>
    <strong>计算输入版本</strong>
    <Select aria-label="城市计算数据版本" style={{width:'100%'}} value={selected} onChange={setSelected} disabled={busy}
      options={[{value:'builtin',label:'原始本地城市数据'},...versions.map(row=>({value:row.version,disabled:row.status!=='completed',label:`${row.version} · ${row.counts?.poi ?? '?'} POI`}))]} />
    <Button size="small" disabled={busy || selected===(city?.custom_input_version || 'builtin')} onClick={activate}>切换数据源</Button>
    <div>切换后重新准备数据；旧需求与实验仍保留，不会混用。</div>
    {city?.id && <a href={`/api/cities/${city.id}/custom-input-template`} download>下载标准数据模板</a>}
    {error && <Alert type="error" message={error} />}
  </section>;
}
