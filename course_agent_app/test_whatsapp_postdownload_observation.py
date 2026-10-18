"""A downloaded attachment survives a temporary WhatsApp message remount."""
import io
import unittest
import zipfile
from unittest.mock import Mock,patch

from chrome_whatsapp_batch import (
    BatchObservationUnavailable,BatchSourceChanged,verify_downloaded_batch,
)
import test_whatsapp_multifile_batch as fixtures


class PostDownloadObservationTests(unittest.TestCase):
    def setUp(self):
        self.anchor={'sender':'alice','attachment_message_id':'zip',
                     'attachments':[{'index':0,'name':'archive.zip'}],
                     'attachment_fingerprint':'verified-fingerprint'}
        self.row={'message_id':'zip','direction':'incoming','sender_id':'alice',
                  'attachment_batch_id':'zip','attachments':self.anchor['attachments'],
                  'attachment_fingerprint':self.anchor['attachment_fingerprint']}
        self.elapsed=0
        self.page=Mock()
        self.page.wait_for_timeout.side_effect=self.advance

    def advance(self,milliseconds):self.elapsed+=milliseconds/1000

    def verify(self,reader):
        with patch('chrome_whatsapp_batch.time.monotonic',side_effect=lambda:self.elapsed):
            return verify_downloaded_batch(self.page,self.anchor,[self.anchor],reader)

    def test_temporarily_missing_message_is_reobserved_without_transfer(self):
        reader=Mock(side_effect=[[],[self.row],[self.row]])
        result=self.verify(reader)
        self.assertEqual(result,{'status':'verified','observations':3})
        self.assertEqual(reader.call_count,3)

    def test_incomplete_fields_must_reappear_and_match_original(self):
        for field,value in (('direction','unknown'),('sender_id',''),('attachment_batch_id',None),
                            ('attachments',[]),('attachment_fingerprint',None)):
            with self.subTest(field=field):
                self.elapsed=0
                reader=Mock(side_effect=[[{**self.row,field:value}],[self.row],[self.row]])
                self.assertEqual(self.verify(reader)['observations'],3)

    def test_complete_conflicting_evidence_is_not_retried(self):
        changes=[{'sender_id':'bob'},{'direction':'outgoing'},{'attachment_batch_id':'other'},
                 {'attachments':[{'index':0,'name':'replacement.zip'}]},
                 {'attachment_fingerprint':'changed-caption-or-mention'}]
        for changeset in changes:
            with self.subTest(changeset=changeset):
                self.elapsed=0
                reader=Mock(side_effect=[[{**self.row,**changeset}],[self.row]])
                with self.assertRaises(BatchSourceChanged):self.verify(reader)
                self.assertEqual(reader.call_count,1)

    def test_persistent_absence_is_bounded_and_reports_retained_bytes(self):
        reader=Mock(return_value=[])
        with self.assertRaisesRegex(BatchObservationUnavailable,'Downloaded bytes are retained'):
            self.verify(reader)
        self.assertLessEqual(reader.call_count,31)

    def test_timeout_identifies_the_missing_sibling_not_the_anchor(self):
        sibling={**self.anchor,'attachment_message_id':'second',
                 'attachments':[{'index':0,'name':'second.zip'}]}
        with patch('chrome_whatsapp_batch.time.monotonic',side_effect=lambda:self.elapsed):
            with self.assertRaises(BatchObservationUnavailable) as caught:
                verify_downloaded_batch(self.page,self.anchor,[self.anchor,sibling],lambda:[self.row])
        self.assertEqual(caught.exception.message_id,'second')
        self.assertTrue(str(caught.exception).startswith('second.zip:'))

    def test_two_matching_observations_must_be_consecutive(self):
        reader=Mock(side_effect=[[self.row],[],[self.row],[self.row]])
        self.assertEqual(self.verify(reader)['observations'],4)

    def test_new_sibling_during_reobservation_cannot_be_ignored(self):
        extra={**self.row,'message_id':'extra','attachments':[{'index':0,'name':'second.zip'}]}
        reader=Mock(side_effect=[[],[self.row,extra]])
        with self.assertRaisesRegex(ValueError,'grew during download'):self.verify(reader)

    def test_conflicting_duplicate_is_rejected_even_with_incomplete_duplicate(self):
        reader=Mock(return_value=[{**self.row,'attachments':[]},{**self.row,'sender_id':'bob'}])
        with self.assertRaisesRegex(BatchSourceChanged,'sender changed'):self.verify(reader)
        self.assertEqual(reader.call_count,1)


class ZipRemountRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.MultiDocumentBatchTests.setUpClass()

    @classmethod
    def tearDownClass(cls):fixtures.MultiDocumentBatchTests.tearDownClass()

    def setUp(self):
        self.c=fixtures.MultiDocumentBatchTests()
        self.c.setUp();self.addCleanup(self.c.doCleanups)
        self.c.fixture(extra='')
        self.c.page.locator('[data-testid="document-thumb"]').evaluate('''e=>{
          e.title='Download "archive.zip"';e.querySelector('[data-name]').textContent='archive.zip';}''')
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:archive.writestr('TutorialPrompt.txt','An attached tutorial prompt.')
        self.payload=stream.getvalue()
        self.c.page.evaluate('''bytes=>{
          const create=URL.createObjectURL.bind(URL);
          URL.createObjectURL=()=>create(new Blob([new Uint8Array(bytes)]));
        }''',list(self.payload))

    def test_real_zip_saved_once_and_planner_runs_after_exact_message_returns(self):
        c=self.c;c.r.poll();real_download=c.library.download
        def download_and_remount(*args):
            real_download(*args)
            c.page.locator('[data-id="first"]').evaluate('''e=>{
              const html=e.outerHTML,parent=e.parentElement;e.remove();
              setTimeout(()=>parent.insertAdjacentHTML('beforeend',html),250);
            }''')
        with patch.object(c.library,'download',side_effect=download_and_remount) as transfer,patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1):
            c.r.handle_queue()
        transfer.assert_called_once();c.r.submit.assert_called_once()
        state=c.r.flows['group'].state
        self.assertEqual(state['attachment_postcheck']['status'],'verified')
        self.assertGreater(state['attachment_postcheck']['observations'],2)
        self.assertEqual(c.page.evaluate('window.downloaded'),['archive.zip'])
        self.assertEqual(c.library.read_bytes(c.library.snapshot(),state['whatsapp_files'][0]['id'])[1],self.payload)

    def test_real_zip_remains_saved_when_message_never_returns(self):
        c=self.c;c.r.poll();real_download=c.library.download
        def download_and_remove(*args):
            real_download(*args);c.page.locator('[data-id="first"]').evaluate('e=>e.remove()')
        with patch.object(c.library,'download',side_effect=download_and_remove) as transfer,patch('chrome_whatsapp_batch.BATCH_QUIET_SECONDS',.1),patch('chrome_whatsapp_batch.BATCH_RECHECK_SECONDS',.3):
            c.r.handle_queue()
        transfer.assert_called_once();c.r.submit.assert_not_called()
        state=c.r.flows['group'].state
        self.assertEqual(state['phase'],'FAILED')
        self.assertEqual(state['attachment_postcheck']['error_type'],'BatchObservationUnavailable')
        self.assertEqual(len(state['whatsapp_files']),1)
        self.assertIn('Downloaded bytes are retained',state['reason'])


if __name__=='__main__':unittest.main()
