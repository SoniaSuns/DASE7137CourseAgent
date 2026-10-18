"""Task-scoped WhatsApp document downloads; attachments are data, never code."""
from __future__ import annotations

import json
import base64
from pathlib import Path
import re
import secrets
import tempfile
import time
import unicodedata

from chrome_file_library import FileLibrary, FileLibraryChanged, READABLE, sha

MAX_FILE = 32 * 1024 * 1024
MAX_TASK = 128 * 1024 * 1024
MAX_FILES = 12
DOWNLOAD_SECONDS = 45
MENU_SECONDS = 3


def reject_open_warning(page):
    warning=page.locator('[data-testid="harmful-file-warning-modal"]:visible')
    if not warning.count():return
    # Cancel this exact prompt; never acknowledge Open File or disable a guard.
    cancel=warning.get_by_role('button',name=re.compile(r'^(Cancel|取消)$',re.I))
    if cancel.count()==1:cancel.click(timeout=2000)
    else:page.keyboard.press('Escape')
    raise ValueError('WhatsApp requested a safety confirmation to open this attachment. '
                     'The file was not opened or executed; automatic download stopped.')


def trigger_document_download(page,message_id,doc,marker):
    """Use Save/Download, not the file card's potentially unsafe Open action."""
    reject_open_warning(page)
    if page.locator('[role="menu"]:visible').count():
        raise ValueError('An existing WhatsApp menu is open; attachment download cannot be uniquely selected.')
    source=page.locator('[data-course-agent-download="'+marker+'"]')
    source.click(button='right',timeout=5000)
    deadline=time.monotonic()+MENU_SECONDS
    opened=False
    try:
        while time.monotonic()<deadline:
            reject_open_warning(page)
            visible_menus=page.locator('[role="menu"]:visible')
            opened=opened or bool(visible_menus.count())
            # WhatsApp also renders a sibling reaction toolbar with role=menu.
            menus=visible_menus.filter(has=page.locator('[role="menuitem"]:visible'))
            if menus.count()>1:
                raise ValueError('Multiple attachment action menus are visible; no file was opened.')
            if menus.count()==1:
                controls=menus.get_by_role('menuitem',name=re.compile(r'^(Download|下载|下載)$',re.I)).filter(visible=True)
                if controls.count()>1:
                    raise ValueError('The exact attachment menu has no unique Download action; no file was opened.')
                if controls.count()==1:
                    if doc not in descriptors(page,[message_id]).get(message_id,[]):
                        raise ValueError('WhatsApp attachment changed while its download menu was open.')
                    controls.click(timeout=3000)
                    return
            page.wait_for_timeout(50)
        raise ValueError('The exact attachment menu has no unique Download action; no file was opened.')
    finally:
        if opened and page.locator('[role="menu"]:visible').count():
            for _ in range(3):
                if not page.locator('[role="menu"]:visible').count():break
                page.keyboard.press('Escape')
                page.wait_for_timeout(100)
            # A menu's closing animation must finish before the next file.
            page.wait_for_function("() => ![...document.querySelectorAll('[role=menu]')].some(e=>e.getBoundingClientRect().width && e.getBoundingClientRect().height && getComputedStyle(e).visibility!=='hidden' && getComputedStyle(e).display!=='none')",timeout=2000)


def download_document(page, message_id, doc, storage):
    """Legacy card/viewer adapter, retained for existing callers only.

    The live listener uses download_document_via_menu, never this Open-capable
    adapter. Both adapters share transfer validation and resource cleanup.
    """
    return _download_document(page,message_id,doc,storage,menu_only=False)


def download_document_via_menu(page, message_id, doc, storage):
    """Save through an explicit Download action; never fall back to Open."""
    return _download_document(page,message_id,doc,storage,menu_only=True)


def _download_document(page, message_id, doc, storage, *, menu_only):
    """Bound Chrome download events even on no_defaults CDP connections.

    Scope Chrome's temporary download policy to this browser context, restore its
    normal browser policy afterwards, and accept only this page's main frame.
    """
    page_session = page.context.new_cdp_session(page)
    session = page.context.browser.new_browser_cdp_session()
    received, progress = [], {}
    context = {}
    staging = Path(tempfile.mkdtemp(prefix='transfer-', dir=storage))
    configured = False
    try:
        # Moodle inspections activate another tab. A successful pointer click on
        # a background WhatsApp tab does not prove its download was started.
        # Acquire the source tab before the one authorized download gesture.
        page.bring_to_front()
        info = page_session.send('Target.getTargetInfo')['targetInfo']
        # Target exposes an ID even for the persistent default profile, but
        # Browser download commands only accept IDs of non-default contexts.
        contexts = session.send('Target.getBrowserContexts')['browserContextIds']
        if info.get('browserContextId') in contexts: context['browserContextId'] = info['browserContextId']
        frame = page_session.send('Page.getFrameTree')['frameTree']['frame']['id']
        session.on('Browser.downloadWillBegin', lambda event: received.append(event) if event.get('frameId') == frame else None)
        session.on('Browser.downloadProgress', lambda event: progress.update({event['guid']: event}))
        session.send('Browser.setDownloadBehavior', {'behavior': 'allowAndName', 'downloadPath': str(staging),
                                                    'eventsEnabled': True, **context})
        configured = True
        marker=secrets.token_hex(12)
        if not page.evaluate(CLICK_DOCUMENT_SCRIPT, {'id': message_id, **doc, 'marker':marker}):
            raise ValueError('The exact incoming WhatsApp attachment is no longer visible.')
        if menu_only:trigger_document_download(page,message_id,doc,marker)
        else:page.locator('[data-course-agent-download="'+marker+'"]').click(timeout=5000)
        viewer_clicked = False
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        while time.monotonic() < deadline:
            page.wait_for_timeout(100)
            reject_open_warning(page)
            if len(received) > 1:
                raise ValueError('WhatsApp produced multiple downloads; no source was accepted.')
            if not received:
                if not menu_only and not viewer_clicked: viewer_clicked = page.evaluate(CLICK_VIEWER_DOWNLOAD, doc['name'])
                continue
            event = received[0]
            if unicodedata.normalize('NFC', event['suggestedFilename']) != safe_name(doc['name']):
                raise ValueError('Downloaded filename does not match the requested WhatsApp attachment.')
            status = progress.get(event['guid'], {})
            if max(status.get('receivedBytes', 0), status.get('totalBytes', 0)) > MAX_FILE:
                raise ValueError('WhatsApp attachment exceeds 32 MiB.')
            if status.get('state') == 'canceled':
                raise ValueError('WhatsApp download was cancelled; no source was accepted.')
            if status.get('state') == 'completed':
                if not re.fullmatch(r'[a-fA-F0-9-]{16,64}', event['guid']):
                    raise ValueError('Chrome supplied an invalid download identifier.')
                source = FileLibrary(staging).safe_path(event['guid'])
                if source.stat().st_size > MAX_FILE:
                    raise ValueError('WhatsApp attachment exceeds 32 MiB.')
                return source.read_bytes()
        if not received:
            raise ValueError('WhatsApp download timed out: the browser did not start the transfer. '
                             'The attachment was identified, but its contents have not been read.')
        raise ValueError('WhatsApp download timed out after the transfer started; no completed source was accepted.')
    finally:
        for event in received:
            if progress.get(event['guid'], {}).get('state') not in {'completed', 'canceled'}:
                try: session.send('Browser.cancelDownload', {'guid': event['guid'], **context})
                except Exception: pass
        try:
            if configured:
                session.send('Browser.setDownloadBehavior', {'behavior': 'default', 'eventsEnabled': False, **context})
        finally:
            for connection in (session, page_session):
                try: connection.detach()
                except Exception: pass
            # Never remove another tab's concurrent download from this directory.
            for event in received:
                if not re.fullmatch(r'[a-fA-F0-9-]{16,64}', event['guid']): continue
                for suffix in ('', '.crdownload'):
                    path = staging / (event['guid'] + suffix)
                    try: path.unlink(missing_ok=True)
                    except OSError: pass
            try: staging.rmdir()
            except OSError: pass
            from whatsapp_web import close_whatsapp_document_viewer
            close_whatsapp_document_viewer(page)

# Current WhatsApp omits tail-in on consecutive documents in one sender group.
# Resolve that group from exact DOM neighbours, never from filename similarity.
ATTACHMENT_PROVENANCE_JS = r"""
 const quote='[data-testid*="quoted"],blockquote';
 const ownNodes=(root,selector)=>[...root.querySelectorAll(selector)].filter(e=>!e.closest(quote));
 const outgoing=root=>root.closest('.message-out') || ownNodes(root,
   '[data-testid="tail-out"],[data-icon="tail-out"],[data-icon="msg-check"],[data-icon="msg-dblcheck"],[data-testid="msg-check"],[data-testid="msg-dblcheck"]').length;
 const incoming=root=>!outgoing(root) && (root.closest('.message-in') || ownNodes(root,
   '.message-in,[data-testid="tail-in"],[data-icon="tail-in"]').length);
 const sourceRoots=[...document.querySelectorAll('[data-id]')].filter(e=>!e.closest(quote));
 const unique=root=>sourceRoots.filter(e=>e.getAttribute('data-id')===root.getAttribute('data-id')).length===1;
 const identity=root=>{
   const authors=ownNodes(root,'[data-testid="author"]');
   // A phone/JID is identity evidence; a shared display name is insufficient.
   const ids=authors.map(e=>(e.textContent||'').trim()).filter(s=>/^\+?[\d\s().-]{7,}$/.test(s));
   return ids.length===1 ? ids[0].replace(/[^\d+]/g,'') : '';
 };
 const clock=root=>{const c=ownNodes(root,'[data-testid="msg-meta"]');return c.length===1?c[0].textContent.trim():'';};
 const day=root=>{
   const timeline=root.closest('[data-tab="8"]');
   for(let b=root;timeline && b && b!==timeline;b=b.parentElement){
     for(let s=b.previousElementSibling;s;s=s.previousElementSibling){
       if(s.getAttribute('tabindex')==='-1' && !s.matches('[data-id],[role="row"]') &&
          !s.querySelector('[data-id],[data-pre-plain-text],[data-testid="document-thumb"]') &&
          /^(今天|昨天|昨日|Today|Yesterday|\d{4}年\d{1,2}月\d{1,2}日|\d+[/-]\d+[/-]\d+)$/i.test(s.textContent.trim()))return s.textContent.trim();
     }
   }return '';
 };
 const documentSource=root=>ownNodes(root,'[data-testid="document-thumb"]').length>0;
 const hasCaption=root=>ownNodes(root,'[data-testid~="document-caption"],[data-pre-plain-text]').some(e=>e.textContent.trim());
 const anchors=new Map();
 for(let i=0;i<sourceRoots.length;i++){
   const root=sourceRoots[i];if(!unique(root) || outgoing(root) || !documentSource(root))continue;
   if(incoming(root)){if(identity(root) && clock(root))anchors.set(root,root);continue;}
   const prev=sourceRoots[i-1];
   if(prev && anchors.has(prev) && !hasCaption(root) && identity(root) &&
      identity(root)===identity(prev) && clock(root) && clock(root)===clock(prev) && day(root)===day(prev))
     anchors.set(root,anchors.get(prev));
 }
 const accepted=root=>unique(root) && !outgoing(root) && (incoming(root) || anchors.has(root));
"""

ATTACHMENT_BATCHES_SCRIPT = 'ids => {' + ATTACHMENT_PROVENANCE_JS + r"""
 return Object.fromEntries(sourceRoots.filter(r=>ids.includes(r.getAttribute('data-id')) && anchors.has(r))
   .map(r=>[r.getAttribute('data-id'),anchors.get(r).getAttribute('data-id')]));
}"""


def attachment_batches(page, ids):
    return page.evaluate(ATTACHMENT_BATCHES_SCRIPT, ids)


ATTACHMENT_RECORDS_SCRIPT = '() => {' + ATTACHMENT_PROVENANCE_JS + r"""
 return sourceRoots.filter(r=>anchors.has(r) && r.getBoundingClientRect().width).map(r=>{
   const authors=ownNodes(r,'[data-testid="author"]');
   return {message_id:r.getAttribute('data-id'),direction:'incoming',
     sender_id:authors.map(e=>e.textContent.trim()).find(s=>/^\+?[\d\s().-]{7,}$/.test(s)),
     sender_display_name:authors[0].textContent.trim(),attachment_batch_id:anchors.get(r).getAttribute('data-id'),
     attachment_batch_position:sourceRoots.indexOf(r)};
 });
}"""


def attachment_records(page):
    """Include mounted group documents even when below the current viewport."""
    return page.evaluate(ATTACHMENT_RECORDS_SCRIPT)


# Use exact message IDs, never global document indexes or quoted thumbnails.
DOCUMENTS_SCRIPT = 'ids => {' + ATTACHMENT_PROVENANCE_JS + r"""
 const result = {};
 for (const id of ids) {
   const roots = [...document.querySelectorAll('[data-id]')].filter(e => e.getAttribute('data-id') === id);
   if (roots.length !== 1) continue;
   const root = roots[0];
   const unquoted = selector => [...root.querySelectorAll(selector)].some(e=>!e.closest('[data-testid*="quoted"],blockquote'));
   if (!accepted(root)) continue;
   const docs = [...root.querySelectorAll('[data-testid="document-thumb"]')].filter(e =>
     !e.closest('[data-testid*="quoted"],blockquote') && e.getBoundingClientRect().width);
   result[id] = docs.map((e, index) => {
     const title = e.getAttribute('title') || '';
     const quoted = title.match(/[“"]([^”"]+)[”"]\s*$/);
     const leaf = [...e.querySelectorAll('[title],span,div')].find(n =>
       !n.children.length && /^.+\.[a-z0-9]{1,12}$/i.test((n.getAttribute('title') || n.textContent || '').trim()));
     const name = quoted ? quoted[1] : leaf ? (leaf.getAttribute('title') || leaf.textContent).trim() : '';
     return {index, name};
   }).filter(e => e.name);
   const images = [...root.querySelectorAll('[data-testid="image-thumb"]')].filter(e =>
     !e.closest('[data-testid*="quoted"],blockquote') && e.getBoundingClientRect().width);
   result[id].push(...images.map((e,index)=>({index,name:'Image-'+(index+1)+'.image',kind:'image'})));
 }
 return result;
}"""

CLICK_DOCUMENT_SCRIPT = '({id,index,name,marker}) => {' + ATTACHMENT_PROVENANCE_JS + r"""
 const roots = [...document.querySelectorAll('[data-id]')].filter(e => e.getAttribute('data-id') === id);
 if (roots.length !== 1) return false;
 const root=roots[0];
 const unquoted = selector => [...root.querySelectorAll(selector)].some(e=>!e.closest('[data-testid*="quoted"],blockquote'));
 if (!accepted(root)) return false;
 const docs=[...root.querySelectorAll('[data-testid="document-thumb"]')].filter(e =>
   !e.closest('[data-testid*="quoted"],blockquote') && e.getBoundingClientRect().width);
 const e=docs[index]; if (!e) return false;
 e.setAttribute('data-course-agent-download',marker); return true;
}"""

# A PDF thumbnail can open WhatsApp's viewer. Only use a uniquely labelled
# download control outside message bubbles and only when its filename is visible.
CLICK_VIEWER_DOWNLOAD = r"""name => {
 const visible=e=>e.getBoundingClientRect().width && e.getBoundingClientRect().height;
 const labels=[...document.querySelectorAll('span,div')].filter(e=>
   !e.children.length && !e.closest('[data-id]') && visible(e) && (e.textContent||'').includes(name));
 if (!labels.length) return false;
 const controls=[...document.querySelectorAll('button,[role="button"],a[download]')].filter(e=>{
   if (e.closest('[data-id]') || !visible(e)) return false;
   const label=(e.getAttribute('aria-label')||e.getAttribute('title')||'').trim();
   return /^(download|下载|下載)(\b|\s|$)/i.test(label) || e.hasAttribute('download');
 });
 if (controls.length!==1) return false;
 controls[0].click(); return true;
}"""


def descriptors(page, ids):
    return page.evaluate(DOCUMENTS_SCRIPT, ids)


# Only the loaded media blob of the exact incoming message is a source. The
# small data: preview is deliberately excluded, as are arbitrary remote URLs.
IMAGE_BYTES_SCRIPT = r"""async ({id,index,limit}) => {
 const roots=[...document.querySelectorAll('[data-id]')].filter(e=>e.getAttribute('data-id')===id);
 if (roots.length!==1) throw Error('Image message is unavailable');
 const root=roots[0], quote='[data-testid*="quoted"],blockquote';
 const has=s=>[...root.querySelectorAll(s)].some(e=>!e.closest(quote));
 if (root.closest('.message-out') || has('[data-testid="tail-out"],[data-icon="tail-out"]') ||
     !(root.closest('.message-in') || has('.message-in,[data-testid="tail-in"],[data-icon="tail-in"]')))
   throw Error('Image is not an incoming source');
 const images=[...root.querySelectorAll('[data-testid="image-thumb"]')].filter(e=>!e.closest(quote) && e.getBoundingClientRect().width);
 const thumb=images[index]; if (!thumb) throw Error('Image is unavailable');
 const sources=[...thumb.querySelectorAll('img')].filter(e=>!e.closest(quote) && e.complete && e.naturalWidth &&
   e.src.startsWith('blob:'+location.origin+'/'));
 const urls=[...new Set(sources.map(e=>e.src))];
 if (urls.length!==1) throw Error('Full image has not loaded; thumbnail is not a source');
 const response=await fetch(urls[0],{signal:AbortSignal.timeout(10000)});
 const blob=await response.blob();
 if (!blob.size || blob.size>limit) throw Error('Image exceeds the attachment size limit');
 const data=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(blob);});
 if (!root.isConnected || !thumb.isConnected || !sources.some(e=>e.src===urls[0])) throw Error('Image changed while reading');
 return data.split(',')[1];
}"""


def download_image(page, message_id, doc):
    data=base64.b64decode(page.evaluate(IMAGE_BYTES_SCRIPT, {'id':message_id,'index':doc['index'],'limit':MAX_FILE}),validate=True)
    if not data or len(data)>MAX_FILE:raise ValueError('WhatsApp image exceeds 32 MiB.')
    from chrome_file_library import image_metadata
    if not image_metadata(data):raise ValueError('WhatsApp media bytes are not a readable image.')
    return data


def fingerprint(record):
    """Exclude changing presentation labels (Download/View), include provenance."""
    return sha(json.dumps({
        'id': record.get('dom_id') or record.get('id') or record.get('message_id'),
        'sender': record.get('sender_id') or record.get('sender_display_name'),
        'direction': record.get('direction'),
        'text': record.get('message_text', ''),
        'mentioned': record.get('self_mentioned') is True,
        'documents': record.get('attachments', []),
    }, sort_keys=True, ensure_ascii=False).encode())


def safe_name(name):
    name = unicodedata.normalize('NFC', name)
    if (not name or len(name) > 220 or name in {'.', '..'} or
            re.search(r'[\x00-\x1f<>:"/\\|?*]', name) or name.rstrip(' .') != name or
            re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I)):
        raise ValueError('WhatsApp attachment has an unsafe filename.')
    return name


class WhatsAppFiles(FileLibrary):
    """Merge only the active run's downloads with the owner's normal library."""
    def __init__(self, base, storage, *, document_download=None):
        super().__init__(base.root, base.index, detect_content=True)
        self.base = base
        self.storage = FileLibrary(storage, Path(storage) / 'unused-index.json')
        self.storage.root.mkdir(parents=True, exist_ok=True)
        self.scope = None
        self.files = []
        # Explicitly inject the menu-only adapter in the live listener. The
        # default preserves the legacy library API for existing callers.
        self.document_download = document_download or download_document

    def bind(self, chat, state):
        scope = [chat, state.get('sender'), state.get('run')]
        files = state.get('whatsapp_files', [])
        if any(e.get('owner') != scope for e in files):
            raise ValueError('WhatsApp attachment belongs to another task.')
        changed = self.scope != scope or self.files != files
        self.scope = scope
        self.files = [dict(e) for e in files]
        return changed

    def snapshot(self):
        result = self.base.snapshot()
        result['files'] = result['files'] + [dict(e) for e in self.files]
        result['snapshot_id'] = sha(json.dumps(result['files'], sort_keys=True).encode())
        return result

    def entry(self, snapshot, file_id):
        entry = super().entry(snapshot, file_id)
        if file_id.startswith('wa:') and not any(e == entry and e['owner'] == self.scope for e in self.files):
            raise ValueError('WhatsApp source is not bound to the current task.')
        return entry

    def read_bytes(self, snapshot, file_id):
        entry = self.entry(snapshot, file_id)
        if not file_id.startswith('wa:'):
            return self.base.read_bytes(snapshot, file_id)
        if entry.get('archive_parent_id'):
            from chrome_archive_sources import read_member
            return entry, read_member(self, snapshot, entry)
        path = self.storage.safe_path(entry['storage_key'])
        if path.stat().st_size > MAX_FILE:
            raise ValueError('WhatsApp attachment exceeds 32 MiB.')
        data = path.read_bytes()
        if sha(data) != entry['sha256']:
            raise FileLibraryChanged(entry, sha(data))
        return entry, data

    def download(self, page, message_id, documents, save_entry):
        if not self.scope or not all(self.scope):
            raise ValueError('WhatsApp download requires a current requester and task.')
        for doc in documents:
            name = safe_name(doc['name'])
            key = sha(json.dumps([self.scope, message_id, doc], ensure_ascii=False).encode())
            file_id = 'wa:' + key
            if any(e['id'] == file_id for e in self.files):
                self.read_bytes(self.snapshot(), file_id)
                continue
            if sum(not e.get('archive_parent_id') for e in self.files) >= MAX_FILES:
                raise ValueError('This task already has 12 WhatsApp attachments.')
            observed = descriptors(page, [message_id]).get(message_id, [])
            if observed != documents:
                raise ValueError('WhatsApp attachment changed before download; send the file again with your request.')
            data = (download_image(page,message_id,doc) if doc.get('kind')=='image' else
                    self.document_download(page, message_id, doc, self.storage.root))
            if sum(e['bytes'] for e in self.files if not e.get('archive_parent_id')) + len(data) > MAX_TASK:
                raise ValueError('WhatsApp attachments exceed this task\'s 128 MiB limit.')
            relative = key + Path(name).suffix.lower()
            target = self.storage.root / relative
            # Exclusive creation prevents replacing any existing file or following a link.
            try:
                with target.open('xb') as stream: stream.write(data)
            except FileExistsError:
                if self.storage.safe_path(relative).read_bytes() != data:
                    raise ValueError('Stored WhatsApp attachment differs from this download.')
            entry = {'id': file_id, 'relative_path': 'WhatsApp/' + key[:8] + '/' + name,
                     'format': Path(name).suffix.lower(), 'bytes': len(data), 'sha256': sha(data),
                     'text_reader': Path(name).suffix.lower() in READABLE, 'storage_key': relative,
                     'owner': list(self.scope), 'message_id': message_id}
            if doc.get('kind'):entry['media_kind']=doc['kind']
            save_entry(entry)
            self.files.append(entry)
