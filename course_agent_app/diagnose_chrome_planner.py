"""Read-only operator replay of the failed planner; never sends chat/Moodle writes."""
import json
import argparse
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_shared_chrome_agent import ROOT, Runtime, load_config
from chrome_planner import CodexPlanner
from chrome_moodle import MoodleBrowser, digest
from chrome_file_library import FileLibrary
from dedicated_chrome import profile_endpoint, PROFILE
from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--replay-context',choices=['original_context_15-15-41.json','original_context_15-16-01.json','context.json'])
    parser.add_argument('--inspect-replay',action='store_true')
    args=parser.parse_args()
    config,env=load_config()
    folder=ROOT/'reports/shared_chrome/planner_diagnosis'
    folder.mkdir(parents=True,exist_ok=True)
    state=json.loads((ROOT/'reports/shared_chrome/state'/ (digest('test approval')[:12]+'.json')).read_text(encoding='utf-8'))
    original_run=subprocess.run
    def observed_run(command,*args,**kwargs):
        result=original_run(command,*args,**kwargs)
        if 'exec' not in command:return result
        prompt=kwargs.get('input','')
        stderr=(result.stderr or '').replace(prompt,'[PROMPT OMITTED]')
        for key,value in env.items():
            if value and len(value)>3 and re.search(r'password|secret|token|api.?key|subscription',key,re.I):
                stderr=stderr.replace(value,'[REDACTED]')
        stderr=re.sub(r'(?i)(bearer\s+|(?:sesskey|access_token|api_key)=)[^\s&]+',r'\1[REDACTED]',stderr)
        details={'exit_code':result.returncode,'input_characters':len(prompt),
                 'stderr_tail':stderr[-6500:],'stdout_characters':len(result.stdout or ''),
                 'output_exists':Path(command[command.index('--output-last-message')+1]).is_file()}
        (folder/'last_process.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(details,ensure_ascii=True),flush=True)
        return result
    if args.replay_context:
        context=json.loads((folder/args.replay_context).read_text(encoding='utf-8'))
        print('Diagnostic replay of the captured original input; not execution evidence.',flush=True)
        with patch('chrome_planner.subprocess.run',side_effect=observed_run):
            decision=CodexPlanner(env,folder/'runs')(context)
        (folder/'decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Planner decision: '+decision['decision'],flush=True)
        return
    with sync_playwright() as p:
        b=p.chromium.connect_over_cdp(profile_endpoint(PROFILE),no_defaults=True,timeout=15000)
        page=b.contexts[0].new_page()
        try:
            browser=MoodleBrowser(page,env)
            for cid in config['course_ids']:
                overview=browser.overview(cid)
                print('Fresh course read: '+cid+'; activities='+str(len(overview['modules'])),flush=True)
            library=FileLibrary(config['file_library_root'])
            runtime=Runtime(None,browser,[],config['course_ids'],None,folder/'state',None,library,True)
            runtime.file_snapshot=library.snapshot()
            if args.inspect_replay:
                original=json.loads((folder/'original_context_15-16-01.json').read_text(encoding='utf-8'))
                for read in original['file_evidence']:
                    runtime.file_evidence.append(library.inspect(runtime.file_snapshot,read['file_id'],read['offset']))
                targets=json.loads((folder/'original_inspect_targets.json').read_text(encoding='utf-8'))
                for index,target in enumerate(targets,1):
                    form=browser.inspect(target)
                    print(f'Form {index}/{len(targets)}: fields={len(form["fields"])}; characters={len(json.dumps(form))}',flush=True)
            context=runtime.context(SimpleNamespace(state={**state,'phase':'PLANNING'}))
            (folder/'context.json').write_text(json.dumps(context,ensure_ascii=False),encoding='utf-8')
            print('Starting same planner with fresh context.',flush=True)
            with patch('chrome_planner.subprocess.run',side_effect=observed_run):
                decision=CodexPlanner(env,folder/'runs')(context)
            (folder/'decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Planner decision: '+decision['decision'],flush=True)
        finally:page.close()


if __name__=='__main__':main()
