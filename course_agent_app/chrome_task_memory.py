"""Ten callable task identities; historical audit files never grant execution."""
import copy
import json
import re
import time
import uuid
from pathlib import Path

LIMIT=10
ACTIVE={'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'}
OUTCOMES={'DONE','PARTIAL','FAILED','CANCELLED','EXPIRED','INTERRUPTED','NEEDS_READBACK'}
CODE=r'(?:T-)?[a-fA-F0-9]{12}'


def result_summary(state):
    """Bounded historical evidence, never executable operations or authority."""
    if state.get('phase') not in OUTCOMES:return None
    from run_trace import RunTrace
    cleaner=RunTrace(Path('.'),[state.get('code','')])
    def clip(value,size):return cleaner.clean(str(value or ''))[:size]
    receipts=[r for r in state.get('verified_previous_results',[])+state.get('results',[])
              if isinstance(r,dict) and r.get('verified') is True]
    selected=state.get('selected',[])
    pending=bool(state.get('write_started') and state.get('readback_resolution')!='original_observed_state' and
        state['write_started'] not in [o.get('id') for o in selected[:len(state.get('results',[]))]])
    return {'task_id':task_code(state),'source_run':state['run'],'phase':state['phase'],
        'request':clip(state.get('task'),800),'answer':clip(state.get('last_answer'),6000),
        'reason':clip(state.get('reason'),350),'needs_readback':state['phase']=='NEEDS_READBACK' or pending,
        'verified_count':len(receipts),'verified_results':[
            {k:clip(r[k],180) for k in ('target','change') if k in r} for r in receipts[-5:]],
        'remaining_selected_count':max(0,len(selected)-len(state.get('results',[]))),
        'schedule_required':bool(state.get('schedule_required')),'goal_verified':bool(state.get('goal_verified')),
        'goal_unresolved':[clip(x,200) for x in state.get('goal_unresolved',[])[:10]],
        'refinements':[clip(x,200) for x in state.get('conversation',[])[-2:]],
        'targets':list(dict.fromkeys(clip(o.get('target'),100) for o in selected if o.get('target')))[:10],
        'summarized':True}


def task_code(state):
    return state.get('task_id') or ('T-'+state['run'] if state.get('run') else 'unassigned')


def command(text):
    text=text.strip()
    if re.fullmatch(r'/?status|状态|运行状态',text,re.I):return ('status','','')
    if len(set(re.findall(r'T-[a-f0-9]{12}(?![a-z0-9])',text,re.I)))>1:return ('help','','')
    if re.fullmatch(r'/?tasks|任务列表',text,re.I):return ('list','','')
    match=re.fullmatch(r'(?:/?task|查看任务)\s+('+CODE+r')',text,re.I)
    if match:return ('show',match[1],'')
    match=re.fullmatch(r'(?:please\s+)?(?:/?(?:resume|continue)(?:\s+task)?|继续(?:任务)?)\s*[:：]?\s*('+CODE+r')(?![a-fA-F0-9])(?:[\s,，:：]+([\s\S]+))?',text,re.I)
    if match:return ('resume',match[1],(match[2] or '').strip())
    # Extract identity mechanically; the planner interprets what using it means.
    reference=re.search(r'T-[a-f0-9]{12}(?![a-z0-9])',text,re.I)
    if reference:return ('reference',reference[0],text)
    if re.match(r'^(?:/tasks?|/resume|(?:please\s+)?continue\s+task|任务列表|查看任务|继续任务)(?:\s|T-|$)',text,re.I):
        return ('help','','')
    return None


def operation_memory(op):
    result={k:copy.deepcopy(op[k]) for k in ('id','capability','target','target_kind','grade_identity','course_id','title','summary','scope','reason','depends_on','params') if k in op}
    result['fields']=[{k:p[k] for k in ('key','value') if k in p} for p in op.get('fields',[])]
    return result


def remaining_goal_scope(state):
    """A prerequisite manifest must not overwrite the parent task's scope."""
    if state.get('goal_verified'):return []
    selected=state.get('selected',[]);results=state.get('results',[])
    baseline=copy.deepcopy(state.get('goal_scope') or state.get('previously_selected_scope') or
                           state.get('task_memory_context',{}).get('previously_selected_scope',[]))
    if selected and all(o.get('capability')=='set_section_visibility' for o in selected) and baseline:
        return baseline
    def signature(o):
        return json.dumps({k:v for k,v in operation_memory(o).items() if k in {'capability','target','params','fields'}},sort_keys=True)
    completed=[x['operation'] for x in state.get('completed_operation_memory',[]) if x.get('receipt',{}).get('verified')]
    completed+=selected[:len(results)]
    done={signature(o) for o in completed}
    remaining=[];seen=set()
    for op in baseline+selected[len(results):]:
        key=signature(op)
        if key not in done and key not in seen:
            item=operation_memory(op)
            if 'fields' not in op:item.pop('fields',None)
            remaining.append(item);seen.add(key)
    return remaining


def continuation(state):
    """Planner context only: deliberately omit approval, forms and fingerprints."""
    selected=state.get('selected',[]);results=state.get('results',[])
    if len(results)>len(selected) or any(r.get('verified') is not True for r in results):
        raise ValueError('Task receipts are inconsistent; inspect its audit before continuing.')
    if state.get('phase') in {'APPROVED','EXECUTING','NEEDS_READBACK'}:
        raise ValueError('This task has a possible pending save; reconcile it before continuing.')
    if state.get('write_started') and not any(o.get('id')==state['write_started'] for o in selected[:len(results)]) and state.get('readback_resolution')!='original_observed_state':
        raise ValueError('A previous save is unconfirmed; no task replay is permitted.')
    completed=copy.deepcopy(state.get('completed_operation_memory',[]))
    for op,receipt in zip(selected,results):
        completed.append({'operation':operation_memory(op),'receipt':copy.deepcopy(receipt)})
    return {
        'task_id':task_code(state),'source_run':state['run'],'source_phase':state.get('phase'),
        'original_request':state['task'],'conversation':copy.deepcopy(state.get('conversation',[])),
        'verified_previous_results':copy.deepcopy(state.get('verified_previous_results',[])+results),
        'completed_operation_memory':completed,
        'previously_selected_scope':remaining_goal_scope(state),
        'goal_scope':copy.deepcopy(state.get('goal_scope') or state.get('previously_selected_scope',[])),
        'schedule_required':bool(state.get('schedule_required')),
        'schedule_contract':copy.deepcopy(state.get('schedule_contract',{})),
        'goal_followups':state.get('goal_followups',0),
        'exploration_required':bool(state.get('exploration_evidence') or state.get('exploration_required')),
        'exploration_sources':copy.deepcopy(state.get('exploration_sources',[])),
        'original_date_interval_evidence':copy.deepcopy(state.get('original_date_interval_evidence',[])),
        'readback_repair':copy.deepcopy(state.get('readback_repair',{})),
        'referenced_task_results':copy.deepcopy(state.get('referenced_task_results',[])),
        'previous_answer':copy.deepcopy(state.get('last_answer') or state.get('task_memory_context',{}).get('previous_answer','')),
    }


class TaskMemory:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'task_memory.json'

    def read(self):
        if not self.path.exists():return []
        return json.loads(self.path.read_text(encoding='utf-8'))['tasks']

    def write(self,entries):
        self.root.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            tmp.write_text(json.dumps({'version':1,'limit':LIMIT,'tasks':entries},ensure_ascii=False,indent=2),encoding='utf-8')
            # Windows readers can briefly deny rename/delete sharing. Retry only
            # this local atomic commit; never replay workflow or Moodle actions.
            for attempt in range(6):
                try:
                    tmp.replace(self.path)
                    break
                except PermissionError:
                    if attempt==5:raise
                    time.sleep(0.05*2**attempt)
        finally:
            try:tmp.unlink(missing_ok=True)
            except OSError:pass  # Cleanup must not hide the persistence failure.

    def record(self,state,*,register=False):
        if not state.get('run') or not state.get('sender') or not state.get('chat'):return
        entries=self.read();code=task_code(state)
        entry=next((e for e in entries if e['task_id']==code),None)
        if entry is None:
            if not register:return  # Evicted codes never silently reappear.
            if len(entries)>=LIMIT:
                victim=next((e for e in entries if e['state'].get('phase') not in ACTIVE),None)
                if victim is None:raise ValueError('All ten task slots are active; finish a task before creating another.')
                entries.remove(victim)
            entry={'task_id':code,'created':time.time(),'runs':[]};entries.append(entry)
        if state['run'] not in entry['runs']:entry['runs'].append(state['run'])
        entry['state']=copy.deepcopy(state);entry['state']['task_id']=code
        summary=result_summary(state)
        if summary is not None and summary!=entry.get('result_summary'):
            entry['result_summary']=summary;entry['result_updated']=time.time()
        self.write(entries)

    def recent_results(self,chat,sender,*,limit=10,exclude_task=None):
        if type(limit) is not int or not 5<=limit<=10:raise ValueError('Recent result limit must be 5 to 10.')
        rows=[]
        for entry in self.read():
            state=entry['state']
            if state.get('chat')!=chat or state.get('sender')!=sender or entry['task_id']==exclude_task:continue
            summary=entry.get('result_summary') or result_summary(state)
            if summary is not None:
                rows.append((entry.get('result_updated',entry.get('created',0)),copy.deepcopy(summary)))
        rows.sort(key=lambda row:row[0],reverse=True)
        items=[];size=0
        for updated,summary in rows[:limit]:
            summary['recorded_at']=updated
            length=len(json.dumps(summary,ensure_ascii=False))
            if size+length>24000:break
            items.append(summary);size+=length
        return {'limit':limit,'available':len(rows),'included':len(items),'items':items,
                'usage':'Historical context only. Re-read current evidence; no approval or automatic continuation.'}

    def id_for_run(self,run):
        return next((e['task_id'] for e in self.read() if run in e['runs']),None)

    def lookup(self,code,chat,sender):
        normalized=code.lower().removeprefix('t-')
        for entry in self.read():
            if normalized==entry['task_id'][2:].lower() or normalized in entry['runs']:
                state=entry['state']
                if state['chat']==chat and state['sender']==sender:return copy.deepcopy(state)
        raise ValueError('Task code is unavailable in this chat for this requester (unknown or evicted). Use /tasks.')

    def visible(self,chat,sender):
        return [copy.deepcopy(e['state']) for e in reversed(self.read()) if e['state']['chat']==chat and e['state']['sender']==sender]

    def migrate(self):
        if self.path.exists():return
        latest={}
        for path in self.root.glob('*.json'):
            try:state=json.loads(path.read_text(encoding='utf-8-sig'))
            except (ValueError,OSError):continue
            if not isinstance(state,dict) or not all(state.get(k) for k in ('run','chat','sender','task')):continue
            run=state['run']
            if not re.fullmatch(r'[a-f0-9]{12}',run):continue
            priority=(bool(re.fullmatch(r'[a-f0-9]{12}',path.stem)),path.stat().st_mtime)
            if run not in latest or priority>latest[run][0]:latest[run]=(priority,state)
        def identity(state,seen=()):
            if state.get('task_id'):return state['task_id']
            parent=state.get('renewed_from') or state.get('retry_of')
            if parent in latest and parent not in seen:return identity(latest[parent][1],(*seen,state['run']))
            return 'T-'+(parent if parent and re.fullmatch(r'[a-f0-9]{12}',parent) else state['run'])
        grouped={};newest={}
        for priority,state in sorted(latest.values(),key=lambda row:row[0][1]):
            if not priority[0] and state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL'}:
                state['phase']='INTERRUPTED'  # An archived question is not a live task slot.
            code=identity(state);state['task_id']=code
            entry=grouped.setdefault(code,{'task_id':code,'created':state.get('created',priority[1]),'runs':[]})
            entry['runs'].append(state['run'])
            rank=(priority[0],state.get('created',0),priority[1])
            if code not in newest or rank>newest[code]:entry['state']=state;newest[code]=rank
        entries=sorted(grouped.values(),key=lambda e:e['created'])
        while len(entries)>LIMIT:
            victim=next((e for e in entries if e['state'].get('phase') not in ACTIVE),None)
            if victim is None:raise ValueError('More than ten active legacy tasks need reconciliation.')
            entries.remove(victim)
        self.write(entries)


def description(state):
    done=len(state.get('verified_previous_results',[]))+len(state.get('results',[]))
    remaining=max(0,len(state.get('selected',[]))-len(state.get('results',[])))
    return f"{task_code(state)} | {state.get('phase','UNKNOWN')} | verified: {done} | pending selected: {remaining}\n{state['task']}"
