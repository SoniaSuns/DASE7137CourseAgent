"""A photo-first request must include its following document/image siblings."""
import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image
from chrome_whatsapp_attachments import attachment_batches, download_image
from chrome_whatsapp_batch import batch_sources
import test_whatsapp_multifile_batch as fixtures


def picture():
    out = io.BytesIO()
    Image.new('RGB', (24, 24), 'green').save(out, format='PNG')
    return out.getvalue()


class MixedAttachmentBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.MultiDocumentBatchTests.setUpClass()

    @classmethod
    def tearDownClass(cls): fixtures.MultiDocumentBatchTests.tearDownClass()

    def setUp(self):
        self.c = fixtures.MultiDocumentBatchTests()
        self.c.setUp(); self.addCleanup(self.c.doCleanups)

    def photo(self, mid):
        self.c.page.evaluate('''async ({id,bytes})=>{
            const root=document.querySelector('[data-id="'+id+'"]');
            const doc=root.querySelector('[data-testid="document-thumb"]');
            const photo=document.createElement('div');photo.dataset.testid='image-thumb';
            photo.style='width:24px;height:24px';
            const image=document.createElement('img');
            image.src=URL.createObjectURL(new Blob([Uint8Array.from(atob(bytes),c=>c.charCodeAt(0))],{type:'image/png'}));
            photo.appendChild(image);doc.replaceWith(photo);await image.decode();
            const caption=root.querySelector('[data-testid~="document-caption"]');
            if(caption)caption.dataset.testid='image-caption selectable-text';
        }''', {'id': mid, 'bytes': base64.b64encode(picture()).decode()})

    def test_photo_first_collects_and_reads_both_following_documents(self):
        c = self.c; c.fixture(); self.photo('first'); c.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS', .1): c.r.handle_queue()
        c.r.submit.assert_called_once()
        context = c.r.submit.call_args.args[1]
        self.assertEqual(len(context['request_attachments']), 3)
        self.assertEqual(len(context['visual_inputs']), 1)
        self.assertEqual(context['_image_inputs'][0]['data'], picture())
        self.assertEqual(c.page.evaluate('window.downloaded'), ['second.py', 'third.py'])
        state = c.r.flows['group'].state
        self.assertEqual(state['attachment_postcheck']['status'], 'verified')
        for entry in state['whatsapp_files'][1:]:
            self.assertIn('Source for', c.library.inspect(c.library.snapshot(), entry['id'])['text'])

    def test_document_then_photo_then_document_are_one_batch(self):
        c = self.c; c.fixture(); self.photo('second'); c.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS', .1): c.r.handle_queue()
        c.r.submit.assert_called_once()
        self.assertEqual(len(c.r.submit.call_args.args[1]['request_attachments']), 3)
        self.assertEqual(len(c.r.submit.call_args.args[1]['visual_inputs']), 1)
        self.assertEqual(c.page.evaluate('window.downloaded'), ['first.py', 'third.py'])

    def test_three_photos_have_distinct_ids_and_visual_inputs(self):
        c = self.c; c.fixture()
        for mid in ('first', 'second', 'third'): self.photo(mid)
        c.r.poll()
        with patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS', .1): c.r.handle_queue()
        c.r.submit.assert_called_once()
        context = c.r.submit.call_args.args[1]
        self.assertEqual(len({e['file_id'] for e in context['visual_inputs']}), 3)
        self.assertEqual(len(context['request_attachments']), 3)

    def test_image_caption_or_other_identity_stops_batch(self):
        c = self.c
        for kwargs in ({'caption': 'Separate request'}, {'sender': '+19999999999'},
                       {'clock': '00:00'}, {'tail': 'out'}, {'tail': 'in'}):
            with self.subTest(kwargs=kwargs):
                c.fixture(c.doc('second', **kwargs) + c.doc('third'))
                self.photo('first'); self.photo('second')
                rows = c.rows()
                self.assertEqual(len(batch_sources(c.anchor(rows), rows)), 1)

    def test_quoted_image_cannot_anchor_document_siblings(self):
        c = self.c; c.fixture(); self.photo('first')
        c.page.locator('[data-testid="image-thumb"]').evaluate('''e=>{
            const quote=document.createElement('blockquote');e.replaceWith(quote);quote.appendChild(e);}''')
        batches = attachment_batches(c.page, ['first', 'second', 'third'])
        self.assertEqual(batches, {})

    def test_tail_less_photo_must_remain_bound_to_incoming_batch(self):
        c = self.c; c.fixture(); self.photo('second')
        self.assertEqual(download_image(c.page, 'second', {'index': 0}), picture())
        c.page.locator('[data-id="first"]').evaluate('e=>e.remove()')
        with self.assertRaises(Exception): download_image(c.page, 'second', {'index': 0})


if __name__ == '__main__': unittest.main()
