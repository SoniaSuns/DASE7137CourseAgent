"""Compact approvals retain the exact manifest and multipart delivery boundary."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

from chrome_approval import approval_message
from chrome_delivery import message_parts
from run_shared_chrome_agent import Runtime


class CompactWhatsAppMessagesTests(unittest.TestCase):
    def state(self):
        return {'run':'a'*12,'task_id':'T-'+'a'*12,'code':'123456',
                'task':'Inspect both courses\nand adjust confirmed files.', 'phase':'PLANNING',
                'selected':[{'id':cid,'course_id':cid,'title':'Shared title',
                    'summary':'Change the availability setting.',
                    'fields':[{'key':'id_visible','type':'select-one','label':'Availability',
                               'before':'0','value':'1','options':[
                                   {'value':'0','label':'Hide from students'},
                                   {'value':'1','label':'Show on course page'}]}]}
                    for cid in ('140147','140148')]}

    def test_identical_titles_stay_bound_to_courses_and_exact_changes(self):
        state=self.state();before=copy.deepcopy(state);text=approval_message(state)
        self.assertEqual(state,before)
        self.assertTrue(text.startswith('Reply: OK 123456 to confirm; NO 123456 to cancel.\n'))
        self.assertIn('1. Class A — Shared title',text)
        self.assertIn('2. Class B — Shared title',text)
        self.assertEqual(text.count('Hide from students → Show on course page'),2)
        self.assertNotIn(state['task'],text)
        for duplicate in ('\nTarget:','\nCourse:','\nTask:','\nMethod:'):
            self.assertNotIn(duplicate,text)

    def test_long_plan_keeps_commands_first_and_every_change_in_bounded_parts(self):
        state=self.state();state['selected']*=60
        text=approval_message(state);parts=message_parts(text)
        self.assertGreater(len(parts),1)
        self.assertTrue(all(len(part)<=3000 for part in parts))
        self.assertIn('Reply: OK 123456 to confirm; NO 123456 to cancel.',parts[0])
        joined='\n'.join(parts)
        self.assertIn('Item 120: Class B — Shared title',joined)
        self.assertEqual(joined.count('Hide from students → Show on course page'),120)

    def runtime(self,state):
        runtime=Runtime.__new__(Runtime)
        runtime.flows={'chat':SimpleNamespace(state=state,save=Mock())}
        runtime.mention_only_chats=set();runtime.auto_block_recovery=False;runtime.open=Mock()
        return runtime

    def test_status_omits_exact_multiline_request_without_losing_findings(self):
        state=self.state();runtime=self.runtime(state)
        text='Status: Waiting\nTask: '+state['task']+'\nReason: File date is uncertain.\nTask: a quoted finding'
        runtime.wa=Mock()
        with patch('run_shared_chrome_agent.send_message',return_value=True) as send:
            runtime.send('chat',text)
        actual=send.call_args.args[1]
        self.assertNotIn(state['task'],actual)
        self.assertIn('Reason: File date is uncertain.\nTask: a quoted finding',actual)
        self.assertIn('Task ID: '+state['task_id'],actual)

    def test_persisted_multipart_status_is_not_rewritten_on_resume(self):
        state=self.state();runtime=self.runtime(state);runtime.wa=Mock()
        text='Status: Waiting\nTask ID: '+state['task_id']+'\nTask: '+state['task']+'\nChange: Checking.'
        delivery={'parts':[text],'next_part':0,'part_receipts':[]}
        with patch('run_shared_chrome_agent.send_message',return_value=True) as send:
            runtime.send('chat',text,delivery=delivery)
        self.assertEqual(send.call_args.args[1],text)
        self.assertEqual(send.call_args.kwargs['resume'],delivery)


if __name__=='__main__':unittest.main()
