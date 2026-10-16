import json
import unittest
from chrome_rich_text import canonical
from chrome_availability import validate_tree
from chrome_approval import display_value
from audit_capability_conflicts import DesiredBehaviorDefects,GroupSubmitTiming
from unittest.mock import Mock
import tempfile
from pathlib import Path
from run_shared_chrome_agent import Runtime


class EditorValidation(unittest.TestCase):
    def test_formatted_html_and_links_are_data(self):
        self.assertEqual(canonical('<p><strong>Week 5</strong><br>Lecture</p>'),canonical('<p><strong>Week 5</strong><br/>Lecture</p>'))
        canonical('<table><tr><td style="text-align: center">Lecture</td></tr></table><a href="https://example.org">Source</a>')

    def test_active_content_rejected(self):
        for value in ['<script>alert(1)</script>','<a href="javascript:alert(1)">x</a>','<p onclick="x()">x</p>',
                      '<p style="color:expression(x)">x</p>','<iframe src="https://example.org"></iframe>']:
            with self.subTest(value=value),self.assertRaises(ValueError):canonical(value)

    def test_add_remove_dates_preserve_opaque_conditions(self):
        opaque={'type':'completion','cm':7,'e':1}
        before={'op':'&','c':[opaque],'showc':[True]}
        after={'op':'&','c':[opaque,{'type':'date','d':'>=','t':1791504000}],'showc':[True,False]}
        validate_tree(json.dumps(before),json.dumps(after),{},'140148')
        validate_tree(json.dumps(after),json.dumps(before),{},'140148')
        with self.assertRaisesRegex(ValueError,'Preserve'):
            validate_tree(json.dumps(after),json.dumps({'op':'&','c':[],'showc':[]}),{},'140148')

    def test_group_restriction_requires_same_course_observation(self):
        value=json.dumps({'op':'&','c':[{'type':'group','id':7}],'showc':[True]})
        targets={'g':{'kind':'group','id':'7','course_id':'140148'}}
        validate_tree('',value,targets,'140148')
        with self.assertRaisesRegex(ValueError,'observed group'):validate_tree('',value,targets,'140147')

    def test_visibility_and_timestamp_review(self):
        value=json.dumps({'op':'&','c':[{'type':'date','d':'>=','t':1791504000}],'showc':[False]})
        validate_tree('',value,{},'140148')
        text=display_value({'key':'id_availabilityconditionsjson'},value)
        self.assertIn('available from',text);self.assertIn('hidden when unmet',text)


class ExecutionBoundaries(unittest.TestCase):
    def test_stop_between_operations_preserves_verified_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            browser=Mock();runtime=Runtime(Mock(),browser,['chat'],[],Mock(),Path(tmp),Mock());runtime.send=Mock()
            flow=runtime.flows['chat'];flow.begin('Two changes','staff','one')
            ops=[{'id':str(i),'target':str(i),'capability':'update_moodle_form','summary':'Change','scope':'requested','fields':[],'depends_on':[]} for i in range(2)]
            flow.offer({},ops);flow.mark_sent();flow.approve('OK '+flow.state['code'],'staff','two')
            def poll():
                if flow.state['results']:runtime.stopped=True
                return True
            runtime.poll_safely=Mock(side_effect=poll)
            def execute(op,guard):guard();return {'verified':True,'change':'Saved'}
            browser.execute.side_effect=execute;runtime.execute('chat')
            self.assertEqual(flow.state['phase'],'INTERRUPTED');self.assertEqual(len(flow.state['results']),1)
            self.assertEqual(browser.execute.call_count,1)


if __name__=='__main__':unittest.main()
