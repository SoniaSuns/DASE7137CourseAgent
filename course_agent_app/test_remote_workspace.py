"""Multi-user ownership, approval, recovery and dispatch regressions."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from remote_workspace import Workspace, validate_plan, read_url, digest
from remote_gateway import Gateway

BINDING = {'origin': 'https://moodle.example.test', 'course_id': '7', 'actor_id': '41', 'tab_id': 3}
FORM_URL = BINDING['origin']+'/course/modedit.php?update=12'
FORM = {'fields': [{'name': 'name', 'type': 'text', 'label': 'Name', 'value': 'Old', 'disabled': False, 'options': []},
                   {'name': 'locked', 'type': 'text', 'label': 'Locked', 'value': 'Keep', 'disabled': True, 'options': []}],
        'signature': 'fixture-signature'}


def register(store, name='alice', device='a'*32):
    login = store.login(name, 'fixture-password-only', device, store.invite())
    return login, store.auth(login['token'])


def offered(store, actor):
    state = store.create(actor, 'Rename the file', BINDING, [], 'e'*32)
    state['evidence'] = [{'url': FORM_URL, 'title': 'Example File', 'form': copy.deepcopy(FORM)}]
    state['plan'] = validate_plan(state, [{'url': FORM_URL, 'changes': [{'name': 'name', 'value': 'New'}]}])
    state.update(phase='APPROVAL', plan_hash=digest(state['plan']))
    state.pop('command', None); store.save(state)
    return state


class RemoteWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.store = Workspace(self.root)
        self.login, self.actor = register(self.store)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_invitation_is_single_use_and_password_and_session_are_hashed(self):
        invite = self.store.invite()
        self.store.login('bob', 'another-fixture-password', 'b'*32, invite)
        with self.assertRaises(ValueError):
            self.store.login('charlie', 'another-fixture-password', 'c'*32, invite)
        rows = str([tuple(r) for r in self.store.db.execute('SELECT * FROM users')])
        sessions = str([tuple(r) for r in self.store.db.execute('SELECT * FROM sessions')])
        self.assertNotIn('fixture-password', rows)
        self.assertNotIn(self.login['token'], sessions)

    def test_expired_invitation_and_logout_are_rejected(self):
        invite = self.store.invite()
        self.store.db.execute('UPDATE invites SET expires=0'); self.store.db.commit()
        with self.assertRaises(ValueError):
            self.store.login('bob', 'another-fixture-password', 'b'*32, invite)
        self.store.logout(self.login['token'])
        with self.assertRaises(PermissionError):
            self.store.auth(self.login['token'])

    def test_two_users_cannot_read_each_others_files_tasks_or_approvals(self):
        _, bob = register(self.store, 'bob', 'b'*32)
        file = self.store.upload(self.actor, 'same-name.csv', b'group,score\n1,80')
        self.store.upload(bob, 'same-name.csv', b'group,score\n1,20')
        task = offered(self.store, self.actor)
        self.assertEqual(len(self.store.files(bob)), 1)
        self.assertEqual(self.store.list_tasks(bob), [])
        for call in (lambda: self.store.file_data(bob, file['id']), lambda: self.store.get(bob, task['id']),
                     lambda: self.store.approve(bob, task['id'], task['plan_hash'])):
            with self.assertRaises(ValueError):
                call()

    def test_second_device_can_see_history_but_cannot_get_command_or_confirm(self):
        login = self.store.login('alice', 'fixture-password-only', 'd'*32)
        other = self.store.auth(login['token'])
        task = offered(self.store, self.actor)
        executing = self.store.approve(self.actor, task['id'], task['plan_hash'])
        self.assertNotIn('command', self.store.public(executing, other))
        with self.assertRaises(ValueError):
            self.store.approve(other, task['id'], task['plan_hash'])

    def test_file_paths_quota_hash_and_active_delete_are_enforced(self):
        for name in ('../file.txt', 'C:\\secret.txt', 'a/b.txt', ''):
            with self.assertRaises(ValueError):
                self.store.upload(self.actor, name, b'x')
        file = self.store.upload(self.actor, 'roster.html', b'<table></table>')
        self.store.create(self.actor, 'Read roster', BINDING, [file['id']], 'f'*32)
        with self.assertRaises(ValueError):
            self.store.delete_file(self.actor, file['id'])
        row, _ = self.store.file_data(self.actor, file['id']); Path(row['path']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.store.file_data(self.actor, file['id'])

    def test_new_task_retry_is_idempotent_but_changed_request_is_not(self):
        one = self.store.create(self.actor, 'Inspect', BINDING, [], 'f'*32)
        two = self.store.create(self.actor, 'Inspect', BINDING, [], 'f'*32)
        self.assertEqual(one['id'], two['id'])
        with self.assertRaises(ValueError):
            self.store.create(self.actor, 'Different', BINDING, [], 'f'*32)

    def test_older_active_task_is_not_lost_behind_history_limit(self):
        first = self.store.create(self.actor, 'Inspect', BINDING, [], 'f'*32)
        for i in range(105):
            item = copy.deepcopy(first); item.update(id=f'{i:032x}', phase='DONE', request_id=f'{i:032x}')
            self.store.save(item)
        self.assertEqual(len(self.store.list_tasks(self.actor)), 100)
        with self.assertRaises(ValueError):
            self.store.create(self.actor, 'Another', BINDING, [], '9'*32)

    def test_unapproved_stale_and_other_device_execution_are_rejected(self):
        task = offered(self.store, self.actor)
        with self.assertRaises(ValueError):
            self.store.approve(self.actor, task['id'], 'wrong')
        with self.assertRaises(ValueError):
            self.store.authorize_operation(self.actor, task['id'], 'none', task['plan'][0]['id'], task['plan_hash'])
        task = self.store.approve(self.actor, task['id'], task['plan_hash'])
        self.assertTrue(self.store.authorize_operation(self.actor, task['id'], task['command']['id'], task['plan'][0]['id'], task['plan_hash'])['authorized'])
        self.store.reconcile(self.actor, task['id'])
        with self.assertRaises(ValueError):
            self.store.authorize_operation(self.actor, task['id'], task['command']['id'], task['plan'][0]['id'], task['plan_hash'])

    def test_empty_execute_receipt_needs_readback_and_is_not_replayed(self):
        task = offered(self.store, self.actor)
        task = self.store.approve(self.actor, task['id'], task['plan_hash'])
        body = {'binding': BINDING, 'results': [], 'error': 'transport lost'}
        result = self.store.receipt(self.actor, task['id'], task['command']['id'], body)
        self.assertEqual(result['phase'], 'NEEDS_READBACK')
        self.assertEqual(self.store.receipt(self.actor, task['id'], task['command']['id'], body)['phase'], 'NEEDS_READBACK')
        with self.assertRaises(ValueError):
            self.store.cancel(self.actor, task['id'])
        verify = self.store.reconcile(self.actor, task['id'])
        self.assertEqual(verify['command']['kind'], 'verify')

    def test_restart_invalidates_approval_and_preserves_possible_save(self):
        task = offered(self.store, self.actor)
        self.store.close(); self.store = Workspace(self.root)
        self.assertEqual(self.store.get(self.actor, task['id'])['phase'], 'INTERRUPTED')
        with self.assertRaises(ValueError):
            self.store.approve(self.actor, task['id'], task['plan_hash'])
        task['phase'] = 'EXECUTING'; self.store.save(task)
        self.store.close(); self.store = Workspace(self.root)
        self.assertEqual(self.store.get(self.actor, task['id'])['phase'], 'NEEDS_READBACK')

    def test_admin_invite_does_not_interrupt_running_tasks(self):
        task = offered(self.store, self.actor)
        admin = Workspace(self.root, recover=False)
        try:
            admin.invite()
            self.assertEqual(self.store.get(self.actor, task['id'])['phase'], 'APPROVAL')
        finally:
            admin.close()

    def test_plan_only_accepts_exact_observed_editable_fields(self):
        state = {'binding': BINDING, 'evidence': [{'url': FORM_URL, 'title': 'File', 'form': FORM}]}
        for changes in ([{'name': 'sesskey', 'value': 'x'}], [{'name': 'locked', 'value': 'x'}],
                        [{'name': 'name', 'value': 'X'}, {'name': 'name', 'value': 'Y'}]):
            with self.assertRaises(ValueError):
                validate_plan(state, [{'url': FORM_URL, 'changes': changes}])
        with self.assertRaises(ValueError):
            validate_plan(state, [{'url': FORM_URL+'&delete=1', 'changes': [{'name': 'name', 'value': 'X'}]}])

    def test_read_urls_reject_mutating_queries_other_course_and_other_hosts(self):
        for url in (FORM_URL+'&delete=1', BINDING['origin']+'/course/view.php?id=8',
                    BINDING['origin']+'/course/mod.php?delete=12&sesskey=x', 'https://elsewhere.test/course/view.php?id=7'):
            self.assertFalse(read_url(url, BINDING))
        self.assertTrue(read_url(FORM_URL, BINDING))


class RemoteGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.gateway = Gateway(self.temp.name, {}, {'port': 0}).start()
        self.login, self.actor = register(self.gateway.store)
        self.url = f'http://127.0.0.1:{self.gateway.server.server_port}'

    def tearDown(self):
        self.gateway.close(); self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        h = {'Authorization': 'Bearer '+self.login['token'], 'Origin': 'chrome-extension://'+'a'*32}
        if data is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        req = Request(self.url+path, data=None if data is None else json.dumps(data).encode(), headers=h)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_http_auth_host_origin_and_upload_ownership(self):
        self.assertEqual(self.request('/v1/workspace')[0], 200)
        for headers in ({'Authorization': ''}, {'Host': 'evil.test'}, {'Origin': 'https://evil.test'}, {'Origin': 'null'}):
            self.assertIn(self.request('/v1/workspace', headers=headers)[0], (401, 403))
        status, file = self.request('/v1/files', {'name': 'same.csv', 'data': base64.b64encode(b'a,b').decode()})
        self.assertEqual(status, 200)
        other, _ = register(self.gateway.store, 'bob', 'b'*32)
        status, body = self.request('/v1/workspace', headers={'Authorization': 'Bearer '+other['token']})
        self.assertEqual(body['files'], [])
        self.assertEqual(self.request('/v1/files/'+file['id']+'/delete', {}, {'Authorization': 'Bearer '+other['token']})[0], 400)

    def test_reply_during_worker_unwind_is_not_left_stuck_planning(self):
        state = self.gateway.store.create(self.actor, 'Inspect', BINDING, [], 'f'*32)
        state.update(phase='PLANNING', evidence=[{'url': BINDING['origin']+'/course/view.php?id=7', 'links': [{'url': FORM_URL, 'kind': 'form'}]}])
        self.gateway.store.save(state)
        calls = []
        def planner(context):
            calls.append(context)
            return {'action': 'inspect' if len(calls)==1 else 'finish', 'message': 'Checked', 'url': FORM_URL,
                    'file_id': '', 'offset': 0, 'operations': [], 'unresolved': []}
        self.gateway.planner_factory = lambda env, root: planner
        original = self.gateway.store.save
        replied = []
        def save(current):
            original(current)
            if current['phase'] == 'READING' and current['rounds'] == 1 and not replied:
                replied.append(True)
                resumed = self.gateway.store.receipt(self.actor, current['id'], current['command']['id'],
                    {'binding': BINDING, 'snapshot': {'url': FORM_URL, 'title': 'Example', 'links': [], 'form': FORM}})
                self.gateway.schedule(resumed)  # Original worker is still present in jobs.
        with patch.object(self.gateway.store, 'save', side_effect=save):
            self.gateway.schedule(state)
            deadline = time.time()+5
            while time.time()<deadline and self.gateway.store.get(self.actor, state['id'])['phase'] != 'DONE':
                time.sleep(.02)
        self.assertEqual(self.gateway.store.get(self.actor, state['id'])['phase'], 'DONE')
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
