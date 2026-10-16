import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from chrome_form_dates import validate, review, MoodleDateConflict, interval_evidence
from chrome_moodle import digest
from run_shared_chrome_agent import Runtime


def fields():
    result=[]
    for name,day in [('allowsubmissionsfromdate',8),('duedate',15),('gradingduedate',22),('cutoffdate',23)]:
        for part,value in {'enabled':True,'year':'2026','month':'11','day':str(day),'hour':'18','minute':'59'}.items():
            result.append({'key':'id_'+name+'_'+part,'name':name+'['+part+']',
                           'type':'checkbox' if part=='enabled' else 'select-one','value':value})
    return result


class DateTests(unittest.TestCase):
    def test_interval_evidence_measures_actual_gap_without_inventing_new_dates(self):
        original=fields();evidence=interval_evidence(original)
        gap=next(x for x in evidence['intervals'] if x['from']=='allowsubmissionsfromdate' and x['to']=='duedate')
        self.assertEqual(gap['local_wall_clock_minutes'],7*24*60)
        for f in original:
            if f['name']=='duedate[day]':f['value']='10'
            if f['name']=='duedate[minute]':f['value']='29'
        changed=interval_evidence(original)
        gap=next(x for x in changed['intervals'] if x['from']=='allowsubmissionsfromdate' and x['to']=='duedate')
        self.assertEqual(gap['local_wall_clock_minutes'],2*24*60-30)
        self.assertNotIn('proposed_date',changed)

    def test_disabled_dates_do_not_create_intervals(self):
        original=fields()
        for f in original:
            if f['name']=='cutoffdate[enabled]':f['value']=False
        evidence=interval_evidence(original)
        self.assertFalse(evidence['dates']['cutoffdate']['enabled'])
        self.assertFalse(any('cutoffdate' in (x['from'],x['to']) for x in evidence['intervals']))

    def test_equal_or_reversed_submission_interval_is_rejected(self):
        for day in ('2','9'):
            patches=[{'key':'id_allowsubmissionsfromdate_day','value':day},{'key':'id_duedate_day','value':'2'}]
            with self.assertRaisesRegex(MoodleDateConflict,'must be later than'):
                validate(fields(),patches,'Interactive Presentation')

    def test_valid_interval_and_disabled_dates(self):
        validate(fields(),[{'key':'id_duedate_day','value':'9'}])
        validate(fields(),[{'key':'id_allowsubmissionsfromdate_enabled','value':False},{'key':'id_duedate_day','value':'2'}])

    def test_related_cutoff_and_grading_relationships(self):
        for key in ('id_cutoffdate_day','id_gradingduedate_day'):
            with self.assertRaisesRegex(MoodleDateConflict,'at or after'):
                validate(fields(),[{'key':key,'value':'10'}])

    def test_full_datetime_review_and_impossible_calendar_date(self):
        rows=review(fields(),[{'key':'id_duedate_day','value':'9'}])
        self.assertEqual(rows,[{'label':'Due date','before':'2026-11-15 18:59','after':'2026-11-09 18:59'}])
        with self.assertRaisesRegex(MoodleDateConflict,'valid calendar date'):
            validate(fields(),[{'key':'id_duedate_day','value':'31'}])


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.browser=Mock();self.runtime=Runtime(Mock(),self.browser,['group'],[],Mock(),self.root,Mock(),mention_only_chats=['group'])
        self.runtime.send=Mock();self.runtime.verify=Mock(side_effect=RuntimeError('Different saved values'))
        self.runtime.poll_safely=Mock(return_value=True)
        self.flow=self.runtime.flows['group'];self.flow.begin('Move dates','staff','root')
        ops=[{'id':str(i),'target':str(i),'title':'Assignment','capability':'update_moodle_form','scope':'requested',
              'summary':'Move date','fields':[],'target_kind':'module'} for i in range(5)]
        self.flow.state.update(phase='NEEDS_READBACK',mention_authorized=True,selected=ops,
                               results=[{'verified':True}]*3,manifest_hash=digest(ops),write_started='3')
        self.browser.forms={'3':{'fields':fields()}}
        self.record={'id':'new','chat':'group','sender':'staff','text':'Please compare the syllabus','self_mentioned':True}
        self.runtime.queue=[self.record]

    def tearDown(self):self.temp.cleanup()

    def test_original_snapshot_releases_queue_without_replaying_or_losing_remaining_work(self):
        self.browser.unchanged_after_attempt.return_value=True
        self.runtime.handle_queue()
        self.assertEqual(self.flow.state['phase'],'INTERRUPTED')
        self.assertEqual(len(self.flow.state['results']),3)
        self.assertEqual(self.runtime.queue[0]['id'],'new')
        self.assertEqual(len(self.runtime.queue[1]['previously_selected_scope']),2)
        self.assertEqual(len(self.runtime.queue[1]['verified_previous_results']),3)
        self.assertNotIn('code',self.runtime.queue[1])
        self.browser.execute.assert_not_called()
        self.assertTrue(list(self.root.glob('reconciled-*.json')))

    def test_mixed_or_unavailable_state_stays_locked_but_acknowledges_once(self):
        for outcome in (False,RuntimeError('Read failed')):
            with self.subTest(outcome=str(outcome)):
                self.record.pop('readback_notice_sent',None);self.runtime.send.reset_mock()
                if isinstance(outcome,Exception):self.browser.unchanged_after_attempt.side_effect=outcome
                else:self.browser.unchanged_after_attempt.return_value=outcome
                self.runtime.handle_queue();count=self.runtime.send.call_count
                self.runtime.handle_queue()
                self.assertEqual(self.runtime.send.call_count,count)
                self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK');self.assertEqual(len(self.runtime.queue),1)
                self.browser.execute.assert_not_called()

    def test_changed_manifest_cannot_clear_uncertain_save(self):
        self.flow.state['manifest_hash']='changed'
        self.runtime.reconcile('group')
        self.runtime.verify.assert_not_called();self.browser.unchanged_after_attempt.assert_not_called()
        self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK')

    def test_pending_submission_is_not_cleared_by_original_state_after_restart(self):
        self.flow.state['submission_observation']={'target':'3','started':123,'completion':'pending'}
        self.browser.unchanged_after_attempt.return_value=True
        self.runtime.reconcile('group')
        self.browser.unchanged_after_attempt.assert_not_called()
        self.assertEqual(self.flow.state['phase'],'NEEDS_READBACK')

    def test_late_submit_completion_triggers_one_readback_without_another_save(self):
        self.flow.state['submission_observation']={'target':'3','started':123,'completion':'pending'}
        self.browser.submission_observation={'target':'3','started':123,'completion':'response_loaded'}
        self.runtime.verify.side_effect=None;self.runtime.verify.return_value={'verified':True}
        self.runtime.reconcile_completed_submission();self.runtime.reconcile_completed_submission()
        self.runtime.verify.assert_called_once()
        self.assertEqual(len(self.flow.state['results']),4)
        self.assertEqual(self.flow.state['phase'],'INTERRUPTED')
        self.browser.execute.assert_not_called()

    def test_exact_interrupted_queue_recovery_never_restores_approval(self):
        path=self.root/'interrupted.json';self.runtime.interrupted_queue_path=path
        self.runtime.queue=[];path.write_text(json.dumps([self.record]))
        self.runtime.recover_queued_message('new')
        self.assertEqual(self.runtime.queue[0]['id'],'new')
        with self.assertRaises(ValueError):self.runtime.recover_queued_message('new')
        self.runtime.queue=[];path.write_text(json.dumps([{**self.record,'text':'OK 123456'}]))
        with self.assertRaises(ValueError):self.runtime.recover_queued_message('new')

    def test_preserved_task_cannot_become_answer_to_another_question(self):
        self.flow.state['phase']='QUESTION'
        self.runtime.queue=[{**self.record,'retry_of':'older-run'}]
        self.runtime.submit=Mock()
        self.runtime.handle_queue()
        self.assertEqual(len(self.runtime.queue),1);self.assertEqual(self.flow.state['phase'],'QUESTION')
        self.runtime.submit.assert_not_called()

    def test_waiting_policy_resume_keeps_completed_results_and_rejects_approval(self):
        self.runtime.queue=[]
        self.flow.state={k:v for k,v in self.flow.state.items() if k not in {'selected','manifest_hash','write_started'}}
        self.flow.state.update(phase='INTERRUPTED',resume_phase='PLANNING',results=[],
                               verified_previous_results=[{'verified':True}],previously_selected_scope=[{'target':'x','summary':'Remaining dates','scope':'requested'}])
        run=self.flow.state['run'];self.runtime.retry_failed_run(run,waiting=True)
        self.assertEqual(self.runtime.queue[0]['verified_previous_results'],[{'verified':True}])
        self.assertNotIn('code',self.runtime.queue[0])
        self.runtime.queue=[];self.flow.state['approval_sent']=True
        with self.assertRaises(ValueError):self.runtime.retry_failed_run(run,waiting=True)

    def test_date_preflight_failure_returns_to_planning_without_saving(self):
        self.runtime.queue=[];self.flow.state.update(phase='APPROVED',results=[])
        self.browser.preflight.side_effect=MoodleDateConflict('Invalid interval; inspect original dates')
        self.runtime.execute('group')
        self.assertEqual(len(self.runtime.queue),1)
        self.assertIn('original date intervals',self.runtime.queue[0]['recovery_feedback'])
        self.browser.execute.assert_not_called()

    def test_not_dispatched_save_replans_without_false_pending_response_notice(self):
        from chrome_moodle import MoodleSaveNotDispatched
        self.runtime.queue=[];self.flow.state.update(phase='APPROVED',results=[])
        self.runtime.poll_safely=Mock();self.browser.unchanged_after_attempt.return_value=True
        self.browser.forms.update({op['target']:{'fields':[]} for op in self.flow.state['selected']})
        def no_click(op,guard):
            guard()
            self.browser.submission_observation={'target':op['target'],'completion':'not_dispatched','post_requests':0}
            raise MoodleSaveNotDispatched('Save did not dispatch')
        self.browser.execute.side_effect=no_click
        self.runtime.execute('group')
        self.assertEqual(self.flow.state['phase'],'INTERRUPTED');self.assertEqual(len(self.runtime.queue),1)
        self.assertEqual(self.flow.state['results'],[])
        self.assertEqual(self.browser.execute.call_count,1)
        self.assertTrue(all('Status: Failed' not in c.args[1] for c in self.runtime.send.call_args_list))

    def test_trial_failure_replans_without_save_intent(self):
        from chrome_moodle import MoodleSaveNotDispatched
        self.runtime.queue=[];self.flow.state.update(phase='APPROVED',results=[])
        self.runtime.poll_safely=Mock();self.browser.execute.side_effect=MoodleSaveNotDispatched('Save button not actionable')
        self.runtime.execute('group')
        self.assertEqual(self.flow.state['phase'],'FAILED');self.assertEqual(len(self.runtime.queue),1)
        self.assertIn('Save was not dispatched',self.runtime.queue[0]['recovery_feedback'])

    def test_visibility_preflight_conflict_replans_before_any_write(self):
        from chrome_structure import MoveVisibilityConflict
        self.runtime.queue=[];self.flow.state.update(phase='APPROVED',results=[])
        self.runtime.poll_safely=Mock();self.browser.preflight.side_effect=MoveVisibilityConflict('Destination became hidden')
        self.runtime.execute('group')
        self.assertEqual(len(self.runtime.queue),1)
        self.assertIn('No move was dispatched',self.runtime.queue[0]['recovery_feedback'])
        self.browser.execute.assert_not_called()

    def test_operator_retirement_preserves_receipts_and_requires_fresh_plan(self):
        self.browser.retire_unobserved_section_submit.return_value={'target':'3','completion':'retired_unobserved_attempt'}
        self.browser.unchanged_after_attempt.return_value=True
        run=self.flow.state['run'];self.runtime.retire_section_submit(run)
        self.assertEqual(self.flow.state['phase'],'INTERRUPTED')
        self.assertEqual(len(self.flow.state['results']),3)
        self.assertEqual(len(self.runtime.queue[-1]['verified_previous_results']),3)
        self.assertNotIn('code',self.runtime.queue[-1])
        self.browser.execute.assert_not_called()
        self.assertTrue((self.root/('retired-submit-source-'+run+'.json')).exists())

    def test_operator_retirement_cannot_override_changed_manifest(self):
        self.flow.state['manifest_hash']='tampered'
        with self.assertRaises(ValueError):self.runtime.retire_section_submit(self.flow.state['run'])
        self.browser.retire_unobserved_section_submit.assert_not_called()

    def test_verified_group_rollback_returns_remaining_work_to_review(self):
        self.runtime.queue=[]
        op={**self.flow.state['selected'][0],'capability':'update_group_membership','relation_snapshot':[{'member':True},{'member':False}]}
        self.flow.state.update(selected=[op],results=[],manifest_hash=digest([op]),write_started=op['id'])
        self.runtime.groups.resolve=Mock(return_value={'relation_snapshot':op['relation_snapshot']})
        self.runtime.reconcile('group')
        self.assertEqual(self.flow.state['phase'],'INTERRUPTED');self.assertEqual(len(self.runtime.queue),1)
        self.browser.execute.assert_not_called()


if __name__=='__main__':unittest.main()
