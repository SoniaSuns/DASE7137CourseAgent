import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from run_shared_chrome_agent import Runtime
from chrome_moodle import digest
from chrome_task_memory import task_code


class ApprovalRenewalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.browser=Mock();self.browser.forms={}
        self.runtime=Runtime(Mock(),self.browser,['chat'],[],Mock(),Path(self.temp.name),Mock())
        self.runtime.send=Mock(return_value=True);self.runtime.poll_safely=Mock(return_value=True)
        self.flow=self.runtime.flows['chat'];self.flow.begin('Rename the selected section','staff','original')
        self.op={'id':'one','capability':'update_moodle_form','target':'140148:section:1','course_id':'140148','title':'Old label',
                 'summary':'Rename section to Week 5','scope':'requested','depends_on':[],'params':{},'fields':[{'key':'id_name','value':'Week 5'}]}
        self.flow.state['mention_authorized']=True;self.flow.offer({},[self.op]);self.flow.state['phase']='INTERRUPTED';self.flow.save()
        self.browser.resolve.side_effect=lambda op:{**op,'title':'Clean name','course_id':'140148','fields':[{'key':'id_name','label':'Section name','type':'text','before':'Topic 5','value':'Week 5'}]}

    def test_renewal_preserves_exact_scope_and_never_saves(self):
        previous=self.flow.state['run'];self.runtime.renew_approval(previous)
        self.assertNotEqual(self.flow.state['run'],previous);self.assertEqual(self.flow.state['renewed_from'],previous)
        self.assertTrue(self.flow.state['approval_sent']);self.assertEqual(self.flow.state['selected'][0]['fields'][0]['value'],'Week 5')
        self.assertEqual(self.flow.state['selected'][0]['target'],self.op['target']);self.browser.execute.assert_not_called()
        self.browser.inspect.assert_called_once_with(self.op['target']);self.runtime.send.assert_called_once()

    def test_uncertain_write_cannot_be_renewed(self):
        self.flow.state['write_started']='one'
        with self.assertRaises(ValueError):self.runtime.renew_approval(self.flow.state['run'])
        self.runtime.send.assert_not_called()

    def test_renewal_keeps_remaining_goal_and_reference_context(self):
        remaining=[{'target':'140148:module:4','summary':'Move remaining file','scope':'requested'}]
        self.flow.state.update(previously_selected_scope=remaining,
            referenced_task_results=[{'task_id':'T-aaaaaaaaaaaa','answer':'Earlier check'}],
            readback_repair={'move_replay_forbidden':True,'target':'140148:module:3'})
        self.runtime.renew_approval(self.flow.state['run'])
        self.assertEqual(self.flow.state['previously_selected_scope'],remaining)
        self.assertEqual(len(self.flow.state['referenced_task_results']),1)
        self.assertTrue(self.flow.state['readback_repair']['move_replay_forbidden'])
        self.browser.execute.assert_not_called()

    def test_renewal_restores_scope_from_legacy_prerequisite_memory(self):
        remaining=[{'target':'140148:module:4','summary':'Move remaining file','scope':'requested'}]
        self.flow.state['task_memory_context']={'previously_selected_scope':remaining}
        self.runtime.renew_approval(self.flow.state['run'])
        self.assertEqual(self.flow.state['previously_selected_scope'],remaining)

    def test_tampered_scope_cannot_be_renewed(self):
        self.flow.state['selected'][0]['target']='140148:section:2'
        with self.assertRaises(ValueError):self.runtime.renew_approval(self.flow.state['run'])
        self.runtime.send.assert_not_called()

    def test_plain_resume_uses_exact_scope_without_planner_or_new_identity(self):
        old=copy.deepcopy(self.flow.state)
        record={'id':'new-command','sender':'staff','self_mentioned':True,'text':'continue task'+task_code(old)}
        self.runtime.queue=[{'chat':'chat',**record}];self.runtime.handle_queue()
        self.assertEqual(task_code(self.flow.state),task_code(old))
        self.assertNotEqual(self.flow.state['run'],old['run'])
        self.assertEqual(self.flow.state['phase'],'APPROVAL');self.assertTrue(self.flow.state['approval_sent'])
        self.assertEqual([o['target'] for o in self.flow.state['selected']],[o['target'] for o in old['selected']])
        self.assertFalse(self.runtime.jobs);self.runtime.pool.submit.assert_not_called()
        self.browser.execute.assert_not_called()

    def test_resume_skips_receipted_operation_and_keeps_remaining_dependency_order(self):
        self.flow.state['selected'].append({**self.op,'id':'two','target':'140148:section:2','depends_on':['one']})
        self.flow.state.update(manifest_hash=digest(self.flow.state['selected']),results=[{'verified':True,'change':'One done'}],write_started='one')
        self.flow.save();old=copy.deepcopy(self.flow.state)
        record={'id':'new-command','sender':'staff','self_mentioned':True,'text':'continue task'+task_code(old)}
        self.runtime.queue=[{'chat':'chat',**record}];self.runtime.handle_queue()
        self.assertEqual([o['id'] for o in self.flow.state['selected']],['two'])
        self.assertEqual(self.flow.state['selected'][0]['depends_on'],[])
        self.assertEqual(len(self.flow.state['verified_previous_results']),1)
        self.browser.execute.assert_not_called()

    def test_uncertain_part_requires_readback_before_continuing(self):
        state=self.flow.state;state['phase']='APPROVAL';state['approval_sent']=False
        pending={'text':'whole review','parts':['first','second','last'],'next_part':1,
                 'part_receipts':[['first-id']],'send_attempted':True,'exclude_ids':['old'],
                 'phase':'APPROVAL','action_required':True,'is_approval':True,'is_scope':False}
        state['pending_delivery']=pending;self.runtime.open=Mock()
        with patch('run_shared_chrome_agent.matching_outgoing_ids',return_value=set()):
            self.runtime.reconcile_deliveries()
        self.runtime.send.assert_not_called();self.assertFalse(state['approval_sent'])
        def finish(chat,text,*,delivery):
            self.assertEqual(delivery['next_part'],2)
            self.assertEqual(delivery['part_receipts'],[['first-id'],['second-id']])
            state['delivery_verified_ids']=['first-id','second-id','last-id'];return True
        self.runtime.send.side_effect=finish
        with patch('run_shared_chrome_agent.matching_outgoing_ids',return_value={'second-id'}) as readback:
            self.runtime.reconcile_deliveries()
        self.assertEqual(readback.call_args.args[1],'second')
        self.assertTrue(state['approval_sent']);self.assertNotIn('pending_delivery',state)
        self.assertEqual(len(state['delivery_verified_ids']),3)

    def test_approval_has_no_time_limit_by_default(self):
        self.flow.state.update(phase='APPROVAL',expires=0,approval_sent=False)
        self.flow.mark_sent()
        self.assertIsNone(self.flow.state['expires'])
        self.flow.state['code']='123456'
        with patch('chrome_approval.time.time',return_value=10**12):
            self.assertTrue(self.flow.approve('OK 123456',self.flow.state['sender'],'late-ok'))
        self.assertEqual(self.flow.state['phase'],'APPROVED')

    def test_configured_approval_window_starts_after_delivery(self):
        self.flow.state.update(phase='APPROVAL',expires=0,approval_sent=False)
        with patch.object(type(self.flow),'approval_timeout',1800):
            with patch('chrome_approval.time.time',return_value=10000):self.flow.mark_sent()
        self.assertEqual(self.flow.state['expires'],11800)
        self.flow.state['code']='123456'
        with patch('chrome_approval.time.time',return_value=11801):
            with self.assertRaisesRegex(ValueError,'Approval expired'):
                self.flow.approve('OK 123456',self.flow.state['sender'],'late-ok')
        self.assertEqual(self.flow.state['phase'],'EXPIRED')
