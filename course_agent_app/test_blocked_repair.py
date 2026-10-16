import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from chrome_moodle import digest,MoodleSubmissionUnconfirmed
from run_shared_chrome_agent import Runtime,IMPLEMENTATION_VERSION
from chrome_task_memory import task_code,continuation


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.browser=Mock();self.browser.forms={};self.browser.overviews={}
        self.browser.targets={'140148:module:1':{},'140148:module:2':{}}
        self.runtime=Runtime(Mock(),self.browser,['chat'],['140148'],Mock(),self.root,Mock(),mention_only_chats=['chat'])
        self.runtime.send=Mock();self.runtime.submit=Mock();self.runtime.poll_safely=Mock()
        self.flow=self.runtime.flows['chat'];self.flow.begin('Reorder files','staff','first')
        self.move={'id':'move1','capability':'move_module','target':'140148:module:1','title':'Slides','course_id':'140148',
                   'scope':'requested','summary':'Move slides','depends_on':['done'],'fields':[],
                   'params':{'target_section':'140148:section:9','before_module':''}}
        done={**self.move,'id':'done','capability':'update_moodle_form','depends_on':[]}
        remaining={**self.move,'id':'move2','target':'140148:module:2','depends_on':['move1']}
        selected=[done,self.move,remaining]
        self.flow.state.update(phase='NEEDS_READBACK',selected=selected,manifest_hash=digest(selected),write_started='move1',
            results=[{'verified':True,'change':'Renamed a section'}],mention_authorized=True,
            submission_observation={'target':self.move['target'],'completion':'response_loaded'})
        self.flow.save();self.old=copy.deepcopy(self.flow.state)
        self.proof={'classification':'move_applied_visibility_changed','target':self.move['target'],
            'field':'id_visible','expected_value':'0','observed_value':'1','move_replay_forbidden':True}
        self.runtime.structure.visibility_drift=Mock(return_value=self.proof)
        self.record={'id':'followup','chat':'chat','sender':'staff','self_mentioned':True,
                     'text':'after you move the files please let them invisible','readback_notice_sent':True}

    def test_existing_queued_instruction_replans_same_task_without_writes(self):
        self.runtime.queue=[self.record];self.runtime.handle_queue()
        state=self.flow.state
        self.assertEqual(state['phase'],'PLANNING');self.assertEqual(task_code(state),task_code(self.old))
        self.assertNotEqual(state['run'],self.old['run']);self.assertEqual(state['conversation'],[self.record['text']])
        self.assertEqual(state['verified_previous_results'],self.old['results'])
        self.assertEqual(len(state['completed_operation_memory']),1)
        self.assertEqual(state['previously_selected_scope'][0]['capability'],'update_moodle_form')
        self.assertEqual(state['previously_selected_scope'][1]['depends_on'],['move1'])
        self.assertEqual(state['results'],[]);self.assertNotIn('approval_sent',state)
        self.assertEqual(self.runtime.queue,[]);self.runtime.submit.assert_called_once()
        self.browser.execute.assert_not_called();self.browser.editor_action.assert_not_called()
        self.assertTrue((self.root/('interrupted-save-'+self.old['run']+'.json')).exists())

    def test_other_sender_chat_or_unmentioned_cannot_repair(self):
        for changes in ({'sender':'other'},{'chat':'other'},{'self_mentioned':False},{'text':'OK 123456'},{'id':'first'}):
            self.assertFalse(self.runtime.repair_blocked_request('chat',{**self.record,**changes}))
        self.runtime.structure.visibility_drift.assert_not_called();self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK')

    def test_chinese_followup_is_not_routed_by_magic_english_words(self):
        record={**self.record,'text':'移动后请隐藏这些文件，保留已经完成的章节修改'}
        self.assertTrue(self.runtime.repair_blocked_request('chat',record))
        self.assertEqual(self.flow.state['conversation'],[record['text']])
        self.browser.execute.assert_not_called()

    def test_tampered_manifest_never_unblocks(self):
        self.flow.state['selected'][1]['summary']='tampered'
        with self.assertRaisesRegex(ValueError,'inconsistent'):self.runtime.repair_blocked_request('chat',self.record)
        self.runtime.submit.assert_not_called()

    def test_unsettled_submission_stays_blocked_and_does_not_spin(self):
        self.runtime.structure.visibility_drift.side_effect=MoodleSubmissionUnconfirmed('Still pending')
        self.runtime.queue=[self.record];self.runtime.handle_queue();self.runtime.handle_queue()
        self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK');self.assertEqual(len(self.runtime.queue),1)
        self.assertEqual(self.runtime.structure.visibility_drift.call_count,1);self.runtime.submit.assert_not_called()

    def test_unknown_mixed_changes_cannot_be_reclassified(self):
        self.runtime.structure.visibility_drift.return_value=None
        self.assertFalse(self.runtime.repair_blocked_request('chat',self.record))
        self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK');self.runtime.submit.assert_not_called()

    def test_settled_move_with_unavailable_visibility_automatically_asks_without_replay(self):
        self.proof.update(can_restore=False,expected_value=None,requires_visibility_choice=True,
                          expected_label='Show on course page',observed_label='Hide on course page')
        self.runtime.verify=Mock(side_effect=RuntimeError('Visibility options changed'))
        self.runtime.structure.preflight=Mock(side_effect=ValueError('Move already applied'))
        self.runtime.reconcile('chat')
        state=self.flow.state
        self.assertEqual(state['phase'],'PLANNING');self.assertEqual(state['task_id'],self.old['task_id'])
        self.assertEqual(state['previously_selected_scope'][0]['fields'],[])
        self.assertEqual(state['verified_previous_results'],self.old['results'])
        self.assertTrue(state['readback_repair']['move_replay_forbidden'])
        self.assertIn('Ask the requester',state['recovery_feedback'])
        self.browser.execute.assert_not_called();self.runtime.submit.assert_called_once()

    def test_automatic_recovery_cannot_clear_a_persisted_pending_response(self):
        self.flow.state['submission_observation']['completion']='pending'
        self.runtime.verify=Mock(side_effect=RuntimeError('Pending'))
        self.runtime.reconcile('chat')
        self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK')
        self.runtime.structure.visibility_drift.assert_not_called()

    def test_persisted_pending_submit_cannot_be_cleared_after_restart(self):
        self.flow.state['submission_observation']['completion']='pending'
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.runtime.repair_blocked_request('chat',self.record)
        self.runtime.structure.visibility_drift.assert_not_called();self.runtime.submit.assert_not_called()

    def test_model_cannot_repeat_already_applied_move_and_guard_survives_continuation(self):
        self.runtime.repair_blocked_request('chat',self.record)
        with self.assertRaisesRegex(ValueError,'already confirmed'):
            self.runtime.accept_decision('chat',{'decision':'plan','operations':[self.move]}, {'rounds':0,'repairs':0})
        self.flow.state['phase']='EXPIRED';self.flow.save()
        self.assertTrue(continuation(self.flow.state)['readback_repair']['move_replay_forbidden'])
        self.runtime.validate_repair_plan(self.flow.state,[{'capability':'update_moodle_form','target':self.move['target']}])


if __name__=='__main__':unittest.main()
