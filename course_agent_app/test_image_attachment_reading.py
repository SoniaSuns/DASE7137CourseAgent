"""Regressions for image captions being mistaken for requests without a file."""
import base64
from datetime import datetime
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from PIL import Image
from chrome_file_library import FileLibrary, FileLibraryChanged, sha
from chrome_whatsapp_attachments import WhatsAppFiles, descriptors, download_image
from run_shared_chrome_agent import Runtime, intake_records, request_record
from chrome_planner import CodexPlanner, compact_context, stage_visual_inputs


def picture():
    out=io.BytesIO()
    Image.new('RGB',(40,30),'blue').save(out,format='PNG')
    return out.getvalue()


class ImageIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.p.stop()

    def setUp(self):
        self.ctx=self.browser.new_context();self.addCleanup(self.ctx.close)
        self.page=self.ctx.new_page()
        self.page.route('**/*',lambda r:r.fulfill(body='<body></body>',content_type='text/html'))
        self.page.goto('https://fixture.invalid')
        self.page.evaluate("localStorage.setItem('last-lid',JSON.stringify('123456789@lid'))")

    def fixture(self,caption='Tell me the content of this file',tail='in',quoted=False,blob=True):
        self.page.set_content(f'''<div id="main"><div data-tab="8"><div tabindex="-1">Today</div>
          <div data-id="photo-message"><span data-testid="tail-{tail}"></span>
          <div data-pre-plain-text="[00:29, 18/10/2026] Alice: ">
          <span data-testid="author">Alice</span>
          {'<blockquote>' if quoted else ''}
          <div data-testid="image-thumb" style="width:100px;height:100px"><img id="full"></div>
          <span data-testid="image-caption selectable-text" class="selectable-text">
          <span data-mention-jid="123456789@lid">@CourseAgent</span> {caption}</span>
          {'</blockquote>' if quoted else ''}</div><div data-testid="msg-meta">00:29</div></div></div></div>''')
        self.page.evaluate('''async ({data,blob})=>{
          const bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));
          const img=document.querySelector('#full');
          img.src=blob?URL.createObjectURL(new Blob([bytes],{type:'image/png'})):'data:image/png;base64,'+data;
          await img.decode();
        }''',{'data':base64.b64encode(picture()).decode(),'blob':blob})

    def test_real_image_shape_carries_caption_mention_and_media_into_task(self):
        for caption in ('Tell me the content of this file','请解释这张图 / summarize this attachment'):
            with self.subTest(caption=caption):
                self.fixture(caption)
                row=next(r for r in intake_records(self.page,'CourseAgent') if r.get('message_id')=='photo-message')
                self.assertTrue(row['self_mentioned'])
                self.assertEqual(row['addressed_text'],caption)
                record=request_record(row)
                self.assertEqual(record['attachments'][0]['kind'],'image')
                self.assertEqual(record['attachment_message_id'],'photo-message')
                self.assertEqual(download_image(self.page,'photo-message',record['attachments'][0]),picture())

    def test_quote_and_outgoing_image_cannot_authorize_or_supply_media(self):
        for opts in ({'tail':'out'},{'quoted':True}):
            self.fixture(**opts)
            self.assertFalse(descriptors(self.page,['photo-message']).get('photo-message'))
            with self.assertRaises(Exception):download_image(self.page,'photo-message',{'index':0})

    def test_caption_without_timestamp_wrapper_is_discovered_and_dated(self):
        from run_shared_chrome_agent import record_time
        self.fixture()
        self.page.evaluate("document.querySelector('[data-pre-plain-text]').removeAttribute('data-pre-plain-text')")
        rows=intake_records(self.page,'CourseAgent')
        self.assertEqual(len(rows),1)
        self.assertTrue(rows[0]['self_mentioned'])
        self.assertEqual(rows[0]['addressed_text'],'Tell me the content of this file')
        self.assertEqual(rows[0]['attachments'][0]['kind'],'image')
        self.assertIsNotNone(record_time(rows[0],datetime.now().astimezone()))

    def test_thumbnail_only_and_remote_url_are_not_full_image_sources(self):
        self.fixture(blob=False)
        with self.assertRaisesRegex(Exception,'Full image'):download_image(self.page,'photo-message',{'index':0})
        self.page.evaluate("document.querySelector('#full').src='https://fixture.invalid/remote.png'")
        with self.assertRaisesRegex(Exception,'Full image'):download_image(self.page,'photo-message',{'index':0})

    def test_task_scoped_image_download_inspection_and_hash_validation(self):
        self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'base').mkdir()
            library=WhatsAppFiles(FileLibrary(root/'base',root/'index.json'),root/'downloads')
            state={'sender':'Alice','run':'run1','whatsapp_files':[]};library.bind('chat',state)
            docs=descriptors(self.page,['photo-message'])['photo-message']
            library.download(self.page,'photo-message',docs,state['whatsapp_files'].append)
            snapshot=library.snapshot();entry=library.files[0]
            evidence=library.inspect(snapshot,entry['id'])
            self.assertEqual(evidence['status'],'image')
            self.assertEqual(evidence['visual']['width'],40)
            self.assertEqual(entry['media_kind'],'image')
            browser=Mock();browser.overviews={};browser.forms={};browser.unavailable_targets={}
            runtime=Runtime(Mock(),browser,['chat'],[],Mock(),root/'state',Mock(),library,True)
            flow=runtime.flows['chat'];flow.begin('Read this image','Alice','photo-message')
            flow.state['run']='run1';flow.state['whatsapp_files']=state['whatsapp_files']
            runtime.file_snapshot=snapshot;runtime.file_evidence=[evidence]
            context=runtime.context(flow)
            self.assertEqual(context['request_attachments'][0]['file_id'],entry['id'])
            self.assertEqual(context['_image_inputs'][0]['data'],picture())
            self.assertEqual(context['visual_inputs'][0]['file_id'],entry['id'])
            runtime.library_planner_access=False
            self.assertEqual(runtime.context(flow)['_image_inputs'],[])
            library.bind('chat',{'sender':'Bob','run':'run2'})
            with self.assertRaisesRegex(ValueError,'current task'):library.read_bytes(snapshot,entry['id'])
            library.bind('chat',state)
            (root/'downloads'/entry['storage_key']).write_bytes(b'changed')
            with self.assertRaises(FileLibraryChanged):library.read_bytes(snapshot,entry['id'])


class ContentDetectionTests(unittest.TestCase):
    def inspect(self,name,data):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'files').mkdir();(root/'files'/name).write_bytes(data)
            library=FileLibrary(root/'files',root/'index.json',detect_content=True)
            snapshot=library.snapshot()
            return library.inspect(snapshot,snapshot['files'][0]['id'])

    def test_unlisted_extension_and_no_extension_read_unicode_text(self):
        for name in ('notes.unlisted','README','notes.bin'):
            for encoding in ('utf-8','utf-16','utf-32'):
                result=self.inspect(name,'课程资料\nGroup 2: presentation'.encode(encoding))
                self.assertEqual(result['status'],'read')
                self.assertIn('课程资料',result['text'])

    def test_renamed_office_and_image_use_actual_content(self):
        from docx import Document
        doc=Document();doc.add_paragraph('Evidence in renamed Office file')
        out=io.BytesIO();doc.save(out)
        self.assertIn('Evidence in renamed',self.inspect('source.unknown',out.getvalue())['text'])
        self.assertEqual(self.inspect('source.txt',picture())['status'],'image')

    def test_archive_reads_members_without_extracting_paths(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('../not-extracted.weird','Meeting notes: Group 2')
            z.writestr('binary.dat',b'\x00\xff\x01')
        result=self.inspect('package.dat',out.getvalue())
        self.assertIn('Group 2',result['text'])
        self.assertIn('further inspection required',result['text'])

    def test_binary_is_not_fabricated_as_text(self):
        self.assertEqual(self.inspect('unknown.foo',b'\x00\xff\x01')['status'],'reader_unavailable')


class VisualPlannerTests(unittest.TestCase):
    def context(self):
        data=picture()
        return {'request':'Explain this image','visual_inputs':[{'file_id':'wa:one','relative_path':'Image.image'}],
                '_image_inputs':[{'file_id':'wa:one','data':data,'sha256':sha(data)}]}

    def test_visual_bytes_are_staged_but_not_embedded_in_json_prompt_or_trace(self):
        context=self.context()
        self.assertNotIn('_image_inputs',compact_context(context))
        json.dumps(compact_context(context))
        from run_trace import RunTrace
        self.assertNotIn(base64.b64encode(picture()).decode(),json.dumps(RunTrace(Path('.')).clean(context)))
        with tempfile.TemporaryDirectory() as tmp:
            paths=stage_visual_inputs(context,Path(tmp))
            with Image.open(paths[0]) as im:self.assertEqual(im.getpixel((0,0)),(0,0,255,255))
            context['_image_inputs'][0]['data']=b'tampered'
            with self.assertRaisesRegex(ValueError,'changed'):stage_visual_inputs(context,Path(tmp))

    def test_transparent_background_does_not_erase_opaque_dark_content(self):
        im=Image.new('RGBA',(4,4),(0,0,0,0));im.putpixel((1,1),(0,0,0,255))
        out=io.BytesIO();im.save(out,format='PNG');data=out.getvalue()
        context=self.context();context['_image_inputs'][0].update(data=data,sha256=sha(data))
        with tempfile.TemporaryDirectory() as tmp:
            paths=stage_visual_inputs(context,Path(tmp))
            with Image.open(paths[0]) as staged:
                self.assertEqual(staged.getpixel((0,0)),(0,0,0,0))
                self.assertEqual(staged.getpixel((1,1)),(0,0,0,255))

    def test_cli_receives_staged_image_and_prompt_without_source_bytes(self):
        import subprocess
        context=self.context()
        with tempfile.TemporaryDirectory() as tmp:
            def run(command,**kwargs):
                self.assertIn('--image',command)
                self.assertTrue(Path(command[command.index('--image')+1]).is_file())
                self.assertNotIn('_image_inputs',kwargs['input'])
                Path(command[command.index('--output-last-message')+1]).write_text('{"decision":"answer"}')
                return subprocess.CompletedProcess(command,0,stdout='',stderr='')
            with patch('chrome_planner.subprocess.run',side_effect=run):
                self.assertEqual(CodexPlanner({'CODEX_BIN':__file__},Path(tmp))(context)['decision'],'answer')

    def runtime(self,**state):
        r=Runtime.__new__(Runtime)
        r.flows={'chat':SimpleNamespace(state={'phase':'PLANNING','run':'a'*12,'task':'Read this file',**state},save=Mock())}
        r.defer_uncertain_items=True;r.observer=Mock();r.send=Mock();r.submit=Mock();r.context=Mock(return_value={})
        return r

    def test_informational_missing_source_asks_in_current_task_without_defer(self):
        r=self.runtime()
        r.accept_decision('chat',{'decision':'question','task_kind':'information','message':'Which of the two attached images?'},{'rounds':0,'repairs':0})
        self.assertEqual(r.flows['chat'].state['phase'],'QUESTION')
        r.submit.assert_not_called()
        self.assertNotIn('new task',r.send.call_args.args[1])

    def test_information_answer_is_not_zero_operations_partial_report(self):
        r=self.runtime()
        r.accept_decision('chat',{'decision':'answer','task_kind':'information','message':'The image shows a blue square.',
                                 'impact_review':{'unresolved_dependencies':[]}},{'rounds':0,'repairs':0})
        self.assertEqual(r.flows['chat'].state['phase'],'DONE')
        self.assertNotIn('Verified operations',r.send.call_args.args[1])

    def test_information_classification_cannot_downgrade_existing_write_goal(self):
        r=self.runtime(goal_scope=[{'id':'move'}])
        r.accept_decision('chat',{'decision':'question','task_kind':'information','message':'Which week?'},{'rounds':0,'repairs':0})
        r.submit.assert_called_once()
        self.assertEqual(r.flows['chat'].state['phase'],'PLANNING')


if __name__=='__main__':unittest.main()
