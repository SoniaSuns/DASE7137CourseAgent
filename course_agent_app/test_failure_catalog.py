import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import build_diagnostic_report as report
import watch_failure_report as watcher
from report_io import atomic_text,exclusive,ReportBusy


class FailureCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'reports/catalog';self.state=self.root/'reports/state'
        self.out.mkdir(parents=True);self.state.mkdir(parents=True)
        for name,value in [('ROOT',self.root),('OUT',self.out),('STATE',self.state)]:
            p=patch.object(report,name,value);p.start();self.addCleanup(p.stop)

    def state_file(self,run='aaaaaaaaaaaa',phase='FAILED'):
        p=self.state/(run+'.json')
        p.write_text(json.dumps({'run':run,'task_id':'T-'+run,'phase':phase,'task':'Change date',
                               'reason':'Failed fixture','chat':'fixture','created':1}),encoding='utf-8')
        return p

    def test_merge_deduplicates_and_retains_history_after_source_eviction(self):
        p=self.state_file();rows=report.failures()
        report.save('failed_requests.json',{'cases':rows})
        self.assertEqual(len(report.failures()),1)
        p.unlink();self.state_file('bbbbbbbbbbbb')
        rows=report.failures();self.assertEqual({r['run'] for r in rows},{'aaaaaaaaaaaa','bbbbbbbbbbbb'})

    def test_new_trace_error_in_done_task_is_preserved_and_redacted(self):
        self.state_file(phase='DONE');folder=self.state/'traces/aaaaaaaaaaaa';folder.mkdir(parents=True)
        artifact='c'*32+'-error.json'
        (folder/artifact).write_text(json.dumps({'message':'sesskey=private OK 654321'}),encoding='utf-8')
        event={'event':'error','span':'c'*32,'at_ns':100,'layer':'browser','operation':'inspect','artifact':artifact}
        (folder/'trace.jsonl').write_text(json.dumps(event)+'\n{incomplete',encoding='utf-8')
        rows=report.failures();self.assertEqual(len(rows),1)
        self.assertIn('已完成',rows[0]['outcome']);self.assertEqual(rows[0]['first_observed_step'],'inspect')
        self.assertNotIn('private',json.dumps(rows));self.assertNotIn('654321',json.dumps(rows))

    def test_curated_diagnosis_and_evidence_survive_updates(self):
        self.state_file()
        (self.out/'failure_annotations.json').write_text(json.dumps({'aaaaaaaaaaaa':{
            'diagnosis':'Confirmed cause','handling':'Fixed locally','evidence':['evidence.txt']}}),encoding='utf-8')
        rows=report.failures();report.save('failed_requests.json',{'cases':rows})
        self.state_file(phase='DONE');rows=report.failures()
        self.assertEqual(rows[0]['diagnosis'],'Confirmed cause');self.assertIn('evidence.txt',rows[0]['evidence'])

    def test_reported_question_loop_is_cataloged_without_fake_execution_failure(self):
        self.state_file(phase='QUESTION')
        (self.out/'failure_annotations.json').write_text(json.dumps({'aaaaaaaaaaaa':{
            'reported_issue':True,'reason':'Repeated answered question'}}),encoding='utf-8')
        rows=report.failures()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['phase'],'QUESTION')
        self.assertIn('不等于执行失败',rows[0]['outcome'])

    def test_corrupt_existing_catalog_is_not_silently_replaced(self):
        p=self.out/'failed_requests.json';p.write_text('{partial',encoding='utf-8')
        with self.assertRaises(ValueError):report.failures()
        self.assertEqual(p.read_text(encoding='utf-8'),'{partial')

    def test_csv_has_chinese_summary_and_inert_request_text(self):
        self.state_file();rows=report.failures();rows[0]['request']='=dangerous formula'
        report.write_failure_csv(rows)
        with (self.out/'failed_requests.csv').open(encoding='utf-8-sig',newline='') as f:csvrows=list(csv.DictReader(f))
        self.assertEqual(csvrows[0]['请求'],"'=dangerous formula")
        self.assertIn('确认原因/诊断',csvrows[0]);self.assertIn('已做处理/后续',csvrows[0])

    def test_atomic_write_failure_keeps_last_good_file(self):
        p=self.out/'file.txt';p.write_text('old',encoding='utf-8')
        with patch.object(Path,'replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):atomic_text(p,'new')
        self.assertEqual(p.read_text(encoding='utf-8'),'old')
        self.assertEqual(list(self.out.glob('*.tmp')),[])

    def test_report_lock_excludes_concurrent_writers(self):
        p=self.out/'writer.lock'
        with exclusive(p):
            with self.assertRaises(ReportBusy):
                with exclusive(p):pass
        with exclusive(p):pass

    def test_two_watcher_refreshes_collect_new_failure_in_same_file(self):
        self.state_file();status={}
        with patch.object(watcher,'OUT',self.out):
            watcher.refresh(status)
            self.assertIsNone(status['error']);first=status['cases']
            self.state_file('bbbbbbbbbbbb');watcher.refresh(status)
            self.assertEqual(status['cases'],first+1)
            self.assertTrue((self.out/'failed_requests.csv').exists())
            self.assertEqual(json.loads((self.out/'failed_requests.json').read_text(encoding='utf-8'))['count'],2)


if __name__=='__main__':unittest.main()
