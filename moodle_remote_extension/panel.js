'use strict';
const $=id=>document.getElementById(id);
let view=null,selected='',refreshing=false,pending=null;
const checked=new Set();
const permissionPattern=url=>{const u=new URL(url);return u.protocol+'//'+u.hostname+'/*';};
const labels={READING:'读取你的 Moodle',PLANNING:'分析与规划',APPROVAL:'等待你的确认',EXECUTING:'在你的浏览器执行',NEEDS_READBACK:'需要核查保存结果',QUESTION:'等待补充',DONE:'已完成',PARTIAL:'明确项已处理，仍有未完成项',FAILED:'任务未完成',INTERRUPTED:'服务重启，任务已中断',CANCELLED:'已取消'};
async function send(type,body){const r=await chrome.runtime.sendMessage({type,body});if(!r?.ok)throw Error(r?.error||'插件执行器未响应。');return r.result;}
function error(e){$('error').textContent=e?.message||'';}
async function action(fn){try{error(null);await fn();await refresh();}catch(e){error(e);}}
function current(){return view?.tasks.find(t=>t.id===selected);}
function render(){
  $('tasks').replaceChildren(new Option('新任务',''));
  for(const task of view.tasks)$('tasks').add(new Option((labels[task.phase]||task.phase)+' · '+task.text.slice(0,50),task.id));
  $('tasks').value=selected;
  $('library').replaceChildren();
  for(const f of view.files){
    const row=document.createElement('label'),box=document.createElement('input'),text=document.createElement('span'),del=document.createElement('button');
    box.type='checkbox';box.checked=checked.has(f.id);box.onchange=()=>box.checked?checked.add(f.id):checked.delete(f.id);
    text.textContent=f.name+' ('+Math.ceil(f.bytes/1024)+' KB)';del.textContent='删除';del.className='secondary';del.onclick=()=>action(()=>send('delete',{id:f.id}));row.append(box,text,del);$('library').append(row);
  }
  const b=view.binding;$('binding').textContent=b?`${b.origin} · 课程 ${b.course_id} · Moodle 用户 ${b.actor_id}`:'请打开自己的 Moodle 课程主页，再连接。';
  const task=current(),own=task?.device===view.device_id;
  $('result').textContent=task?`${labels[task.phase]||task.phase}\n${task.message||''}${task.unresolved.length?'\n\n未完成：\n'+task.unresolved.join('\n\n'):''}${task.results.length?'\n\n核验：\n'+task.results.map(r=>r.status+' · '+(r.message||'')).join('\n'):''}${!own?'\n请在创建此任务的浏览器设备继续。':''}`:'选择课程和资料，然后输入操作要求。';
  $('approval').hidden=!(own&&task.phase==='APPROVAL');
  $('plan').textContent=task?.plan.map(op=>op.title+'\n'+op.changes.map(c=>`${c.label}: ${c.before||'空'} → ${c.value||'空'}`).join('\n')).join('\n\n')||'';
  $('reconcile').hidden=!(own&&['EXECUTING','NEEDS_READBACK'].includes(task.phase));
  $('cancel-task').hidden=!(own&&['PLANNING','READING','QUESTION'].includes(task.phase));
  $('send').textContent=task?.phase==='QUESTION'?'回复当前问题':'发送新任务';
}
async function refresh(){
  if(refreshing)return;refreshing=true;
  try{const settings=await send('settings');$('login').hidden=!!settings.login;$('workspace').hidden=!settings.login;
    if(!settings.login){$('connection').textContent='未登录';return;}
    view=await send('workspace');$('connection').textContent=settings.login.name+' · 已连接';render();
  }catch(e){$('connection').textContent='连接中断';error(e);}finally{refreshing=false;}
}
$('signin').onclick=()=>action(async()=>{
  const server=new URL($('server').value).origin;
  if(!await chrome.permissions.request({origins:[permissionPattern(server)]}))throw Error('需要允许插件连接你填写的服务地址。');
  await send('login',{server,name:$('name').value,password:$('password').value,invite:$('invite').value.trim()});$('password').value='';$('invite').value='';selected='';checked.clear();
});
$('logout').onclick=()=>action(async()=>{await send('logout');selected='';view=null;checked.clear();});
$('bind').onclick=()=>action(async()=>{
  const tabs=await chrome.tabs.query({active:true,currentWindow:true}),url=tabs[0]?.url;
  if(!url||!url.startsWith('https://'))throw Error('请先打开并选中自己的 Moodle 课程主页。');
  if(!await chrome.permissions.request({origins:[permissionPattern(url)]}))throw Error('需要允许插件读取和操作你选中的 Moodle 网站。');
  await send('bind');
});
$('files').onchange=()=>action(async()=>{
  for(const file of $('files').files){
    if(!file.size||file.size>32*1024*1024)throw Error('每份文件必须非空且不超过 32 MiB。');
    const data=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<data.length;i+=16384)binary+=String.fromCharCode(...data.subarray(i,i+16384));
    const result=await send('upload',{name:file.name,data:btoa(binary)});checked.add(result.id);
  }$('files').value='';
});
$('tasks').onchange=()=>{selected=$('tasks').value;render();};
$('send').onclick=()=>action(async()=>{
  const text=$('request').value.trim();if(!text)throw Error('请输入要求。');
  if(current()?.phase==='QUESTION'){await send('reply',{id:selected,text});}
  else{
    pending=pending||{text,file_ids:[...checked],request_id:crypto.randomUUID()};
    if(pending.text!==text)throw Error('上一条请求的接收结果尚不明确，请先重试同一条请求。');
    const result=await send('create',pending);selected=result.id;pending=null;
  }$('request').value='';
});
$('confirm').onclick=()=>action(()=>send('approve',{id:selected,plan_hash:current().plan_hash}));
$('cancel').onclick=$('cancel-task').onclick=()=>action(()=>send('cancel',{id:selected}));
$('reconcile').onclick=()=>action(()=>send('reconcile',{id:selected}));
send('settings').then(s=>{$('server').value=s.server||'http://127.0.0.1:18791';refresh();}).catch(error);
setInterval(refresh,2500);
