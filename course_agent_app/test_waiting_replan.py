import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import question_recovery as recovery
from run_shared_chrome_agent import Runtime


class WaitingReplanTests(unittest.TestCase):
    def setUp(self):
        self.run = 'abcdef123456'
        self.state = {'run': self.run, 'task_id': 'T-'+self.run, 'chat': 'chat', 'task': 'Align files',
            'sender': 'staff', 'message_ids': ['request'], 'phase': 'QUESTION', 'resume_phase': 'PLANNING',
            'mention_authorized': True, 'pending_question': 'Old incorrect question', 'results': [],
            'reply_boundary': 'a'*24, 'delivery_verified_ids': ['delivered-question'], 'delivery_parts_count': 1,
            'conversation': ['Keep these three items in their original location.'],
            'verified_previous_results': [{'verified': True}]*9, 'completed_operation_memory': [{'receipt': 'done'}]}
        self.status = {'queued': 0, 'pending_delivery': [], 'phases': {'chat': 'QUESTION'}}

    def check(self, state=None, queue=None, status=None, others=()):
        self.assertTrue(callable(getattr(recovery, 'validate_waiting_replan', None)),
                        'Delivered-question replan needs a guarded preflight.')
        return recovery.validate_waiting_replan([state or self.state, *others], queue or [], self.run,
                                               self.status if status is None else status)

    def test_delivered_question_replan_preserves_previous_receipts(self):
        original = copy.deepcopy(self.state)
        self.assertIs(self.check(), self.state)
        self.assertEqual(self.state, original)

    def test_approval_possible_write_or_unconfirmed_delivery_blocks_restart(self):
        for change in ({'phase': 'APPROVAL'}, {'resume_phase': 'EXECUTING'}, {'selected': []},
                       {'manifest_hash': ''}, {'write_started': None}, {'approval_sent': True},
                       {'results': [{}]}, {'pending_delivery': {'text': 'unsent'}},
                       {'delivery_verified_ids': []}, {'delivery_parts_count': 2},
                       {'reply_boundary': ''}, {'mention_authorized': False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.check(state={**self.state, **change})

    def test_other_active_chat_queued_message_or_inconsistent_status_blocks_restart(self):
        for kwargs in ({'queue': [{'chat': 'chat'}]}, {'others': [{'phase': 'PLANNING'}]},
                       {'others': [{'phase': 'FAILED', 'pending_delivery': {'text': 'pending'}}]},
                       {'status': {}}, {'status': {**self.status, 'queued': 1}},
                       {'status': {**self.status, 'phases': {'chat': 'PLANNING'}}},
                       {'status': {**self.status, 'phases': {'chat': 'QUESTION', 'other': 'APPROVAL'}}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.check(**kwargs)

    def test_existing_post_restart_retry_queues_only_fresh_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Runtime(Mock(), Mock(), ['chat'], [], Mock(), Path(tmp), Mock())
            flow = runtime.flows['chat']; flow.state = copy.deepcopy(self.state)
            flow.state['phase'] = 'INTERRUPTED'
            runtime.retry_failed_run(self.run, waiting=True)
            self.assertEqual(len(runtime.queue), 1)
            queued = runtime.queue[0]
            for key in ('task_id', 'conversation', 'verified_previous_results', 'completed_operation_memory'):
                self.assertEqual(queued[key], self.state[key])
            self.assertNotIn('selected', queued)
            self.assertNotIn('approval_sent', queued)
            runtime.browser.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
