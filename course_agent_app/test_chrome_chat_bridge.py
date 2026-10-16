"""Local transport must preserve the shared runtime's approval authority."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from chrome_chat_bridge import CHAT,SENDER,ChatStore,attach
from run_shared_chrome_agent import Runtime,WhatsAppIntakeUnavailable


class ChromeChatBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.runtime=Runtime(None,Mock(),['wa'],['140147','140148'],Mock(),self.root/'state',Mock())
        self.bridge=attach(self.runtime,self.root/'panel',port=0)
        self.flow=self.runtime.flows[CHAT]

    def tearDown(self):
        self.bridge.close();self.tmp.cleanup()

    def http(self,body=None,headers=None):
        h={'Origin':'chrome-extension://'+'a'*32,'Authorization':'Bearer '+self.bridge.config['token']}
        if body is not None:h['Content-Type']='application/json'
        h.update(headers or {})
        h={key:value for key,value in h.items() if value is not None}
        request=Request(f'http://127.0.0.1:{self.bridge.server.server_port}/v1/'+('messages' if body is not None else 'state'),
                        data=json.dumps(body).encode() if body is not None else None,headers=h)
        try:
            with urlopen(request,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as exc:
            with exc:return exc.code,json.load(exc)

    def plan(self):
        self.flow.begin('Change a confirmed setting',SENDER,'initial-message')
        self.flow.state['mention_authorized']=True
        operation={'id':'change','capability':'update_moodle_form','target':'140148:module:7',
            'course_id':'140148','title':'Example File','summary':'Change the confirmed field.',
            'scope':'requested','depends_on':[],'fields':[]}
        self.flow.offer({},[operation]);self.runtime.render_pending(CHAT);self.bridge.refresh(self.runtime)
        return self.bridge.store.snapshot()['approval']

    def message(self,text='Inspect course A',context=None,number=1):
        return {'id':f'message-number-{number:08d}','text':text,'context':context}

    def test_http_rejects_missing_token_web_origin_null_origin_and_wrong_host(self):
        for headers in ({'Authorization':''},{'Origin':'https://example.com'},{'Origin':'null'},{'Host':'evil.example'}):
            with self.subTest(headers=list(headers)):self.assertEqual(self.http(headers=headers)[0],403)
        status,body=self.http();self.assertEqual(status,200);self.assertEqual(body['phase'],'IDLE')

    def test_chrome_host_permission_fetch_without_origin_still_requires_private_token(self):
        self.assertEqual(self.http(headers={'Origin':None})[0],200)
        self.assertEqual(self.http(headers={'Origin':None,'Authorization':''})[0],403)
        self.assertEqual(self.http(self.message(),headers={'Origin':None})[0],202)

    def test_post_is_durable_and_idempotent_before_and_after_main_thread_drain(self):
        body=self.message()
        self.assertEqual(self.http(body)[0],202);self.assertEqual(self.runtime.queue,[])
        self.assertEqual(self.http(body)[1]['status'],'queued')
        self.assertEqual(self.bridge.store.snapshot()['pending_messages'],1)
        self.bridge.drain(self.runtime)
        self.assertEqual(len(self.runtime.queue),1)
        self.assertEqual(self.http(body)[1]['status'],'accepted')
        self.bridge.drain(self.runtime);self.assertEqual(len(self.runtime.queue),1)
        self.runtime.browser.overview.assert_not_called()

    def test_message_id_cannot_be_reused_for_different_content(self):
        self.http(self.message());self.assertEqual(self.http(self.message('Different request'))[0],400)

    def test_partial_context_is_rejected_before_persistence_and_listener_drain(self):
        for context in ({},{'run':'old'},{'run':'old','boundary':'old'},'invalid'):
            self.assertEqual(self.http(self.message(context=context))[0],400)
            self.bridge.drain(self.runtime)
        self.assertEqual(self.runtime.queue,[])
        self.assertEqual(self.bridge.store.snapshot()['messages'],[])

    def test_current_plan_confirmation_enters_existing_approval_authority_once(self):
        plan=self.plan();selected=copy.deepcopy(self.flow.state['selected'])
        self.runtime.execute=Mock()
        body=self.message('OK '+plan['code'],plan['context'])
        self.http(body);self.bridge.drain(self.runtime);self.runtime.handle_queue()
        self.assertEqual(self.flow.state['phase'],'APPROVED')
        self.assertEqual(self.flow.state['selected'],selected)
        self.runtime.execute.assert_called_once_with(CHAT)
        self.http(body);self.bridge.drain(self.runtime);self.runtime.handle_queue()
        self.runtime.execute.assert_called_once()

    def test_missing_stale_or_incorrect_plan_context_never_enters_execution_queue(self):
        plan=self.plan()
        contexts=[None,{**plan['context'],'run':'old'},{**plan['context'],'message_id':'old'},
                  {**plan['context'],'manifest_hash':'changed'}]
        for number,context in enumerate(contexts):
            self.http(self.message('OK '+plan['code'],context,number))
            self.bridge.drain(self.runtime)
            self.assertEqual(self.runtime.queue,[]);self.assertEqual(self.flow.state['phase'],'APPROVAL')

    def test_reply_that_becomes_stale_in_shared_queue_is_dropped(self):
        plan=self.plan();self.runtime.execute=Mock()
        self.http(self.message('OK '+plan['code'],plan['context']));self.bridge.drain(self.runtime)
        self.flow.state['reply_boundary']='changed';self.flow.save()
        self.runtime.handle_queue();self.runtime.execute.assert_not_called();self.assertEqual(self.runtime.queue,[])

    def test_other_channel_active_work_serializes_local_task(self):
        self.runtime.flows['wa'].state.update(phase='APPROVAL',sender='someone')
        self.runtime.start=Mock();self.http(self.message());self.bridge.drain(self.runtime)
        self.runtime.handle_queue();self.runtime.start.assert_not_called()
        self.assertEqual(len(self.runtime.queue),1);self.assertTrue(self.bridge.store.snapshot()['other_task_active'])

    def test_whatsapp_poll_failure_still_drains_local_requests(self):
        self.runtime.poll=Mock(side_effect=WhatsAppIntakeUnavailable('offline'))
        self.http(self.message());self.assertFalse(self.runtime.poll_safely())
        self.assertEqual(len(self.runtime.queue),1);self.assertIn(CHAT,self.runtime.readable_chats)

    def test_status_response_does_not_replace_current_plan_message(self):
        plan=self.plan()
        self.runtime.send(CHAT,'Status: Waiting',requester={'sender':SENDER})
        self.bridge.refresh(self.runtime)
        self.assertEqual(self.bridge.store.snapshot()['approval'],plan)

    def test_restart_marks_unconsumed_requests_interrupted_without_replay(self):
        self.http(self.message());store=ChatStore(self.root/'panel')
        self.assertEqual(store.snapshot()['interrupted_messages'],1)
        self.assertEqual(store.snapshot()['pending_messages'],0)

    def test_cancel_uses_current_plan_without_execution(self):
        plan=self.plan();self.runtime.execute=Mock()
        self.http(self.message('NO '+plan['code'],plan['context']));self.bridge.drain(self.runtime)
        self.runtime.handle_queue();self.runtime.execute.assert_not_called()
        self.assertEqual(self.flow.state['phase'],'CANCELLED')


if __name__=='__main__':unittest.main()
