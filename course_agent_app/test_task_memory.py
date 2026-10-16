import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from chrome_approval import ChromeApproval
from chrome_task_memory import TaskMemory,command,continuation,task_code
from run_shared_chrome_agent import Runtime


def state(n,phase='DONE'):
    return {'run':f'{n:012x}','chat':'chat','sender':'staff','task':'Original goal',
            'phase':phase,'created':n,'results':[],'conversation':['Keep existing times.']}


class TaskMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.memory=TaskMemory(self.root)

    def test_eleventh_evicts_oldest_and_does_not_reuse_code(self):
        for n in range(1,12):self.memory.record(state(n),register=True)
        self.assertEqual(len(self.memory.read()),10)
        with self.assertRaises(ValueError):self.memory.lookup('T-000000000001','chat','staff')
        self.assertEqual(self.memory.lookup('00000000000b','chat','staff')['run'],'00000000000b')
        self.memory.record(state(1))
        with self.assertRaises(ValueError):self.memory.lookup('000000000001','chat','staff')

    def test_active_oldest_protected_and_all_active_limit_rejected(self):
        self.memory.record(state(1,'NEEDS_READBACK'),register=True)
        for n in range(2,12):self.memory.record(state(n),register=True)
        self.assertEqual(len(self.memory.read()),10)
        self.memory.lookup('000000000001','chat','staff')
        with self.assertRaises(ValueError):self.memory.lookup('000000000002','chat','staff')
        self.memory.write([])
        for n in range(1,11):self.memory.record(state(n,'APPROVAL'),register=True)
        with self.assertRaises(ValueError):self.memory.record(state(11),register=True)
        self.assertEqual(len(self.memory.read()),10)

    def test_resume_same_slot_and_latest_attempt_survives_reload(self):
        original=state(1);self.memory.record(original,register=True)
        next_state={**state(2),'task_id':task_code(original)};self.memory.record(next_state)
        restored=TaskMemory(self.root)
        self.assertEqual(len(restored.read()),1)
        self.assertEqual(restored.lookup(original['run'],'chat','staff')['run'],next_state['run'])
        self.assertEqual(restored.lookup(next_state['run'],'chat','staff')['task_id'],task_code(original))

    def test_cross_chat_and_requester_are_not_visible(self):
        self.memory.record(state(1),register=True)
        for chat,sender in [('other','staff'),('chat','other')]:
            with self.assertRaises(ValueError):self.memory.lookup('000000000001',chat,sender)
            self.assertEqual(self.memory.visible(chat,sender),[])

    def test_legacy_migration_groups_retries_and_never_reimports_evicted(self):
        for n in range(1,13):
            (self.root/f'archived-run-{n:012x}.json').write_text(json.dumps(state(n)),encoding='utf-8')
        continued={**state(13),'retry_of':f'{12:012x}','phase':'EXPIRED'}
        (self.root/'aaaaaaaaaaaa.json').write_text(json.dumps(continued),encoding='utf-8')
        self.memory.migrate();self.assertEqual(len(self.memory.read()),10)
        result=self.memory.lookup(f'{12:012x}','chat','staff')
        self.assertEqual(result['run'],continued['run'])
        with self.assertRaises(ValueError):self.memory.lookup(f'{1:012x}','chat','staff')
        self.memory.migrate()
        with self.assertRaises(ValueError):self.memory.lookup(f'{1:012x}','chat','staff')

    def test_continuation_keeps_completed_and_remaining_not_authority(self):
        s=state(1,'FAILED');s.update(code='123456',approval_sent=True,manifest_hash='old',
            selected=[{'id':'done','target':'140148:module:1','fingerprint':'old','fields':[{'key':'id_name','value':'A','before':'B'}]},
                      {'id':'remaining','target':'140148:module:2','fields':[]}],
            results=[{'verified':True,'target':'A'}],write_started='done')
        result=continuation(s)
        self.assertEqual(len(result['verified_previous_results']),1)
        self.assertEqual(result['previously_selected_scope'][0]['id'],'remaining')
        self.assertEqual(result['completed_operation_memory'][0]['operation']['target'],'140148:module:1')
        for key in ('code','approval_sent','manifest_hash','fingerprint'):
            self.assertNotIn('"'+key+'"',json.dumps(result))

    def test_uncertain_write_never_replayed(self):
        for changes in ({'phase':'NEEDS_READBACK'},{'write_started':'unknown'},
                        {'results':[{'verified':False}],'selected':[{}]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):continuation({**state(1),**changes})

    def test_protocol_accepts_chinese_english_and_rejects_paths(self):
        self.assertEqual(command('继续任务 T-000000000001 保留时间'),('resume','T-000000000001','保留时间'))
        self.assertEqual(command('/resume 000000000001'),('resume','000000000001',''))
        self.assertEqual(command('/tasks'),('list','',''))
        self.assertEqual(command('/resume ../../secret'),('help','',''))
        self.assertIsNone(command('please change the deadlines'))

    def test_real_reported_resume_phrase_and_spacing_variants(self):
        for text in ('continue taskT-a881c3557b99','continue task T-a881c3557b99',
                     'Please continue T-a881c3557b99','继续任务T-a881c3557b99',
                     'resume T-a881c3557b99','/resume T-a881c3557b99'):
            with self.subTest(text=text):self.assertEqual(command(text),('resume','T-a881c3557b99',''))

    def test_unparsed_reference_does_not_become_new_goal(self):
        text='please use taskT-a881c3557b99 memory'
        self.assertEqual(command(text),('reference','T-a881c3557b99',text))
        for text in ('continue task invalid',
                     'continue T-a881c3557b99 and T-000000000001'):
            with self.subTest(text=text):self.assertEqual(command(text),('help','',''))

    def test_failed_continuation_preserves_remaining_scope_and_answer(self):
        s=state(1,'FAILED');s.update(previously_selected_scope=[{'target':'140148:module:2'}],
                                   task_memory_context={'previous_answer':'Previously checked syllabus'})
        memory=continuation(s)
        self.assertEqual(memory['previously_selected_scope'],s['previously_selected_scope'])
        self.assertEqual(memory['previous_answer'],'Previously checked syllabus')

    def test_archived_question_does_not_pin_a_slot(self):
        s=state(1,'QUESTION')
        (self.root/'archived-run-000000000001.json').write_text(json.dumps(s),encoding='utf-8')
        self.memory.migrate()
        self.assertEqual(self.memory.lookup(s['run'],'chat','staff')['phase'],'INTERRUPTED')

    def test_evicted_code_cannot_start_an_unregistered_continuation(self):
        flow=ChromeApproval(self.root/'live.json','chat')
        with self.assertRaises(ValueError):flow.begin('Task','staff','new',task_id='T-000000000001')
        self.assertEqual(flow.state,{})


class RuntimeTaskMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.browser=Mock();self.browser.forms={};self.browser.overviews={}
        self.browser.targets={'140148:module:1':{},'140148:module:2':{}}
        self.runtime=Runtime(Mock(),self.browser,['chat'],['140148'],Mock(),self.root,Mock(),mention_only_chats=['chat'])
        self.runtime.send=Mock(return_value=True);self.runtime.poll_safely=Mock(return_value=True)
        self.flow=self.runtime.flows['chat'];self.flow.begin('Original request','staff','first')
        self.flow.state.update(phase='EXPIRED',mention_authorized=True,conversation=['Keep dates'],
            selected=[{'id':'one','target':'140148:module:1','fields':[]},
                      {'id':'two','target':'140148:module:2','fields':[]}],
            results=[{'verified':True,'change':'done'}],write_started='one',approval_sent=True)
        self.flow.save();self.old=copy.deepcopy(self.flow.state)
        self.record={'id':'new','sender':'staff','requester_name':'Staff','text':'/resume '+task_code(self.old),'self_mentioned':True}

    def test_resume_preserves_identity_reads_forms_and_plans_without_saving(self):
        self.runtime.handle_task_command('chat',self.record,('resume',task_code(self.old),'Also keep names'))
        fresh=self.flow.state
        self.assertEqual(task_code(fresh),task_code(self.old));self.assertNotEqual(fresh['run'],self.old['run'])
        self.assertEqual(fresh['phase'],'PLANNING');self.assertNotIn('approval_sent',fresh)
        self.assertEqual(fresh['verified_previous_results'],self.old['results'])
        self.assertEqual(fresh['conversation'],['Keep dates','Also keep names'])
        self.assertEqual(fresh['previously_selected_scope'][0]['id'],'two')
        self.browser.execute.assert_not_called();self.assertEqual(self.browser.inspect.call_count,2)
        self.assertEqual(len(self.runtime.task_memory.read()),1)
        with self.assertRaises(ValueError):self.flow.approve('OK '+self.old['code'],'staff','old-approval')

    def test_command_runs_through_queue_and_does_not_allocate_new_slot(self):
        self.runtime.queue=[{'chat':'chat',**self.record}]
        self.runtime.handle_queue()
        self.assertEqual(self.flow.state['phase'],'PLANNING');self.assertEqual(self.runtime.queue,[])
        self.assertEqual(len(self.runtime.task_memory.read()),1)

    def test_no_mention_does_not_resume_expired_task(self):
        self.runtime.queue=[{'chat':'chat',**self.record,'self_mentioned':False}]
        self.runtime.handle_queue()
        self.assertEqual(self.flow.state['phase'],'EXPIRED');self.browser.inspect.assert_not_called()

    def test_resume_rejected_while_an_approval_is_active(self):
        self.flow.state['phase']='APPROVAL';self.flow.save()
        with self.assertRaises(ValueError):self.runtime.handle_task_command('chat',self.record,('resume',task_code(self.old),''))
        self.browser.inspect.assert_not_called()

    def test_same_task_resume_with_instruction_replans_instead_of_repeating_question(self):
        for instruction in ('Make all files through Week 6 visible.', '第六周及以前全部文件显示出来'):
            with self.subTest(instruction=instruction):
                self.runtime.jobs.clear()
                self.flow.state.update(phase='QUESTION',results=[],pending_question='Keep it hidden?',resume_phase='PLANNING')
                self.flow.state.pop('write_started',None);self.flow.save()
                record={**self.record,'id':'reply-'+instruction}
                self.runtime.handle_task_command('chat',record,('resume',task_code(self.old),instruction))
                self.assertEqual(self.flow.state['phase'],'PLANNING')
                self.assertEqual(self.flow.state['conversation'][-1],instruction)
                self.assertNotIn('pending_question',self.flow.state)
                self.assertEqual(self.runtime.jobs['chat']['context']['reply'],instruction)
                self.browser.execute.assert_not_called()

    def test_same_task_resume_without_instruction_keeps_question(self):
        self.flow.state.update(phase='QUESTION',results=[],pending_question='Which files?')
        self.flow.state.pop('write_started',None);self.flow.save()
        self.runtime.handle_task_command('chat',self.record,('resume',task_code(self.old),''))
        self.assertEqual(self.flow.state['phase'],'QUESTION');self.assertFalse(self.runtime.jobs)

    def test_verified_section_prerequisite_queues_same_goal_once_with_receipts(self):
        op={'id':'show-week','capability':'set_section_visibility','target':'140148:section:6',
            'course_id':'140148','params':{'action':'show'},'fields':[],'summary':'Show Week 6','scope':'requested'}
        self.flow.state.update(phase='DONE',selected=[op],results=[{'verified':True,'change':'Show Week 6'}],
            write_started='show-week',previously_selected_scope=[{'target':'140148:module:2','summary':'Move remaining file','scope':'requested'}])
        self.flow.save()
        self.assertTrue(self.runtime.continue_after_section_visibility('chat'))
        self.assertTrue(self.runtime.continue_after_section_visibility('chat'))
        self.assertEqual(len(self.runtime.queue),1)
        record=self.runtime.queue[0]
        self.assertEqual(record['task_id'],task_code(self.old))
        self.assertEqual(record['conversation'],['Keep dates'])
        self.assertEqual(record['previously_selected_scope'][0]['target'],'140148:module:2')
        self.assertTrue(record['verified_previous_results'][0]['verified'])
        self.assertNotIn('code',record);self.browser.execute.assert_not_called()

    def test_failed_browser_inspection_does_not_leave_planning_stuck(self):
        self.browser.inspect.side_effect=RuntimeError('Read failed')
        self.runtime.queue=[{'chat':'chat',**self.record}];self.runtime.handle_queue()
        self.assertEqual(self.flow.state['phase'],'FAILED');self.assertFalse(self.runtime.jobs)
        memory=continuation(self.flow.state)
        self.assertEqual(memory['previously_selected_scope'][0]['id'],'two')

    def test_new_approval_code_cannot_collide_with_source_code(self):
        old=int(self.old['code']);new=(old+1)%1000000
        with patch('chrome_approval.secrets.randbelow',side_effect=[old,new]):
            self.runtime.handle_task_command('chat',self.record,('resume',task_code(self.old),''))
        self.assertNotEqual(self.flow.state['code'],self.old['code'])

    def test_list_and_show_do_not_change_approval_boundary(self):
        self.flow.state.update(phase='APPROVAL',reply_boundary='boundary');self.flow.save()
        for action in ('list','show'):
            self.runtime.handle_task_command('chat',self.record,(action,task_code(self.old),''))
        self.assertEqual(self.flow.state['reply_boundary'],'boundary')
        self.assertEqual(self.flow.state['run'],self.old['run'])

    def test_status_is_read_only_and_does_not_reveal_another_requesters_task(self):
        before=copy.deepcopy(self.flow.state)
        self.runtime.handle_task_command('chat',self.record,('status','',''))
        self.assertIn(task_code(before),self.runtime.send.call_args.args[1])
        self.assertEqual(self.flow.state,before);self.runtime.pool.submit.assert_not_called()
        self.runtime.handle_task_command('chat',{**self.record,'sender':'other'},('status','',''))
        self.assertNotIn(task_code(before),self.runtime.send.call_args.args[1])

    def test_new_requester_can_list_without_prior_flow_authorization(self):
        self.flow.state={};self.runtime.send=Runtime.send.__get__(self.runtime)
        self.runtime.open=Mock()
        with patch('run_shared_chrome_agent.send_message',return_value={'verified_message_ids':['id']}) as send:
            self.runtime.handle_task_command('chat',self.record,('list','',''))
        send.assert_called_once();self.assertEqual(send.call_args.kwargs['mention_name'],'Staff')


if __name__=='__main__':unittest.main()
