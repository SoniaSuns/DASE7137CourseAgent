"""ZIP requests must read each content member, not just the parent text preview."""
import io
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
from chrome_archive_sources import expand_archives, inspect_members
from chrome_file_library import FileLibrary, FileLibraryChanged, sha
from chrome_planner import stage_visual_inputs
from chrome_whatsapp_attachments import WhatsAppFiles
from run_shared_chrome_agent import Runtime


def zipped(items):
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in items:
                archive.writestr(name, data)
    return out.getvalue()


def picture():
    out = io.BytesIO()
    Image.new('RGB', (20, 30), 'blue').save(out, format='PNG')
    return out.getvalue()


class ArchiveMemberTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'base').mkdir()
        self.library = WhatsAppFiles(FileLibrary(self.root / 'base', self.root / 'index.json'), self.root / 'downloads')
        self.state = {'sender': 'Alice', 'run': 'run1', 'whatsapp_files': []}
        self.library.bind('chat', self.state)

    def attach(self, data, name='bundle.zip'):
        key = sha(data)
        (self.root / 'downloads' / key).write_bytes(data)
        entry = {'id': 'wa:' + key, 'relative_path': 'WhatsApp/' + name,
                 'format': Path(name).suffix, 'bytes': len(data), 'sha256': sha(data),
                 'text_reader': True, 'storage_key': key, 'owner': list(self.library.scope), 'message_id': 'source'}
        self.library.files.append(entry)
        self.state['whatsapp_files'].append(entry)
        return entry

    def inspect_all(self):
        manifest = expand_archives(self.library)
        evidence = []
        inspect_members(self.library, self.library.snapshot(), manifest, evidence)
        return manifest, evidence

    def test_runtime_supplies_two_texts_and_image_from_one_zip(self):
        self.attach(zipped([('Tutorial.jpg', picture()), ('Prompt.txt', b'first prompt'),
                            ('Initial.txt', b'second prompt'), ('__MACOSX/._Tutorial.jpg', b'metadata')]))
        browser = Mock(); browser.overviews = {}; browser.forms = {}; browser.unavailable_targets = {}
        runtime = Runtime(Mock(), browser, ['chat'], [], Mock(), self.root / 'state', Mock(), self.library, True)
        flow = runtime.flows['chat']; flow.begin('Read every file', 'Alice', 'source')
        flow.state.update(self.state)
        context = runtime.context(flow)
        self.assertEqual(len(context['request_attachments']), 1)
        self.assertEqual([e['status'] for e in context['archive_members']], ['image', 'read', 'read', 'metadata_skipped'])
        self.assertEqual([e['text'].strip() for e in context['file_evidence'] if e['status'] == 'read'], ['first prompt', 'second prompt'])
        self.assertEqual(context['_image_inputs'][0]['data'], picture())
        visual_root = self.root / 'visual'; visual_root.mkdir()
        self.assertEqual(len(stage_visual_inputs(context, visual_root)), 1)
        again = runtime.context(flow)
        self.assertEqual(len(again['file_evidence']), 3)
        self.assertEqual(len(flow.state['whatsapp_files']), 4)
        runtime.library_planner_access = False
        self.assertEqual(runtime.context(flow)['archive_members'], [])
        runtime.pool.shutdown(wait=True)

    def test_traversal_and_duplicate_names_are_distinct_in_memory_only(self):
        self.attach(zipped([('../outside.txt', b'safe data'), ('same.txt', b'one'), ('same.txt', b'two')]))
        manifest, evidence = self.inspect_all()
        self.assertEqual(len({r['file_id'] for r in manifest}), 3)
        self.assertEqual([e['text'].strip() for e in evidence], ['safe data', 'one', 'two'])
        self.assertFalse((self.root / 'outside.txt').exists())
        self.assertEqual(len(list((self.root / 'downloads').iterdir())), 1)

    def test_nested_zip_is_expanded_but_office_is_a_document(self):
        office = zipped([('word/document.xml', b'<xml/>')])
        self.attach(zipped([('nested.zip', zipped([('leaf.txt', b'nested text')])), ('document.docx', office)]))
        manifest = expand_archives(self.library)
        self.assertEqual([e['status'] for e in manifest], ['archive', 'pending_read', 'pending_read'])
        self.assertEqual(len(self.library.files), 4)

    def test_member_revalidates_parent_hash_and_task_owner(self):
        root = self.attach(zipped([('file.txt', b'text')]))
        manifest, _ = self.inspect_all(); fid = manifest[0]['file_id']; snapshot = self.library.snapshot()
        self.library.bind('chat', {'sender': 'Bob', 'run': 'run2'})
        with self.assertRaisesRegex(ValueError, 'current task'):
            self.library.read_bytes(snapshot, fid)
        self.state['whatsapp_files'] = snapshot['files']
        self.library.bind('chat', self.state)
        (self.root / 'downloads' / root['storage_key']).write_bytes(b'changed')
        with self.assertRaises(FileLibraryChanged):
            self.library.read_bytes(snapshot, fid)

    def test_retry_rebinds_members_and_preserves_exact_bytes(self):
        self.attach(zipped([('file.txt', b'text')]))
        manifest, _ = self.inspect_all()
        self.library.bind('chat', {'sender': 'Alice', 'run': 'run2', 'whatsapp_files': [
            {**e, 'owner': ['chat', 'Alice', 'run2']} for e in self.library.files]})
        self.assertEqual(self.library.read_bytes(self.library.snapshot(), manifest[0]['file_id'])[1], b'text')
        self.assertEqual(len(expand_archives(self.library)), 1)

    def test_unsupported_member_does_not_hide_readable_sibling(self):
        self.attach(zipped([('unknown.bin', b'\x00\x01\xff'), ('read.txt', b'hello')]))
        manifest, _ = self.inspect_all()
        self.assertIn(manifest[0]['status'], {'reader_unavailable', 'no_extractable_text'})
        self.assertEqual(manifest[1]['status'], 'read')

    def test_all_text_pages_are_read_until_complete(self):
        self.attach(zipped([('long.txt', b'x' * 35000)]))
        manifest, evidence = self.inspect_all()
        self.assertEqual([e['offset'] for e in evidence], [0, 16000, 32000])
        self.assertEqual(manifest[0]['status'], 'read')
        self.assertIsNone(evidence[-1]['next_offset'])

    def test_text_and_image_budgets_leave_explicit_member_outcomes(self):
        self.attach(zipped([(f'{i}.png', picture()) for i in range(13)] + [('long.txt', b'x' * 150000), ('last.txt', b'last')]))
        manifest, evidence = self.inspect_all()
        self.assertEqual(sum(e['status'] == 'image' for e in evidence), 12)
        self.assertEqual(manifest[12]['status'], 'unreadable')
        self.assertIn('12 images', manifest[12]['reason'])
        self.assertEqual(manifest[13]['status'], 'pending_pages')
        self.assertEqual(manifest[14]['status'], 'pending_read')

    def test_expansion_limits_report_names_without_failing_siblings(self):
        self.attach(zipped([('small.txt', b'ok'), ('large.txt', b'x' * 300)]))
        with patch('chrome_archive_sources.MAX_EXPANDED', 100):
            manifest, _ = self.inspect_all()
        self.assertEqual(manifest[0]['status'], 'read')
        self.assertEqual(manifest[1]['status'], 'unreadable')
        self.assertTrue(manifest[1]['relative_path'].endswith('large.txt'))

    def test_depth_limit_and_corrupt_zip_are_reported(self):
        nested = zipped([('leaf.txt', b'text')])
        for _ in range(4):
            nested = zipped([('next.zip', nested)])
        self.attach(nested)
        manifest, _ = self.inspect_all()
        self.assertEqual(manifest[-1]['status'], 'unreadable')
        self.attach(b'PK\x03\x04\x00\xff', 'corrupt.zip')
        self.assertEqual(expand_archives(self.library)[-1]['reason'], 'Corrupt ZIP archive.')

    def test_corrupt_central_directory_does_not_abort_other_archives(self):
        data = bytearray(zipped([('one.txt', b'one'), ('two.txt', b'two')]))
        first = data.index(b'PK\x01\x02'); second = data.index(b'PK\x01\x02', first + 1)
        data[second:second+4] = b'BAD!'
        self.attach(bytes(data), 'corrupt.zip')
        self.attach(zipped([('good.txt', b'available')]), 'good.zip')
        manifest, evidence = self.inspect_all()
        self.assertEqual(manifest[0]['status'], 'unreadable')
        self.assertEqual(manifest[1]['status'], 'read')
        self.assertEqual(evidence[0]['text'].strip(), 'available')

    def test_encrypted_member_is_named_and_other_member_is_read(self):
        data = bytearray(zipped([('private.txt', b'secret'), ('public.txt', b'public')]))
        local = data.index(b'PK\x03\x04'); central = data.index(b'PK\x01\x02')
        data[local + 6] |= 1; data[central + 8] |= 1
        self.attach(bytes(data))
        manifest, evidence = self.inspect_all()
        self.assertEqual(manifest[0]['status'], 'unreadable')
        self.assertIn('Password-protected', manifest[0]['reason'])
        self.assertEqual(manifest[1]['status'], 'read')
        self.assertEqual(evidence[0]['text'].strip(), 'public')

    def test_member_count_includes_metadata_and_has_bounded_manifest(self):
        self.attach(zipped([(f'__MACOSX/._{i}', b'metadata') for i in range(10)]))
        with patch('chrome_archive_sources.MAX_MEMBERS', 2):
            manifest, evidence = self.inspect_all()
        self.assertEqual(len(manifest), 3)
        self.assertEqual(manifest[-1]['status'], 'unreadable')
        self.assertIn('8 remaining entries', manifest[-1]['reason'])
        self.assertEqual(evidence, [])

    def test_corrupt_compressed_member_does_not_abort_readable_sibling(self):
        data = bytearray(zipped([('broken.txt', b'a' * 1000), ('good.txt', b'available')]))
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            first = archive.infolist()[0]
            start = first.header_offset + 30 + len(first.filename.encode()) + len(first.extra)
            data[start:start+first.compress_size] = b'\xff' * first.compress_size
        self.attach(bytes(data))
        manifest, evidence = self.inspect_all()
        self.assertEqual(manifest[0]['status'], 'unreadable')
        self.assertEqual(manifest[1]['status'], 'read')
        self.assertEqual(evidence[0]['text'].strip(), 'available')


if __name__ == '__main__':
    unittest.main()
