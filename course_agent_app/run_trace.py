"""Local, redacted input/output spans. Recording never authorizes or replays tools."""
import functools
import hashlib
import json
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

SENSITIVE=re.compile(r'password|secret|sesskey|cookie|authorization|access.?token|api.?key|subscription|^code$|sender|requester_name',re.I)


class RunTrace:
    def __init__(self,root,secrets=()):
        self.root=Path(root);self.secrets=tuple(s for s in secrets if isinstance(s,str) and len(s)>3)
        self.lock=threading.RLock();self.last_error=None

    def clean(self,value,key=''):
        if SENSITIVE.search(key):return '[REDACTED]'
        if isinstance(value,dict):return {str(k):self.clean(v,str(k)) for k,v in value.items()}
        if isinstance(value,(list,tuple,set)):return [self.clean(v) for v in value]
        if isinstance(value,bytes):return {'binary_bytes':len(value),'sha256':hashlib.sha256(value).hexdigest(),'body':'not stored'}
        if isinstance(value,Path):return value.name
        if isinstance(value,str):
            for secret in self.secrets:value=value.replace(secret,'[REDACTED]')
            # A model prompt can embed JSON; redact its sensitive fields too.
            value=re.sub(r'(?i)("(?:password|secret|sesskey|cookie|authorization|access_token|api_key|code|sender|requester_name)"\s*:\s*)"(?:[^"\\]|\\.)*"',r'\1"[REDACTED]"',value)
            value=re.sub(r'(?i)\b(OK|NO)\s+\d{6}\b',r'\1 [REDACTED]',value)
            value=re.sub(r'(?i)((?:sesskey|access_token|api_key|token|password)=)[^\s&"<>]+',r'\1[REDACTED]',value)
            value=re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+',r'\1[REDACTED]',value)
            return value
        if value is None or type(value) in (int,float,bool):return value
        return {'object_type':type(value).__name__}  # Never repr(Page), callbacks or credentials.

    def write(self,run,task_id,span,event,layer,operation,payload,**meta):
        try:
            if not re.fullmatch(r'[a-f0-9]{12}|startup',str(run)):return
            with self.lock:
                folder=self.root/run;folder.mkdir(parents=True,exist_ok=True)
                artifact=span+'-'+event+'.json'
                (folder/artifact).write_text(json.dumps(self.clean(payload),ensure_ascii=False,indent=2),encoding='utf-8')
                entry={'at_ns':time.time_ns(),'run':run,'task_id':task_id,'span':span,'event':event,
                       'layer':layer,'operation':operation,'artifact':artifact,**meta}
                with (folder/'trace.jsonl').open('a',encoding='utf-8') as handle:
                    handle.write(json.dumps(self.clean(entry),ensure_ascii=False)+'\n')
                self.last_error=None
        except Exception as exc:self.last_error=type(exc).__name__

    def note(self,run,task_id,layer,operation,payload):
        self.write(run,task_id,uuid.uuid4().hex,'note',layer,operation,payload)

    def call(self,run,task_id,layer,operation,arguments,invoke):
        span=uuid.uuid4().hex;started=time.monotonic()
        self.write(run,task_id,span,'input',layer,operation,arguments)
        try:result=invoke()
        except Exception as exc:
            error={'type':type(exc).__name__,'message':str(exc),
                   'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                             for f in traceback.extract_tb(exc.__traceback__)[-12:]]}
            self.write(run,task_id,span,'error',layer,operation,error,duration_seconds=round(time.monotonic()-started,4))
            raise
        self.write(run,task_id,span,'output',layer,operation,result,duration_seconds=round(time.monotonic()-started,4))
        return result

    def wrap(self,obj,names,layer,identity,*,argument_identity=False):
        for name in names:
            original=getattr(obj,name,None)
            if not callable(original):continue
            def bind(method,operation):
                @functools.wraps(method)
                def wrapped(*args,**kwargs):
                    run,task=identity(*args,**kwargs) if argument_identity else identity()
                    return self.call(run,task,layer,operation,{'args':args,'kwargs':kwargs},lambda:method(*args,**kwargs))
                return wrapped
            setattr(obj,name,bind(original,name))


def environment_secrets(env):
    return [value for key,value in env.items() if SENSITIVE.search(key)]
