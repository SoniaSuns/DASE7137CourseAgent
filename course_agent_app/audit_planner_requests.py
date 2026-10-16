"""Real planner, synthetic evidence, no browser executor or WhatsApp I/O."""
import copy
import json
from pathlib import Path
from run_shared_chrome_agent import load_config
from chrome_planner import CodexPlanner
from chrome_form_dates import interval_evidence,dates,describe


def field(key,value,type='text',name=None,options=None):
    return {'key':key,'name':name or key,'type':type,'value':value,'label':name or key,
            'disabled':False,'rich':False,'options':options or []}


def fixture():
    modules=[{'key':'140148:module:1','course_id':'140148','kind':'module','id':'1','title':'Lecture 4.pdf','section':'Week 4','text':'Lecture 4'},
             {'key':'140148:module:2','course_id':'140148','kind':'module','id':'2','title':'Lecture 5.pdf','section':'Week 4','text':'Lecture 5'},
             {'key':'140148:module:3','course_id':'140148','kind':'module','id':'3','title':'Presentation assignment','section':'Week 5','text':'Presentation assignment'}]
    options=[{'value':'0','label':'Hidden','disabled':False},{'value':'1','label':'Visible','disabled':False}]
    forms=[{'target':m['key'],'course_id':'140148','title':m['title'],'fields':[
        field('id_name',m['title'],name='name'),field('id_visible','1',type='select-one',name='visible',options=options)]} for m in modules]
    for name,day in [('allowsubmissionsfromdate',8),('duedate',15),('gradingduedate',22)]:
        forms[-1]['fields'].append(field('id_'+name+'_enabled',True,type='checkbox',name=name+'[enabled]'))
        for part,value,maxvalue in [('day',day,31),('month',11,12),('year',2026,2030),('hour',18,23),('minute',59,59)]:
            values=range(2025,2031) if part=='year' else range(0 if part in {'hour','minute'} else 1,maxvalue+1)
            forms[-1]['fields'].append(field('id_'+name+'_'+part,str(value),type='select-one',name=name+'['+part+']',
                options=[{'value':str(v),'label':str(v),'disabled':False} for v in values]))
    return {'request':'','reply':'','feedback':'','current_time':'2026-10-08T01:00:00+08:00',
        'courses':[{'course_id':'140148','label':'Class B','title':'Class B','sections':[{'id':'section-4','title':'Week 4'},
        {'id':'section-5','title':'Week 5'}],'modules':modules,'groups':[],'groupings':[],'creation_targets':[]}],
        'forms':forms,'file_library':{'status':'available','semester':'2026-2027 Semester 1','files':[]},
        'file_evidence':[],'pending_scope':[],'conversation':[],'verified_previous_results':[],
        'previously_selected_scope':[],'planning_validation':{},'recovery_feedback':'',
        'date_interval_evidence':[{'target':forms[-1]['target'],**interval_evidence(forms[-1]['fields'])}],
        'original_date_interval_evidence':[]}


def main():
    root=Path(__file__).parent/'reports/audit_20261008';root.mkdir(parents=True,exist_ok=True)
    _,env=load_config();env['CODEX_PLANNER_TIMEOUT']='120'
    requests=[
      ('move_en','In Class B move Lecture 4.pdf and Lecture 5.pdf from Week 4 to Week 5. Preserve file contents, visibility and all settings.'),
      ('move_mixed','Class B 的 L4/L5 都属于 week 5，请把 Lecture 4.pdf 和 Lecture 5.pdf 从 Week 4 移到 Week 5，其他设置保留。'),
      ('reorder','In Class B Week 4, place Lecture 5.pdf before Lecture 4.pdf, keeping both in that section and preserving their names and content.'),
      ('mixed_move_hide','In Class B move Lecture 4.pdf to Week 5 and hide it from students. Both changes are required.'),
      ('date_shift','In Class B set Presentation assignment due date to 2 November 2026. Preserve the original intervals from opening to due and from due to grading reminder, and keep the time of day. This is the confirmed target date; no syllabus file is needed.'),
      ('upload_replace','In Class B replace the content of Lecture 4.pdf with the indexed revised-lecture-4.pdf. Keep its current name, visibility and section.'),
      ('ambiguous_section','In Class B move Lecture 4.pdf to the later week. Please choose the correct destination for me.')]
    results=[]
    for name,prompt in requests:
        context=fixture();context['request']=prompt
        if name=='upload_replace':
            context['file_library']['files']=[{'id':'fixture-revised','relative_path':'revised-lecture-4.pdf','format':'.pdf','text_reader':'pdf'}]
            context['file_evidence']=[{'file_id':'fixture-revised','offset':0,'text':'Synthetic revised lecture 4 content.','status':'read','next_offset':None}]
        row={'case':name,'request':prompt,'mode':'real_planner_synthetic_evidence','moodle_writes':0,'whatsapp_sends':0}
        try:
            planner=CodexPlanner(env,root/'planner'/name)
            decision=planner(context)
            # Match the production bounded review of an initial unsupported stop.
            if decision.get('decision')=='stop':
                row['initial_decision']=decision
                context['feedback']='Review a supported alternative without dropping requested work. Identify missing capability versus missing input. No writes are permitted in this audit.'
                decision=planner(context)
            row['result']=decision
            if name=='date_shift' and decision.get('decision')=='plan':
                op=next((o for o in decision['operations'] if o['target']=='140148:module:3'),None)
                row['proposed_dates']={k:describe(v) for k,v in dates(context['forms'][-1]['fields'],op['fields'] if op else []).items()}
        except Exception as exc:row['error_type']=type(exc).__name__;row['error']=str(exc)[:600]
        results.append(row)
        (root/'planner_requests.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':name,'decision':row.get('result',{}).get('decision'),'error_type':row.get('error_type')},ensure_ascii=True),flush=True)


if __name__=='__main__':main()
