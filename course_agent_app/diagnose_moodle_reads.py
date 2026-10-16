"""Operator-only current failed-plan readback; no Save or WhatsApp actions."""
import json
import time
import traceback
from pathlib import Path
from playwright.sync_api import sync_playwright
from chrome_moodle import MoodleBrowser
from dedicated_chrome import PROFILE, profile_endpoint


def main():
    root=Path(__file__).parent/'reports/shared_chrome'
    state=json.loads((root/'state/29ed78e10116.json').read_text(encoding='utf-8'))
    assert state['phase']=='FAILED' and not state.get('results') and 'write_started' not in state
    keys=list(dict.fromkeys(op['target'] for op in state['plan']['operations']))
    report={'run':state['run'],'mode':'read_only','forms':[]}
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(profile_endpoint(PROFILE),no_defaults=True,timeout=15000)
        page=browser.contexts[0].new_page()
        try:
            reader=MoodleBrowser(page)
            for cid in sorted({k.split(':')[0] for k in keys}):
                reader.overview(cid);print('Inventory ready: '+cid,flush=True)
            for key in keys:
                started=time.monotonic();row={'target':key}
                try:
                    form=reader.inspect(key);row.update(status='read',fields=len(form['fields']))
                except Exception as exc:
                    row.update(status='failed',error_type=type(exc).__name__,action=reader.last_action,
                        frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                                for f in traceback.extract_tb(exc.__traceback__) if 'course_agent_app' in f.filename])
                    try:row['page_state']=page.evaluate('()=>({ready:document.readyState,pending:window.M?.util?.pending_js?.length||0,forms:document.querySelectorAll("form.mform").length})')
                    except Exception:pass
                row['seconds']=round(time.monotonic()-started,2);report['forms'].append(row)
                (root/'moodle-read-diagnosis.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(row),flush=True)
        finally:page.close()


if __name__=='__main__':main()
