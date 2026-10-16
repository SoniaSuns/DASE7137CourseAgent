"""Operator-only rehearsal of an unwritten failed run. Never click Save.

Uses a temporary tab in the existing profile. POST is limited to observed
read-only initialization and the page's drawer-display preference; course writes are blocked and filled
forms are discarded. No WhatsApp I/O.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright
from chrome_moodle import MoodleBrowser, digest
from dedicated_chrome import PROFILE, profile_endpoint


def main():
    parser=argparse.ArgumentParser();parser.add_argument('run');args=parser.parse_args()
    root=Path(__file__).parent/'reports/shared_chrome'
    states=[]
    for path in (root/'state').glob('*.json'):
        if len(path.stem)!=12:continue
        state=json.loads(path.read_text(encoding='utf-8'))
        if isinstance(state,dict) and state.get('run')==args.run:states.append(state)
    if len(states)!=1:raise ValueError('One exact current failed run is required.')
    state=states[0]
    if (state.get('phase')!='FAILED' or state.get('results') or 'write_started' in state
        or digest(state.get('selected'))!=state.get('manifest_hash')):
        raise ValueError('Rehearsal requires a verified failed manifest with no possible save.')
    report={'run':args.run,'mode':'prepare_only','save_clicks':0,'blocked_write_requests':0,'forms':[]}
    path=root/('form-rehearsal-'+args.run+'.json')
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(profile_endpoint(PROFILE),no_defaults=True,timeout=15000)
        page=browser.contexts[0].new_page()
        def gate(route):
            req=route.request
            allowed=req.method in {'GET','HEAD'}
            if req.method=='POST' and urlparse(req.url).path=='/api/rest/v2/user/current/preferences/drawer-open-index':
                allowed=True  # Existing Moodle page initialization, not a course setting.
            if req.method=='POST' and urlparse(req.url).path=='/repository/draftfiles_ajax.php':
                allowed=parse_qs(urlparse(req.url).query).get('action')==['list']
            if req.method=='POST' and urlparse(req.url).path in {'/lib/ajax/service.php','/lib/ajax/service-nologin.php'}:
                try:
                    payload=req.post_data_json
                    allowed=isinstance(payload,list) and bool(payload) and all(isinstance(item,dict) and item.get('methodname') in {
                        'core_courseformat_get_state','core_message_get_unsent_message','core_output_load_template_with_dependencies',
                        'tiny_autosave_resume_session'} for item in payload)
                except ValueError:pass
            if not allowed:
                report['blocked_write_requests']+=1
                print(json.dumps({'blocked_path':urlparse(req.url).path,'method':req.method}),flush=True)
                route.abort()
            else:route.continue_()
        page.route('**/*',gate)
        try:
            reader=MoodleBrowser(page)
            for cid in sorted({op['course_id'] for op in state['selected']}):
                reader.overview(cid);print(json.dumps({'inventory_ready':cid}),flush=True)
            for op in state['selected']:
                # Discard each unsaved form by closing its owned tab. Never navigate
                # away through a beforeunload dialog shared by multiple CDP clients.
                page.close()
                page=browser.contexts[0].new_page();page.route('**/*',gate);reader.page=page
                row={'target':op['target'],'field_count':len(op['fields'])}
                try:
                    reader.prepare(op)
                    row['status']='prepared_and_values_verified'
                except Exception as exc:
                    row.update(status='failed',error_type=type(exc).__name__,action=reader.last_action)
                    import traceback
                    row['frames']=[{'function':f.name,'line':f.lineno,'file':Path(f.filename).name}
                                   for f in traceback.extract_tb(exc.__traceback__) if 'course_agent_app' in f.filename]
                    row['initialization_pending']=page.evaluate('()=>window.M?.util?.pending_js || []')
                report['forms'].append(row)
                path.write_text(json.dumps(report,indent=2),encoding='utf-8')
                print(json.dumps(row,ensure_ascii=True),flush=True)
                if row['status']=='failed':break
        finally:
            page.close()
            report['all_prepared']=len(report['forms'])==len(state['selected']) and all(
                row['status']=='prepared_and_values_verified' for row in report['forms'])
            path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    if not report['all_prepared']:raise SystemExit(1)


if __name__=='__main__':main()
