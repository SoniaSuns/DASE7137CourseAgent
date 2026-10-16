import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import TimeoutError as BrowserTimeout
from chrome_moodle import MoodleBrowser
from chrome_course_scope import course, course_identity
from run_shared_chrome_agent import Runtime


class RecoveryTests(unittest.TestCase):
    def test_startup_chat_read_recovers_before_taking_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Mock(),Mock(),['group'],[],Mock(),Path(temp),Mock())
            runtime.open=Mock(side_effect=[BrowserTimeout('private failure'),None])
            runtime.recover_transport=Mock()
            with patch('run_shared_chrome_agent.observed_chat_type',return_value='group'),patch('run_shared_chrome_agent.intake_records',return_value=[]):
                runtime.baseline()
            runtime.recover_transport.assert_called_once_with('reload')
            self.assertEqual(runtime.open.call_count,2)
            self.assertEqual(runtime.seen['group'],set())
            self.assertNotIn('private failure',next(Path(temp).glob('page-failures-*.json')).read_text())

    def test_moodle_recovery_replaces_only_owned_tab_without_reloading(self):
        from shared_chrome import SharedChrome
        session=SharedChrome.__new__(SharedChrome)
        session.browser=Mock();session.browser.is_connected.return_value=True
        old=Mock();old.is_closed.return_value=False
        replacement=Mock();wa=Mock()
        session.owned_pages=[old];session.whatsapp_page=Mock(return_value=wa)
        session.moodle_page=Mock(return_value=replacement)
        page,observed,reconnected=session.recover_moodle_page(old)
        self.assertIs(page,replacement);self.assertIs(observed,wa);self.assertFalse(reconnected)
        old.close.assert_called_once_with(run_before_unload=False);old.reload.assert_not_called()
        external=Mock()
        session.recover_moodle_page(external)
        external.close.assert_not_called();external.reload.assert_not_called()

    def test_moodle_recovery_reconnects_local_browser_before_replacing_tab(self):
        from shared_chrome import SharedChrome
        session=SharedChrome.__new__(SharedChrome)
        session.browser=Mock();session.browser.is_connected.return_value=False
        session.playwright=Mock();session.env={'COURSE_AGENT_CDP_URL':'http://127.0.0.1:9222'}
        session.owned_pages=[];session.whatsapp_page=Mock();session.moodle_page=Mock()
        _,_,reconnected=session.recover_moodle_page(Mock())
        self.assertTrue(reconnected)
        session.playwright.chromium.connect_over_cdp.assert_called_once_with('http://127.0.0.1:9222',timeout=15000,no_defaults=True)

    def test_whatsapp_recovery_holds_focus_session_without_navigation(self):
        from shared_chrome import activate_whatsapp_page
        page=Mock(url='https://web.whatsapp.com/')
        session=activate_whatsapp_page(page)
        session.send.assert_called_once_with('Emulation.setFocusEmulationEnabled',{'enabled':True})
        session.detach.assert_not_called()
        page.goto.assert_not_called();page.bring_to_front.assert_not_called()

    def test_focus_recovery_rejects_other_sites_and_detaches_failed_session(self):
        from shared_chrome import activate_whatsapp_page
        page=Mock(url='https://moodle.hku.hk/')
        with self.assertRaises(ValueError):activate_whatsapp_page(page)
        page.context.new_cdp_session.assert_not_called()
        page.url='https://web.whatsapp.com/'
        session=page.context.new_cdp_session.return_value
        session.send.side_effect=RuntimeError('Unavailable')
        with self.assertRaises(RuntimeError):activate_whatsapp_page(page)
        session.detach.assert_called_once()

    def test_login_is_diagnosed_before_course_identity(self):
        page=Mock(url='https://moodle.hku.hk/login/index.php')
        page.evaluate.return_value=['1']
        with self.assertRaisesRegex(ValueError,'requires login'):
            course_identity(page,course('140147'))

    def test_poll_timeout_does_not_exit_or_consume_pending_request(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Mock(),Mock(),['test approval'],[],Mock(),Path(temp),Mock())
            runtime.queue=[{'chat':'test approval','id':'fresh','sender':'staff','text':'Request'}]
            runtime.poll=Mock(side_effect=BrowserTimeout('private browser diagnostics'))
            self.assertFalse(runtime.poll_safely())
            self.assertEqual(len(runtime.queue),1)
            self.assertNotIn('private',runtime.transport_error)
            runtime.poll=Mock()
            self.assertTrue(runtime.poll_safely())
            self.assertEqual(runtime.transport_error,'')


class LoginRecoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.chrome=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.chrome.close();cls.p.stop()

    def setUp(self):
        self.ctx=self.chrome.new_context();self.page=self.ctx.new_page()
        self.posts=0;self.logged_in=False;self.accept=True;self.foreign=False;self.portal=False;self.portal_visits=0
        self.page.route('**/*',self.route)
        self.browser=MoodleBrowser(self.page,{'MOODLE_LOGIN_USERNAME':'fixture','MOODLE_LOGIN_PASSWORD':'fixture-only'})

    def tearDown(self):self.ctx.close()

    def route(self,route):
        if 'authCAS=CAS' in route.request.url:
            self.portal_visits+=1;self.logged_in=True
        if route.request.method=='POST':
            self.posts+=1;self.logged_in=self.accept
        if self.logged_in:
            route.fulfill(body='<body class="course-140147">Course fixture</body>');return
        # Serve the login form directly: Playwright may follow HTTP redirects beyond
        # the original route handler. Fixtures must never escape to the real host.
        action='https://other.invalid/login' if self.foreign else '/login/index.php'
        portal='<a href="https://moodle.hku.hk/login/index.php?authCAS=CAS">HKU Portal user login</a>' if self.portal else ''
        route.fulfill(body=f'<body class="course-1">{portal}<form method="post" action="{action}"><input id="username" name="username"><input id="password" name="password" type="password"><button id="loginbtn">Log in</button></form></body>')

    def test_login_once_returns_to_exact_interrupted_course(self):
        self.browser.goto(course('140147'),course('140147').url)
        self.assertEqual(self.posts,1)
        self.assertEqual(self.page.url,course('140147').url)

    def test_failed_credentials_are_not_repeated(self):
        self.accept=False
        for _ in range(2):
            with self.assertRaises(ValueError):self.browser.goto(course('140147'),course('140147').url)
        self.assertEqual(self.posts,1)

    def test_foreign_login_action_never_receives_credentials(self):
        self.foreign=True
        with self.assertRaises(ValueError):self.browser.goto(course('140147'),course('140147').url)
        self.assertEqual(self.posts,0)

    def test_existing_portal_session_recovers_without_password_submission(self):
        self.portal=True
        self.browser.goto(course('140147'),course('140147').url)
        self.assertEqual(self.portal_visits,1)
        self.assertEqual(self.posts,0)
        self.assertEqual(self.page.url,course('140147').url)


if __name__=='__main__':unittest.main()
