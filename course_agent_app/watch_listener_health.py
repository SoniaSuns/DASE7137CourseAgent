"""Read-only, bounded continuous acceptance recording; no browser or messaging imports."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import socket
import time

from listener_health import assess_health


def sample(path):
    try:
        health=json.loads(path.read_text(encoding='utf-8'))
        return {'observed_at':datetime.now(timezone.utc).isoformat(),
            'assessment':assess_health(health),'pid':health.get('pid'),'stage':health.get('stage'),
            'chats':health.get('chats',{}),'recovery_count':health.get('recovery_count',0)}
    except (OSError,ValueError):
        return {'observed_at':datetime.now(timezone.utc).isoformat(),'assessment':'health_unavailable'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--duration-hours',type=float,default=24)
    parser.add_argument('--interval',type=float,default=15)
    args=parser.parse_args()
    if not 0<args.duration_hours<=72 or not 1<=args.interval<=60:raise ValueError('Invalid recording duration or interval.')
    root=Path(__file__).parent/'reports/shared_chrome'
    root.mkdir(parents=True,exist_ok=True)
    with socket.socket() as lock:
        lock.bind(('127.0.0.1',18791))
        started=time.monotonic();deadline=started+args.duration_hours*3600
        counts=Counter();longest_gap=0;gap_start=None
        record=root/('health-observation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.jsonl')
        summary_path=root/'health_observation.json'
        while True:
            now=time.monotonic();point=sample(root/'health.json');counts[point['assessment']]+=1
            if point['assessment']=='healthy':
                if gap_start is not None:longest_gap=max(longest_gap,now-gap_start)
                gap_start=None
            elif gap_start is None:gap_start=now
            with record.open('a',encoding='utf-8') as output:output.write(json.dumps(point,ensure_ascii=False)+'\n')
            summary={'status':'completed' if now>=deadline else 'running','updated':point['observed_at'],
                'requested_hours':args.duration_hours,'observed_seconds':now-started,'samples':sum(counts.values()),
                'assessments':dict(counts),'last_assessment':point['assessment'],
                'longest_unhealthy_seconds':max(longest_gap,now-gap_start if gap_start is not None else 0),
                'record':record.name,'messages_sent':0,'moodle_writes':0}
            tmp=summary_path.with_suffix('.tmp');tmp.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(summary_path)
            if now>=deadline:break
            time.sleep(min(args.interval,max(0,deadline-time.monotonic())))


if __name__=='__main__':main()
