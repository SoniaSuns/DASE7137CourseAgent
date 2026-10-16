"""Owner policy: one approval for confirmed work; uncertainty ends separately."""
import copy
import json
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import chrome_deferred_items as deferred
import chrome_schedule_alignment as alignment
from chrome_approval import ChromeApproval
from chrome_task_memory import result_summary
from run_shared_chrome_agent import Runtime
from task_metrics import summarize
from task_observability import TaskObserver
import test_schedule_alignment as fixtures


class DeferredCompletionTests(unittest.TestCase):
    def runtime(self, state):
        r = Runtime.__new__(Runtime)
        r.defer_uncertain_items = True
        r.flows = {'chat': SimpleNamespace(state=state, save=Mock())}
        r.queue = []; r.send = Mock(); r.submit = Mock(); r.context = Mock(return_value={})
        r.observer = Mock(); r.library = None; r.record_failure = Mock()
        return r

    def state(self):
        return {'run': 'a'*12, 'task_id': 'T-'+'a'*12, 'task': 'Align the course',
                'phase': 'PLANNING', 'results': [], 'verified_previous_results': [{'verified': True}]*14}

    def test_verified_batch_closes_without_requeue_and_keeps_receipts(self):
        state = self.state()
        state.update(phase='INTERRUPTED', selected=[{'id': 'move'}], write_started='move',
                     results=[{'verified': True, 'change': 'Moved confirmed item'}],
                     plan={'impact_review': {'unresolved_dependencies': [
                         'Workshop Slides: topic missing; provide the source file.',
                         'Survey: intended week unknown; provide the target week.']}})
        r = self.runtime(state)
        self.assertTrue(r.finish_goal_review('chat'))
        self.assertEqual(state['phase'], 'PARTIAL')
        self.assertFalse(state['goal_verified'])
        self.assertEqual(len(state['results']), 1)
        self.assertEqual(len(state['verified_previous_results']), 14)
        self.assertEqual(r.queue, [])
        r.submit.assert_not_called()
        text = r.send.call_args.args[1]
        self.assertIn('\n\n2. Survey:', text)
        self.assertIn('start a new task for each item', text)
        self.assertNotIn('Status: Done', text)
        self.assertEqual(result_summary(state)['phase'], 'PARTIAL')

    def test_partial_never_closes_an_approval_or_uncertain_save(self):
        for change in ({'phase': 'APPROVED'}, {'phase': 'EXECUTING'}, {'phase': 'NEEDS_READBACK'},
                       {'phase': 'APPROVAL'}, {'selected': [{'id': 'one'}]}, {'write_started': 'unknown'},
                       {'write_started': ''}, {'write_started': None},
                       {'pending_delivery': {'text': 'Unconfirmed message'}}):
            with self.subTest(change=change):
                state = {**self.state(), **change}; original = copy.deepcopy(state)
                flow = SimpleNamespace(state=state, save=Mock())
                with self.assertRaises(ValueError): deferred.close(flow, ['Uncertain file'])
                self.assertEqual(state, original)
                flow.save.assert_not_called()

    def test_question_gets_one_chance_to_offer_confirmed_work_then_closes(self):
        state = self.state(); r = self.runtime(state)
        result = {'decision': 'question', 'message': 'Which week for Survey?',
                  'impact_review': {'unresolved_dependencies': ['Survey: provide intended week.']}}
        job = {'rounds': 2, 'repairs': 0}
        r.accept_decision('chat', result, job)
        r.submit.assert_called_once(); r.send.assert_not_called()
        r.accept_decision('chat', result, job)
        self.assertEqual(state['phase'], 'PARTIAL')
        self.assertEqual(r.submit.call_count, 1)

    def test_empty_partition_keeps_every_unresolved_target_without_writes(self):
        f = fixtures.AlignmentTests(); f.setUp()
        review = {'accepted': False, 'items': [{'target': row['target'], 'accepted': False,
                  'reason': 'Source missing'} for row in f.contract['items']]}
        contract, ops, notes = alignment.partition_review(review, f.contract, f.ops, f.browser, allow_empty=True)
        self.assertEqual(ops, [])
        self.assertTrue(all(row['disposition'] == 'unresolved' for row in contract['items']))
        self.assertEqual(len(notes), 3)
        review['items'].pop()
        with self.assertRaises(alignment.AlignmentReviewRequired):
            alignment.partition_review(review, f.contract, f.ops, f.browser, allow_empty=True)

    def test_first_review_immediately_offers_independent_operations(self):
        f = fixtures.AlignmentTests(); f.setUp()
        review = {'accepted': False, 'items': [{'target': row['target'], 'accepted': row['target'] != 'a:m3',
                  'reason': 'Source missing'} for row in f.contract['items']]}
        state = self.state(); r = self.runtime(state); r.browser = f.browser
        future = Future(); future.set_result(review)
        r.jobs = {'chat': {'future': future, 'context': {'review_kind': 'schedule_alignment'},
                          'alignment_candidate': {'operations': f.ops}, 'review_contract': f.contract,
                          'origin_job': {}, 'rounds': 1, 'repairs': 0}}
        r.readable_chats = None; r.accept_decision = Mock()
        r.complete_jobs()
        candidate = r.accept_decision.call_args.args[1]
        self.assertEqual([o['target'] for o in candidate['operations']], ['a:m1', 'a:m2'])
        r.submit.assert_not_called(); r.record_failure.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            flow = ChromeApproval(Path(tmp)/'flow.json', 'chat'); flow.begin('Align files', 'staff', 'request')
            operations = [{'id': o['id'], 'target': o['target'], 'capability': o['capability'],
                           'scope': 'requested', 'summary': 'Move confirmed file', 'depends_on': []}
                          for o in candidate['operations']]
            flow.offer(candidate, operations)
            self.assertEqual(flow.state['phase'], 'APPROVAL')
            with self.assertRaises(ValueError): flow.before_save(operations[0])
            flow.mark_sent(); flow.approve('OK '+flow.state['code'], 'staff', 'new-reply')
            flow.before_save(operations[0])
            self.assertEqual(flow.state['phase'], 'EXECUTING')

    def test_partial_is_terminal_and_not_counted_as_goal_success(self):
        state = self.state(); state.update(phase='PARTIAL', created=1, schedule_required=True,
                                          selected=[{'id': 'one'}], results=[{'verified': True}])
        with tempfile.TemporaryDirectory() as tmp:
            earlier = {**state, 'run': 'b'*12, 'created': 0, 'phase': 'DONE'}
            Path(tmp, 'archived-run-'+'b'*12+'.json').write_text(json.dumps(earlier), encoding='utf-8')
            Path(tmp, 'a'*12+'.json').write_text(json.dumps(state), encoding='utf-8')
            row = summarize(tmp)['rows'][0]
            self.assertFalse(row['done']); self.assertFalse(row['active']); self.assertTrue(row['partial'])
            self.assertFalse(row['first_run_done'])
            observer = TaskObserver(Path(tmp)/'obs'); observer.sync('chat', state)
            self.assertEqual(observer.snapshot()['tasks']['chat']['assessment'], 'closed_with_unresolved_items')

    def test_answer_with_unresolved_dependencies_is_never_done(self):
        state = self.state(); r = self.runtime(state)
        r.accept_decision('chat', {'decision': 'answer', 'message': 'Finished',
                                  'impact_review': {'unresolved_dependencies': ['Poster: provide submission destination.']}},
                          {'repairs': 0})
        self.assertEqual(state['phase'], 'PARTIAL')
        self.assertNotIn('Status: Done', r.send.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
