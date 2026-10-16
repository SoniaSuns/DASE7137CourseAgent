"""Authenticated localhost transport for the Chrome chat side panel.

HTTP threads only persist messages. The existing listener's main thread owns
planning, approval state and all browser work, sharing its one-task queue.
"""
from __future__ import annotations

import copy
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time

CHAT='Chrome Moodle Chat'
SENDER='local-chrome-owner'
PORT=18790
WAITING={'QUESTION','SCOPE','APPROVAL'}
ACTIVE={'PLANNING',*WAITING,'APPROVED','EXECUTING','NEEDS_READBACK'}


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
    try:
        tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        for attempt in range(6):
            try:tmp.replace(path);return
            except PermissionError:
                if attempt==5:raise
                time.sleep(.05*(attempt+1))
    finally:
        if tmp.exists():tmp.unlink()


def connection(root):
    path=Path(root)/'connection.json'
    if path.exists():
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('base_url')!=f'http://127.0.0.1:{PORT}' or not re.fullmatch(r'[a-f0-9]{64}',str(value.get('token',''))):
            raise ValueError('Invalid local chat connection settings.')
        return value
    value={'base_url':f'http://127.0.0.1:{PORT}','token':secrets.token_hex(32)}
    atomic_json(path,value)
    return value


class ChatStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'conversation.json';self.lock=threading.RLock()
        self.data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'messages':[],'commands':{}}
        # Restart never replays queued approval or a possibly consumed request.
        for command in self.data['commands'].values():
            if command['status']=='queued':command['status']='interrupted'
        self.view={'phase':'CONNECTING','connected':False,'approval':None,'reply_context':None}
        self.save()

    def save(self):atomic_json(self.path,self.data)

    def publish(self,role,text,*,run='',kind='message'):
        with self.lock:
            message={'id':secrets.token_hex(12),'role':role,'text':text,'run':run,'kind':kind,'at':time.time()}
            self.data['messages'].append(message);self.save()
            return message['id']

    def submit(self,body):
        if not isinstance(body,dict):raise ValueError('Expected a message object.')
        request_id=body.get('id');text=body.get('text');context=body.get('context')
        if not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}',request_id):
            raise ValueError('A stable message ID is required.')
        if not isinstance(text,str) or not text.strip() or len(text)>20000:raise ValueError('Enter 1–20000 characters.')
        if context is not None and (not isinstance(context,dict) or set(context)!={'run','boundary','manifest_hash','message_id'} or
                any(not isinstance(v,str) for v in context.values())):raise ValueError('Invalid reply context.')
        payload={'id':request_id,'text':text.strip(),'context':context}
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        with self.lock:
            old=self.data['commands'].get(request_id)
            if old:
                if old['digest']!=digest:raise ValueError('This message ID already has different content.')
                return {'id':request_id,'status':old['status']}
            self.data['commands'][request_id]={**payload,'digest':digest,'status':'queued'}
            self.data['messages'].append({'id':'user-'+request_id,'role':'user','text':payload['text'],'run':'','kind':'message','at':time.time()})
            self.save()
        return {'id':request_id,'status':'queued'}

    def snapshot(self):
        with self.lock:
            value=copy.deepcopy(self.view)
            value['messages']=copy.deepcopy(self.data['messages'][-200:])
            value['interrupted_messages']=sum(c['status']=='interrupted' for c in self.data['commands'].values())
            value['pending_messages']=sum(c['status']=='queued' for c in self.data['commands'].values())
            return value


class PanelBridge:
    def __init__(self,root,*,port=PORT):
        self.store=ChatStore(root);self.config=connection(root);self.port=port
        self.server=None;self.thread=None

    def start(self):
        bridge=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,status,body):
                data=json.dumps(body,ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                origin=self.headers.get('Origin','')
                if re.fullmatch(r'chrome-extension://[a-p]{32}',origin):
                    self.send_header('Access-Control-Allow-Origin',origin)
                    self.send_header('Vary','Origin')
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            def allowed(self,auth=True):
                if self.headers.get('Host')!=f'127.0.0.1:{bridge.server.server_port}':return False
                # Chrome host-permission fetches can omit Origin. They still
                # require the private bearer token; browser-page preflights and
                # all explicit non-extension origins remain forbidden.
                origin=self.headers.get('Origin')
                if origin is not None and not re.fullmatch(r'chrome-extension://[a-p]{32}',origin):return False
                if not auth and origin is None:return False
                bearer=self.headers.get('Authorization','')
                return not auth or (bool(re.fullmatch(r'Bearer [a-f0-9]{64}',bearer)) and
                                    secrets.compare_digest(bearer,'Bearer '+bridge.config['token']))
            def do_OPTIONS(self):
                if not self.allowed(False):return self.respond(403,{'error':'Forbidden origin.'})
                self.send_response(204)
                self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
                self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type')
                self.send_header('Vary','Origin');self.end_headers()
            def do_GET(self):
                if not self.allowed():return self.respond(403,{'error':'Connection is not authorized.'})
                if self.path!='/v1/state':return self.respond(404,{'error':'Not found.'})
                self.respond(200,bridge.store.snapshot())
            def do_POST(self):
                if not self.allowed():return self.respond(403,{'error':'Connection is not authorized.'})
                if self.path!='/v1/messages':return self.respond(404,{'error':'Not found.'})
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.respond(415,{'error':'Use JSON.'})
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=100000:raise ValueError('Message is too large or empty.')
                    self.connection.settimeout(5)
                    body=json.loads(self.rfile.read(size).decode('utf-8'))
                    result=bridge.store.submit(body)
                except (ValueError,UnicodeError,TimeoutError) as exc:
                    return self.respond(400,{'error':str(exc)})
                self.respond(202,result)
        self.server=ThreadingHTTPServer(('127.0.0.1',self.port),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,name='moodle-chat-http',daemon=True)
        self.thread.start();return self

    def close(self):
        if self.server:self.server.shutdown();self.server.server_close()
        if self.thread:self.thread.join(timeout=3)

    def publish(self,runtime,text,requester=None):
        from chrome_approval import approval_message
        flow=runtime.flows[CHAT];state=flow.state
        action=requester is None and state.get('phase') in WAITING
        kind='approval' if action and state.get('phase')=='APPROVAL' and text==approval_message(state) else 'message'
        mid=self.store.publish('assistant',text,run=state.get('run',''),kind=kind)
        if action:
            state['reply_boundary']=secrets.token_hex(12)
            state['panel_action_message_id']=mid
        if kind=='approval':state['panel_approval_message_id']=mid
        state['delivery_verified_ids']=[mid];state['delivery_parts_count']=1
        flow.save()
        return True

    def refresh(self,runtime):
        state=runtime.flows[CHAT].state;phase=state.get('phase','IDLE')
        context=None;approval=None
        if phase in WAITING and state.get('reply_boundary'):
            context={'run':state['run'],'boundary':state['reply_boundary'],
                     'manifest_hash':state.get('manifest_hash',''),
                     'message_id':state.get('panel_approval_message_id','') if phase=='APPROVAL' else state.get('panel_action_message_id','')}
            if phase=='APPROVAL' and state.get('approval_sent') and context['message_id']:
                approval={'code':state['code'],'context':context}
        observer=runtime.observer.snapshot()['tasks'].get(CHAT,{})
        other=[c for c,f in runtime.flows.items() if c!=CHAT and f.state.get('phase') in ACTIVE]
        with self.store.lock:
            self.store.view={'connected':True,'phase':phase,'task_id':state.get('task_id',''),
                'approval':approval,'reply_context':context,'progress':copy.deepcopy(observer),
                'other_task_active':bool(other),'queued':sum(r['chat']==CHAT for r in runtime.queue),
                'courses':[{'id':'140147','name':'Class A'},{'id':'140148','name':'Class B'}]}

    def drain(self,runtime):
        # This method runs on the listener thread, never on an HTTP thread.
        flow=runtime.flows[CHAT]
        with self.store.lock:
            pending=[copy.deepcopy(c) for c in self.store.data['commands'].values() if c['status']=='queued']
        for command in pending:
            state=flow.state;context=command['context'];text=command['text'];error=''
            protocol=bool(re.fullmatch(r'(?:OK|NO)\s+\d{6}',text,re.I))
            waiting=state.get('phase') in WAITING
            expected={'run':state.get('run',''),'boundary':state.get('reply_boundary',''),
                      'manifest_hash':state.get('manifest_hash',''),
                      'message_id':state.get('panel_approval_message_id','') if state.get('phase')=='APPROVAL' else state.get('panel_action_message_id','')}
            if context is not None and (not waiting or not expected['boundary'] or context!=expected):
                error='这条回复对应的计划已改变，请查看最新消息后再回复。'
            elif waiting and context is None:
                error='请刷新当前问题或计划后回复。'
            elif protocol and (state.get('phase')!='APPROVAL' or not state.get('approval_sent') or
                              text.split()[-1]!=state.get('code') or context!=expected):
                error='当前没有与这条确认匹配的计划，请查看最新 Plan。'
            record={'chat':CHAT,'id':'panel:'+command['id'],'sender':SENDER,'requester_name':'You',
                    'text':text,'self_mentioned':True,'origin':'authenticated_chrome_panel'}
            if context is not None:
                record.update(reply_to_run=context['run'],reply_boundary=context['boundary'])
            if error:
                self.store.publish('assistant',error,kind='error')
            elif not any(r['id']==record['id'] for r in runtime.queue) and record['id'] not in state.get('message_ids',[]):
                runtime.queue.append(record);runtime.save_queue()
            with self.store.lock:
                self.store.data['commands'][command['id']]['status']='rejected' if error else 'accepted'
                self.store.save()
        if runtime.readable_chats is None:runtime.readable_chats=set()
        runtime.readable_chats.add(CHAT)
        self.refresh(runtime)


def attach(runtime,root,*,port=PORT):
    from chrome_approval import ChromeApproval
    from chrome_moodle import digest
    from chrome_task_memory import task_code
    bridge=PanelBridge(root,port=port)
    flow=ChromeApproval(runtime.root/(digest(CHAT)[:12]+'.json'),CHAT)
    runtime.flows[CHAT]=flow
    # Local authentication supplies the trusted origin; the existing same-run
    # reply guard and immutable approval state still apply to this channel.
    runtime.mention_only_chats.add(CHAT)
    def saved(state):
        runtime.observer.sync(CHAT,state)
        runtime.trace.note(state.get('run','startup'),task_code(state),'workflow','panel_state',
                           {'phase':state.get('phase'),'results':state.get('results',[])})
    flow.on_save=saved;saved(flow.state)
    runtime.panel=bridge
    bridge.refresh(runtime)
    return bridge.start()
