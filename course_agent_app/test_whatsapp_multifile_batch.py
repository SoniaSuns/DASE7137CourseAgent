"""Multi-document captions include all structurally bound incoming siblings."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from chrome_file_library import FileLibrary
from chrome_whatsapp_attachments import WhatsAppFiles,descriptors,attachment_batches,download_document_via_menu
from chrome_whatsapp_batch import batch_sources,collect_batch
from run_shared_chrome_agent import Runtime,intake_records,request_record,record_message_id


class MultiDocumentBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ctx=self.browser.new_context();self.addCleanup(self.ctx.close)
        self.page=self.ctx.new_page()
        self.page.route('**/*',lambda route:route.fulfill(body='<body></body>',content_type='text/html'))
        self.page.goto('https://fixture.invalid')
        self.page.evaluate("localStorage.setItem('last-lid',JSON.stringify('123456789@lid'))")
        root=Path(self.tmp.name);(root/'base').mkdir()
        self.library=WhatsAppFiles(FileLibrary(root/'base',root/'index.json'),root/'downloads',
                                  document_download=download_document_via_menu)
        self.r=Runtime(self.page,Mock(overviews={},forms={},unavailable_targets={}),['group'],[],Mock(),
                       root/'state',Mock(),self.library,True,mention_only_chats=['group'])
        self.r.open=Mock();self.r.send=Mock();self.r.submit=Mock();self.r.account_name='Agent'

    def doc(self,mid,*,tail='',caption='',sender='+12345678901',clock=None):
        clock=clock or datetime.now().strftime('%H:%M')
        return f'''<div data-id="{mid}" data-testid="conv-msg-{mid}"><div data-testid="msg-container">
          {('<span data-testid="tail-'+tail+'"></span>') if tail else ''}
          <div><span data-testid="author">Alice</span><span data-testid="author">{sender}</span></div>
          <button data-testid="document-thumb" title='Download "{mid}.py"'><span data-name>{mid}.py</span><span>PY 7 kB</span></button>
          {('<span data-testid="document-caption selectable-text" class="selectable-text">'+caption+'</span>') if caption else ''}
          <div data-testid="msg-meta">{clock}</div></div></div>'''

    def fixture(self,extra=None,text='Read the contents of these files'):
        caption='<span data-plain-text="@Agent" data-app-text-template="123456789@lid">@Agent</span> '+text
        self.page.set_content('<div id="main"><div data-tab="8"><div tabindex="-1">Today</div>'+self.doc('first',tail='in',caption=caption)+
                              (extra if extra is not None else self.doc('second')+self.doc('third'))+'</div></div>')
        self.page.evaluate('''()=>{
          window.downloaded=[];window.opened=[];
          for(const s of document.querySelectorAll('[data-testid="document-thumb"]')){
            s.onclick=()=>window.opened.push(s.textContent);
            s.oncontextmenu=e=>{e.preventDefault();const m=document.createElement('div');m.setAttribute('role','menu');
              const b=document.createElement('button');b.setAttribute('role','menuitem');b.textContent='Download';
              b.onclick=()=>{const name=s.querySelector('[data-name]').textContent;window.downloaded.push(name);m.remove();const a=document.createElement('a');
                a.href=URL.createObjectURL(new Blob(['Source for '+name]));a.download=name;a.click();};
              m.appendChild(b);document.body.appendChild(m);};
          }
          document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('[role="menu"]').forEach(m=>m.remove());});
        }''')

    def rows(self):return intake_records(self.page,'Agent')
    def anchor(self,rows):return request_record(next(r for r in rows if record_message_id(r)=='first'))

    def test_tail_less_siblings_are_identified_and_legacy_single_document_still_works(self):
        self.fixture()
        self.assertEqual(attachment_batches(self.page,['first','second','third']),
                         {'first':'first','second':'first','third':'first'})
        self.assertEqual([d[0]['name'] for d in descriptors(self.page,['first','second','third']).values()],
                         ['first.py','second.py','third.py'])

    def test_runtime_downloads_all_three_once_before_planning(self):
        for wording in ('Read the contents of these files','请阅读这些附件 and explain each file'):
            with self.subTest(wording=wording):
                # A fresh runtime is needed only for the second task's seen IDs.
                self.r.seen['group'].clear();self.r.message_revisions['group'].clear()
                self.fixture(text=wording);self.r.poll()
                self.assertEqual(len(self.r.queue),1)
                with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):self.r.handle_queue()
                state=self.r.flows['group'].state
                self.assertEqual(len(state['whatsapp_files']),3)
                self.assertEqual(len(state['attachment_source_manifest']),3)
                self.assertEqual(self.page.evaluate('window.downloaded'),['first.py','second.py','third.py'])
                self.assertEqual(self.page.evaluate('window.opened'),[])
                context=self.r.submit.call_args.args[1]
                self.assertEqual(len(context['request_attachments']),3)
                snapshot=self.library.snapshot()
                for entry in state['whatsapp_files']:
                    self.assertIn('Source for',str(self.library.inspect(snapshot,entry['id'])))
                state['phase']='DONE';self.r.flows['group'].save()

    def test_group_stops_at_other_sender_clock_caption_direction_or_new_tail(self):
        variants=[{'sender':'+19876543210'},{'clock':'00:00'},{'caption':'Another request'},
                  {'tail':'out'},{'tail':'in'}]
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                self.fixture(self.doc('boundary',**kwargs)+self.doc('third'))
                rows=self.rows();sources=batch_sources(self.anchor(rows),rows)
                self.assertEqual([r['attachment_message_id'] for r in sources],['first'])

    def test_shared_display_name_without_phone_is_not_identity(self):
        self.fixture(self.doc('second',sender='Alice'))
        self.assertNotIn('second',attachment_batches(self.page,['first','second']))
        self.assertEqual(descriptors(self.page,['second']),{})

    def test_day_separator_and_unrelated_plain_message_end_batch(self):
        for boundary in ('<div tabindex="-1">Yesterday</div>',
                         '<div data-id="chat"><div data-pre-plain-text="[12:35] Alice:">Other message</div></div>'):
            self.fixture(boundary+self.doc('second'))
            self.assertNotIn('second',attachment_batches(self.page,['first','second']))

    def test_unanchored_and_quoted_documents_never_supply_source(self):
        self.fixture('<blockquote>'+self.doc('quoted',tail='in')+'</blockquote>'+self.doc('second'))
        self.assertNotIn('quoted',descriptors(self.page,['quoted']))
        self.page.locator('[data-id="first"]').evaluate('(e)=>e.remove()')
        self.assertNotIn('second',descriptors(self.page,['second']))

    def test_duplicate_message_root_is_rejected(self):
        self.fixture(self.doc('second')+self.doc('second'))
        self.assertNotIn('second',descriptors(self.page,['second']))

    def test_late_mounted_sibling_is_collected_before_snapshot_closes(self):
        self.fixture(self.doc('second'));rows=self.rows();anchor=self.anchor(rows)
        self.page.evaluate('html=>setTimeout(()=>document.querySelector("[data-tab=\\"8\\"]").insertAdjacentHTML("beforeend",html),120)',self.doc('third'))
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.25):
            result=collect_batch(self.page,anchor,rows,self.rows)
        self.assertEqual([r['attachment_message_id'] for r in result],['first','second','third'])

    def test_changed_sibling_during_collection_fails_before_any_download(self):
        self.fixture();self.r.poll()
        self.page.evaluate('''()=>setTimeout(()=>document.querySelector('[data-id="second"] [data-testid="document-thumb"]').title='Download "replacement.py"',150)''')
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.3):self.r.handle_queue()
        self.assertEqual(self.r.flows['group'].state['phase'],'FAILED')
        self.assertEqual(self.page.evaluate('window.downloaded'),[])
        self.r.submit.assert_not_called()

    def test_source_changed_after_first_download_cannot_produce_success(self):
        self.fixture();self.r.poll()
        real_download=self.library.download
        def change_later(*args):
            real_download(*args)
            self.page.locator('[data-id="third"]').evaluate('(e)=>e.remove()')
        with patch.object(self.library,'download',side_effect=change_later),patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):
            self.r.handle_queue()
        self.assertEqual(self.r.flows['group'].state['phase'],'FAILED')
        self.r.submit.assert_not_called()

    def test_offscreen_sibling_is_included_and_downloaded(self):
        self.fixture()
        self.page.locator('[data-id="third"]').evaluate('(e)=>e.style.marginTop="1200px"')
        self.assertGreater(self.page.locator('[data-id="third"]').bounding_box()['y'],self.page.viewport_size['height'])
        self.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):self.r.handle_queue()
        self.assertEqual(len(self.r.flows['group'].state['whatsapp_files']),3)
        self.assertEqual(self.page.evaluate('window.downloaded'),['first.py','second.py','third.py'])
        self.r.submit.assert_called_once()

    def test_scroll_does_not_change_manifest_order_when_second_file_is_offscreen(self):
        self.fixture()
        self.page.locator('[data-id="second"]').evaluate('(e)=>{e.style.position="absolute";e.style.top="1800px";}')
        rows=self.rows();anchor=self.anchor(rows)
        first=batch_sources(anchor,rows)
        self.page.locator('[data-id="second"]').scroll_into_view_if_needed()
        second=batch_sources(anchor,self.rows())
        self.assertEqual(first,second)
        self.assertEqual([r['attachment_message_id'] for r in first],['first','second','third'])

    def test_new_sibling_during_download_prevents_partial_answer(self):
        self.fixture();self.r.poll()
        real_download=self.library.download
        def add_later(*args):
            real_download(*args)
            if not self.page.locator('[data-id="fourth"]').count():
                self.page.locator('[data-tab="8"]').evaluate('(e,html)=>e.insertAdjacentHTML("beforeend",html)',self.doc('fourth'))
        with patch.object(self.library,'download',side_effect=add_later),patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):
            self.r.handle_queue()
        self.assertEqual(self.r.flows['group'].state['phase'],'FAILED')
        self.assertIn('grew during download',self.r.flows['group'].state['reason'])
        self.r.submit.assert_not_called()

    def test_menu_closing_animation_finishes_before_next_download(self):
        self.fixture()
        self.page.evaluate('''()=>{
          const create=document.createElement.bind(document);
          document.createElement=(tag,...args)=>{const e=create(tag,...args),remove=e.remove.bind(e);
            e.remove=()=>e.getAttribute('role')==='menu'?setTimeout(remove,250):remove();return e;};
        }''')
        self.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):self.r.handle_queue()
        self.assertEqual(self.page.evaluate('window.downloaded'),['first.py','second.py','third.py'])
        self.r.submit.assert_called_once()

    def test_menu_hidden_in_place_does_not_block_next_file(self):
        self.fixture()
        self.page.evaluate('''()=>{
          const create=document.createElement.bind(document);
          document.createElement=(tag,...args)=>{const e=create(tag,...args),remove=e.remove.bind(e);
            e.remove=()=>e.getAttribute('role')==='menu'?setTimeout(()=>e.style.visibility='hidden',450):remove();return e;};
        }''')
        self.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):self.r.handle_queue()
        self.assertEqual(self.page.evaluate('window.downloaded'),['first.py','second.py','third.py'])
        self.r.submit.assert_called_once()


if __name__=='__main__':unittest.main()
