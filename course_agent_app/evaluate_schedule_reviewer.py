"""Opt-in live-model regression using an authorized source and fixture inventory.

No browser connection, messaging, approval or Moodle write is possible here.
"""
import argparse
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace
from run_shared_chrome_agent import load_config
from chrome_file_library import FileLibrary
from chrome_planner import CodexPlanner
from chrome_schedule_alignment import review_context, check_review


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',action='store_true');args=parser.parse_args()
    if not args.run:parser.error('Use --run to call the configured planning service with the relevant authorized syllabus.')
    config,env=load_config()
    if not config['file_library_planner_access']:raise ValueError('Source planner access is not authorized.')
    root=Path(__file__).resolve().parent/'reports/syllabus_alignment_20261010/model_evaluation'
    root.mkdir(parents=True,exist_ok=True)
    library=FileLibrary(config['file_library_root']);snapshot=library.snapshot()
    entry=next(e for e in snapshot['files'] if e['relative_path']=='00 Overview/DASE7137_1_Course_Syllabus_Internal.docx')
    source=library.inspect(snapshot,entry['id'])
    titles=['Workshop 4: Storyboard Development','Workshop 3: XRCC tutorial 3','Workshop 5: XRCC tutorial 4']
    modules=[{'key':f'140147:module:fixture{i}','title':title,'course_id':'140147'} for i,title in enumerate(titles)]
    sections=[{'key':f'140147:section:fixture{w}','title':f'Week {w}','course_id':'140147'} for w in (5,6,8)]
    browser=SimpleNamespace(overviews={'140147':{'course_id':'140147','label':'Class A','modules':modules,'sections':sections}})
    contract={'required':True,'course_ids':['140147'],'sources':[{'course_id':'140147','file_id':entry['id']}],'items':[]}
    for module,w in zip(modules,(5,6,8)):
        row=next(r for r in source['table_rows'] if r['row_id'].startswith('t3:') and r['cells'][0].strip()==str(w))
        contract['items'].append({'target':module['key'],'target_section':f'140147:section:fixture{w}',
            'file_id':entry['id'],'row_id':row['row_id'],'week_column':0,'expected_week':w,
            'disposition':'place','visibility':'preserve','reason':'Place the named workshop with the matching syllabus topic.'})
    wrong=copy.deepcopy(contract)
    for key in ('target_section','row_id','expected_week'):wrong['items'][0][key]=contract['items'][1][key]
    planner=CodexPlanner(env,root/'planner');rows=[]
    for name,proposed,expected in [('correct_week_5_6_8',contract,True),('wrong_storyboard_week6_cites_existing_row',wrong,False)]:
        context=review_context(proposed,browser,[source],
            'Align these Class A workshops with the internal syllabus; preserve visibility.',[])
        context.update(run_id=name,task_id='reviewer-regression')
        start=time.monotonic();result=planner(context)
        try:check_review(result,proposed);accepted=True
        except ValueError:accepted=False
        rows.append({'case':name,'expected_accepted':expected,'accepted':accepted,'passed':accepted==expected,
                     'seconds':round(time.monotonic()-start,1),'review':result})
        report={'mode':'live model with real source and three-item fixture inventory; no Moodle writes',
                'source':source['relative_path'],'source_sha256':source['sha256'],'cases':rows}
        (root/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:rows[-1][k] for k in ('case','passed','seconds')}),flush=True)
    return 0 if all(r['passed'] for r in rows) else 1


if __name__=='__main__':raise SystemExit(main())
