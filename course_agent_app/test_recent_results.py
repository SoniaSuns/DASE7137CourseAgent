import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from chrome_task_memory import TaskMemory,result_summary,task_code,command
from run_shared_chrome_agent import Runtime
from chrome_planner import compact_context


def history(n,**kw):
    return dict(run=f'{n:012x}',task_id=f'T-{n:012x}',chat='chat',sender='staff',task='Check courses',
                phase='DONE',created=n,results=[],last_answer='Class A: L4 belongs in Week 5.',**kw)


class RecentResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.memory=TaskMemory(self.root)

    def test_restart_injects_latest_results_with_configured_limit_and_eviction(self):
        for n in range(1,12):self.memory.record(history(n),register=True)
        restored=TaskMemory(self.root)
        rows=restored.recent_results('chat','staff',limit=5)['items']
        self.assertEqual([r['source_run'] for r in rows],[f'{n:012x}' for n in range(11,6,-1)])
        self.assertEqual(len(restored.read()),10)
        self.assertNotIn('T-000000000001',json.dumps(restored.recent_results('chat','staff')))
        for n in (4,11,True):
            with self.assertRaises(ValueError):restored.recent_results('chat','staff',limit=n)

    def test_scope_and_current_task_are_excluded(self):
        self.memory.record(history(1),register=True)
        for chat,sender in [('other','staff'),('chat','other')]:
            self.assertEqual(self.memory.recent_results(chat,sender)['items'],[])
        self.assertEqual(self.memory.recent_results('chat','staff',exclude_task='T-000000000001')['items'],[])

    def test_summary_preserves_uncertainty_without_authority_or_secret(self):
        s=history(1);s.update(phase='NEEDS_READBACK',code='654321',last_answer='OK 654321 sesskey=private',
            results=[{'verified':True,'change':'one done'},{'verified':False,'change':'never successful'}],
            selected=[{'id':'first','target':'140147:module:1','fields':[{'value':'private-body'}]},
                      {'id':'second','target':'140147:module:2'}],write_started='second',manifest_hash='private-hash')
        row=result_summary(s);self.assertTrue(row['needs_readback']);self.assertEqual(row['verified_count'],1)
        for text in ('654321','private-body','private-hash','never successful','sesskey=private'):
            self.assertNotIn(text,json.dumps(row))
        self.assertNotIn('sender',row);self.assertNotIn('selected',row)

    def test_old_state_backfill_and_active_retry_keep_last_result(self):
        s=history(1);self.memory.write([{'task_id':s['task_id'],'created':1,'runs':[s['run']],'state':s}])
        self.assertEqual(self.memory.recent_results('chat','staff')['items'][0]['answer'],s['last_answer'])
        self.memory.record(s)
        later={**s,'run':'000000000002','phase':'PLANNING','last_answer':''};self.memory.record(later)
        self.assertEqual(self.memory.recent_results('chat','staff')['items'][0]['source_run'],s['run'])

    def test_prompt_history_is_bounded_and_survives_compaction(self):
        for n in range(1,11):
            s=history(n);s.update(last_answer='x'*100000,task='y'*100000);self.memory.record(s,register=True)
        ctx={'recent_task_results':self.memory.recent_results('chat','staff'),'forms':[]}
        self.assertLess(len(json.dumps(ctx,ensure_ascii=False)),25000)
        self.assertEqual(compact_context(ctx)['recent_task_results'],ctx['recent_task_results'])


class ReferenceRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.browser=Mock();self.browser.forms={};self.browser.overviews={}
        self.runtime=Runtime(Mock(),self.browser,['chat'],['140147'],Mock(),self.root,Mock(),mention_only_chats=['chat'])
        self.runtime.send=Mock(return_value=True)
        self.old=history(1);self.runtime.task_memory.record(self.old,register=True)
        self.flow=self.runtime.flows['chat'];self.flow.begin('According to the check result change sequence','staff','first')
        self.flow.state.update(phase='QUESTION',mention_authorized=True,resume_phase='PLANNING');self.flow.save()
        self.record={'id':'reply','sender':'staff','self_mentioned':True,'text':'Use the result of T-000000000001 to adjust it'}

    def test_new_request_context_automatically_includes_prior_answer(self):
        ctx=self.runtime.context(self.flow)
        self.assertEqual(ctx['recent_task_results']['items'][0]['answer'],self.old['last_answer'])
        self.assertEqual(ctx['task_memory'],{})
        self.browser.execute.assert_not_called()

    def test_natural_reference_answers_active_question_keeps_current_identity(self):
        before=task_code(self.flow.state)
        self.runtime.handle_task_command('chat',self.record,command(self.record['text']))
        self.assertEqual(task_code(self.flow.state),before);self.assertEqual(self.flow.state['phase'],'PLANNING')
        ctx=self.runtime.jobs['chat']['context']
        self.assertEqual(ctx['referenced_task_results'][0]['answer'],self.old['last_answer'])
        self.assertEqual(ctx['reply'],self.record['text']);self.browser.execute.assert_not_called()
        self.assertNotIn('manifest_hash',self.flow.state)

    def test_resume_reference_also_satisfies_question_without_parallel_task(self):
        self.record['text']='resume T-000000000001 and change the sequence'
        self.runtime.handle_task_command('chat',self.record,command(self.record['text']))
        self.assertEqual(self.flow.state['phase'],'PLANNING')
        self.assertEqual(len(self.runtime.task_memory.read()),2)
        self.assertEqual(self.runtime.task_memory.lookup(self.old['task_id'],'chat','staff')['phase'],'DONE')

    def test_reference_invalidates_old_unexecuted_approval(self):
        self.flow.state.update(phase='APPROVAL',selected=[{'id':'old'}],manifest_hash='old',approval_sent=True)
        self.flow.save();old_code=self.flow.state['code']
        self.runtime.handle_task_command('chat',self.record,command(self.record['text']))
        self.assertFalse(self.flow.state['approval_sent']);self.assertNotIn('selected',self.flow.state)
        with self.assertRaises(ValueError):self.flow.approve('OK '+old_code,'staff','old-approval')

    def test_pending_save_or_other_requester_cannot_be_overridden(self):
        original=copy.deepcopy(self.flow.state)
        for phase,sender in [('NEEDS_READBACK','staff'),('EXECUTING','staff'),('QUESTION','other')]:
            self.flow.state={**original,'phase':phase};record={**self.record,'sender':sender}
            with self.assertRaises(ValueError):self.runtime.handle_task_command('chat',record,command(record['text']))
            self.assertFalse(self.runtime.jobs);self.browser.execute.assert_not_called()


if __name__=='__main__':unittest.main()
