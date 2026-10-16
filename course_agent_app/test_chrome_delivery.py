import html
import unittest
from unittest.mock import patch,Mock

from playwright.sync_api import sync_playwright
from chrome_delivery import matching_outgoing_ids,send_message
from chrome_delivery import message_parts,WhatsAppSendUnconfirmedError


class DeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start();cls.browser=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.p.stop()

    def setUp(self):self.page=self.browser.new_page(viewport={'width':900,'height':600})
    def tearDown(self):self.page.close()

    def fixture(self,text,label='查看更多',outgoing=True):
        status='<span data-testid="msg-meta"><svg><title>wds-ic-read</title></svg></span>' if outgoing else ''
        self.page.set_content('<div id="main"><div data-id="new">'+status+
            '<span data-testid="selectable-text" style="white-space:pre-wrap">Approval preview...</span>'+
            '<div role="button" data-testid="caption-read-more-button">'+label+'</div></div></div>')
        self.page.locator('[role=button]').evaluate('''(e,text)=>e.onclick=()=>{
            e.parentElement.querySelector('[data-testid="selectable-text"]').textContent=text;e.remove();}''',text)

    def test_chinese_folded_long_message_is_verified_in_full(self):
        expected='Moodle approval\n'+'Deadline change\n'*150+'Reply:\nOK 123456\nNO 123456'
        self.fixture(expected)
        self.assertFalse(matching_outgoing_ids(self.page,expected))
        self.assertEqual(matching_outgoing_ids(self.page,expected,expand=True),{'new'})

    def test_localized_control_and_whole_message_required(self):
        expected='时间调整 / Timing changes\nOK 123456\nNO 123456'
        self.fixture(expected,label='Read more')
        self.assertEqual(matching_outgoing_ids(self.page,expected,expand=True),{'new'})
        self.assertFalse(matching_outgoing_ids(self.page,expected+'\nMissing change',expand=True))

    def test_rendered_inline_spaces_do_not_block_complete_approval(self):
        expected='Moodle approval\nWorkshop IV Slides  File\nOK 123456\nNO 123456'
        self.fixture(expected.replace('  ',' '))
        self.assertEqual(matching_outgoing_ids(self.page,expected,expand=True),{'new'})
        self.assertFalse(matching_outgoing_ids(self.page,expected.replace('123456','123457'),expand=True))
        self.assertFalse(matching_outgoing_ids(self.page,expected+'\nAnother file',expand=True))

    def test_inline_code_recovers_delimiters_from_observed_markup(self):
        expected='Status: Waiting\nMove `Workshop IV Slides` to Week 5?\nOK 123456'
        self.page.set_content('''<div id="main"><div data-id="new" class="message-out">
          <span class="selectable-text">Status: Waiting<br>Move <code class="selectable-text"
          data-app-text-template="`${appText}`">Workshop IV Slides</code> to Week 5?<br>OK 123456</span>
          </div></div>''')
        self.assertEqual(matching_outgoing_ids(self.page,expected),{'new'})
        for wrong in (expected.replace('Week 5','Week 6'),expected.replace('123456','123457'),
                      expected+'\nAdditional change',expected.replace('Workshop IV','Workshop V')):
            self.assertFalse(matching_outgoing_ids(self.page,wrong))
        self.assertFalse(matching_outgoing_ids(self.page,expected,exclude_ids={'new'}))
        self.page.locator('code').evaluate("e=>e.removeAttribute('data-app-text-template')")
        self.assertFalse(matching_outgoing_ids(self.page,expected))

    def test_code_in_quote_or_incoming_message_cannot_confirm_send(self):
        expected='Move `File`\nOK 123456'
        body='<span class="selectable-text">Move <code data-app-text-template="`${appText}`">File</code><br>OK 123456</span>'
        self.page.set_content('<div id="main"><div data-id="new" class="message-in">'+body+'</div></div>')
        self.assertFalse(matching_outgoing_ids(self.page,expected))
        self.page.set_content('<div id="main"><div data-id="new" class="message-out"><blockquote>'+body+'</blockquote><span class="selectable-text">Other reply</span></div></div>')
        self.assertFalse(matching_outgoing_ids(self.page,expected))

    def test_hidden_content_cannot_complete_formatted_message(self):
        for style in ('display:none','visibility:hidden','visibility:collapse'):
            with self.subTest(style=style):
                self.page.set_content('<div id="main"><div data-id="new" class="message-out">'
                    '<span class="selectable-text">Move <code data-app-text-template="`${appText}`">File</code>'
                    '<span style="'+style+'"><br>OK 123456</span></span></div></div>')
                self.assertFalse(matching_outgoing_ids(self.page,'Move `File`\nOK 123456'))
                self.assertEqual(matching_outgoing_ids(self.page,'Move `File`'),{'new'})
        self.page.set_content('<div id="main"><div style="display:none"><div data-id="new" class="message-out">'
            '<span class="selectable-text">Move <code data-app-text-template="`${appText}`">File</code></span></div></div></div>')
        self.assertFalse(matching_outgoing_ids(self.page,'Move `File`'))
        self.assertFalse(matching_outgoing_ids(self.page,'Move File'))

    def test_expand_uses_pointer_events_required_by_real_control(self):
        expected='Moodle approval\nShow Week 6\nOK 123456'
        self.fixture(expected)
        self.page.locator('[role=button]').evaluate('''(e,text)=>{
            let pressed=false;e.onpointerdown=()=>pressed=true;e.onclick=()=>{
              if(pressed){e.parentElement.querySelector('[data-testid="selectable-text"]').textContent=text;e.remove();}
            };}''',expected)
        self.assertEqual(matching_outgoing_ids(self.page,expected,expand=True),{'new'})

    def test_incremental_read_more_requires_last_part_before_confirmation(self):
        expected='Moodle approval\nAll changes\nOK 123456\nNO 123456'
        self.fixture(expected)
        self.page.locator('[role=button]').evaluate('''(e,text)=>{
            let count=0;e.onclick=()=>{
              e.parentElement.querySelector('[data-testid="selectable-text"]').textContent=++count===1 ? text.slice(0,25) : text;
              if(count===2)e.remove();};}''',expected)
        self.assertFalse(matching_outgoing_ids(self.page,expected,expand=True))
        self.assertEqual(matching_outgoing_ids(self.page,expected,expand=True),{'new'})

    def test_incoming_quoted_and_old_messages_never_confirm_new_send(self):
        expected='Moodle approval\nOK 123456'
        self.fixture(expected,outgoing=False)
        self.assertFalse(matching_outgoing_ids(self.page,expected,expand=True))
        self.assertEqual(self.page.locator('[role=button]').count(),1)
        self.fixture(expected)
        self.assertFalse(matching_outgoing_ids(self.page,expected,expand=True,exclude_ids={'new'}))
        self.assertEqual(self.page.locator('[role=button]').count(),1)
        self.page.set_content('<div id="main"><div data-id="new" class="message-out"><blockquote><span class="selectable-text">'+html.escape(expected)+'</span></blockquote><span class="selectable-text">Other text</span></div></div>')
        self.assertFalse(matching_outgoing_ids(self.page,expected,expand=True))

    def test_send_clicks_once_and_confirms_new_folded_message(self):
        text='Moodle approval\n'+'Changes\n'*100+'OK 123456'
        self.page.set_content('<div id="main"></div>')
        with patch('chrome_delivery.compose_box'),patch('chrome_delivery.focus_compose_box'),\
             patch('chrome_delivery.set_action_message_text',return_value={}),\
             patch('chrome_delivery.scroll_chat_to_bottom'),\
             patch('chrome_delivery.click_send',side_effect=lambda *a,**k:self.fixture(text)) as click:
            result=send_message(self.page,text)
        click.assert_called_once()
        self.assertEqual(result['verified_message_ids'],['new'])

    def test_access_dates_render_without_raw_control_json(self):
        from chrome_approval import display_value
        text=display_value({'key':'id_availabilityconditionsjson'},'{"op":"&","c":[{"type":"date","d":">=","t":1791468000}],"showc":[true]}')
        self.assertIn('HKT',text)
        self.assertIn('available from',text)
        self.assertNotIn('1791468000',text)
        self.assertNotIn('{',text)

    def test_before_click_browser_failure_is_deferred(self):
        from playwright.sync_api import TimeoutError
        from chrome_delivery import WhatsAppSendDeferredError
        with patch('chrome_delivery.outgoing_snapshots',return_value=[]),\
             patch('chrome_delivery.compose_box',side_effect=TimeoutError('disconnected')),\
             patch('chrome_delivery.click_send') as click:
            with self.assertRaises(WhatsAppSendDeferredError) as caught:send_message(self.page,'Message')
        self.assertFalse(caught.exception.send_attempted);click.assert_not_called()

    def test_click_response_loss_never_clicks_twice_and_can_be_read_back(self):
        from playwright.sync_api import TimeoutError
        from chrome_delivery import WhatsAppSendUnconfirmedError,WhatsAppSendDeferredError
        self.page.set_content('<div id="main"></div>')
        text='Moodle approval\nVerified current scope\nOK 123456'
        control=Mock()
        def lost_response(**kwargs):
            self.fixture(text)
            raise TimeoutError('response lost after click')
        control.click.side_effect=lost_response
        with patch('chrome_delivery.compose_box'),patch('chrome_delivery.focus_compose_box'),\
             patch('chrome_delivery.set_action_message_text',return_value={}),\
             patch('chrome_delivery.first_visible',return_value=control):
            with self.assertRaises(WhatsAppSendUnconfirmedError) as caught:send_message(self.page,text)
        self.assertNotIsInstance(caught.exception,WhatsAppSendDeferredError)
        control.click.assert_called_once()
        self.assertEqual(matching_outgoing_ids(self.page,text,expand=True),{'new'})


class MultipartTests(unittest.TestCase):
    def test_parts_are_bounded_and_approval_footer_is_last(self):
        message='Moodle approval\n'+''.join(f'Change {i}: move the selected file to its requested week.\n' for i in range(150))+'\nReply:\nOK 123456\nNO 123456'
        parts=message_parts(message)
        self.assertGreater(len(parts),1);self.assertTrue(all(len(p)<=3000 for p in parts))
        self.assertTrue(all('OK 123456' not in p for p in parts[:-1]));self.assertIn('NO 123456',parts[-1])
        self.assertIn('\nReply:\n',parts[-1])
        for i in range(150):self.assertEqual(sum(f'Change {i}:' in p for p in parts),1)

    def test_oversized_single_line_is_not_dropped(self):
        text='x'*10000;parts=message_parts(text)
        self.assertEqual(''.join(p.split('\n',1)[1] for p in parts),text)
        self.assertTrue(all(len(p)<=3000 for p in parts))

    def test_partial_send_resumes_only_unconfirmed_remaining_parts(self):
        text='Review\n'+('File change\n'*700)+'\nReply:\nOK 123456\nNO 123456';parts=message_parts(text)
        error=WhatsAppSendUnconfirmedError('response lost');error.delivery_baseline=['old']
        with patch('chrome_delivery.send_single_message',side_effect=[{'verified_message_ids':['first']},error]) as send:
            with self.assertRaises(WhatsAppSendUnconfirmedError) as caught:send_message(Mock(),text)
            self.assertEqual(send.call_count,2)
        exc=caught.exception;self.assertEqual(exc.next_part,1);self.assertEqual(exc.part_receipts,[['first']])
        # Readback, not a second send, confirms the uncertain second part.
        resume={'parts':parts,'next_part':2,'part_receipts':[['first'],['second']]}
        with patch('chrome_delivery.send_single_message',return_value={'verified_message_ids':['last']}) as send:
            result=send_message(Mock(),text,resume=resume)
            self.assertEqual(send.call_count,len(parts)-2)
            self.assertNotIn(parts[0],[c.args[1] for c in send.call_args_list])
            self.assertNotIn(parts[1],[c.args[1] for c in send.call_args_list])
        self.assertEqual(result['verified_message_ids'][:2],['first','second'])

    def test_resume_rejects_changed_message(self):
        with self.assertRaises(ValueError):send_message(Mock(),'Changed',resume={'parts':['Original'],'next_part':0,'part_receipts':[]})


if __name__=='__main__':unittest.main()
