"""Transport failures must not expose authenticated request headers."""
import unittest
from playwright.sync_api import Error
from test_autonomous_exploration import browser_fixture, CID, TARGET
from chrome_readonly_moodle import ReadOnlyMoodle


class ReadOnlyTransportPrivacyTests(unittest.TestCase):
    def test_request_failure_redacts_cookie_bearing_playwright_message(self):
        browser, _ = browser_fixture()
        browser.page.context.request.get.side_effect = Error('cookie: MoodleSession=SYNTHETIC_SECRET')
        broker = ReadOnlyMoodle(browser, [CID])
        with self.assertRaises(RuntimeError) as raised:
            broker.read(TARGET)
        self.assertNotIn('SYNTHETIC_SECRET', str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertEqual(broker.receipts, {})

    def test_body_failure_redacts_headers_and_disposes_response(self):
        browser, response = browser_fixture()
        response.body.side_effect = Error('authorization: SYNTHETIC_SECRET')
        broker = ReadOnlyMoodle(browser, [CID])
        with self.assertRaises(RuntimeError) as raised:
            broker.read(TARGET)
        self.assertNotIn('SYNTHETIC_SECRET', str(raised.exception))
        response.dispose.assert_called_once()
        self.assertEqual(broker.receipts, {})
