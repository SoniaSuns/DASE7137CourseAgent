// Protocol fault injection; DOM/native submission is separately covered in Chromium.
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const clone=x=>structuredClone(x);
function harness(){
  const local={},session={login:{server:'https://agent.test',user_id:'user',device_id:'device',token:'synthetic'}};
  const storage=data=>({async get(key){if(Array.isArray(key))return Object.fromEntries(key.map(k=>[k,clone(data[k])]));return {[key]:clone(data[key])};},async set(value){assert.ok(JSON.stringify(value).length<10*1024*1024,'oversized Chrome local-storage write');Object.assign(data,clone(value));},async remove(key){delete data[key];},async clear(){for(const k of Object.keys(data))delete data[k];},async setAccessLevel(){}});
  const context={console,URL,structuredClone,AbortSignal,setTimeout,clearTimeout,chrome:{sidePanel:{async setPanelBehavior(){}},storage:{local:storage(local),session:storage(session)},runtime:{id:'fixture',onMessage:{addListener(){}}}}};
  vm.createContext(context);vm.runInContext(source,context);
  const binding={origin:'https://moodle.test',course_id:'7',actor_id:'41',tab_id:3};
  const op={id:'op1',url:'https://moodle.test/course/modedit.php?update=12',form:{signature:'same',fields:[]},changes:[]};
  const task={id:'task',device:'device',binding,command:{id:'cmd',kind:'execute',payload:{plan:[op],plan_hash:'hash'}}};
  session['grant:task']={user_id:'user',server:'https://agent.test',plan_hash:'hash',binding:clone(binding),plan:clone(task.command.payload.plan)};
  context.task=task;context.posts=0;context.authorizations=0;context.receipts=[];
  vm.runInContext(`navigate=async()=>{};bound=async()=>{};waitNavigation=()=>({promise:Promise.resolve(),stop(){}});
    call=async(_,method)=>{if(method==='snapshot')return {form:{signature:'same'}};if(method==='apply'){posts++;return {submitted:true}}};
    readback=async(_,op)=>({operation_id:op.id,status:'verified'});
    api=async(path,body)=>{if(path.endsWith('/authorize')){authorizations++;return {authorized:true}};receipts.push(body);return {}};`,context);
  return {context,local,session,task,run:code=>vm.runInContext(code,context)};
}
(async()=>{
  let checks=0;
  {
    const h=harness();h.run(`let lost=true;const originalApi=api;api=async(path,body)=>{if(path.endsWith('/receipt')&&lost){lost=false;throw Error('connection lost')}return originalApi(path,body)};`);
    await assert.rejects(h.run('processTask(task)'));
    assert.equal(h.context.posts,1);
    await h.run('processTask(task)');assert.equal(h.context.posts,1);assert.equal(h.context.receipts.length,1);checks++;
  }
  {
    const h=harness();h.run(`waitNavigation=()=>({promise:Promise.reject(Error('save outcome unknown')),stop(){}});`);
    await h.run('processTask(task)');await h.run('processTask(task)');
    assert.equal(h.context.posts,1);assert.equal(h.context.receipts[1].result.results[0].status,'uncertain');checks++;
  }
  {
    const h=harness();h.task.command.payload.plan.push({...clone(h.task.command.payload.plan[0]),id:'op2'});h.session['grant:task'].plan=clone(h.task.command.payload.plan);
    h.run(`const originalApi=api;api=async(path,body)=>{if(path.endsWith('/authorize')&&authorizations===1)throw Error('server restarted');return originalApi(path,body)};`);
    await h.run('processTask(task)');assert.equal(h.context.posts,1);assert.equal(h.context.receipts[0].result.results[1].status,'not_saved');checks++;
  }
  {
    const h=harness();let release;h.context.gate=new Promise(resolve=>release=resolve);
    h.run(`const originalApi=api;api=async(path,body)=>{if(path.endsWith('/authorize'))await gate;return originalApi(path,body)};`);
    const running=h.run('processTask(task)');
    await new Promise(resolve=>setTimeout(resolve,10));
    await assert.rejects(h.run(`handle({type:'reconcile',body:{id:'task'}})`));
    await assert.rejects(h.run(`handle({type:'logout'})`));
    h.context.other=clone(h.task);h.context.other.command.id='verify';h.context.other.command.kind='verify';
    await h.run('processTask(other)');assert.equal(h.context.receipts.length,0);
    release();await running;assert.equal(h.context.posts,1);checks++;
  }
  {
    const h=harness();h.task.command.kind='download';h.task.command.payload={url:'https://moodle.test/pluginfile.php/1/mod_resource/content/0/a.pdf',source_url:'https://moodle.test/mod/resource/view.php?id=1&forceview=1'};
    h.run(`call=async()=>({name:'large.pdf',data:'A'.repeat(44*1024*1024)});`);
    await h.run('processTask(task)');assert.equal(h.context.receipts.length,1);assert.equal(Object.keys(h.local).length,0);checks++;
  }
  {
    const h=harness();h.task.command.payload.plan[0].changes=[{name:'name',before:'Old',value:'Tampered'}];
    await h.run('processTask(task)');assert.equal(h.context.posts,0);assert.equal(h.context.authorizations,0);checks++;
  }
  console.log(JSON.stringify({passed:checks,scenarios:['lost receipt','uncertain save','server restart','concurrent recovery/logout','large download','changed approved manifest']}));
})().catch(e=>{console.error(e);process.exitCode=1});
