"""Real Playwright against local routes: placement, approval and uncertain writes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from unittest.mock import Mock
from playwright.sync_api import sync_playwright
from chrome_moodle import MoodleBrowser,MoodleSubmissionUnconfirmed
from chrome_structure import CourseStructure,layout,MoveVisibilityConflict
from chrome_section_visibility import SectionVisibility
from chrome_approval import ChromeApproval


CID='140148'
def module(n):return f'{CID}:module:{n}'
def section(n):return f'{CID}:section:{n}'


class StructureFixture(unittest.TestCase):
    def setUp(self):
        self.pw=sync_playwright().start();self.chrome=self.pw.chromium.launch(headless=True)
        self.context=self.chrome.new_context();self.page=self.context.new_page()
        self.addCleanup(self.pw.stop);self.addCleanup(self.chrome.close);self.addCleanup(self.context.close)
        self.order={'101':['1','2'],'102':['3']};self.names={'1':'Lecture 4','2':'Lecture 5','3':'Assignment'}
        self.section_names={'101':'Week 4','102':'Week 5'};self.visible={str(n):'0' if n==1 else '1' for n in range(1,4)}
        self.posts=0;self.delay=0;self.reject=False;self.move_shows=False
        self.hidden_sections=set()
        self.page.route('**/*',self.route)
        self.browser=MoodleBrowser(self.page);self.struct=CourseStructure(self.browser)
        self.browser.overview(CID)
        for n in range(1,4):self.browser.inspect(module(n))

    def route(self,route):
        req=route.request;parsed=urlparse(req.url);q=parse_qs(parsed.query);path=parsed.path
        if path=='/lib/ajax/service.php':
            self.posts+=1;args=json.loads(req.post_data)
            if args['action'] in {'section_show','section_hide'}:
                if not self.reject:
                    if args['action']=='section_show':self.hidden_sections.discard(str(args['ids'][0]))
                    else:self.hidden_sections.add(str(args['ids'][0]))
                route.fulfill(status=403 if self.reject else 200,content_type='application/json',body='{}');return
            if not self.reject:
                mid=str(args['ids'][0]);dest=str(args['targetsectionid']);anchor=str(args.get('targetcmid',''))
                for items in self.order.values():
                    if mid in items:items.remove(mid)
                self.order[dest].insert(self.order[dest].index(anchor) if anchor else len(self.order[dest]),mid)
                if self.move_shows:self.visible[mid]='1'
            route.fulfill(status=403 if self.reject else 200,content_type='application/json',body='{}');return
        body='<body class="course-140148">'
        if path=='/course/view.php':
            for index,(sid,items) in enumerate(self.order.items()):
                body+=f'<li class="section {"hidden" if sid in self.hidden_sections else ""}" id="section-{index}" data-id="{sid}" data-number="{index}"><div data-for="section_title"><button>Select section Collapse Expand all</button><h3 class="sectionname">{self.section_names[sid]}</h3></div>'
                body+=f'<a data-action="{"sectionShow" if sid in self.hidden_sections else "sectionHide"}" data-id="{sid}">Visibility</a>'
                for mid in items:body+=f'<div id="module-{mid}" class="activity modtype_assign"><span class="instancename">{self.names[mid]}</span></div>'
                body+='</li>'
            body+='''<script>window.M={cfg:{sesskey:'fixture-only'}};window.require=(names,done)=>done({call:items=>[fetch('/lib/ajax/service.php',{method:'POST',body:JSON.stringify(items[0].args)}).then(r=>{if(!r.ok)throw Error('rejected');return new Promise(resolve=>setTimeout(()=>resolve('{}'),DELAY));})]});</script>'''.replace('DELAY',str(self.delay))
        elif path=='/course/modedit.php':
            mid=q['update'][0]
            if req.method=='POST':
                self.posts+=1;values=parse_qs(req.post_data,keep_blank_values=True)
                self.names[mid]=values['name'][0];self.visible[mid]=values['visible'][0]
            body+=self.form(mid)
        elif path=='/course/editsection.php':
            sid=q['id'][0]
            if req.method=='POST':self.posts+=1;self.section_names[sid]=parse_qs(req.post_data)['name'][0]
            body+=f'<form class="mform" method="post"><input type="hidden" name="id" value="{sid}"><input id="id_name" name="name" value="{self.section_names[sid]}"><button id="save" name="submitbutton" type="submit">Save</button></form>'
        elif path=='/group/index.php':body+='<select id="groups"></select>'
        route.fulfill(content_type='text/html',body=body)

    def form(self,mid):
        hidden=any(mid in self.order[sid] for sid in self.hidden_sections)
        choices=[('0','Hidden'),('1','Available by link')] if hidden else [('0','Hidden'),('1','Visible'),('-1','Available by link')]
        options=''.join(f'<option value="{v}" {"selected" if self.visible[mid]==v else ""}>{label}</option>' for v,label in choices)
        return f'''<form class="mform" method="post"><input type="hidden" name="course" value="{CID}"><input type="hidden" name="update" value="{mid}">
        <input id="id_name" name="name" value="{self.names[mid]}"><select id="id_visible" name="visible">{options}</select>
        <button id="save" name="submitbutton" type="submit">Save</button></form>'''

    def op(self,n=1,destination=102,anchor='',policy=''):
        return self.struct.resolve({'id':f'move{n}','capability':'move_module','target':module(n),'fields':[],
            'params':{'target_section':section(destination),'before_module':module(anchor) if anchor else '','visibility_policy':policy},
            'summary':f'Move lecture {n}','scope':'requested','reason':'staff request','depends_on':[]})

    def flow(self,path,ops):
        flow=ChromeApproval(path,'fixture');flow.begin('Move lectures','staff','one');flow.offer({},ops)
        flow.mark_sent();flow.approve('OK '+flow.state['code'],'staff','two');return flow

    def test_two_moves_use_verified_intermediate_state_preserve_hidden_and_manifest(self):
        ops=[self.op(1),self.op(2)];original=copy.deepcopy(ops)
        with tempfile.TemporaryDirectory() as temp:
            flow=self.flow(Path(temp)/'state.json',ops);completed=[]
            for op in flow.state['selected']:
                result=self.struct.execute(op,lambda:flow.before_save(op),completed);flow.receipt(result);completed.append((op,result))
            self.assertEqual(flow.state['phase'],'DONE');self.assertEqual(flow.state['selected'],original)
        self.assertEqual(self.order,{'101':[],'102':['3','1','2']});self.assertEqual(self.visible['1'],'0');self.assertEqual(self.posts,2)

    def test_reorder_within_section_keeps_ids(self):
        op=self.op(2,101,1);result=self.struct.execute(op,Mock())
        self.assertTrue(result['verified']);self.assertEqual(self.order['101'],['2','1']);self.assertEqual(self.posts,1)

    def test_section_names_exclude_wrapper_controls(self):
        view=self.browser.read_layout(CID)
        self.assertEqual([s['title'] for s in view['sections']],['Week 4','Week 5'])
        op=self.op();self.assertEqual(op['placement_review']['from'],'Week 4')

    def section_op(self):
        self.hidden_sections.add('102');self.browser.read_layout(CID)
        return SectionVisibility(self.browser).resolve({'id':'show','capability':'set_section_visibility',
            'target':section(102),'params':{'action':'show'},'fields':[],
            'summary':'Show Week 5','scope':'requested','reason':'Required for visible files','depends_on':[]})

    def test_section_show_approval_and_readback_keep_layout(self):
        op=self.section_op();tool=SectionVisibility(self.browser);before=copy.deepcopy(self.order)
        self.assertIn('Assignment',op['summary'])
        with tempfile.TemporaryDirectory() as temp:
            flow=self.flow(Path(temp)/'state.json',[op])
            result=tool.execute(op,lambda:flow.before_save(op));flow.receipt(result)
            self.assertEqual(flow.state['phase'],'DONE')
        self.assertNotIn('102',self.hidden_sections);self.assertEqual(self.order,before);self.assertEqual(self.posts,1)

    def test_section_contents_drift_blocks_before_dispatch(self):
        op=self.section_op();self.order['102'].append('2');self.order['101'].remove('2');guard=Mock()
        with self.assertRaisesRegex(ValueError,'contents changed'):SectionVisibility(self.browser).execute(op,guard)
        guard.assert_not_called();self.assertEqual(self.posts,0)

    def test_section_timeout_readback_never_replays(self):
        op=self.section_op();self.delay=1000;self.browser.submit_timeout_seconds=.05
        tool=SectionVisibility(self.browser)
        with self.assertRaises(MoodleSubmissionUnconfirmed):tool.execute(op,Mock())
        with self.assertRaises(MoodleSubmissionUnconfirmed):tool.verify(op)
        self.page.wait_for_timeout(1100)
        self.assertTrue(tool.verify(op)['verified']);self.assertEqual(self.posts,1)

    def test_section_action_requires_approval_before_any_request(self):
        op=self.section_op()
        def denied():raise ValueError('No approval')
        with self.assertRaisesRegex(ValueError,'No approval'):SectionVisibility(self.browser).execute(op,denied)
        self.assertEqual(self.posts,0);self.assertIn('102',self.hidden_sections)

    def test_section_verify_does_not_accept_unchanged_visibility(self):
        op=self.section_op()
        with self.assertRaisesRegex(RuntimeError,'readback'):SectionVisibility(self.browser).verify(op)
        self.assertEqual(self.posts,0)

    def test_external_layout_change_blocks_before_write(self):
        op=self.op();self.order['101'].reverse();guard=Mock()
        with self.assertRaisesRegex(ValueError,'outside'):self.struct.execute(op,guard)
        guard.assert_not_called();self.assertEqual(self.posts,0)

    def test_hidden_destination_conflict_is_found_before_move_is_offered(self):
        self.hidden_sections.add('102');self.browser.read_layout(CID)
        with self.assertRaisesRegex(MoveVisibilityConflict,'hidden section'):self.op(2)
        self.assertEqual(self.posts,0);self.assertIn('2',self.order['101'])

    def test_destination_visibility_change_after_approval_prevents_any_write(self):
        op=self.op(2);guard=Mock();self.hidden_sections.add('102')
        with self.assertRaisesRegex(MoveVisibilityConflict,'visibility changed'):self.struct.execute(op,guard)
        guard.assert_not_called();self.assertEqual(self.posts,0)

    def test_hidden_item_keeps_its_meaning_despite_contextual_option_changes(self):
        self.hidden_sections.add('102');self.browser.read_layout(CID);op=self.op(1)
        result=self.struct.execute(op,Mock(),before_preserve=Mock())
        self.assertTrue(result['verified']);self.assertEqual(self.visible['1'],'0');self.assertEqual(self.posts,1)

    def test_approved_inherit_hidden_has_explicit_summary_and_preserves_hidden_outcome(self):
        self.hidden_sections.add('102');self.browser.read_layout(CID);op=self.op(2,policy='inherit_hidden')
        self.assertIn('hidden from students',op['summary']);guard=Mock();preserve=Mock()
        result=self.struct.execute(op,guard,before_preserve=preserve)
        self.assertTrue(result['verified']);self.assertEqual(self.visible['2'],'0')
        self.assertEqual(self.posts,2);guard.assert_called_once();preserve.assert_called_once()

    def test_same_numeric_value_with_different_label_never_restores_old_visibility(self):
        op=self.op(2);old=self.browser.editor_action
        def move_then_hide(*args,**kwargs):
            old(*args,**kwargs);self.hidden_sections.add('102');self.visible['2']='0'
        self.browser.editor_action=move_then_hide;preserve=Mock()
        with self.assertRaisesRegex(RuntimeError,'settings differ'):self.struct.execute(op,Mock(),before_preserve=preserve)
        proof=self.struct.visibility_drift(op)
        self.assertFalse(proof['can_restore']);self.assertTrue(proof['requires_visibility_choice'])
        self.assertIsNone(proof['expected_value']);self.assertTrue(proof['move_replay_forbidden'])
        self.assertEqual(self.posts,1);preserve.assert_not_called()
        self.names['2']='External edit'
        self.assertIsNone(self.struct.visibility_drift(op))

    def test_no_approval_no_ajax_write(self):
        op=self.op()
        with tempfile.TemporaryDirectory() as temp:
            flow=ChromeApproval(Path(temp)/'state.json','fixture');flow.begin('Move','staff','one');flow.offer({},[op])
            with self.assertRaises(ValueError):self.struct.execute(op,lambda:flow.before_save(op))
        self.assertEqual(self.posts,0)

    def test_late_move_response_readback_never_replays(self):
        op=self.op();self.delay=600;self.browser.submit_timeout_seconds=.05
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.struct.execute(op,Mock())
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.struct.verify(op)
        self.page.wait_for_timeout(800)
        self.assertTrue(self.struct.verify(op)['verified']);self.assertEqual(self.posts,1)

    def test_pending_move_rebinds_to_its_original_tab(self):
        op=self.op();self.delay=600;self.browser.submit_timeout_seconds=.05
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.struct.execute(op,Mock())
        original=self.browser.page;self.browser.page=self.context.new_page()
        self.assertTrue(self.browser.rebind_pending_page(self.context));self.assertIs(self.browser.page,original)
        self.page.wait_for_timeout(800);self.assertTrue(self.struct.verify(op)['verified']);self.assertEqual(self.posts,1)

    def test_rejected_move_is_not_claimed_verified(self):
        op=self.op();self.reject=True
        with self.assertRaises(RuntimeError):self.struct.execute(op,Mock())
        self.assertEqual(self.order['101'],['1','2']);self.assertEqual(self.posts,1)

    def test_section_rename_uses_observed_form(self):
        key=section(102);self.browser.inspect(key)
        op=self.browser.resolve({'id':'rename','capability':'update_moodle_form','target':key,'summary':'Rename week',
            'scope':'requested','depends_on':[],'fields':[{'key':'id_name','value':'Week 5 - Lectures'}],'params':{}})
        self.assertTrue(self.browser.execute(op,Mock())['verified']);self.assertEqual(self.section_names['102'],'Week 5 - Lectures')

    def test_move_after_approved_visibility_change_preserves_updated_settings(self):
        move=self.op();change=self.browser.resolve({'id':'hide','capability':'update_moodle_form','target':module(1),
            'summary':'Show lecture','fields':[{'key':'id_visible','value':'1'}],'params':{}})
        receipt=self.browser.execute(change,Mock())
        self.assertTrue(self.struct.execute(move,Mock(),[(change,receipt)])['verified']);self.assertEqual(self.visible['1'],'1')

    def test_cross_course_destination_rejected(self):
        op=self.op();self.browser.targets[section(102)]['course_id']='140147'
        with self.assertRaisesRegex(ValueError,'cross courses'):self.struct.resolve(op)

    def test_move_preserves_visibility_when_moodle_changes_it(self):
        self.move_shows=True;op=self.op()
        with tempfile.TemporaryDirectory() as temp:
            flow=self.flow(Path(temp)/'state.json',[op])
            result=self.struct.execute(op,lambda:flow.before_save(op),
                before_preserve=lambda:flow.before_preservation_save(op))
            flow.receipt(result);self.assertEqual(flow.state['phase'],'DONE')
        self.assertEqual(self.posts,2);self.assertEqual(self.visible['1'],'0')
        self.assertEqual(self.order['102'],['3','1'])

    def test_blocked_move_drift_is_read_only_and_specific(self):
        self.move_shows=True;op=self.op()
        with self.assertRaisesRegex(RuntimeError,'id_visible'):self.struct.execute(op,Mock())
        proof=self.struct.visibility_drift(op)
        self.assertEqual(proof['expected_value'],'0');self.assertEqual(proof['observed_value'],'1')
        self.assertTrue(proof['move_replay_forbidden']);self.assertEqual(self.posts,1)
        self.names['1']='Unexpected content change'
        self.assertIsNone(self.struct.visibility_drift(op));self.assertEqual(self.posts,1)

    def test_pending_move_cannot_be_classified_as_repairable(self):
        self.move_shows=True;self.delay=600;self.browser.submit_timeout_seconds=.05;op=self.op()
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.struct.execute(op,Mock(),before_preserve=Mock())
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.struct.visibility_drift(op)
        self.assertEqual(self.posts,1)

    def test_preservation_requires_executing_manifest_and_cannot_repeat(self):
        op=self.op()
        with tempfile.TemporaryDirectory() as temp:
            flow=self.flow(Path(temp)/'state.json',[op])
            with self.assertRaises(ValueError):flow.before_preservation_save(op)
            flow.before_save(op);flow.before_preservation_save(op)
            with self.assertRaises(ValueError):flow.before_preservation_save(op)
            flow.fail('uncertain preservation')
            with self.assertRaises(ValueError):flow.before_preservation_save(op)

    def test_preservation_response_failure_does_not_repeat_move_or_save(self):
        self.move_shows=True;op=self.op();original=self.browser.execute
        def fail_after_save(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('fixture lost response after preservation')
        self.browser.execute=fail_after_save
        with self.assertRaisesRegex(RuntimeError,'lost response'):
            self.struct.execute(op,Mock(),before_preserve=Mock())
        self.assertEqual(self.posts,2);self.assertTrue(self.struct.verify(op)['verified'])
        self.assertEqual(self.posts,2)

    def test_operator_retires_only_marked_legacy_section_tab_then_reads_without_save(self):
        self.context.route('**/*',self.route)
        key=section(102);self.browser.inspect(key)
        op=self.browser.resolve({'id':'rename','capability':'update_moodle_form','target':key,'summary':'Rename',
            'scope':'requested','depends_on':[],'fields':[{'key':'id_name','value':'Week 9'}],'params':{}})
        self.browser.prepare(op);old=self.browser.page;old.evaluate('()=>window.name="legacy-test-marker"')
        obs={'target':key,'completion':'pending','post_requests':0,'response_status':None,
             'click_error_type':'TimeoutError','page_marker':'legacy-test-marker'}
        result=self.browser.retire_unobserved_section_submit(op,obs)
        self.assertTrue(old.is_closed());self.assertEqual(result['completion'],'retired_unobserved_attempt')
        self.assertTrue(self.browser.unchanged_after_attempt(op));self.assertEqual(self.posts,0)
        self.browser.page.close()

    def test_legacy_retirement_rejects_observed_posts_or_instrumented_clicks(self):
        key=section(102);self.browser.inspect(key)
        op=self.browser.resolve({'id':'rename','capability':'update_moodle_form','target':key,'summary':'Rename',
            'scope':'requested','depends_on':[],'fields':[{'key':'id_name','value':'Week 9'}],'params':{}})
        base={'target':key,'completion':'pending','post_requests':0,'response_status':None,
              'click_error_type':'TimeoutError','page_marker':'legacy-test-marker'}
        for patch in ({'post_requests':1},{'probe_token':'instrumented'},{'response_status':200}):
            with self.assertRaises(ValueError):self.browser.retire_unobserved_section_submit(op,{**base,**patch})
        self.assertFalse(self.page.is_closed());self.assertEqual(self.posts,0)

    def test_old_background_count_requires_live_zero_event_probe_before_retirement(self):
        self.context.route('**/*',self.route)
        key=section(102);self.browser.inspect(key)
        op=self.browser.resolve({'id':'rename','capability':'update_moodle_form','target':key,'summary':'Rename',
            'scope':'requested','depends_on':[],'fields':[{'key':'id_name','value':'Week 9'}],'params':{}})
        self.browser.prepare(op);old=self.browser.page
        old.evaluate('''()=>{window.name='old-count';window.__courseAgentSubmitProbe={token:'probe',document,
            events:{pointerdown:0,mousedown:0,click:1,submit:0}}}''')
        obs={'target':key,'completion':'pending','post_requests':0,'write_requests':2,'response_status':None,
             'click_error_type':'TimeoutError','page_marker':'old-count','probe_token':'probe',
             'dispatch_probe':{'ready':'complete','events':{'pointerdown':0,'mousedown':0,'click':0,'submit':0}}}
        with self.assertRaisesRegex(ValueError,'probe changed'):self.browser.retire_unobserved_section_submit(op,obs)
        self.assertFalse(old.is_closed())
        old.evaluate('()=>window.__courseAgentSubmitProbe.events.click=0')
        # The current observation format may have unknown writes and cannot use this old-format recovery.
        with self.assertRaises(ValueError):self.browser.retire_unobserved_section_submit(op,{**obs,'background_requests':0})
        result=self.browser.retire_unobserved_section_submit(op,obs)
        self.assertTrue(old.is_closed());self.assertEqual(result['completion'],'retired_unobserved_attempt')
        self.assertTrue(self.browser.unchanged_after_attempt(op));self.assertEqual(self.posts,0)
        self.browser.page.close()


if __name__=='__main__':unittest.main()
