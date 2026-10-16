'use strict';
chrome.sidePanel.setPanelBehavior({openPanelOnActionClick:true}).catch(()=>{});
chrome.storage.session.setAccessLevel({accessLevel:'TRUSTED_CONTEXTS'}).catch(()=>{});
const active=new Set();
const canonical=x=>JSON.stringify(x,(_,v)=>v&&!Array.isArray(v)&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
async function config(){return (await chrome.storage.session.get('login')).login;}
function serverOrigin(value){
  const u=new URL(value);
  const octets=u.hostname.split('.').map(Number);
  const privateIPv4=octets.length===4&&octets.every(x=>Number.isInteger(x)&&x>=0&&x<=255)&&
    (octets[0]===10||(octets[0]===172&&octets[1]>=16&&octets[1]<=31)||(octets[0]===192&&octets[1]===168));
  if(u.username||u.password||u.search||u.hash||!['','/'].includes(u.pathname)||
    !(u.protocol==='https:'||(u.protocol==='http:'&&(u.hostname==='127.0.0.1'||privateIPv4))))throw Error('请输入 HTTPS 地址，或可信局域网的私有 IPv4 HTTP 地址。');
  return u.origin;
}
async function api(path,body,login){
  const c=login||await config();if(!c)throw Error('请先登录共享服务。');
  const response=await fetch(c.server+path,{method:body===undefined?'GET':'POST',headers:{'Authorization':'Bearer '+(c.token||''),...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),cache:'no-store',signal:AbortSignal.timeout(60000)});
  const result=await response.json();if(!response.ok)throw Error(result.error||'服务连接失败。');return result;
}
async function call(tabId,method,args=[]){
  await chrome.scripting.executeScript({target:{tabId},files:['moodle.js']});
  const values=await chrome.scripting.executeScript({target:{tabId},func:async(method,args)=>{
    try{return {ok:true,result:await globalThis.RemoteMoodle[method](...args)};}catch(e){return {ok:false,error:e.message};}
  },args:[method,args]});
  const answer=values[0]?.result;if(!answer?.ok)throw Error(answer?.error||'无法读取 Moodle 标签页。');return answer.result;
}
async function bound(binding){
  const deadline=Date.now()+45000;
  while((await chrome.tabs.get(binding.tab_id)).status==='loading'){
    if(Date.now()>deadline)throw Error('上一次页面加载或保存尚未结束，请稍后核查。');
    await new Promise(resolve=>setTimeout(resolve,200));
  }
  return call(binding.tab_id,'bound',[binding]);
}
function waitNavigation(tabId,timeout=45000){
  let stop;
  const promise=new Promise((resolve,reject)=>{
    let loading=false;
    const listener=(id,change)=>{if(id!==tabId)return;if(change.status==='loading')loading=true;if(loading&&change.status==='complete'){stop();resolve();}};
    const timer=setTimeout(()=>{stop();reject(Error('页面导航未完成，保存结果需要核查。'));},timeout);
    stop=()=>{clearTimeout(timer);chrome.tabs.onUpdated.removeListener(listener);};
    chrome.tabs.onUpdated.addListener(listener);
  });
  promise.catch(()=>{});
  return {promise,stop};
}
async function navigate(binding,url){
  await bound(binding);
  if(!await call(binding.tab_id,'allowed',[url,binding]))throw Error('页面不在本任务的 Moodle 读取范围内。');
  const nav=waitNavigation(binding.tab_id);
  try{await chrome.tabs.update(binding.tab_id,{url});await nav.promise;}finally{nav.stop();}
  await bound(binding);
}
function sameValue(a,b,name){
  // Moodle formats numeric grade values on save; never apply this to names/text.
  if(/^(finalgrade|grade)$/.test(name)&&/^-?\d+(\.\d+)?$/.test(a)&&/^-?\d+(\.\d+)?$/.test(b))return Number(a)===Number(b);
  return a===b;
}
async function readback(binding,op){
  await navigate(binding,op.url);
  const now=await call(binding.tab_id,'snapshot',[binding]);
  if(!now.form)throw Error('保存后的表单无法读取。');
  const values=new Map(now.form.fields.map(f=>[f.name,f.value]));
  const changed=new Map(op.changes.map(c=>[c.name,c.value]));
  const mismatch=op.form.fields.filter(f=>!sameValue(values.get(f.name)||'',changed.has(f.name)?changed.get(f.name):f.value,f.name)).map(f=>f.label);
  return {operation_id:op.id,status:mismatch.length?'mismatch':'verified',message:mismatch.length?'Readback differs: '+mismatch.join(', '):'Saved values and untouched fields checked.',observed:op.changes.map(c=>({name:c.name,value:values.get(c.name)}))};
}
async function execute(task,command,c,journalKey){
  const result={binding:task.binding,results:[]};
  const grant=(await chrome.storage.session.get('grant:'+task.id))['grant:'+task.id];
  if(command.kind==='execute' && (!grant||grant.user_id!==c.user_id||grant.server!==c.server||grant.plan_hash!==command.payload.plan_hash||
    canonical(grant.binding)!==canonical(task.binding)||canonical(grant.plan)!==canonical(command.payload.plan)))throw Error('这个浏览器尚未确认这份完全一致的 Plan。请查看最新计划。');
  for(const op of command.payload.plan){
    const opKey='operation:'+c.server+':'+c.user_id+':'+task.id+':'+op.id;
    let saved=(await chrome.storage.local.get(opKey))[opKey];
    if(command.kind==='verify'){
      try{result.results.push(await readback(task.binding,op));}catch(e){result.results.push({operation_id:op.id,status:'uncertain',message:e.message});break;}
      continue;
    }
    if(saved){
      if(saved.status==='verified'){result.results.push(saved);continue;}
      result.results.push({operation_id:op.id,status:'uncertain',message:'A previous submission may have occurred. Read back without submitting again.'});break;
    }
    let submitted=false;
    try{
      await navigate(task.binding,op.url);
      const fresh=await call(task.binding.tab_id,'snapshot',[task.binding]);
      if(fresh.form?.signature!==op.form.signature)throw Error('表单在确认后已改变，需要新的 Plan。');
      await api('/v1/tasks/'+task.id+'/authorize',{command_id:command.id,operation_id:op.id,plan_hash:command.payload.plan_hash},c);
      // Durable mark precedes every possible save, even if the worker/browser dies.
      await chrome.storage.local.set({[opKey]:{operation_id:op.id,status:'uncertain'}});
      submitted=true;
      const nav=waitNavigation(task.binding.tab_id);
      try{
        try{await call(task.binding.tab_id,'apply',[task.binding,op]);}catch(e){
          // Navigation may destroy the injected function before its response.
          // Never retry it: wait for the native submission's navigation instead.
        }
        await nav.promise;
      }finally{nav.stop();}
      const receipt=await readback(task.binding,op);
      await chrome.storage.local.set({[opKey]:receipt});result.results.push(receipt);
      if(receipt.status!=='verified')break;
    }catch(e){
      const receipt={operation_id:op.id,status:submitted?'uncertain':'not_saved',message:e.message};
      await chrome.storage.local.set({[opKey]:receipt});result.results.push(receipt);break;
    }
  }
  return result;
}
async function processTask(task){
  const c=await config(),command=task.command;
  if(!c||!command||task.device!==c.device_id)return;
  const jobKey=c.server+':'+c.user_id+':'+task.id+':'+command.id;
  if(active.size)return;active.add(jobKey);
  const journalKey='receipt:'+jobKey;
  try{
    let stored=(await chrome.storage.local.get(journalKey))[journalKey];
    if(!stored){
      let result,route='receipt';
      if(command.kind==='read'){
        try{await navigate(task.binding,command.payload.url);result={binding:task.binding,snapshot:await call(task.binding.tab_id,'snapshot',[task.binding])};}
        catch(e){result={binding:task.binding,error:e.message};}
      }else if(command.kind==='download'){
        route='download';
        try{await navigate(task.binding,command.payload.source_url);result={binding:task.binding,...await call(task.binding.tab_id,'download',[task.binding,command.payload.url])};}
        catch(e){result={binding:task.binding,error:e.message};}
      }else if(['execute','verify'].includes(command.kind)){
        try{result=await execute(task,command,c,journalKey);}
        catch(e){result={binding:task.binding,results:[],error:e.message};}
      }else throw Error('不支持的浏览器命令。');
      stored={route,body:route==='download'?{command_id:command.id,...result}:{command_id:command.id,result}};
      // Downloads are read-only and safe to repeat. Never fill Chrome's local
      // storage quota with a 32 MiB file encoded as base64; only save receipts
      // whose replay could otherwise cause another write.
      if(route!=='download')await chrome.storage.local.set({[journalKey]:stored});
    }
    // Uploading a cached receipt is safe; it does not repeat the browser operation.
    await api('/v1/tasks/'+task.id+'/'+stored.route,stored.body,c);
    await chrome.storage.local.remove(journalKey);
  }finally{active.delete(jobKey);}
}
async function handle(message){
  const body=message.body||{};
  if(message.type==='settings'){
    const prefs=await chrome.storage.local.get(['server','device_id']);
    if(!prefs.device_id){prefs.device_id=crypto.randomUUID();await chrome.storage.local.set({device_id:prefs.device_id});}
    return {...prefs,login:await config()};
  }
  if(message.type==='login'){
    if(active.size)throw Error('请等待当前浏览器操作结束后切换账号。');
    const server=serverOrigin(body.server),prefs=await handle({type:'settings'});
    const login=await api('/v1/'+(body.invite?'register':'login'),{name:body.name,password:body.password,invite:body.invite,device_id:prefs.device_id},{server});
    await chrome.storage.session.clear();await chrome.storage.session.set({login:{server,...login}});await chrome.storage.local.set({server});return {name:login.name};
  }
  if(message.type==='logout'){
    if(active.size)throw Error('请等待当前浏览器操作结束；保存结果尚未确认时不要切换账号。');
    await api('/v1/logout',{});await chrome.storage.session.clear();return {ok:true};
  }
  if(message.type==='bind'){
    if(active.size)throw Error('请等待当前浏览器操作结束后切换课程。');
    const tabs=await chrome.tabs.query({active:true,currentWindow:true}),tab=tabs[0];
    if(!tab)throw Error('请先打开自己的 Moodle 课程标签页。');
    const identity=await call(tab.id,'identity');const binding={...identity,tab_id:tab.id};
    await chrome.storage.session.set({binding});return binding;
  }
  if(message.type==='workspace'){
    const workspace=await api('/v1/workspace');
    for(const task of workspace.tasks)if(task.command)processTask(task).catch(()=>{});
    return {...workspace,binding:(await chrome.storage.session.get('binding')).binding};
  }
  if(message.type==='create'){
    const binding=(await chrome.storage.session.get('binding')).binding;
    if(!binding)throw Error('请先连接自己的 Moodle 课程。');await bound(binding);
    return api('/v1/tasks',{...body,binding});
  }
  if(message.type==='approve'){
    const c=await config();const workspace=await api('/v1/workspace');
    const task=workspace.tasks.find(t=>t.id===body.id);
    if(!task||task.device!==c.device_id||task.phase!=='APPROVAL'||task.plan_hash!==body.plan_hash)throw Error('Plan 已改变，请重新查看。');
    await bound(task.binding);
    await chrome.storage.session.set({['grant:'+task.id]:{user_id:c.user_id,server:c.server,plan_hash:task.plan_hash,plan:task.plan,binding:task.binding}});
    return api('/v1/tasks/'+task.id+'/approve',{plan_hash:task.plan_hash});
  }
  if(['cancel','reply','reconcile'].includes(message.type)){
    if(message.type==='reconcile'&&active.size)throw Error('浏览器操作仍在进行，请等待它结束后再核查。');
    return api('/v1/tasks/'+body.id+'/'+message.type,body);
  }
  if(message.type==='upload')return api('/v1/files',body);
  if(message.type==='delete')return api('/v1/files/'+body.id+'/delete',{});
  throw Error('未知请求。');
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(sender.id!==chrome.runtime.id||sender.url!==chrome.runtime.getURL('panel.html')){reply({ok:false,error:'Only the extension panel may issue commands.'});return;}
  handle(message).then(result=>reply({ok:true,result}),error=>reply({ok:false,error:error.message}));return true;
});
