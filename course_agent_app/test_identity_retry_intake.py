"""Transient own-profile failures cannot silently consume fresh group tasks."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from chrome_moodle import digest
from run_shared_chrome_agent import Runtime, request_record, WhatsAppIntakeUnavailable


class IdentityRetryIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Runtime(Mock(), Mock(), ['group'], [], Mock(), Path(self.tmp.name), Mock(), mention_only_chats=['group'])
        self.r.open = Mock()

    def raw(self, mid, *, mentioned=False, at=None, text='Check both courses; leave uncertain files unchanged.'):
        return {'message_id': mid, 'direction': 'incoming', 'sender_id': 'staff', 'sender_display_name': 'Staff',
                'message_text': '@Observed Agent '+text, 'addressed_text': text, 'self_mentioned': mentioned,
                'meta': (at or self.r.started).strftime('[%H:%M, %Y/%m/%d] Staff:')}

    def test_missing_identity_preserves_new_message_and_retries_once_after_cooldown(self):
        raw = self.raw('new')
        with patch('run_shared_chrome_agent.discover_account_name', side_effect=['', 'Observed Agent']) as discover:
            with patch('run_shared_chrome_agent.time.monotonic', return_value=100), patch('run_shared_chrome_agent.intake_records', return_value=[raw]):
                self.r.poll(); self.r.poll()
            self.assertEqual(discover.call_count, 1)
            self.assertNotIn('new', self.r.seen['group'])
            self.assertEqual(self.r.queue, [])
            with patch('run_shared_chrome_agent.time.monotonic', return_value=131), patch('run_shared_chrome_agent.intake_records', return_value=[self.raw('new', mentioned=True)]):
                self.r.poll(); self.r.poll()
            self.assertEqual(discover.call_count, 2)
        self.assertEqual([r['id'] for r in self.r.queue], ['new'])
        self.assertNotIn('edited_message_id', self.r.queue[0])

    def test_identity_learning_does_not_replay_baseline_or_old_unseen_messages(self):
        old = self.raw('baseline', at=self.r.started-timedelta(days=1))
        self.r.seen['group'].add('baseline')
        self.r.message_revisions['group']['baseline'] = self.r.message_revision(old)
        rows = [self.raw('baseline', mentioned=True, at=self.r.started-timedelta(days=1)),
                self.raw('old-unseen', mentioned=True, at=self.r.started-timedelta(days=1))]
        with patch('run_shared_chrome_agent.discover_account_name', return_value='Observed Agent'), patch('run_shared_chrome_agent.intake_records', return_value=rows):
            self.r.poll(); self.r.poll()
        self.assertEqual(self.r.queue, [])

    def test_failed_chat_keeps_identity_rebaseline_until_successful_read(self):
        old = self.raw('baseline', at=self.r.started-timedelta(days=1))
        self.r.seen['group'].add('baseline')
        self.r.message_revisions['group']['baseline'] = self.r.message_revision(old)
        self.r.open.side_effect = [WhatsAppIntakeUnavailable('temporarily unavailable'), None]
        with patch('run_shared_chrome_agent.discover_account_name', return_value='Observed Agent'):
            with self.assertRaises(WhatsAppIntakeUnavailable): self.r.poll()
            with patch('run_shared_chrome_agent.intake_records', return_value=[self.raw('baseline', mentioned=True)]):
                self.r.poll()
        self.assertEqual(self.r.queue, [])
        self.assertEqual(self.r.identity_rebaseline['group'], set())

    def test_name_is_not_inferred_from_message_and_numeric_mentions_still_work(self):
        with patch('run_shared_chrome_agent.discover_account_name', return_value=''), patch('run_shared_chrome_agent.intake_records', return_value=[self.raw('plain-name'), self.raw('numeric-own', mentioned=True)]):
            self.r.poll()
        self.assertEqual(self.r.account_name, '')
        self.assertEqual([r['id'] for r in self.r.queue], ['numeric-own'])
        with patch('run_shared_chrome_agent.discover_account_name', return_value='Observed Agent'), patch('run_shared_chrome_agent.time.monotonic', return_value=self.r.identity_retry_at+1), patch('run_shared_chrome_agent.intake_records', return_value=[self.raw('plain-name')]):
            self.r.poll()
        self.assertEqual([r['id'] for r in self.r.queue], ['numeric-own'])

    def test_bound_approval_reply_is_not_lost_while_profile_is_missing(self):
        flow = self.r.flows['group']
        flow.begin('Change file sequence', 'staff', 'task')
        flow.state.update(phase='APPROVAL', mention_authorized=True, reply_boundary='boundary', approval_sent=True)
        raw = self.raw('approval', text='OK '+flow.state['code'])
        raw['message_text'] = raw['addressed_text']
        with patch('run_shared_chrome_agent.discover_account_name', return_value=''), patch('run_shared_chrome_agent.intake_records', return_value=[raw]):
            self.r.poll()
        self.assertEqual([r['id'] for r in self.r.queue], ['approval'])
        self.assertEqual(self.r.queue[0]['reply_to_run'], flow.state['run'])
        self.assertEqual(flow.state['phase'], 'APPROVAL')

    def test_explicit_missed_recovery_refreshes_identity_and_revalidates_exact_body(self):
        raw = self.raw('missed', mentioned=True, at=self.r.started-timedelta(hours=1))
        boundary = self.r.started-timedelta(hours=2)
        record = request_record(raw, boundary)
        record.update(text=raw['addressed_text'], self_mentioned=True, requester_name='Staff')
        path = Path(self.tmp.name)/'candidate.json'
        candidate = {'chat': 'group', 'record': record, 'record_hash': digest(record), 'boundary': boundary.isoformat()}
        path.write_text(json.dumps(candidate), encoding='utf-8')
        with patch('run_shared_chrome_agent.discover_account_name', return_value='Observed Agent'), patch('run_shared_chrome_agent.intake_records', return_value=[raw]):
            self.r.recover_mention(path)
            self.r.poll()
        self.assertEqual(self.r.account_name, 'Observed Agent')
        self.assertEqual(self.r.queue, [{'chat': 'group', **record}])
        self.assertFalse(path.exists())
        path.write_text(json.dumps(candidate), encoding='utf-8')
        changed = {**raw, 'addressed_text': 'Different request'}
        self.r.queue.clear()
        with patch('run_shared_chrome_agent.intake_records', return_value=[changed]):
            with self.assertRaisesRegex(ValueError, 'exact missed incoming'): self.r.recover_mention(path)
        self.assertEqual(self.r.queue, [])

    def test_delayed_remount_and_incomplete_history_do_not_become_edits(self):
        old = self.raw('late', at=self.r.started-timedelta(days=1))
        self.r.seen['group'].add('late')
        self.r.message_revisions['group']['late'] = self.r.message_revision(old)
        self.r.message_bodies['group']['late'] = self.r.message_body_revision(old)
        with patch('run_shared_chrome_agent.discover_account_name', return_value='Observed Agent'), patch('run_shared_chrome_agent.intake_records', return_value=[]):
            self.r.poll()
        incomplete = {**old, 'meta': '', 'message_text': ''}
        with patch('run_shared_chrome_agent.intake_records', return_value=[incomplete]): self.r.poll()
        self.assertIn('late', self.r.identity_rebaseline['group'])
        with patch('run_shared_chrome_agent.intake_records', return_value=[{**old, 'self_mentioned': True}]):
            self.r.poll(); self.r.poll()
        self.assertEqual(self.r.queue, [])

    def test_learning_identity_keeps_bound_reply_but_real_edit_retires_old_body(self):
        flow = self.r.flows['group']; flow.begin('Review files', 'staff', 'task')
        flow.state.update(phase='QUESTION', mention_authorized=True, reply_boundary='boundary')
        raw = self.raw('reply')
        with patch('run_shared_chrome_agent.discover_account_name', return_value=''), patch('run_shared_chrome_agent.intake_records', return_value=[raw]): self.r.poll()
        before = list(self.r.queue)
        with patch('run_shared_chrome_agent.discover_account_name', return_value='Observed Agent'), patch('run_shared_chrome_agent.time.monotonic', return_value=self.r.identity_retry_at+1), patch('run_shared_chrome_agent.intake_records', return_value=[{**raw, 'self_mentioned': True}]): self.r.poll()
        self.assertEqual(self.r.queue, before)
        changed = self.raw('reply', mentioned=True, text='Only check course A.')
        with patch('run_shared_chrome_agent.intake_records', return_value=[changed]): self.r.poll()
        self.assertEqual(len(self.r.queue), 1)
        self.assertEqual(self.r.queue[0]['text'], 'Only check course A.')


if __name__ == '__main__':
    unittest.main()
