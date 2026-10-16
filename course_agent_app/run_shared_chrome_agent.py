"""Small browser-first entry based on origin_course_agent, isolated from v1."""
from __future__ import annotations

import argparse
import copy
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
import re
import secrets
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
IMPLEMENTATION_VERSION='2026-10-12-edited-mentions-r11'
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parent))

from chrome_approval import ChromeApproval, approval_message, scope_message
from chrome_task_memory import TaskMemory,task_code,command as task_command,continuation,description as task_description,operation_memory,result_summary
from chrome_group_changes import GroupChanges
from chrome_structure import CourseStructure,MoveVisibilityConflict
from chrome_section_visibility import SectionVisibility
import chrome_schedule_alignment as schedule_alignment
import chrome_deferred_items as deferred_items

GOAL_FIELDS=('goal_scope','schedule_required','schedule_contract','goal_followups','exploration_required','exploration_sources')
from chrome_resources import ResourceFiles
from chrome_moodle import MoodleBrowser, digest, MoodleFormRejected, MoodleSubmissionUnconfirmed, MoodleSaveNotDispatched
from chrome_form_dates import validate as validate_dates, MoodleDateConflict, interval_evidence
from chrome_planner import CodexPlanner
from chrome_course_scope import MoodleLoginRequired
from chrome_file_library import FileLibrary, FileLibraryChanged, DEFAULT_ROOT
from chrome_mentions import MESSAGE_BODY_SCRIPT, discover_account_name, observed_chat_type
from shared_chrome import SharedChrome
from listener_health import ListenerHealth, HealthHeartbeat
from task_observability import TaskObserver,task_status_text
from run_trace import RunTrace,environment_secrets
from chrome_delivery import send_message, matching_outgoing_ids, WhatsAppSendDeferredError
from whatsapp_web import (open_chat, message_records, record_message_id,
                                    scroll_chat_to_bottom, wait_for_whatsapp_ready, WhatsAppSendUnconfirmedError,
                                    whatsapp_safe_compose_text)


def load_config():
    config=json.loads((ROOT/'chrome_operator_config.json').read_text(encoding='utf-8'))
    if not config.get('chats') or len(config['chats'])!=len(set(config['chats'])):
        raise ValueError('Configure distinct WhatsApp chat names.')
    from chrome_course_scope import course
    for cid in config['course_ids']: course(cid)
    config.setdefault('file_library_root',str(DEFAULT_ROOT))
    config.setdefault('file_library_planner_access',False)
    config.setdefault('mention_only_chats',[])
    config.setdefault('recent_result_limit',10)
    config.setdefault('approval_timeout_seconds',None)
    config.setdefault('defer_uncertain_items',False)
    config.setdefault('chrome_chat_enabled',False)
    config.setdefault('remote_workspace', {'enabled': False, 'port': 18791, 'public_origin': ''})
    remote = config['remote_workspace']
    if (not isinstance(remote,dict) or type(remote.get('enabled')) is not bool or
            type(remote.get('port')) is not int or not 1024 <= remote['port'] <= 65535 or
            remote['port'] in {18789,18790} or not isinstance(remote.get('public_origin'),str)):
        raise ValueError('Configure remote_workspace enabled, distinct port and HTTPS public_origin.')
    from remote_network import lan_config
    lan_config(remote)
    config.setdefault('autonomous_exploration',{'enabled':False,'max_calls':6,'timeout_seconds':300})
    exploration=config['autonomous_exploration']
    if (not isinstance(exploration,dict) or type(exploration.get('enabled')) is not bool or
            type(exploration.get('max_calls')) is not int or not 1<=exploration['max_calls']<=20 or
            type(exploration.get('timeout_seconds')) is not int or not 30<=exploration['timeout_seconds']<=900):
        raise ValueError('Configure autonomous_exploration with enabled, max_calls (1-20) and timeout_seconds (30-900).')
    if type(config['chrome_chat_enabled']) is not bool:
        raise ValueError('chrome_chat_enabled must be a boolean.')
    if type(config['defer_uncertain_items']) is not bool:
        raise ValueError('defer_uncertain_items must be a boolean.')
    timeout=config['approval_timeout_seconds']
    if timeout is not None and (type(timeout) is not int or timeout<=0):
        raise ValueError('approval_timeout_seconds must be null (no limit) or a positive integer.')
    if type(config['recent_result_limit']) is not int or not 5<=config['recent_result_limit']<=10:
        raise ValueError('recent_result_limit must be an integer from 5 to 10.')
    if not isinstance(config['mention_only_chats'],list) or not set(config['mention_only_chats'])<=set(config['chats']):
        raise ValueError('Mention-only chats must be configured monitored chats.')
    if type(config['file_library_planner_access']) is not bool:
        raise ValueError('file_library_planner_access must be a boolean.')
    env=dict(os.environ)
    # Reuse original local configuration without printing it.
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1); env.setdefault(k.strip(),v.strip())
    # Persist the listener's selected model across operator shells/restarts.
    # A stage-specific environment override remains explicit operator authority.
    if 'planner_model' in config:
        model=config['planner_model']
        if not isinstance(model,str) or not model.strip():raise ValueError('planner_model must be a nonempty model ID.')
        if not env.get('CODEX_PLANNER_MODEL','').strip():env['CODEX_PLANNER_MODEL']=model.strip()
    return config,env


def record_time(record, now):
    # Parse browser metadata only. Never infer observation time from staff text.
    for source in (str(record.get('meta') or ''),str(record.get('timestamp') or '')):
        match=re.search(r'(?:(上午|下午)\s*)?(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[,，]\s*(.+)',source,re.I)
        if not match:continue
        local_period,hour,minute,period,date=match.groups()
        hour=int(hour);minute=int(minute)
        if period or local_period:
            hour=hour%12+(12 if (period or '').upper()=='PM' or local_period=='下午' else 0)
        year_first=re.match(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})(?:日)?',date)
        year_last=re.match(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})',date)
        if year_first:
            year,month,day=map(int,year_first.groups());dates=[(year,month,day)]
        elif year_last:
            first,second,year=map(int,year_last.groups());dates=[(year,first,second),(year,second,first)]
        else:continue
        candidates=[]
        for year,month,day in dates:
            try:candidates.append(datetime(year,month,day,hour,minute,tzinfo=now.tzinfo))
            except ValueError:pass
        if candidates:return min(candidates,key=lambda value:abs(value-now))
    return None


def request_record(record, started_at=None):
    """Mechanical provenance filtering, never staff phrase classification."""
    if record.get('direction')!='incoming': return None
    mid=record_message_id(record)
    sender=str(record.get('sender_id') or record.get('sender_display_name') or '').strip()
    text=str(record.get('message_text') if 'message_text' in record else record.get('text') or '').strip()
    if not mid or not sender or not text: return None
    if started_at:
        observed=record_time(record,datetime.now(started_at.tzinfo))
        if observed is None or observed<started_at.replace(second=0,microsecond=0):return None
    return {'id':mid,'sender':sender,'text':text}


def intake_records(page, account_name=''):
    records=message_records(page)
    bodies=page.evaluate(MESSAGE_BODY_SCRIPT,{'ids':[record_message_id(r) for r in records],'account_name':account_name})
    for record in records:
        body=bodies.get(record_message_id(record))
        if body:
            record['message_text']=body['text']
            record['self_mentioned']=body['self_mentioned']
            record['addressed_text']=body['addressed_text']
            if body['meta']:record['meta']=body['meta']
            sender=re.search(r'\]\s*(.*?):\s*$',record.get('meta',''))
            if sender and not record.get('sender_id'):record['sender_id']=sender.group(1).strip()
    return records


def user_reason(exc):
    if type(exc).__module__.startswith('playwright'):
        return 'The browser could not complete the current page action. Connection or login failure has not been established.'
    if isinstance(exc,(ValueError,RuntimeError)) and not isinstance(exc,json.JSONDecodeError):return str(exc)
    return 'The current step did not complete ('+type(exc).__name__+'). No automatic save retry was performed.'


class WhatsAppIntakeUnavailable(ValueError):
    pass


class Runtime:
    def __init__(self, wa, browser, chats, course_ids, planner, state_dir, pool, library=None, library_planner_access=False, mention_only_chats=(),recent_result_limit=10):
        self.wa=wa; self.browser=browser; self.groups=GroupChanges(browser)
        self.structure=CourseStructure(browser);self.resources=ResourceFiles(browser,library)
        self.section_visibility=SectionVisibility(browser)
        self.chats=chats; self.course_ids=course_ids; self.planner=planner; self.pool=pool
        self.root=Path(state_dir); self.root.mkdir(parents=True,exist_ok=True)
        self.task_memory=TaskMemory(self.root);self.task_memory.migrate()
        self.recent_result_limit=recent_result_limit
        self.flows={c:ChromeApproval(self.root/(digest(c)[:12]+'.json'),c) for c in chats}
        self.seen={c:set() for c in chats}
        self.message_revisions={c:{} for c in chats}
        self.message_bodies={c:{} for c in chats}
        self.jobs={}; self.queue=[]; self.stopped=False
        self.transport_error=''
        self.health=ListenerHealth(chats)
        self.observer=TaskObserver(self.root/'observability')
        self.health.task_observer=self.observer
        for chat,flow in self.flows.items():
            flow.on_save=lambda state,c=chat:self.observer.sync(c,state)
            self.observer.sync(chat,flow.state)
        self.recover_transport=None
        self.readable_chats=None
        self.mention_only_chats=set(mention_only_chats)
        self.chat_types={}; self.account_name=''
        self.identity_retry_at=0
        self.library=library
        self.library_planner_access=library_planner_access
        self.file_snapshot=None; self.file_evidence=[]
        self.auto_block_recovery=False
        self.defer_uncertain_items=False
        self.explorer=None;self.exploration_max_calls=6
        self.exploration_broker=None;self.exploration_run=None
        self.block_recovery_root=ROOT/'reports/auto_recovery'
        self.restart_recovery=None
        self.planning_validation={}
        self.trace=RunTrace(self.root/'traces',environment_secrets(browser.login_env if isinstance(browser,MoodleBrowser) else {}))
        def record_state(chat,state):
            self.observer.sync(chat,state)
            self.trace.note(state.get('run','startup'),task_code(state),'workflow','state',
                {key:state[key] for key in ('phase','reason','results','verified_previous_results','write_started','expires') if key in state})
        for chat,flow in self.flows.items():flow.on_save=lambda state,c=chat:record_state(c,state)
        self.trace_chat=None
        from contextvars import ContextVar
        from functools import wraps
        trace_context=ContextVar('trace_chat',default=None)
        def identity():
            flow=self.flows.get(trace_context.get() or self.trace_chat)
            return (flow.state.get('run','startup'),task_code(flow.state)) if flow else ('startup','')
        def chat_identity(chat,*args,**kwargs):
            state=self.flows[chat].state
            return state.get('run','startup'),task_code(state)
        if isinstance(browser,MoodleBrowser):self.trace.wrap(browser,['overview','read_layout','inspect','resolve','preflight','prepare','execute','verify','submit_and_wait'],'moodle_browser',identity)
        if isinstance(library,FileLibrary):self.trace.wrap(library,['snapshot','inspect','validate_reads'],'file_library',identity)
        for capability in (self.structure,self.section_visibility,self.groups,self.resources):
            self.trace.wrap(capability,['resolve','preflight','execute','verify','visibility_drift'],'capability',identity)
        self.trace.wrap(self,['accept_decision','ensure_plan_evidence'],'validation',chat_identity,argument_identity=True)
        self.trace.wrap(self,['send'],'whatsapp_delivery',chat_identity,argument_identity=True)
        self.trace.wrap(self,['handle_task_command'],'workflow',chat_identity,argument_identity=True)
        def scoped(method):
            @wraps(method)
            def invoke(chat,*args,**kwargs):
                token=trace_context.set(chat)
                try:return method(chat,*args,**kwargs)
                finally:trace_context.reset(token)
            return invoke
        for method in ('start','resume_selected','finish_selected_resume','accept_decision','ensure_plan_evidence',
                       'execute','reconcile','render_pending','send','handle_task_command','preserve_remaining_task','repair_blocked_request'):
            setattr(self,method,scoped(getattr(self,method)))
        self.started=datetime.now(ZoneInfo('Asia/Hong_Kong'))
        self.queue_path=self.root/'inbox.json'
        self.interrupted_queue_path=None
        # Restart never executes an old queued request silently. Keep it available for diagnosis.
        if self.queue_path.exists():
            archived=self.root/('interrupted-inbox-'+str(time.time_ns())+'.json')
            self.queue_path.replace(archived)
            self.interrupted_queue_path=archived

    def recover_question_reply(self,run_id):
        """Explicit operator recovery, only after full outgoing readback."""
        from question_recovery import validate_question_recovery
        if self.jobs or self.queue or not self.interrupted_queue_path:
            raise ValueError('Question recovery requires idle startup and this restart archive.')
        records=json.loads(self.interrupted_queue_path.read_text(encoding='utf-8'))
        state,reply=validate_question_recovery([f.state for f in self.flows.values()],records,run_id,restarted=True)
        chat=state['chat'];flow=self.flows[chat];pending=state['pending_delivery']
        self.open(chat)
        matched=matching_outgoing_ids(self.wa,whatsapp_safe_compose_text(pending['text']),
                                      expand=True,exclude_ids=pending['exclude_ids'])
        if len(matched)!=1:raise ValueError('The complete new outgoing question could not be uniquely verified.')
        # Only this already-sent question and its explicitly mentioned answer are
        # restored. No previous approval, selected write, or possible save is eligible.
        state.update(phase='QUESTION',reply_boundary=secrets.token_hex(12),
                     delivery_verified_ids=sorted(matched),delivery_parts_count=1)
        state.pop('pending_delivery')
        flow.save()
        self.recover_queued_message(reply['id'])

    def recover_queued_message(self,message_id):
        """Operator-authorized recovery from this restart's own archived queue only."""
        if not self.interrupted_queue_path:raise ValueError('No interrupted queue is available.')
        records=json.loads(self.interrupted_queue_path.read_text(encoding='utf-8'))
        matches=[r for r in records if r.get('id')==message_id]
        if len(matches)!=1:raise ValueError('One exact queued message is required.')
        record=matches[0]
        if (record.get('chat') not in self.chats or record.get('self_mentioned') is not True or
            not record.get('sender') or not record.get('text') or
            re.fullmatch(r'(?:OK|NO)\s+\d{6}',record['text'].strip(),re.I) or
            any(r.get('id')==message_id for r in self.queue)):
            raise ValueError('Only an authenticated mentioned request may be recovered; never an approval.')
        self.queue.insert(0,record);self.save_queue()

    def open(self,chat):
        try:open_chat(self.wa,chat)
        except RuntimeError:
            raise WhatsAppIntakeUnavailable('The configured chat could not be opened uniquely.') from None
        # Original helper can fall back to a click. Verify the exact header after it.
        header=self.wa.locator('#main header')
        exact=header.get_by_text(chat,exact=True)
        if exact.count()!=1:
            raise WhatsAppIntakeUnavailable('WhatsApp did not open the exact configured chat.')
        scroll_chat_to_bottom(self.wa)

    def baseline(self):
        from playwright.sync_api import Error as BrowserError
        for chat in self.chats:
            for attempt in range(3):
                try:
                    self.open(chat)
                    break
                except (BrowserError,WhatsAppIntakeUnavailable) as exc:
                    self.record_failure(chat,exc,'startup_chat_read')
                    if attempt==2 or not self.recover_transport:raise
                    # Startup needs the same recovery as steady-state intake.
                    # The transport preserves drafts and never clicks Send.
                    with self.health.work('recovering_whatsapp'):
                        self.recover_transport('reload' if attempt==0 else 'reconnect')
            kind=observed_chat_type(self.wa)
            self.chat_types[chat]=kind
            # Unknown UI is never silently treated as a direct conversation.
            if kind!='direct':self.mention_only_chats.add(chat)
            records=intake_records(self.wa,self.account_name)
            self.seen[chat]={record_message_id(r) for r in records}
            self.message_revisions[chat]={record_message_id(r):self.message_revision(r) for r in records}
            self.message_bodies[chat]={record_message_id(r):self.message_body_revision(r) for r in records}
            print(f"Chat ready: {chat}; visible records={len(records)}; timestamp evidence={sum(record_time(r,self.started) is not None for r in records)}",flush=True)

    def send(self,chat,text,*,delivery=None,requester=None):
        from playwright.sync_api import Error as BrowserError
        if chat in self.mention_only_chats and not self.flows[chat].state.get('mention_authorized') and not (requester and requester.get('self_mentioned')):
            return
        state=self.flows[chat].state
        if self.auto_block_recovery and text.startswith('Status: Blocked'):
            self.queue_block_recovery(chat,text)
            text='Status: Waiting\nTask: '+state.get('task','')+'\nChange: The blocked step has been queued for automatic diagnosis and repair. You do not need to repeat the request.'
        if text.startswith('Status:') and state.get('task') and not delivery:
            # Omit only the exact repeated request, never the findings or a
            # persisted multipart message whose delivery must resume unchanged.
            repeated='\nTask: '+state['task']
            if repeated+'\n' in text:text=text.replace(repeated+'\n','\n',1)
            elif text.endswith(repeated):text=text[:-len(repeated)]
        if text.startswith('Status:') and state.get('run') and '\nTask ID:' not in text:
            first,separator,rest=text.partition('\n')
            text=first+'\nTask ID: '+task_code(state)+(separator+rest if separator else '')
        action_required=requester is None and state.get('phase') in {'SCOPE','QUESTION','APPROVAL'}
        panel=getattr(self,'panel',None)
        if panel:
            from chrome_chat_bridge import CHAT
            if chat==CHAT:return panel.publish(self,text,requester=requester)
        try:
            try:self.open(chat)
            except (BrowserError,WhatsAppIntakeUnavailable):
                raise WhatsAppSendDeferredError('The chat is unavailable before sending; delivery is deferred.') from None
            if chat in self.mention_only_chats and (action_required or requester):
                recipient=requester or state
                receipt=send_message(self.wa,text,mention_name=recipient.get('requester_name') or recipient.get('sender',''),chat_type='group',resume=delivery)
            else:receipt=send_message(self.wa,text,resume=delivery)
        except WhatsAppSendUnconfirmedError as exc:
            state['pending_delivery']={'text':text,'phase':state.get('phase','IDLE'),'action_required':action_required,
                'send_attempted':getattr(exc,'send_attempted',True),
                'exclude_ids':getattr(exc,'delivery_baseline',[]),
                'is_approval':state.get('phase')=='APPROVAL' and text==approval_message(state),
                'is_scope':state.get('phase')=='SCOPE' and text==scope_message(state)}
            if requester:state['pending_delivery']['requester']=requester
            if hasattr(exc,'delivery_parts'):
                state['pending_delivery'].update(parts=exc.delivery_parts,next_part=exc.next_part,part_receipts=exc.part_receipts)
            elif delivery:
                state['pending_delivery'].update({k:delivery[k] for k in ('parts','next_part','part_receipts')})
            self.flows[chat].save()
            # A terminal/status notification must not kill the listener from an
            # exception handler. Its exact text remains queued for readback only.
            if not action_required and state.get('phase')!='PLANNING':return False
            raise
        if action_required:
            state['reply_boundary']=secrets.token_hex(12)
            self.flows[chat].save()
        if isinstance(receipt,dict):
            state['delivery_verified_ids']=receipt.get('verified_message_ids',[])
            state['delivery_parts_count']=len(receipt.get('parts',[text]));self.flows[chat].save()
        return True

    def reconcile_deliveries(self):
        """Read back uncertain parts; send only subsequent or proven unsent parts."""
        for chat,flow in self.flows.items():
            state=flow.state;pending=state.get('pending_delivery')
            if not pending or pending['phase']!=state.get('phase','IDLE'):continue
            self.open(chat)
            if pending.get('send_attempted',True):
                expected=pending['parts'][pending['next_part']] if pending.get('parts') else pending['text']
                matched=matching_outgoing_ids(self.wa,whatsapp_safe_compose_text(expected),
                                             expand=True,exclude_ids=pending['exclude_ids'])
                if not matched:continue
                if pending.get('parts'):
                    pending.setdefault('part_receipts',[]).append(sorted(matched));pending['next_part']+=1
                    pending.update(send_attempted=False,exclude_ids=[]);flow.save()
                    if not self.send(chat,pending['text'],delivery=pending,**({'requester':pending['requester']} if pending.get('requester') else {})):continue
                    matched=set(state.get('delivery_verified_ids',[]))
                state.pop('pending_delivery',None)
            else:
                # A proven pre-send failure may be retried. An uncertain click never is.
                state.pop('pending_delivery');flow.save()
                if not self.send(chat,pending['text'],delivery=pending if pending.get('parts') else None,**({'requester':pending['requester']} if pending.get('requester') else {})):continue
                matched=set(state.get('delivery_verified_ids',[]))
            if pending['action_required']:state['reply_boundary']=secrets.token_hex(12)
            state['delivery_verified_ids']=sorted(matched)
            flow.save()
            if pending['is_approval']:flow.mark_sent()
            elif state.get('phase') in {'APPROVAL','SCOPE'} and not pending['is_scope'] and not pending.get('requester'):
                self.render_pending(chat)
            elif state.get('phase')=='PLANNING' and chat not in self.jobs and not pending.get('requester'):
                if state.get('exact_resume_operations'):
                    self.finish_selected_resume(chat)
                    continue
                self.browser.forms.clear()
                for cid in self.course_ids:self.browser.overview(cid)
                self.submit(chat,self.context(flow))

    def save_queue(self):
        tmp=self.queue_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.queue,ensure_ascii=False),encoding='utf-8')
        tmp.replace(self.queue_path)

    def queue_block_recovery(self,chat,reason):
        import auto_block_recovery as recovery
        flow=self.flows[chat]
        job=recovery.enqueue(self.block_recovery_root,flow.state,reason)
        flow.state['auto_recovery_job']=job.name
        flow.state['auto_recovery_status']='analyzing'
        flow.state['auto_recovery_last_notice']=time.time()
        flow.save()
        return job

    def service_block_recovery(self):
        if not self.auto_block_recovery:return
        import auto_block_recovery as recovery
        for chat,flow in self.flows.items():
            name=flow.state.get('auto_recovery_job','')
            if not re.fullmatch(r'[a-f0-9]{12}-[a-f0-9]{12}',name):continue
            job=self.block_recovery_root/name
            if (job/'handled.json').exists():continue
            if not (job/'result.json').exists():
                progress=recovery.read(job/'progress.json') if (job/'progress.json').exists() else {}
                flow.state['auto_recovery_status']=progress.get('stage','queued')
                if time.time()-flow.state.get('auto_recovery_last_notice',time.time())>=180:
                    flow.state['auto_recovery_last_notice']=time.time();flow.save()
                    self.send(chat,'Status: Waiting\nChange: Automatic recovery is still working: '+
                        {'diagnose':'analyzing the failed step','repair':'preparing a workflow correction',
                         'regression':'running regression checks','review_patch':'reviewing the tested correction'}.get(progress.get('stage'),'waiting for diagnosis')+'. No course save is being replayed.')
                continue
            request=recovery.read(job/'request.json');result=recovery.read(job/'result.json')
            if request['run']!=flow.state.get('run'):
                recovery.write(job/'handled.json',{'status':'superseded'});continue
            status=result['status'];diagnosis=result.get('diagnosis',{})
            if status in {'ready','retry'}:
                # No repair activates or restarts while anyone has an approval,
                # active planner, possible save, pending delivery or queued input.
                active={'PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'}
                unsafe=(self.jobs or self.queue or any(f.state.get('phase') in active or f.state.get('pending_delivery') for f in self.flows.values())
                        or flow.state.get('results') or 'write_started' in flow.state)
                if unsafe:
                    flow.state['auto_recovery_status']='ready_waiting_for_safe_boundary';flow.save();continue
                try:
                    if status=='ready':
                        recovery.activate(job)
                        self.restart_recovery={'run':flow.state['run'],'unwritten':bool(flow.state.get('selected'))}
                        flow.state['auto_recovery_status']='installed';flow.save()
                        recovery.write(job/'handled.json',{'status':'installed','at':time.time()})
                        self.stopped=True
                        try:self.send(chat,'Status: Waiting\nChange: The workflow repair passed regression checks and was installed. Restarting this task with fresh evidence and a new approval.')
                        except Exception as notice_error:self.record_failure(chat,notice_error,'repair_restart_notice')
                        return
                    recovery.write(job/'handled.json',{'status':'retrying','at':time.time()})
                    self.retry_failed_run(flow.state['run'],unwritten=bool(flow.state.get('selected')))
                    flow.state['auto_recovery_status']='retrying';flow.save()
                    self.send(chat,'Status: Waiting\nChange: Automatic diagnosis completed. Rechecking the task using current evidence; completed work is retained.')
                except Exception as exc:
                    if self.restart_recovery:raise
                    recovery.write(job/'handled.json',{'status':'activation_rejected','reason':user_reason(exc)})
                    flow.state['auto_recovery_status']='technical_review';flow.save()
                    self.send(chat,'Status: Waiting\nReason: Automatic repair could not be activated: '+user_reason(exc)+'\nChange: No course write was replayed.')
            elif status=='question':
                question=diagnosis.get('question','').strip()
                if not question or not diagnosis.get('evidence'):
                    result={'status':'technical_review','error':'Diagnosis did not provide an evidence-backed question.'}
                    recovery.write(job/'result.json',result);continue
                if not flow.state.get('results') and 'write_started' not in flow.state and not flow.state.get('selected'):
                    flow.state.update(phase='QUESTION',resume_phase='PLANNING',pending_question=question)
                flow.state['auto_recovery_status']='needs_user_clarification';flow.save()
                self.send(chat,'Status: Waiting\nTask: '+flow.state.get('task','')+'\n'+question)
                recovery.write(job/'handled.json',{'status':'question_sent','at':time.time()})
            else:
                flow.state['auto_recovery_status']=status;flow.save()
                reason=('Automatic recovery stopped and requires technical repair.' if status=='technical_review'
                        else diagnosis.get('summary') or 'Automatic recovery could not complete.')
                self.send(chat,'Status: Failed\nReason: '+reason+'\nChange: The diagnostic report is retained. No approval or course write was replayed.')
                recovery.write(job/'handled.json',{'status':status,'at':time.time()})

    def record_failure(self,chat,exc,lane):
        """Persist the actual failing stage without raw browser/credential data."""
        state=self.flows[chat].state
        action=getattr(self.browser,'last_action',{})
        self.observer.failure(chat,exc,lane,action if isinstance(action,dict) else {})
        event={'run':state.get('run'),'lane':lane,'phase':state.get('phase'),
               'error_type':type(exc).__name__,'action':action if isinstance(action,dict) else {},
               'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                         for f in traceback.extract_tb(exc.__traceback__) if str(ROOT) in f.filename]}
        path=self.root/('page-failures-'+state.get('run','startup')+'.json')
        events=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        events.append(event)
        path.write_text(json.dumps(events[-20:],ensure_ascii=False,indent=2),encoding='utf-8')

    def recover_mention(self,path):
        """Operator-requested recovery of one exact missed task, never an approval."""
        candidate=json.loads(Path(path).read_text(encoding='utf-8'))
        chat=candidate['chat']; expected=candidate['record']
        if chat not in self.mention_only_chats or digest(expected)!=candidate['record_hash']:
            raise ValueError('Missed-message recovery provenance does not match.')
        if any(f.state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'} for f in self.flows.values()):
            raise ValueError('Finish the active task before recovering a missed request.')
        if any(expected['id'] in f.state.get('message_ids',[]) for f in self.flows.values()):
            raise ValueError('This message already belongs to a task; do not replay it.')
        boundary=datetime.fromisoformat(candidate['boundary'])
        if not self.account_name:self.refresh_account_identity(force=True)
        self.open(chat)
        matches=[]
        source_id=expected.get('edited_message_id',expected['id'])
        for raw in intake_records(self.wa,self.account_name):
            if record_message_id(raw)!=source_id or raw.get('self_mentioned') is not True:continue
            record=request_record(raw,boundary)
            if not record:continue
            record.update(text=raw['addressed_text'],self_mentioned=True,
                          requester_name=raw.get('sender_display_name') or record['sender'])
            if expected.get('edited_message_id'):
                record.update(id=source_id+':edit:'+self.message_revision(raw)[:16],edited_message_id=source_id)
            if digest(record)==candidate['record_hash']:
                matches.append(record)
                matched_revision=self.message_revision(raw)
                matched_body=self.message_body_revision(raw)
        if len(matches)!=1 or re.fullmatch(r'(?:OK|NO)\s+\d{6}',matches[0]['text'].strip(),re.I):
            raise ValueError('The exact missed incoming task could not be verified; no history replay.')
        # Claim the operator-selected file once before queueing. Normal startup never
        # reads this file; an interrupted recovery requires explicit fresh inspection.
        Path(path).replace(Path(path).with_name('recovered-mention-'+str(time.time_ns())+'.json'))
        self.seen[chat].add(source_id)
        self.message_revisions[chat][source_id]=matched_revision
        self.message_bodies[chat][source_id]=matched_body
        getattr(self,'identity_rebaseline',{}).get(chat,set()).discard(source_id)
        self.queue.append({'chat':chat,**matches[0]});self.save_queue()

    def retry_failed_run(self,run_id,*,undelivered=False,unwritten=False,waiting=False):
        """Explicit operator fresh planning; no approval or execution is reused."""
        matches=[(chat,f) for chat,f in self.flows.items() if f.state.get('run')==run_id]
        if len(matches)!=1 or self.jobs or self.queue:
            raise ValueError('Retry requires one exact failed run and idle intake.')
        if any(f.state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'} for f in self.flows.values()):
            raise ValueError('Finish the active task before retrying a failed request.')
        chat,flow=matches[0];state=flow.state
        offered=any(k in state for k in ('selected','manifest_hash')) or state.get('approval_sent') is True
        safe_undelivered=(undelivered and state.get('approval_sent') is False and
                          bool(state.get('selected')) and digest(state['selected'])==state.get('manifest_hash'))
        safe_unwritten=(unwritten and bool(state.get('selected')) and
                        digest(state['selected'])==state.get('manifest_hash'))
        allowed_phase=state.get('phase') in {'FAILED','INTERRUPTED'} if not waiting else (
            state.get('phase')=='INTERRUPTED' and state.get('resume_phase')=='PLANNING' and not offered)
        if (not allowed_phase or state.get('mention_authorized') is not True or
                state.get('results') or 'write_started' in state or
                (offered and not (safe_undelivered or safe_unwritten)) or
                (undelivered and not safe_undelivered) or (unwritten and not safe_unwritten) or
                not state.get('task') or not state.get('sender') or not state.get('message_ids')):
            raise ValueError('Fresh planning requires a verified mentioned failure with no possible or completed write.')
        archive=self.root/('retried-'+digest(run_id)[:16]+'-'+str(time.time_ns())+'.json')
        with archive.open('x',encoding='utf-8') as handle:
            json.dump(state,handle,ensure_ascii=False,indent=2)
        # An explicit new attempt supersedes the old failed status notification.
        # Its text remains archived; never let it hold the recovered task queue.
        state.pop('pending_delivery',None)
        state.pop('reply_boundary',None)
        flow.save()
        record={'chat':chat,'id':state['message_ids'][0],'sender':state['sender'],'text':state['task'],
                **{k:copy.deepcopy(state[k]) for k in GOAL_FIELDS if k in state},
                'task_id':task_code(state),
                'self_mentioned':True,'requester_name':state.get('requester_name',state['sender']),
                'retry_of':run_id,'conversation':state.get('conversation',[]),
                'verified_previous_results':state.get('verified_previous_results',[]),
                'completed_operation_memory':state.get('completed_operation_memory',[]),
                'task_memory_context':state.get('task_memory_context',{}),
                'readback_repair':state.get('readback_repair',{}),
                'referenced_task_results':state.get('referenced_task_results',[]),
                'recovery_feedback':state.get('recovery_feedback',''),
                'original_date_interval_evidence':state.get('original_date_interval_evidence',[]),
                'previously_selected_scope':[{k:op[k] for k in ('target','summary','scope')}
                    for op in (state.get('selected') or state.get('previously_selected_scope',[]))]}
        self.queue.append(record);self.save_queue()

    def recheck_task(self,code,instruction):
        """Explicit operator correction of a settled result, with a new approval."""
        matches=[(c,f) for c,f in self.flows.items() if f.state.get('task_id')==code]
        if len(matches)!=1 or self.jobs or self.queue or not instruction.strip():
            raise ValueError('Recheck requires one exact settled task, correction instructions and idle intake.')
        if any(f.state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'} for f in self.flows.values()):
            raise ValueError('Finish the active task before rechecking a result.')
        chat,flow=matches[0];state=flow.state
        if state.get('phase')!='DONE' or state.get('mention_authorized') is not True:
            raise ValueError('Only a completed authenticated task can be explicitly rechecked.')
        memory=continuation(state)
        memory['conversation'].append(instruction.strip())
        memory.update(schedule_required=True,goal_followups=0,schedule_contract={})
        self.queue.append({'chat':chat,'id':state['message_ids'][0],'sender':state['sender'],
            'requester_name':state.get('requester_name',state['sender']),'text':state['task'],
            'self_mentioned':True,**memory,'task_memory_context':copy.deepcopy(memory),'retry_of':state['run'],
            'recovery_feedback':'The owner reported an incorrect completed result and requested correction. Historical receipts prove only that those writes happened, not that their destinations matched the syllabus. Re-read authoritative source rows and current inventory; correct wrong placements through a new explicit approval. Apply the latest correction scope in the conversation.'})
        self.save_queue()

    def renew_approval(self,run_id):
        """Operator-requested fresh inspection of one exact unwritten selected scope."""
        matches=[(c,f) for c,f in self.flows.items() if f.state.get('run')==run_id]
        if len(matches)!=1 or self.jobs:raise ValueError('Approval renewal requires one exact inactive planning run.')
        chat,flow=matches[0];old=copy.deepcopy(flow.state)
        if (old.get('phase') not in {'INTERRUPTED','APPROVAL','EXPIRED','FAILED'} or not old.get('selected') or
                old.get('results') or old.get('write_started') or old.get('mention_authorized') is not True or
                digest(old['selected'])!=old.get('manifest_hash')):
            raise ValueError('Only an intact selected scope with no attempted Moodle write can receive a renewed approval.')
        if any(f is not flow and f.state.get('phase') in {'PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'} for f in self.flows.values()):
            raise ValueError('Finish the other active task before renewing this approval.')
        if old.get('schedule_required') or old.get('exploration_evidence') or old.get('exploration_required') or any(o['capability']=='move_module' for o in old['selected']):
            memory=continuation(old)
            flow.state['phase']='INTERRUPTED';flow.save()
            self.start(chat,{'id':old['message_ids'][0],'sender':old['sender'],'text':old['task'],
                'requester_name':old.get('requester_name',old['sender']),'self_mentioned':True,
                **memory,'task_memory_context':memory,
                'recovery_feedback':'Re-read authoritative sources and review placement independently before renewing this plan. No old approval carries over.'})
            return
        resolved=self.refresh_selected_operations(old['selected'],old['task']+' '+json.dumps(old.get('conversation',[])))
        if self.stopped:return
        (self.root/('superseded-approval-'+run_id+'.json')).write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
        flow.state['phase']='INTERRUPTED';flow.save()
        flow.begin(old['task'],old['sender'],old['message_ids'][0],task_id=task_code(old))
        while flow.state['code']==old.get('code'):
            flow.state['code']=f'{secrets.randbelow(1000000):06d}'
        for key in ('mention_authorized','requester_name','conversation','verified_previous_results','original_date_interval_evidence','completed_operation_memory','task_memory_context','readback_repair','previously_selected_scope','referenced_task_results','recovery_feedback')+GOAL_FIELDS:
            if key in old:flow.state[key]=copy.deepcopy(old[key])
        if 'previously_selected_scope' not in old and old.get('task_memory_context',{}).get('previously_selected_scope'):
            flow.state['previously_selected_scope']=copy.deepcopy(old['task_memory_context']['previously_selected_scope'])
        flow.state['renewed_from']=run_id;flow.save()
        flow.offer({'impact_review':{'inspected_targets':list(self.browser.forms),'findings':['The previously selected scope was checked against fresh Moodle evidence.'],'unresolved_dependencies':[]}},resolved)
        if flow.state['phase']=='SCOPE':flow.select([o['id'] for o in resolved])
        try:self.render_pending(chat)
        except WhatsAppSendUnconfirmedError:
            if not flow.state.get('pending_delivery'):raise

    def refresh_selected_operations(self,operations,context):
        self.browser.forms.clear()
        for cid in dict.fromkeys(o['course_id'] for o in operations):self.browser.overview(cid)
        targets=list(dict.fromkeys(o['target'] for o in operations))
        chat=next((c for c,f in self.flows.items() if f.state.get('phase') in {'PLANNING','INTERRUPTED','APPROVAL'}),None)
        for index,key in enumerate(targets):
            previous=next(o for o in operations if o['target']==key)
            if previous.get('grade_identity') and key not in self.browser.targets:self.browser.restore_grade(previous)
            if chat:self.observer.step(chat,'inspecting_forms',current=index,total=len(targets),target=key)
            with self.health.work('refreshing_approval'):self.browser.inspect(key)
            if chat:self.observer.step(chat,'inspecting_forms',current=index+1,total=len(targets),target=key)
            if (index+1)%5==0:
                self.poll_safely()
                if self.stopped:return []
        resolved=[]
        for previous in operations:
            op={k:copy.deepcopy(previous[k]) for k in ('id','capability','target','summary','scope','reason','depends_on','params') if k in previous}
            op['fields']=[{'key':p['key'],'value':p['value']} for p in previous.get('fields',[])]
            if op['capability']=='update_moodle_form':item=self.browser.resolve(op)
            elif op['capability']=='move_module':item=self.structure.resolve(op)
            elif op['capability']=='set_section_visibility':item=self.section_visibility.resolve(op)
            elif op['capability'] in {'upload_file_resource','replace_file_resource'}:
                evidence=[self.library.inspect(previous['source_snapshot'],op['params']['file_id'])]
                item=self.resources.resolve(op,previous['source_snapshot'],evidence)
            else:item=self.groups.resolve(op,context)
            validate_dates(item.get('date_fields',[]),item.get('fields',[]),item['title'])
            resolved.append(item)
        return resolved

    def resume_selected(self,chat,record,previous,memory):
        if previous.get('exploration_evidence') or previous.get('exploration_required'):
            return self.start(chat,{**record,'text':previous['task'],'task_id':task_code(previous),
                'self_mentioned':True,**memory,'task_memory_context':memory,
                'recovery_feedback':'Reacquire and reanalyse the original exploration sources before proposing remaining changes. Historical analysis is not fresh evidence.'})
        """Pure continuation re-reads exact selected operations without scope replanning."""
        if previous.get('selected') and digest(previous['selected'])!=previous.get('manifest_hash'):
            raise ValueError('The saved selected scope is inconsistent; no continuation was started.')
        operations=copy.deepcopy(memory['previously_selected_scope'])
        remaining_ids={o['id'] for o in operations}
        for op in operations:op['depends_on']=[i for i in op.get('depends_on',[]) if i in remaining_ids]
        flow=self.flows[chat]
        if flow.state.get('run'):
            (self.root/('archived-run-'+flow.state['run']+'.json')).write_text(json.dumps(flow.state,ensure_ascii=False,indent=2),encoding='utf-8')
        flow.begin(previous['task'],record['sender'],record['id'],task_id=task_code(previous))
        flow.state.update(mention_authorized=True,requester_name=record.get('requester_name') or record['sender'],
                          retry_of=previous['run'],task_memory_context=memory,exact_resume_operations=operations)
        self.trace.note(flow.state['run'],task_code(flow.state),'workflow','resume_selected',{'request':record,'source_run':previous['run'],'memory':memory,'operations':operations})
        for key in ('conversation','verified_previous_results','completed_operation_memory','previously_selected_scope','original_date_interval_evidence','readback_repair')+GOAL_FIELDS:
            if key in memory:flow.state[key]=copy.deepcopy(memory[key])
        flow.save()
        self.send(chat,'Status: Waiting\nChange: Restoring '+str(len(operations))+' previously selected operations. Reading current Moodle settings; completed work is retained and a new approval is required.')
        self.finish_selected_resume(chat)

    def finish_selected_resume(self,chat):
        flow=self.flows[chat];state=flow.state
        if state.get('phase')!='PLANNING':raise ValueError('Exact continuation has no planning task.')
        operations=state['exact_resume_operations']
        self.file_evidence=[];self.planning_validation={}
        self.file_snapshot=self.library.snapshot() if self.library else None
        if state.get('exploration_required') or state.get('exploration_evidence'):
            for cid in self.course_ids:self.browser.overview(cid)
            self.submit(chat,self.context(flow,feedback='Reacquire the exploration_sources before proposing the remaining selected scope. Previous calculated values are not fresh source evidence.'))
            return
        resolved=self.refresh_selected_operations(operations,state['task']+' '+json.dumps(state.get('conversation',[])))
        self.validate_repair_plan(state,resolved)
        if self.stopped:flow.state['phase']='INTERRUPTED';flow.save();return
        if not any(o['scope']=='requested' for o in resolved):resolved[0]['scope']='requested'
        flow.offer({'impact_review':{'inspected_targets':list(self.browser.forms),'findings':['Restored the exact remaining selected scope using fresh evidence.'],'unresolved_dependencies':[]}},resolved)
        if flow.state['phase']=='SCOPE':flow.select([o['id'] for o in resolved])
        self.render_pending(chat)

    @staticmethod
    def message_body_revision(raw):
        return digest({'text':re.sub(r'\s+',' ',str(raw.get('message_text') or '')).strip(),
                       'direction':raw.get('direction'),'sender':raw.get('sender_id') or raw.get('sender_display_name')})

    @staticmethod
    def message_revision(raw):
        text=raw.get('addressed_text') if raw.get('self_mentioned') else raw.get('message_text')
        return digest({'text':re.sub(r'\s+',' ',str(text or '')).strip(),
                       'mentioned':raw.get('self_mentioned') is True,'direction':raw.get('direction'),
                       'sender':raw.get('sender_id') or raw.get('sender_display_name')})

    def refresh_account_identity(self,*,force=False):
        """Retry transient profile reads; never derive identity from message text."""
        if self.account_name:return False
        now=time.monotonic()
        if not force and now<getattr(self,'identity_retry_at',0):return False
        self.identity_retry_at=now+30
        name=discover_account_name(self.wa)
        if not isinstance(name,str) or not name.strip():return False
        self.account_name=name.strip()
        self.identity_rebaseline={chat:set(self.seen[chat]) for chat in self.chats}
        return True

    def poll(self):
        from playwright.sync_api import Error as BrowserError
        self.refresh_account_identity()
        failures=[];self.readable_chats=set()
        for chat in self.chats:
            self.health.attempt(chat)
            try:
                self.open(chat)
                records=intake_records(self.wa,self.account_name)
            except (BrowserError,WhatsAppIntakeUnavailable) as exc:
                self.health.failed(chat,exc);failures.append(exc);continue
            self.health.read(chat,len(records));self.readable_chats.add(chat)
            for raw in records:
                mid=record_message_id(raw)
                if not mid:continue
                revision=self.message_revision(raw)
                previous=self.message_revisions[chat].get(mid)
                edited=mid in self.seen[chat]
                if raw.get('direction')=='incoming' and (
                        record_time(raw,self.started) is None or
                        (not str(raw.get('message_text','')).strip() and not raw.get('documents'))):
                    # Newly mounted bubbles may not have their body/metadata yet.
                    # Observe again next poll instead of consuming an incomplete record.
                    continue
                pending=getattr(self,'identity_rebaseline',{}).get(chat,set())
                body_revision=self.message_body_revision(raw)
                if mid in pending:
                    pending.discard(mid)
                    prior_body=self.message_bodies[chat].get(mid)
                    self.message_bodies[chat][mid]=body_revision
                    if prior_body is None or prior_body==body_revision:
                        # Name discovery changes classification, not staff text.
                        # Retain authenticated queued replies; never replay history.
                        self.message_revisions[chat][mid]=revision
                        continue
                    # A separately observed body edit still retires its old queue.
                if edited and (previous is None or previous==revision):continue
                state=self.flows[chat].state
                bound=(state.get('mention_authorized') and state.get('sender')==(raw.get('sender_id') or raw.get('sender_display_name')) and
                       state.get('phase') in {'QUESTION','SCOPE','APPROVAL'} and state.get('reply_boundary'))
                if (chat in self.mention_only_chats and raw.get('direction')=='incoming' and
                        not edited and not self.account_name and raw.get('self_mentioned') is not True and not bound):
                    # The phone ID can still recognize numeric mentions. Named
                    # addressing remains undecided until the profile is read.
                    # Keep the original timestamp boundary; no edited shortcut.
                    continue
                self.seen[chat].add(mid)
                self.message_revisions[chat][mid]=revision
                self.message_bodies[chat][mid]=body_revision
                if edited:
                    # Retire an unconsumed older body even if the edit removes
                    # its addressing or turns it into an ineligible approval.
                    self.queue=[q for q in self.queue if not (q['chat']==chat and
                        (q['id']==mid or q.get('edited_message_id')==mid))]
                    self.save_queue()
                if raw.get('self_mentioned') is True:
                    raw={**raw,'message_text':raw.get('addressed_text','')}
                # Only a change observed after baseline may use its observation
                # time; unchanged historical messages remain excluded.
                record=request_record(raw,None if edited else self.started)
                if record:
                    if edited:
                        if re.fullmatch(r'(?:OK|NO)\s+\d{6}',record['text'].strip(),re.I):continue
                        record.update(id=mid+':edit:'+revision[:16],edited_message_id=mid)
                    record['self_mentioned']=raw.get('self_mentioned') is True
                    record['requester_name']=raw.get('sender_display_name') or record['sender']
                    state=self.flows[chat].state
                    if chat in self.mention_only_chats:
                        bound=(state.get('mention_authorized') and state.get('sender')==record['sender'] and
                               state.get('phase') in {'QUESTION','SCOPE','APPROVAL'} and state.get('reply_boundary'))
                        protocol=re.fullmatch(r'(?:OK|NO)\s+\d{6}',record['text'].strip(),re.I)
                        if protocol:
                            # Approval syntax is never a new task, even when mentioned.
                            if not bound or state.get('phase')!='APPROVAL' or not state.get('approval_sent'):continue
                            if record['text'].split()[-1]!=state.get('code'):continue
                        elif not record['self_mentioned'] and not bound:continue
                        if bound:
                            record.update(reply_to_run=state['run'],reply_boundary=state['reply_boundary'])
                    if record['text'].casefold() in {'stop agent','shutdown agent','停止','退出','停止 agent'}:
                        self.stopped=True;return
                    self.queue.append({'chat':chat,**record})
                    self.save_queue()
        if failures:raise failures[-1]

    def poll_safely(self):
        from playwright.sync_api import Error as BrowserError
        try:
            with self.health.work('reading_whatsapp'):self.poll()
            self.health.cycle(True)
            self.transport_error=''
            return True
        except (BrowserError,WhatsAppIntakeUnavailable):
            self.health.cycle(False)
            # A tab timeout must not kill intake or replay any Moodle operation.
            self.transport_error='WhatsApp is temporarily unavailable; retrying message intake. No save is retried.'
            if self.recover_transport:
                action=self.health.recovery_due()
                if action:
                    try:
                        with self.health.work('recovering_whatsapp'):self.recover_transport(action)
                        self.health.recovery_result(True)
                    except (BrowserError,RuntimeError,ValueError,OSError):self.health.recovery_result(False)
            return False
        finally:
            if getattr(self,'panel',None):self.panel.drain(self)

    def context(self,flow,reply='',feedback=''):
        # Restarted questions enter planning without start(). Restore current
        # source inventory here as well; historical citations grant no evidence.
        if self.library and self.file_snapshot is None:
            self.file_snapshot=self.library.snapshot()
        library_context=None
        if self.file_snapshot:
            library_context={'status':'planner_access_not_authorized','file_count':len(self.file_snapshot['files'])}
            if self.library_planner_access:
                # Absolute paths, sizes and hashes stay local; the model needs IDs and relative names.
                library_context={'status':'available','semester':self.file_snapshot['semester'],
                    'files':[{k:e[k] for k in ('id','relative_path','format','text_reader')}
                             for e in self.file_snapshot['files']]}
        unavailable=getattr(self.browser,'unavailable_targets',{})
        if not isinstance(unavailable,dict):unavailable={}
        return {'request':flow.state['task'],'reply':reply,'feedback':feedback,
                'exploration':{'enabled':self.explorer is not None,
                    'remaining_calls':max(0,self.exploration_max_calls-flow.state.get('exploration_calls',0)),
                    'sources':self.exploration_broker.catalog() if self.exploration_broker and self.exploration_run==flow.state['run'] else [],
                    'evidence':flow.state.get('exploration_evidence',[])},
                'defer_uncertain_items':getattr(self,'defer_uncertain_items',False),
                'run_id':flow.state['run'],
                'task_id':task_code(flow.state),
                'task_memory':flow.state.get('task_memory_context',{}),
                'referenced_task_results':flow.state.get('referenced_task_results',[]),
                'recent_task_results':self.task_memory.recent_results(flow.chat,flow.state['sender'],
                    limit=self.recent_result_limit,exclude_task=task_code(flow.state)),
                'completed_operation_memory':flow.state.get('completed_operation_memory',[]),
                'current_time':datetime.now().astimezone().isoformat(),
                'courses':list(self.browser.overviews.values()),'forms':list(self.browser.forms.values()),
                'unavailable_targets':list(unavailable.values()),
                'file_library':library_context,
                'file_evidence':[{k:v for k,v in e.items() if k!='sha256'} for e in self.file_evidence]
                                 if self.library_planner_access else [],
                'pending_scope':[{k:op[k] for k in ('id','capability','target','summary','scope','reason','depends_on','fields','params') if k in op}
                    for op in flow.state.get('candidates',[])] if flow.state['phase']=='SCOPE' else [],
                'conversation':flow.state.get('conversation',[]),
                'verified_previous_results':flow.state.get('verified_previous_results',[]),
                'recovery_feedback':flow.state.get('recovery_feedback',''),
                'readback_repair':flow.state.get('readback_repair',{}),
                'date_interval_evidence':[{'target':form['target'],**interval_evidence(form['fields'])}
                    for form in self.browser.forms.values() if interval_evidence(form['fields'])['dates']],
                'original_date_interval_evidence':flow.state.get('original_date_interval_evidence',[]),
                'planning_validation':self.planning_validation,
                **{key:flow.state.get(key) for key in GOAL_FIELDS},
                'previously_selected_scope':flow.state.get('previously_selected_scope',[])}

    def submit(self,chat,context,rounds=0,repairs=0):
        context={**context,'planner_attempt_id':secrets.token_hex(6)}
        self.observer.step(chat,'planner_running',round=rounds+1,repair=repairs,forms=len(context.get('forms',[])),attempt=context['planner_attempt_id'])
        self.jobs[chat]={'future':self.pool.submit(self.planner,context),'context':context,
                         'rounds':rounds,'repairs':repairs}

    def start(self,chat,record):
        if chat in self.mention_only_chats and record.get('self_mentioned') is not True:return
        flow=self.flows[chat]
        if flow.state.get('phase') in {'FAILED','DONE','PARTIAL','CANCELLED','EXPIRED','INTERRUPTED'}:
            prior=flow.state.get('run','')
            if re.fullmatch(r'[a-f0-9]{12}',prior):
                (self.root/('archived-run-'+prior+'.json')).write_text(json.dumps(flow.state,ensure_ascii=False,indent=2),encoding='utf-8')
        flow.begin(record['text'],record['sender'],record['id'],task_id=record.get('task_id'))
        self.trace.note(flow.state['run'],task_code(flow.state),'intake','request',{'request':record,'parsed_command':task_command(record['text'])})
        flow.state['mention_authorized']=record.get('self_mentioned') is True
        flow.state['requester_name']=record.get('requester_name') or record['sender']
        if record.get('retry_of'):flow.state['retry_of']=record['retry_of']
        for key in ('conversation','previously_selected_scope','verified_previous_results','recovery_feedback','original_date_interval_evidence','completed_operation_memory','task_memory_context','readback_repair','referenced_task_results')+GOAL_FIELDS:
            if record.get(key):flow.state[key]=record[key]
        if not flow.state.get('goal_scope') and record.get('previously_selected_scope'):
            flow.state['goal_scope']=copy.deepcopy(record['previously_selected_scope'])
        flow.save()
        self.file_evidence=[]
        self.exploration_broker=None;self.exploration_run=None
        self.planning_validation={}
        self.file_snapshot=self.library.snapshot() if self.library else None
        self.send(chat,'Status: Waiting\nTask: '+record['text']+'\nChange: Reading current courses and related settings before proposing changes.')
        self.browser.forms.clear()
        self.observer.step(chat,'reading_course_inventory')
        for cid in self.course_ids: self.browser.overview(cid)
        if record.get('task_memory_context') and not flow.state.get('schedule_required'):
            for op in record.get('previously_selected_scope',[])+[r.get('operation',{}) for r in record.get('completed_operation_memory',[])]:
                if op.get('grade_identity') and op['target'] not in self.browser.targets:self.browser.restore_grade(op)
            targets=list(dict.fromkeys([o['target'] for o in record.get('previously_selected_scope',[]) if o.get('target')]+[
                row['operation']['target'] for row in record.get('completed_operation_memory',[]) if row.get('operation',{}).get('target')]))
            for index,target in enumerate(targets):
                self.observer.step(chat,'inspecting_forms',current=index,total=len(targets),target=target)
                if target in self.browser.targets:
                    with self.health.work('reading_task_memory_targets'):self.browser.inspect(target)
                if (index+1)%5==0:
                    self.poll_safely()
                    if self.stopped:
                        flow.state['phase']='INTERRUPTED';flow.save();return
        self.submit(chat,self.context(flow))

    def handle_task_command(self,chat,record,command):
        action,code,instruction=command
        reply=lambda text:self.send(chat,text,requester=record)
        if action=='status':
            health=self.health.snapshot();row=self.observer.snapshot()['tasks'].get(chat)
            current=self.flows[chat].state
            if current.get('sender')!=record['sender']:row=None
            reply('Listener: '+health['status']+'\n'+(task_status_text(row) if row else 'Use /tasks to see your own tasks.'))
            return
        if action=='help':
            reply('Task commands: /tasks; /task T-xxxxxxxxxxxx; /resume T-xxxxxxxxxxxx [additional instructions]. Mention this account when starting or resuming a task.')
            return
        if action=='list':
            rows=self.task_memory.visible(chat,record['sender'])
            reply('Saved tasks (up to 10 across the agent):\n'+('\n\n'.join(task_description(s) for s in rows) or 'No callable task codes.')+'\n\nResume: /resume TASK-ID [additional instructions]')
            return
        previous=self.task_memory.lookup(code,chat,record['sender'])
        if action=='show':
            row=self.observer.snapshot()['tasks'].get(chat)
            detail='\n\n'+task_status_text(row) if row and row.get('run')==previous['run'] else ''
            reply('Task memory:\n'+task_description(previous)+detail+'\n\nResume: /resume '+task_code(previous)+' [additional instructions]')
            return
        current=self.flows[chat]
        if (not self.jobs and current.state.get('phase') in {'QUESTION','SCOPE','APPROVAL'} and
            current.state.get('sender')==record['sender'] and current.state.get('mention_authorized') and
            not current.state.get('write_started') and not current.state.get('results') and
            not any(f is not current and f.state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'} for f in self.flows.values())):
            if task_code(previous)==task_code(current.state):
                if not instruction:
                    reply('This task is waiting for your reply. '+current.state.get('pending_question','Reply to its latest question or approval with the requested clarification.'))
                    return
                current.bound_reply(record['sender'],record['id'])
                current.state.setdefault('conversation',[]).append(instruction)
                old_code=current.state.get('code')
                current.state.update(phase='PLANNING',approval_sent=False,code=f'{secrets.randbelow(1000000):06d}')
                while current.state['code']==old_code:current.state['code']=f'{secrets.randbelow(1000000):06d}'
                for key in ('candidates','selected','manifest_hash','pending_question','resume_phase','reply_boundary','alignment_repairs'):
                    current.state.pop(key,None)
                current.save();self.browser.forms.clear()
                for cid in self.course_ids:self.browser.overview(cid)
                self.submit(chat,self.context(current,reply=instruction,feedback='The requester supplied new instructions for this same active task. Apply them to the remaining goal; do not repeat the previous question if the reply already resolves it. Re-inspect and obtain fresh approval for changes.'))
                return
            # A source task reference answers the current question; it does not
            # start a competing task or authorize the source task's remaining writes.
            summary=result_summary(previous)
            if summary is None or summary['needs_readback']:
                raise ValueError('The referenced task has no settled result yet; its save needs readback before reuse.')
            current.bound_reply(record['sender'],record['id'])
            refs=[r for r in current.state.get('referenced_task_results',[]) if r['task_id']!=summary['task_id']]
            current.state['referenced_task_results']=[*refs,summary][-10:]
            current.state.setdefault('conversation',[]).append(record['text'])
            current.state.update(phase='PLANNING',approval_sent=False,code=f'{secrets.randbelow(1000000):06d}')
            for key in ('candidates','selected','manifest_hash','pending_question'):current.state.pop(key,None)
            current.save();self.browser.forms.clear()
            for cid in self.course_ids:self.browser.overview(cid)
            self.submit(chat,self.context(current,reply=record['text'],feedback='Use the explicitly referenced historical result to clarify the current request. Re-inspect current Moodle and relevant source evidence; do not execute or resume an unrelated task.'))
            return
        if self.jobs or any(f.state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'} for f in self.flows.values()):
            raise ValueError('A task is active. Finish its current question/approval or save readback before resuming a saved task.')
        if action=='reference':
            summary=result_summary(previous)
            if summary is None or summary['needs_readback']:raise ValueError('The referenced result is still unsettled; inspect its task status first.')
            self.start(chat,{**record,'referenced_task_results':[summary]})
            return
        memory=continuation(previous)
        remaining=memory['previously_selected_scope']
        if not instruction and not previous.get('exploration_evidence') and not memory.get('exploration_required') and not memory.get('schedule_required') and remaining and all(o.get('capability')=='update_moodle_form' and o.get('course_id') and o.get('id') for o in remaining):
            return self.resume_selected(chat,record,previous,memory)
        next_record={**record,'text':previous['task'],'task_id':task_code(previous),
            'retry_of':previous['run'],'task_memory_context':memory,
            **{k:memory[k] for k in ('conversation','verified_previous_results','completed_operation_memory','previously_selected_scope','original_date_interval_evidence','readback_repair','referenced_task_results')+GOAL_FIELDS},
            'recovery_feedback':'Resume this saved task from its historical results. Read current Moodle evidence, preserve completed work, and propose only the remaining requested changes with fresh approval.'}
        if instruction:next_record['conversation']=[*next_record['conversation'],instruction]
        self.start(chat,next_record)

    def render_pending(self,chat):
        flow=self.flows[chat]
        if flow.state['phase']=='SCOPE':
            self.send(chat,scope_message(flow.state))
        elif flow.state['phase']=='APPROVAL':
            try:
                for op in flow.state['selected']:
                    validate_dates(op.get('date_fields',[]),op.get('fields',[]),op['title'])
            except MoodleDateConflict:
                # Not yet delivered/approved: return the invalid proposal to bounded planning.
                if flow.state.get('approval_sent'):raise
                flow.state['phase']='SCOPE';flow.save()
                raise
            self.send(chat,approval_message(flow.state))
            flow.mark_sent()

    def accept_decision(self,chat,result,job):
        flow=self.flows[chat]
        kind=result.get('decision')
        self.observer.event(chat,'planner_decision',task_id=task_code(flow.state),run=flow.state['run'],decision=str(kind))
        if kind=='explore':
            from chrome_exploration import begin
            begin(self,chat,result,job)
        elif kind=='inspect':
            keys=result.get('inspect_targets',[])
            if not keys or job['rounds']>=8:
                raise ValueError('Inspection budget ended without a complete plan; identify the unresolved dependency.')
            missing=[k for k in dict.fromkeys(keys) if k not in self.browser.forms]
            feedback=job.get('context',{}).get('feedback','')
            if not missing:
                self.observer.step(chat,'evidence_cache_hit',kind='forms',count=len(set(keys)))
                feedback+=' Requested forms are already in forms. Their cached inspection is returned successfully. Use these fields to continue the plan; request only genuinely missing evidence.'
            for index,key in enumerate(missing):
                self.observer.step(chat,'inspecting_forms',current=index,total=len(set(keys)),target=key)
                try:
                    with self.health.work('reading_moodle'):self.browser.inspect(key)
                except Exception as exc:
                    from chrome_moodle import MoodleReadUnavailable
                    if self.explorer and isinstance(exc,MoodleReadUnavailable):
                        from chrome_exploration import begin
                        begin(self,chat,{'inspect_targets':[key],'message':'The standard Moodle reader failed. Recover relevant page evidence for the original task.'},job)
                        return
                    raise
                self.observer.step(chat,'inspecting_forms',current=index+1,total=len(set(keys)),target=key)
                if (index+1)%5==0:
                    self.poll_safely()
                    if self.stopped:return
            self.submit(chat,self.context(flow,feedback=feedback),job['rounds']+1,job['repairs'])
        elif kind=='inspect_files':
            if not self.library_planner_access:
                raise ValueError('The local file index is ready, but the owner has not authorized providing library information to the planning service.')
            requests=result.get('file_reads',[])
            batches=(len(requests)+5)//6
            if not self.library or not self.file_snapshot:
                raise ValueError('The current file-library snapshot is unavailable; initialize source inventory before inspection.')
            if not requests:
                raise ValueError('File inspection requires at least one exact file and offset.')
            if job['rounds']+batches>8:
                raise ValueError('File inspection budget ended; use the supplied evidence to identify the remaining dependency.')
            existing={(r['file_id'],r['offset']) for r in self.file_evidence}
            feedback=job.get('context',{}).get('feedback','')
            if all((r['file_id'],r['offset']) in existing for r in requests):
                # Reads are idempotent. A repeated source page is not a task
                # failure, even when an unrelated plan repair was already used.
                self.library.validate_reads(self.file_snapshot,self.file_evidence)
                self.observer.step(chat,'evidence_cache_hit',kind='files',count=len(requests))
                feedback+=' Requested file pages are already in file_evidence and are returned successfully from validated cache. Use their text and table_rows to continue the correction. next_offset=null means the source is complete; otherwise request only that exact next_offset if more text is needed.'
            for index,request in enumerate(requests):
                if index and index%6==0:
                    self.poll_safely()
                    if self.stopped:return
                pair=(request['file_id'],request['offset'])
                if pair in existing:continue
                if request['offset'] and not any(r['file_id']==request['file_id'] and
                        r['next_offset']==request['offset'] for r in self.file_evidence):
                    raise ValueError('Read the first file page before its exact next_offset.')
                self.observer.step(chat,'reading_source_files',current=len(self.file_evidence),total=len(self.file_evidence)+len(requests))
                self.file_evidence.append(self.library.inspect(self.file_snapshot,**request))
                existing.add(pair)
            failed=list(dict.fromkeys(r['file_id'] for r in self.file_evidence
                if r['file_id'] in {request['file_id'] for request in requests} and r.get('status') in {'reader_unavailable','no_extractable_text'}))
            explored={sid for e in flow.state.get('exploration_evidence',[]) for sid in e.get('source_ids',[])}
            failed=[fid for fid in failed if fid not in explored]
            if self.explorer and failed:
                from chrome_exploration import begin
                begin(self,chat,{'file_reads':[{'file_id':fid,'offset':0} for fid in failed[:12]],
                    'message':'The standard file reader could not extract these sources. Inspect their actual bytes, use alternate parsers or code and recover evidence needed for the task.'},job)
                return
            self.submit(chat,self.context(flow,feedback=feedback),job['rounds']+batches,job['repairs'])
        elif kind=='plan':
            self.validate_exploration(flow)
            self.validate_repair_plan(flow.state,result.get('operations',[]))
            if any(o.get('capability')=='set_section_visibility' for o in result.get('operations',[])):
                if any(o.get('capability')!='set_section_visibility' for o in result['operations']):
                    raise ValueError('Approve section visibility prerequisites as a separate stage. Their changes alter file visibility options; remaining edits will be automatically re-planned after fresh readback.')
                if len({o['target'] for o in result['operations']})!=len(result['operations']):
                    raise ValueError('Use one visibility operation per exact section in this prerequisite stage.')
            if self.library:self.library.validate_reads(self.file_snapshot,self.file_evidence)
            review=result.get('impact_review',{})
            if not isinstance(review.get('findings'),list) or not review['findings']:
                raise ValueError('A plan must report its related-change review.')
            if not self.ensure_plan_evidence(chat,result,job):return
            if not self.ensure_alignment_review(chat,result,job):return
            ops=[]
            for op in result.get('operations',[]):
                if op['capability']=='update_moodle_form':
                    resolved=self.browser.resolve(op)
                elif op['capability']=='move_module':
                    resolved=self.structure.resolve(op)
                    contract=flow.state.get('schedule_contract',{})
                    row=next((r for r in contract.get('items',[]) if r['target']==op['target']),None)
                    if row:
                        source=next(e for e in self.file_evidence if e['file_id']==row['file_id'] and e.get('table_rows'))
                        cited=next(r for r in source['table_rows'] if r['row_id']==row['row_id'])
                        resolved['schedule_citation']={'file_id':row['file_id'],'row_id':row['row_id'],
                            'source':Path(source['relative_path']).name,
                            'placement_basis':row.get('placement_basis','syllabus'),
                            'week':row['expected_week'],'row_text':' | '.join(cited['cells']),
                            'sha256':source['sha256']}
                elif op['capability']=='set_section_visibility':
                    resolved=self.section_visibility.resolve(op)
                elif op['capability'] in {'upload_file_resource','replace_file_resource'}:
                    resolved=self.resources.resolve(op,self.file_snapshot,self.file_evidence)
                else:
                    resolved=self.groups.resolve(op,flow.state['task']+' '+json.dumps(flow.state.get('conversation',[])))
                ops.append(resolved)
            if not flow.state.get('goal_scope'):flow.state['goal_scope']=[operation_memory(op) for op in ops]
            flow.state['requires_goal_verification']=bool(flow.state.get('schedule_required') or review.get('unresolved_dependencies') or
                any(o['capability']=='set_section_visibility' for o in ops))
            flow.offer(result,ops)
            if review.get('unresolved_dependencies') and not getattr(self,'defer_uncertain_items',False):
                self.send(chat,'Related items still need attention:\n'+'\n'.join('- '+x for x in review['unresolved_dependencies']))
            self.render_pending(chat)
        elif kind=='select_scope':
            flow.select(result.get('selected_ids',[])); self.render_pending(chat)
        elif kind=='revise_scope':
            if not job['context'].get('reply') or flow.state.get('phase')!='SCOPE':
                raise ValueError('Changing the requested scope requires a current requester reply to the offered scope.')
            flow.select(result.get('selected_ids',[]),allow_narrowing=True)
            self.render_pending(chat)
        elif kind=='question':
            if getattr(self,'defer_uncertain_items',False) and flow.state['phase']=='PLANNING':
                if not flow.state.get('uncertainty_pass'):
                    flow.state['uncertainty_pass']=1;flow.save()
                    self.submit(chat,self.context(flow,feedback='Do not ask another clarification. Plan all independent grounded operations first, keeping uncertain items in unresolved_dependencies. If no confirmed operations remain, return defer with one named item and missing information per unresolved_dependencies entry. Unanswered question: '+result['message']),job['rounds'],job['repairs'])
                    return
                self.close_deferred(chat,result.get('impact_review',{}).get('unresolved_dependencies') or [result['message']])
                return
            flow.state['resume_phase']=flow.state['phase']
            flow.state['pending_question']=result['message']
            flow.state['phase']='QUESTION'; flow.save()
            self.send(chat,'Status: Waiting\nTask: '+flow.state['task']+'\n'+result['message'])
        elif kind=='defer':
            if not getattr(self,'defer_uncertain_items',False) or flow.state['phase']!='PLANNING' or result.get('operations'):
                raise ValueError('Deferred closure requires planning with no remaining proposed operations.')
            self.close_deferred(chat,result.get('impact_review',{}).get('unresolved_dependencies',[]))
        elif kind in {'answer','stop'}:
            if kind=='stop' and job['repairs']==0:
                raise ValueError(result['message'])
            if kind=='answer' and getattr(self,'defer_uncertain_items',False) and result.get('impact_review',{}).get('unresolved_dependencies'):
                self.close_deferred(chat,result['impact_review']['unresolved_dependencies']);return
            if kind=='answer' and (flow.state.get('schedule_required') or result.get('alignment',{}).get('required')):
                if not self.ensure_alignment_review(chat,result,job):return
                issues=schedule_alignment.verify_current(flow.state['schedule_contract'],self.browser)
                if issues:
                    if getattr(self,'defer_uncertain_items',False):
                        self.close_deferred(chat,issues);return
                    raise ValueError('Whole-goal alignment is not complete: '+'; '.join(issues)[:1800])
                flow.state['goal_verified']=True
            elif kind=='answer' and flow.state.get('goal_followups') and flow.state.get('plan',{}).get('impact_review',{}).get('unresolved_dependencies'):
                raise ValueError('Resolve the tracked dependencies before claiming the whole goal is complete.')
            flow.state['phase']='DONE' if kind=='answer' else 'FAILED'
            if kind=='answer':flow.state['last_answer']=result['message']
            flow.save()
            self.send(chat,('Status: Done' if kind=='answer' else 'Status: Blocked')+'\nTask: '+flow.state['task']+'\n'+result['message'])
        else: raise ValueError('Planner returned an unknown decision.')

    def ensure_alignment_review(self,chat,result,job):
        flow=self.flows[chat];alignment=result.get('alignment',{})
        alignment,normalizations=schedule_alignment.normalize_bindings(alignment,self.file_evidence)
        alignment,inherited_citations=schedule_alignment.inherit_citation_sources(alignment,self.browser,self.file_evidence)
        alignment,operations,destinations=schedule_alignment.resolve_missing_destinations(alignment,result.get('operations',[]),self.browser,self.file_evidence)
        if 'operations' in result:result['operations']=operations
        alignment,column_normalizations=schedule_alignment.normalize_week_columns(alignment,self.browser,self.file_evidence)
        if result.get('alignment') is not None:result['alignment']=alignment
        if normalizations:
            self.trace.note(flow.state['run'],task_code(flow.state),'planning','source_bindings_normalized',normalizations)
        if inherited_citations:
            self.trace.note(flow.state['run'],task_code(flow.state),'planning','citation_sources_inherited',inherited_citations)
        if destinations:
            self.trace.note(flow.state['run'],task_code(flow.state),'planning','missing_destinations_resolved',destinations)
        if column_normalizations:
            self.trace.note(flow.state['run'],task_code(flow.state),'planning','week_columns_normalized',column_normalizations)
        if alignment.get('required'):flow.state['schedule_required']=True;flow.save()
        contract=schedule_alignment.validate(alignment,result.get('operations',[]),self.browser,self.file_evidence,
                                              required=bool(flow.state.get('schedule_required')))
        moves=[o for o in result.get('operations',[]) if o['capability']=='move_module']
        if not contract and not moves:return True
        if job.get('alignment_checked')==digest(result):
            if contract:flow.state['schedule_contract']=contract;flow.save()
            return True
        if contract:
            context=schedule_alignment.review_context(contract,self.browser,self.file_evidence,flow.state['task'],flow.state.get('conversation',[]))
        else:
            contract={'items':[{'target':k} for k in dict.fromkeys(o['target'] for o in moves)]}
            context={'review_kind':'direct_placement','request':flow.state['task'],'conversation':flow.state.get('conversation',[]),
                     'operations':moves,'courses':list(self.browser.overviews.values())}
        context.update(run_id=flow.state['run'],task_id=task_code(flow.state))
        self.submit(chat,context,job['rounds'],job['repairs'])
        self.jobs[chat].update(alignment_candidate=copy.deepcopy(result),review_contract=contract,origin_job=job)
        return False

    def ensure_plan_evidence(self,chat,result,job):
        """Fill exact, known read gaps; never accept an ungrounded write plan."""
        operations=result.get('operations',[])
        keys=list(dict.fromkeys(result.get('impact_review',{}).get('inspected_targets',[])+[
            op['target'] for op in operations if op.get('capability') in {'update_moodle_form','move_module','upload_file_resource','replace_file_resource'}]))
        unknown=[key for key in keys if key not in self.browser.targets]
        missing=[key for key in keys if key not in self.browser.forms and key not in unknown]
        invalid=[]
        for op in operations:
            if op.get('capability')!='update_moodle_form' or op['target'] not in self.browser.forms:continue
            observed={f['key'] for f in self.browser.forms[op['target']]['fields']}
            fields=[p['key'] for p in op.get('fields',[])]
            absent=list(dict.fromkeys(key for key in fields if key not in observed))
            duplicate=list(dict.fromkeys(key for index,key in enumerate(fields) if key in fields[:index]))
            if absent or duplicate:
                invalid.append({'target':op['target'],'unknown_keys':absent,'duplicate_keys':duplicate,
                                'observed_keys':sorted(observed)})
        self.planning_validation={'unknown_targets':unknown,'missing_form_targets':missing,'invalid_fields':invalid}
        if unknown or missing or invalid:
            # Identifiers only: no field contents, credentials or participant data.
            path=self.root/('plan-grounding-'+self.flows[chat].state['run']+'.json')
            history=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
            history.append(self.planning_validation)
            path.write_text(json.dumps(history[-12:],ensure_ascii=False,indent=2),encoding='utf-8')
        if unknown:raise ValueError('The plan references targets absent from the current course inventory; use the exact observed targets.')
        if missing:
            if job['rounds']>=8:raise ValueError('The inspection budget ended with unresolved form evidence; the plan remains unapproved.')
            for index,key in enumerate(missing):
                self.observer.step(chat,'inspecting_forms',current=index,total=len(missing),target=key)
                with self.health.work('reading_moodle'):self.browser.inspect(key)
                self.observer.step(chat,'inspecting_forms',current=index+1,total=len(missing),target=key)
                if (index+1)%5==0:
                    self.poll_safely()
                    if self.stopped:return False
            self.planning_validation['missing_form_targets']=[]
            self.planning_validation['automatically_inspected_targets']=missing
            context=self.context(self.flows[chat],reply=job.get('context',{}).get('reply',''),
                feedback='Missing known forms were just inspected. Rebuild the complete plan from this fresh evidence; correct any field references in planning_validation. No old proposed changes were accepted.')
            self.submit(chat,context,job['rounds']+1,job['repairs'])
            return False
        if invalid:raise ValueError('The plan contains invalid or repeated field references; use the exact keys in the observed forms.')
        return True

    def refresh_changed_source(self,chat,error):
        """One fresh evidence epoch before any offered/possible write, never approval."""
        flow=self.flows[chat];state=flow.state
        if (not isinstance(error,FileLibraryChanged) or not self.library or not self.library_planner_access or
                state.get('phase')!='PLANNING' or state.get('mention_authorized') is not True or
                state.get('source_refreshes',0)>=1 or state.get('results') or state.get('approval_sent') is True or
                any(k in state for k in ('candidates','selected','manifest_hash','write_started')) or
                state.get('pending_delivery') or self.jobs or self.queue or
                any(f is not flow and (f.state.get('phase') in {'PLANNING','QUESTION','SCOPE','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'}
                                      or f.state.get('pending_delivery')) for f in self.flows.values())):
            return False
        fresh=self.library.snapshot()
        entry=self.library.entry(fresh,error.file_id)
        if entry['relative_path']!=error.relative_path:
            raise ValueError('Changed source identity could not be retained; no substitution was made.')
        # All old source pages and review conclusions belong to the old epoch.
        # Merely taking a new snapshot does not count as reading its contents.
        self.file_snapshot=fresh;self.file_evidence=[];self.planning_validation={}
        if state.get('exploration_evidence'):
            state['exploration_required']=True
            state.pop('exploration_evidence',None)
            self.exploration_broker=None;self.exploration_run=None
        self.browser.forms.clear()
        state.update(source_refreshes=state.get('source_refreshes',0)+1,schedule_contract={})
        for key in ('alignment_repairs','pending_question','resume_phase','reply_boundary'):
            state.pop(key,None)
        change={'file_id':error.file_id,'relative_path':error.relative_path,
                'previous_sha256':error.expected_sha256,'observed_sha256':error.actual_sha256,
                'snapshot_sha256':entry['sha256']}
        state['source_version_change']=change;flow.save()
        self.trace.note(state['run'],task_code(state),'planning','source_version_refresh',change)
        for cid in self.course_ids:self.browser.overview(cid)
        context=self.context(flow,feedback='The same selected source changed during planning. The previous proposal, source pages and review are discarded. Read the changed source again and rebuild the complete plan from current evidence. Preserve the current task instructions and verified completed effects. Current-plan approval is still required for every new write.')
        self.submit(chat,context,0,0)
        return True

    def complete_jobs(self):
        for chat,job in list(self.jobs.items()):
            if self.readable_chats is not None and chat not in self.readable_chats:continue
            if not job['future'].done(): continue
            del self.jobs[chat]
            if job.get('exploration') and (self.flows[chat].state.get('run')!=job['run_id'] or self.flows[chat].state.get('phase')!='PLANNING'):
                self.observer.event(chat,'stale_exploration_discarded',run=job['run_id'])
                continue
            decision={}
            try:
                decision=job['future'].result()
                if job.get('exploration'):
                    from chrome_exploration import finish
                    finish(self,chat,decision,job)
                elif job.get('alignment_candidate'):
                    candidate=copy.deepcopy(job['alignment_candidate'])
                    if job['context'].get('review_kind')=='schedule_alignment':
                        # Semantic corrections get their own budget: a prior
                        # coordinate/schema correction must not consume it.
                        partial=getattr(self,'defer_uncertain_items',False)
                        if not partial and self.flows[chat].state.get('alignment_repairs',0)<2:
                            try:schedule_alignment.check_review(decision,job['review_contract'])
                            except ValueError as review_error:
                                raise schedule_alignment.AlignmentReviewRequired(str(review_error)) from review_error
                        contract,operations,notes=schedule_alignment.partition_review(decision,job['review_contract'],candidate.get('operations',[]),self.browser,allow_empty=partial)
                        candidate['alignment']=contract
                        candidate['operations']=operations
                        candidate.setdefault('impact_review',{}).setdefault('unresolved_dependencies',[]).extend(
                            self.browser.targets[r['target']]['title']+': '+r['reason']
                            for r in contract['items'] if r['disposition']=='unresolved')
                        if partial and not operations:
                            flow=self.flows[chat]
                            flow.state['schedule_contract']=contract;flow.save()
                            if self.library:self.library.validate_reads(self.file_snapshot,self.file_evidence)
                            issues=candidate['impact_review']['unresolved_dependencies']+schedule_alignment.verify_current(contract,self.browser)
                            if issues:
                                self.close_deferred(chat,issues);continue
                    else:schedule_alignment.check_review(decision,job['review_contract'])
                    self.accept_decision(chat,candidate,{**job['origin_job'],'alignment_checked':digest(candidate)})
                else:self.accept_decision(chat,decision,job)
            except WhatsAppSendUnconfirmedError:
                if not self.flows[chat].state.get('pending_delivery'):raise
            except Exception as exc:
                flow=self.flows[chat]
                self.record_failure(chat,exc,'planning')
                if isinstance(exc,FileLibraryChanged):
                    try:
                        if self.refresh_changed_source(chat,exc):continue
                    except Exception as refresh_error:
                        self.record_failure(chat,refresh_error,'source_refresh');exc=refresh_error
                    flow.fail(user_reason(exc))
                    self.send(chat,'Status: Blocked\nTask: '+flow.state.get('task','')+
                        '\nReason: '+user_reason(exc)+'\nVerify: Source evidence remains unapproved; no save was replayed.')
                    continue
                semantic=isinstance(exc,schedule_alignment.AlignmentReviewRequired)
                if semantic and flow.state.get('alignment_repairs',0)<2 and flow.state['phase'] in {'PLANNING','SCOPE','QUESTION'}:
                    flow.state['alignment_repairs']=flow.state.get('alignment_repairs',0)+1
                    flow.save()
                    context=self.context(flow,reply=job.get('context',{}).get('reply',''),
                        feedback=user_reason(exc)+' Correct the mapping using inspected authoritative syllabus rows and observed material identity. Inspect additional available evidence when needed. Distinguish genuinely ambiguous items from items the review says are already resolved. Do not ask the requester to repeat known instructions. Keep all remaining goal items accounted for.')
                    # Preserve all per-item findings; the short error summary can
                    # omit later targets and must not become the whole repair input.
                    if job.get('review_contract'):
                        context['placement_review']={'proposal':copy.deepcopy(job['review_contract']),
                                                     'review':copy.deepcopy(decision)}
                    self.submit(chat,context,job['rounds'],job['repairs'])
                elif not semantic and job['repairs']<1 and flow.state['phase'] in {'PLANNING','SCOPE','QUESTION'}:
                    # One bounded alternative review with fresh course evidence, not repeated identical plans.
                    for cid in self.course_ids: self.browser.overview(cid)
                    context=self.context(flow,reply=job.get('context',{}).get('reply',''),
                        feedback=user_reason(exc)+' Reassess the whole goal and a supported alternative. Preserve authorization and scope.')
                    self.submit(chat,context,job['rounds'],job['repairs']+1)
                elif semantic:
                    questions=schedule_alignment.clarification_questions(
                        decision,job.get('review_contract',{'items':[]}),self.browser)
                    if questions:
                        if getattr(self,'defer_uncertain_items',False):
                            self.close_deferred(chat,questions);continue
                        flow.state.update(phase='QUESTION',resume_phase='PLANNING',pending_question=
                            'Please resolve these remaining decisions:\n'+'\n'.join('- '+q for q in questions)+
                            '\nCompleted work is retained; no unapproved write occurred.')
                        flow.save();self.send(chat,'Status: Waiting\nTask: '+flow.state['task']+'\n'+flow.state['pending_question'])
                    else:
                        flow.fail('Placement review could not produce a consistent plan. Technical repair is required. '+user_reason(exc))
                        self.send(chat,'Status: Failed\nTask: '+flow.state['task']+
                            '\nReason: The placement plan did not pass independent review and requires technical repair.'+
                            '\nChange: Your instructions and completed work are retained. No unapproved write occurred.')
                else:
                    flow.fail(user_reason(exc))
                    self.send(chat,'Status: Blocked\nTask: '+flow.state.get('task','')+'\nReason: '+user_reason(exc)+'\nVerify: No Moodle save was authorized or attempted by this planning step.')

    def validate_exploration(self,flow):
        if flow.state.get('exploration_required') and not flow.state.get('exploration_evidence'):
            raise ValueError('Reacquire autonomous source analysis before renewing this Plan.')
        if not flow.state.get('exploration_evidence'):return
        if self.exploration_run!=flow.state['run'] or not self.exploration_broker:
            raise ValueError('Exploration evidence must be reacquired after restart before a write Plan.')
        self.exploration_broker.validate()

    def execute(self,chat):
        flow=self.flows[chat]
        try:
            # Check the entire selected scope before the first write.
            self.validate_exploration(flow)
            if self.library:self.library.validate_reads(self.file_snapshot,self.file_evidence)
            if flow.state.get('schedule_required'):
                contract=flow.state['schedule_contract']
                for cid in contract['course_ids']:self.browser.read_layout(cid)
                schedule_alignment.validate(contract,flow.state['selected'],self.browser,self.file_evidence,required=True)
            for index,op in enumerate(flow.state['selected']):
                self.observer.step(chat,'preflight',current=index,total=len(flow.state['selected']),target=op['target'])
                if not self.execution_checkpoint(flow):return
                if op['capability']=='update_moodle_form': self.browser.preflight(op)
                elif op['capability']=='move_module':self.structure.preflight(op)
                elif op['capability']=='set_section_visibility':self.section_visibility.preflight(op)
                elif op['capability'] in {'upload_file_resource','replace_file_resource'}:self.resources.preflight(op)
                else:
                    fresh=self.groups.resolve(op,flow.state['task']+' '+json.dumps(flow.state.get('conversation',[])))
                    if (fresh['relation_snapshot']!=op['relation_snapshot'] or
                            fresh['group_inventory_digest']!=op['group_inventory_digest']):
                        raise ValueError('Group relation evidence changed after approval.')
            for op in flow.state['selected']:
                self.observer.step(chat,'executing_operation',current=len(flow.state['results']),total=len(flow.state['selected']),target=op['target'])
                if not self.execution_checkpoint(flow):return
                try:
                    if op['capability']=='update_moodle_form':
                        result=self.browser.execute(op,lambda:flow.before_save(op))
                    elif op['capability']=='move_module':
                        result=self.structure.execute(op,lambda:flow.before_save(op),self.completed_operations(flow),
                            before_preserve=lambda:flow.before_preservation_save(op))
                    elif op['capability']=='set_section_visibility':
                        result=self.section_visibility.execute(op,lambda:flow.before_save(op))
                    elif op['capability'] in {'upload_file_resource','replace_file_resource'}:
                        result=self.resources.execute(op,lambda:flow.before_save(op),self.completed_operations(flow))
                    else:
                        result=self.groups.execute(op,flow.state['task']+' '+json.dumps(flow.state.get('conversation',[])),lambda:flow.before_save(op))
                except Exception as action_error:
                    if flow.state['phase']!='EXECUTING': raise
                    self.record_failure(chat,action_error,'save_response')
                    observation=getattr(self.browser,'submission_observation',{})
                    if isinstance(observation,dict) and observation.get('target')==op['target']:
                        flow.state['submission_observation']=dict(observation);flow.save()
                    # A click timeout may occur after save succeeded. Reconcile by reading,
                    # never by clicking Save again or asking for a second approval.
                    try:result=self.verify(op)
                    except Exception:
                        if isinstance(action_error,MoodleFormRejected):
                            flow.state['save_rejection']=str(action_error);flow.save()
                        raise action_error
                flow.receipt(result)
            if self.continue_after_section_visibility(chat):return
            if self.finish_goal_review(chat):return
            self.send(chat,'Status: Done\nTask: '+flow.state['task']+'\nChange: '+
                      '; '.join(r['change'] for r in flow.state['results'])+
                      '\nVerify: Every selected change matched fresh Moodle readback.\nRun: '+flow.state['run'])
        except Exception as exc:
            action=getattr(self.browser,'last_action',{})
            if not isinstance(action,dict):action={}
            reason=user_reason(exc)
            if type(exc).__module__.startswith('playwright') and action:
                description={'read_form':'read the settings form','fill_field':'edit the '+action.get('label','selected')+' control',
                             'verify_prepared_form':'check the prepared settings','save_form':'submit the settings'}.get(action.get('stage'),'complete the page action')
                reason='Moodle could not '+description+' for '+action.get('title','the selected item')+'.'
            diagnostic={'run':flow.state['run'],'error_type':type(exc).__name__,'action':action,
                'possible_save':'write_started' in flow.state,'verified_changes':len(flow.state.get('results',[])),
                'frames':[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
                          for frame in traceback.extract_tb(exc.__traceback__) if str(ROOT) in frame.filename]}
            failure_path=self.root/('execution-failure-'+flow.state['run']+'.json')
            failure_path.write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2),encoding='utf-8')
            flow.fail(reason)
            uncertain=flow.state['phase']=='NEEDS_READBACK'
            if uncertain and isinstance(exc,MoodleSaveNotDispatched):
                self.reconcile(chat)
                return
            if not uncertain and isinstance(exc,(MoodleDateConflict,MoodleSaveNotDispatched,MoveVisibilityConflict)):
                feedback=(' Re-read original date intervals and re-plan using the owner interval-preservation default.'
                          if isinstance(exc,MoodleDateConflict) else
                          ' No move was dispatched. Re-read the destination visibility and ask about the conflicting outcome before fresh approval.'
                          if isinstance(exc,MoveVisibilityConflict) else
                          ' Save was not dispatched. Re-read current settings and propose the remaining work for fresh approval.')
                self.preserve_remaining_task(chat,reason+feedback)
                self.send(chat,'Status: Waiting\nTask: '+flow.state['task']+'\nReason: '+reason+
                    '\nChange: Returning the remaining task to inspection and planning. Previously verified changes are preserved; no save is replayed.')
                return
            self.send(chat,'Status: Failed\nTask: '+flow.state['task']+'\nReason: '+reason+
                      '\nVerify: '+('A save may have occurred. Further execution is paused for readback; no automatic replay.' if uncertain else
                                     'No further save attempted. Previously verified changes: '+str(len(flow.state['results'])))+
                      '\nRun: '+flow.state['run'])
            if uncertain:self.reconcile(chat)  # One bounded read-only recovery pass; never retry Save.

    def execution_checkpoint(self,flow):
        # Intake only: queued tasks must not recursively execute against this page.
        self.poll_safely()
        if self.stopped:
            flow.state.update(phase='INTERRUPTED',reason='Stopped between operations; verified results retained.')
            flow.save()
            return False
        return True

    def verify(self,op):
        flow=next((f for f in self.flows.values() if op in f.state.get('selected',[])),None)
        completed=self.completed_operations(flow) if flow else []
        if op['capability']=='update_moodle_form':return self.browser.verify(op)
        if op['capability']=='move_module':return self.structure.verify(op,completed)
        if op['capability']=='set_section_visibility':return self.section_visibility.verify(op)
        if op['capability'] in {'upload_file_resource','replace_file_resource'}:return self.resources.verify(op,completed)
        return self.groups.verify(op)

    def continue_after_section_visibility(self,chat):
        """Persist the completed prerequisite, then inspect/plan, never auto-approve."""
        state=self.flows[chat].state
        selected=state.get('selected',[])
        if (state.get('phase') not in {'DONE','INTERRUPTED'} or len(state.get('results',[]))!=len(selected) or not selected or
            any(o['capability']!='set_section_visibility' for o in selected)):return False
        memory=continuation(state)
        # This stage contains only prerequisites; retain the original remaining
        # goal instead of replacing it with the now-complete section operations.
        if not any(r.get('retry_of')==state['run'] for r in self.queue):
            self.queue.append({'chat':chat,'id':state['message_ids'][0],'sender':state['sender'],
                'requester_name':state.get('requester_name',state['sender']),
                'text':state['task'],'self_mentioned':state.get('mention_authorized') is True,
                **memory,'task_memory_context':memory,'retry_of':state['run'],
                'recovery_feedback':'The approved section visibility prerequisites were saved and verified. Re-read file forms and the original remaining scope plus conversation. Do not repeat completed moves or section changes. Continue the complete goal and propose only remaining work with fresh approval; answer if all requested outcomes are verified.'})
            self.save_queue()
        self.send(chat,'Status: Waiting\nTask: '+state['task']+'\nChange: Section visibility changes were verified. Checking the remaining file visibility and placement before a new approval.')
        return True

    def finish_goal_review(self,chat):
        flow=self.flows[chat];state=flow.state
        issues=list(state.get('plan',{}).get('impact_review',{}).get('unresolved_dependencies',[]))
        if state.get('schedule_required'):
            if self.library:self.library.validate_reads(self.file_snapshot,self.file_evidence)
            issues+=schedule_alignment.verify_current(state['schedule_contract'],self.browser)
        if not issues:
            if state.get('schedule_required'):state['goal_verified']=True
            state['phase']='DONE';flow.save();return False
        if getattr(self,'defer_uncertain_items',False):
            self.close_deferred(chat,issues);return True
        # A verified batch is not a verified goal. Persist the difference before
        # notifying, and continue through inspection/new approval only.
        state.update(phase='INTERRUPTED',goal_unresolved=list(dict.fromkeys(issues)))
        state['goal_followups']=state.get('goal_followups',0)+1;flow.save()
        if state['goal_followups']>3:
            state.update(phase='QUESTION',resume_phase='PLANNING',pending_question=
                'Completed changes are retained, but these items remain unresolved: '+'; '.join(issues)[:1800]+'. Please clarify the unresolved scope or missing source.')
            flow.save();self.send(chat,'Status: Waiting\nTask: '+state['task']+'\n'+state['pending_question']);return True
        memory=continuation(state)
        if not any(r.get('retry_of')==state['run'] for r in self.queue):
            self.queue.append({'chat':chat,'id':state['message_ids'][0],'sender':state['sender'],
                'requester_name':state.get('requester_name',state['sender']),'text':state['task'],
                'self_mentioned':state.get('mention_authorized') is True,**memory,'task_memory_context':memory,
                'retry_of':state['run'],'recovery_feedback':'Only the approved batch completed. The overall goal remains incomplete. Re-read sources and current state, preserve correct completed effects and resolve remaining items: '+'; '.join(issues)[:2500]})
            self.save_queue()
        self.send(chat,'Status: Waiting\nTask: '+state['task']+'\nChange: The approved batch was verified. Continuing inspection of the remaining goal; the overall task is not complete.');return True

    def close_deferred(self,chat,issues):
        targets=getattr(getattr(self,'browser',None),'targets',None)
        self.send(chat,deferred_items.close(self.flows[chat],issues,
                  targets=targets if isinstance(targets,dict) else None))

    @staticmethod
    def completed_operations(flow):
        return list(zip(flow.state.get('selected',[]),flow.state.get('results',[])))

    def preserve_remaining_task(self,chat,feedback):
        state=self.flows[chat].state
        (self.root/('reconciled-'+state['run']+'.json')).write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        remaining=state['selected'][len(state['results']):]
        if not remaining or any(r.get('retry_of')==state['run'] for r in self.queue):return
        self.queue.append({'chat':chat,'id':state['message_ids'][0],
            'task_id':task_code(state),
            'sender':state['sender'],'text':state['task'],'self_mentioned':state.get('mention_authorized') is True,
            'requester_name':state.get('requester_name',state['sender']),
            'retry_of':state['run'],'conversation':state.get('conversation',[]),
            **{k:copy.deepcopy(state[k]) for k in GOAL_FIELDS if k in state},
            'verified_previous_results':state.get('verified_previous_results',[])+state['results'],
            'completed_operation_memory':state.get('completed_operation_memory',[])+[
                {'operation':operation_memory(op),'receipt':receipt}
                for op,receipt in zip(state['selected'],state['results'])],
            'recovery_feedback':feedback,
            'original_date_interval_evidence':state.get('original_date_interval_evidence') or [
                {'target':item['target'],**interval_evidence(item.get('date_fields',[]))}
                for item in remaining if item.get('date_fields')],
            'previously_selected_scope':[{k:item[k] for k in ('target','summary','scope')} for item in remaining]})
        self.save_queue()

    def reconcile(self,chat):
        flow=self.flows[chat]
        if flow.state.get('phase')!='NEEDS_READBACK': return
        index=len(flow.state['results'])
        op=flow.state['selected'][index]
        if digest(flow.state['selected'])!=flow.state.get('manifest_hash') or flow.state.get('write_started')!=op['id']:
            self.send(chat,'Status: Blocked\nReason: Interrupted-save evidence does not match the approved operation. No write replayed.')
            return
        try:
            result=self.verify(op)
            flow.state['results'].append(result)
            finished=len(flow.state['results'])==len(flow.state['selected'])
            flow.state['phase']='DONE' if finished and not flow.state.get('requires_goal_verification') else 'INTERRUPTED'
            flow.save()
            if self.continue_after_section_visibility(chat):return
            if finished and self.finish_goal_review(chat):return
            self.preserve_remaining_task(chat,'The interrupted change matched approved readback. Re-plan only remaining changes with fresh evidence and approval.')
            self.send(chat,'Status: '+('Done' if flow.state['phase']=='DONE' else 'Waiting')+
                      '\nTask: '+flow.state['task']+'\nVerify: The interrupted change was confirmed by fresh readback.'+
                      '\nChange: No write was replayed. Remaining changes are queued for fresh planning and approval.')
        except Exception as exc:
            original_matches=False
            try:
                # After restart a pending POST may still finish in the old tab.
                # An unchanged snapshot alone cannot clear that uncertainty.
                pending=flow.state.get('submission_observation',{}).get('completion')=='pending'
                if not pending and op['capability']=='update_moodle_form':original_matches=self.browser.unchanged_after_attempt(op) is True
                elif not pending and op['capability'] in {'update_group_membership','update_grouping_membership'}:
                    fresh=self.groups.resolve(op,flow.state['task']+' '+json.dumps(flow.state.get('conversation',[])))
                    original_matches=fresh['relation_snapshot']==op['relation_snapshot']
                elif not pending and op['capability']=='move_module':
                    self.browser.check_submission_settled(op)
                    self.structure.preflight(op,self.completed_operations(flow));original_matches=True
                elif not pending and op['capability']=='set_section_visibility':
                    self.browser.check_submission_settled(op)
                    self.section_visibility.preflight(op);original_matches=True
                elif not pending and op['capability'] in {'upload_file_resource','replace_file_resource'}:
                    self.browser.check_submission_settled(op)
                    self.resources.preflight(op,self.completed_operations(flow));original_matches=True
            except Exception:pass  # Read failure does not authorize clearing an uncertain save.
            if original_matches:
                feedback=flow.state.get('save_rejection','')
                try:
                    if op['capability']=='update_moodle_form':validate_dates(self.browser.forms[op['target']]['fields'],op['fields'],op['title'])
                except MoodleDateConflict as conflict:feedback=str(conflict)
                flow.state.update(phase='INTERRUPTED',readback_resolution='original_observed_state',
                    reason='Fresh readback matches the original settings. The attempted changes are not present; no save was replayed.')
                flow.save()
                # Preserve the remaining original goal behind already queued requests.
                # This is a fresh planning task, never replay of an approved operation.
                self.preserve_remaining_task(chat,feedback or 'An attempted change is absent in fresh readback. Reassess a supported alternative.')
                self.send(chat,'Status: Waiting\nTask: '+flow.state['task']+
                    '\nTarget: '+op['title']+'\nVerify: Fresh settings match the original state; this attempted change is not present.'+
                    '\nChange: '+str(len(flow.state['results']))+' earlier changes were verified. No save was replayed. '+
                    'The remaining task is queued for revised planning and fresh approval; earlier queued requests will be processed first.'+
                    ('\nReason: '+feedback if feedback else ''))
                return
            # A settled move with only contextual visibility differences has a
            # known partial outcome. Preserve its position and ask/plan repairs.
            if (op['capability']=='move_module' and
                flow.state.get('submission_observation',{}).get('target')==op['target'] and
                flow.state.get('submission_observation',{}).get('completion')=='response_loaded'):
                try:proof=self.structure.visibility_drift(op,self.completed_operations(flow))
                except Exception:proof=None
                if isinstance(proof,dict) and proof.get('classification')=='move_applied_visibility_changed':
                    self.replan_visibility_repair(chat,proof)
                    return
            # Keep uncertain evidence; never infer no save from changed or mixed values.
            self.send(chat,'Status: Blocked\nTask: '+flow.state['task']+
                      '\nVerify: The interrupted save is still unresolved.\nReason: '+user_reason(exc)+
                      '\nChange: No write replayed. Check the affected Moodle item before continuing.')

    def retire_section_submit(self,run_id):
        """Operator recovery of older observation formats with exact-tab evidence."""
        matches=[(chat,flow) for chat,flow in self.flows.items() if flow.state.get('run')==run_id]
        if len(matches)!=1:raise ValueError('One exact current interrupted run is required.')
        chat,flow=matches[0];state=flow.state;ops=state.get('selected',[]);results=state.get('results',[])
        if (state.get('phase')!='NEEDS_READBACK' or len(results)>=len(ops) or
            digest(ops)!=state.get('manifest_hash') or any(r.get('verified') is not True for r in results) or
            state.get('write_started')!=ops[len(results)]['id']):
            raise ValueError('The interrupted operation does not match its immutable manifest and receipts.')
        (self.root/('retired-submit-source-'+run_id+'.json')).write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        observation=self.browser.retire_unobserved_section_submit(ops[len(results)],state.get('submission_observation',{}))
        state['submission_observation']=observation;flow.save()
        self.trace.note(run_id,task_code(state),'recovery','retire_legacy_section_tab',observation)
        self.reconcile(chat)

    @staticmethod
    def validate_repair_plan(state,operations):
        proof=state.get('readback_repair',{})
        if proof.get('move_replay_forbidden') and any(o.get('capability')=='move_module' and
                o.get('target')==proof.get('target') for o in operations):
            raise ValueError('Fresh readback already confirmed this activity was moved. Repair its settings with update_moodle_form; do not move it again.')

    def repair_blocked_request(self,chat,record):
        """Consume a genuine follow-up only after proving a bounded partial result.

        This path performs reads and planning only. New writes need a new approval.
        Unknown/mixed content or pending responses remain quarantined.
        """
        flow=self.flows[chat];state=flow.state
        if (state.get('phase')!='NEEDS_READBACK' or record.get('sender')!=state.get('sender') or
            record.get('chat')!=chat or record.get('self_mentioned') is not True or
            not record.get('id') or record['id'] in state.get('message_ids',[]) or
            re.fullmatch(r'(?:OK|NO)\s+\d{6}',record.get('text','').strip(),re.I)):
            return False
        selected=state.get('selected',[]);results=state.get('results',[])
        if (len(results)>=len(selected) or any(r.get('verified') is not True for r in results) or
            digest(selected)!=state.get('manifest_hash')):
            raise ValueError('The interrupted manifest or receipts are inconsistent; no repair was planned.')
        op=selected[len(results)]
        if state.get('write_started')!=op['id'] or op['capability']!='move_module':return False
        observation=state.get('submission_observation',{})
        if observation.get('target')!=op['target'] or observation.get('completion')!='response_loaded':
            raise MoodleSubmissionUnconfirmed('The recorded submit has not been confirmed settled; repair planning cannot clear it.')
        proof=self.structure.visibility_drift(op,self.completed_operations(flow))
        if not isinstance(proof,dict) or proof.get('classification')!='move_applied_visibility_changed':return False
        return self.replan_visibility_repair(chat,proof,record)

    def replan_visibility_repair(self,chat,proof,record=None):
        """No write: preserve a proven move and plan the unresolved visibility."""
        flow=self.flows[chat];state=flow.state;selected=state['selected'];results=state['results']
        if (state.get('phase')!='NEEDS_READBACK' or digest(selected)!=state.get('manifest_hash') or
            len(results)>=len(selected) or any(r.get('verified') is not True for r in results)):
            raise ValueError('The interrupted move manifest or receipts changed; no recovery planned.')
        op=selected[len(results)]
        if state.get('write_started')!=op['id'] or proof.get('target')!=op['target'] or not proof.get('move_replay_forbidden'):
            raise ValueError('The partial movement evidence does not match the interrupted operation.')
        old=copy.deepcopy(state)
        instruction=record['text'] if record else ''
        if record is None:
            record={'chat':chat,'id':old['message_ids'][0],'sender':old['sender'],
                    'requester_name':old.get('requester_name',old['sender']),
                    'self_mentioned':old.get('mention_authorized') is True}
        # The already-applied move is replaced by a settings repair proposal,
        # not counted as a verified operation or dispatched a second time.
        repair={'id':op['id'],'capability':'update_moodle_form','target':op['target'],'course_id':op['course_id'],
            'summary':'Review and repair visibility for the already moved '+op['title'],
            'scope':'requested','reason':'Movement was read back; visibility differs from the preserved settings.',
            'depends_on':[],'params':{},'fields':([{'key':proof['field'],'value':proof['expected_value']}]
                if proof.get('can_restore',True) else [])}
        remaining=[repair]+[operation_memory(o) for o in selected[len(results)+1:]]
        ids={o['id'] for o in remaining}
        for item in remaining:item['depends_on']=[d for d in item.get('depends_on',[]) if d in ids]
        memory={'task_id':task_code(old),'source_run':old['run'],'original_request':old['task'],
            **{k:copy.deepcopy(old[k]) for k in GOAL_FIELDS if k in old},
            'conversation':old.get('conversation',[])+([instruction] if instruction else []),
            'verified_previous_results':old.get('verified_previous_results',[])+results,
            'completed_operation_memory':old.get('completed_operation_memory',[])+[
                {'operation':operation_memory(o),'receipt':r} for o,r in zip(selected,results)],
            'previously_selected_scope':remaining,'original_date_interval_evidence':old.get('original_date_interval_evidence',[]),
            'readback_repair':{**proof,'source_run':old['run']}}
        feedback=('The interrupted move is conclusively at the approved position, but its visibility changed. '
            'Do not repeat that move or the verified earlier work. Interpret the new requester instruction in conversation, '
            'review the proposed settings repair and the remaining original scope using current forms, and ask about any '
            'ambiguous file scope. Fresh scope selection and approval are required. Observed recovery: '+json.dumps(proof))
        if proof.get('requires_visibility_choice'):
            feedback+=' The original visibility meaning is absent from current options. Use the requester latest visibility instruction if supplied. When shown-on-course-page visibility requires showing the section, propose set_section_visibility as an explicit prerequisite approval including its full impact, then re-inspect file options. Never unhide a section without that approval or map old numeric values onto different meanings. Ask the requester only if the desired visibility remains ambiguous.'
        pending={**record,**memory,'text':old['task'],'retry_of':old['run'],'task_memory_context':memory,'recovery_feedback':feedback}
        (self.root/('interrupted-save-'+old['run']+'.json')).write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
        state.update(phase='INTERRUPTED',readback_resolution='move_applied_visibility_changed',readback_repair=memory['readback_repair'])
        flow.save()
        self.start(chat,pending)
        return True

    def reconcile_completed_submission(self):
        self.browser.refresh_editor_submission()
        observation=getattr(self.browser,'submission_observation',{})
        if not isinstance(observation,dict) or observation.get('completion')!='response_loaded':return
        for chat,flow in self.flows.items():
            old=flow.state.get('submission_observation',{})
            if (flow.state.get('phase')=='NEEDS_READBACK' and old.get('completion')=='pending' and
                    old.get('target')==observation.get('target') and old.get('started')==observation.get('started')):
                flow.state['submission_observation']=dict(observation);flow.save()
                self.reconcile(chat)

    def handle_queue(self):
        # One bounded task at a time; continue polling both chats while planning/waiting.
        active=[c for c,f in self.flows.items() if f.state.get('phase') in
                {'PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'}]
        for i,record in enumerate(self.queue):
            chat=record['chat']; flow=self.flows[chat]
            self.trace_chat=chat
            if self.readable_chats is not None and chat not in self.readable_chats:continue
            if flow.state.get('pending_delivery'):continue
            if chat in self.mention_only_chats:
                bound=(flow.state.get('mention_authorized') and flow.state.get('sender')==record['sender'] and
                       flow.state.get('phase') in {'QUESTION','SCOPE','APPROVAL'} and
                       bool(record.get('reply_boundary')) and record.get('reply_boundary')==flow.state.get('reply_boundary') and
                       record.get('reply_to_run')==flow.state.get('run'))
                if (record.get('reply_boundary') and not bound) or (not record.get('self_mentioned') and not bound):
                    self.queue.pop(i);self.save_queue();return
            command=task_command(record['text'])
            if command:
                self.queue.pop(i);self.save_queue()
                try:self.handle_task_command(chat,record,command)
                except WhatsAppSendUnconfirmedError:
                    if not flow.state.get('pending_delivery'):raise
                except Exception as exc:
                    self.record_failure(chat,exc,'task_memory_command')
                    if flow.state.get('phase')=='PLANNING' and chat not in self.jobs:
                        flow.fail(user_reason(exc))
                    self.send(chat,'Task memory: '+user_reason(exc),requester=record)
                return
            if chat in self.jobs: continue
            if record.get('retry_of') and active:continue  # A preserved task is never an answer to a different task's question.
            if active and chat not in active: continue
            if active and flow.state.get('sender')!=record['sender']: continue
            if flow.state.get('phase')=='NEEDS_READBACK':
                if record.get('repair_review_version')!=IMPLEMENTATION_VERSION:
                    record['repair_review_version']=IMPLEMENTATION_VERSION;self.save_queue()
                    old_run=flow.state.get('run')
                    try:
                        if self.repair_blocked_request(chat,record):return
                    except WhatsAppSendUnconfirmedError:
                        if not flow.state.get('pending_delivery'):raise
                        return
                    except Exception as exc:
                        self.record_failure(chat,exc,'blocked_repair_readback')
                        if flow.state.get('run')!=old_run:
                            flow.fail(user_reason(exc))
                        self.send(chat,'Status: Waiting\nReason: Repair inspection could not finish: '+user_reason(exc)+'\nChange: No write was replayed.')
                        return
                    finally:
                        if flow.state.get('run')!=old_run:
                            self.queue.remove(record);self.save_queue()
                if not record.get('readback_notice_sent'):
                    record['readback_notice_sent']=True;self.save_queue()
                    self.send(chat,'Status: Waiting\nTask: '+record['text']+
                        '\nReason: Your request was received. An earlier save needs readback; checking it without repeating any write.'+
                        '\nVerify: '+str(len(flow.state.get('results',[])))+' earlier changes were verified.')
                    self.reconcile(chat)
                    return
                continue
            self.queue.pop(i); self.save_queue()
            body=record['text']
            if flow.state.get('run'):
                self.trace.note(flow.state['run'],task_code(flow.state),'intake','consumed_message',{'request':record,'phase':flow.state.get('phase')})
            # Only lifecycle and exact approval syntax are mechanical commands.
            if body.strip().casefold() in {'stop agent','shutdown agent','停止','退出','停止 agent'}:
                self.stopped=True; return
            try:
                phase=flow.state.get('phase')
                if phase=='APPROVAL':
                    if re.fullmatch(r'(?:OK|NO)\s+\d{6}',body.strip(),re.I):
                        if flow.approve(body,record['sender'],record['id']):
                            with self.health.work('executing_moodle'):self.execute(chat)
                        else:self.send(chat,'Status: Done\nChange: Cancelled. No Moodle write was made.')
                    else:
                        flow.bound_reply(record['sender'],record['id'])
                        flow.state.setdefault('conversation',[]).append(body)
                        flow.state.update(phase='PLANNING',approval_sent=False,code=f'{secrets.randbelow(1000000):06d}')
                        for key in ('candidates','selected','manifest_hash','alignment_repairs'):flow.state.pop(key,None)
                        flow.save();self.browser.forms.clear()
                        for cid in self.course_ids:self.browser.overview(cid)
                        self.submit(chat,self.context(flow,reply=body))
                elif phase in {'QUESTION','SCOPE'}:
                    flow.bound_reply(record['sender'],record['id'])
                    flow.state.setdefault('conversation',[]).append(body)
                    if phase=='QUESTION': flow.state['phase']=flow.state.pop('resume_phase','PLANNING')
                    flow.state.pop('pending_question',None)
                    flow.state.pop('alignment_repairs',None)
                    flow.save()
                    flow.state.pop('reply_boundary',None);flow.save()
                    self.submit(chat,self.context(flow,reply=body))
                else:self.start(chat,record)
            except WhatsAppSendUnconfirmedError:
                if not flow.state.get('pending_delivery'):raise
            except Exception as exc:
                self.record_failure(chat,exc,'incoming_reply')
                if flow.state.get('phase')=='PLANNING' and chat not in self.jobs:flow.fail(user_reason(exc))
                self.send(chat,'Status: Waiting\nReason: '+user_reason(exc))
            return


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',action='store_true',help='Monitor configured chats; enables replies and approval workflow.')
    parser.add_argument('--inspect-forms',action='store_true',help='Read representative settings and group forms at startup, without saving.')
    parser.add_argument('--recover-mentioned',action='store_true',help='Recover one operator-verified missed mention from the fixed local candidate file.')
    parser.add_argument('--retry-failed-run',help='Operator-authorized retry of one exact failed planning run; never replays approvals or writes.')
    parser.add_argument('--diagnose-blocked-run',help='Queue automatic Codex diagnosis of one exact blocked run without replaying it.')
    parser.add_argument('--retry-undelivered-run',help='Re-plan an exact failed, unapproved delivery with its conversation and selected scope preserved.')
    parser.add_argument('--replan-unwritten-run',help='Operator-only fresh planning of one failed run with no possible save; invalidate old approval and preserve scope.')
    parser.add_argument('--recover-queued-message',help='Operator-only restore of one authenticated mentioned request from this restart\'s archived queue; never restores approval.')
    parser.add_argument('--recover-question-run',help='Explicit operator recovery of an unapproved question and its archived answer after fresh outgoing readback.')
    parser.add_argument('--replan-waiting-run',help='Operator-only fresh planning of an interrupted, unapproved planning question after an owner policy clarification.')
    parser.add_argument('--renew-approval-run',help='Operator-authorized fresh inspection and new approval for an exact unwritten selected scope.')
    parser.add_argument('--recheck-task-id',help='Operator-requested fresh syllabus check of one completed task; requires correction instructions and new approval.')
    parser.add_argument('--recheck-instruction-file',type=Path,help='UTF-8 owner correction instructions for --recheck-task-id.')
    parser.add_argument('--retire-unobserved-section-submit',help='Explicit operator recovery of one legacy zero-POST section-name click: retire its marked tab, independently read back, then re-plan with new approval.')
    args=parser.parse_args()
    if bool(args.recheck_task_id)!=bool(args.recheck_instruction_file):
        parser.error('--recheck-task-id and --recheck-instruction-file must be provided together.')
    if args.run:
        from watch_failure_report import ensure_running
        try:ensure_running()
        except OSError as exc:print('Failure report watcher could not start: '+type(exc).__name__,flush=True)
    config,env=load_config()
    ChromeApproval.approval_timeout=config['approval_timeout_seconds']
    browser_mode=config.get('browser_mode','existing')
    if browser_mode not in {'existing','dedicated'}:
        raise ValueError('browser_mode must be existing or dedicated.')
    # Local port only provides an exclusive-process lock; no HTTP endpoints or secrets.
    lock=socket.socket(); lock.bind(('127.0.0.1',18789))
    from playwright.sync_api import sync_playwright, Error as BrowserError
    with sync_playwright() as p:
        if browser_mode=='dedicated':
            from dedicated_chrome import ensure_browser, PROFILE
            env['COURSE_AGENT_CDP_URL']=ensure_browser()
            env['COURSE_AGENT_CHROME_USER_DATA_DIR']=str(PROFILE)
            print('Connecting to the persistent v2 Chrome profile.',flush=True)
        else:
            print('Connecting to current Chrome. Allow its debugging dialog if shown.',flush=True)
        status_path=ROOT/'reports/shared_chrome/status.json'
        status_path.parent.mkdir(parents=True,exist_ok=True)
        status_path.write_text(json.dumps({'status':'waiting_for_chrome','pid':os.getpid(),
                                         'updated':datetime.now().astimezone().isoformat()}),encoding='utf-8')
        session=SharedChrome(p,env)
        try:
            wa=session.whatsapp_page(); wait_for_whatsapp_ready(wa,30)
            browser=MoodleBrowser(session.moodle_page(wa),env)
            with ThreadPoolExecutor(max_workers=1) as pool, ExitStack() as lifecycle:
                runtime=Runtime(wa,browser,config['chats'],config['course_ids'],
                                CodexPlanner(env,ROOT/'reports/shared_chrome/planner'),
                                ROOT/'reports/shared_chrome/state',pool,
                                FileLibrary(config['file_library_root']),config['file_library_planner_access'],
                                config['mention_only_chats'],config['recent_result_limit'])
                runtime.auto_block_recovery=bool(config.get('auto_block_recovery',{}).get('enabled',False))
                runtime.defer_uncertain_items=config['defer_uncertain_items']
                if config['autonomous_exploration']['enabled']:
                    from chrome_exploration import CodexExplorer
                    runtime.explorer=CodexExplorer(env,ROOT/'reports/exploration',config['autonomous_exploration']['timeout_seconds'])
                    runtime.exploration_max_calls=config['autonomous_exploration']['max_calls']
                if args.run and config['chrome_chat_enabled']:
                    from chrome_chat_bridge import attach
                    lifecycle.callback(attach(runtime,ROOT/'reports/chrome_chat').close)
                remote_gateway = None
                if args.run and config['remote_workspace']['enabled']:
                    from remote_gateway import Gateway
                    remote_config = dict(config['remote_workspace'])
                    remote_config['public_origin'] = os.environ.get('COURSE_AGENT_REMOTE_PUBLIC_ORIGIN', remote_config['public_origin'])
                    remote_gateway = Gateway(ROOT/'reports/remote_workspace', env, remote_config).start()
                    lifecycle.callback(remote_gateway.close)
                lifecycle.enter_context(HealthHeartbeat(runtime.health,ROOT/'reports/shared_chrome/health.json'))
                def recover_transport(action):
                    page,reconnected=session.recover_whatsapp(runtime.wa,action)
                    runtime.wa=page
                    if reconnected:
                        if not runtime.browser.rebind_pending_page(page.context):
                            runtime.browser.page=session.moodle_page(page)
                        runtime.browser.login_attempted=False
                runtime.recover_transport=recover_transport
                def recover_moodle_read(page):
                    with runtime.health.work('recovering_moodle'):
                        replacement,wa_page,reconnected=session.recover_moodle_page(page)
                        runtime.wa=wa_page
                        if reconnected:runtime.browser.login_attempted=False
                        return replacement
                browser.recover_read_page=recover_moodle_read
                browser.diagnostic_path=ROOT/'reports/shared_chrome/moodle_read_recovery.json'
                browser.submission_diagnostic_path=ROOT/'reports/shared_chrome/moodle_submit_observation.json'
                library_snapshot=runtime.library.snapshot()
                print(f"File library ready: {len(library_snapshot['files'])} indexed sources",flush=True)
                session.focus_whatsapp(wa)
                runtime.account_name=discover_account_name(wa)
                print('WhatsApp account display name observed: '+str(bool(runtime.account_name)),flush=True)
                runtime.baseline()
                for cid in config['course_ids']:
                    try:overview=browser.overview(cid)
                    except MoodleLoginRequired:
                        if not args.run:raise
                        print('Moodle login is required. WhatsApp intake will remain available.',flush=True)
                        break
                    print(f"Ready: {overview['label']}, {len(overview['modules'])} activities, {len(overview['groups'])} groups",flush=True)
                    if args.inspect_forms:
                        keys=[overview['modules'][0]['key'],overview['groups'][0]['key'],*overview['creation_targets']]
                        for key in keys:
                            form=browser.inspect(key)
                            print(f"Form checked: {key}, {len(form['fields'])} fields",flush=True)
                if not args.run:
                    print('Read-only check passed. No messages or Moodle writes sent.',flush=True); return
                print('Monitoring: '+', '.join(config['chats']),flush=True)
                if args.retire_unobserved_section_submit:
                    runtime.retire_section_submit(args.retire_unobserved_section_submit)
                    print('Retired the exact legacy section-edit tab and independently reconciled; no save replayed.',flush=True)
                if args.recover_queued_message:
                    runtime.recover_queued_message(args.recover_queued_message)
                if args.recover_question_run:
                    runtime.recover_question_reply(args.recover_question_run)
                    print('Recovered the exact previously queued mentioned request.',flush=True)
                for chat in runtime.flows:
                    # An explicitly recovered repair instruction gets the read-only
                    # repair assessment instead of another identical blocked notice.
                    if not any(r.get('chat')==chat and r.get('sender')==runtime.flows[chat].state.get('sender') for r in runtime.queue):
                        runtime.reconcile(chat)
                if args.renew_approval_run:
                    runtime.renew_approval(args.renew_approval_run)
                    print('Renewed the exact selected scope after fresh inspection; awaiting current approval.',flush=True)
                if args.recheck_task_id:
                    runtime.recheck_task(args.recheck_task_id,args.recheck_instruction_file.read_text(encoding='utf-8'))
                    print('Queued an explicit correction of the completed result; current approval is required for every write.',flush=True)
                if args.retry_failed_run:
                    runtime.retry_failed_run(args.retry_failed_run)
                    print('Queued the exact failed planning request with fresh approval required.',flush=True)
                if args.diagnose_blocked_run:
                    matches=[(c,f) for c,f in runtime.flows.items() if f.state.get('run')==args.diagnose_blocked_run]
                    if len(matches)!=1 or matches[0][1].state.get('phase') not in {'FAILED','NEEDS_READBACK','INTERRUPTED'}:
                        raise ValueError('Diagnosis needs one exact blocked task.')
                    chat,flow=matches[0]
                    runtime.send(chat,'Status: Blocked\nReason: '+flow.state.get('reason','Interrupted task requires diagnosis.'))
                if args.replan_waiting_run:
                    runtime.retry_failed_run(args.replan_waiting_run,waiting=True)
                    print('Queued the clarified planning question with completed work preserved and fresh approval required.',flush=True)
                if args.retry_undelivered_run:
                    runtime.retry_failed_run(args.retry_undelivered_run,undelivered=True)
                    print('Queued the undelivered request for fresh planning with its prior scope preserved.',flush=True)
                if args.replan_unwritten_run:
                    runtime.retry_failed_run(args.replan_unwritten_run,unwritten=True)
                    print('Queued the unwritten failed request for fresh evidence and a new approval.',flush=True)
                if args.recover_mentioned:
                    runtime.recover_mention(ROOT/'reports/shared_chrome/missed_mention_candidate.json')
                    print('Recovered one freshly verified missed mentioned task; no approval replayed.',flush=True)
                while not runtime.stopped:
                    runtime.health.stage='listening'
                    transport_ready=runtime.poll_safely()
                    if runtime.stopped:break
                    if transport_ready or runtime.readable_chats:
                        try:runtime.reconcile_deliveries()
                        except (BrowserError,WhatsAppSendUnconfirmedError,WhatsAppIntakeUnavailable):pass
                        runtime.reconcile_completed_submission()
                        runtime.complete_jobs(); runtime.service_block_recovery()
                        if not runtime.stopped:runtime.handle_queue()
                    if getattr(runtime,'panel',None):runtime.panel.refresh(runtime)
                    status={'status':'monitoring' if transport_ready else 'reconnecting_whatsapp',
                            'runtime_version':IMPLEMENTATION_VERSION,
                            # Set by deploy_v2.ps1; empty when started without a recorded commit.
                            'source_commit':os.environ.get('COURSE_AGENT_SOURCE_COMMIT',''),
                            'reason':runtime.transport_error,'pid':os.getpid(),
                            'updated':datetime.now().astimezone().isoformat(),
                            'chats':config['chats'],'queued':len(runtime.queue),
                            'mention_only_chats':sorted(runtime.mention_only_chats),
                            'whatsapp_response_policy':'v1',
                            'chat_types':runtime.chat_types,
                            'named_mention_identity_ready':bool(runtime.account_name),
                            'file_library_root':str(runtime.library.root),
                            'file_library_count':len((runtime.file_snapshot or library_snapshot)['files']),
                            'file_library_planner_access':runtime.library_planner_access,
                            'phases':{c:f.state.get('phase','IDLE') for c,f in runtime.flows.items()}}
                    status['pending_delivery']=[c for c,f in runtime.flows.items() if f.state.get('pending_delivery')]
                    status['remote_workspace']={'enabled':remote_gateway is not None,
                        'active_tasks':remote_gateway.store.active_count() if remote_gateway else 0,
                        'port':config['remote_workspace']['port'],
                        'lan_address':config['remote_workspace'].get('lan_address',''),
                        'lan_subnet':config['remote_workspace'].get('lan_subnet',''),
                        'public_origin':remote_gateway.config.get('public_origin','') if remote_gateway else ''}
                    status['intake_health']=runtime.health.snapshot()
                    status['observability']=runtime.observer.snapshot()
                    status['trace_write_error']=runtime.trace.last_error
                    status['auto_block_recovery']={'enabled':runtime.auto_block_recovery,'model':'gpt-6-astra','reasoning_effort':'high',
                        'tasks':{c:f.state.get('auto_recovery_status') for c,f in runtime.flows.items() if f.state.get('auto_recovery_job')}}
                    status['planner_trace_write_error']=runtime.planner.trace.last_error if isinstance(runtime.planner,CodexPlanner) else None
                    (ROOT/'reports/shared_chrome/status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
                    time.sleep(2)
        finally:
            # Disconnecting the listener must not cancel a slow browser submission.
            if 'browser' in locals() and browser.submission_observation.get('completion')=='pending':
                pending_page=browser.submission_page
                if pending_page in session.owned_pages:session.owned_pages.remove(pending_page)
            session.close();lock.close()
    if 'runtime' in locals() and runtime.restart_recovery:
        retry=runtime.restart_recovery
        flag='--replan-unwritten-run' if retry['unwritten'] else '--retry-failed-run'
        with (ROOT/'reports/auto_recovery/restarted-listener.log').open('a',encoding='utf-8') as log:
            subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--run',flag,retry['run']],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


if __name__=='__main__':
    try: main()
    except KeyboardInterrupt: print('Stopped; existing Chrome remains open.')
    except Exception as exc:
        # Browser exceptions can include connection endpoint IDs; keep startup logs concise.
        import traceback
        frames=traceback.extract_tb(exc.__traceback__)
        location=next((f'{Path(f.filename).name}:{f.lineno}' for f in reversed(frames) if str(ROOT) in f.filename),'startup')
        reason=user_reason(exc)
        print('Startup/check did not finish: '+type(exc).__name__+' at '+location+'. '+reason,file=sys.stderr)
        report=ROOT/'reports/shared_chrome/startup_error.json'
        report.parent.mkdir(parents=True,exist_ok=True)
        error={'status':'startup_failed','type':type(exc).__name__,'location':location,'reason':reason,
               'updated':datetime.now().astimezone().isoformat()}
        report.write_text(json.dumps(error,indent=2),encoding='utf-8')
        (report.parent/'status.json').write_text(json.dumps(error,indent=2),encoding='utf-8')
        sys.exit(1)
