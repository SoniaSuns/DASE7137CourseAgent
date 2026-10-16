"""Optional persistent Chrome profile using the documented debugging-port mode."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse
from urllib.request import build_opener, ProxyHandler

from shared_chrome import connection_endpoint

ROOT=Path(__file__).resolve().parents[1]
PROFILE=ROOT/'.chrome_agent_profile'


def chrome_executable():
    candidates=[os.environ.get('COURSE_AGENT_CHROME_EXE',''),
                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                str(Path(os.environ.get('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():return candidate
    raise RuntimeError('Chrome was not found; set COURSE_AGENT_CHROME_EXE.')


def profile_endpoint(profile):
    endpoint=connection_endpoint({'COURSE_AGENT_CHROME_USER_DATA_DIR':str(profile)})
    parsed=urlparse(endpoint)
    opener=build_opener(ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{parsed.port}/json/version',timeout=2) as response:
        advertised=json.load(response).get('webSocketDebuggerUrl','')
    if urlparse(advertised).path!=parsed.path:
        raise RuntimeError('The listening browser does not match the dedicated profile endpoint.')
    return endpoint


def ensure_browser(profile=PROFILE, *, headless=False):
    profile=Path(profile).resolve()
    if not profile.is_relative_to(ROOT):
        raise ValueError('The v2 dedicated profile must remain inside v2.')
    profile.mkdir(parents=True,exist_ok=True)
    try:return profile_endpoint(profile)
    except Exception:pass
    command=[chrome_executable(),f'--user-data-dir={profile}','--remote-debugging-port=0',
             '--remote-debugging-address=127.0.0.1','--no-first-run','--no-default-browser-check']
    command+=['--headless=new','about:blank'] if headless else ['https://web.whatsapp.com/','https://moodle.hku.hk/']
    subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if headless else 0)
    for _ in range(60):
        try:return profile_endpoint(profile)
        except Exception:time.sleep(.5)
    raise RuntimeError('The dedicated Chrome endpoint did not become ready. Close only that profile and retry.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--setup-only',action='store_true')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    endpoint=ensure_browser()
    if args.setup_only:
        print('Dedicated Chrome is open. Sign in to Moodle and link WhatsApp once, then run start_v2_dedicated_chrome.ps1.')
        return
    env={**os.environ,'COURSE_AGENT_CDP_URL':endpoint,'COURSE_AGENT_CHROME_USER_DATA_DIR':str(PROFILE)}
    command=[sys.executable,'-u',str(ROOT/'course_agent_app/run_shared_chrome_agent.py')]
    if not args.check:command.append('--run')
    raise SystemExit(subprocess.call(command,env=env))


if __name__=='__main__':main()
