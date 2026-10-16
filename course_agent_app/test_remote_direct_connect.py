"""No human login; browser possession still isolates files, tasks and approval."""
import base64
import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from remote_workspace import Workspace
from remote_gateway import Gateway
from test_remote_workspace import offered, register


class DirectConnectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Workspace(self.temp.name)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_two_browsers_cannot_read_files_tasks_or_approve_each_other(self):
        a = self.store.connect_browser('a'*32, '1'*64)
        b = self.store.connect_browser('b'*32, '2'*64)
        alice, bob = self.store.auth(a['token']), self.store.auth(b['token'])
        file = self.store.upload(alice, 'grades.csv', b'group,score\n1,80')
        task = offered(self.store, alice)
        self.assertEqual(self.store.files(bob), [])
        self.assertEqual(self.store.list_tasks(bob), [])
        with self.assertRaises(ValueError):self.store.file_data(bob, file['id'])
        with self.assertRaises(ValueError):self.store.approve(bob, task['id'], task['plan_hash'])
        with self.assertRaises(PermissionError):self.store.connect_browser('a'*32, '2'*64)
        with self.assertRaises(PermissionError):self.store.auth('a'*32)

    def test_disconnect_restart_reconnect_retains_workspace_and_revokes_old_session(self):
        first = self.store.connect_browser('a'*32, '1'*64)
        actor = self.store.auth(first['token'])
        file = self.store.upload(actor, 'notes.txt', b'private notes')
        self.store.logout(first['token'])
        self.store.close(); self.store = Workspace(self.temp.name)
        second = self.store.connect_browser('a'*32, '1'*64)
        self.assertEqual(first['user_id'], second['user_id'])
        self.assertEqual(self.store.files(self.store.auth(second['token']))[0]['id'],file['id'])
        with self.assertRaises(PermissionError):self.store.auth(first['token'])
        stored = self.store.db.execute('SELECT secret FROM browser_access').fetchone()['secret']
        self.assertNotEqual(stored, '1'*64)

    def test_connected_legacy_workspace_upgrade_preserves_files_but_requires_matching_device(self):
        previous, actor = register(self.store)
        file = self.store.upload(actor, 'legacy.txt', b'keep')
        with self.assertRaises(PermissionError):
            self.store.connect_browser('b'*32, '1'*64, previous['token'])
        connected = self.store.connect_browser('a'*32, '1'*64, previous['token'])
        self.assertEqual(connected['user_id'], previous['user_id'])
        self.assertEqual(self.store.files(self.store.auth(connected['token']))[0]['id'],file['id'])

    def test_http_connect_requires_no_invite_and_data_still_requires_browser_token(self):
        gateway = Gateway(Path(self.temp.name)/'http', {}, {'port':0}).start()
        def request(path, data=None, token='', origin=None):
            headers={'Authorization':'Bearer '+token, 'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            req=Request(f'http://127.0.0.1:{gateway.server.server_port}'+path,
                        data=None if data is None else json.dumps(data).encode(),headers=headers)
            try:
                with urlopen(req,timeout=5) as r:return r.status,json.load(r)
            except HTTPError as error:
                with error:return error.code,json.load(error)
        try:
            self.assertEqual(request('/v1/workspace')[0],401)
            body={'device_id':'a'*32,'browser_key':'1'*64}
            self.assertEqual(request('/v1/connect',body,origin='https://evil.test')[0],403)
            code, connected = request('/v1/connect',body)
            self.assertEqual(code,200)
            self.assertEqual(request('/v1/workspace',token=connected['token'])[0],200)
            self.assertEqual(request('/v1/connect',dict(body,browser_key='2'*64))[0],401)
            self.assertEqual(request('/v1/connect',dict(body,browser_key=''))[0],400)
        finally:gateway.close()


if __name__ == '__main__':
    unittest.main()
