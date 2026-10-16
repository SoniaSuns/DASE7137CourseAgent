"""Opt-in real extension smoke check using a mock executor, never live Moodle.

Run before deploying the loopback bridge (port 18790 must be free).
Uses Playwright's bundled Chromium and a disposable profile.
"""
import json
from pathlib import Path
import shutil
import tempfile
from unittest.mock import Mock
from playwright.sync_api import sync_playwright,expect
from chrome_chat_bridge import CHAT,SENDER,attach,connection
from run_shared_chrome_agent import Runtime


def verify():
    root=Path(__file__).resolve().parents[1]
    artifacts=root/'course_agent_app/reports/chrome_chat/ui_verification'
    artifacts.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='moodle-panel-check-') as folder:
        temp=Path(folder);extension=temp/'extension'
        shutil.copytree(root/'moodle_chrome_extension',extension)
        (extension/'connection.json').write_text(json.dumps(connection(temp/'bridge')),encoding='utf-8')
        runtime=Runtime(None,Mock(),['wa'],['140147','140148'],Mock(),temp/'state',Mock())
        bridge=attach(runtime,temp/'bridge')
        try:
            with sync_playwright() as p:
                context=p.chromium.launch_persistent_context(str(temp/'profile'),channel='chromium',headless=True,
                    args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}'],
                    viewport={'width':390,'height':820})
                try:
                    worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                    extension_id=worker.url.split('/')[2]
                    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'chrome-extension://{extension_id}/panel.html')
                    expect(page.locator('#connection')).to_have_class('pill online',timeout=15000)
                    expect(page.locator('#welcome')).to_be_visible()
                    page.screenshot(path=str(artifacts/'welcome.png'))
                    page.locator('#request').fill('Inspect Class A <img src=x onerror=alert(1)>')
                    page.locator('#send').click()
                    expect(page.locator('.message.user')).to_have_count(1)
                    assert page.locator('.message img').count()==0
                    bridge.drain(runtime)
                    assert len(runtime.queue)==1
                    request=runtime.queue.pop();runtime.save_queue()
                    flow=runtime.flows[CHAT]
                    flow.begin(request['text'],SENDER,request['id']);flow.state['mention_authorized']=True
                    flow.offer({},[{'id':'example','capability':'update_moodle_form','target':'140148:module:7',
                        'course_id':'140148','title':'Example File','summary':'Move to the confirmed Week 8 position.',
                        'scope':'requested','depends_on':[],'fields':[]}])
                    runtime.render_pending(CHAT);bridge.refresh(runtime)
                    expect(page.locator('#approval')).to_be_visible(timeout=6000)
                    page.screenshot(path=str(artifacts/'approval.png'))
                    runtime.execute=Mock()
                    page.locator('#confirm').click()
                    expect(page.locator('.message.user')).to_have_count(2)
                    bridge.drain(runtime);runtime.handle_queue()
                    runtime.execute.assert_called_once_with(CHAT)
                    assert flow.state['phase']=='APPROVED'
                    flow.state['phase']='DONE';flow.save()
                    runtime.send(CHAT,'Status: Done\nVerify: Mock execution only; no Moodle write.')
                    bridge.refresh(runtime)
                    expect(page.locator('#approval')).to_be_hidden(timeout=6000)
                    page.reload();expect(page.locator('.message.user')).to_have_count(2)
                    expect(page.locator('.message.assistant')).to_have_count(2)
                    page.set_viewport_size({'width':300,'height':680})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert not errors,errors
                    result={'extension_loaded':True,'authenticated_http':True,'text_only_rendering':True,
                        'current_plan_confirmation':True,'mock_execution_count':1,'history_after_reload':True,
                        'narrow_layout':True,'page_errors':errors,'moodle_writes':0}
                    (artifacts/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
                    print(json.dumps(result))
                finally:context.close()
        finally:bridge.close()


if __name__=='__main__':verify()
