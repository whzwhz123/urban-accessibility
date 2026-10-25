import React, { useState } from 'react';
import { readJson } from '../../utils/localApi';
export default function RegisterCity({ onRegistered }) {
  const [values,setValues]=useState({id:'',name:'',province:'',city_code:'',longitude:'',latitude:''});
  const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');
  const submit=async()=>{setBusy(true);setMessage('');try{const value=await fetch('/api/cities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)}).then(readJson);onRegistered(value.city);}catch(error){setMessage(error.message);}finally{setBusy(false);}};
  return <details style={{padding:10,border:'1px solid #ffffff22',borderRadius:8,marginBottom:12,color:'#c8d7e4',fontSize:12}}><summary>注册目录中没有的新城市</summary>
    <p>注册后上传该城市的线网与POI，不要求先放入全国数据集。经纬度填写WGS84。</p>
    {[['id','英文城市ID'],['name','城市名称'],['province','省份（可选）'],['city_code','高德城市编码（可选）'],['longitude','中心经度'],['latitude','中心纬度']].map(([key,label])=><label key={key} style={{display:'block',marginTop:6}}>{label}<input aria-label={label} value={values[key]} disabled={busy} onChange={e=>setValues({...values,[key]:e.target.value})} style={{width:'100%'}} /></label>)}
    <button onClick={submit} disabled={busy||!values.id||!values.name||!values.longitude||!values.latitude}>{busy?'正在注册':'注册并选择此城市'}</button><div role="status">{message}</div>
  </details>;
}
