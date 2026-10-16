"""Local file-picker/upload fixtures with real byte readback and approval."""
import json
import tempfile
import unittest
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from unittest.mock import Mock
from playwright.sync_api import sync_playwright
from chrome_moodle import MoodleBrowser
from chrome_resources import ResourceFiles
from chrome_file_library import FileLibrary
from chrome_approval import ChromeApproval


class ResourceFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name);sources=root/'files';sources.mkdir();(sources/'revised.txt').write_bytes(b'Revised source bytes')
        self.library=FileLibrary(sources,root/'index.json');self.snapshot=self.library.snapshot()
        self.fileid=self.snapshot['files'][0]['id'];self.evidence=[self.library.inspect(self.snapshot,self.fileid)]
        self.pw=sync_playwright().start();self.addCleanup(self.pw.stop)
        self.chrome=self.pw.chromium.launch(headless=True);self.addCleanup(self.chrome.close)
        self.context=self.chrome.new_context();self.addCleanup(self.context.close);self.page=self.context.new_page()
        self.items={'1':{'name':'Lecture','filename':'original.txt','data':b'Original bytes','visible':'0'}}
        self.draft=None;self.uploads=0;self.saves=0;self.reject=False
        self.page.route('**/*',self.route);self.browser=MoodleBrowser(self.page)
        self.resources=ResourceFiles(self.browser,self.library);self.browser.overview('140148')

    def route(self,route):
        req=route.request;p=urlparse(req.url);q=parse_qs(p.query);body='<body class="course-140148">'
        if p.path=='/repository/upload':
            self.uploads+=1
            raw=b'Content-Type: '+req.headers['content-type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+req.post_data_buffer
            parts=list(BytesParser(policy=default).parsebytes(raw).iter_parts());part=next(x for x in parts if x.get_filename())
            self.draft={'filename':part.get_filename(),'data':part.get_payload(decode=True)}
            route.fulfill(content_type='application/json',body='{}');return
        if p.path.startswith('/pluginfile.php/'):
            mid=p.path.split('/')[2];route.fulfill(content_type='application/octet-stream',body=self.items[mid]['data']);return
        if p.path=='/mod/resource/view.php':
            mid=q['id'][0];body+=f'<div class="resourceworkaround"><a href="/pluginfile.php/{mid}/mod_resource/content/1/{self.items[mid]["filename"]}">Download</a></div>'
        elif p.path=='/course/view.php':
            body+='<li class="section" id="section-0" data-id="101" data-number="0"><h3 class="sectionname">Week 5</h3>'
            for mid,item in self.items.items():body+=f'<div id="module-{mid}" class="activity modtype_resource"><span class="instancename">{item["name"]}</span></div>'
            body+='</li>'
        elif p.path=='/group/index.php':body+='<select id="groups"></select>'
        elif p.path=='/course/modedit.php':
            mid=q.get('update',[''])[0];item=self.items.get(mid,{'name':'','filename':'','data':b'','visible':'1'})
            if req.method=='POST':
                self.saves+=1
                if not self.reject:
                    values=parse_qs(req.post_data,keep_blank_values=True)
                    if not mid:mid=str(len(self.items)+1)
                    item={'name':values['name'][0],'visible':values['visible'][0],**self.draft};self.items[mid]=item
            self.draft=None
            body+=f'''<form class="mform" method="post"><input type="hidden" name="course" value="140148"><input type="hidden" name="update" value="{mid}">
              <input id="id_name" name="name" value="{item['name']}"><select id="id_visible" name="visible"><option value="0" {'selected' if item['visible']=='0' else ''}>Hidden</option><option value="1" {'selected' if item['visible']=='1' else ''}>Visible</option></select>
              <div id="fitem_id_files"><div class="filemanager fm-loaded"><div class="fp-btn-add"><button type="button" onclick="document.querySelector('#picker').style.display='block'">Add</button></div><div id="names">'''
            if item['filename']:body+=f'<div class="fp-file"><span class="fp-filename">{item["filename"]}</span></div>'
            body+='''</div></div></div><button type="submit" name="submitbutton" id="save">Save</button></form>
              <div id="picker" class="moodle-dialogue-focused filepicker" style="display:none"><input type="file" name="repo_upload_file"><input name="title"><button class="fp-upload-btn" onclick="upload()">Upload</button></div>
              <button class="fp-dlg-butoverwrite" style="display:none" onclick="finish()">Overwrite</button>
              <script>async function upload(){const file=document.querySelector('input[type=file]').files[0];const data=new FormData();data.append('repo_upload_file',file);await fetch('/repository/upload',{method:'POST',body:data});
              if(document.querySelector('.fp-filename'))document.querySelector('.fp-dlg-butoverwrite').style.display='block';else finish();}
              function finish(){document.querySelector('#names').innerHTML='<div class="fp-file"><span class="fp-filename">'+document.querySelector('input[name=title]').value+'</span></div>';document.querySelector('#picker').style.display='none';document.querySelector('.fp-dlg-butoverwrite').style.display='none';}</script>'''
        route.fulfill(content_type='text/html',body=body)

    def op(self,creation=False):
        target='140148:new_resource:101' if creation else '140148:module:1';self.browser.inspect(target)
        proposal={'id':'file','capability':'upload_file_resource' if creation else 'replace_file_resource','target':target,
            'params':{'file_id':self.fileid,'target_section':'140148:section:101' if creation else ''},
            'summary':'Publish revised file','scope':'requested','depends_on':[],
            'fields':[{'key':'id_name','value':'New lecture'}] if creation else []}
        return self.resources.resolve(proposal,self.snapshot,self.evidence)

    def test_replace_preserves_identity_name_visibility_and_checks_bytes(self):
        op=self.op();guard=Mock();result=self.resources.execute(op,guard)
        self.assertTrue(result['verified']);guard.assert_called_once()
        self.assertEqual(self.items['1'],{'name':'Lecture','filename':'original.txt','data':b'Revised source bytes','visible':'0'})
        self.assertEqual((self.uploads,self.saves),(1,1))

    def test_upload_resolves_exact_new_id_and_bytes(self):
        op=self.op(True);result=self.resources.execute(op,Mock())
        self.assertEqual(result['created_target'],'140148:module:2');self.assertEqual(self.items['2']['data'],b'Revised source bytes')
        self.assertEqual(self.items['1']['data'],b'Original bytes');self.assertEqual((self.uploads,self.saves),(1,1))

    def test_no_approval_no_draft_upload_or_save(self):
        op=self.op();flow=ChromeApproval(Path(self.temp.name)/'state.json','fixture');flow.begin('Replace','staff','one');flow.offer({},[op])
        with self.assertRaises(ValueError):self.resources.execute(op,lambda:flow.before_save(op))
        self.assertEqual((self.uploads,self.saves),(0,0))

    def test_source_changes_after_approval_prevent_upload(self):
        op=self.op();self.library.safe_path('revised.txt').write_bytes(b'Unapproved edits')
        with self.assertRaisesRegex(ValueError,'changed'):self.resources.execute(op,Mock())
        self.assertEqual((self.uploads,self.saves),(0,0))

    def test_remote_content_changes_after_approval_prevent_replacement(self):
        op=self.op();self.items['1']['data']=b'External edit'
        with self.assertRaisesRegex(ValueError,'content changed'):self.resources.execute(op,Mock())
        self.assertEqual((self.uploads,self.saves),(0,0))

    def test_rejected_save_cannot_claim_success_from_filename(self):
        op=self.op();self.reject=True
        with self.assertRaisesRegex(RuntimeError,'bytes'):self.resources.execute(op,Mock())
        self.assertEqual((self.uploads,self.saves),(1,1));self.assertEqual(self.items['1']['data'],b'Original bytes')


if __name__=='__main__':unittest.main()
