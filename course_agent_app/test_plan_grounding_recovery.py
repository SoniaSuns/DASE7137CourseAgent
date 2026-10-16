import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from run_shared_chrome_agent import Runtime


class PlanGroundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.browser=Mock();self.browser.forms={};self.browser.targets={'a':{},'b':{}};self.browser.overviews={}
        self.runtime=Runtime(Mock(),self.browser,['chat'],[],Mock(),self.root,Mock())
        self.runtime.send=Mock();self.runtime.submit=Mock();self.runtime.poll_safely=Mock()
        self.flow=self.runtime.flows['chat'];self.flow.begin('Move dates','staff','message')
        self.result={'decision':'plan','impact_review':{'findings':['Related dates reviewed'],'inspected_targets':['a'],
                      'unresolved_dependencies':[]},'operations':[{'id':'one','capability':'update_moodle_form',
                      'target':'b','fields':[{'key':'day','value':'20'}]}]}
        self.job={'rounds':2,'repairs':1}

    def tearDown(self):self.tmp.cleanup()

    def test_known_missing_forms_are_read_then_replanned_without_offering_or_saving(self):
        def read(key):self.browser.forms[key]={'target':key,'fields':[{'key':'day','name':'day','type':'text','value':'10'}]}
        self.browser.inspect.side_effect=read
        self.runtime.accept_decision('chat',self.result,self.job)
        self.assertEqual([c.args[0] for c in self.browser.inspect.call_args_list],['a','b'])
        self.assertEqual(self.runtime.submit.call_args.args[2:],(3,1))
        self.assertEqual(len(self.runtime.submit.call_args.args[1]['forms']),2)
        self.assertEqual(self.flow.state['phase'],'PLANNING')
        self.browser.resolve.assert_not_called();self.browser.execute.assert_not_called()

    def test_unknown_targets_never_trigger_arbitrary_browser_inspection(self):
        self.result['impact_review']['inspected_targets']=['outside_inventory']
        with self.assertRaisesRegex(ValueError,'current course inventory'):
            self.runtime.accept_decision('chat',self.result,self.job)
        self.browser.inspect.assert_not_called()
        self.assertIn('outside_inventory',self.runtime.context(self.flow)['planning_validation']['unknown_targets'])

    def test_invalid_and_duplicate_fields_get_exact_structured_feedback(self):
        self.browser.forms={k:{'target':k,'fields':[{'key':'day','name':'day','type':'text','value':'10'}]} for k in ('a','b')}
        self.result['operations'][0]['fields']=[{'key':'guessed_day','value':'private-do-not-log'},
            {'key':'day','value':'20'},{'key':'day','value':'20'}]
        with self.assertRaisesRegex(ValueError,'field references'):
            self.runtime.accept_decision('chat',self.result,self.job)
        feedback=self.runtime.context(self.flow)['planning_validation']
        self.assertEqual(feedback['invalid_fields'][0]['unknown_keys'],['guessed_day'])
        self.assertEqual(feedback['invalid_fields'][0]['duplicate_keys'],['day'])
        self.assertNotIn('private-do-not-log',next(self.root.glob('plan-grounding-*.json')).read_text())
        self.browser.execute.assert_not_called()

    def test_missing_read_budget_is_bounded(self):
        with self.assertRaisesRegex(ValueError,'inspection budget'):
            self.runtime.accept_decision('chat',self.result,{'rounds':8,'repairs':1})
        self.browser.inspect.assert_not_called()

    def test_visibility_prerequisite_cannot_share_stale_form_approval(self):
        self.result['operations'].append({'id':'show','capability':'set_section_visibility','target':'a'})
        with self.assertRaisesRegex(ValueError,'separate stage'):
            self.runtime.accept_decision('chat',self.result,self.job)
        self.browser.resolve.assert_not_called();self.browser.execute.assert_not_called()

    def test_start_archives_previous_failed_goal_and_receipts(self):
        old=self.flow.state['run'];self.flow.state.update(phase='FAILED',verified_previous_results=[{'verified':True}],reason='Planning rejected')
        self.runtime.start('chat',{'text':'Follow-up','sender':'staff','id':'next','self_mentioned':True})
        archived=json.loads((self.root/('archived-run-'+old+'.json')).read_text())
        self.assertEqual(archived['task'],'Move dates');self.assertEqual(len(archived['verified_previous_results']),1)
        self.assertNotEqual(old,self.flow.state['run'])


if __name__=='__main__':unittest.main()
