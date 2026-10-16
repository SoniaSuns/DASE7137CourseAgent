"""Explicit capability audit; original failures are retained as regressions.

This file is deliberately outside test*.py discovery; no production writes or
WhatsApp messages. Run explicitly with python -m unittest audit_capability_conflicts.
"""
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from playwright.sync_api import sync_playwright
from run_shared_chrome_agent import Runtime
from chrome_moodle import MoodleBrowser
from chrome_group_changes import GroupChanges
from chrome_planner import schema
from chrome_availability import date_changes


class CapabilityBoundaries(unittest.TestCase):
    def test_supported_and_remaining_executor_lanes_are_explicit(self):
        allowed=schema()['properties']['operations']['items']['properties']['capability']['enum']
        for capability in ['move_module','upload_file_resource','replace_file_resource']:
            self.assertIn(capability,allowed)
        for capability in ['delete_module','delete_group','copy_module_between_courses']:
            with self.subTest(capability=capability):self.assertNotIn(capability,allowed)

    def test_hidden_section_identity_cannot_be_used_as_a_move_field(self):
        browser=MoodleBrowser(Mock())
        browser.forms={'resource':{'target':'resource','fields':[{'key':'id_name','name':'name','type':'text',
                           'value':'Lecture 4','options':[],'rich':False}]}}
        with self.assertRaisesRegex(ValueError,'not observed'):
            browser.resolve({'target':'resource','capability':'update_moodle_form',
                             'fields':[{'key':'id_section','value':'5'}]})

    def test_new_or_removed_release_condition_is_not_supported_by_timestamp_editor(self):
        empty={'op':'&','c':[],'showc':[]}
        restricted={'op':'&','c':[{'type':'date','d':'>=','t':1791504000}],'showc':[True]}
        for before,after in [(empty,restricted),(restricted,empty)]:
            with self.subTest(before=before),self.assertRaises(ValueError):
                date_changes(json.dumps(before),json.dumps(after))

    def test_rich_text_rejects_active_content(self):
        from chrome_rich_text import canonical
        with self.assertRaises(ValueError):canonical('<script>arbitraryCode()</script>')


class DesiredBehaviorDefects(unittest.TestCase):
    def test_group_reader_should_follow_replaced_work_tab(self):
        old=Mock(url='about:blank');new=Mock(url='https://moodle.hku.hk/group/assign.php?id=2')
        new.locator.return_value.count.return_value=1
        new.evaluate.return_value={'existing':['7'],'potential':['8']}
        browser=Mock(page=old);groups=GroupChanges(browser)
        def navigate(*args):browser.page=new
        browser.goto.side_effect=navigate
        self.assertEqual(groups.grouping_state({'id':'2','course_id':'140148'}),{'existing':['7'],'potential':['8']})

    def test_multi_operation_execution_should_yield_to_chat_intake(self):
        with tempfile.TemporaryDirectory() as temp:
            browser=Mock();runtime=Runtime(Mock(),browser,['chat'],[],Mock(),Path(temp),Mock())
            runtime.send=Mock();runtime.poll_safely=Mock(return_value=True)
            flow=runtime.flows['chat'];flow.begin('Change three deadlines','staff','root')
            operations=[{'id':str(i),'target':str(i),'course_id':'140148','title':'Assignment',
                'capability':'update_moodle_form','scope':'requested','summary':'Update deadline',
                'fields':[],'depends_on':[]} for i in range(3)]
            flow.offer({},operations);flow.mark_sent();flow.approve('OK '+flow.state['code'],'staff','approval')
            def execute(op,guard):guard();return {'verified':True,'change':op['summary']}
            browser.execute.side_effect=execute
            runtime.execute('chat')
            self.assertEqual(flow.state['phase'],'DONE')
            self.assertGreater(runtime.poll_safely.call_count,0,'Long execution never returns to WhatsApp intake between operations.')


class GroupSubmitTiming(unittest.TestCase):
    def test_delayed_group_submit_should_finish_before_readback(self):
        with sync_playwright() as p:
            chrome=p.chromium.launch(headless=True)
            context=chrome.new_context();page=context.new_page();state={'member':False,'posts':0}
            def route(route):
                if route.request.method=='POST':state.update(member=True,posts=state['posts']+1)
                option='<option value="22">Fixture participant</option>'
                route.fulfill(content_type='text/html',body='''<body class="course-140148">
                  <form method="post" onsubmit="event.preventDefault();const f=this;setTimeout(()=>f.submit(),600)">
                  <select id="addselect" name="addselect">'''+('' if state['member'] else option)+'''</select>
                  <select id="removeselect" name="removeselect">'''+(option if state['member'] else '')+'''</select>
                  <button id="add" type="submit">Add</button><button id="remove" type="submit">Remove</button></form>''')
            page.route('**/*',route)
            try:
                groups=GroupChanges(MoodleBrowser(page))
                groups.change_member({'id':'2','course_id':'140148'},{'user_id':'22'},'22',True)
                self.assertTrue(state['member']);self.assertEqual(state['posts'],1)
            finally:context.close();chrome.close()


if __name__=='__main__':unittest.main()
