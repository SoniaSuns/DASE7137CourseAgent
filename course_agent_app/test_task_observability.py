import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone
from task_observability import TaskObserver,task_status_text
from agent_status import snapshot,render


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=1000
        self.observer=TaskObserver(self.root,clock=lambda:self.now,max_bytes=500)
        self.state={'run':'a'*12,'task_id':'T-'+'b'*12,'phase':'PLANNING','created':990,
                    'task':'PRIVATE BODY','code':'654321','sender':'PRIVATE SENDER','results':[]}
        self.observer.sync('chat',self.state)

    def row(self):return self.observer.snapshot()['tasks']['chat']

    def test_live_heartbeat_does_not_reset_real_progress(self):
        self.observer.step('chat','planner_running',round=1,forms=104)
        self.now=1400
        for _ in range(10):self.observer.sync('chat',self.state);self.observer.snapshot()
        self.assertEqual(self.row()['no_progress_seconds'],400)
        self.assertEqual(self.row()['assessment'],'slow_progress')
        self.observer.step('chat','inspecting_forms',current=1,total=32,target='140148:module:1')
        self.assertEqual(self.row()['assessment'],'working')

    def test_approval_expiration_and_questions_are_not_stalls(self):
        self.state.update(phase='APPROVAL',approval_sent=True,expires=1100)
        self.observer.sync('chat',self.state);self.now=1200
        self.assertEqual(self.row()['assessment'],'approval_expired')
        self.state.update(expires=None);self.observer.sync('chat',self.state);self.now=99999
        self.assertEqual(self.row()['assessment'],'waiting_for_approval')
        self.assertIsNone(self.row()['approval_remaining_seconds'])
        self.state['phase']='QUESTION';self.observer.sync('chat',self.state);self.now=99999
        self.assertEqual(self.row()['assessment'],'waiting_for_answer')

    def test_saving_target_and_verified_progress(self):
        self.state.update(phase='EXECUTING',selected=[{'id':'op','target':'140148:module:7'}],write_started='op')
        self.observer.sync('chat',self.state)
        self.assertEqual(self.row()['target'],'140148:module:7')
        self.state.update(phase='DONE',results=[{'verified':True}]);self.observer.sync('chat',self.state)
        self.assertEqual(self.row()['verified'],1);self.assertEqual(self.row()['assessment'],'done')

    def test_no_raw_messages_codes_credentials_or_exception_text(self):
        self.observer.failure('chat',ValueError('SECRET TOKEN'),'preflight',{'target':'140148:module:1','field':'id_name','value':'SECRET VALUE'})
        self.observer.event('chat','step',text='PRIVATE',code='654321',input_characters=120)
        data=json.dumps(self.observer.snapshot())+self.observer.path.read_text(encoding='utf-8')
        for secret in ('PRIVATE BODY','PRIVATE SENDER','654321','SECRET TOKEN','SECRET VALUE'):self.assertNotIn(secret,data)
        self.assertEqual(self.row()['last_error']['field'],'id_name')

    def test_log_rotation_is_bounded_and_errors_do_not_stop_task(self):
        for i in range(60):self.observer.step('chat','inspecting_forms',current=i,total=60)
        self.assertTrue((self.root/'events.previous.jsonl').exists())
        self.assertLess(self.observer.path.stat().st_size,1000)
        with patch.object(Path,'open',side_effect=OSError('disk unavailable')):
            self.observer.step('chat','planner_running')
        self.assertEqual(self.observer.snapshot()['telemetry_write_error'],'OSError')

    def test_new_run_resets_old_error_and_step(self):
        self.observer.failure('chat',ValueError(),'preflight',{})
        self.state['run']='c'*12;self.observer.sync('chat',self.state)
        self.assertNotIn('last_error',self.row());self.assertEqual(self.row()['step'],'planning')


class StatusReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'state').mkdir();(self.root/'planner').mkdir()
    def write(self,name,data):
        (self.root/name).write_text(json.dumps(data),encoding='utf-8')

    def test_stale_heartbeat_is_not_reported_as_running(self):
        self.write('health.json',{'updated':datetime.fromtimestamp(1000,timezone.utc).isoformat(),'status':'healthy','stage':'listening'})
        doc=snapshot(self.root,now=1100)
        self.assertEqual(doc['listener'],'heartbeat_stale')
        self.assertIn('not proof',render(doc))

    def test_legacy_status_does_not_invent_zero_second_progress(self):
        self.write('state/aaaaaaaaaaaa.json',{'chat':'chat','run':'a'*12,'phase':'QUESTION','results':[]})
        text=render(snapshot(self.root))
        self.assertIn('waiting_for_answer',text);self.assertIn('Last progress: unavailable',text)
        self.assertNotIn('Last progress: 0s',text)

    def test_planner_metrics_require_matching_run_and_attempt(self):
        row={'run':'a'*12,'attempt':'call1','step':'planner_running'}
        self.write('health.json',{'observability':{'tasks':{'chat':row}}})
        progress={'run_id':'a'*12,'attempt_id':'other','started':1000,'status':'running'}
        self.write('planner/planner_progress.json',progress)
        self.assertNotIn('planner',snapshot(self.root,now=1010)['tasks']['chat'])
        progress['attempt_id']='call1';self.write('planner/planner_progress.json',progress)
        self.assertEqual(snapshot(self.root,now=1010)['tasks']['chat']['planner']['elapsed_seconds'],10)

    def test_malformed_status_is_visible_without_traceback(self):
        (self.root/'health.json').write_text('{',encoding='utf-8')
        self.assertEqual(snapshot(self.root)['listener'],'invalid_health')


if __name__=='__main__':unittest.main()
