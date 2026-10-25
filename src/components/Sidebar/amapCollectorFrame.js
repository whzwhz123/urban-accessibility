// Isolate AMap SDK and credentials from the main map; credentials remain in memory.
export const collectorFrame = `<!doctype html><meta charset="utf-8"><script>
let stopped = false;
let batch = [];
let startedAt = Date.now();
function sessionNow() { const hour=Number(new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',hour:'2-digit',hour12:false}).format(new Date()))%24;return hour>=7&&hour<9?'morning':hour>=9&&hour<17?'midday':hour>=17&&hour<19?'evening':'snapshot'; }
async function flush(config) {
  if(!batch.length)return;const rows=batch;batch=[];
  const payload={metadata:{city_id:config.city_id,demand_run_id:config.demand_run_id},[config.kind==='isochrone'?'features':'records']:rows};
  const response=await fetch('/api/cities/'+config.city_id+'/collection-runs/'+config.server_job_id+'/chunks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  if(!response.ok){batch.unshift(...rows);throw new Error('采集批次未保存，已停止；恢复任务后继续');}
}
async function record(config,row) { batch.push(row);if(batch.length>=10 || Date.now()-startedAt>30000){await flush(config);startedAt=Date.now();} }
async function complete(config,value) { await flush(config);const response=await fetch('/api/cities/'+config.city_id+'/collection-runs/'+config.server_job_id+'/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({metadata:value.metadata})});const result=await response.json();if(!response.ok)throw new Error(result.error||'采集汇总失败');send('complete',{...value,stored_result:result}); }
const origin = parent.location.origin;
const send = (type, value) => parent.postMessage({channel:'urban-amap', type, value}, origin);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
window.addEventListener('unhandledrejection', e => send('error', e.reason?.message || '采集脚本异常'));
function search(service, start, end) {
  return new Promise(resolve => {
    let done=false;
    const finish=value=>{if(!done){done=true;clearTimeout(timer);resolve(value);}};
    const timer=setTimeout(()=>finish({status:'timeout'}),45000);
    service.search(new AMap.LngLat(...start),new AMap.LngLat(...end),(status,result)=>finish({status,result}));
  });
}
async function navigation(config) {
  await new Promise(resolve=>AMap.plugin(['AMap.Transfer','AMap.Driving'],resolve));
  const bus=new AMap.Transfer({city:config.city_name,cityd:config.city_name,policy:AMap.TransferPolicy.NO_SUBWAY,nightflag:false});
  const car=new AMap.Driving({policy:AMap.DrivingPolicy.LEAST_TIME});
  const records=[];let requests=0,cacheHits=0;
  const db=await new Promise((resolve,reject)=>{
    const request=indexedDB.open('urban-navigation-v1',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('records',{keyPath:'id'});
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
  });
  const storage=(mode,action)=>new Promise((resolve,reject)=>{
    const transaction=db.transaction('records',mode);let request=action(transaction.objectStore('records'));
    transaction.oncomplete=()=>resolve(request.result);transaction.onerror=()=>reject(transaction.error);
  });
  // Only reuse a contemporaneous 30-minute snapshot, never historic peak traffic.
  const bucket=Math.floor(Date.now()/1800000);
  for(const point of config.points){
    if(stopped)break;
    if(config.session!=='snapshot'&&sessionNow()!==config.session){stopped=true;break;}
    if(requests+2>config.request_budget){stopped=true;break;}
    if(new Set(config.completed_keys||[]).has(point.pair_id)) { cacheHits++;send('progress',{done:records.length+cacheHits,total:config.points.length,requests,cache_hits:cacheHits});continue; }
    const id=config.collection_id+':'+bucket+':'+point.pair_id;
    const previous=await storage('readonly',store=>store.get(id));
    if(!config.force_refresh && previous?.record.status==='ok'){records.push(previous.record);cacheHits++;}
    else {
      const start=[point.gcj_lon,point.gcj_lat],end=point.destination_gcj02;
      requests++;const transit=await search(bus,start,end);await sleep(config.interval_ms);
      if(stopped)break;
      requests++;const driving=await search(car,start,end);await sleep(config.interval_ms);
      const plans=(transit.result?.plans||[]).filter(plan=>Number(plan.time)>0 && plan.segments?.some(segment=>segment.transit_mode==='BUS') && plan.segments.every(segment=>['WALK','BUS'].includes(segment.transit_mode)));
      plans.sort((a,b)=>a.time-b.time);const plan=plans[0];
      const routes=(driving.result?.routes||[]).filter(route=>Number(route.time)>0);routes.sort((a,b)=>a.time-b.time);
      const record={pair_id:point.pair_id,grid_id:point.grid_id,origin_wgs84:[point.wgs_lon,point.wgs_lat],destination_wgs84:point.destination_wgs84,session:config.session==='snapshot'?'snapshot':sessionNow(),
        collected_at:new Date().toISOString(),bus_status:transit.status,car_status:driving.status,status:plan&&routes.length?'ok':([transit.status,driving.status].every(value=>['complete','no_data'].includes(value))?'no_route':'api_error'),
        bus_error:transit.status==='error'?String(transit.result?.info||transit.result||'service_error').slice(0,100):null,car_error:driving.status==='error'?String(driving.result?.info||driving.result||'service_error').slice(0,100):null,
        bus_time:plan?plan.time/60:null,car_time:routes.length?routes[0].time/60:null,transfer:plan?Math.max(0,plan.segments.filter(s=>s.transit_mode==='BUS').length-1):null};
      records.push(record);if (/LIMIT|INVALID_USER|SCODE|DENIED|AUTH/i.test((record.bus_error||'')+' '+(record.car_error||''))) stopped=true;await storage('readwrite',store=>store.put({id,record}));
    }
    await record(config,records[records.length-1]);
    send('progress',{done:records.length,total:config.points.length,requests,cache_hits:cacheHits});
  }
  db.close();
  await complete(config,{metadata:{city_id:config.city_id,demand_run_id:config.demand_run_id,source:'AMap.Transfer + AMap.Driving',
    policy:'NO_SUBWAY / LEAST_TIME',session:config.session,time_basis:config.session==='snapshot'?'live_snapshot':'observed_in_named_time_window',snapshot_bucket:bucket,sampling:config.sampling,subset_only:true,
    requested_pairs:config.points.length,valid_pairs:records.filter(row=>row.status==='ok').length,requests,cache_hits:cacheHits,stopped},records});
}
window.addEventListener('message',async event=>{
  if(event.source!==parent||event.origin!==origin||event.data.channel!=='urban-amap')return;
  if(event.data.type==='stop'){stopped=true;window.urbanArrivalCollector?.stop();return;}
  if(event.data.type!=='start')return;
  try {
    const {key,security,config}=event.data.value;
    window._AMapSecurityConfig={securityJsCode:security};
    await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('高德SDK加载超时，请检查网络和JS API凭证')),30000);
      const tag=document.createElement('script');tag.src='https://webapi.amap.com/maps?v=2.0&key='+encodeURIComponent(key);
      tag.onload=()=>{clearTimeout(timer);resolve();};tag.onerror=()=>{clearTimeout(timer);reject(new Error('高德SDK加载失败'));};document.head.append(tag);
    });
    if(config.kind==='tci' && config.session!=='snapshot' && sessionNow()!==config.session)throw new Error('当前时间不属于所选时段；请启用等待时段采集，不得伪造历史时段');
    if(stopped){send('error','采集已停止，尚无完成记录');return;}
    if(config.kind==='tci') await navigation(config);
    else {
      window.urbanArrivalProgress=value=>send('progress',value);
      window.urbanArrivalRecord=feature=>record(config,feature);
      window.urbanArrivalFlush=()=>flush(config);
      window.urbanArrivalComplete=value=>complete(config,value).catch(error=>send('error',error.message));
      const response=await fetch('/api/amap/arrival-collector');
      if(!response.ok)throw new Error('无法读取等时圈采集程序');
      const tag=document.createElement('script');tag.textContent=(await response.text()).replace('__ARRIVAL_CONFIG__',JSON.stringify(config));document.head.append(tag);
    }
  }catch(error){send('error',error.message);}
});
send('ready',null);
<\/script>`;
