"""Refresh the existing failure catalog independently of the live agent."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime,timezone
from report_io import atomic_text,exclusive,ReportBusy

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'reports/trace_eval_20261009'


def ensure_running():
    return subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--watch'],cwd=ROOT,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).pid


def refresh(status):
    from build_diagnostic_report import main
    try:
        main()
        report=json.loads((OUT/'failed_requests.json').read_text(encoding='utf-8'))
        status.update(last_success=datetime.now(timezone.utc).isoformat(),cases=report['count'],error=None)
    except Exception as exc:
        status['error']=type(exc).__name__  # No credential-bearing error strings.
    status.update(pid=os.getpid(),updated=datetime.now(timezone.utc).isoformat(),interval_seconds=30)
    atomic_text(OUT/'failure_report_status.json',json.dumps(status,indent=2))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--watch',action='store_true');args=parser.parse_args()
    try:
        with exclusive(OUT/'failure-report-watcher.lock'):
            status={}
            while True:
                try:refresh(status)
                except OSError:pass  # A later cycle retries a temporary file lock.
                if not args.watch:return
                time.sleep(30)
    except ReportBusy:return  # One independent reporter for this workspace.


if __name__=='__main__':main()
