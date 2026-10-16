"""Tenant-owned task/file store. No Moodle connection or owner file-library access."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlparse, parse_qs


ACTIVE = {'PLANNING', 'READING', 'QUESTION', 'APPROVAL', 'EXECUTING', 'NEEDS_READBACK'}
MAX_FILE = 32 * 1024 * 1024
MAX_USER_FILES = 256 * 1024 * 1024


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    return hashlib.sha256(value).hexdigest()


def uid():
    return secrets.token_hex(16)


def check_binding(value):
    if not isinstance(value, dict) or set(value) != {'origin', 'course_id', 'actor_id', 'tab_id'}:
        raise ValueError('Choose your Moodle course tab first.')
    p = urlparse(value['origin'])
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.path or p.query or p.fragment:
        raise ValueError('Moodle must use an exact HTTPS origin.')
    if not all(re.fullmatch(r'[1-9][0-9]{0,12}', str(value[k])) for k in ('course_id', 'actor_id')):
        raise ValueError('A signed-in Moodle account and exact course are required.')
    if type(value['tab_id']) is not int or value['tab_id'] < 0:
        raise ValueError('Choose an existing browser tab.')
    return dict(value)


def read_url(url, binding):
    """Only structurally read-only Moodle endpoints, never arbitrary action URLs."""
    p = urlparse(url)
    if p.scheme + '://' + p.netloc != binding['origin'] or p.username or p.password or p.fragment:
        return False
    q = parse_qs(p.query, keep_blank_values=True)
    if any(len(v) != 1 for v in q.values()):
        return False
    cid = str(binding['course_id'])
    if p.path == '/course/view.php':
        return set(q) <= {'id', 'section'} and q.get('id') == [cid]
    if p.path in {'/user/index.php', '/grade/report/grader/index.php', '/grade/report/singleview/index.php'}:
        return (set(q) <= {'id', 'page', 'perpage', 'group', 'sifirst', 'silast', 'tifirst', 'tilast', 'item', 'itemid'}
                and q.get('id') == [cid])
    if p.path == '/course/modedit.php':
        return set(q) == {'update'} and q['update'][0].isdigit()
    if p.path == '/course/editsection.php':
        return set(q) == {'id'} and q['id'][0].isdigit()
    if p.path == '/grade/edit/tree/grade.php':
        return set(q) == {'courseid', 'id'} and q['courseid'] == [cid] and q['id'][0].isdigit()
    if re.fullmatch(r'/mod/(resource|folder|page|book|assign|quiz|url|choice)/view.php', p.path):
        return set(q) <= {'id', 'forceview'} and q.get('id', [''])[0].isdigit() and q.get('forceview', ['1']) == ['1']
    return False


class Workspace:
    def __init__(self, root, recover=True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root/'workspace.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS invites(hash TEXT PRIMARY KEY, expires REAL);
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT UNIQUE, salt TEXT, password TEXT);
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY,user TEXT,device TEXT,expires REAL);
        CREATE TABLE IF NOT EXISTS browser_access(device TEXT PRIMARY KEY,secret TEXT,user TEXT);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,user TEXT,name TEXT,bytes INTEGER,sha TEXT,path TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,user TEXT,device TEXT,state TEXT);
        ''')
        self.epoch = uid()
        self.failures = {}
        with self.lock:
            for row in self.db.execute('SELECT * FROM tasks').fetchall() if recover else []:
                state = json.loads(row['state'])
                if state['phase'] in ACTIVE:
                    state['phase'] = 'NEEDS_READBACK' if state['phase'] in {'EXECUTING', 'NEEDS_READBACK'} else 'INTERRUPTED'
                    state['message'] = 'Server restarted. Previous approval is invalid; no save will be replayed.'
                    state.pop('command', None)
                    self.save(state)
            self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def invite(self):
        code = secrets.token_urlsafe(32)
        with self.lock, self.db:
            self.db.execute('INSERT INTO invites VALUES (?,?)', (digest(code.encode()), time.time()+86400))
        return code

    def credentials(self, name, password, device):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.@+-]{3,100}', name):
            raise ValueError('Use a 3–100 character account name.')
        if not isinstance(password, str) or not 12 <= len(password) <= 200:
            raise ValueError('Use a password of 12–200 characters.')
        if not isinstance(device, str) or not re.fullmatch(r'[a-f0-9-]{32,40}', device):
            raise ValueError('Invalid browser device identity.')

    def login(self, name, password, device, invite=None):
        self.credentials(name, password, device)
        name = name.casefold()
        with self.lock, self.db:
            attempts = [t for t in self.failures.get(name, []) if t > time.time()-300]
            if len(attempts) >= 10:
                raise ValueError('Too many login attempts; wait five minutes.')
            attempts.append(time.time()); self.failures[name] = attempts
            if invite is not None:
                row = self.db.execute('SELECT * FROM invites WHERE hash=?', (digest(str(invite).encode()),)).fetchone()
                if not row or row['expires'] < time.time():
                    raise ValueError('Invitation is invalid or expired.')
                salt = secrets.token_hex(16)
                hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
                try:
                    self.db.execute('INSERT INTO users VALUES (?,?,?,?)', (uid(), name, salt, hashed))
                except sqlite3.IntegrityError:
                    raise ValueError('Account could not be created.') from None
                self.db.execute('DELETE FROM invites WHERE hash=?', (row['hash'],))
            row = self.db.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
            salt = bytes.fromhex(row['salt']) if row else bytes(16)
            hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()
            if not row or not secrets.compare_digest(hashed, row['password']):
                raise ValueError('Account name or password is incorrect.')
            self.failures.pop(name, None)
            token = secrets.token_urlsafe(48)
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (digest(token.encode()), row['id'], device, time.time()+7*86400))
            return {'token': token, 'user_id': row['id'], 'name': name, 'device_id': device}

    def auth(self, token):
        with self.lock:
            row = self.db.execute('SELECT * FROM sessions WHERE hash=?', (digest(token.encode()),)).fetchone()
            if not row or row['expires'] < time.time():
                raise PermissionError('Please sign in again.')
            return {'user': row['user'], 'device': row['device']}

    def connect_browser(self, device, secret, previous_token=''):
        """Automatic browser identity: a device ID alone never grants access."""
        if not isinstance(device, str) or not re.fullmatch(r'[a-f0-9-]{32,40}', device):
            raise ValueError('Invalid browser device identity.')
        if not isinstance(secret, str) or not re.fullmatch(r'[a-f0-9]{64}', secret):
            raise ValueError('Invalid browser access key.')
        hashed = digest(secret.encode())
        with self.lock, self.db:
            row = self.db.execute('SELECT * FROM browser_access WHERE device=?', (device,)).fetchone()
            if row:
                if not secrets.compare_digest(hashed, row['secret']):
                    raise PermissionError('Browser access key does not match.')
                user = row['user']
            else:
                # Preserve a currently connected legacy workspace on upgrade.
                if previous_token:
                    actor = self.auth(previous_token)
                    if actor['device'] != device:
                        raise PermissionError('Browser device changed.')
                    user = actor['user']
                else:
                    user = uid()
                    self.db.execute('INSERT INTO users VALUES (?,?,?,?)',
                                    (user, 'browser-'+user, secrets.token_hex(16), secrets.token_hex(64)))
                self.db.execute('INSERT INTO browser_access VALUES (?,?,?)', (device, hashed, user))
            self.db.execute('DELETE FROM sessions WHERE user=? AND device=?', (user, device))
            token = secrets.token_urlsafe(48)
            self.db.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                            (digest(token.encode()), user, device, time.time()+7*86400))
            return {'token': token, 'user_id': user, 'name': 'My browser', 'device_id': device}

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute('DELETE FROM sessions WHERE hash=?', (digest(token.encode()),))

    def files(self, actor):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT id,name,bytes,sha FROM files WHERE user=?', (actor['user'],))]

    def upload(self, actor, name, data):
        if not isinstance(name, str) or len(name) > 200 or re.search(r'[\\/:\x00-\x1f]', name) or name in {'.', '..', ''}:
            raise ValueError('Use a file name without a directory path.')
        if not data or len(data) > MAX_FILE:
            raise ValueError('Files must be nonempty and at most 32 MiB.')
        with self.lock, self.db:
            total = self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM files WHERE user=?', (actor['user'],)).fetchone()[0]
            if total + len(data) > MAX_USER_FILES:
                raise ValueError('Your file space is full (256 MiB). Delete unused files first.')
            fid = uid(); folder = self.root/'users'/actor['user']/'files'; folder.mkdir(parents=True, exist_ok=True)
            path = folder/fid
            path.write_bytes(data)
            self.db.execute('INSERT INTO files VALUES (?,?,?,?,?,?)', (fid, actor['user'], name, len(data), digest(data), str(path)))
            return {'id': fid, 'name': name, 'bytes': len(data), 'sha': digest(data)}

    def file_data(self, actor, fid):
        with self.lock:
            row = self.db.execute('SELECT * FROM files WHERE id=? AND user=?', (fid, actor['user'])).fetchone()
            if not row:
                raise ValueError('File is not in your workspace.')
            data = Path(row['path']).read_bytes()
            if digest(data) != row['sha']:
                raise ValueError('Stored file changed; upload it again.')
            return dict(row), data

    def delete_file(self, actor, fid):
        with self.lock, self.db:
            for task in self.list_tasks(actor, all_rows=True):
                if task['phase'] in ACTIVE and fid in task['file_ids']:
                    raise ValueError('An active task still uses this file.')
            row, _ = self.file_data(actor, fid)
            Path(row['path']).unlink()
            self.db.execute('DELETE FROM files WHERE id=? AND user=?', (fid, actor['user']))

    def save(self, state):
        state['updated'] = time.time()
        self.db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?)',
                        (state['id'], state['user'], state['device'], json.dumps(state, ensure_ascii=False)))
        self.db.commit()

    def get(self, actor, tid, device=False):
        with self.lock:
            row = self.db.execute('SELECT state FROM tasks WHERE id=? AND user=?', (tid, actor['user'])).fetchone()
            if not row:
                raise ValueError('Task is not in your workspace.')
            state = json.loads(row['state'])
            if device and state['device'] != actor['device']:
                raise ValueError('Continue this task on its original browser device.')
            return state

    def list_tasks(self, actor, all_rows=False):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute('SELECT state FROM tasks WHERE user=? ORDER BY rowid DESC'+('' if all_rows else ' LIMIT 100'), (actor['user'],))]

    def active_count(self):
        with self.lock:
            return sum(json.loads(r[0])['phase'] in ACTIVE for r in self.db.execute('SELECT state FROM tasks'))

    def create(self, actor, text, binding, file_ids, request_id):
        check_binding(binding)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 20000:
            raise ValueError('Enter a task of 1–20000 characters.')
        if not re.fullmatch(r'[a-f0-9-]{32,40}', str(request_id)):
            raise ValueError('A stable request ID is required.')
        if not isinstance(file_ids, list) or len(file_ids) > 20 or len(set(file_ids)) != len(file_ids):
            raise ValueError('Select at most twenty distinct files.')
        with self.lock:
            for old in self.list_tasks(actor, all_rows=True):
                if old.get('request_id') == request_id:
                    if (old['text'], old['binding'], old['file_ids'], old['device']) != (text, binding, file_ids, actor['device']):
                        raise ValueError('Request ID was already used for different input.')
                    return old
                if old['device'] == actor['device'] and old['phase'] in ACTIVE:
                    raise ValueError('Finish or cancel your active browser task first.')
            if sum(self.file_data(actor, fid)[0]['bytes'] for fid in file_ids) > 64*1024*1024:
                raise ValueError('Selected task files exceed 64 MiB.')
            state = {'id': uid(), **actor, 'epoch': self.epoch, 'request_id': request_id,
                     'text': text, 'binding': binding, 'file_ids': file_ids, 'phase': 'READING',
                     'evidence': [], 'messages': [], 'results': [], 'rounds': 0,
                     'message': 'Reading your selected Moodle course.', 'plan': [], 'unresolved': []}
            self.command(state, 'read', {'url': binding['origin']+'/course/view.php?id='+str(binding['course_id'])})
            self.save(state)
            return state

    def command(self, state, kind, payload):
        state['command'] = {'id': uid(), 'kind': kind, 'payload': payload, 'binding': state['binding'], 'issued': time.time()}

    def receipt(self, actor, tid, command_id, result):
        with self.lock:
            state = self.get(actor, tid, device=True)
            cmd = state.get('command', {})
            if cmd.get('id') != command_id:
                if state.get('last_receipt_id') == command_id:
                    return state  # Lost HTTP response; never execute the browser operation again.
                raise ValueError('Browser reply belongs to an old command.')
            if result.get('binding') != state['binding']:
                raise ValueError('Browser account, course or tab changed.')
            if len(json.dumps(result)) > 900000:
                raise ValueError('Browser evidence is too large.')
            if cmd['kind'] == 'read':
                if state['phase'] != 'READING':
                    raise ValueError('No read is pending.')
                if result.get('error'):
                    state.update(phase='FAILED', message='Browser read failed: '+str(result['error'])[:1000])
                else:
                    snapshot = result['snapshot']
                    if snapshot.get('url') != cmd['payload']['url'] or not read_url(snapshot['url'], state['binding']):
                        raise ValueError('Browser returned a different source.')
                    state['evidence'].append(snapshot)
                    state.update(phase='PLANNING', message='Analysing your course and selected files.')
            elif cmd['kind'] in {'execute', 'verify'}:
                if state['phase'] not in {'EXECUTING', 'NEEDS_READBACK'}:
                    raise ValueError('No approved execution is pending.')
                receipts = result.get('results', [])
                if not isinstance(receipts, list) or len(receipts) > len(state['plan']):
                    raise ValueError('Invalid execution receipts.')
                for i, item in enumerate(receipts):
                    if item.get('operation_id') != state['plan'][i]['id']:
                        raise ValueError('Receipt does not match the approved operation order.')
                state['results'] = receipts
                done = len(receipts) == len(state['plan']) and all(r.get('status') == 'verified' for r in receipts)
                uncertain = (not receipts or len(receipts) < len(state['plan']) or
                             any(r.get('status') not in {'verified', 'not_saved', 'mismatch'} for r in receipts))
                state.update(phase=('PARTIAL' if state['unresolved'] else 'DONE') if done else ('NEEDS_READBACK' if uncertain else 'FAILED'),
                             message='Approved changes were read back in your browser.' if done else 'Execution stopped. Review the retained browser receipts; no save will be replayed.')
            else:
                raise ValueError('Unknown browser command.')
            state['last_receipt_id'] = command_id
            state.pop('command', None)
            self.save(state)
            return state

    def download_receipt(self, actor, tid, body):
        with self.lock:
            state = self.get(actor, tid, device=True)
            cmd = state.get('command', {})
            if cmd.get('id') != body.get('command_id'):
                if state.get('last_receipt_id') == body.get('command_id'):
                    return state
                raise ValueError('Download reply is stale.')
            if cmd['kind'] != 'download' or state['phase'] != 'READING' or body.get('binding') != state['binding']:
                raise ValueError('No matching browser download is pending.')
            if body.get('error'):
                state.update(phase='FAILED', message='Browser download failed: '+str(body['error'])[:500])
            else:
                data = base64.b64decode(body.get('data', ''), validate=True)
                total = sum(self.file_data(actor, fid)[0]['bytes'] for fid in state['file_ids'])
                if total+len(data) > 64*1024*1024 or len(state['file_ids']) >= 20:
                    raise ValueError('Task source budget exceeded.')
                entry = self.upload(actor, body.get('name'), data)
                state['file_ids'].append(entry['id'])
                state.setdefault('downloads', []).append({'url': cmd['payload']['url'], **entry})
                state['phase'] = 'PLANNING'
            state['last_receipt_id'] = cmd['id']; state.pop('command', None); self.save(state)
            return state

    def reconcile(self, actor, tid):
        with self.lock:
            state = self.get(actor, tid, device=True)
            if state['phase'] not in {'EXECUTING', 'NEEDS_READBACK'}:
                raise ValueError('No uncertain execution needs readback.')
            state.update(phase='NEEDS_READBACK', message='Reading current values only; previous save will not be replayed.')
            if state.get('command', {}).get('kind') != 'verify':
                self.command(state, 'verify', {'plan': state['plan'], 'plan_hash': state['plan_hash']})
            self.save(state); return state

    def approve(self, actor, tid, plan_hash):
        with self.lock:
            state = self.get(actor, tid, device=True)
            if state['phase'] == 'EXECUTING' and state.get('plan_hash') == plan_hash:
                return state
            if state['phase'] != 'APPROVAL' or state['epoch'] != self.epoch or state.get('plan_hash') != plan_hash:
                raise ValueError('This Plan is no longer available for confirmation.')
            # File bytes are immutable and owner-scoped; check them again before authorization.
            for fid in state['file_ids']:
                self.file_data(actor, fid)
            state.update(phase='EXECUTING', approved_at=time.time())
            self.command(state, 'execute', {'plan': state['plan'], 'plan_hash': plan_hash})
            self.save(state)
            return state

    def authorize_operation(self, actor, tid, command_id, operation_id, plan_hash):
        with self.lock:
            state = self.get(actor, tid, device=True)
            if (state['phase'] != 'EXECUTING' or state['epoch'] != self.epoch or
                    state.get('command', {}).get('id') != command_id or state.get('plan_hash') != plan_hash or
                    operation_id not in {op['id'] for op in state['plan']}):
                raise ValueError('Execution authorization is no longer current. No further save is permitted.')
            for fid in state['file_ids']:
                self.file_data(actor, fid)
            return {'authorized': True, 'command_id': command_id, 'operation_id': operation_id}

    def cancel(self, actor, tid):
        with self.lock:
            state = self.get(actor, tid, device=True)
            if state['phase'] in {'EXECUTING', 'NEEDS_READBACK'}:
                raise ValueError('A possible save requires readback; it cannot be reset or replayed.')
            state.update(phase='CANCELLED', message='Task cancelled.')
            state.pop('command', None); self.save(state)
            return state

    def reply(self, actor, tid, text):
        with self.lock:
            state = self.get(actor, tid, device=True)
            if state['phase'] != 'QUESTION' or not isinstance(text, str) or not 1 <= len(text) <= 20000:
                raise ValueError('No question is awaiting this reply.')
            state['messages'].append(text); state['phase'] = 'PLANNING'; self.save(state)
            return state

    @staticmethod
    def public(state, actor):
        result = copy.deepcopy(state)
        for key in ('user', 'epoch', 'request_id', 'evidence', 'file_evidence', 'analysis'):
            result.pop(key, None)
        if state['device'] != actor['device']:
            result.pop('command', None)
        return result


def validate_plan(state, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 50:
        raise ValueError('A Plan needs 1–50 inspected form operations.')
    forms = {e['url']: e for e in state['evidence'] if e.get('form')}
    seen = set(); prepared = []
    for op in operations:
        if set(op) != {'url', 'changes'} or op['url'] in seen or op['url'] not in forms:
            raise ValueError('Each Plan target must be one distinct inspected form.')
        if not read_url(op['url'], state['binding']):
            raise ValueError('Unrecognized Moodle form target.')
        seen.add(op['url']); source = forms[op['url']]
        fields = {f['name']: f for f in source['form']['fields']}
        changes = op['changes']
        if not isinstance(changes, list) or not changes or len(changes) > 80:
            raise ValueError('Specify the exact inspected field changes.')
        names = set(); approved = []
        for change in changes:
            if set(change) != {'name', 'value'} or change['name'] not in fields or change['name'] in names:
                raise ValueError('A changed field was not uniquely inspected.')
            names.add(change['name']); field = fields[change['name']]; value = change['value']
            if (not isinstance(value, str) or len(value) > 20000 or field.get('disabled')
                    or re.search(r'sesskey|token|password|secret|csrf|auth', field['name'], re.I)):
                raise ValueError('This field cannot be changed through the remote form executor.')
            if field['type'] == 'checkbox' and value not in {'true', 'false'}:
                raise ValueError('Checkbox values must be true or false.')
            if field['type'] == 'select' and value not in [o['value'] for o in field['options']]:
                raise ValueError('Choose an observed option.')
            if value != field['value']:
                approved.append({'name': change['name'], 'label': field['label'], 'before': field['value'], 'value': value})
        if approved:
            prepared.append({'id': uid(), 'url': op['url'], 'title': source['title'],
                             'form': source['form'], 'changes': approved})
    if not prepared:
        raise ValueError('The proposed values already match the inspected forms.')
    return prepared
