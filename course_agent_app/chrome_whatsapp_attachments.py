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


def download_document(page, message_id, doc, storage):
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
        page.locator('[data-course-agent-download="'+marker+'"]').click(timeout=5000)
        viewer_clicked = False
        deadline = time.monotonic() + DOWNLOAD_SECONDS
        while time.monotonic() < deadline:
            page.wait_for_timeout(100)
            if len(received) > 1:
                raise ValueError('WhatsApp produced multiple downloads; no source was accepted.')
            if not received:
                if not viewer_clicked: viewer_clicked = page.evaluate(CLICK_VIEWER_DOWNLOAD, doc['name'])
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
        raise ValueError('WhatsApp download timed out; no source was accepted. Send the file again.')
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

# Use exact message IDs, never global document indexes or quoted thumbnails.
DOCUMENTS_SCRIPT = r"""ids => {
 const result = {};
 for (const id of ids) {
   const roots = [...document.querySelectorAll('[data-id]')].filter(e => e.getAttribute('data-id') === id);
   if (roots.length !== 1) continue;
   const root = roots[0];
   const unquoted = selector => [...root.querySelectorAll(selector)].some(e=>!e.closest('[data-testid*="quoted"],blockquote'));
   if (root.matches('.message-out') || root.closest('.message-out') || unquoted('[data-testid="tail-out"],[data-icon="tail-out"]')) continue;
   if (!root.matches('.message-in') && !root.closest('.message-in') && !unquoted('.message-in,[data-testid="tail-in"],[data-icon="tail-in"]')) continue;
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

CLICK_DOCUMENT_SCRIPT = r"""({id,index,name,marker}) => {
 const roots = [...document.querySelectorAll('[data-id]')].filter(e => e.getAttribute('data-id') === id);
 if (roots.length !== 1) return false;
 const root=roots[0];
 const unquoted = selector => [...root.querySelectorAll(selector)].some(e=>!e.closest('[data-testid*="quoted"],blockquote'));
 if (root.matches('.message-out') || root.closest('.message-out') || unquoted('[data-testid="tail-out"],[data-icon="tail-out"]')) return false;
 if (!root.matches('.message-in') && !root.closest('.message-in') && !unquoted('.message-in,[data-testid="tail-in"],[data-icon="tail-in"]')) return false;
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
    def __init__(self, base, storage):
        super().__init__(base.root, base.index, detect_content=True)
        self.base = base
        self.storage = FileLibrary(storage, Path(storage) / 'unused-index.json')
        self.storage.root.mkdir(parents=True, exist_ok=True)
        self.scope = None
        self.files = []

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
            if len(self.files) >= MAX_FILES:
                raise ValueError('This task already has 12 WhatsApp attachments.')
            observed = descriptors(page, [message_id]).get(message_id, [])
            if observed != documents:
                raise ValueError('WhatsApp attachment changed before download; send the file again with your request.')
            data = (download_image(page,message_id,doc) if doc.get('kind')=='image' else
                    download_document(page, message_id, doc, self.storage.root))
            if sum(e['bytes'] for e in self.files) + len(data) > MAX_TASK:
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
