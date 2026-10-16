import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chrome_mentions import MESSAGE_BODY_SCRIPT
from run_shared_chrome_agent import Runtime


class MentionDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()

    def setUp(self):
        self.ctx=self.browser.new_context();self.page=self.ctx.new_page()
        self.page.route('**/*',lambda route:route.fulfill(content_type='text/html',body='<body></body>'))
        self.page.goto('https://fixture.invalid')
        self.page.evaluate("localStorage.setItem('last-wid-md',JSON.stringify('123456789:3@c.us'))")

    def tearDown(self):self.ctx.close()

    def record(self,content):
        self.page.set_content('<div data-id="message"><div data-pre-plain-text="[14:00, 2026/10/6] Staff:"><span class="selectable-text">'+content+'</span></div></div>')
        return self.page.evaluate(MESSAGE_BODY_SCRIPT,['message'])['message']

    def test_real_self_mention_with_name_and_jid(self):
        record=self.record('<span data-mention-jid="123456789@s.whatsapp.net">@Agent</span> 请修改 Class B 的 due date')
        self.assertTrue(record['self_mentioned'])
        self.assertEqual(record['addressed_text'],'请修改 Class B 的 due date')
        self.assertNotIn('123456789',json.dumps(record))

    def test_numeric_structured_mention_and_approval(self):
        record=self.record('<span class="matched-mention">@+123 456 789</span> OK 123456')
        self.assertTrue(record['self_mentioned'])
        self.assertEqual(record['addressed_text'],'OK 123456')

    def test_edited_duplicate_phone_link_and_multiple_mentions(self):
        for instruction in ['ignore L3, keep its original place', '请把 prototype 文件移到对应周']:
            body=('<span data-mention-jid="123456789@c.us">@Agent</span>'
                  '<a>+123456789</a>\u202c\u202c '
                  '<span class="matched-mention">@+123456789</span> '+instruction)
            record=self.record(body)
            self.assertTrue(record['self_mentioned'])
            self.assertEqual(record['addressed_text'],instruction)

    def test_duplicate_cleanup_preserves_other_phone_and_plain_text_not_trigger(self):
        record=self.record('<span data-mention-jid="123456789@c.us">@Agent</span><a>+987654321</a> call this number')
        self.assertIn('+987654321',record['addressed_text'])
        self.assertFalse(self.record('@123456789<a>+123456789</a> update dates')['self_mentioned'])

    def test_rich_text_list_metadata_does_not_replace_message_body(self):
        record=self.record('<span class="matched-mention">@+123456789</span> Change these:<ul><li>'
                           '<span class="selectable-text" data-pre-plain-text="- ">Week 5 files</span></li></ul>')
        self.assertTrue(record['self_mentioned'])
        self.assertIn('Change these:',record['addressed_text'])
        self.assertIn('Week 5 files',record['addressed_text'])
        self.assertIn('14:00',record['meta'])

    def test_plain_text_other_account_and_quoted_mentions_are_ignored(self):
        for body in ['@123456789 Change the deadline',
                     '<span class="matched-mention">@987654321</span> Change deadline',
                     '<span data-mention-jid="987654321@c.us">@Agent</span> 请修改',
                     '<span data-testid="quoted-message"><span data-mention-jid="123456789@c.us">@Agent</span></span> Thanks']:
            with self.subTest(body=body):self.assertFalse(self.record(body)['self_mentioned'])

    def test_no_identity_never_guesses(self):
        self.page.evaluate('localStorage.clear()')
        record=self.record('<span data-mention-jid="123456789@c.us">@Agent</span> Change date')
        self.assertFalse(record['self_mentioned'])
        self.assertFalse(record['account_identity_available'])

    def test_v1_observed_profile_name_on_real_marker_only(self):
        for body,expected in [('<span class="matched-mention">@Course Agent</span> Update dates',True),
                              ('@Course Agent Update dates',False),
                              ('<span data-mention-jid="987654321@c.us">@Course Agent</span> Update dates',False)]:
            self.record(body)
            result=self.page.evaluate(MESSAGE_BODY_SCRIPT,{'ids':['message'],'account_name':'Course Agent'})['message']
            self.assertEqual(result['self_mentioned'],expected)

    def test_live_ui_role_button_mention_without_legacy_classes(self):
        for body,expected in [('<span role="button" tabindex="0" class="xtnn1bt">@Course Agent</span> 请检查作业',True),
                              ('<span role="button" tabindex="0">@Someone Else</span> Please check assignment',False),
                              ('<span>@Course Agent</span> Please check assignment',False),
                              ('<span data-testid="quoted-message"><span role="button" tabindex="0">@Course Agent</span></span> Thanks',False)]:
            self.record(body)
            result=self.page.evaluate(MESSAGE_BODY_SCRIPT,{'ids':['message'],'account_name':'Course Agent'})['message']
            self.assertEqual(result['self_mentioned'],expected)

    def test_chinese_group_header_does_not_default_to_direct(self):
        from chrome_mentions import observed_chat_type
        self.page.set_content('<div id="main"><header><button aria-label="群组视频通话"></button></header><div data-id="opaque-id"></div></div>')
        self.assertEqual(observed_chat_type(self.page),'group')
        self.page.set_content('<div id="main"><header></header></div>')
        self.assertEqual(observed_chat_type(self.page),'unknown')

    def test_profile_identity_read_returns_to_chat_list(self):
        from chrome_mentions import discover_account_name
        self.page.set_content('''<header><button aria-label="对话" onclick="document.getElementById('pane-side').style.display='block'">Chats</button></header>
            <div><span dir="auto">Course Agent</span><button aria-label="编辑“姓名”">Edit</button></div>
            <div id="pane-side" style="display:none">Chat list</div>''')
        self.assertEqual(discover_account_name(self.page),'Course Agent')
        self.assertTrue(self.page.locator('#pane-side').is_visible())

    def test_current_own_drawer_name_strips_real_mention_from_approval(self):
        from chrome_mentions import discover_account_name
        self.page.set_content('''<div data-testid="me-tab-drawer"><div data-testid="drawer-title-body">
            <span dir="auto">Example Operator</span></div></div>''')
        name=discover_account_name(self.page)
        self.assertEqual(name,'Example Operator')
        self.record('<span role="button" tabindex="0">@Example Operator</span> OK 123456')
        record=self.page.evaluate(MESSAGE_BODY_SCRIPT,{'ids':['message'],'account_name':name})['message']
        self.assertTrue(record['self_mentioned']);self.assertEqual(record['addressed_text'],'OK 123456')

    def test_other_drawer_title_is_not_account_identity(self):
        from chrome_mentions import discover_account_name
        self.page.set_content('<div data-testid="drawer-title-body"><span dir="auto">Other Member</span></div>')
        self.assertEqual(discover_account_name(self.page),'')

    def test_profile_cleanup_timeout_keeps_the_observed_identity(self):
        from chrome_mentions import discover_account_name
        from playwright.sync_api import TimeoutError as BrowserTimeout
        self.page.set_content('<div data-testid="me-tab-drawer"><div data-testid="drawer-title-body"><span dir="auto">Observed Operator</span></div></div>')
        with patch.object(self.page.keyboard,'press',side_effect=BrowserTimeout('drawer transition')):
            self.assertEqual(discover_account_name(self.page),'Observed Operator')

    def test_profile_read_timeout_returns_unknown_without_guessing(self):
        from chrome_mentions import discover_account_name
        from playwright.sync_api import TimeoutError as BrowserTimeout
        with patch.object(self.page,'evaluate',side_effect=BrowserTimeout('profile unavailable')):
            self.assertEqual(discover_account_name(self.page),'')


class MentionIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.runtime=Runtime(Mock(),Mock(),['group','test approval'],[],Mock(),Path(self.temp.name),Mock(),mention_only_chats=['group'])
        self.runtime.open=Mock()

    def tearDown(self):self.temp.cleanup()

    def test_operator_retry_starts_exact_failed_task_with_fresh_approval(self):
        flow=self.waiting('FAILED');old=dict(flow.state)
        flow.state['pending_delivery']={'text':'Old failed notification','phase':'FAILED'}
        self.runtime.send=Mock();self.runtime.submit=Mock()
        self.runtime.browser.overviews={}
        self.runtime.browser.forms={}
        self.runtime.retry_failed_run(old['run'])
        self.assertNotIn('pending_delivery',flow.state)
        archived=list(self.runtime.root.glob('retried-*.json'))
        self.assertEqual(json.loads(archived[0].read_text(encoding='utf-8'))['pending_delivery']['text'],'Old failed notification')
        self.assertEqual(self.runtime.queue[0]['chat'],'group')
        self.runtime.handle_queue()
        self.assertEqual(flow.state['task'],old['task'])
        self.assertEqual(flow.state['sender'],old['sender'])
        self.assertEqual(flow.state['message_ids'],old['message_ids'])
        self.assertEqual(flow.state['retry_of'],old['run'])
        self.assertNotEqual(flow.state['run'],old['run'])
        self.assertEqual(flow.state['phase'],'PLANNING')
        self.assertNotIn('approval_sent',flow.state)
        with self.assertRaises(ValueError):flow.approve('OK '+old['code'],old['sender'],'new')
        with self.assertRaises(ValueError):self.runtime.retry_failed_run(old['run'])

    def test_retry_rejects_unmentioned_wrong_run_and_possible_writes(self):
        flow=self.waiting('FAILED');original=dict(flow.state)
        for changes in ({'mention_authorized':False},{'selected':[]},{'write_started':'op'},
                        {'results':[{'verified':True}]},{'phase':'NEEDS_READBACK'}):
            with self.subTest(changes=changes):
                flow.state={**original,**changes}
                with self.assertRaises(ValueError):self.runtime.retry_failed_run(original['run'])
                self.assertEqual(self.runtime.queue,[])
        flow.state=original
        with self.assertRaises(ValueError):self.runtime.retry_failed_run('wrong-run')

    def test_delivery_retry_preserves_refinements_and_scope_but_not_approval(self):
        from chrome_moodle import digest
        flow=self.waiting('FAILED')
        selected=[{'target':'140147:module:1','summary':'Change future dates','scope':'requested'}]
        flow.state.update(selected=selected,manifest_hash=digest(selected),approval_sent=False,
                          conversation=['Keep time of day; future items only.'],message_ids=['root','followup'])
        self.runtime.retry_failed_run(flow.state['run'],undelivered=True)
        record=self.runtime.queue[0]
        self.assertEqual(record['conversation'],flow.state['conversation'])
        self.assertEqual(record['previously_selected_scope'],selected)
        self.assertNotIn('approval_sent',record)
        self.assertNotIn('code',record)

    def test_delivery_retry_rejects_a_delivered_or_changed_manifest(self):
        from chrome_moodle import digest
        flow=self.waiting('FAILED')
        selected=[{'target':'140147:module:1','summary':'Change dates','scope':'requested'}]
        flow.state.update(selected=selected,manifest_hash=digest(selected),approval_sent=True)
        with self.assertRaises(ValueError):self.runtime.retry_failed_run(flow.state['run'],undelivered=True)
        flow.state['approval_sent']=False;flow.state['manifest_hash']='changed'
        with self.assertRaises(ValueError):self.runtime.retry_failed_run(flow.state['run'],undelivered=True)

    def test_operator_replan_of_unwritten_approved_failure_needs_new_approval(self):
        from chrome_moodle import digest
        flow=self.waiting('FAILED');old=dict(flow.state)
        selected=[{'target':'140147:module:1','summary':'Change future dates','scope':'requested'}]
        flow.state.update(selected=selected,manifest_hash=digest(selected),approval_sent=True,
                          conversation=['All except tutorial 4.'])
        self.runtime.send=Mock();self.runtime.submit=Mock()
        self.runtime.browser.overviews={};self.runtime.browser.forms={}
        self.runtime.retry_failed_run(flow.state['run'],unwritten=True)
        self.runtime.handle_queue()
        self.assertEqual(flow.state['phase'],'PLANNING')
        self.assertEqual(flow.state['conversation'],['All except tutorial 4.'])
        self.assertEqual(flow.state['previously_selected_scope'],selected)
        self.assertNotEqual(flow.state['run'],old['run'])
        with self.assertRaises(ValueError):flow.approve('OK '+old['code'],old['sender'],'new')
        self.runtime.browser.execute.assert_not_called()

    def test_unwritten_replan_rejects_any_possible_save_or_changed_manifest(self):
        from chrome_moodle import digest
        flow=self.waiting('FAILED');selected=[{'target':'x','scope':'requested','summary':'Change'}]
        flow.state.update(selected=selected,manifest_hash=digest(selected),approval_sent=True)
        original=dict(flow.state)
        for changes in ({'write_started':None},{'results':[{'verified':True}]},
                        {'phase':'NEEDS_READBACK'},{'manifest_hash':'changed'},{'mention_authorized':False}):
            with self.subTest(changes=changes):
                flow.state={**original,**changes}
                with self.assertRaises(ValueError):self.runtime.retry_failed_run(flow.state['run'],unwritten=True)
                self.assertEqual(self.runtime.queue,[])
    def test_uncertain_approval_is_read_back_without_resend_or_permission(self):
        from chrome_approval import approval_message,ChromeApproval
        from whatsapp_web import WhatsAppSendUnconfirmedError
        flow=self.waiting('PLANNING')
        flow.offer({},[{'id':'one','capability':'update_moodle_form','target':'140147:module:1','title':'Activity','course_id':'140147',
                       'summary':'Change due date','scope':'requested','depends_on':[],'fields':[]}])
        error=WhatsAppSendUnconfirmedError('uncertain');error.delivery_baseline=['old']
        with patch('run_shared_chrome_agent.send_message',side_effect=error) as sender:
            with self.assertRaises(WhatsAppSendUnconfirmedError):self.runtime.send('group',approval_message(flow.state))
            self.assertEqual(flow.state['phase'],'APPROVAL')
            self.assertFalse(flow.state['approval_sent'])
            with patch('run_shared_chrome_agent.matching_outgoing_ids',return_value=set()):self.runtime.reconcile_deliveries()
            self.assertFalse(flow.state['approval_sent'])
            with patch('run_shared_chrome_agent.matching_outgoing_ids',return_value={'new'}) as reader:
                self.runtime.reconcile_deliveries()
                self.assertEqual(reader.call_args.kwargs['exclude_ids'],['old'])
            self.assertTrue(flow.state['approval_sent'])
            self.assertNotIn('pending_delivery',flow.state)
            sender.assert_called_once()
        flow.state['pending_delivery']={'phase':'APPROVAL'};flow.save()
        self.runtime.flows['group']=ChromeApproval(flow.path,'group')
        with patch('run_shared_chrome_agent.matching_outgoing_ids') as reader:
            self.runtime.reconcile_deliveries();reader.assert_not_called()

    def test_failed_notification_does_not_escape_and_kill_listener(self):
        from whatsapp_web import WhatsAppSendUnconfirmedError
        flow=self.waiting('PLANNING')
        future=Mock();future.done.return_value=True;future.result.return_value={}
        self.runtime.jobs={'group':{'future':future,'repairs':1}}
        self.runtime.accept_decision=Mock(side_effect=ValueError('Scope needs review'))
        with patch('run_shared_chrome_agent.send_message',side_effect=WhatsAppSendUnconfirmedError('not visible')):
            self.runtime.complete_jobs()
        self.assertEqual(self.runtime.jobs,{})
        self.assertEqual(flow.state['phase'],'FAILED')
        self.assertIn('pending_delivery',flow.state)
        self.assertFalse(self.runtime.stopped)

    def test_scope_revision_uses_current_reply_and_typed_choice_not_staff_phrase_matching(self):
        self.runtime.render_pending=Mock()
        for reply in ('ALL, except tutorial 4','Everything else; leave that workshop alone','全部，tutorial 4 不改'):
            with self.subTest(reply=reply):
                self.runtime.flows['group'].state['phase']='FAILED'
                flow=self.waiting('PLANNING')
                flow.offer({},[{'id':'one','capability':'update_moodle_form','target':'first','scope':'requested','summary':'Workshop change','depends_on':[],'fields':[]},
                               {'id':'two','capability':'update_moodle_form','target':'second','scope':'related','summary':'Other change','depends_on':[],'fields':[]}])
                result={'decision':'revise_scope','selected_ids':['two']}
                with self.assertRaises(ValueError):self.runtime.accept_decision('group',result,{'context':{'reply':''}})
                self.runtime.accept_decision('group',result,{'context':{'reply':reply}})
                self.assertEqual([op['id'] for op in flow.state['selected']],['two'])
                self.assertFalse(flow.state['approval_sent'])

    def raw(self,mid,text='Please adjust the deadline',mentioned=False,direction='incoming'):
        return {'message_id':mid,'direction':direction,'sender_id':'staff','message_text':text,
                'addressed_text':text,'self_mentioned':mentioned,
                'meta':self.runtime.started.strftime('[%H:%M, %Y/%m/%d] Staff:')}

    def test_only_addressed_group_messages_enter_queue(self):
        records=[self.raw('ordinary'),self.raw('other-mention'),self.raw('own-message',mentioned=True,direction='outgoing'),
                 self.raw('addressed','把 deadline 推迟一天 please',True)]
        with patch('run_shared_chrome_agent.intake_records',side_effect=[records,[self.raw('test-message')]]):
            self.runtime.poll()
        self.assertEqual([r['id'] for r in self.runtime.queue],['addressed','test-message'])
        self.runtime.planner.assert_not_called()

    def test_edit_replaces_queued_body_and_is_not_repeated(self):
        original=self.raw('editable','Check L3',True)
        updated=self.raw('editable','Ignore L3; move prototype files',True)
        for row in (original,updated,updated):
            with patch('run_shared_chrome_agent.intake_records',side_effect=[[row],[]]):self.runtime.poll()
        self.assertEqual(len(self.runtime.queue),1)
        self.assertEqual(self.runtime.queue[0]['text'],updated['addressed_text'])
        self.assertEqual(self.runtime.queue[0]['edited_message_id'],'editable')

    def test_adding_real_mention_to_seen_message_triggers(self):
        for row in (self.raw('edited','Change dates'),self.raw('edited','Change dates',True)):
            with patch('run_shared_chrome_agent.intake_records',side_effect=[[row],[]]):self.runtime.poll()
        self.assertEqual(len(self.runtime.queue),1)
        self.assertTrue(self.runtime.queue[0]['self_mentioned'])

    def test_edited_approval_never_authorizes_and_removes_stale_queue(self):
        self.waiting('APPROVAL')
        for row in (self.raw('edited','Check dates',True),self.raw('edited','OK 123456',True)):
            with patch('run_shared_chrome_agent.intake_records',side_effect=[[row],[]]):self.runtime.poll()
        self.assertEqual(self.runtime.queue,[])
        self.assertEqual(self.runtime.flows['group'].state['phase'],'APPROVAL')

    def test_edit_removing_mention_cancels_unconsumed_task(self):
        for row in (self.raw('edited','Change dates',True),self.raw('edited','Change dates',False)):
            with patch('run_shared_chrome_agent.intake_records',side_effect=[[row],[]]):self.runtime.poll()
        self.assertEqual(self.runtime.queue,[])

    def test_unmentioned_stop_does_not_stop_monitor(self):
        with patch('run_shared_chrome_agent.intake_records',side_effect=[[self.raw('stop','stop agent')],[]]):
            self.runtime.poll()
        self.assertFalse(self.runtime.stopped)
        self.assertEqual(self.runtime.queue,[])

    def test_unmentioned_approval_or_legacy_queue_entry_is_silent(self):
        self.runtime.queue=[{'chat':'group','id':'approval','sender':'staff','text':'OK 123456'}]
        self.runtime.send=Mock();self.runtime.execute=Mock()
        self.runtime.handle_queue()
        self.runtime.execute.assert_not_called();self.runtime.send.assert_not_called()
        self.assertEqual(self.runtime.queue,[])

    def test_unaddressed_flow_cannot_send_lifecycle_or_error_reply(self):
        with patch('run_shared_chrome_agent.send_message') as sender:
            self.runtime.send('group','Status: Waiting')
            sender.assert_not_called()

    def waiting(self,phase='QUESTION'):
        flow=self.runtime.flows['group'];flow.begin('Requested task','staff','root')
        flow.state.update(phase=phase,mention_authorized=True,reply_boundary='question-one')
        if phase=='APPROVAL':flow.state.update(approval_sent=True,code='123456')
        flow.save()
        return flow

    def test_v1_both_groups_require_mention_for_new_tasks(self):
        self.runtime.mention_only_chats.add('test approval')
        with patch('run_shared_chrome_agent.intake_records',side_effect=[[self.raw('ordinary')],[self.raw('testordinary')]]):
            self.runtime.poll()
        self.assertEqual(self.runtime.queue,[])

    def test_v1_same_requester_answer_is_bound_to_observed_question(self):
        flow=self.waiting()
        other={**self.raw('other'), 'sender_id':'someone-else'}
        with patch('run_shared_chrome_agent.intake_records',side_effect=[[other,self.raw('answer','Class B please')],[]]):
            self.runtime.poll()
        self.assertEqual(len(self.runtime.queue),1)
        self.assertEqual(self.runtime.queue[0]['reply_to_run'],flow.state['run'])
        flow.state['reply_boundary']='different-question'
        self.runtime.submit=Mock();self.runtime.send=Mock()
        self.runtime.handle_queue()
        self.runtime.submit.assert_not_called();self.runtime.send.assert_not_called()

    def test_v1_exact_current_approval_without_remention(self):
        self.waiting('APPROVAL')
        records=[self.raw('wrong','OK 999999'),self.raw('right','OK 123456'),
                 {**self.raw('other','OK 123456'),'sender_id':'someone-else'}]
        with patch('run_shared_chrome_agent.intake_records',side_effect=[records,[]]):self.runtime.poll()
        self.assertEqual([r['id'] for r in self.runtime.queue],['right'])

    def test_v1_stray_approval_never_becomes_new_task(self):
        with patch('run_shared_chrome_agent.intake_records',side_effect=[[self.raw('stray','OK 123456',True)],[]]):
            self.runtime.poll()
        self.assertEqual(self.runtime.queue,[])

    def test_v1_action_request_addresses_requester_in_original_chat(self):
        flow=self.waiting();flow.state['requester_name']='Staff Name'
        with patch('run_shared_chrome_agent.send_message') as sender:
            self.runtime.send('group','Which date?')
            sender.assert_called_once_with(self.runtime.wa,'Which date?',mention_name='Staff Name',chat_type='group',resume=None)
        self.runtime.open.assert_called_once_with('group')

    def test_explicit_missed_mention_recovery_revalidates_body_and_is_single_use(self):
        from chrome_moodle import digest
        from run_shared_chrome_agent import request_record
        raw=self.raw('missed','Check course settings',True)
        record=request_record(raw,self.runtime.started)
        record.update(self_mentioned=True,requester_name='staff')
        path=Path(self.temp.name)/'candidate.json'
        candidate={'chat':'group','record':record,'record_hash':digest(record),'boundary':self.runtime.started.isoformat()}
        path.write_text(json.dumps(candidate),encoding='utf-8')
        changed={**raw,'addressed_text':'A different request'}
        with patch('run_shared_chrome_agent.intake_records',return_value=[changed]):
            with self.assertRaises(ValueError):self.runtime.recover_mention(path)
        self.assertEqual(self.runtime.queue,[])
        with patch('run_shared_chrome_agent.intake_records',return_value=[raw]):self.runtime.recover_mention(path)
        self.assertEqual(self.runtime.queue[0]['id'],'missed')
        self.assertFalse(path.exists())

    def test_missed_mention_recovery_never_replays_approval(self):
        from chrome_moodle import digest
        from run_shared_chrome_agent import request_record
        raw=self.raw('approval','OK 123456',True)
        record=request_record(raw,self.runtime.started)
        record.update(self_mentioned=True,requester_name='staff')
        path=Path(self.temp.name)/'candidate.json'
        path.write_text(json.dumps({'chat':'group','record':record,'record_hash':digest(record),'boundary':self.runtime.started.isoformat()}),encoding='utf-8')
        with patch('run_shared_chrome_agent.intake_records',return_value=[raw]):
            with self.assertRaises(ValueError):self.runtime.recover_mention(path)
        self.assertEqual(self.runtime.queue,[])

    def test_operator_recovers_exact_edited_body_not_consumed_original(self):
        from chrome_moodle import digest
        from run_shared_chrome_agent import request_record
        raw=self.raw('original','Ignore L3 and move prototype files',True)
        self.runtime.flows['group'].state.update(phase='DONE',message_ids=['original'])
        record=request_record(raw,self.runtime.started)
        record.update(self_mentioned=True,requester_name='staff',edited_message_id='original',
                      id='original:edit:'+self.runtime.message_revision(raw)[:16])
        path=Path(self.temp.name)/'candidate.json'
        path.write_text(json.dumps({'chat':'group','record':record,'record_hash':digest(record),'boundary':self.runtime.started.isoformat()}),encoding='utf-8')
        with patch('run_shared_chrome_agent.intake_records',return_value=[raw]):self.runtime.recover_mention(path)
        self.assertEqual(self.runtime.queue[0]['id'],record['id'])


if __name__=='__main__':unittest.main()
