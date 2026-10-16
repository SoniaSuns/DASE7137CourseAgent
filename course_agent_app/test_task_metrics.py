import json
import tempfile
import unittest
from pathlib import Path

from task_metrics import summarize


def run(n,task_id,phase,**extra):
    return {'run':f'{n:012x}','task_id':task_id,'task':'Goal '+task_id,'phase':phase,'created':n,'results':[],**extra}


class TaskMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def write(self,name,state):
        (self.root/(name+'.json')).write_text(json.dumps(state),encoding='utf-8')

    def test_write_task_rates_and_runs_per_task(self):
        op=[{'id':'one'}]
        self.write('archived-run-000000000001',run(1,'T-a','FAILED',selected=op))
        self.write('archived-run-000000000002',run(2,'T-a','EXPIRED',selected=op))
        self.write('archived-run-000000000003',run(3,'T-a','DONE',selected=op,results=[{'verified':True}]))
        self.write('archived-run-000000000004',run(4,'T-b','DONE',selected=op,results=[{'verified':True}]))
        self.write('archived-run-000000000005',run(5,'T-c','DONE'))
        report=summarize(self.root)
        self.assertEqual((report['tasks'],report['runs'],report['write_tasks']),(3,5,2))
        self.assertEqual(report['write_first_run_success_rate'],0.5)
        self.assertEqual(report['write_done_rate'],1.0)
        self.assertEqual(report['mean_runs_per_write_task'],2.0)
        self.assertEqual(report['rows'][0]['phases'],['FAILED','EXPIRED','DONE'])

    def test_live_flow_overrides_archived_copy_and_untagged_runs_group_by_text(self):
        self.write('archived-run-000000000001',run(1,'T-a','INTERRUPTED'))
        self.write('000000000001',run(1,'T-a','APPROVAL',selected=[{'id':'one'}]))
        self.write('archived-run-000000000002',{'run':'000000000002','task':' Same Goal','phase':'FAILED','created':2})
        self.write('archived-run-000000000003',{'run':'000000000003','task':'same goal','phase':'FAILED','created':3})
        rows={r['task']:r for r in summarize(self.root)['rows']}
        self.assertTrue(rows['T-a']['active']);self.assertEqual(rows['T-a']['phases'],['APPROVAL'])
        self.assertEqual(rows['text:same goal']['runs'],2)

    def test_schedule_done_without_goal_verification_is_flagged(self):
        self.write('archived-run-000000000001',run(1,'T-a','DONE',schedule_required=True,selected=[{'id':'one'}]))
        self.assertEqual(summarize(self.root)['done_without_goal_verification'],1)


if __name__=='__main__':unittest.main()
