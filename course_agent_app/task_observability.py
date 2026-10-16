"""Content-free task telemetry. Heartbeats do not count as task progress."""
import copy
import json
import re
import threading
import time
from pathlib import Path

STAGES={'PLANNING':'planning','QUESTION':'waiting_for_answer','SCOPE':'waiting_for_scope',
        'APPROVAL':'waiting_for_approval','APPROVED':'preflight','EXECUTING':'saving_and_readback',
        'NEEDS_READBACK':'waiting_for_readback','EXPIRED':'approval_expired','INTERRUPTED':'interrupted',
        'FAILED':'failed','DONE':'done','PARTIAL':'closed_with_unresolved_items','CANCELLED':'cancelled','IDLE':'idle'}
COUNTERS={'current','total','forms','round','repair','queue_size','input_characters'}


class TaskObserver:
    def __init__(self,root,*,clock=time.time,max_bytes=2*1024*1024):
        self.root=Path(root);self.clock=clock;self.max_bytes=max_bytes
        self.lock=threading.RLock();self.rows={};self.recent=[];self.write_error=None
        self.path=self.root/'events.jsonl'

    def event(self,chat,kind,**values):
        # Only callers' numeric counters and bounded identifiers are accepted.
        event={'at':self.clock(),'chat':chat,'event':kind}
        for key,value in values.items():
            if key in COUNTERS and type(value) in (int,float):event[key]=value
            elif key in {'task_id','run','phase','step','target','field','lane','error_type','decision','attempt'} and isinstance(value,str):
                if re.fullmatch(r'[A-Za-z0-9_:\-./]{1,100}',value):event[key]=value
        with self.lock:
            self.recent=(self.recent+[event])[-100:]
            try:
                self.root.mkdir(parents=True,exist_ok=True)
                if self.path.exists() and self.path.stat().st_size>=self.max_bytes:
                    self.path.replace(self.root/'events.previous.jsonl')
                with self.path.open('a',encoding='utf-8') as handle:handle.write(json.dumps(event,ensure_ascii=False)+'\n')
                self.write_error=None
            except OSError as exc:self.write_error=type(exc).__name__  # Telemetry cannot interrupt a save.

    def sync(self,chat,state):
        with self.lock:
            now=self.clock();previous=self.rows.get(chat,{})
            run=state.get('run','');phase=state.get('phase','IDLE')
            base={'task_id':state.get('task_id') or ('T-'+run if run else ''),'run':run,'phase':phase,
                  'verified':len(state.get('results',[])),'previously_verified':len(state.get('verified_previous_results',[])),
                  'selected':len(state.get('selected',[])),'approval_sent':state.get('approval_sent',False),
                  'remembered_remaining':len(state.get('previously_selected_scope',[])),
                  'schedule_required':bool(state.get('schedule_required')),'goal_verified':bool(state.get('goal_verified')),
                  'alignment_items':len(state.get('schedule_contract',{}).get('items',[])),
                  'goal_unresolved_count':len(state.get('goal_unresolved',[])),
                  'approval_expires':state.get('expires'),'pending_delivery':bool(state.get('pending_delivery'))}
            changed=any(previous.get(k)!=v for k,v in base.items())
            fresh=run!=previous.get('run') or not previous
            row={} if fresh else dict(previous)
            row.update(base)
            if fresh:row.update(started=state.get('created',now),last_progress=now,step_started=now)
            if fresh or phase!=previous.get('phase'):
                row.update(step=STAGES.get(phase,'unknown'),step_started=now,target='',current=0,total=0)
            if phase=='EXECUTING':
                target=next((o.get('target','') for o in state.get('selected',[]) if o.get('id')==state.get('write_started')),'')
                row.update(target=target,current=base['verified'],total=base['selected'])
            if phase=='APPROVAL' and not base['approval_sent']:row['step']='delivering_approval'
            elif phase=='APPROVAL' and base['approval_sent']:row['step']='waiting_for_approval'
            if changed:
                row['last_progress']=now
                self.event(chat,'state',task_id=base['task_id'],run=run,phase=phase,current=base['verified'],total=base['selected'])
            self.rows[chat]=row

    def step(self,chat,step,**values):
        with self.lock:
            row=self.rows.get(chat)
            if row is None:return
            safe={k:v for k,v in values.items() if k in COUNTERS and type(v) in (int,float)}
            for key in ('target','attempt'):
                if key in values and re.fullmatch(r'[A-Za-z0-9_:\-./]{0,100}',str(values[key])):safe[key]=values[key]
            now=self.clock();row.update(step=step,step_started=now,last_progress=now,**safe)
            self.event(chat,'step',task_id=row['task_id'],run=row['run'],step=step,**safe)

    def failure(self,chat,exc,lane,action):
        with self.lock:
            row=self.rows.get(chat)
            if row is None:return
            data={'error_type':type(exc).__name__,'lane':lane}
            for key in ('target','field'):
                value=action.get(key)
                if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_:\-./]{1,100}',value):data[key]=value
            row['last_error']={**data,'at':self.clock(),'step':row.get('step')}
            self.event(chat,'error',task_id=row['task_id'],run=row['run'],**data)

    def snapshot(self):
        with self.lock:
            now=self.clock();rows=copy.deepcopy(self.rows)
            for row in rows.values():
                row['elapsed_seconds']=max(0,round(now-row['started']))
                row['step_seconds']=max(0,round(now-row['step_started']))
                row['no_progress_seconds']=max(0,round(now-row['last_progress']))
                phase=row['phase'];limit=300 if row.get('step')=='planner_running' else 90
                row['slow_threshold_seconds']=limit
                if phase=='APPROVAL' and row['approval_sent']:
                    if row.get('approval_expires') is None:
                        row['approval_remaining_seconds']=None
                        row['assessment']='waiting_for_approval'
                    else:
                        remaining=round(row['approval_expires']-now)
                        row['approval_remaining_seconds']=max(0,remaining)
                        row['assessment']='approval_expired' if remaining<=0 else 'waiting_for_approval'
                elif phase in {'QUESTION','SCOPE','EXPIRED','NEEDS_READBACK','INTERRUPTED','FAILED','DONE','PARTIAL','CANCELLED','IDLE'}:
                    row['assessment']=STAGES.get(phase,'unknown')
                elif row['pending_delivery']:row['assessment']='waiting_for_delivery_verification'
                else:row['assessment']='slow_progress' if row['no_progress_seconds']>limit else 'working'
            return {'tasks':rows,'telemetry_write_error':self.write_error,'recent_events':copy.deepcopy(self.recent[-20:])}


def task_status_text(row):
    if not row:return 'No live step information is available for this task.'
    lines=[f"Task ID: {row.get('task_id','unknown')}",f"Task state: {row.get('assessment',row.get('phase','unknown'))}",
           f"Step: {row.get('step','unknown')} ("+(str(row['step_seconds'])+'s' if 'step_seconds' in row else 'timing unavailable')+')',
           ('Last progress: '+str(row['no_progress_seconds'])+'s ago' if 'no_progress_seconds' in row else 'Last progress: unavailable in this listener version'),
           f"Verified changes: {row.get('verified',0)}/{row.get('selected',0)}; earlier: {row.get('previously_verified',0)}"]
    if row.get('total'):lines.append(f"Step progress: {row.get('current',0)}/{row['total']}")
    if row.get('schedule_required'):
        lines.append('Syllabus goal: '+('verified' if row.get('goal_verified') else 'not yet verified')+
                     '; materials accounted for: '+str(row.get('alignment_items',0))+
                     '; unresolved findings: '+str(row.get('goal_unresolved_count',0)))
    if not row.get('selected') and row.get('remembered_remaining'):lines.append('Remembered remaining scope: '+str(row['remembered_remaining'])+'; no current approved plan.')
    if row.get('target'):lines.append('Target: '+row['target'])
    if row.get('round') is not None:lines.append(f"Planning round: {row['round']}; repair: {row.get('repair',0)}; forms: {row.get('forms',0)}")
    if row.get('last_error'):lines.append('Last error: '+row['last_error']['error_type']+' at '+row['last_error']['lane'])
    if row.get('assessment')=='slow_progress':lines.append('This step is slow; a stalled process has not been established.')
    return '\n'.join(lines)
