"""Read-only operator status, also usable with older listener versions."""
import argparse
import json
import re
import time
from pathlib import Path
from listener_health import assess_health
from task_observability import STAGES,task_status_text

ROOT=Path(__file__).resolve().parent/'reports/shared_chrome'


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def snapshot(root=ROOT,*,now=None):
    root=Path(root);now=time.time() if now is None else now
    health=read(root/'health.json');status=read(root/'status.json')
    observed=health.get('observability',{})
    tasks=observed.get('tasks',{})
    if not tasks:
        for path in (root/'state').glob('*.json'):
            if not re.fullmatch('[a-f0-9]{12}',path.stem):continue
            state=read(path)
            if not state.get('chat'):continue
            phase=state.get('phase','IDLE');run=state.get('run','')
            row={'task_id':state.get('task_id','T-'+run),'run':run,'phase':phase,
                 'assessment':STAGES.get(phase,'unknown'),'step':'legacy_listener_no_step_telemetry',
                 'verified':len(state.get('results',[])),'selected':len(state.get('selected',[])),
                 'remembered_remaining':len(state.get('previously_selected_scope',[])),
                 'previously_verified':len(state.get('verified_previous_results',[])),
                 'state_update_age_seconds':round(max(0,now-path.stat().st_mtime)),'pending_delivery':bool(state.get('pending_delivery'))}
            if phase=='APPROVAL' and state.get('expires') is not None and state['expires']<now:row['assessment']='approval_expired'
            tasks[state['chat']]=row
    planner=read(root/'planner/planner_progress.json')
    for row in tasks.values():
        if planner.get('run_id')==row.get('run') and planner.get('attempt_id')==row.get('attempt') and row.get('step')=='planner_running':
            row['planner']={k:planner.get(k) for k in ('status','started','finished','timeout_seconds','input_characters','form_count','exit_code')}
            row['planner']['elapsed_seconds']=round(max(0,(planner.get('finished') or now)-planner['started']))
    inbox=read(root/'state/inbox.json')
    return {'listener':assess_health(health,now=now),'pid':health.get('pid'),
            'runtime_version':status.get('runtime_version'),'heartbeat_updated':health.get('updated'),
            'listener_step':health.get('stage'),'queue_size':len(inbox) if isinstance(inbox,list) else status.get('queued'),
            'chats':health.get('chats',{}),'tasks':tasks,
            'telemetry_write_error':observed.get('telemetry_write_error'),
            'trace_write_error':status.get('trace_write_error'),
            'planner_trace_write_error':status.get('planner_trace_write_error'),
            'recent_events':observed.get('recent_events',[])}


def render(doc):
    lines=[f"Listener: {doc['listener']} | PID {doc.get('pid')} | {doc.get('runtime_version')}",
           f"Heartbeat: {doc.get('heartbeat_updated')} | queue: {doc.get('queue_size')}"]
    if doc['listener']=='heartbeat_stale':lines.append('Heartbeat is stale. The old task state is not proof that work is still running.')
    for chat,row in doc.get('tasks',{}).items():
        lines+=['',chat,task_status_text(row)]
        if row.get('step')=='legacy_listener_no_step_telemetry':lines.append('This running version has no per-step timing; last state update was '+str(row.get('state_update_age_seconds'))+'s ago.')
        if row.get('planner'):lines.append('Planner: '+json.dumps(row['planner'],ensure_ascii=False))
    if doc.get('telemetry_write_error'):lines.append('Telemetry write error: '+doc['telemetry_write_error'])
    for key in ('trace_write_error','planner_trace_write_error'):
        if doc.get(key):lines.append(key+': '+doc[key])
    return '\n'.join(lines)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--json',action='store_true')
    parser.add_argument('--follow',action='store_true');args=parser.parse_args()
    while True:
        doc=snapshot();print(json.dumps(doc,ensure_ascii=False,indent=2) if args.json else render(doc),flush=True)
        if not args.follow:break
        time.sleep(5)


if __name__=='__main__':main()
