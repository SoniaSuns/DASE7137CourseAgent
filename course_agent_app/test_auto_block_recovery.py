import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import auto_block_recovery as recovery
from run_shared_chrome_agent import Runtime


class RepairGateTests(unittest.TestCase):
    def setup_patch(self,root):
        app=root/'app';app.mkdir();(app/'workflow.py').write_text('VALUE = 1\n')
        plan={'summary':'Fix value','changes':[
            {'path':'workflow.py','before_sha256':recovery.sha((app/'workflow.py').read_bytes()),'content':'VALUE = 2\n'},
            {'path':'test_new_regression.py','before_sha256':recovery.sha(b''),'content':'import unittest\nclass Regression(unittest.TestCase):\n def test_value(self):\n  import workflow\n  self.assertEqual(workflow.VALUE, 2)\n'}],
            'tests':['test_new_regression']}
        return app,plan

    def test_paths_existing_tests_and_stale_base_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,plan=self.setup_patch(Path(tmp));self.assertEqual(recovery.validate_patch(plan,app),['test_new_regression'])
            for name in ('../workflow.py','C:/outside.py','chrome_approval.py','auto_block_recovery.py','.env','reports/x.py'):
                with self.subTest(path=name),self.assertRaises(ValueError):recovery.allowed_path(name,app)
            (app/'test_new_regression.py').write_text('original test')
            with self.assertRaisesRegex(ValueError,'Source changed'):recovery.validate_patch(plan,app)
            (app/'test_new_regression.py').unlink();(app/'workflow.py').write_text('VALUE = 3\n')
            with self.assertRaisesRegex(ValueError,'Source changed'):recovery.validate_patch(plan,app)

    def test_execution_guard_methods_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            app,plan=self.setup_patch(Path(tmp));path=app/'run_shared_chrome_agent.py'
            path.write_text('def execute():\n return "verify"\n')
            plan['changes'][0]={'path':path.name,'before_sha256':recovery.sha(path.read_bytes()),'content':'def execute():\n return "skip"\n'}
            with self.assertRaisesRegex(ValueError,'protected'):recovery.validate_patch(plan,app)

    def test_activation_requires_verified_exact_patch_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app,plan=self.setup_patch(root);job=root/'job';job.mkdir()
            recovery.write(job/'patch.json',plan)
            recovery.write(job/'review.json',{k:True for k in ('accepted','preserves_approval','preserves_scope','preserves_readback','regression_covers_cause')})
            recovery.write(job/'validation.json',{'patch_sha256':'wrong','full_suite_exit':0,'regression_after_exit':0,'regression_before_failed':True})
            with self.assertRaisesRegex(ValueError,'does not match'):recovery.activate(job,app)
            recovery.write(job/'validation.json',{'patch_sha256':recovery.sha(json.dumps(plan,sort_keys=True).encode()),'full_suite_exit':0,'regression_after_exit':0,'regression_before_failed':True})
            recovery.activate(job,app)
            self.assertEqual((app/'workflow.py').read_text(),'VALUE = 2\n')
            self.assertEqual((job/'backup/workflow.py').read_text(),'VALUE = 1\n')

    def test_model_and_effort_are_pinned_and_not_inherited_from_planner(self):
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp);commands=[]
            def run(command,**kwargs):
                commands.append(command);recovery.write(job/'probe-output.json',{'ok':True});return Mock(returncode=0)
            with patch.object(recovery.subprocess,'run',side_effect=run):
                result=recovery.model_call(job,'probe',{},recovery.obj({'ok':{'type':'boolean'}}),
                    {'CODEX_BIN':__file__,'CODEX_MODEL':'wrong-model','CODEX_PLANNER_MODEL':'also-wrong','CODEX_DANGEROUSLY_BYPASS_APPROVALS_AND_SANDBOX':'1'})
            command=commands[0]
            self.assertEqual(command[command.index('--model')+1],'gpt-6-astra')
            self.assertIn('model_reasoning_effort="high"',command)
            self.assertEqual(command[command.index('--sandbox')+1],'read-only')
            self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',command)
            self.assertTrue(result['ok'])

    def test_failure_queue_is_durable_redacted_deduplicated_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state={'run':'a'*12,'task_id':'T-example','task':'Check source','conversation':['OK 123456'],'sender':'private'}
            job=recovery.enqueue(root,state,'same failure',start=False)
            self.assertEqual(job,recovery.enqueue(root,state,'same failure',start=False))
            data=(job/'request.json').read_text();self.assertNotIn('123456',data);self.assertNotIn('private',data)
            state['run']='b'*12;recovery.enqueue(root,state,'same failure',start=False)
            state['run']='c'*12;third=recovery.enqueue(root,state,'same failure',start=False)
            self.assertEqual(recovery.read(third/'result.json')['status'],'technical_review')

    def test_staged_regression_must_fail_before_and_pass_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app,plan=self.setup_patch(root);job=root/'job';job.mkdir()
            candidate=root/'candidate';candidate.mkdir();(candidate/'workflow.py').write_text('VALUE = 1\n')
            with patch.object(recovery,'validate_patch',return_value=plan['tests']),patch.object(recovery,'snapshot_project',return_value=candidate):
                recovery.stage_patch(job,plan)
            self.assertEqual((app/'workflow.py').read_text(),'VALUE = 1\n')
            self.assertEqual(recovery.read(job/'validation.json')['full_suite_exit'],0)
            self.assertIn('FAILED (failures=1)',(job/'regression-before.log').read_text())


class RecoveryRoutingTests(unittest.TestCase):
    def runtime(self,root,status):
        browser=Mock();browser.forms={};browser.targets={};browser.overviews={}
        runtime=Runtime(Mock(),browser,['chat'],[],Mock(),root/'state',Mock())
        runtime.auto_block_recovery=True;runtime.block_recovery_root=root/'jobs';runtime.send=Mock()
        flow=runtime.flows['chat'];flow.begin('Align files','staff','msg');flow.state['mention_authorized']=True;flow.fail('Bad plan')
        job=recovery.enqueue(runtime.block_recovery_root,flow.state,'Bad plan',start=False)
        flow.state['auto_recovery_job']=job.name
        recovery.write(job/'result.json',{'status':status,'diagnosis':{'category':'user_claim' if status=='question' else 'workflow',
            'summary':'Diagnosis','question':'Which of the two conflicting source versions should apply?','evidence':['The two supplied source rows disagree.']}})
        return runtime,flow,job

    def test_user_issue_becomes_specific_bound_question(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime,flow,job=self.runtime(Path(tmp),'question');runtime.service_block_recovery()
            self.assertEqual(flow.state['phase'],'QUESTION')
            self.assertEqual(flow.state['resume_phase'],'PLANNING')
            self.assertIn('two conflicting source',flow.state['pending_question'])
            self.assertTrue((job/'handled.json').exists())

    def test_retry_does_not_replay_write_or_old_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime,flow,job=self.runtime(Path(tmp),'retry');runtime.service_block_recovery()
            self.assertEqual(len(runtime.queue),1)
            self.assertEqual(runtime.queue[0]['task_id'],flow.state['task_id'])
            self.assertNotIn('selected',runtime.queue[0]);self.assertNotIn('code',runtime.queue[0])
            runtime.browser.execute.assert_not_called()

    def test_possible_save_new_input_and_other_active_task_defer_activation(self):
        for condition in ('write','queue','planner','approval'):
            with tempfile.TemporaryDirectory() as tmp,self.subTest(condition=condition):
                runtime,flow,job=self.runtime(Path(tmp),'ready')
                if condition=='write':flow.state['write_started']='uncertain'
                if condition=='queue':runtime.queue.append({'text':'new instruction'})
                if condition=='planner':runtime.jobs['chat']={'future':Mock()}
                if condition=='approval':flow.state['phase']='APPROVAL'
                with patch.object(recovery,'activate') as activate:runtime.service_block_recovery();activate.assert_not_called()
                self.assertIsNone(runtime.restart_recovery);self.assertFalse((job/'handled.json').exists())

    def test_verified_repair_requests_graceful_restart_only_at_idle_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime,flow,job=self.runtime(Path(tmp),'ready')
            with patch.object(recovery,'activate') as activate:runtime.service_block_recovery();activate.assert_called_once_with(job)
            self.assertTrue(runtime.stopped);self.assertEqual(runtime.restart_recovery['run'],flow.state['run'])
            self.assertFalse(runtime.restart_recovery['unwritten'])

    def test_blocked_message_queues_diagnosis_before_user_notification(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime,flow,job=self.runtime(Path(tmp),'retry')
            runtime.open=Mock();runtime.queue_block_recovery=Mock();runtime.send=Runtime.send.__get__(runtime)
            with patch('run_shared_chrome_agent.send_message',return_value={}) as send:
                runtime.send('chat','Status: Blocked\nReason: source mismatch')
            runtime.queue_block_recovery.assert_called_once()
            self.assertIn('automatic diagnosis',send.call_args.args[1])
            self.assertNotIn('Status: Blocked',send.call_args.args[1])
