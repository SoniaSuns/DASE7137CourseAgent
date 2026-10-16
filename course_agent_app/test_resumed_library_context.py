import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from chrome_file_library import FileLibrary
from run_shared_chrome_agent import Runtime
from question_recovery import validate_failed_retry
import auto_block_recovery as recovery


class ResumedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);sources=self.root/'files';sources.mkdir()
        (sources/'syllabus.txt').write_text('Week 5: Storyboard Development',encoding='utf-8')
        self.library=FileLibrary(sources,self.root/'index.json')
        browser=Mock();browser.forms={};browser.overviews={}
        self.runtime=Runtime(Mock(),browser,['chat'],[],Mock(),self.root/'state',Mock(),
                             library=self.library,library_planner_access=True)
        self.flow=self.runtime.flows['chat'];self.flow.begin('Move files','staff','request')
        self.flow.state.update(phase='QUESTION',resume_phase='PLANNING',mention_authorized=True,
                               verified_previous_results=[{'verified':True}])
        self.runtime.submit=Mock()

    def test_resumed_context_initializes_sources_and_allows_grounded_read(self):
        self.assertIsNone(self.runtime.file_snapshot)
        context=self.runtime.context(self.flow,reply='Move to Week 5')
        self.assertEqual(context['file_library']['status'],'available')
        file_id=context['file_library']['files'][0]['id']
        self.assertNotIn('sha256',context['file_library']['files'][0])
        self.assertEqual(context['file_evidence'],[])
        self.runtime.accept_decision('chat',{'decision':'inspect_files','file_reads':[{'file_id':file_id,'offset':0}]},
                                     {'rounds':0,'repairs':0,'context':context})
        self.assertIn('Storyboard',self.runtime.file_evidence[0]['text'])
        self.assertEqual(self.flow.state['verified_previous_results'],[{'verified':True}])
        self.runtime.submit.assert_called_once();self.runtime.browser.execute.assert_not_called()

    def test_access_authority_and_real_budget_are_preserved(self):
        self.runtime.library_planner_access=False
        context=self.runtime.context(self.flow)
        self.assertEqual(context['file_library']['status'],'planner_access_not_authorized')
        self.assertNotIn('files',context['file_library'])
        request={'decision':'inspect_files','file_reads':[{'file_id':'file:unknown','offset':0}]}
        with self.assertRaisesRegex(ValueError,'not authorized'):
            self.runtime.accept_decision('chat',request,{'rounds':0,'repairs':0})
        self.runtime.library_planner_access=True
        with self.assertRaisesRegex(ValueError,'budget ended'):
            self.runtime.accept_decision('chat',request,{'rounds':8,'repairs':0})

    def test_missing_snapshot_is_not_reported_as_budget_exhaustion(self):
        with self.assertRaisesRegex(ValueError,'snapshot is unavailable'):
            self.runtime.accept_decision('chat',{'decision':'inspect_files','file_reads':[{'file_id':'x','offset':0}]},
                                         {'rounds':0,'repairs':0})

    def test_technical_repair_failure_does_not_claim_to_be_waiting(self):
        self.flow.state['phase']='FAILED'
        self.runtime.auto_block_recovery=True;self.runtime.block_recovery_root=self.root/'jobs'
        self.runtime.send=Mock()
        job=recovery.enqueue(self.runtime.block_recovery_root,self.flow.state,'bad plan',start=False)
        self.flow.state['auto_recovery_job']=job.name
        recovery.write(job/'result.json',{'status':'technical_review','error':'Internal test-module mismatch'})
        self.runtime.service_block_recovery()
        message=self.runtime.send.call_args.args[1]
        self.assertTrue(message.startswith('Status: Failed\n'))
        self.assertIn('technical repair',message)
        self.assertNotIn('Internal test-module mismatch',message)
        self.assertTrue((job/'handled.json').exists())

    def test_failed_retry_preflight_rejects_wrong_run_and_any_write_state(self):
        self.flow.state.update(phase='FAILED');state=self.flow.state;run=state['run']
        self.assertIs(validate_failed_retry([state],[],run),state)
        with self.assertRaises(ValueError):validate_failed_retry([state],[],'0'*12)
        with self.assertRaises(ValueError):validate_failed_retry([state],[{'text':'new request'}],run)
        for change in ({'phase':'QUESTION'},{'selected':[]},{'manifest_hash':'old'},
                       {'write_started':'maybe'},{'results':[{}]},{'mention_authorized':False},
                       {'message_ids':[]},{'approval_sent':True},{'pending_delivery':{'text':'pending'}}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_failed_retry([{**state,**change}],[],run)


if __name__=='__main__':unittest.main()
