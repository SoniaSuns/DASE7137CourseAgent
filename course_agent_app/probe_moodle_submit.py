"""Operator-only transport-blocked submit probe; no Moodle write can pass its gate."""
import json
import time
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright
from chrome_moodle import MoodleBrowser
from dedicated_chrome import PROFILE,profile_endpoint


def main():
    root=Path(__file__).parent/'reports/shared_chrome'
    state=json.loads((root/'state/reconciled-b220da9a1914.json').read_text(encoding='utf-8'))
    assert state['readback_resolution']=='original_observed_state'
    op=state['selected'][0]
    report={'mode':'transport_blocked_local_submit_probe','run':state['run'],'course_writes_forwarded':0,'events':[]}
    start=time.monotonic();held=[]
    def event(kind,**data):report['events'].append({'event':kind,'seconds':round(time.monotonic()-start,3),**data})
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(profile_endpoint(PROFILE),no_defaults=True,timeout=15000)
        page=browser.contexts[0].new_page()
        def gate(route):
            req=route.request;path=urlparse(req.url).path
            if req.method in {'GET','HEAD'}:route.continue_();return
            if req.method=='POST' and path=='/course/modedit.php':
                # Deliberately do NOT forward the request. Persist date controls only.
                values=parse_qs(req.post_data or '')
                date_names={f['name'] for f in op['date_fields']}
                event('intercepted_submit',dates={k:v for k,v in values.items() if k in date_names},
                      controls={k:v for k,v in values.items() if k in {'submitbutton','submitbutton2','cancel','update','coursemodule'}})
                route.fulfill(content_type='text/html',body='<body class="course-140147"><p id="probe-intercepted">Local probe: submission was blocked.</p></body>')
                return
            allowed=False;methods=[]
            if req.method=='POST' and path=='/api/rest/v2/user/current/preferences/drawer-open-index':allowed=True
            if req.method=='POST' and path=='/repository/draftfiles_ajax.php':allowed=parse_qs(urlparse(req.url).query).get('action')==['list']
            if req.method=='POST' and path in {'/lib/ajax/service.php','/lib/ajax/service-nologin.php'}:
                payload=req.post_data_json
                if isinstance(payload,list):methods=[x.get('methodname','') for x in payload if isinstance(x,dict)]
                if methods and all(m=='tiny_autosave_reset_session' for m in methods):
                    held.append(route);event('held_editor_cleanup');return
                allowed=isinstance(payload,list) and bool(payload) and all(isinstance(x,dict) and x.get('methodname') in {
                    'core_courseformat_get_state','core_message_get_unsent_message','core_output_load_template_with_dependencies','tiny_autosave_resume_session'} for x in payload)
            if allowed:route.continue_()
            else:event('blocked_other_write',path=path,methods=methods);route.abort()
        page.route('**/*',gate)
        try:
            reader=MoodleBrowser(page);reader.overview(op['course_id']);button=reader.prepare(op)
            event('prepared');button.click(timeout=10000);event('click_returned')
            page.wait_for_load_state('domcontentloaded',timeout=10000);event('load_state_returned')
            event('before_editor_cleanup_released',intercepted_submissions=sum(e['event']=='intercepted_submit' for e in report['events']))
            for route in held:route.abort()
            held.clear();event('editor_cleanup_released')
            page.wait_for_timeout(4000)
            event('settled',intercepted_marker=page.locator('#probe-intercepted').count(),
                  errors=page.locator('form.mform .invalid-feedback, form.mform [id^="id_error_"]').all_text_contents())
        except Exception as exc:event('probe_error',error_type=type(exc).__name__)
        finally:
            for route in held:
                try:route.abort()
                except Exception:pass
            page.close()
            (root/'submit-probe-b220da9a1914.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':main()
