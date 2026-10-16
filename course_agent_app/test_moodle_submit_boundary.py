"""Save completion must precede GET readback; all URLs are local fixtures."""
from unittest.mock import Mock
import unittest
from test_shared_chrome_agent import BrowserIntegrationTests
from chrome_moodle import MoodleFormRejected, MoodleSubmissionUnconfirmed, MoodleSaveNotDispatched


class SubmitBoundaryTests(BrowserIntegrationTests):
    def test_rejected_guard_does_not_leave_a_pending_submission_lock(self):
        op=self.resolved()
        with self.assertRaises(ValueError):
            self.browser.execute(op,Mock(side_effect=ValueError('No approval')))
        self.assertEqual(self.browser.submission_observation['completion'],'not_submitted')
        self.browser.inspect(op['target']);self.assertEqual(self.writes,0)

    def install_delayed_form(self,reject=False):
        original=self.route
        def route(request):
            if request.request.method=='POST':return original(request)
            action=('document.getElementById("id_error_due").textContent="Submission was rejected by the date validator"'
                    if reject else 'form.submit()')
            request.fulfill(content_type='text/html',body=f'''<body class="course-140148">
              <form class="mform" method="post" onsubmit='event.preventDefault();const form=this;setTimeout(()=>{{{action}}},600)'>
              <input type="hidden" name="course" value="140148"><input type="hidden" name="update" value="7">
              <select id="id_due_day" name="duedate[day]"><option value="10" {'selected' if self.value=='10' else ''}>10</option><option value="20" {'selected' if self.value=='20' else ''}>20</option></select>
              <div id="id_error_due" class="invalid-feedback" style="display:none"></div>
              <input type="submit" id="id_submitbutton" name="submitbutton" value="Save"></form>''')
        self.page.unroute('**/*');self.page.route('**/*',route)

    def test_async_submit_finishes_before_readback_navigation(self):
        self.install_delayed_form();op=self.resolved();guard=Mock()
        result=self.browser.execute(op,guard)
        self.assertTrue(result['verified']);self.assertEqual(self.writes,1);guard.assert_called_once()

    def test_async_client_rejection_is_observed_before_readback(self):
        self.install_delayed_form(reject=True);op=self.resolved()
        with self.assertRaisesRegex(MoodleFormRejected,'date validator'):
            self.browser.execute(op,Mock())
        self.assertEqual(self.writes,0)

    def test_server_error_status_is_preserved(self):
        original=self.route
        def route(request):
            if request.request.method=='POST':
                self.writes+=1
                request.fulfill(status=503,body='<body>Service temporarily unavailable</body>')
            else:original(request)
        self.page.unroute('**/*');self.page.route('**/*',route);op=self.resolved()
        with self.assertRaisesRegex(RuntimeError,'HTTP 503'):self.browser.execute(op,Mock())
        self.assertEqual(self.writes,1)

    def test_pending_submit_is_not_aborted_by_readback_and_late_result_is_readable(self):
        self.install_delayed_form();op=self.resolved();self.browser.submit_timeout_seconds=0.1
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.execute(op,Mock())
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.verify(op)
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.unchanged_after_attempt(op)
        self.page.wait_for_timeout(900)
        self.assertEqual(self.browser.submission_observation['completion'],'response_loaded')
        self.assertTrue(self.browser.verify(op)['verified']);self.assertEqual(self.writes,1)

    def test_overlay_after_trial_proves_save_input_never_dispatched(self):
        op=self.resolved();self.browser.save_click_timeout_ms=250
        def cover():
            self.page.evaluate('''()=>{const d=document.createElement('div');d.style='position:fixed;inset:0;z-index:999999;background:white';document.body.append(d)}''')
        with self.assertRaisesRegex(MoodleSaveNotDispatched,'before any'):
            self.browser.execute(op,cover)
        obs=self.browser.submission_observation
        self.assertEqual(obs['completion'],'not_dispatched');self.assertEqual(self.writes,0)
        self.assertFalse(any(obs['dispatch_probe']['events'].values()))
        self.assertIn('click_error',obs);self.assertEqual(obs['write_requests'],0)
        # Readback is allowed because dispatch was disproven, not merely absent POSTs.
        self.assertTrue(self.browser.unchanged_after_attempt(op));self.assertEqual(self.writes,0)

    def test_failed_trial_never_calls_approval_boundary(self):
        op=self.resolved();original=self.browser.prepare;self.browser.save_click_timeout_ms=200
        def prepare(operation):
            button=original(operation)
            self.page.evaluate('''()=>{const d=document.createElement('div');d.style='position:fixed;inset:0;z-index:999999';document.body.append(d)}''')
            return button
        self.browser.prepare=prepare;guard=Mock()
        with self.assertRaisesRegex(MoodleSaveNotDispatched,'not actionable'):self.browser.execute(op,guard)
        guard.assert_not_called();self.assertEqual(self.writes,0)

    def test_background_rpc_does_not_turn_zero_click_into_pending_save(self):
        self.background_timeout('core_courseformat_get_state',MoodleSaveNotDispatched)
        obs=self.browser.submission_observation
        self.assertEqual(obs['background_requests'],1);self.assertEqual(obs['write_requests'],0)
        self.assertEqual(obs['completion'],'not_dispatched')
        self.assertTrue(self.browser.unchanged_after_attempt(self.pending_op))

    def test_unknown_ajax_with_zero_click_remains_protected(self):
        self.background_timeout('core_course_update_courses',MoodleSubmissionUnconfirmed)
        obs=self.browser.submission_observation
        self.assertEqual(obs['background_requests'],0);self.assertEqual(obs['write_requests'],1)
        self.assertEqual(obs['completion'],'pending')
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.verify(self.pending_op)

    def background_timeout(self,method,error):
        self.page.route('**/lib/ajax/service.php*',lambda r:r.fulfill(content_type='application/json',body='[]'))
        self.pending_op=self.resolved();self.browser.save_click_timeout_ms=200;self.browser.submit_timeout_seconds=.02
        def cover():
            self.page.evaluate('''async method=>{
              await fetch('/lib/ajax/service.php',{method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify([{methodname:method,args:{private:'never log this'}}])});
              const d=document.createElement('div');d.style='position:fixed;inset:0;z-index:999999';document.body.append(d);
            }''',method)
        with self.assertRaises(error):self.browser.execute(self.pending_op,cover)
        self.assertEqual(self.writes,0)
        self.assertNotIn('never log this',str(self.browser.submission_observation))

    def test_background_form_renderer_remains_active_and_saves_once(self):
        op=self.resolved();other=self.page.context.new_page()
        try:
            def move_focus():other.bring_to_front()
            result=self.browser.execute(op,move_focus)
            self.assertTrue(result['verified']);self.assertEqual(self.writes,1)
            self.assertFalse(self.page.evaluate('()=>document.hidden'))
            self.assertTrue(self.page.evaluate('()=>document.hasFocus()'))
        finally:other.close()

    def test_real_click_without_immediate_post_remains_pending(self):
        self.install_delayed_form();op=self.resolved();self.browser.submit_timeout_seconds=.05
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.execute(op,Mock())
        self.assertEqual(self.browser.submission_observation['completion'],'pending')
        self.assertEqual(self.writes,0)
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.unchanged_after_attempt(op)
        self.page.wait_for_timeout(900)
        self.assertEqual(self.writes,1);self.assertTrue(self.browser.verify(op)['verified'])

    def test_click_timeout_after_dispatch_does_not_authorize_repeat(self):
        from playwright.sync_api import TimeoutError as ClickTimeout
        self.install_delayed_form();op=self.resolved();original=self.browser.prepare
        self.browser.submit_timeout_seconds=.05
        def prepare(operation):
            button=original(operation);click=button.click
            def fail_after_event(*args,**kwargs):
                click(*args,**kwargs)
                if not kwargs.get('trial'):raise ClickTimeout('fixture click returned after event; sesskey=private')
            button.click=fail_after_event
            return button
        self.browser.prepare=prepare
        with self.assertRaises(MoodleSubmissionUnconfirmed):self.browser.execute(op,Mock())
        obs=self.browser.submission_observation
        self.assertGreater(obs['dispatch_probe']['events']['click'],0)
        self.assertNotIn('sesskey=private',obs['click_error']);self.assertEqual(obs['completion'],'pending')
        self.page.wait_for_timeout(900)
        self.assertTrue(self.browser.verify(op)['verified']);self.assertEqual(self.writes,1)


if __name__=='__main__':unittest.main()
