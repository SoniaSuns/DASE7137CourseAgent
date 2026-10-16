"""Authenticated remote workspace. Browser work is delivered ONLY to its client.

Listen on loopback, with an optional exact private LAN interface and subnet.
No imports of SharedChrome, MoodleBrowser, owner library or WhatsApp runtime.
"""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlparse

from remote_workspace import Workspace, digest, read_url, validate_plan
from remote_planner import RemotePlanner, analyze_file
from remote_network import lan_config, allowed_peer


class Gateway:
    def __init__(self, root, env, config, planner_factory=RemotePlanner):
        self.root = Path(root); self.env = dict(env); self.config = config
        self.store = Workspace(root)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='remote-planner')
        self.planner_factory = planner_factory
        self.jobs = set(); self.jobs_lock = threading.Lock(); self.stopping = False
        self.server = None; self.thread = None; self.lan_server = None

    def schedule(self, state):
        if state['phase'] != 'PLANNING' or self.stopping:
            return
        with self.jobs_lock:
            if state['id'] in self.jobs:
                return
            self.jobs.add(state['id'])
        self.pool.submit(self.reason, state['id'], {'user': state['user'], 'device': state['device']})

    def reason(self, tid, actor):
        try:
            root = self.root/'users'/actor['user']/'tasks'/tid
            planner = self.planner_factory(self.env, root/'planner')
            while not self.stopping:
                with self.store.lock:
                    state = self.store.get(actor, tid, device=True)
                    if state['phase'] != 'PLANNING':
                        return
                    if state['rounds'] >= 16:
                        raise ValueError('Task reached its analysis limit. Findings were retained; narrow the remaining request.')
                    state['rounds'] += 1; self.store.save(state)
                files = [self.store.file_data(actor, fid)[0] for fid in state['file_ids']]
                context = {k: state.get(k) for k in ('text', 'binding', 'evidence', 'messages', 'file_evidence', 'analysis')}
                context['files'] = [{k: f[k] for k in ('id', 'name', 'bytes', 'sha')} for f in files]
                decision = planner(context)
                action = decision['action']
                if action in {'file', 'analyze'}:
                    if decision['file_id'] not in state['file_ids']:
                        raise ValueError('Planner selected a file outside this task.')
                    entry, data = self.store.file_data(actor, decision['file_id'])
                    if action == 'file':
                        from chrome_file_library import text_chunks
                        offset = decision['offset']
                        if type(offset) is not int or not 0 <= offset <= 500000:
                            raise ValueError('Invalid file page offset.')
                        text = ''
                        try:
                            for chunk in text_chunks(data, Path(entry['name']).suffix.lower()):
                                text += chunk+'\n'
                                if len(text) > offset+24000:
                                    break
                            evidence = {'id': entry['id'], 'name': entry['name'], 'sha': entry['sha'],
                                        'offset': offset, 'text': text[offset:offset+24000],
                                        'next_offset': offset+24000 if len(text) > offset+24000 else None}
                        except Exception:
                            evidence = {'id': entry['id'], 'status': 'reader_unavailable', 'next': 'Use analyze for autonomous Python parsing.'}
                        key = 'file_evidence'
                    else:
                        evidence = analyze_file(self.env, root/'analysis', state, entry, data, decision['message'])
                        evidence.pop('workspace', None); key = 'analysis'
                    with self.store.lock:
                        current = self.store.get(actor, tid, device=True)
                        if current['phase'] != 'PLANNING':
                            return
                        current.setdefault(key, []).append(evidence); self.store.save(current)
                    continue
                with self.store.lock:
                    current = self.store.get(actor, tid, device=True)
                    if current['phase'] != 'PLANNING':
                        return
                    current['message'] = str(decision['message'])[:20000]
                    current['unresolved'] = list(dict.fromkeys(str(x)[:2000] for x in decision['unresolved']))[:100]
                    if action == 'inspect':
                        links = {l['url']: dict(l, source_url=e['url']) for e in current['evidence'] for l in e.get('links', [])}
                        url = decision['url']
                        if url not in links:
                            raise ValueError('Planner requested an unobserved browser source.')
                        if links[url].get('kind') == 'download':
                            self.store.command(current, 'download', {'url': url, 'source_url': links[url]['source_url']})
                        elif read_url(url, current['binding']):
                            self.store.command(current, 'read', {'url': url})
                        else:
                            raise ValueError('The observed link has no supported read operation.')
                        current['phase'] = 'READING'
                    elif action == 'plan':
                        current['plan'] = validate_plan(current, decision['operations'])
                        current['plan_hash'] = digest({'binding': current['binding'], 'plan': current['plan'], 'epoch': current['epoch']})
                        current['phase'] = 'APPROVAL'
                    elif action == 'question':
                        current['phase'] = 'QUESTION'
                    elif action == 'finish':
                        if decision['operations']:
                            raise ValueError('Read-only completion cannot include operations.')
                        current['phase'] = 'PARTIAL' if current['unresolved'] else 'DONE'
                    else:
                        raise ValueError('Unknown planning decision.')
                    self.store.save(current)
                return
        except Exception as exc:
            with self.store.lock:
                state = self.store.get(actor, tid)
                if state['phase'] == 'PLANNING':
                    state.update(phase='FAILED', message='Analysis stopped: '+(str(exc)[:1000] if isinstance(exc, ValueError) else type(exc).__name__+'. Local diagnostic retained.'))
                    self.store.save(state)
        finally:
            with self.jobs_lock:
                self.jobs.discard(tid)
            # A browser reply can arrive between publishing READING and this
            # worker releasing its slot. Never lose that new planning request.
            if not self.stopping:
                self.schedule(self.store.get(actor, tid))

    def dispatch(self, method, path, body, token):
        if method == 'POST' and path in {'/v1/login', '/v1/register'}:
            return self.store.login(body.get('name'), body.get('password'), body.get('device_id'),
                                    body.get('invite', '') if path.endswith('register') else None)
        actor = self.store.auth(token)
        if method == 'POST' and path == '/v1/logout':
            self.store.logout(token); return {'ok': True}
        if method == 'GET' and path == '/v1/workspace':
            return {'files': self.store.files(actor), 'tasks': [self.store.public(s, actor) for s in self.store.list_tasks(actor)],
                    'device_id': actor['device'], 'execution_location': 'your_browser', 'server_epoch': self.store.epoch}
        if method == 'POST' and path == '/v1/files':
            try:
                data = base64.b64decode(body['data'], validate=True)
            except (ValueError, KeyError, TypeError):
                raise ValueError('Invalid file encoding.') from None
            return self.store.upload(actor, body.get('name'), data)
        match = re.fullmatch(r'/v1/files/([a-f0-9]{32})/delete', path)
        if method == 'POST' and match:
            self.store.delete_file(actor, match[1]); return {'ok': True}
        if method == 'POST' and path == '/v1/tasks':
            state = self.store.create(actor, body.get('text'), body.get('binding'), body.get('file_ids', []), body.get('request_id'))
            return self.store.public(state, actor)
        match = re.fullmatch(r'/v1/tasks/([a-f0-9]{32})/(receipt|approve|cancel|reply|download|reconcile|authorize)', path)
        if method == 'POST' and match:
            tid, action = match.groups()
            if action == 'authorize':
                return self.store.authorize_operation(actor, tid, body.get('command_id'), body.get('operation_id'), body.get('plan_hash'))
            if action == 'receipt':
                state = self.store.receipt(actor, tid, body.get('command_id'), body.get('result', {}))
            elif action == 'approve':
                state = self.store.approve(actor, tid, body.get('plan_hash'))
            elif action == 'cancel':
                state = self.store.cancel(actor, tid)
            elif action == 'reply':
                state = self.store.reply(actor, tid, body.get('text'))
            elif action == 'reconcile':
                state = self.store.reconcile(actor, tid)
            else:
                state = self.store.download_receipt(actor, tid, body)
            self.schedule(state)
            return self.store.public(state, actor)
        raise ValueError('Unknown workspace endpoint.')

    def start(self):
        gateway = self
        lan = lan_config(self.config)
        public = self.config.get('public_origin', '')
        if public and (urlparse(public).scheme != 'https' or public != 'https://'+urlparse(public).netloc or urlparse(public).username):
            raise ValueError('public_origin must be an exact HTTPS origin.')
        self.auth_attempts = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, data):
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                origin = self.headers.get('Origin', '')
                if re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers(); self.wfile.write(raw)

            def allowed(self):
                hosts = {f'127.0.0.1:{gateway.server.server_port}'}
                if lan:
                    hosts.add(f'{lan[0]}:{gateway.server.server_port}')
                if public:
                    hosts.add(urlparse(public).netloc)
                origin = self.headers.get('Origin')
                return (allowed_peer(self.client_address[0], lan) and self.headers.get('Host') in hosts
                        and (origin is None or re.fullmatch(r'chrome-extension://[a-p]{32}', origin)))

            def do_OPTIONS(self):
                if not self.allowed() or not self.headers.get('Origin'):
                    return self.respond(403, {'error': 'Forbidden origin.'})
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Vary', 'Origin'); self.end_headers()

            def handle_request(self, method):
                if not self.allowed():
                    return self.respond(403, {'error': 'Forbidden origin or host.'})
                try:
                    body = None
                    if method == 'POST':
                        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                            raise ValueError('Use JSON.')
                        size = int(self.headers.get('Content-Length', '0'))
                        limit = 46*1024*1024 if self.path == '/v1/files' or self.path.endswith('/download') else 1000000
                        if not 0 < size <= limit:
                            raise ValueError('Request is empty or too large.')
                        if self.path not in {'/v1/login', '/v1/register'}:
                            gateway.store.auth(self.headers.get('Authorization', '').removeprefix('Bearer '))
                        self.connection.settimeout(30)
                        body = json.loads(self.rfile.read(size).decode('utf-8'))
                        if not isinstance(body, dict):
                            raise ValueError('Expected a JSON object.')
                    if self.path in {'/v1/login', '/v1/register'}:
                        with gateway.store.lock:
                            gateway.auth_attempts = [t for t in gateway.auth_attempts if t > time.time()-60]
                            if len(gateway.auth_attempts) >= 30:
                                return self.respond(429, {'error': 'Too many login attempts; try later.'})
                            gateway.auth_attempts.append(time.time())
                    result = gateway.dispatch(method, self.path, body, self.headers.get('Authorization', '').removeprefix('Bearer '))
                    self.respond(200, result)
                except PermissionError:
                    self.respond(401, {'error': 'Please sign in again.'})
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    self.respond(400, {'error': str(exc)[:1000] if isinstance(exc, ValueError) else 'Invalid request.'})
                except Exception:
                    self.respond(500, {'error': 'Workspace request failed; no automatic save retry.'})

            def do_GET(self):
                self.handle_request('GET')

            def do_POST(self):
                self.handle_request('POST')
        self.server = ThreadingHTTPServer(('127.0.0.1', self.config.get('port', 18791)), Handler)
        if lan:
            try:
                self.lan_server = ThreadingHTTPServer((lan[0], self.server.server_port), Handler)
            except Exception:
                self.server.server_close(); self.server = None
                raise
            self.lan_server.daemon_threads = True
            threading.Thread(target=self.lan_server.serve_forever, daemon=True, name='remote-workspace-lan').start()
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name='remote-workspace-http')
        self.thread.start()
        return self

    def close(self):
        self.stopping = True
        if self.lan_server:
            self.lan_server.shutdown(); self.lan_server.server_close()
        if self.server:
            self.server.shutdown(); self.server.server_close()
        self.pool.shutdown(wait=True)
        self.store.close()
