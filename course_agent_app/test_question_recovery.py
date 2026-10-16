import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from question_recovery import validate_question_recovery
from run_shared_chrome_agent import Runtime


class QuestionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state={'chat':'chat','run':'abcdef123456','sender':'staff','task':'Original task',
            'phase':'QUESTION','resume_phase':'PLANNING','mention_authorized':True,
            'pending_question':'Move `File`?','message_ids':['request'],'results':[],
            'verified_previous_results':[{'verified':True}],
            'pending_delivery':{'phase':'QUESTION','text':'Move `File`?','parts':['Move `File`?'],
                'next_part':0,'part_receipts':[],'action_required':True,'send_attempted':True,
                'is_approval':False,'is_scope':False,'exclude_ids':['old']}}
        self.reply={'chat':'chat','id':'answer','sender':'staff','text':'Yes, move the file.', 'self_mentioned':True}

    def test_accepts_only_exact_unapproved_run_and_authenticated_answer(self):
        state,reply=validate_question_recovery([self.state],[self.reply],self.state['run'])
        self.assertIs(state,self.state);self.assertIs(reply,self.reply)
        for change in ({'phase':'APPROVAL'},{'selected':[{}]},{'write_started':'maybe'},
                       {'results':[{}]},{'approval_sent':True},{'manifest_hash':'approved'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_question_recovery([{**self.state,**change}],[self.reply],self.state['run'])
        for change in ({'sender':'other'},{'chat':'other'},{'self_mentioned':False},
                       {'text':'OK 123456'},{'text':'/resume abcdef123456'},
                       {'reply_boundary':'old'},{'id':'request'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_question_recovery([self.state],[{**self.reply,**change}],self.state['run'])

    def test_other_work_or_multipart_delivery_blocks_exception(self):
        for queue in ([],[self.reply,self.reply]):
            with self.assertRaises(ValueError):validate_question_recovery([self.state],queue,self.state['run'])
        with self.assertRaises(ValueError):
            validate_question_recovery([self.state,{'phase':'EXECUTING'}],[self.reply],self.state['run'])
        self.state['pending_delivery']['parts'].append('second')
        with self.assertRaises(ValueError):validate_question_recovery([self.state],[self.reply],self.state['run'])

    def test_missing_or_invalid_delivery_baseline_rejected_before_restart(self):
        for value in (None,'old',[''],[123]):
            self.state['pending_delivery']['exclude_ids']=value
            with self.assertRaises(ValueError):
                validate_question_recovery([self.state],[self.reply],self.state['run'])

    def test_restores_answer_only_after_unique_readback_and_keeps_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=Runtime(Mock(),Mock(),['chat'],[],Mock(),root,Mock())
            flow=runtime.flows['chat'];self.state['phase']='INTERRUPTED';flow.state=copy.deepcopy(self.state)
            runtime.interrupted_queue_path=root/'archived.json'
            runtime.interrupted_queue_path.write_text(json.dumps([self.reply]),encoding='utf-8')
            runtime.open=Mock();flow.save=Mock()
            for matches in (set(),{'one','two'}):
                with patch('run_shared_chrome_agent.matching_outgoing_ids',return_value=matches),self.assertRaises(ValueError):
                    runtime.recover_question_reply(self.state['run'])
                self.assertIn('pending_delivery',flow.state);self.assertEqual(runtime.queue,[])
            with patch('run_shared_chrome_agent.matching_outgoing_ids',return_value={'new'}) as match:
                runtime.recover_question_reply(self.state['run'])
            self.assertEqual(runtime.queue,[self.reply])
            self.assertEqual(flow.state['phase'],'QUESTION')
            self.assertEqual(flow.state['verified_previous_results'],[{'verified':True}])
            self.assertNotIn('pending_delivery',flow.state)
            self.assertEqual(flow.state['delivery_verified_ids'],['new'])
            self.assertEqual(match.call_args.kwargs['exclude_ids'],['old'])
            runtime.browser.execute.assert_not_called()
            runtime.browser.overviews={};runtime.browser.forms={};runtime.submit=Mock()
            runtime.handle_queue()
            self.assertEqual(runtime.queue,[])
            self.assertEqual(flow.state['phase'],'PLANNING')
            self.assertEqual(flow.state['run'],self.state['run'])
            self.assertEqual(flow.state['conversation'],[self.reply['text']])
            self.assertEqual(flow.state['verified_previous_results'],[{'verified':True}])
            runtime.submit.assert_called_once()
            runtime.browser.execute.assert_not_called()


if __name__=='__main__':unittest.main()
