"""Regression for the live test-approval document caption missed on 17 October."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from chrome_file_library import FileLibrary
from chrome_whatsapp_attachments import descriptors, CLICK_DOCUMENT_SCRIPT
from run_shared_chrome_agent import Runtime, intake_records, record_time, request_record


class DocumentCaptionIntakeTests(unittest.TestCase):
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
        self.ctx = self.browser.new_context()
        self.addCleanup(self.ctx.close)
        self.page = self.ctx.new_page()
        self.page.route('**/*', lambda route: route.fulfill(body='<body></body>', content_type='text/html'))
        self.page.goto('https://fixture.invalid')
        self.page.evaluate("localStorage.setItem('last-lid',JSON.stringify('123456789@lid'))")

    def fixture(self, *, tail='in', caption=None, separator='昨天'):
        if caption is None:
            caption='''<span role="button" tabindex="0"><span data-plain-text="@CourseAgent"
              data-app-text-template="123456789@lid">@CourseAgent</span></span> Tell me the content of this file'''
        self.page.set_content(f'''<div id="main"><div data-tab="8">
          <div tabindex="-1">{separator}</div><div><div><div role="row">
          <div data-id="document-message" data-testid="conv-msg-document-message">
            <div data-testid="msg-container"><span data-testid="tail-{tail}"></span>
              <div><span data-testid="author">Alice</span><span data-testid="author">+123 456 7890</span></div>
              <div role="button" data-testid="document-thumb" title='下载“ScriptParser.cs”'>
                <span>ScriptParser.cs</span><span>CS</span><span>24 kB</span></div>
              <span data-testid="document-caption selectable-text" class="selectable-text copyable-text">{caption}</span>
              <div data-testid="msg-meta"><span>17:10</span></div>
            </div>
          </div></div></div></div></div></div>''')

    def record(self):
        rows = intake_records(self.page, 'CourseAgent')
        return next(r for r in rows if r.get('message_id')=='document-message')

    def test_live_document_layout_reads_caption_real_mention_file_and_timeline_date(self):
        self.fixture()
        row = self.record()
        self.assertEqual(row['addressed_text'], 'Tell me the content of this file')
        self.assertTrue(row['self_mentioned'])
        self.assertEqual(row['attachments'], [{'index': 0, 'name': 'ScriptParser.cs'}])
        now = datetime(2026, 10, 18, 0, 6, tzinfo=ZoneInfo('Asia/Hong_Kong'))
        self.assertEqual(record_time(row, now), datetime(2026, 10, 17, 17, 10, tzinfo=now.tzinfo))
        self.page.evaluate("document.querySelector('[data-testid=\"document-thumb\"]').onclick=()=>window.fileClicked=true")
        self.assertTrue(self.page.evaluate(CLICK_DOCUMENT_SCRIPT, {'id': row['message_id'], **row['attachments'][0], 'marker':'fixture'}))
        self.page.locator('[data-course-agent-download="fixture"]').click()
        self.assertTrue(self.page.evaluate('window.fileClicked'))

    def test_plain_text_at_sign_is_not_a_structured_mention(self):
        self.fixture(caption='@CourseAgent Tell me the content of this file')
        self.assertFalse(self.record()['self_mentioned'])

    def test_quoted_caption_and_quoted_incoming_tail_never_authorize(self):
        self.fixture(tail='out', caption='''<blockquote><span data-testid="tail-in"></span>
             <span data-mention-jid="123456789@lid">@CourseAgent</span> Tell me the content</blockquote>''')
        row = self.record()
        self.assertFalse(row['self_mentioned'])
        self.assertEqual(row['attachments'], [])
        self.assertFalse(self.page.evaluate(CLICK_DOCUMENT_SCRIPT, {'id': 'document-message', 'index': 0, 'name': 'ScriptParser.cs'}))

    def test_outgoing_document_without_legacy_classes_cannot_be_downloaded(self):
        self.fixture(tail='out')
        self.assertEqual(descriptors(self.page, ['document-message']), {})
        self.assertEqual(self.record()['direction'],'outgoing')
        self.assertIsNone(request_record(self.record()))

    def test_outgoing_caption_with_own_mention_never_enters_runtime_queue(self):
        self.fixture(tail='out',separator='今天')
        row=self.record()
        row['document_clock']=datetime.now(ZoneInfo('Asia/Hong_Kong')).strftime('%H:%M')
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Mock(),Mock(),['group'],[],Mock(),Path(temp),Mock(),mention_only_chats=['group'])
            runtime.account_name='CourseAgent';runtime.open=Mock()
            with patch('run_shared_chrome_agent.intake_records',return_value=[row]):runtime.poll()
            self.assertEqual(runtime.queue,[])

    def test_missing_timeline_date_cannot_be_inferred_from_caption(self):
        self.fixture(separator='', caption='今天 2026/10/18 @CourseAgent read this')
        self.assertIsNone(record_time(self.record(), datetime.now(ZoneInfo('Asia/Hong_Kong'))))

    def test_quoted_clock_is_not_used_as_the_documents_timestamp(self):
        self.fixture(caption='<blockquote><div data-testid="msg-meta">01:23</div></blockquote> source')
        self.assertEqual(self.record()['document_clock'],'17:10')

    def test_date_text_inside_previous_message_is_not_a_separator(self):
        self.fixture(separator='')
        self.page.evaluate('''() => {
          const timeline=document.querySelector('[data-tab="8"]');
          const fake=document.createElement('div');fake.tabIndex=-1;
          fake.innerHTML='<div data-id="previous">今天</div>';
          timeline.insertBefore(fake,timeline.children[1]);
        }''')
        self.assertIsNone(record_time(self.record(), datetime.now(ZoneInfo('Asia/Hong_Kong'))))

    def test_fresh_caption_enters_queue_while_historical_caption_stays_excluded(self):
        self.fixture(separator='今天')
        row = self.record()
        now = datetime.now(ZoneInfo('Asia/Hong_Kong'))
        row['document_clock'] = now.strftime('%H:%M')
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Mock(), Mock(), ['group'], [], Mock(), Path(temp), Mock(), mention_only_chats=['group'])
            runtime.account_name='CourseAgent';runtime.open=Mock()
            with patch('run_shared_chrome_agent.intake_records',return_value=[row]): runtime.poll()
            self.assertEqual(len(runtime.queue), 1)
            self.assertEqual(runtime.queue[0]['text'], 'Tell me the content of this file')
            self.assertEqual(runtime.queue[0]['attachments'][0]['name'], 'ScriptParser.cs')
            old={**row,'message_id':'older-message','document_date_label':'昨天'}
            with patch('run_shared_chrome_agent.intake_records',return_value=[old]): runtime.poll()
            self.assertEqual(len(runtime.queue), 1)

    def test_code_file_is_read_as_text_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sources=root/'sources';sources.mkdir()
            code='using UnityEngine;\npublic class ScriptParser { /* source evidence */ }'
            (sources/'ScriptParser.cs').write_bytes(code.encode('utf-8'))
            library=FileLibrary(sources,root/'index.json');snapshot=library.snapshot()
            entry=snapshot['files'][0]
            self.assertTrue(entry['text_reader'])
            result=library.inspect(snapshot,entry['id'])
            self.assertEqual(result['status'],'read')
            self.assertIn(code,result['text'])


if __name__=='__main__': unittest.main()
