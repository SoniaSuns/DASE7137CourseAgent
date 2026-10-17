"""Named regressions for task-bound incoming WhatsApp documents."""
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import Mock, patch

from chrome_file_library import FileLibrary, FileLibraryChanged, sha
from chrome_whatsapp_attachments import WhatsAppFiles, descriptors, fingerprint, safe_name
from run_shared_chrome_agent import Runtime, request_record, intake_records


class AttachmentBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root/'base').mkdir()
        self.library = WhatsAppFiles(FileLibrary(root/'base', root/'index.json'), root/'downloads')
        self.state = {'sender': 'alice', 'run': 'run1', 'whatsapp_files': []}
        self.library.bind('group', self.state)
        self.ctx = self.browser.new_context(accept_downloads=True)
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='<body></body>'))
        self.page.goto('https://fixture.invalid')

    def fixture(self, viewer=False, suggested='participants.html'):
        self.page.set_content('''
          <div class="message-in" data-id="other"><button data-testid="document-thumb" title='下载“participants.html”'>other</button></div>
          <div class="message-out" data-id="outgoing"><button data-testid="document-thumb" title='下载“participants.html”'>out</button></div>
          <div class="message-in" data-id="wanted">
            <blockquote><button data-testid="document-thumb" title='下载“quoted.html”'>quoted</button></blockquote>
            <button id="wanted-file" data-testid="document-thumb" title='下载“participants.html”'>file</button>
          </div>''')
        self.page.evaluate('''({viewer,suggested}) => {
          window.clicked=[];
          const download=()=>{
            const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob(['<table><tr><td>Alice</td><td>Group 2</td></tr></table>'],{type:'text/html'}));
            a.download=suggested; a.click();
          };
          document.querySelector('#wanted-file').onclick=()=>{
            window.clicked.push('wanted');
            if (!viewer) {download();return;}
            const v=document.createElement('div');
            v.innerHTML='<span>participants.html</span><button aria-label="Download">Download</button>';
            document.body.appendChild(v);v.querySelector('button').onclick=download;
          };
          document.querySelector('[data-id="other"] button').onclick=()=>window.clicked.push('other');
        }''', {'viewer': viewer, 'suggested': suggested})

    def download(self):
        docs = descriptors(self.page, ['wanted'])['wanted']
        self.library.download(self.page, 'wanted', docs, self.state['whatsapp_files'].append)

    def test_exact_incoming_document_excludes_quotes_outgoing_and_same_name_neighbor(self):
        self.fixture()
        found = descriptors(self.page, ['wanted', 'outgoing', 'other'])
        self.assertEqual(found['wanted'], [{'index': 0, 'name': 'participants.html'}])
        self.assertNotIn('outgoing', found)
        self.download()
        self.assertEqual(self.page.evaluate('window.clicked'), ['wanted'])
        entry = self.library.files[0]
        read = self.library.inspect(self.library.snapshot(), entry['id'])
        self.assertIn('Alice | Group 2', read['text'])
        # Source bytes are task-local and absent from the persistent owner index.
        index = json.loads(self.library.index.read_text())
        self.assertEqual(index['files'], [])
        self.download()
        self.assertEqual(self.page.evaluate('window.clicked'), ['wanted'])
        self.assertEqual(len(self.state['whatsapp_files']), 1)

    def test_viewer_download_uses_visible_filename_and_unique_control(self):
        self.fixture(viewer=True)
        self.download()
        self.assertEqual(len(self.library.files), 1)

    def test_filename_mismatch_and_oversize_are_not_registered(self):
        self.fixture(suggested='different.html')
        with self.assertRaisesRegex(ValueError, 'filename'):
            self.download()
        self.assertEqual(self.state['whatsapp_files'], [])
        self.fixture()
        with patch('chrome_whatsapp_attachments.MAX_FILE', 10):
            with self.assertRaisesRegex(ValueError, '32 MiB'):
                self.download()
        self.assertEqual(self.state['whatsapp_files'], [])

    def test_changed_document_descriptor_is_rejected_before_click(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.library.download(self.page, 'wanted', [{'index': 0, 'name': 'old.html'}], Mock())
        self.assertEqual(self.page.evaluate('window.clicked'), [])

    def test_task_and_requester_switch_reject_old_snapshot_and_tampered_bytes(self):
        self.fixture()
        self.download()
        snapshot = self.library.snapshot()
        entry = snapshot['files'][0]
        for state in ({'sender': 'bob', 'run': 'run1'}, {'sender': 'alice', 'run': 'run2'}):
            self.library.bind('group', state)
            self.assertEqual(self.library.snapshot()['files'], [])
            with self.assertRaisesRegex(ValueError, 'current task'):
                self.library.read_bytes(snapshot, entry['id'])
        with self.assertRaisesRegex(ValueError, 'another task'):
            self.library.bind('other group', self.state)
        self.library.bind('group', self.state)
        (self.library.storage.root/entry['storage_key']).write_bytes(b'changed')
        with self.assertRaises(FileLibraryChanged):
            self.library.read_bytes(snapshot, entry['id'])

    def test_task_file_count_and_aggregate_limits(self):
        self.fixture()
        with patch('chrome_whatsapp_attachments.MAX_FILES', 0):
            with self.assertRaisesRegex(ValueError, '12'):
                self.download()
        with patch('chrome_whatsapp_attachments.MAX_TASK', 10):
            with self.assertRaisesRegex(ValueError, '128 MiB'):
                self.download()
        self.assertEqual(self.library.files, [])

    def test_real_intake_document_caption_retains_mention_and_source_provenance(self):
        self.fixture()
        self.page.evaluate('''() => {
          localStorage.setItem('last-wid-md',JSON.stringify('123456789:3@c.us'));
          const node=document.createElement('div');
          node.setAttribute('data-pre-plain-text','[14:00, 2026/10/17] Alice:');
          node.innerHTML='<span class="selectable-text"><span data-mention-jid="123456789@s.whatsapp.net">@Agent</span> Inspect this source</span>';
          document.querySelector('[data-id="wanted"]').appendChild(node);
        }''')
        rows = [r for r in intake_records(self.page, 'Agent') if r['message_id']=='wanted']
        self.assertTrue(rows)
        self.assertTrue(all(r['self_mentioned'] for r in rows))
        self.assertEqual(len({r['attachment_fingerprint'] for r in rows}), 1)
        self.assertEqual(rows[0]['attachments'], [{'index': 0, 'name': 'participants.html'}])
        self.assertEqual(rows[0]['addressed_text'], 'Inspect this source')

    def test_stalled_download_has_bounded_timeout_and_no_registered_source(self):
        self.fixture()
        self.page.evaluate("document.querySelector('#wanted-file').onclick=()=>{}")
        started = time.monotonic()
        with patch('chrome_whatsapp_attachments.DOWNLOAD_SECONDS', .2):
            with self.assertRaisesRegex(ValueError, 'timed out'): self.download()
        self.assertLess(time.monotonic()-started, 3)
        self.assertEqual(self.library.files, [])


class AttachedChromeDownloadTests(unittest.TestCase):
    def test_download_in_existing_default_context_over_cdp_no_defaults(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as temporary, sync_playwright() as p:
            root = Path(temporary)
            process = subprocess.Popen([p.chromium.executable_path, '--headless=new', '--no-sandbox',
                '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                '--no-first-run', '--no-default-browser-check', '--user-data-dir='+str(root/'profile'), 'about:blank'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            browser = None
            try:
                port_file = root/'profile/DevToolsActivePort'
                for _ in range(100):
                    if port_file.exists(): break
                    time.sleep(.1)
                port, path = port_file.read_text().splitlines()[:2]
                browser = p.chromium.connect_over_cdp('ws://127.0.0.1:'+port+path, no_defaults=True)
                page = browser.contexts[0].pages[0]
                page.set_content('''<div class="message-in" data-id="file"><button data-testid="document-thumb" title='下载“notes.txt”'
                   onclick="const a=document.createElement('a');a.href=window.URL.createObjectURL(new Blob(['attached CDP bytes']));a.download='notes.txt';a.click();">notes.txt</button></div>''')
                (root/'base').mkdir()
                library = WhatsAppFiles(FileLibrary(root/'base', root/'index.json'), root/'sources')
                library.bind('group', {'sender': 'alice', 'run': 'run1'})
                library.download(page, 'file', descriptors(page, ['file'])['file'], Mock())
                read = library.inspect(library.snapshot(), library.files[0]['id'])
                self.assertIn('attached CDP bytes', read['text'])
            finally:
                if browser:
                    try: browser.new_browser_cdp_session().send('Browser.close')
                    except Exception: pass
                    browser.close()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=10)


class AttachmentIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root/'base').mkdir()
        self.library = WhatsAppFiles(FileLibrary(root/'base', root/'index.json'), root/'downloads')
        browser = Mock(overviews={}, forms={}, unavailable_targets={})
        self.r = Runtime(Mock(), browser, ['group'], [], Mock(), root/'state', Mock(), self.library, True,
                         mention_only_chats=['group'])
        self.r.open = Mock()
        self.r.send = Mock()
        self.r.submit = Mock()
        self.r.execute = Mock()
        self.r.account_name = 'Agent'

    def raw(self, mid='incoming', text='Inspect this source', mentioned=True, sender='alice'):
        row = {'message_id': mid, 'direction': 'incoming', 'sender_id': sender,
               'message_text': text, 'addressed_text': text, 'self_mentioned': mentioned,
               'meta': self.r.started.strftime('[%H:%M, %Y/%m/%d] Alice:'),
               'documents': ['notes.txt'], 'attachments': [{'name': 'notes.txt', 'index': 0}]}
        row['attachment_fingerprint'] = fingerprint({**row, 'dom_id': mid})
        return row

    def waiting(self, phase='QUESTION'):
        flow = self.r.flows['group']
        flow.begin('Check supplied source', 'alice', 'initial')
        flow.state.update(phase=phase, mention_authorized=True, reply_boundary='boundary', code='123456',
                          approval_sent=True, candidates=[{'id': 'old'}], selected=[{'id': 'old'}])
        flow.save()
        return flow

    def poll(self, rows):
        with patch('run_shared_chrome_agent.intake_records', return_value=rows):
            self.r.poll()

    def test_captionless_attachment_only_enters_bound_same_requester_question(self):
        self.poll([self.raw(text='', mentioned=False)])
        self.assertEqual(self.r.queue, [])
        flow = self.waiting()
        self.poll([self.raw('other', text='', mentioned=False, sender='bob'),
                   self.raw('reply', text='', mentioned=False)])
        self.assertEqual(len(self.r.queue), 1)
        self.assertEqual(self.r.queue[0]['reply_to_run'], flow.state['run'])
        self.assertTrue(self.r.queue[0]['attachment_only'])

    def test_document_with_real_mention_starts_task_and_is_downloaded_before_plan(self):
        row = self.raw()
        self.poll([row])
        with patch('run_shared_chrome_agent.intake_records', return_value=[row]), patch.object(self.library, 'download') as download:
            self.r.handle_queue()
        download.assert_called_once()
        self.r.submit.assert_called_once()
        self.r.execute.assert_not_called()

    def test_caption_sender_or_attachment_change_rejected_before_download(self):
        self.waiting()
        row = self.raw()
        record = request_record(row)
        for change in ({'sender_id': 'bob'}, {'message_text': 'Changed'}, {'attachments': [{'name': 'other.txt', 'index': 0}]}):
            changed = {**row, **change}
            changed['attachment_fingerprint'] = fingerprint({**changed, 'dom_id': row['message_id']})
            with patch('run_shared_chrome_agent.intake_records', return_value=[changed]), patch.object(self.library, 'download') as download:
                with self.assertRaisesRegex(ValueError, 'changed or is unavailable'):
                    self.r.capture_attachments('group', record)
                download.assert_not_called()

    def test_attachment_with_ok_caption_invalidates_approval_and_replans(self):
        flow = self.waiting('APPROVAL')
        row = self.raw(text='OK 123456', mentioned=False)
        self.poll([row])
        with patch('run_shared_chrome_agent.intake_records', return_value=[row]), patch.object(self.library, 'download') as download:
            self.r.handle_queue()
        download.assert_called_once()
        self.r.execute.assert_not_called()
        self.r.submit.assert_called_once()
        self.assertFalse(flow.state['approval_sent'])
        self.assertNotIn('selected', flow.state)
        self.assertNotEqual(flow.state['code'], '123456')

    def test_old_reply_boundary_never_downloads(self):
        flow = self.waiting()
        self.poll([self.raw(text='', mentioned=False)])
        flow.state['reply_boundary'] = 'new question'
        with patch.object(self.library, 'download') as download:
            self.r.handle_queue()
        download.assert_not_called()
        self.r.submit.assert_not_called()

    def test_attachment_edits_retire_queued_original(self):
        row = self.raw()
        self.poll([row])
        changed = copy.deepcopy(row)
        changed['attachments'][0]['name'] = 'replacement.txt'
        self.poll([changed])
        self.assertEqual(len(self.r.queue), 1)
        self.assertEqual(self.r.queue[0]['attachments'][0]['name'], 'replacement.txt')
        self.assertEqual(self.r.queue[0]['attachment_message_id'], row['message_id'])

    def test_unsafe_windows_filenames_never_become_paths(self):
        for name in ('../file.txt', 'a/b.txt', 'a\\b.txt', 'CON.txt', 'a:stream.txt', '..', 'x. '):
            with self.subTest(name=name), self.assertRaises(ValueError): safe_name(name)
        self.assertEqual(safe_name('课程说明.pdf'), '课程说明.pdf')

    def test_new_source_at_scope_requires_new_plan(self):
        flow = self.waiting('SCOPE')
        row = self.raw(text='', mentioned=False)
        self.poll([row])
        with patch('run_shared_chrome_agent.intake_records', return_value=[row]), patch.object(self.library, 'download'):
            self.r.handle_queue()
        self.assertEqual(flow.state['phase'], 'PLANNING')
        self.assertNotIn('candidates', flow.state)
        self.assertNotIn('selected', flow.state)
        self.assertFalse(flow.state['approval_sent'])
        self.assertEqual(self.r.submit.call_args.args[1]['pending_scope'], [])

    def test_download_failure_is_reported_failed_not_waiting(self):
        row = self.raw()
        self.poll([row])
        with patch('run_shared_chrome_agent.intake_records', return_value=[row]), patch.object(self.library, 'download', side_effect=ValueError('Download timed out')):
            self.r.handle_queue()
        self.assertEqual(self.r.flows['group'].state['phase'], 'FAILED')
        self.assertIn('Status: Failed', self.r.send.call_args.args[1])
        self.r.submit.assert_not_called()

    def test_attachment_without_legacy_document_metadata_is_not_lost(self):
        self.waiting()
        row = self.raw(text='', mentioned=False)
        row['documents'] = []
        self.poll([row])
        self.assertEqual(len(self.r.queue), 1)

    def saved_source(self):
        flow = self.waiting()
        data = b'Week 8 release'
        (self.library.storage.root/'saved.txt').write_bytes(data)
        entry = {'id': 'wa:saved', 'relative_path': 'WhatsApp/saved/notes.txt', 'format': '.txt',
                 'bytes': len(data), 'sha256': sha(data), 'text_reader': True, 'storage_key': 'saved.txt',
                 'owner': ['group', 'alice', flow.state['run']], 'message_id': 'source-message'}
        flow.state.update(whatsapp_files=[entry], phase='FAILED')
        flow.save()
        return flow

    def test_explicit_same_task_retry_revalidates_and_rebinds_saved_attachment(self):
        flow = self.saved_source()
        old_run = flow.state['run']
        self.r.start('group', {'id': 'resume', 'sender': 'alice', 'text': flow.state['task'],
                              'self_mentioned': True, 'retry_of': old_run})
        self.assertNotEqual(flow.state['run'], old_run)
        entry = flow.state['whatsapp_files'][0]
        self.assertEqual(entry['owner'], ['group', 'alice', flow.state['run']])
        context = self.r.submit.call_args.args[1]
        self.assertEqual(context['file_library']['files'][0]['id'], 'wa:saved')
        self.assertIn('Week 8', self.library.inspect(self.r.file_snapshot, 'wa:saved')['text'])
        flow.state['phase'] = 'DONE'
        self.r.start('group', {'id': 'new', 'sender': 'alice', 'text': 'New unrelated task', 'self_mentioned': True})
        self.assertEqual(self.r.submit.call_args.args[1]['file_library']['files'], [])

    def test_retry_cannot_import_other_requesters_saved_source(self):
        flow = self.saved_source()
        with self.assertRaisesRegex(ValueError, 'another requester'):
            self.r.start('group', {'id': 'resume', 'sender': 'bob', 'text': flow.state['task'],
                                  'self_mentioned': True, 'retry_of': flow.state['run']})

    def test_retry_rejects_tampered_saved_attachment(self):
        flow = self.saved_source()
        (self.library.storage.root/'saved.txt').write_bytes(b'changed')
        with self.assertRaises(FileLibraryChanged):
            self.r.start('group', {'id': 'resume', 'sender': 'alice', 'text': flow.state['task'],
                                  'self_mentioned': True, 'retry_of': flow.state['run']})
        self.r.submit.assert_not_called()



if __name__ == '__main__':
    unittest.main()
