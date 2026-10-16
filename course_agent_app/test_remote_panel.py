"""Exercise the shipped panel's direct connection and private file persistence."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from playwright.sync_api import sync_playwright, expect
from remote_gateway import Gateway

ROOT = Path(__file__).resolve().parents[1]


class RemotePanelTests(unittest.TestCase):
    def test_panel_connects_without_account_uploads_and_reconnects_same_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root/'extension'
            shutil.copytree(ROOT/'moodle_remote_extension', extension)
            manifest = json.loads((extension/'manifest.json').read_text(encoding='utf-8'))
            # Pregrant only the fixture host; the production UI requests this host on click.
            manifest['host_permissions'] = ['http://127.0.0.1/*']
            (extension/'manifest.json').write_text(json.dumps(manifest))
            gateway = Gateway(root/'server', {}, {'port': 0}, lambda env, path: None).start()
            try:
                with sync_playwright() as p:
                    context = p.chromium.launch_persistent_context(str(root/'profile'),
                        channel='chromium', headless=True,
                        args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
                    try:
                        worker = context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(worker.url.rsplit('/', 1)[0]+'/panel.html')
                        page.locator('#server').fill(f'http://127.0.0.1:{gateway.server.server_port}')
                        self.assertEqual(page.locator('#name, #password, #invite').count(), 0)
                        page.locator('#signin').click()
                        try:
                            expect(page.locator('#workspace')).to_be_visible(timeout=15000)
                        except AssertionError:
                            self.fail('Panel connection error: '+page.locator('#error').inner_text()+'; JS: '+repr(errors))
                        expect(page.locator('#connection')).to_contain_text('已连接')
                        page.locator('#files').set_input_files({'name': 'personal-notes.csv',
                            'mimeType': 'text/csv', 'buffer': b'group,grade\n1,80\n'})
                        expect(page.locator('#library')).to_contain_text('personal-notes.csv')
                        expect(page.locator('#library input')).to_be_checked()
                        expect(page.locator('#error')).to_have_text('')
                        page.locator('#logout').click()
                        expect(page.locator('#login')).to_be_visible()
                        expect(page.locator('#workspace')).to_be_hidden()
                        page.locator('#signin').click()
                        expect(page.locator('#workspace')).to_be_visible()
                        expect(page.locator('#library')).to_contain_text('personal-notes.csv')
                        self.assertEqual(errors, [])
                        # Another extension document cannot impersonate the exact panel.
                        page.goto(worker.url.rsplit('/', 1)[0]+'/panel.html?untrusted=1')
                        response = page.evaluate("chrome.runtime.sendMessage({type:'settings'})")
                        self.assertFalse(response['ok'])
                        self.assertIn('Only the extension panel', response['error'])
                    finally:
                        context.close()
            finally:
                gateway.close()


if __name__ == '__main__':
    unittest.main()
