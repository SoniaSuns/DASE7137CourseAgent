"""Download menu exit animations must never send Escape into the conversation."""
import io
import unittest
import zipfile
from unittest.mock import patch

import test_whatsapp_multifile_batch as fixtures


class DownloadChatPreservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MultiDocumentBatchTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        fixtures.MultiDocumentBatchTests.tearDownClass()

    def setUp(self):
        self.c = fixtures.MultiDocumentBatchTests()
        self.c.setUp(); self.addCleanup(self.c.doCleanups)
        self.c.fixture(extra='')
        self.c.page.locator('[data-testid="document-thumb"]').evaluate('''e=>{
            e.title='Download "archive.zip"';e.querySelector('[data-name]').textContent='archive.zip';}''')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('one.txt', 'first text')
            archive.writestr('two.txt', 'second text')
        self.payload = stream.getvalue()

    def animate_menu(self, action_lingers):
        self.c.page.evaluate('''({bytes,actionLingers})=>{
            window.escapeCount=0;
            const source=document.querySelector('[data-testid="document-thumb"]');
            source.oncontextmenu=e=>{
                e.preventDefault();
                const menu=document.createElement('div');menu.setAttribute('role','menu');
                const button=document.createElement('button');button.setAttribute('role','menuitem');button.textContent='Download';
                const reaction=document.createElement('div');reaction.setAttribute('role','menu');reaction.textContent='Reaction';
                button.onclick=()=>{
                    window.downloaded.push('archive.zip');
                    // WhatsApp has already handled the selection, but closing DOM
                    // remains visible. Escape now belongs to the conversation.
                    if(actionLingers)setTimeout(()=>menu.remove(),450);else menu.remove();
                    setTimeout(()=>reaction.remove(),600);
                    const a=document.createElement('a');a.download='archive.zip';
                    a.href=URL.createObjectURL(new Blob([new Uint8Array(bytes)]));a.click();
                };
                menu.appendChild(button);document.body.append(menu,reaction);
            };
            document.addEventListener('keydown',e=>{
                if(e.key==='Escape'){window.escapeCount++;document.querySelector('#main')?.remove();}
            });
        }''', {'bytes': list(self.payload), 'actionLingers': action_lingers})

    def assert_completes(self, action_lingers):
        c = self.c; self.animate_menu(action_lingers); c.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS', .1):
            c.r.handle_queue()
        self.assertEqual(c.page.evaluate('window.escapeCount'), 0)
        self.assertEqual(c.page.locator('#main').count(), 1)
        c.r.submit.assert_called_once()
        state = c.r.flows['group'].state
        self.assertEqual(state['attachment_postcheck']['status'], 'verified')
        self.assertEqual(c.page.evaluate('window.downloaded'), ['archive.zip'])
        context = c.r.submit.call_args.args[1]
        self.assertEqual([r['status'] for r in context['archive_members']], ['read', 'read'])
        self.assertEqual(len(context['file_evidence']), 2)

    def test_reaction_exit_animation_does_not_close_chat(self):
        self.assert_completes(False)

    def test_action_menu_exit_animation_does_not_close_chat(self):
        self.assert_completes(True)

    def test_three_files_wait_for_previous_reaction_exit_without_escape(self):
        c = self.c; c.fixture()
        c.page.evaluate('''()=>{
            window.escapeCount=0;
            for(const source of document.querySelectorAll('[data-testid=document-thumb]')){
                const old=source.oncontextmenu;
                source.oncontextmenu=e=>{
                    old(e);
                    const reaction=document.createElement('div');reaction.setAttribute('role','menu');reaction.textContent='Reaction';
                    document.body.appendChild(reaction);
                    const button=document.querySelector('[role=menuitem]'),click=button.onclick;
                    button.onclick=()=>{click();setTimeout(()=>reaction.remove(),600);};
                };
            }
            document.addEventListener('keydown',e=>{
                if(e.key==='Escape'){window.escapeCount++;document.querySelector('#main')?.remove();}
            });
        }''')
        c.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS', .1):
            c.r.handle_queue()
        self.assertEqual(c.page.evaluate('window.escapeCount'), 0)
        self.assertEqual(c.page.evaluate('window.downloaded'), ['first.py', 'second.py', 'third.py'])
        c.r.submit.assert_called_once()
        self.assertEqual(len(c.r.submit.call_args.args[1]['request_attachments']), 3)
        self.assertEqual(c.r.flows['group'].state['attachment_postcheck']['status'], 'verified')

    def test_click_error_after_dispatch_cannot_send_escape_into_chat(self):
        from playwright.sync_api import Locator
        c = self.c; self.animate_menu(True); c.r.poll()
        original = Locator.click
        def click(locator, *args, **kwargs):
            is_download = locator.get_attribute('role') == 'menuitem'
            result = original(locator, *args, **kwargs)
            if is_download:
                raise RuntimeError('Synthetic post-dispatch browser error')
            return result
        with patch.object(Locator, 'click', click), patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS', .1):
            c.r.handle_queue()
        self.assertEqual(c.page.evaluate('window.escapeCount'), 0)
        self.assertEqual(c.page.locator('#main').count(), 1)
        self.assertEqual(c.r.flows['group'].state['phase'], 'FAILED')
        c.r.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
