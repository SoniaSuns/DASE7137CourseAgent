import copy
from concurrent.futures import Future
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from run_shared_chrome_agent import Runtime
from chrome_file_library import FileLibrary


class SourceVersionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);sources=root/'files';sources.mkdir()
        self.source=sources/'syllabus.txt';self.source.write_text('Week 5: Original topic',encoding='utf-8')
        self.library=FileLibrary(sources,root/'index.json')
        browser=Mock();browser.forms={};browser.overviews={};browser.targets={}
        self.runtime=Runtime(Mock(),browser,['chat'],[],Mock(),root/'state',Mock(),
                             library=self.library,library_planner_access=True)
        self.runtime.send=Mock();self.runtime.submit=Mock()
        self.flow=self.runtime.flows['chat'];self.flow.begin('Use the internal syllabus','staff','message')
        self.flow.state.update(mention_authorized=True,conversation=['Keep the assignment in General.'],
                               verified_previous_results=[{'verified':True}]*9,
                               completed_operation_memory=[{'receipt':'old'}],schedule_contract={'stale':True})
        self.runtime.file_snapshot=self.library.snapshot()
        self.file_id=self.runtime.file_snapshot['files'][0]['id']
        self.runtime.file_evidence=[self.library.inspect(self.runtime.file_snapshot,self.file_id)]

    def change_error(self,text='Week 6: Updated topic'):
        self.source.write_text(text,encoding='utf-8')
        try:self.library.read_bytes(self.runtime.file_snapshot,self.file_id)
        except ValueError as exc:return exc
        self.fail('Changed bytes must never pass the old snapshot guard.')

    def finish_error(self,error):
        future=Future();future.set_exception(error)
        self.runtime.jobs['chat']={'future':future,'context':{},'rounds':7,'repairs':1}
        self.runtime.complete_jobs()

    def test_change_is_named_and_unapproved_plan_gets_fresh_empty_evidence(self):
        before=copy.deepcopy(self.flow.state)
        error=self.change_error()
        self.assertIn('syllabus.txt',str(error))
        self.finish_error(error)
        self.assertEqual(self.flow.state['phase'],'PLANNING')
        self.assertEqual(self.runtime.file_evidence,[])
        self.assertEqual(self.flow.state['schedule_contract'],{})
        for key in ('run','task_id','task','conversation','verified_previous_results','completed_operation_memory'):
            self.assertEqual(self.flow.state[key],before[key])
        context=self.runtime.submit.call_args.args[1]
        self.assertEqual(context['file_evidence'],[])
        self.assertIn('Read the changed source again',context['feedback'])
        self.assertEqual(self.runtime.submit.call_args.args[2:],(0,0))
        fresh=self.library.inspect(self.runtime.file_snapshot,self.file_id)
        self.assertIn('Updated topic',fresh['text'])
        self.runtime.browser.execute.assert_not_called()

    def test_refresh_is_bounded_and_repeated_changes_fail_closed(self):
        self.finish_error(self.change_error())
        self.assertEqual(self.flow.state['phase'],'PLANNING')
        self.runtime.submit.reset_mock()
        self.finish_error(self.change_error('Week 7: Changed again'))
        self.assertEqual(self.flow.state['phase'],'FAILED')
        self.runtime.submit.assert_not_called()

    def test_offered_or_possible_write_markers_never_refresh(self):
        error=self.change_error()
        before=copy.deepcopy(self.flow.state)
        snapshot=copy.deepcopy(self.runtime.file_snapshot)
        for change in ({'phase':'APPROVAL'},{'selected':[]},{'manifest_hash':''},{'write_started':None},
                       {'approval_sent':True},{'results':[{}]},{'candidates':[]}):
            with self.subTest(change=change):
                self.flow.state={**copy.deepcopy(before),**change}
                self.runtime.submit.reset_mock()
                self.finish_error(error)
                self.runtime.submit.assert_not_called()
                self.assertEqual(self.runtime.file_snapshot,snapshot)
                self.runtime.browser.execute.assert_not_called()

    def test_permission_failure_is_not_treated_as_a_version_change(self):
        snapshot=copy.deepcopy(self.runtime.file_snapshot)
        self.finish_error(PermissionError('Source unavailable'))
        self.assertEqual(self.runtime.file_snapshot,snapshot)
        self.assertEqual(self.flow.state['phase'],'FAILED')
        self.runtime.submit.assert_not_called()


if __name__=='__main__':unittest.main()
