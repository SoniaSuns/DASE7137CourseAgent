"""Operator-only readback of a failed preflight. No sends, approval changes or saves."""
import json
from pathlib import Path

from run_shared_chrome_agent import ROOT,load_config
from chrome_moodle import MoodleBrowser
from dedicated_chrome import profile_endpoint,PROFILE
from playwright.sync_api import sync_playwright


def main():
    root=ROOT/'reports/shared_chrome'
    states=[json.loads(path.read_text(encoding='utf-8')) for path in (root/'state').glob('*.json')
            if path.name not in {'inbox.json'} and not path.name.startswith(('retried-','interrupted-'))]
    matches=[s for s in states if isinstance(s,dict) and s.get('phase')=='FAILED' and s.get('selected')
             and not s.get('results') and not s.get('write_started') and 'form changed' in s.get('reason','')]
    if len(matches)!=1:raise RuntimeError('One failed preflight with no possible save is required.')
    state=matches[0];op=state['selected'][0];original=None
    logs=Path.home()/'.codex/sessions'
    candidates=sorted(logs.glob('*/*/*/*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:18]
    for path in candidates:
        for line in path.read_text(encoding='utf-8').splitlines():
            event=json.loads(line);item=event.get('payload',{})
            if event.get('type')!='response_item' or item.get('role')!='user':continue
            for block in item.get('content',[]):
                text=block.get('text','').replace('\r\n','\n')
                if '\n\nINPUT:\n' not in text:continue
                try:context=json.loads(text.split('\n\nINPUT:\n',1)[1])
                except ValueError:continue
                for form in context.get('forms',[]):
                    if form.get('target')!=op['target'] or form.get('fingerprint')!=op['fingerprint']:continue
                    original=json.loads(json.dumps(form))
                    for index,field in enumerate(original['fields']):
                        if 'field_ref' in field:
                            ref=field.pop('field_ref');field={**context['field_catalog'][ref],**field};original['fields'][index]=field
                        if 'options_ref' in field:field['options']=context['option_catalog'][field.pop('options_ref')]
        if original:break
    _,env=load_config()
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(profile_endpoint(PROFILE),no_defaults=True,timeout=15000)
        page=browser.contexts[0].new_page()
        try:
            reader=MoodleBrowser(page,env);reader.overview(op['course_id'])
            fresh=reader.inspect(op['target']);second=reader.inspect(op['target'])
            def differences(a,b):
                before={f['key']:f for f in a['fields']};after={f['key']:f for f in b['fields']};result=[]
                for key in sorted(set(before)|set(after)):
                    old=before.get(key,{});new=after.get(key,{})
                    changed=[name for name in set(old)|set(new) if old.get(name)!=new.get(name)]
                    if not changed:continue
                    row={'field':key,'label':new.get('label',old.get('label')),'changed_properties':sorted(changed)}
                    if key=='id_availabilityconditionsjson':
                        try:
                            x=json.loads(old.get('value',''));y=json.loads(new.get('value',''))
                            row['condition_changes']=[{'index':i,'properties':[name for name in set(left)|set(right) if left.get(name)!=right.get(name)]}
                                for i,(left,right) in enumerate(zip(x.get('c',[]),y.get('c',[]))) if left!=right]
                        except (ValueError,TypeError):pass
                    result.append(row)
                return result
            report={'run':state['run'],'target':op['target'],'original_snapshot_found':original is not None,
                'original_vs_fresh':differences(original,fresh) if original else None,
                'two_consecutive_reads':differences(fresh,second),'first_target_matches_approval':fresh['fingerprint']==op['fingerprint'],
                'moodle_writes':0}
            (root/'form_drift_diagnosis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=True))
        finally:page.close()


if __name__=='__main__':main()
