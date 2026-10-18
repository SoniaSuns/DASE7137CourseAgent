"""Bounded, task-owned ZIP members. Member names are labels, never disk paths."""
import io
import json
import lzma
from pathlib import PurePosixPath
import zipfile
import zlib

from chrome_file_library import READABLE, FileLibraryChanged, content_format, sha

MAX_MEMBERS = 512
MAX_EXPANDED = 32 * 1024 * 1024
MAX_DEPTH = 3
AUTO_TEXT = 128000


def member_bytes(data, index, name):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if type(index) is not int or not 0 <= index < len(items):
            raise ValueError('Archive member index changed.')
        item = items[index]
        if item.filename != name or item.is_dir():
            raise ValueError('Archive member identity changed.')
        if item.flag_bits & 1:
            raise ValueError('Password-protected archive member.')
        if item.file_size > MAX_EXPANDED:
            raise ValueError('Archive member exceeds the expanded-size limit.')
        with archive.open(item) as stream:
            result = stream.read(MAX_EXPANDED + 1)
        if len(result) > MAX_EXPANDED or len(result) != item.file_size:
            raise ValueError('Archive member size is invalid or exceeds the limit.')
        return result


def read_member(library, snapshot, entry, seen=()):
    if entry['id'] in seen or len(seen) >= MAX_DEPTH:
        raise ValueError('Archive ancestry is cyclic or too deep.')
    parent = library.entry(snapshot, entry['archive_parent_id'])
    if parent['sha256'] != entry['archive_parent_sha256']:
        raise ValueError('Archive parent identity changed.')
    if parent.get('archive_parent_id'):
        data = read_member(library, snapshot, parent, (*seen, entry['id']))
    else:
        _, data = library.read_bytes(snapshot, parent['id'])
    result = member_bytes(data, entry['archive_index'], entry['archive_name'])
    if sha(result) != entry['sha256']:
        raise FileLibraryChanged(entry, sha(result))
    return result


def expand_archives(library):
    """Return a complete inventory, retaining named failures and metadata skips."""
    manifest = []
    budget = {'members': 0, 'bytes': 0}
    known = {e['id']: e for e in library.files}

    def expand(parent, data, depth):
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (ValueError, OSError, zipfile.BadZipFile):
            manifest.append({'file_id': parent['id'], 'relative_path': parent['relative_path'],
                             'status': 'unreadable', 'reason': 'Corrupt ZIP archive.'})
            return
        with archive:
            items = archive.infolist()
            for index, item in enumerate(items):
                if item.is_dir():
                    continue
                if budget['members'] >= MAX_MEMBERS:
                    manifest.append({'file_id': parent['id'], 'relative_path': parent['relative_path'],
                                     'status': 'unreadable', 'reason': f'Archive inventory limit reached; {len(items)-index} remaining entries were not inspected.'})
                    break
                budget['members'] += 1
                path = parent['relative_path'] + '/' + item.filename
                row = {'parent_id': parent['id'], 'member_index': index, 'relative_path': path}
                manifest.append(row)
                name = item.filename.replace('\\', '/')
                if '__MACOSX' in name.split('/') or PurePosixPath(name).name.startswith('._'):
                    row.update(status='metadata_skipped', reason='AppleDouble filesystem metadata, not document content.')
                    continue
                if depth > MAX_DEPTH or budget['bytes'] + item.file_size > MAX_EXPANDED:
                    row.update(status='unreadable', reason='Archive expansion depth, member-count or 32 MiB total limit reached.')
                    continue
                budget['bytes'] += item.file_size
                try:
                    member = member_bytes(data, index, item.filename)
                    digest = sha(member)
                    fid = 'wa:' + sha(json.dumps([parent['id'], parent['sha256'], index, item.filename, digest]).encode())
                    suffix = PurePosixPath(name).suffix.lower()
                    detected = content_format(member, suffix)
                    entry = {'id': fid, 'relative_path': path, 'format': suffix,
                             'bytes': len(member), 'sha256': digest, 'text_reader': detected in READABLE,
                             'owner': list(library.scope), 'message_id': parent['message_id'],
                             'archive_parent_id': parent['id'], 'archive_parent_sha256': parent['sha256'],
                             'archive_index': index, 'archive_name': item.filename}
                    if fid in known and known[fid] != entry:
                        raise ValueError('Archive member metadata changed.')
                    if fid not in known:
                        library.files.append(entry)
                        known[fid] = entry
                    row.update(file_id=fid, status='archive' if detected == '.zip' else 'pending_read')
                    if detected == '.zip':
                        expand(entry, member, depth + 1)
                except (ValueError, RuntimeError, OSError, NotImplementedError, zipfile.BadZipFile, zlib.error, lzma.LZMAError) as exc:
                    reason = ('Password-protected archive member.' if item.flag_bits & 1 else
                              type(exc).__name__ + ': archive member cannot be unpacked.')
                    row.update(status='unreadable', reason=reason)

    snapshot = library.snapshot()
    for entry in list(library.files):
        if entry.get('archive_parent_id'):
            continue
        _, data = library.read_bytes(snapshot, entry['id'])
        try:
            detected = content_format(data, entry['format'])
        except (ValueError, RuntimeError, OSError, NotImplementedError, zipfile.BadZipFile):
            manifest.append({'file_id': entry['id'], 'relative_path': entry['relative_path'],
                             'status': 'unreadable', 'reason': 'Corrupt or unsupported archive container.'})
            continue
        if detected == '.zip':
            expand(entry, data, 1)
    return manifest


def inspect_members(library, snapshot, manifest, evidence):
    """Read every leaf within bounded context; expose any remaining pages explicitly."""
    text_size = sum(len(e.get('text', '')) for e in evidence)
    images = {e['file_id'] for e in evidence if e.get('status') == 'image'}
    for row in manifest:
        if row['status'] not in {'pending_read', 'read', 'image', 'pending_pages', 'reader_unavailable', 'no_extractable_text'}:
            continue
        fid = row['file_id']
        pages = [e for e in evidence if e['file_id'] == fid]
        offset = pages[-1]['next_offset'] if pages else 0
        while offset is not None and text_size < AUTO_TEXT:
            result = library.inspect(snapshot, fid, offset)
            if result['status'] == 'image' and fid not in images and len(images) >= 12:
                row.update(status='unreadable', reason='Visual input limit reached (12 images per planning request).')
                break
            evidence.append(result)
            pages.append(result)
            text_size += len(result.get('text', ''))
            if result['status'] == 'image':
                images.add(fid)
            offset = result['next_offset']
        if row['status'] == 'unreadable':
            continue
        if pages:
            last = pages[-1]
            row['status'] = 'pending_pages' if last['next_offset'] is not None else last['status']
            if last.get('reason'):
                row['reason'] = last['reason']
            if last['next_offset'] is not None:
                row['next_offset'] = last['next_offset']
        else:
            row.update(status='pending_read', reason='Automatic text budget reached; request this member with inspect_files.')
