import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from playwright.sync_api import TimeoutError as BrowserTimeout
from chrome_moodle import MoodleBrowser
from test_shared_chrome_agent import BrowserIntegrationTests


class ReadRecoveryTests(BrowserIntegrationTests):
    def test_read_timeout_reopens_exact_get_then_succeeds(self):
        from chrome_moodle import stable_form
        self.resolved()
        with patch('chrome_moodle.stable_form',side_effect=[BrowserTimeout('private raw data'),stable_form(self.page)]):
            # Build the stable fixture before injecting its first failed read.
            self.browser.inspect('140148:module:7')
        self.assertEqual(self.writes,0)
        self.assertEqual(self.browser.recovery_events[-1]['outcome'],'recovered')

    def test_permanent_read_failure_is_bounded_and_diagnostic(self):
        self.resolved()
        self.browser.recover_read_page=Mock(return_value=self.page)
        with tempfile.TemporaryDirectory() as tmp:
            self.browser.diagnostic_path=Path(tmp)/'reads.json'
            with patch('chrome_moodle.stable_form',side_effect=BrowserTimeout('private raw data')) as read:
                with self.assertRaisesRegex(ValueError,'after 3 read attempts'):
                    self.browser.inspect('140148:module:7')
            self.assertEqual(read.call_count,3)
            evidence=self.browser.diagnostic_path.read_text()
            self.assertNotIn('private raw data',evidence)
            self.assertIn('wait_form_ready',evidence)
        self.browser.recover_read_page.assert_called_once()
        self.assertEqual(self.writes,0)

    def test_identity_failure_is_not_retried(self):
        self.resolved();self.wrong_course=True
        self.browser.recover_read_page=Mock()
        with self.assertRaisesRegex(ValueError,'exact selected course'):
            self.browser.inspect('140148:module:7')
        self.browser.recover_read_page.assert_not_called()
        self.assertEqual(self.writes,0)

    def test_prepare_retries_before_guard_but_save_click_never_replays(self):
        op=self.resolved();guard=Mock()
        original=self.browser._prepare_once
        calls=[0]
        def transient(operation):
            calls[0]+=1
            if calls[0]==1:raise BrowserTimeout('fill timeout')
            return original(operation)
        self.browser._prepare_once=Mock(side_effect=transient)
        result=self.browser.execute(op,guard)
        self.assertTrue(result['verified']);guard.assert_called_once();self.assertEqual(self.writes,1)

    def test_recovery_still_checks_approved_fingerprint(self):
        op=self.resolved();guard=Mock()
        def timeout_then_drift(*args):
            self.value='20'
            raise BrowserTimeout('initialization timeout')
        from chrome_moodle import stable_form
        calls=[0]
        def transient(page):
            calls[0]+=1
            if calls[0]==1:return timeout_then_drift()
            return stable_form(page)
        with patch('chrome_moodle.stable_form',side_effect=transient):
            with self.assertRaisesRegex(ValueError,'approved form changed'):
                self.browser.execute(op,guard)
        guard.assert_not_called();self.assertEqual(self.writes,0)


if __name__=='__main__':unittest.main()
