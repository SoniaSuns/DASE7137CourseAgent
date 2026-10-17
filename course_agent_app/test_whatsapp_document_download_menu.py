"""Regression for script attachments: Save through Download, never Open File."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chrome_whatsapp_attachments import descriptors,download_document_via_menu,WhatsAppFiles


class ExplicitDocumentDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.p.stop()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ctx=self.browser.new_context();self.addCleanup(self.ctx.close)
        self.page=self.ctx.new_page()

    def fixture(self,label='Download',*,duplicate=False,changed=False,warning=False):
        self.page.set_content('''<div class="message-in" data-id="exact">
          <button data-testid="document-thumb" title='Download "script.py"'>script.py</button></div>
          <div class="message-out" data-id="other"><button data-testid="document-thumb" title='Download "script.py"'>other</button></div>''')
        self.page.evaluate('''({label,duplicate,changed,warning})=>{
          window.actions=[];
          const source=document.querySelector('[data-id="exact"] button');
          const warn=()=>{
            const modal=document.createElement('div');modal.dataset.testid='harmful-file-warning-modal';
            modal.innerHTML='<p>This file may be harmful</p><button>Cancel</button><button>Open File</button>';
            modal.querySelectorAll('button')[0].onclick=()=>{window.actions.push('cancel');modal.remove();};
            modal.querySelectorAll('button')[1].onclick=()=>window.actions.push('unsafe-open');
            document.body.appendChild(modal);
          };
          source.onclick=()=>{window.actions.push('card-open');warn();};
          source.oncontextmenu=e=>{
            e.preventDefault();window.actions.push('exact-menu');
            const menu=document.createElement('div');menu.setAttribute('role','menu');
            for(let i=0;i<(duplicate?2:1);i++){
              const b=document.createElement('button');b.setAttribute('role','menuitem');b.textContent=label;
              b.onclick=()=>{
                window.actions.push('download');menu.remove();
                if(warning){warn();return;}
                const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['unexecuted Python source bytes']));
                a.download='script.py';a.click();
              };
              menu.appendChild(b);
            }
            document.body.appendChild(menu);
            if(changed)source.title='Download "different.py"';
          };
          document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelector('[role="menu"]')?.remove();});
        }''',{'label':label,'duplicate':duplicate,'changed':changed,'warning':warning})

    def download(self):
        docs=descriptors(self.page,['exact'])['exact']
        with patch('chrome_whatsapp_attachments.DOWNLOAD_SECONDS',.4), patch('chrome_whatsapp_attachments.MENU_SECONDS',.6):
            return download_document_via_menu(self.page,'exact',docs[0],Path(self.tmp.name))

    def test_explicit_download_saves_bytes_without_card_open_or_warning(self):
        for label in ('Download','下载','下載'):
            with self.subTest(label=label):
                self.fixture(label)
                self.assertEqual(self.download(),b'unexecuted Python source bytes')
                self.assertEqual(self.page.evaluate('window.actions'),['exact-menu','download'])

    def test_duplicate_download_controls_are_rejected_without_open_fallback(self):
        self.fixture(duplicate=True)
        with self.assertRaisesRegex(ValueError,'unique Download'):self.download()
        self.assertEqual(self.page.evaluate('window.actions'),['exact-menu'])

    def test_menu_without_download_does_not_click_other_action_or_open_card(self):
        self.fixture('Delete')
        with self.assertRaisesRegex(ValueError,'unique Download'):self.download()
        self.assertEqual(self.page.evaluate('window.actions'),['exact-menu'])

    def test_source_changed_after_menu_open_is_not_downloaded(self):
        self.fixture(changed=True)
        with self.assertRaisesRegex(ValueError,'changed'):self.download()
        self.assertEqual(self.page.evaluate('window.actions'),['exact-menu'])

    def test_existing_unrelated_menu_never_supplies_download_action(self):
        self.fixture()
        self.page.evaluate('''()=>{const e=document.createElement('div');e.setAttribute('role','menu');e.innerHTML='<button role="menuitem">Download</button>';document.body.appendChild(e);}''')
        with self.assertRaisesRegex(ValueError,'existing WhatsApp menu'):self.download()
        self.assertEqual(self.page.evaluate('window.actions'),[])

    def test_security_warning_is_cancelled_and_reported_without_waiting_or_acceptance(self):
        self.fixture(warning=True)
        with self.assertRaisesRegex(ValueError,'safety confirmation') as caught:self.download()
        self.assertNotIn('timed out',str(caught.exception))
        self.assertEqual(self.page.evaluate('window.actions'),['exact-menu','download','cancel'])

    def test_missing_context_menu_never_falls_back_to_card_even_without_warning(self):
        self.fixture()
        self.page.evaluate('''()=>{const s=document.querySelector('[data-id="exact"] button');
          s.oncontextmenu=e=>e.preventDefault();s.onclick=()=>window.actions.push('unverified-open');}''')
        with self.assertRaisesRegex(ValueError,'unique Download'):self.download()
        self.assertEqual(self.page.evaluate('window.actions'),[])

    def test_reaction_toolbar_does_not_make_single_action_menu_ambiguous(self):
        self.fixture()
        self.page.evaluate('''()=>{const s=document.querySelector('[data-id="exact"] button'),old=s.oncontextmenu;
          s.oncontextmenu=e=>{old(e);const r=document.createElement('div');r.setAttribute('role','menu');
          r.innerHTML='<button>Reaction</button>';document.body.appendChild(r);};}''')
        self.assertEqual(self.download(),b'unexecuted Python source bytes')

    def test_multiple_action_menus_rejected_even_with_only_one_download_item(self):
        self.fixture()
        self.page.evaluate('''()=>{const s=document.querySelector('[data-id="exact"] button'),old=s.oncontextmenu;
          s.oncontextmenu=e=>{old(e);const r=document.createElement('div');r.setAttribute('role','menu');
          r.innerHTML='<button role="menuitem">Delete</button>';document.body.appendChild(r);};}''')
        with self.assertRaisesRegex(ValueError,'Multiple attachment action menus'):self.download()
        self.assertEqual(self.page.evaluate('window.actions'),['exact-menu'])

    def test_waits_for_download_item_when_menu_renders_first(self):
        self.fixture()
        self.page.evaluate('''()=>{const s=document.querySelector('[data-id="exact"] button'),old=s.oncontextmenu;
          s.oncontextmenu=e=>{old(e);const b=document.querySelector('[role="menuitem"]');
          b.hidden=true;setTimeout(()=>b.hidden=false,150);};}''')
        self.assertEqual(self.download(),b'unexecuted Python source bytes')

    def test_live_factory_explicitly_selects_menu_adapter_and_library_uses_it(self):
        import ast
        from unittest.mock import Mock
        from chrome_file_library import FileLibrary
        tree=ast.parse(Path(__file__).with_name('run_shared_chrome_agent.py').read_text(encoding='utf-8-sig'))
        factories=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='WhatsAppFiles']
        self.assertTrue(factories)
        for factory in factories:
            adapters=[k.value for k in factory.keywords if k.arg=='document_download']
            self.assertEqual(len(adapters),1)
            self.assertIsInstance(adapters[0],ast.Name)
            self.assertEqual(adapters[0].id,'download_document_via_menu')
        root=Path(self.tmp.name);(root/'base').mkdir()
        library=WhatsAppFiles(FileLibrary(root/'base',root/'index.json'),root/'stored',
                              document_download=download_document_via_menu)
        library.bind('fixture',{'sender':'owner','run':'run','whatsapp_files':[]})
        self.fixture()
        receipt=Mock()
        library.download(self.page,'exact',descriptors(self.page,['exact'])['exact'],receipt)
        receipt.assert_called_once()
        self.assertEqual(self.page.evaluate('window.actions'),['exact-menu','download'])


if __name__=='__main__':unittest.main()
