"""Real Chromium, fictional Moodle forms: identity, drift, approval and tenant routing."""
import base64
import copy
import html
import json
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
from remote_gateway import Gateway

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://moodle.example.test'
EDIT = ORIGIN+'/course/modedit.php?update=12'


def fixture(context, actor='41', name='Old'):
    data = {'name': name, 'posts': 0, 'actor': actor, 'hidden_id': '12', 'other': 'Keep'}
    def route(request):
        req = request.request
        if req.method == 'POST':
            values = parse_qs(req.post_data or '')
            data['posts'] += 1; data['name'] = values.get('name', [''])[0]; data['other'] = values.get('description', [''])[0]
            # Render the persisted form as a native POST response. Synthetic
            # redirects bypass Playwright's route into nonexistent fixture DNS.
        if urlparse(req.url).path == '/course/modedit.php':
            main = f'''<h1>Example resource</h1><form method="post" action="{EDIT}">
            <input type="hidden" name="sesskey" value="PRIVATE_SESSION_VALUE">
            <input type="hidden" name="instance" value="{data['hidden_id']}">
            <label for="name">Name</label><input id="name" name="name" value="{html.escape(data['name'])}">
            <label for="description">Description</label><textarea id="description" name="description">{html.escape(data['other'])}</textarea>
            <button id="id_submitbutton" name="submitbutton" type="submit">Save changes</button></form>'''
        else:
            main = f'''<h1>Example course</h1><div id="module-12"><span class="instancename">{html.escape(data['name'])}</span>
            <a href="{EDIT}">Edit settings</a></div><a href="/course/mod.php?delete=12&amp;sesskey=PRIVATE_SESSION_VALUE">Delete</a>'''
        request.fulfill(content_type='text/html', body=f'''<!doctype html><html><body class="course-7 context-700">
            <div class="usermenu"><a href="/user/profile.php?id={data['actor']}">My profile</a></div>
            <main id="region-main">{main}</main></body></html>''')
    context.route(ORIGIN+'/**', route)
    return data


class RemoteDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.script = (ROOT/'moodle_remote_extension/moodle.js').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context(); self.data = fixture(self.context)
        self.page = self.context.new_page(); self.page.goto(EDIT); self.page.evaluate(self.script)
        self.binding = {'origin': ORIGIN, 'course_id': '7', 'actor_id': '41', 'tab_id': 3}

    def tearDown(self):
        self.context.close()

    def snapshot(self):
        return self.page.evaluate('b=>RemoteMoodle.snapshot(b)', self.binding)

    def operation(self):
        snap = self.snapshot()
        return {'url': EDIT, 'form': snap['form'], 'changes': [{'name': 'name', 'before': 'Old', 'value': 'New'}]}

    def test_snapshot_omits_session_values_and_mutating_links(self):
        snapshot = self.snapshot()
        self.assertNotIn('PRIVATE_SESSION_VALUE', json.dumps(snapshot))
        self.assertNotIn('sesskey', json.dumps(snapshot))
        self.assertEqual([f['name'] for f in snapshot['form']['fields']], ['name', 'description'])
        self.page.goto(ORIGIN+'/course/view.php?id=7'); self.page.evaluate(self.script)
        self.assertFalse(any('delete=' in link['url'] for link in self.snapshot()['links']))

    def test_account_or_course_change_prevents_apply(self):
        op = self.operation()
        for key, value in [('actor_id', '82'), ('course_id', '8')]:
            binding = dict(self.binding, **{key: value})
            result = self.page.evaluate('async a=>{try{await RemoteMoodle.apply(...a);return false}catch{return true}}', [binding, op])
            self.assertTrue(result)
        self.assertEqual(self.data['posts'], 0)

    def test_visible_or_hidden_target_drift_prevents_save(self):
        op = self.operation()
        self.page.locator('input[name=instance]').evaluate('(e)=>e.value="99"')
        self.assertTrue(self.page.evaluate('async a=>{try{await RemoteMoodle.apply(...a);return false}catch{return true}}', [self.binding, op]))
        self.page.locator('input[name=instance]').evaluate('(e)=>e.value="12"')
        self.page.locator('#description').fill('Changed by a teacher')
        self.assertTrue(self.page.evaluate('async a=>{try{await RemoteMoodle.apply(...a);return false}catch{return true}}', [self.binding, op]))
        self.assertEqual(self.data['posts'], 0)


class RemoteExtensionIntegrationTests(unittest.TestCase):
    def test_two_real_browser_profiles_keep_files_tasks_approvals_and_saves_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); extension = root/'extension'
            shutil.copytree(ROOT/'moodle_remote_extension', extension)
            manifest = json.loads((extension/'manifest.json').read_text(encoding='utf-8'))
            # Test fixtures grant these exact hosts; the distributable requests them on user clicks.
            manifest['host_permissions'] = [ORIGIN+'/*', 'http://127.0.0.1/*']
            (extension/'manifest.json').write_text(json.dumps(manifest))
            def planner(context):
                forms = [e for e in context['evidence'] if e.get('form')]
                return {'action': 'plan' if forms else 'inspect', 'message': 'Rename the inspected resource.',
                        'url': EDIT, 'file_id': '', 'offset': 0, 'unresolved': [],
                        'operations': [{'url': EDIT, 'changes': [{'name': 'name', 'value': context['text']}]}] if forms else []}
            gateway = Gateway(root/'server', {}, {'port': 0}, lambda env, path: planner).start()
            server = f'http://127.0.0.1:{gateway.server.server_port}'
            contexts = []
            try:
                with sync_playwright() as p:
                    def client(name, actor):
                        ctx = p.chromium.launch_persistent_context(str(root/name), channel='chromium', headless=True,
                            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
                        contexts.append(ctx); data = fixture(ctx, actor, 'Old '+name)
                        worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event('serviceworker')
                        def call(kind, body=None):
                            return worker.evaluate('m=>handle(m)', {'type': kind, 'body': body or {}})
                        call('login', {'server': server, 'name': name, 'password': 'fictional-test-password', 'invite': gateway.store.invite()})
                        page = ctx.new_page(); page.goto(ORIGIN+'/course/view.php?id=7'); page.bring_to_front()
                        call('bind')
                        file = call('upload', {'name': 'notes.csv', 'data': base64.b64encode((name+',80').encode()).decode()})
                        task = call('create', {'text': 'Renamed by '+name, 'file_ids': [file['id']], 'request_id': ('a' if name=='alice' else 'b')*32})
                        return call, data, task['id']
                    alice, a_data, a_id = client('alice', '41')
                    bob, b_data, b_id = client('bob', '82')
                    def until(call, tid, phase):
                        deadline = time.time()+25
                        last = None
                        while time.time()<deadline:
                            last = call('workspace')
                            task = next(t for t in last['tasks'] if t['id']==tid)
                            if task['phase']==phase:
                                return task
                            if task['phase'] in {'FAILED', 'NEEDS_READBACK'}:
                                self.fail(json.dumps(task, ensure_ascii=False))
                            time.sleep(.1)
                        self.fail('Timed out: '+json.dumps(last, ensure_ascii=False))
                    a_plan = until(alice, a_id, 'APPROVAL'); b_plan = until(bob, b_id, 'APPROVAL')
                    self.assertEqual(a_data['posts']+b_data['posts'], 0)
                    self.assertEqual(len(alice('workspace')['tasks']), 1)
                    self.assertEqual(len(bob('workspace')['files']), 1)
                    alice('approve', {'id': a_id, 'plan_hash': a_plan['plan_hash']})
                    until(alice, a_id, 'DONE')
                    self.assertEqual(a_data['posts'], 1); self.assertEqual(b_data['posts'], 0)
                    self.assertEqual(a_data['name'], 'Renamed by alice'); self.assertEqual(a_data['other'], 'Keep')
                    # Polling completed tasks cannot replay a save.
                    for _ in range(3):
                        alice('workspace')
                    self.assertEqual(a_data['posts'], 1)
                    bob('approve', {'id': b_id, 'plan_hash': b_plan['plan_hash']})
                    until(bob, b_id, 'DONE')
                    self.assertEqual(b_data['posts'], 1); self.assertEqual(b_data['name'], 'Renamed by bob')
                    for ctx in contexts:
                        ctx.close()
                    contexts.clear()
            finally:
                gateway.close()


if __name__ == '__main__':
    unittest.main()
