"""Task-level reliability metrics from retained run state. Read-only; never contacts Moodle or WhatsApp.

A task is every run sharing one task ID (older runs without one are grouped by request text).
A write task is one where some run reached a selected Moodle plan. These numbers measure
production outcomes, unlike the fixture regression pass rate.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STATE=ROOT/'reports/shared_chrome/state'
ACTIVE={'PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'}


def load_runs(state_dir):
    runs={}
    for path in Path(state_dir).glob('*.json'):
        if not (re.fullmatch(r'[a-f0-9]{12}',path.stem) or path.stem.startswith('archived-run-')):continue
        try:state=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        if isinstance(state,dict) and state.get('run'):
            # The live flow file is newer than an archived copy of the same run.
            if state['run'] not in runs or re.fullmatch(r'[a-f0-9]{12}',path.stem):runs[state['run']]=state
    return runs


def task_key(state,memory_index):
    return state.get('task_id') or memory_index.get(state['run']) or 'text:'+str(state.get('task','')).strip().casefold()


def summarize(state_dir=STATE):
    state_dir=Path(state_dir)
    memory_index={}
    memory_path=state_dir/'task_memory.json'
    if memory_path.exists():
        tasks=json.loads(memory_path.read_text(encoding='utf-8')).get('tasks',{})
        for task in (tasks.values() if isinstance(tasks,dict) else tasks):
            for run in task.get('runs',[]):memory_index[run]=task['task_id']
    groups={}
    for state in load_runs(state_dir).values():
        groups.setdefault(task_key(state,memory_index),[]).append(state)
    rows=[]
    for key,runs in groups.items():
        runs.sort(key=lambda s:s.get('created',0))
        phases=[s.get('phase','') for s in runs]
        schedule=any(s.get('schedule_required') for s in runs)
        rows.append({'task':key,'request':str(runs[0].get('task',''))[:80],'runs':len(runs),'phases':phases,
                     'write_task':any(s.get('selected') or s.get('results') for s in runs),
                     'first_run_done':phases[0]=='DONE' and phases[-1]!='PARTIAL',
                     'done':'DONE' in phases and phases[-1]!='PARTIAL','active':phases[-1] in ACTIVE,
                     'partial':phases[-1]=='PARTIAL',
                     'done_without_goal_verification':schedule and phases[-1]=='DONE' and not runs[-1].get('goal_verified')})
    rows.sort(key=lambda r:-r['runs'])
    writes=[r for r in rows if r['write_task']];finished=[r for r in writes if r['done']]
    return {'tasks':len(rows),'runs':sum(r['runs'] for r in rows),'write_tasks':len(writes),
            'write_first_run_success_rate':round(sum(r['first_run_done'] for r in writes)/len(writes),3) if writes else None,
            'write_done_rate':round(len(finished)/len(writes),3) if writes else None,
            'mean_runs_per_write_task':round(sum(r['runs'] for r in writes)/len(writes),2) if writes else None,
            'max_runs_per_task':max((r['runs'] for r in rows),default=0),
            'done_without_goal_verification':sum(r['done_without_goal_verification'] for r in rows),
            'rows':rows}


def main():
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--state-dir',default=str(STATE));parser.add_argument('--json',action='store_true')
    args=parser.parse_args();report=summarize(args.state_dir)
    if args.json:print(json.dumps(report,ensure_ascii=False,indent=2));return 0
    print(f"Tasks {report['tasks']} | runs {report['runs']} | write tasks {report['write_tasks']}")
    print(f"Write first-run success {report['write_first_run_success_rate']} | eventually done {report['write_done_rate']} | "
          f"mean runs/write task {report['mean_runs_per_write_task']} | max runs {report['max_runs_per_task']}")
    if report['done_without_goal_verification']:
        print(f"Schedule tasks marked Done without whole-goal verification: {report['done_without_goal_verification']}")
    for row in report['rows']:
        print(f"  {row['task'][:24]:24} runs={row['runs']:<3} {'W' if row['write_task'] else 'R'} "
              f"{'partial' if row['partial'] else 'done' if row['done'] else 'active' if row['active'] else 'open'}  {row['request']}")
    return 0


if __name__=='__main__':raise SystemExit(main())
