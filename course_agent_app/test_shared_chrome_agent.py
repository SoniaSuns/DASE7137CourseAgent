import copy
from concurrent.futures import Future
import json
from pathlib import Path
from datetime import datetime
import tempfile
import unittest
from unittest.mock import Mock

from chrome_approval import ChromeApproval, approval_message
from chrome_course_scope import course
from chrome_moodle import MoodleBrowser
from shared_chrome import SharedChrome, connection_endpoint
from run_shared_chrome_agent import request_record, Runtime


def operation(**overrides):
    return dict(id='one',capability='update_moodle_form',target='140148:module:7',
                course_id='140148',title='Assignment',scope='requested',summary='Extend the due date',
                reason='',depends_on=[],fields=[],**overrides)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'state.json'
        self.flow=ChromeApproval(self.path,'test approval')
        self.flow.begin('Extend assignment deadline','staff-1','message-1')

    def tearDown(self): self.tmp.cleanup()

    def ready(self,operations=None):
        self.flow.offer({},operations or [operation()])
        self.flow.mark_sent()

    def test_scope_is_not_approval(self):
        first=operation()
        related={**operation(),'id':'two','scope':'related','summary':'Adjust cutoff','depends_on':['one']}
        self.flow.offer({},[first,related])
        self.assertEqual(self.flow.state['phase'],'SCOPE')
        self.flow.select(['one'])
        with self.assertRaises(ValueError): self.flow.before_save(first)

    def test_explicit_scope_revision_can_omit_requested_work_without_write_permission(self):
        optional={**operation(),'id':'two','target':'140148:module:8','scope':'related'}
        self.flow.offer({},[operation(),optional])
        self.flow.select(['two'],allow_narrowing=True)
        self.assertEqual([op['id'] for op in self.flow.state['selected']],['two'])
        self.assertEqual(self.flow.state['phase'],'APPROVAL')
        self.assertFalse(self.flow.state['approval_sent'])
        with self.assertRaises(ValueError):self.flow.before_save(optional)

    def test_revised_scope_still_rejects_unknown_empty_and_missing_dependencies(self):
        optional={**operation(),'id':'two','scope':'related','depends_on':['one']}
        self.flow.offer({},[operation(),optional])
        for selected in ([],['unknown'],['two']):
            with self.subTest(selected=selected):
                with self.assertRaises(ValueError):self.flow.select(selected,allow_narrowing=True)

    def test_bound_current_approval_and_exact_plan(self):
        self.ready()
        with self.assertRaises(ValueError): self.flow.approve('OK '+self.flow.state['code'],'other','m2')
        with self.assertRaises(ValueError): self.flow.approve('OK 000000','staff-1','m3')
        self.assertTrue(self.flow.approve('OK '+self.flow.state['code'],'staff-1','m4'))
        op=self.flow.state['selected'][0]
        self.flow.before_save(op)
        with self.assertRaises(ValueError): self.flow.before_save(op)
        self.flow.receipt({'verified':True})
        self.assertEqual(self.flow.state['phase'],'DONE')
        with self.assertRaises(ValueError): self.flow.approve('OK '+self.flow.state['code'],'staff-1','m4')

    def test_mutation_after_approval_rejected(self):
        self.ready(); self.flow.approve('OK '+self.flow.state['code'],'staff-1','m2')
        self.flow.state['selected'][0]['summary']='different'
        with self.assertRaises(ValueError): self.flow.before_save(self.flow.state['selected'][0])

    def test_approval_must_have_been_delivered(self):
        self.flow.offer({},[operation()])
        with self.assertRaises(ValueError): self.flow.approve('OK '+self.flow.state['code'],'staff-1','m2')

    def test_pending_approval_cannot_be_replaced(self):
        self.ready()
        with self.assertRaises(ValueError):self.flow.offer({},[operation()])
        with self.assertRaises(ValueError):self.flow.begin('overwrite','staff-1','m2')

    def test_restart_never_replays_possible_save(self):
        self.ready(); self.flow.approve('OK '+self.flow.state['code'],'staff-1','m2')
        self.flow.before_save(self.flow.state['selected'][0])
        restarted=ChromeApproval(self.path,'test approval')
        self.assertEqual(restarted.state['phase'],'NEEDS_READBACK')
        with self.assertRaises(ValueError): restarted.begin('new task','staff-1','m3')

    def test_restart_invalidates_pending_approval(self):
        self.ready(); restarted=ChromeApproval(self.path,'test approval')
        with self.assertRaises(ValueError): restarted.approve('OK '+restarted.state['code'],'staff-1','m2')

    def test_cancel_has_no_write_permission(self):
        self.ready()
        self.assertFalse(self.flow.approve('NO '+self.flow.state['code'],'staff-1','m2'))
        with self.assertRaises(ValueError): self.flow.before_save(operation())

    def test_omitted_dependency_rejected(self):
        dep={**operation(),'id':'dep','scope':'related'}
        main={**operation(),'depends_on':['dep']}
        self.flow.offer({},[dep,main])
        with self.assertRaises(ValueError): self.flow.select(['one'])

    def test_distinct_chat_cannot_reuse_approval(self):
        self.ready()
        other=ChromeApproval(Path(self.tmp.name)/'other.json','DASE7137 - 2026')
        other.begin('same','staff-1','x1'); other.offer({},[operation()]); other.mark_sent()
        with self.assertRaises(ValueError): other.approve('OK '+self.flow.state['code'],'staff-1','m2')

    def test_optional_same_form_fields_merge_only_after_selection(self):
        first={**operation(),'fingerprint':'same','fields':[{'key':'due','value':'20'}]}
        extra={**operation(),'id':'two','scope':'related','fingerprint':'same',
               'fields':[{'key':'cutoff','value':'21'}]}
        self.flow.offer({},[first,extra]); self.flow.select(['one','two'])
        self.assertEqual(len(self.flow.state['selected']),1)
        self.assertEqual([p['key'] for p in self.flow.state['selected'][0]['fields']],['due','cutoff'])

    def test_conflicting_selected_fields_rejected(self):
        first={**operation(),'fingerprint':'same','fields':[{'key':'due','value':'20'}]}
        extra={**operation(),'id':'two','scope':'related','fingerprint':'same',
               'fields':[{'key':'due','value':'21'}]}
        self.flow.offer({},[first,extra])
        with self.assertRaises(ValueError):self.flow.select(['one','two'])


class ConnectionAndIntakeTests(unittest.TestCase):
    def test_local_endpoint_only(self):
        for endpoint in ['https://evil.test','ws://127.0.0.1.evil.test:9222','http://user:pass@localhost:9222']:
            with self.assertRaises(ValueError):connection_endpoint({'COURSE_AGENT_CDP_URL':endpoint})
        self.assertEqual(connection_endpoint({'COURSE_AGENT_CDP_URL':'http://127.0.0.1:9222'}),'http://127.0.0.1:9222')

    def test_chrome_detach_never_closes_context_or_browser(self):
        s=SharedChrome.__new__(SharedChrome); s.browser=Mock(); page=Mock()
        session=Mock();s.active_page_sessions=[session]
        page.is_closed.return_value=False; s.owned_pages=[page]
        s.close(); page.close.assert_called_once(); s.browser.close.assert_not_called()
        session.detach.assert_called_once()

    def test_profiles_do_not_fall_back_to_sandbox(self):
        self.assertIn('moodle.hku.hk',course('140148').url)
        for value in ['105330','1','https://evil.test']:
            with self.assertRaises(ValueError):course(value)

    def test_multilingual_prompts_are_not_routed_by_phrase(self):
        for text in ['Move the deadline to Friday','将作业截止时间推迟到周五','Class B deadline 改到 Friday']:
            record={'direction':'incoming','sender_id':'x','message_id':'m','text':text}
            self.assertEqual(request_record(record)['text'],text)

    def test_outgoing_unknown_and_uncaptioned_are_not_prompts(self):
        for direction,text in [('outgoing','Status: Done'),('unknown','move groups'),('incoming','')]:
            self.assertIsNone(request_record({'direction':direction,'text':text,'message_id':'m','sender_id':'x'}))

    def test_message_body_excludes_captionless_document_metadata(self):
        self.assertIsNone(request_record({'direction':'incoming','text':'notes.pdf 10:10 AM',
            'message_text':'','documents':['notes.pdf'],'message_id':'m','sender_id':'x'}))

    def test_exact_approval_body_excludes_ui_timestamp(self):
        record=request_record({'direction':'incoming','text':'Staff\nOK 123456\n10:10 AM',
            'message_text':'OK 123456','message_id':'m','sender_id':'x'})
        self.assertEqual(record['text'],'OK 123456')

    def test_old_messages_and_missing_time_do_not_cross_startup_boundary(self):
        from zoneinfo import ZoneInfo
        boundary=datetime(2026,10,5,10,0,tzinfo=ZoneInfo('Asia/Hong_Kong'))
        base={'direction':'incoming','text':'adjust deadline','message_id':'m','sender_id':'x'}
        for meta in ['[09:59, 5/10/2026] Staff:', '', '[10:00, 4/10/2026] Staff:']:
            self.assertIsNone(request_record({**base,'meta':meta},boundary))
        self.assertIsNotNone(request_record({**base,'meta':'[10:01 AM, 10/5/2026] Staff:'},boundary))

    def test_chinese_and_year_first_message_metadata(self):
        from zoneinfo import ZoneInfo
        from run_shared_chrome_agent import record_time
        now=datetime(2026,10,6,14,0,tzinfo=ZoneInfo('Asia/Hong_Kong'))
        expected=now.replace(hour=13,minute=48)
        for metadata in ['[13:48, 2026年10月6日] Staff:', '[13:48, 2026/10/6] Staff:',
                         '[下午1:48, 2026年10月6日] Staff:']:
            self.assertEqual(record_time({'meta':metadata},now),expected)
        self.assertEqual(record_time({'meta':'- ','timestamp':'13:48, 2026年10月6日'},now),expected)


class BrowserIntegrationTests(unittest.TestCase):
    """Actual Playwright and DOM form submission; all Moodle URLs fulfilled locally."""
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start()
        cls.chrome=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.chrome.close();cls.p.stop()

    def setUp(self):
        self.ctx=self.chrome.new_context();self.page=self.ctx.new_page()
        self.value='10';self.writes=0;self.wrong_course=False
        self.page.route('**/*',self.route)
        self.browser=MoodleBrowser(self.page)
        self.browser.targets['140148:module:7']={'key':'140148:module:7','course_id':'140148',
            'id':'7','kind':'module','title':'Assignment','edit_url':'https://moodle.hku.hk/course/modedit.php?update=7'}

    def tearDown(self): self.ctx.close()

    def route(self,route):
        from urllib.parse import parse_qs
        request=route.request
        if request.method=='POST':
            self.writes+=1;self.value=parse_qs(request.post_data or '')['duedate[day]'][0]
        cid='140147' if self.wrong_course else '140148'
        html=f'''<html><body class="course-{cid}"><form class="mform" method="post">
        <input type="hidden" name="course" value="{cid}"><input type="hidden" name="update" value="7">
        <input type="hidden" name="sesskey" value="secret-not-to-expose">
        <input type="password" name="enrolmentkey" value="do-not-read">
        <label for="id_due_day">Due date day</label><select id="id_due_day" name="duedate[day]">
        <option value="10" {'selected' if self.value=='10' else ''}>10</option>
        <option value="20" {'selected' if self.value=='20' else ''}>20</option></select>
        <input id="id_submitbutton" type="submit" name="submitbutton" value="Save changes">
        </form></body></html>'''
        route.fulfill(status=200,content_type='text/html',body=html)

    def resolved(self):
        form=self.browser.inspect('140148:module:7')
        self.assertNotIn('secret-not-to-expose',json.dumps(form))
        self.assertNotIn('do-not-read',json.dumps(form))
        return self.browser.resolve({**operation(),'fields':[{'key':'id_due_day','value':'20'}]})

    def test_actual_approval_save_and_fresh_readback(self):
        op=self.resolved()
        with tempfile.TemporaryDirectory() as tmp:
            f=ChromeApproval(Path(tmp)/'state.json','test approval')
            f.begin('Extend deadline','staff','m1');f.offer({},[op]);f.mark_sent()
            self.assertIn('10 → 20',approval_message(f.state))
            f.approve('OK '+f.state['code'],'staff','m2')
            result=self.browser.execute(op,lambda:f.before_save(op)); f.receipt(result)
            self.assertEqual(f.state['phase'],'DONE')
        self.assertEqual(self.writes,1);self.assertEqual(self.value,'20')

    def test_drift_blocks_before_save(self):
        op=self.resolved();self.value='20';guard=Mock()
        with self.assertRaises(ValueError):self.browser.execute(op,guard)
        self.assertEqual(self.writes,0);guard.assert_not_called()

    def test_wrong_course_blocks_before_save(self):
        op=self.resolved();self.wrong_course=True
        with self.assertRaises(ValueError):self.browser.execute(op,Mock())
        self.assertEqual(self.writes,0)

    def test_unknown_field_or_option_rejected(self):
        self.resolved()
        for patch in [{'key':'sesskey','value':'x'},{'key':'id_due_day','value':'31'}]:
            with self.assertRaises(ValueError):self.browser.resolve({**operation(),'fields':[patch]})

    def test_no_approval_no_post(self):
        op=self.resolved()
        with tempfile.TemporaryDirectory() as tmp:
            f=ChromeApproval(Path(tmp)/'state.json','test approval')
            f.begin('Change date','staff','m1');f.offer({},[op])
            with self.assertRaises(ValueError):self.browser.execute(op,lambda:f.before_save(op))
        self.assertEqual(self.writes,0)

    def test_runtime_scope_to_approval_to_actual_save(self):
        op=self.resolved()
        extra={**op,'id':'two','scope':'related','summary':'Keep the matching deadline component consistent'}
        with tempfile.TemporaryDirectory() as tmp:
            rt=Runtime(Mock(),self.browser,['test approval','DASE7137 - 2026'],['140148'],Mock(),tmp,Mock())
            rt.send=Mock();rt.poll_safely=Mock(return_value=True)
            f=rt.flows['test approval']; f.begin('Move deadline','staff','m1')
            result={'decision':'plan','operations':[op,extra],
                    'impact_review':{'findings':['Related date component checked'],'inspected_targets':[op['target']],'unresolved_dependencies':[]}}
            rt.accept_decision('test approval',result,{'rounds':0,'repairs':0})
            self.assertEqual(f.state['phase'],'SCOPE');self.assertEqual(self.writes,0)
            rt.accept_decision('test approval',{'decision':'select_scope','selected_ids':['one','two']},{})
            self.assertEqual(f.state['phase'],'APPROVAL');self.assertEqual(self.writes,0)
            rt.queue=[{'chat':'test approval','text':'OK '+f.state['code'],'sender':'staff','id':'m2'}]
            rt.handle_queue()
            self.assertEqual(f.state['phase'],'DONE');self.assertEqual(self.writes,1)

    def test_runtime_reconciles_timeout_without_second_save(self):
        op=self.resolved()
        with tempfile.TemporaryDirectory() as tmp:
            rt=Runtime(Mock(),self.browser,['test approval'],['140148'],Mock(),tmp,Mock());rt.send=Mock();rt.poll_safely=Mock(return_value=True)
            f=rt.flows['test approval'];f.begin('Change due date','staff','m1');f.offer({},[op]);f.mark_sent()
            f.approve('OK '+f.state['code'],'staff','m2')
            original=self.browser.execute
            def saved_then_timeout(operation,guard):
                original(operation,guard)
                raise TimeoutError('Lost response after save')
            self.browser.execute=saved_then_timeout
            rt.execute('test approval')
            self.assertEqual(f.state['phase'],'DONE');self.assertEqual(self.writes,1)

    def test_browser_failure_reports_actual_step_and_redacts_raw_error(self):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        op=self.resolved()
        with tempfile.TemporaryDirectory() as tmp:
            rt=Runtime(Mock(),self.browser,['test approval'],['140148'],Mock(),tmp,Mock());rt.send=Mock();rt.poll_safely=Mock(return_value=True)
            flow=rt.flows['test approval'];flow.begin('Change due date','staff','m1')
            flow.offer({},[op]);flow.mark_sent();flow.approve('OK '+flow.state['code'],'staff','m2')
            def fail(operation,guard):
                self.browser.last_action={'stage':'fill_field','target':operation['target'],'title':'Assignment','field':'id_due_day','label':'Due day'}
                raise BrowserTimeout('sensitive browser diagnostics must not be persisted')
            self.browser.execute=fail
            rt.execute('test approval')
            self.assertEqual(flow.state['phase'],'FAILED')
            self.assertIn('Due day',flow.state['reason']);self.assertIn('Assignment',flow.state['reason'])
            self.assertNotIn('signed in',flow.state['reason'])
            diagnostic=json.loads(next(Path(tmp).glob('execution-failure-*.json')).read_text())
            self.assertEqual(diagnostic['action']['stage'],'fill_field')
            self.assertFalse(diagnostic['possible_save'])
            self.assertNotIn('sensitive',json.dumps(diagnostic));self.assertEqual(self.writes,0)

    def test_server_validation_reason_survives_failed_readback(self):
        def reject(route):
            posted=route.request.method=='POST'
            if posted:self.writes+=1
            error='<div id="id_error_due" class="invalid-feedback" style="display:none">Due must be after opening.</div>' if posted else ''
            route.fulfill(content_type='text/html',body='''<body class="course-140148"><form class="mform" method="post">
                <input type="hidden" name="course" value="140148"><input type="hidden" name="update" value="7">
                <select id="id_due_day" name="duedate[day]"><option value="10" selected>10</option><option value="20">20</option></select>
                <input type="submit" name="submitbutton" id="save" value="Save">'''+error+'</form>')
        self.page.unroute('**/*');self.page.route('**/*',reject)
        op=self.resolved()
        with tempfile.TemporaryDirectory() as tmp:
            rt=Runtime(Mock(),self.browser,['test approval'],['140148'],Mock(),tmp,Mock());rt.send=Mock();rt.poll_safely=Mock(return_value=True)
            flow=rt.flows['test approval'];flow.begin('Move deadline','staff','root')
            flow.offer({},[op]);flow.mark_sent();flow.approve('OK '+flow.state['code'],'staff','approval')
            rt.execute('test approval')
            self.assertEqual(self.writes,1);self.assertEqual(flow.state['phase'],'INTERRUPTED')
            self.assertIn('Due must be after opening',flow.state['save_rejection'])
            rt.reconcile('test approval')
            self.assertEqual(flow.state['phase'],'INTERRUPTED');self.assertEqual(self.writes,1)


class GroupTests(unittest.TestCase):
    def setUp(self):
        from chrome_group_changes import GroupChanges
        self.browser=Mock();self.browser.targets={
            'a':{'id':'1','course_id':'140148','kind':'group','title':'A'},
            'b':{'id':'2','course_id':'140148','kind':'group','title':'B'}}
        self.browser.overview.return_value={'groups':[],'groupings':[]}
        self.groups=GroupChanges(self.browser)
        self.members={'1':True,'2':False}
        self.groups.member_state=lambda t,r:('22',{'member':self.members[t['id']],'candidate':not self.members[t['id']]})
        self.op={**operation(),'capability':'update_group_membership','params':{
            'action':'move','source_group':'a','target_group':'b','user_name':'Alice'}}

    def test_name_must_come_from_current_request(self):
        with self.assertRaises(ValueError):self.groups.resolve(self.op,'Move Bob')

    def test_cross_page_identity_ambiguity_fails_before_write(self):
        self.groups.member_state=lambda t,r:(t['id'],{'member':t['id']=='1','candidate':t['id']=='2'})
        with self.assertRaises(ValueError):self.groups.resolve(self.op,'Move Alice from A to B')

    def test_move_source_failure_rolls_back_target(self):
        resolved=self.groups.resolve(self.op,'Move Alice from A to B')
        def change(target,reference,uid,add,operation=None):
            if target['id']=='1':raise RuntimeError('source failed')
            self.members[target['id']]=add
        self.groups.change_member=change
        guard=Mock()
        with self.assertRaises(RuntimeError):self.groups.execute(resolved,'Move Alice from A to B',guard)
        self.assertEqual(self.members,{'1':True,'2':False});guard.assert_called_once()

    def test_move_saved_but_response_lost_does_not_undo_success(self):
        resolved=self.groups.resolve(self.op,'Move Alice from A to B')
        def change(target,reference,uid,add,operation=None):
            self.members[target['id']]=add
            if target['id']=='1':raise TimeoutError('saved but response lost')
        self.groups.change_member=change
        result=self.groups.execute(resolved,'Move Alice from A to B',Mock())
        self.assertTrue(result['verified']);self.assertEqual(self.members,{'1':False,'2':True})


class WhatsAppNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start()
        cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()

    def setUp(self):self.page=self.browser.new_page(viewport={'width':1600,'height':900})
    def tearDown(self):self.page.close()

    def test_exact_chat_title_does_not_depend_on_window_coordinates(self):
        from whatsapp_web import click_chat_row
        self.page.set_content('''<div id="pane-side" style="position:absolute;left:700px">
        <div role="row" onclick="document.querySelector('#main').innerHTML='<header>test approval</header>'">
        <span title="test approval">test approval</span></div></div><div id="main"></div>''')
        self.assertTrue(click_chat_row(self.page,'test approval'))
        self.assertEqual(self.page.locator('#main header').inner_text(),'test approval')

    def test_search_uses_input_structure_independent_of_ui_language(self):
        from whatsapp_web import open_chat
        self.page.set_content('''<div id="side"><input role="textbox" data-tab="3"
          aria-label="搜索或开始新聊天"></div>
          <div id="pane-side"></div><div id="main"></div>''')
        # Use a fixed fixture handler, not browser code supplied by a planner.
        self.page.locator('input').evaluate('''e=>e.oninput=()=>{
            const row=document.createElement('div'); row.setAttribute('role','row');
            const title=document.createElement('span');title.title='test approval';title.textContent='test approval';
            row.append(title);row.onclick=()=>document.querySelector('#main').innerHTML='<header>test approval</header>';
            document.querySelector('#pane-side').replaceChildren(row);
        }''')
        open_chat(self.page,'test approval')
        self.assertEqual(self.page.locator('#main header').inner_text(),'test approval')


if __name__=='__main__':unittest.main()
