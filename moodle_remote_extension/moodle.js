/* Bundled typed DOM adapter. Never evaluates model-supplied JavaScript. */
(() => {
  'use strict';
  const secret = /sesskey|token|password|secret|csrf|auth/i;
  const canonical = x => JSON.stringify(x, (_, v) => v && !Array.isArray(v) && typeof v === 'object'
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
  const hash = async x => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(x))))).map(b=>b.toString(16).padStart(2,'0')).join('');
  function identity() {
    if (location.protocol !== 'https:' || document.querySelector('form#login,#loginbtn')) throw Error('请先在自己的 Chrome 中登录 Moodle。');
    const courses=[...document.body.classList].filter(c=>/^course-\d+$/.test(c)).map(c=>c.slice(7));
    const profile=document.querySelector('.usermenu a[href*="/user/profile.php"], .usernav a[href*="/user/profile.php"]');
    const actor=profile ? new URL(profile.href).searchParams.get('id') : '';
    if(courses.length!==1 || !/^[1-9]\d*$/.test(actor||'') || courses[0]==='1') throw Error('无法确认当前 Moodle 用户和课程，请打开课程主页。');
    return {origin:location.origin,course_id:courses[0],actor_id:actor};
  }
  function bound(binding) {
    const live=identity();
    for(const k of ['origin','course_id','actor_id']) if(String(binding[k])!==live[k]) throw Error('Moodle 登录用户或课程已改变，操作已停止。');
    return live;
  }
  function allowed(raw,b) {
    let u;try{u=new URL(raw,location.href);}catch{return null;}
    if(u.origin!==b.origin || u.username || u.password || u.hash) return null;
    const q=u.searchParams,keys=[...q.keys()];
    if(new Set(keys).size!==keys.length) return null;
    const subset=names=>keys.every(k=>names.includes(k));
    if(u.pathname==='/course/view.php' && subset(['id','section']) && q.get('id')===b.course_id) return u.href;
    if(['/user/index.php','/grade/report/grader/index.php','/grade/report/singleview/index.php'].includes(u.pathname) && subset(['id','page','perpage','group','sifirst','silast','tifirst','tilast','item','itemid']) && q.get('id')===b.course_id) return u.href;
    if(u.pathname==='/course/modedit.php' && keys.length===1 && /^\d+$/.test(q.get('update')||'')) return u.href;
    if(u.pathname==='/course/editsection.php' && keys.length===1 && /^\d+$/.test(q.get('id')||'')) return u.href;
    if(u.pathname==='/grade/edit/tree/grade.php' && keys.length===2 && subset(['courseid','id']) && q.get('courseid')===b.course_id && /^\d+$/.test(q.get('id')||'')) return u.href;
    if(/^\/mod\/(resource|folder|page|book|assign|quiz|url|choice)\/view.php$/.test(u.pathname) && subset(['id','forceview']) && /^\d+$/.test(q.get('id')||'')) {
      if(u.pathname==='/mod/resource/view.php')u.searchParams.set('forceview','1');
      if(!u.searchParams.has('forceview') || u.searchParams.get('forceview')==='1')return u.href;
    }
    return null;
  }
  function downloadAllowed(raw,b) {
    const u=new URL(raw,location.href);
    const contexts=[...document.body.classList].filter(c=>/^context-\d+$/.test(c)).map(c=>c.slice(8));
    const parts=decodeURIComponent(u.pathname).split('/');
    return u.origin===b.origin && !u.username && !u.password && !u.hash && parts[1]==='pluginfile.php' && contexts.includes(parts[2]) && ['mod_resource','mod_folder','mod_page','mod_book'].includes(parts[3]) && parts[4]==='content' && !parts.some(p=>p==='.'||p==='..'||/[\\\x00]/.test(p)) && [...u.searchParams.keys()].every(k=>k==='forcedownload');
  }
  function fieldValue(el) {return el.type==='checkbox'?String(el.checked):el.value;}
  function editable(form) {
    const all=[...form.querySelectorAll('input,select,textarea')].filter(e=>e.name && !secret.test(e.name) && !['hidden','password','submit','button','file','radio','reset','image'].includes(e.type) && !e.readOnly);
    const counts={};for(const e of all)counts[e.name]=(counts[e.name]||0)+1;
    return all.filter(e=>counts[e.name]===1 && !e.multiple);
  }
  async function formSnapshot() {
    const buttons=[...document.querySelectorAll('form button,form input[type=submit]')].filter(e=>['submitbutton','submitbutton2','savechanges'].includes(e.name)||['id_submitbutton','id_submitbutton2'].includes(e.id));
    const forms=[...new Set(buttons.filter(b=>!b.disabled).map(b=>b.form))];
    if(forms.length!==1)return null;
    const form=forms[0], action=new URL(form.action,location.href);
    if(action.origin!==location.origin || form.method.toLowerCase()!=='post')return null;
    const fields=editable(form).map(e=>({name:e.name,type:e.tagName==='SELECT'?'select':e.type==='checkbox'?'checkbox':e.tagName==='TEXTAREA'?'textarea':'text',
      label:(e.labels?.[0]?.textContent||e.name).replace(/\s+/g,' ').trim().slice(0,200),value:fieldValue(e),disabled:e.disabled,
      options:e.tagName==='SELECT'?[...e.options].map(o=>({value:o.value,label:o.textContent,disabled:o.disabled})):[]}));
    const hidden=[...form.querySelectorAll('input[type=hidden]')].filter(e=>!secret.test(e.name)).map(e=>[e.name,e.value]);
    const signature=await hash({url:location.href,action:action.href,method:'post',hidden,fields});
    return {fields,signature};
  }
  async function snapshot(binding) {
    bound(binding);
    if(!allowed(location.href,binding))throw Error('当前页面不属于允许读取的课程页面。');
    const core=document.querySelector('#region-main')||document.body,links=new Map();
    for(const node of core.querySelectorAll('a[href],object[data],iframe[src]')) {
      const raw=node.href||node.data||node.src;
      const url=allowed(raw,binding),title=(node.textContent||'').replace(/\s+/g,' ').trim().slice(0,200);
      if(url)links.set(url,{url,title,kind: /\/(modedit|editsection|grade)\.php$/.test(new URL(url).pathname)?'form':'page'});
      else if(downloadAllowed(raw,binding)) {const u=new URL(raw,location.href);links.set(u.href,{url:u.href,title:title||decodeURIComponent(u.pathname.split('/').pop()),kind:'download'});}
    }
    if(new URL(location.href).pathname==='/course/view.php') {
      for(const node of core.querySelectorAll('[id^="module-"]')) {
        const id=node.id.slice(7);if(!/^\d+$/.test(id))continue;
        const url=binding.origin+'/course/modedit.php?update='+id;
        links.set(url,{url,title:'Edit '+(node.querySelector('.instancename,.activityname')?.textContent||node.textContent).trim().slice(0,200),kind:'form'});
      }
      for(const [path,title] of [['/user/index.php','Participants'],['/grade/report/grader/index.php','Grades']]) {
        const url=binding.origin+path+'?id='+binding.course_id;links.set(url,{url,title,kind:'page'});
      }
    }
    const clone=core.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,template,input,textarea,select,button').forEach(n=>n.remove());
    const text=clone.textContent.replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
    if(text.length>300000 || links.size>1000)throw Error('页面过大，请缩小页面范围或分页。');
    return {url:location.href,title:document.querySelector('h1')?.textContent.trim()||document.title,text,links:[...links.values()],form:await formSnapshot()};
  }
  async function apply(binding,operation) {
    bound(binding);
    if(location.href!==operation.url || !allowed(operation.url,binding))throw Error('保存目标页面已改变。');
    const current=await formSnapshot();
    if(!current || current.signature!==operation.form.signature)throw Error('表单在确认后已改变，请重新规划。');
    const button=[...document.querySelectorAll('form button,form input[type=submit]')].find(e=>(['submitbutton','submitbutton2','savechanges'].includes(e.name)||['id_submitbutton','id_submitbutton2'].includes(e.id))&&!e.disabled);
    const form=button.form,controls=editable(form),names=new Set();
    for(const change of operation.changes) {
      const el=controls.find(e=>e.name===change.name),field=current.fields.find(f=>f.name===change.name);
      if(!el || el.disabled || names.has(change.name) || field.value!==change.before || typeof change.value!=='string')throw Error('修改字段不再与已确认 Plan 一致。');
      if(field.type==='checkbox'&&!['true','false'].includes(change.value))throw Error('复选框值不正确。');
      if(field.type==='select'&&!field.options.some(o=>o.value===change.value&&!o.disabled))throw Error('选项已失效。');
      names.add(change.name);
    }
    for(const change of operation.changes) {
      const el=controls.find(e=>e.name===change.name);
      if(el.type==='checkbox')el.checked=change.value==='true';else el.value=change.value;
      el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));
    }
    if(!form.checkValidity())throw Error('Moodle 表单校验未通过，没有提交保存。');
    // One native form submission. The extension journal is persisted BEFORE this call.
    form.requestSubmit(button);
    return {submitted:true};
  }
  async function download(binding,url) {
    bound(binding);
    if(!downloadAllowed(url,binding))throw Error('文件链接不属于当前已授权 Moodle 页面。');
    const response=await fetch(url,{credentials:'same-origin',redirect:'error',cache:'no-store'});
    if(!response.ok || Number(response.headers.get('content-length')||0)>32*1024*1024)throw Error('无法下载文件或文件超过 32 MiB。');
    const data=new Uint8Array(await response.arrayBuffer());
    if(!data.length||data.length>32*1024*1024)throw Error('下载文件为空或超过 32 MiB。');
    let binary='';for(let i=0;i<data.length;i+=16384)binary+=String.fromCharCode(...data.subarray(i,i+16384));
    return {name:decodeURIComponent(new URL(url).pathname.split('/').pop()),data:btoa(binary)};
  }
  globalThis.RemoteMoodle={identity,bound,snapshot,apply,download,allowed,canonical,hash};
})();
