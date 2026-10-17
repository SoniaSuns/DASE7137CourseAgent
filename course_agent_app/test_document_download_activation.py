"""Regression: activate WhatsApp before a download after using a Moodle tab."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chrome_whatsapp_attachments import download_document


class DownloadActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.p.stop()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ctx=self.browser.new_context();self.addCleanup(self.ctx.close)
        self.page=self.ctx.new_page()
        self.page.set_content('''<div class="message-in" data-id="source">
          <button data-testid="document-thumb" title='Download "notes.py"'>notes.py</button></div>''')
        self.page.evaluate('''()=>{
          window.attempts=[];
          document.querySelector('button').onclick=()=>{
            const active=window.appActive===true;
            window.attempts.push(active);
            if(!active)return;
            const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['source text, never executed']));
            a.download='notes.py';a.click();
          };
        }''')

    def test_background_source_tab_is_activated_before_single_download_click(self):
        # Headless Chromium reports all tabs visible. Model the application's
        # activation gate explicitly; retain real CDP download events and bytes.
        original=self.page.bring_to_front
        def activate():
            original();self.page.evaluate('window.appActive=true')
        with patch.object(self.page,'bring_to_front',side_effect=activate), patch('chrome_whatsapp_attachments.DOWNLOAD_SECONDS',.5):
            data=download_document(self.page,'source',{'index':0,'name':'notes.py'},Path(self.temp.name))
        self.assertEqual(data,b'source text, never executed')
        self.assertEqual(self.page.evaluate('window.attempts'),[True])

    def test_no_download_event_reports_unstarted_transfer_without_resend_instruction(self):
        self.page.evaluate("document.querySelector('button').onclick=()=>{}")
        with patch('chrome_whatsapp_attachments.DOWNLOAD_SECONDS',.1):
            with self.assertRaisesRegex(ValueError,'did not start') as caught:
                download_document(self.page,'source',{'index':0,'name':'notes.py'},Path(self.temp.name))
        self.assertNotIn('Send the file again',str(caught.exception))


if __name__=='__main__':unittest.main()
