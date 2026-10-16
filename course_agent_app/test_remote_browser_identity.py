"""Real extension profiles keep automatic identity through browser restarts."""
import json
from contextlib import ExitStack
from pathlib import Path
import shutil
import tempfile
import unittest

from playwright.sync_api import sync_playwright, expect
from remote_gateway import Gateway

ROOT = Path(__file__).resolve().parents[1]


class BrowserIdentityTests(unittest.TestCase):
    def test_automatic_reconnect_retains_files_and_second_browser_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); extension = root/'extension'
            shutil.copytree(ROOT/'moodle_remote_extension', extension)
            manifest = json.loads((extension/'manifest.json').read_text(encoding='utf-8'))
            manifest['host_permissions'] = ['http://127.0.0.1/*']
            (extension/'manifest.json').write_text(json.dumps(manifest))
            gateway = Gateway(root/'server', {}, {'port':0}).start()
            second = Gateway(root/'second-server', {}, {'port':0}).start()
            contexts = []
            try:
                with sync_playwright() as p, ExitStack() as browsers:
                    browsers.callback(lambda: [ctx.close() for ctx in contexts])
                    def open_panel(profile, reconnect=False):
                        ctx = p.chromium.launch_persistent_context(str(root/profile),channel='chromium',headless=True,
                            args=[f'--disable-extensions-except={extension}',f'--load-extension={extension}'])
                        contexts.append(ctx)
                        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event('serviceworker')
                        page = ctx.new_page();page.goto(worker.url.rsplit('/',1)[0]+'/panel.html')
                        if not reconnect:
                            page.locator('#server').fill(f'http://127.0.0.1:{gateway.server.server_port}')
                            page.locator('#signin').click()
                        expect(page.locator('#workspace')).to_be_visible(timeout=15000)
                        settings = worker.evaluate("handle({type:'settings'})")
                        self.assertNotIn('workspace-access',json.dumps(settings))
                        return ctx,worker,page,settings['login']['user_id']
                    alice,worker,page,alice_id = open_panel('alice')
                    page.locator('#files').set_input_files({'name':'alice-only.txt','mimeType':'text/plain','buffer':b'private notes'})
                    expect(page.locator('#library')).to_contain_text('alice-only.txt')
                    alice.close();contexts.remove(alice)
                    alice,worker,page,reopened_id = open_panel('alice',reconnect=True)
                    self.assertEqual(alice_id,reopened_id)
                    expect(page.locator('#library')).to_contain_text('alice-only.txt')
                    # A long-lived browser session renews on a read without user input.
                    before = worker.evaluate('config()')
                    with gateway.store.lock, gateway.store.db:
                        gateway.store.db.execute('UPDATE sessions SET expires=0 WHERE user=?',(alice_id,))
                    renewed = worker.evaluate("handle({type:'workspace'})")
                    self.assertEqual(renewed['files'][0]['name'],'alice-only.txt')
                    after = worker.evaluate('config()')
                    self.assertEqual(before['user_id'],after['user_id'])
                    self.assertNotEqual(before['token'],after['token'])
                    bob,worker,page,bob_id = open_panel('bob')
                    self.assertNotEqual(alice_id,bob_id)
                    expect(page.locator('#library')).to_have_text('')
                    first = worker.evaluate("chrome.storage.local.get(null)")
                    keys = [k for k in first if k.startswith('workspace-access:')]
                    self.assertEqual(len(keys),1)
                    self.assertEqual(len(first[keys[0]]['key']),64)
                    # A second service origin gets its own identity and key.
                    worker.evaluate("server=>handle({type:'connect',body:{server}})",
                                    f'http://127.0.0.1:{second.server.server_port}')
                    local = worker.evaluate("chrome.storage.local.get(null)")
                    access = [v for k,v in local.items() if k.startswith('workspace-access:')]
                    self.assertEqual(len(access),2)
                    self.assertNotEqual(access[0]['key'],access[1]['key'])
                    self.assertNotEqual(access[0]['device_id'],access[1]['device_id'])
            finally:
                gateway.close();second.close()


if __name__ == '__main__':
    unittest.main()
