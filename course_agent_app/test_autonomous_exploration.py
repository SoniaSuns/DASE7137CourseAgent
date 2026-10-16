"""Synthetic tests for autonomous analysis, its broker and runtime fallback."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from chrome_exploration import CodexExplorer, begin, finish, clean_environment
from chrome_readonly_moodle import ReadOnlyMoodle
from chrome_file_library import FileLibrary, FileLibraryChanged
from chrome_course_scope import MoodleLoginRequired
from run_shared_chrome_agent import Runtime


CID = '140147'
TARGET = CID + ':module:7'
PAGE = b'''<html><body class="course-140147 context-700"><main id="region-main">
<h2>Workshop groups</h2><table><tr><td>Student One</td><td>Group 1</td></tr></table>
<script>window.secret='secret-script';fetch('/delete.php')</script>
<form action="/delete.php"><input type="hidden" name="sesskey" value="secret-session">
<input type="password" value="secret-password"></form>
<a href="/pluginfile.php/700/mod_folder/content/0/participants.html?forcedownload=1">Roster</a>
<a href="/course/delete.php?id=140147&amp;sesskey=secret-session">Delete</a>
<a href="/mod/resource/view.php?id=88">Other course</a>
<a href="https://evil.example.test/data">External</a></main></body></html>'''


def browser_fixture():
    browser = Mock()
    browser.targets = {TARGET: {'key': TARGET, 'id': '7', 'course_id': CID,
        'kind': 'module', 'module_type': 'folder', 'title': 'Overview'}}
    browser.forms = {}
    browser.overviews = {}
    response = Mock(status=200, headers={})
    response.body.return_value = PAGE
    browser.page.context.request.get.return_value = response
    return browser, response


class ReadOnlyExplorationTests(unittest.TestCase):
    def setUp(self):
        self.browser, self.response = browser_fixture()
        self.broker = ReadOnlyMoodle(self.browser, [CID])

    def test_page_download_discovery_strips_credentials_and_never_clicks(self):
        result = self.broker.read(TARGET)
        self.assertIn(b'Student One', result['data'])
        for secret in (b'secret-session', b'secret-password', b'secret-script', b'evil.example'):
            self.assertNotIn(secret, result['data'])
        self.assertEqual(len(result['links']), 1)
        self.assertEqual(result['links'][0]['kind'], 'moodle_file')
        self.browser.page.context.request.get.assert_called_once_with(
            'https://moodle.hku.hk/mod/folder/view.php?id=7', max_redirects=0, timeout=45000)
        self.browser.page.context.request.post.assert_not_called()
        self.browser.page.evaluate.assert_not_called()
        self.browser.page.goto.assert_not_called()
        self.response.body.return_value = b'<table><tr><td>Student One</td><td>Group 1</td></tr></table>'
        downloaded = self.broker.read(result['links'][0]['id'])
        self.assertEqual(downloaded['name'], 'participants.html')
        self.assertIn(b'Group 1', downloaded['data'])

    def test_unobserved_and_cross_course_targets_are_rejected_without_network(self):
        for key in ('https://moodle.hku.hk/course/delete.php?id=140147', '140148:module:7', 'moodle-read:invented'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.broker.read(key)
        self.browser.page.context.request.get.assert_not_called()

    def test_mutating_query_encoded_traversal_foreign_context_and_external_urls_rejected(self):
        urls = ['/mod/folder/view.php?id=7&delete=1', '/user/index.php?id=140147&sesskey=x',
                '/grade/report/grader/index.php?id=140147&edit=1',
                '/pluginfile.php/700/mod_folder/content/0/%2e%2e/secret',
                '/pluginfile.php/800/mod_folder/content/0/private.html',
                '/pluginfile.php/700/user/private/0/private.html',
                '/mod/folder/view.php?id=7&id=88', '/user/index.php?id=140148',
                'https://evil.example.test/pluginfile.php/700/mod_folder/content/0/a']
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(self.broker.allowed('https://moodle.hku.hk'+url if url.startswith('/') else url, CID, {'700'}))

    def test_redirect_and_wrong_course_html_do_not_become_evidence(self):
        self.response.status = 302
        with self.assertRaises(MoodleLoginRequired):
            self.broker.read(TARGET)
        self.response.status = 200
        self.response.body.return_value = PAGE.replace(b'course-140147', b'course-140148')
        with self.assertRaisesRegex(ValueError, 'exact selected course'):
            self.broker.read(TARGET)
        self.assertEqual(self.broker.receipts, {})

    def test_source_change_prevents_accepting_a_stale_plan(self):
        self.broker.read(TARGET)
        self.broker.validate()
        self.response.body.return_value = PAGE.replace(b'Group 1', b'Group 2')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            self.broker.validate()
        self.browser.page.context.request.post.assert_not_called()

    def test_observed_pagination_is_available_but_arbitrary_filters_are_not(self):
        self.response.body.return_value = PAGE.replace(b'</main>',
            b'<a href="/user/index.php?id=140147&amp;page=1&amp;perpage=200">Next</a></main>')
        result = self.broker.read(TARGET)
        self.assertEqual(len(result['links']), 2)
        self.assertFalse(self.broker.allowed('https://moodle.hku.hk/user/index.php?id=140147&roleid=5', CID))

    def test_changed_second_read_does_not_replace_the_original_receipt(self):
        self.broker.read(TARGET)
        old=copy.deepcopy(self.broker.receipts)
        self.response.body.return_value=PAGE.replace(b'Group 1',b'Group 2')
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            self.broker.read(TARGET)
        self.assertEqual(self.broker.receipts,old)
        with self.assertRaisesRegex(ValueError,'evidence changed'):
            self.broker.validate()

    def test_case_insensitive_hidden_password_and_secret_named_controls_are_stripped(self):
        self.response.body.return_value=PAGE.replace(b'</main>',b'<input TYPE="HIDDEN" value="UPPER_SECRET"><input TYPE="PASSWORD" value="PASSWORD_SECRET"><textarea name="authToken">TOKEN_SECRET</textarea></main>')
        result=self.broker.read(TARGET)
        for value in [b'UPPER_SECRET',b'PASSWORD_SECRET',b'TOKEN_SECRET']:
            self.assertNotIn(value,result['data'])


class AnalysisWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = CodexExplorer({'CODEX_BIN': sys.executable, 'CODEX_PLANNER_MODEL': 'gpt-6-astra'}, self.root)
        self.packet = {'run_id': 'run1', 'task': 'Check group scores', 'question': 'Read data',
            'sources': [{'id': 'file:one', 'name': '../../odd.dat', 'kind': 'library', 'data': b'group,score\n1,70\n1,90\n'}],
            'available_sources': [{'id': 'file:two', 'name': 'more.csv'}]}

    def fake_run(self, command, **kwargs):
        folder = Path(kwargs['cwd'])
        payload = json.loads((folder/'input.json').read_text())
        self.assertEqual(payload['sources'][0]['local_path'], 'source_1.dat')
        self.assertEqual((folder/'source_1.dat').read_bytes(), self.packet['sources'][0]['data'])
        result = {'findings': 'Group 1 mean is 80; CSV rows 2 and 3.', 'source_ids': ['file:one'],
                  'unresolved': [], 'request_ids': ['file:two']}
        (folder/'result.json').write_text(json.dumps(result))
        return subprocess.CompletedProcess(command, 0)

    def test_worker_uses_selected_raw_bytes_and_structured_evidence_without_actions(self):
        with patch('chrome_exploration.subprocess.run', side_effect=self.fake_run):
            result = self.worker(self.packet)
        self.assertIn('80', result['findings'])
        self.assertEqual(result['sources'][0]['id'], 'file:one')
        self.assertNotIn('operations', result)
        self.assertEqual(json.loads((Path(result['workspace'])/'progress.json').read_text())['status'], 'completed')

    def test_command_preserves_sandbox_blocks_network_and_ignores_user_integrations(self):
        self.worker.env.update(CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX='1', CODEX_PROFILE='unsafe')
        command = self.worker.command(self.root)
        self.assertIn('read-only', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('tools.view_image=false', command)
        self.assertIn('features.apps=false', command)
        self.assertTrue(any(item.startswith('mcp_servers=') and 'exploration_mcp.py' in item for item in command))
        self.assertIn('--ignore-user-config', command)
        self.assertIn('approval_policy="never"', command)
        self.assertIn('web_search="disabled"', command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
        self.assertNotIn('--profile', command)
        self.assertEqual(clean_environment({'PATH': 'p', 'MOODLE_PASSWORD': 'secret', 'BRIDGE_TOKEN': 'secret',
                                            'CODEX_API_KEY': 'model-auth'}), {'PATH': 'p', 'CODEX_API_KEY': 'model-auth'})

    def test_forged_source_or_operation_is_not_accepted(self):
        for change in ({'source_ids': ['file:invented']}, {'operations': [{'save': True}]}, {'request_ids': ['file:invented']}):
            def run(command, **kwargs):
                done = self.fake_run(command, **kwargs)
                path = Path(kwargs['cwd'])/'result.json'
                result = json.loads(path.read_text());result.update(change)
                path.write_text(json.dumps(result))
                return done
            with self.subTest(change=change), patch('chrome_exploration.subprocess.run', side_effect=run), self.assertRaises(ValueError):
                self.worker(self.packet)

    def test_modified_staged_input_invalidates_the_analysis(self):
        def run(command, **kwargs):
            done = self.fake_run(command, **kwargs)
            (Path(kwargs['cwd'])/'source_1.dat').write_bytes(b'invented')
            return done
        with patch('chrome_exploration.subprocess.run', side_effect=run), self.assertRaisesRegex(ValueError, 'modified an input'):
            self.worker(self.packet)

    def test_process_failure_is_technical_and_retains_diagnostic_without_unsandboxed_retry(self):
        with patch('chrome_exploration.subprocess.run', return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaisesRegex(RuntimeError, 'analysis failed'):
                self.worker(self.packet)
        run.assert_called_once()
        progress = json.loads(next(self.root.glob('*/progress.json')).read_text())
        self.assertEqual(progress['status'], 'failed')


class RuntimeExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root/'files').mkdir()
        (root/'files'/'scores.dat').write_bytes(b'group,score\n1,70\n1,90\n')
        self.library = FileLibrary(root/'files', root/'index.json')
        self.browser, _ = browser_fixture()
        self.pool = Mock()
        self.rt = Runtime(None, self.browser, ['chat'], [CID], None, root/'state', self.pool, self.library, True)
        self.rt.explorer = Mock()
        self.rt.send = Mock();self.rt.submit = Mock()
        self.rt.start('chat', {'text': 'Check the group scores', 'sender': 'teacher', 'id': 'request'})
        self.rt.submit.reset_mock()
        self.fid = self.rt.file_snapshot['files'][0]['id']
        self.job = {'rounds': 0, 'repairs': 0}

    def request(self):
        return {'decision': 'inspect_files', 'file_reads': [{'file_id': self.fid, 'offset': 0}]}

    def test_reader_failure_automatically_stages_bytes_and_keeps_planning(self):
        self.rt.accept_decision('chat', self.request(), self.job)
        self.pool.submit.assert_called_once()
        packet = self.pool.submit.call_args.args[1]
        self.assertEqual(packet['sources'][0]['data'], b'group,score\n1,70\n1,90\n')
        self.assertTrue(self.rt.jobs['chat']['exploration'])
        self.assertEqual(self.rt.flows['chat'].state['phase'], 'PLANNING')
        self.rt.send.assert_called_once()  # only the original intake status
        self.browser.execute.assert_not_called()

    def test_analysis_returns_to_planner_without_approving_or_saving(self):
        self.rt.accept_decision('chat', self.request(), self.job)
        job = self.rt.jobs['chat']
        result = {'run_id': job['run_id'], 'findings': 'Mean is 80.', 'source_ids': [self.fid],
                  'unresolved': [], 'request_ids': [], 'sources': [{'id': self.fid, 'name': 'scores.dat', 'sha256': 's', 'kind': 'library'}]}
        finish(self.rt, 'chat', result, job)
        context = self.rt.submit.call_args.args[1]
        self.assertEqual(context['exploration']['evidence'][0]['findings'], 'Mean is 80.')
        self.assertEqual(self.rt.flows['chat'].state['phase'], 'PLANNING')
        self.browser.execute.assert_not_called()

    def test_library_permission_and_source_drift_are_not_bypassed(self):
        self.rt.library_planner_access = False
        with self.assertRaisesRegex(ValueError, 'not authorized'):
            begin(self.rt, 'chat', self.request(), self.job)
        self.rt.library_planner_access = True
        (self.library.root/'scores.dat').write_bytes(b'changed')
        with self.assertRaises(FileLibraryChanged):
            begin(self.rt, 'chat', self.request(), self.job)
        self.pool.submit.assert_not_called()

    def test_exploration_cannot_run_during_approval(self):
        self.rt.flows['chat'].state['phase'] = 'APPROVAL'
        with self.assertRaisesRegex(ValueError, 'unapproved planning'):
            begin(self.rt, 'chat', self.request(), self.job)
        self.pool.submit.assert_not_called()

    def test_cancelled_worker_result_is_discarded_without_changing_task_state(self):
        self.rt.accept_decision('chat', self.request(), self.job)
        future = Future();future.set_result({'run_id': 'old'})
        self.rt.jobs['chat']['future'] = future
        flow = self.rt.flows['chat'];flow.state['phase'] = 'CANCELLED';flow.save()
        self.rt.complete_jobs()
        self.assertEqual(flow.state['phase'], 'CANCELLED')
        self.assertEqual(self.rt.jobs, {})
        self.rt.submit.assert_not_called()

    def test_renewal_with_analysis_returns_to_source_planning(self):
        from chrome_moodle import digest
        flow=self.rt.flows['chat']
        operation={'id':'one','capability':'update_moodle_form','target':TARGET,'course_id':CID}
        flow.state.update(phase='INTERRUPTED',selected=[operation],manifest_hash=digest([operation]),
                          mention_authorized=True,exploration_evidence=[{'findings':'Old mean'}])
        flow.save()
        self.rt.start=Mock();self.rt.refresh_selected_operations=Mock()
        self.rt.renew_approval(flow.state['run'])
        self.rt.start.assert_called_once()
        self.assertTrue(self.rt.start.call_args.args[1]['exploration_required'])
        self.rt.refresh_selected_operations.assert_not_called()

    def test_resume_cannot_offer_old_analysis_derived_operations(self):
        flow=self.rt.flows['chat']
        flow.state.update(exact_resume_operations=[{'id':'one'}],exploration_required=True)
        self.rt.refresh_selected_operations=Mock()
        self.rt.finish_selected_resume('chat')
        self.rt.submit.assert_called_once()
        self.rt.refresh_selected_operations.assert_not_called()
        self.assertEqual(flow.state['phase'],'PLANNING')
        with self.assertRaisesRegex(ValueError,'Reacquire'):
            self.rt.validate_exploration(flow)


if __name__ == '__main__':
    unittest.main()
