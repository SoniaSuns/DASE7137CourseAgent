"""Observable intake health. Heartbeat thread never accesses a browser or task."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import threading
import time


class ListenerHealth:
    def __init__(self,chats,*,clock=time.monotonic,wall=time.time,stale_after=90):
        self.clock=clock;self.wall=wall;self.stale_after=stale_after
        self.lock=threading.RLock();self.started=clock();self.stage='starting'
        self.chats={chat:{'last_attempt':None,'last_success':None,'reads':0,'failures':0,
                         'consecutive_failures':0,'records':0,'error_type':''} for chat in chats}
        self.failures=0;self.next_recovery=0;self.recovery_count=0;self.last_recovery=None
        self.task_observer=None

    @contextmanager
    def work(self,stage):
        with self.lock:previous=self.stage;self.stage=stage
        try:yield
        finally:
            with self.lock:self.stage=previous

    def attempt(self,chat):
        with self.lock:self.chats[chat]['last_attempt']=self.clock()

    def read(self,chat,count):
        with self.lock:
            row=self.chats[chat];row.update(last_success=self.clock(),consecutive_failures=0,records=count,error_type='')
            row['reads']+=1

    def failed(self,chat,exc):
        with self.lock:
            row=self.chats[chat];row['failures']+=1;row['consecutive_failures']+=1
            row['error_type']=type(exc).__name__  # Never persist browser error text or message bodies.

    def cycle(self,success):
        with self.lock:
            self.failures=0 if success else self.failures+1
            if success:
                self.next_recovery=0
                if self.last_recovery and self.last_recovery['outcome']=='applied_waiting_for_read':
                    self.last_recovery['outcome']='confirmed_by_read'

    def recovery_due(self):
        with self.lock:
            if not self.failures or self.clock()<self.next_recovery:return None
            action='focus' if self.failures<3 else 'reload' if self.failures<6 else 'reconnect'
            self.next_recovery=self.clock()+min(60,5*2**min(self.failures-1,4))
            self.recovery_count+=1
            self.last_recovery={'action':action,'at':self.wall(),'outcome':'attempting'}
            return action

    def recovery_result(self,success):
        with self.lock:
            if self.last_recovery:self.last_recovery['outcome']='applied_waiting_for_read' if success else 'unavailable'

    def snapshot(self):
        with self.lock:
            now=self.clock();rows={}
            for chat,row in self.chats.items():
                age=None if row['last_success'] is None else max(0,now-row['last_success'])
                rows[chat]={k:v for k,v in row.items() if k not in {'last_success','last_attempt'}}
                rows[chat].update(last_success_age_seconds=age,
                    last_attempt_age_seconds=None if row['last_attempt'] is None else max(0,now-row['last_attempt']),
                    healthy=age is not None and age<=self.stale_after and not row['consecutive_failures'])
            healthy=bool(rows) and all(row['healthy'] for row in rows.values()) and self.failures==0
            result={'pid':os.getpid(),'updated':datetime.fromtimestamp(self.wall(),timezone.utc).isoformat(),
                'uptime_seconds':max(0,now-self.started),'stage':self.stage,
                'status':'healthy' if healthy else 'degraded','stale_after_seconds':self.stale_after,
                'chats':rows,'consecutive_cycle_failures':self.failures,'recovery_count':self.recovery_count,
                'last_recovery':dict(self.last_recovery) if self.last_recovery else None}
            if self.task_observer is not None:result['observability']=self.task_observer.snapshot()
            return result


class HealthHeartbeat:
    def __init__(self,health,path,interval=5):
        self.health=health;self.path=Path(path);self.interval=interval
        self.stop_event=threading.Event();self.error=None

    def write(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.health.snapshot(),ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(self.path)

    def __enter__(self):
        self.write()
        def run():
            while not self.stop_event.wait(self.interval):
                try:self.write()
                except OSError as exc:self.error=type(exc).__name__
        self.thread=threading.Thread(target=run,name='listener-health',daemon=True);self.thread.start()
        return self

    def __exit__(self,*args):
        self.stop_event.set();self.thread.join(timeout=2*self.interval)
        with self.health.lock:self.health.stage='stopped'
        self.write()


def assess_health(document,*,now=None,heartbeat_limit=20):
    """An external reader detects a dead heartbeat even if the old status says healthy."""
    try:
        age=(time.time() if now is None else now)-datetime.fromisoformat(document['updated']).timestamp()
        if age>heartbeat_limit or age < -5:return 'heartbeat_stale'
        if document.get('stage')=='stopped':return 'stopped'
        return 'healthy' if document.get('status')=='healthy' else 'intake_degraded'
    except (KeyError,TypeError,ValueError):return 'invalid_health'
