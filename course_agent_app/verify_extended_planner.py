"""Actual configured planner, synthetic evidence, resolve only, no execution."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock
from audit_planner_requests import fixture,field
from chrome_planner import CodexPlanner
from chrome_moodle import MoodleBrowser,form_fingerprint
from chrome_structure import CourseStructure
from chrome_resources import ResourceFiles
from run_shared_chrome_agent import load_config


def main():
    root=Path(__file__).parent/'reports/implementation_20261008/planner';root.mkdir(parents=True,exist_ok=True)
    _,env=load_config();env['CODEX_PLANNER_TIMEOUT']='120'
    base=fixture();c=base['courses'][0]
    for index,s in enumerate(c['sections'],101):
        s.update(key=f'140148:section:{index}',section_id=str(index),number=str(index-100),module_keys=[],new_resource_target=f'140148:new_resource:{index}')
    for m in c['modules']:
        s=next(s for s in c['sections'] if s['title']==m['section']);m.update(section_key=s['key'],module_type='resource' if m['id']!='3' else 'assign');s['module_keys'].append(m['key'])
    for f in base['forms']:
        f['submit']=[{'key':'save','name':'submitbutton','label':'Save'}]
        if f['target'].endswith((':1',':2')):
            f['resource_files']=[f['title']]
            rich=field('id_introeditor','<p>Original description</p>',type='textarea',name='introeditor[text]');rich['rich']=True
            f['fields'] += [rich,field('id_availabilityconditionsjson',json.dumps({'op':'&','c':[],'showc':[]}),type='textarea')]
    for s in c['sections']:
        base['forms'].append({'target':s['key'],'course_id':'140148','title':s['title'],'fields':[field('id_name',s['title'])],'submit':[{'key':'save','name':'submitbutton','label':'Save'}]})
        base['forms'].append({'target':s['new_resource_target'],'course_id':'140148','title':'New file in '+s['title'],'fields':[field('id_name','')],
                             'submit':[{'key':'save','name':'submitbutton','label':'Save'}],'resource_files':[]})
    base['file_library']['files']=[{'id':'file:fixture','relative_path':'revised-lecture.pdf','format':'.pdf','text_reader':True}]
    base['file_evidence']=[{'file_id':'file:fixture','offset':0,'status':'read','text':'Synthetic revised Lecture 4 source for Class B.','next_offset':None}]
    cases=[
      ('move','Class B 的 Lecture 4.pdf 和 Lecture 5.pdf 都放到 Week 5，按 L4、L5 顺序放在章节末尾，其他设置和内容保持。'),
      ('reorder','In Class B Week 4, put Lecture 5.pdf before Lecture 4.pdf. Keep contents and all other settings.'),
      ('mixed','Move Class B Lecture 4.pdf to Week 5 AND hide it. Both effects are required.'),
      ('replace','Replace Class B Lecture 4.pdf with the inspected revised-lecture.pdf source, keeping its activity name, visibility and section.'),
      ('upload','Upload the inspected revised-lecture.pdf into Class B Week 5 as a new resource named Revised Lecture. Leave existing resources unchanged.'),
      ('rename','Rename the Class B Week 5 section to Week 5 - Lectures 4 and 5. Keep its activities unchanged.'),
      ('rich','Change the description of Class B Lecture 4.pdf to a paragraph containing bold text: Read before class.'),
      ('restriction','Add a date restriction to Class B Lecture 4.pdf: available from 26 October 2026 at 18:59 Hong Kong time. Show restriction information before that time; preserve other settings.')]
    results=[]
    for name,request in cases:
        context=copy.deepcopy(base);context['request']=request
        row={'case':name,'request':request,'writes':0}
        try:
            planner=CodexPlanner(env,root/name);decision=planner(context)
            b=MoodleBrowser(Mock());b.overviews={'140148':copy.deepcopy(c)}
            for m in c['modules']:b.targets[m['key']]={**m,'kind':'module'}
            for s in c['sections']:
                b.targets[s['key']]={**s,'id':s['section_id'],'kind':'section','course_id':'140148'}
                b.targets[s['new_resource_target']]={'key':s['new_resource_target'],'id':'new','kind':'new_resource','course_id':'140148','section_key':s['key'],'title':'New file in '+s['title']}
            for f in context['forms']:f['fingerprint']=form_fingerprint(f);b.forms[f['target']]=f
            library=Mock();library.read_bytes.return_value=({'id':'file:fixture','relative_path':'revised-lecture.pdf','sha256':'fixturehash'},b'fixture')
            resources=ResourceFiles(b,library);resources.content_hash=Mock(return_value='oldhash')
            for attempt in range(2):
                try:
                    resolved=[]
                    for op in decision.get('operations',[]):
                        if op['capability']=='move_module':resolved.append(CourseStructure(b).resolve(op))
                        elif op['capability'] in {'upload_file_resource','replace_file_resource'}:resolved.append(resources.resolve(op,{'root':'fixture','files':[]},context['file_evidence']))
                        else:resolved.append(b.resolve(op))
                    break
                except ValueError as exc:
                    if attempt:raise
                    row['initial_validation_error']=str(exc);context['feedback']=str(exc)+' Rebuild from the exact supplied evidence.';decision=planner(context)
            row['decision']=decision;row['resolved_count']=len(resolved)
        except Exception as exc:row['error_type']=type(exc).__name__;row['error']=str(exc)[:500]
        results.append(row);(root/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':name,'decision':row.get('decision',{}).get('decision'),'resolved':row.get('resolved_count'),'error':row.get('error_type')},ensure_ascii=True),flush=True)


if __name__=='__main__':main()
