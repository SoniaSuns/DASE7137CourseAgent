from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

from listener_health import ListenerHealth,HealthHeartbeat,assess_health
from run_shared_chrome_agent import Runtime
from playwright.sync_api import TimeoutError as BrowserTimeout
from shared_chrome import SharedChrome,ChromeConnectionRequired


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.health=ListenerHealth(['a','b'],clock=lambda:self.now,wall=lambda:1800000000+self.now)

    def test_heartbeat_does_not_mask_stalled_chat_reads(self):
        self.health.read('a',2);self.health.read('b',0);self.health.cycle(True)
        self.assertEqual(assess_health(self.health.snapshot(),now=1800000000),'healthy')
        self.now=91
        doc=self.health.snapshot()
        self.assertEqual(assess_health(doc,now=1800000091),'intake_degraded')
        self.assertEqual(assess_health(doc,now=1800000150),'heartbeat_stale')
        self.health.read('a',1)
        self.assertFalse(self.health.snapshot()['chats']['b']['healthy'])

    def test_recovery_backoff_and_reset_require_successful_read_cycle(self):
        self.health.cycle(False);self.assertEqual(self.health.recovery_due(),'focus')
        self.health.recovery_result(True)
        self.assertEqual(self.health.snapshot()['status'],'degraded')
        self.assertIsNone(self.health.recovery_due())
        self.now=20;self.health.cycle(False);self.health.cycle(False)
        self.assertEqual(self.health.recovery_due(),'reload')
        self.now=90
        for _ in range(3):self.health.cycle(False)
        self.assertEqual(self.health.recovery_due(),'reconnect')
        self.health.recovery_result(True)
        for chat in ('a','b'):self.health.read(chat,0)
        self.health.cycle(True)
        self.assertEqual(self.health.snapshot()['last_recovery']['outcome'],'confirmed_by_read')
        self.assertIsNone(self.health.recovery_due())

    def test_health_thread_reports_stale_reads_without_browser_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'health.json'
            with HealthHeartbeat(self.health,path,interval=.02):
                self.health.read('a',0);self.health.read('b',0)
                self.now=200
                deadline=time.monotonic()+2
                doc={}
                while time.monotonic()<deadline:
                    try:doc=json.loads(path.read_text(encoding='utf-8'))
                    except PermissionError:
                        # Windows may briefly deny opening the destination during
                        # atomic replace. Keep the deadline and final assertions.
                        time.sleep(.01);continue
                    if doc['uptime_seconds']==200:break
                    time.sleep(.01)
                self.assertEqual(doc['status'],'degraded')
                self.assertEqual(doc['uptime_seconds'],200)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['stage'],'stopped')

    def test_simulated_twelve_hour_intake_crosses_midnight_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime=Runtime(Mock(),Mock(),['a','b'],[],Mock(),Path(temp),Mock(),mention_only_chats=['a','b'])
            runtime.health=self.health;runtime.recover_transport=Mock();runtime.send=Mock()
            runtime.started=runtime.started.replace(hour=23,minute=50,second=0,microsecond=0)
            runtime.open=Mock()
            latest=None
            for minute in range(721):
                self.now=minute*60
                if minute%60==0:
                    stamp=runtime.started+timedelta(minutes=minute)
                    latest={'message_id':'m'+str(minute),'direction':'incoming','sender_id':'staff',
                        'message_text':'Review dates','addressed_text':'Review dates','self_mentioned':True,
                        'meta':stamp.strftime('[%H:%M, %Y/%m/%d] Staff:')}
                outage=120<=minute<140
                runtime.open.side_effect=lambda chat: (_ for _ in ()).throw(BrowserTimeout('private')) if outage and chat=='a' else None
                with patch('run_shared_chrome_agent.intake_records',side_effect=([[],] if outage else [[latest],[]])):
                    runtime.poll_safely()
                if outage:self.assertEqual(runtime.readable_chats,{'b'})
            ids=[r['id'] for r in runtime.queue]
            self.assertEqual(len(ids),13);self.assertEqual(len(ids),len(set(ids)))
            self.assertIn('m120',ids)
            self.assertEqual(runtime.health.snapshot()['status'],'healthy')
            self.assertTrue(runtime.recover_transport.called)
            runtime.send.assert_not_called();runtime.planner.assert_not_called()


class BrowserRecoveryTests(unittest.TestCase):
    def session(self):
        session=SharedChrome.__new__(SharedChrome)
        session.active_page_sessions=[];session.owned_pages=[]
        session.browser=Mock();session.browser.is_connected.return_value=True
        page=Mock(url='https://web.whatsapp.com/');page.evaluate.return_value=False
        session.browser.contexts=[Mock(pages=[page])]
        return session,page

    def test_reload_only_whatsapp_and_preserves_draft(self):
        session,page=self.session()
        session.recover_whatsapp(page,'reload')
        page.reload.assert_called_once();session.browser.close.assert_not_called()
        page.reload.reset_mock();page.evaluate.return_value=True
        with self.assertRaises(ChromeConnectionRequired):session.recover_whatsapp(page,'reload')
        page.reload.assert_not_called()

    def test_focus_recovery_never_navigates_or_sends(self):
        session,page=self.session()
        result,reconnected=session.recover_whatsapp(page,'focus')
        self.assertIs(result,page);self.assertFalse(reconnected)
        page.reload.assert_not_called();page.goto.assert_not_called();page.click.assert_not_called()


if __name__=='__main__':unittest.main()
