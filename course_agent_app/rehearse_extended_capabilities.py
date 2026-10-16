"""Live read/unsaved-form rehearsal. Never submit or upload anything."""
import copy
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from dedicated_chrome import profile_endpoint,PROFILE
from chrome_moodle import MoodleBrowser
from chrome_course_scope import course
from chrome_resources import ResourceFiles


def main():
    report={'moodle_saves':0,'uploads':0,'whatsapp_sends':0,'checks':{}}
    out=Path(__file__).parent/'reports/implementation_20261008';out.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(profile_endpoint(PROFILE));page=browser.contexts[0].new_page()
        try:
            b=MoodleBrowser(page);overview=b.overview('140148')
            report['checks']['sections_with_exact_ids']=len([s for s in overview['sections'] if s.get('key')])
            report['checks']['modules']=len(overview['modules'])
            target=next(m for m in overview['modules'] if m['module_type']=='resource')
            form=b.inspect(target['key'])
            report['checks']['file_manager_observed']=isinstance(form.get('resource_files'),list)
            resources=ResourceFiles(b,None)
            report['checks']['current_file_hash_readable']=len(resources.content_hash(b.targets[target['key']]))==64
            b.inspect(target['key'])
            rich=next(f for f in form['fields'] if f['rich'])
            op=b.resolve({'id':'rehearsal','capability':'update_moodle_form','target':target['key'],
                'summary':'Unsaved rich text rehearsal','params':{},'fields':[{'key':rich['key'],'value':'<p><strong>Unsaved rehearsal</strong></p>'}]})
            b.prepare(op);report['checks']['native_rich_text_unsaved']=True
            form=b.inspect(target['key'])
            field=next(f for f in form['fields'] if f['key']=='id_availabilityconditionsjson')
            tree=json.loads(field['value']) if field['value'] else {'op':'&','c':[],'showc':[]}
            tree['c'].append({'type':'date','d':'>=','t':1791504000})
            if 'showc' in tree:tree['showc'].append(True)
            op=b.resolve({'id':'rehearsal','capability':'update_moodle_form','target':target['key'],
                'summary':'Unsaved availability rehearsal','params':{},'fields':[{'key':field['key'],'value':json.dumps(tree)}]})
            b.prepare(op);report['checks']['native_condition_add_unsaved']=True
            section=next(s for s in overview['sections'] if s.get('key'))
            form=b.inspect(section['key']);report['checks']['section_name_field_observed']=any(f['key']=='id_name' for f in form['fields'])
            form=b.inspect(section['new_resource_target']);report['checks']['new_resource_form_observed']=form.get('resource_files')==[]
            b.goto(course('140148'),course('140148').url)
            report['checks']['course_editor_module_available']=page.evaluate("()=>new Promise(resolve=>require(['core/ajax'],a=>resolve(typeof a.call==='function'),()=>resolve(false)))")
        except Exception as e:
            report['error_type']=type(e).__name__;report['error']=str(e)[:500]
        finally:page.close()
    (out/'live_rehearsal.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))


if __name__=='__main__':main()
