"use strict";
const $ = id => document.getElementById(id);
const labels = {IDLE:"可以开始新任务",CONNECTING:"正在连接",PLANNING:"正在检查与规划",SCOPE:"等待选择范围",QUESTION:"等待补充信息",APPROVAL:"请查看 Plan 并确认",APPROVED:"准备执行",EXECUTING:"正在执行并核验",NEEDS_READBACK:"正在核实上次保存",DONE:"任务完成",PARTIAL:"已完成明确项，其余已列出",FAILED:"任务未完成，请查看原因",INTERRUPTED:"任务已中断，可通过任务编号继续",CANCELLED:"已取消",EXPIRED:"请重新获取计划"};
let config, state, inFlight=false, refreshing=false, lastMessages="";
let pending=JSON.parse(sessionStorage.getItem("pending")||"null");
$("request").value=sessionStorage.getItem("draft")||"";
function error(text){$("error").textContent=text;$("error").hidden=!text;}
function busy(value){inFlight=value;$("send").disabled=value;$("confirm").disabled=value;$("reject").disabled=value;$("request").readOnly=Boolean(pending);$("send").textContent=pending?"重试发送":"发送 ↑";}
async function api(path,body){
  const response=await fetch(config.base_url+path,{method:body?"POST":"GET",headers:{"Authorization":"Bearer "+config.token,...(body?{"Content-Type":"application/json"}:{})},body:body?JSON.stringify(body):undefined,cache:"no-store",signal:AbortSignal.timeout(8000)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||"连接失败");return data;
}
function render(view){
  state=view;$("connection").textContent="已连接";$("connection").className="pill online";
  $("phase").textContent=labels[view.phase]||view.phase;$("task-id").textContent=view.task_id||"";
  const notes=[];
  if(view.other_task_active)notes.push("另一入口有任务进行中，新请求会排队。");
  else if(view.pending_messages||view.queued)notes.push("请求已接收，等待处理。");
  if(view.interrupted_messages)notes.push("服务重启前的待处理请求未自动重放，请检查后重新提交。");
  $("notice").textContent=notes.join(" ");$("notice").hidden=!notes.length;
  const signature=view.messages.map(m=>m.id).join("|");
  if(signature!==lastMessages){
    const bottom=$("messages").scrollHeight-$("messages").scrollTop-$("messages").clientHeight<100;
    $("messages").querySelectorAll(".message").forEach(m=>m.remove());
    $("welcome").hidden=view.messages.length>0;
    for(const m of view.messages){
      const box=document.createElement("article");box.className="message "+m.role+(m.kind==="approval"?" approval-message":"")+(m.kind==="error"?" error-message":"");box.dataset.messageId=m.id;
      const who=document.createElement("p");who.className="who";who.textContent=m.role==="user"?"YOU":"MOODLE";
      const text=document.createElement("div");text.className="body";text.textContent=m.text;box.append(who,text);$("messages").append(box);
    }
    if(bottom||!lastMessages)$("messages").scrollTop=$("messages").scrollHeight;
    lastMessages=signature;
  }
  // Show confirmation only alongside the actual, complete delivered plan.
  const visiblePlan=view.approval&&view.messages.some(m=>m.id===view.approval.context.message_id&&m.kind==="approval");
  $("approval").hidden=!visiblePlan;
  if(!inFlight&&pending&&view.messages.some(m=>m.id==="user-"+pending.id))finishSend();
}
function finishSend(){pending=null;sessionStorage.removeItem("pending");$("request").value="";sessionStorage.removeItem("draft");busy(inFlight);error("");}
async function refresh(){
  if(!config||refreshing)return;refreshing=true;
  try{render(await api("/v1/state"));if(!pending)error("");}
  catch(e){$("connection").textContent="未连接";$("connection").className="pill";$("phase").textContent="请确认本机 course agent 正在运行";$("approval").hidden=true;state=null;error("暂时无法连接。输入会保留，可点击重新连接。");}
  finally{refreshing=false;}
}
async function send(text,context){
  if(inFlight||!config)return;
  if(!pending){if(!text.trim())return;pending={id:crypto.randomUUID(),text:text.trim(),context:context||null};sessionStorage.setItem("pending",JSON.stringify(pending));}
  busy(true);error("");
  try{const result=await api("/v1/messages",pending);finishSend();if(result.status==="interrupted")error("这条请求在重启时中断，请重新发送。");await refresh();}
  catch(e){error("发送结果未确认，请重试；相同消息不会重复提交。");}
  finally{busy(false);}
}
$("compose").addEventListener("submit",e=>{e.preventDefault();send($("request").value,state?.reply_context);});
$("request").addEventListener("input",()=>sessionStorage.setItem("draft",$("request").value));
$("request").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing){e.preventDefault();$("compose").requestSubmit();}});
$("confirm").addEventListener("click",()=>{const plan=state?.approval;if(plan)send("OK "+plan.code,plan.context);});
$("reject").addEventListener("click",()=>{const plan=state?.approval;if(plan)send("NO "+plan.code,plan.context);});
$("refresh").addEventListener("click",refresh);
document.querySelectorAll("[data-prompt]").forEach(button=>button.addEventListener("click",()=>{if(pending)return;$("request").value=button.dataset.prompt;$("request").focus();sessionStorage.setItem("draft",$("request").value);}));
(async()=>{try{config=await(await fetch("connection.json")).json();if(config.base_url!=="http://127.0.0.1:18790"||!/^[a-f0-9]{64}$/.test(config.token))throw new Error();busy(false);await refresh();setInterval(refresh,2000);}catch(e){error("请加载 prepare_chrome_chat.ps1 生成的扩展文件夹，而不是源码文件夹。");}})();
