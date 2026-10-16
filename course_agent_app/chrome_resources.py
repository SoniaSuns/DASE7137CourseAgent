"""Publish exact indexed bytes through Moodle's resource file picker.

Replacement retains a single-file activity and its filename. Original library
files are never edited; approved source hashes are checked again before upload.
"""
import copy
import hashlib
from pathlib import Path
from urllib.parse import urlparse
from chrome_course_scope import course
from chrome_structure import layout, expected_layout


class ResourceFiles:
    def __init__(self, browser, library):
        self.browser, self.library = browser, library

    def source(self, op):
        if not self.library:raise ValueError('The configured source library is unavailable.')
        entry, data = self.library.read_bytes(op['source_snapshot'], op['params']['file_id'])
        return entry, data

    def content_hash(self, target):
        b = self.browser; selected = course(target['course_id'])
        b.goto(selected,selected.url_for('/mod/resource/view.php',{'id':target['id'],'forceview':1}))
        urls = b.page.locator('a[href],object[data],iframe[src],embed[src],frame[src]').evaluate_all(
            "els=>els.map(e=>e.href||e.data||e.src).filter(u=>u&&u.includes('/mod_resource/content/'))")
        urls = list(dict.fromkeys(u.split('?')[0] for u in urls))
        if len(urls)!=1:raise ValueError('The resource content could not be uniquely identified for byte verification.')
        selected.check_origin(urls[0])
        if not urlparse(urls[0]).path.startswith('/pluginfile.php/'):
            raise ValueError('Resource content is not an observed Moodle file URL.')
        # Fetch inside the connected Chrome session; do not create a second HTTP login.
        return b.page.evaluate("""async url=>{
          const limit=128*1024*1024, controller=new AbortController();
          const timer=setTimeout(()=>controller.abort(),60000);
          try {
            const response=await fetch(url,{credentials:'same-origin',redirect:'error',cache:'no-store',signal:controller.signal});
            if(response.status!==200||Number(response.headers.get('content-length'))>limit)throw Error('File readback unavailable or too large.');
            const reader=response.body.getReader(), chunks=[];let size=0;
            while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;
              if(size>limit){await reader.cancel();throw Error('File readback size limit.');}chunks.push(value);}
            const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
            const hash=await crypto.subtle.digest('SHA-256',bytes);
            return [...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join('');
          } finally {clearTimeout(timer);}
        }""",urls[0])

    def resolve(self, op, snapshot, evidence):
        b=self.browser; target=b.targets.get(op.get('target'),{}); p=op.get('params',{})
        creation=op['capability']=='upload_file_resource'
        if creation:
            if target.get('kind')!='new_resource' or target['section_key']!=p.get('target_section'):
                raise ValueError('Upload requires the exact observed new-resource target for the selected section.')
        elif target.get('kind')!='module' or target.get('module_type')!='resource':
            raise ValueError('Replacement requires an existing file resource activity.')
        if op['target'] not in b.forms:raise ValueError('Inspect the exact resource form first.')
        if not self.library or not snapshot:raise ValueError('No current library index is available.')
        if not any(e['file_id']==p.get('file_id') for e in evidence):
            raise ValueError('Inspect the selected indexed file before proposing its upload.')
        entry,_=self.library.read_bytes(snapshot,p.get('file_id'))
        form=copy.deepcopy(b.forms[op['target']])
        names=form.get('resource_files')
        if names is None or (creation and names) or (not creation and len(names)!=1):
            raise ValueError('This resource workflow requires an empty new resource or a single-file replacement; multi-file packages need a separate plan.')
        if creation and not any(f['key']=='id_name' and str(f['value']).strip() for f in op.get('fields',[])):
            raise ValueError('A new file resource needs an approved name.')
        settings=b.resolve({**op,'capability':'update_moodle_form'}) if op.get('fields') else {
            **op,'fields':[],'course_id':target['course_id'],'title':target['title'],
            'fingerprint':form['fingerprint'],'target_kind':target['kind'],'date_fields':[]}
        before_hash=None if creation else self.content_hash(target)
        return {**settings,'capability':op['capability'],'settings_snapshot':form,
            'source_snapshot':{'root':snapshot['root'],'files':[copy.deepcopy(entry)]},
            'source_sha256':entry['sha256'],'source_name':entry['relative_path'],
            'upload_filename':Path(entry['relative_path']).name if creation else names[0],
            'original_content_sha256':before_hash,'layout_snapshot':layout(b.overviews[target['course_id']]),
            'resource_section':target.get('section_key'),
            'file_review':{'source':entry['relative_path'],'filename':Path(entry['relative_path']).name if creation else names[0],
                           'effect':'create file resource' if creation else 'replace existing file contents'}}

    def preflight(self, op, completed=()):
        b=self.browser;self.source(op)
        if layout(b.read_layout(op['course_id']))!=expected_layout(op,completed):
            raise ValueError('Course layout changed outside the approved operations; inspect before uploading.')
        if op['capability']=='replace_file_resource':
            if self.content_hash(b.targets[op['target']])!=op['original_content_sha256']:
                raise ValueError('Existing file content changed after approval; replacement was not attempted.')
        b.preflight(op)

    def upload_draft(self, op, data):
        page=self.browser.page
        manager=page.locator('#fitem_id_files .filemanager.fm-loaded')
        if manager.count()!=1:raise ValueError('The resource file manager is not unique.')
        add=manager.locator('.fp-btn-add a, .fp-btn-add button')
        if add.count()!=1:raise ValueError('The resource file picker Add control is not unique.')
        add.click()
        dialog=page.locator('.moodle-dialogue-focused:visible')
        repo=dialog.get_by_text('Upload a file',exact=True)
        if repo.count()==1:repo.click()
        file_input=dialog.locator('input[type=file][name=repo_upload_file]')
        file_input.wait_for(state='attached',timeout=15000)
        if file_input.count()!=1:raise ValueError('The selected upload repository is not unique.')
        file_input.set_input_files({'name':op['upload_filename'],'mimeType':'application/octet-stream','buffer':data})
        title=dialog.locator('input[name=title]')
        if title.count()==1:title.fill(op['upload_filename'])
        dialog.locator('.fp-upload-btn').click()
        if op['capability']=='replace_file_resource':
            overwrite=page.locator('.fp-dlg-butoverwrite:visible')
            overwrite.wait_for(state='visible',timeout=60000)
            if overwrite.count()!=1:raise ValueError('The replacement confirmation is not unique.')
            overwrite.click()
        page.wait_for_function("""name=>{
          const fm=document.querySelector('#fitem_id_files .filemanager.fm-loaded');
          const names=fm?[...fm.querySelectorAll('.fp-file .fp-filename')].map(e=>e.textContent.trim()):[];
          return names.length===1&&names[0]===name&&![...document.querySelectorAll('.filepicker .fp-upload-btn')].some(e=>e.getClientRects().length);
        }""",arg=op['upload_filename'],timeout=60000)

    def execute(self, op, before_save, completed=()):
        b=self.browser;self.preflight(op,completed)
        _,data=self.source(op)
        # Prepare settings before any draft write. An empty patch preserves settings.
        button=b.prepare(op)
        before_save()
        self.upload_draft(op,data)
        b.submit_and_wait(op,button,lambda:None)
        if b.form_errors():raise RuntimeError('Moodle rejected the resource form; checking saved content before continuing.')
        return self.verify(op,completed)

    def verify(self, op, completed=()):
        b=self.browser;b.check_submission_settled(op)
        current=b.read_layout(op['course_id']); expected=expected_layout(op,completed)
        target=op['target']; creation=op['capability']=='upload_file_resource'
        if creation:
            old={k for keys in expected.values() for k in keys}
            candidates=[m['key'] for m in current['modules'] if m['key'] not in old and m['module_type']=='resource'
                        and m['section_key']==op['params']['target_section']]
            if len(candidates)!=1:raise RuntimeError('The new resource cannot be uniquely resolved from fresh inventory.')
            target=candidates[0];expected[op['params']['target_section']].append(target)
        if layout(current)!=expected:raise RuntimeError('Resource placement/order differs from the approved result.')
        if self.content_hash(b.targets[target])!=op['source_sha256']:
            raise RuntimeError('Saved file bytes do not match the approved source; no upload is replayed.')
        form=b.inspect(target);values={f['key']:f['value'] for f in form['fields']}
        if any(values.get(p['key'])!=p['value'] for p in op['fields']):
            raise RuntimeError('Resource settings do not match the approved values.')
        if form.get('resource_files')!=[op['upload_filename']]:raise RuntimeError('Saved file names differ from approval.')
        if not creation:
            wanted={f['key']:f['value'] for f in op['settings_snapshot']['fields']}
            wanted.update({p['key']:p['value'] for p in op['fields']})
            # Full stable-form comparison also handles unused optional date controls.
            from chrome_moodle import form_fingerprint
            original=copy.deepcopy(op['settings_snapshot'])
            for f in original['fields']:f['value']=wanted[f['key']]
            original['title']=form['title']
            if form_fingerprint(original)!=form['fingerprint']:
                raise RuntimeError('Replacement changed settings outside the approved fields.')
        return {'status':'Done','target':op['title'],'verified':True,'change':op['summary'],
                'course_id':op['course_id'],**({'created_target':target} if creation else {})}
