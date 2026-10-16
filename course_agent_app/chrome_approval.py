"""Durable approval state for the shared-Chrome entry of approval_server.

Scope selection is planning, never permission to save. One incoming, bound OK
authorizes the immutable selected manifest. A possible save is never replayed.
"""
from __future__ import annotations

import copy
import json
import secrets
import time
import uuid
from pathlib import Path

from chrome_moodle import digest
from chrome_task_memory import TaskMemory,task_code


class ChromeApproval:
    # Seconds a delivered approval stays valid; None means no time limit.
    # Execution preflight still re-reads Moodle and rejects any changed state.
    approval_timeout = None

    def __init__(self, path, chat):
        self.path = Path(path)
        self.chat = chat
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if self.state.get('run'):
            self.state['task_id']=TaskMemory(self.path.parent).id_for_run(self.state['run']) or task_code(self.state)
        if self.state.get('phase') in {'APPROVED','EXECUTING'}:
            self.state['phase'] = 'NEEDS_READBACK'
            self.save()
        elif self.state.get('phase') in {'SCOPE','APPROVAL','QUESTION','PLANNING'}:
            # A restart cannot accept old replies as a new-run approval.
            self.state['phase'] = 'INTERRUPTED'
            self.save()

    def save(self,*,register=False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)
        TaskMemory(self.path.parent).record(self.state,register=register)
        if getattr(self,'on_save',None):self.on_save(self.state)

    def begin(self, task, sender, message_id, *, task_id=None):
        if self.state.get('phase') in {'PLANNING','SCOPE','QUESTION','APPROVAL','APPROVED','EXECUTING','NEEDS_READBACK'}:
            raise ValueError('Finish or cancel the active task; an uncertain save requires readback first.')
        if not sender or not message_id:
            raise ValueError('A request needs stable sender and message provenance.')
        old_code=self.state.get('code')
        if task_id:
            old_code=TaskMemory(self.path.parent).lookup(task_id,self.chat,sender).get('code')
        code=f'{secrets.randbelow(1000000):06d}'
        while code==old_code:code=f'{secrets.randbelow(1000000):06d}'
        self.state = {'run':uuid.uuid4().hex[:12], 'code':code,
                      'chat':self.chat,'sender':sender,'task':task,'message_ids':[message_id],
                      'phase':'PLANNING','created':time.time(),'results':[]}
        self.state['task_id']=task_id or task_code(self.state)
        self.save(register=task_id is None)

    def bound_reply(self, sender, message_id):
        if sender != self.state.get('sender') or not message_id or message_id in self.state.get('message_ids',[]):
            raise ValueError('Reply is not new or does not belong to the current requester.')
        self.state['message_ids'].append(message_id)

    def offer(self, plan, operations):
        if self.state.get('phase') not in {'PLANNING','SCOPE'}:
            raise ValueError('A delivered or approved plan cannot be replaced by the planner.')
        ids = [o.get('id') for o in operations]
        if not ids or any(not isinstance(i,str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Plan needs unique operation IDs.')
        prior = set()
        for op in operations:
            if op.get('scope') not in {'requested','related'} or not str(op.get('summary','')).strip():
                raise ValueError('Each operation needs a scope and a visible change summary.')
            if not set(op.get('depends_on',[])) <= prior:
                raise ValueError('Prerequisites must be exact earlier operation IDs.')
            prior.add(op['id'])
        if not any(o['scope']=='requested' for o in operations):
            raise ValueError('Plan must preserve the requested work.')
        self.state.update(plan=copy.deepcopy(plan), candidates=copy.deepcopy(operations), phase='SCOPE')
        self.save()
        if not any(o['scope']=='related' for o in operations):
            self.select(ids)

    def select(self, selected_ids, *, allow_narrowing=False):
        if self.state.get('phase') != 'SCOPE':
            raise ValueError('No scope choice is pending.')
        candidates = self.state['candidates']
        wanted = set(selected_ids)
        allowed = {o['id'] for o in candidates}
        required = {o['id'] for o in candidates if o['scope']=='requested'}
        if not wanted or (not allow_narrowing and not required <= wanted) or not wanted <= allowed:
            raise ValueError('Scope must include the requested work and only offered related changes.')
        selected = [copy.deepcopy(o) for o in candidates if o['id'] in wanted]
        if any(not set(o.get('depends_on',[])) <= wanted for o in selected):
            raise ValueError('Selected changes need one of the omitted prerequisites.')
        # Optional due/cutoff changes may share one form. The user chooses them
        # independently, then the executor saves the selected union exactly once.
        merged=[]
        by_target={}
        prior_ids=set()
        merge_prerequisites={}
        for op in selected:
            key=op.get('target')
            can_merge=op['capability']=='update_moodle_form' and op.get('target_kind') not in {'new_group','new_grouping'}
            if can_merge and key in by_target:
                first=by_target[key]
                if not set(op.get('depends_on',[])) <= merge_prerequisites[key] | set(first.get('component_ids',[first['id']])):
                    raise ValueError('Merging this form would run before an approved prerequisite; recompose the dependency order.')
                if first.get('fingerprint')!=op.get('fingerprint'):
                    raise ValueError('Related field changes were read from different form versions.')
                fields={p['key']:p for p in first['fields']}
                for patch in op['fields']:
                    if patch['key'] in fields and fields[patch['key']]['value']!=patch['value']:
                        raise ValueError('Selected changes disagree on the same setting.')
                    if patch['key'] not in fields: first['fields'].append(patch)
                first['summary']+='; '+op['summary']
                first.setdefault('component_ids',[first['id']]).append(op['id'])
            else:
                merged.append(op)
                if can_merge:
                    by_target[key]=op
                    merge_prerequisites[key]=set(prior_ids)
            prior_ids.add(op['id'])
        selected=merged
        self.state.update(selected=selected, manifest_hash=digest(selected), phase='APPROVAL',
                          approval_sent=False, expires=None)
        self.save()

    def mark_sent(self):
        if self.state.get('phase') != 'APPROVAL':
            raise ValueError('Approval delivery has no matching immutable plan.')
        self.state['approval_sent'] = True
        self.state['expires'] = None if self.approval_timeout is None else time.time()+self.approval_timeout
        self.save()

    def approve(self, body, sender, message_id):
        self.bound_reply(sender, message_id)
        if self.state.get('phase') != 'APPROVAL' or not self.state.get('approval_sent'):
            raise ValueError('No delivered approval request is pending.')
        if self.state.get('expires') is not None and time.time() > self.state['expires']:
            self.state['phase'] = 'EXPIRED'
            self.save()
            raise ValueError('Approval expired; monitoring continues. Mention this account and send /resume '+task_code(self.state)+' to refresh the remaining work. No old approval will be reused.')
        if body.strip().upper() == 'NO '+self.state['code']:
            self.state['phase'] = 'CANCELLED'
            self.save()
            return False
        if body.strip().upper() != 'OK '+self.state['code']:
            raise ValueError('Use the exact current approval code.')
        if digest(self.state['selected']) != self.state['manifest_hash']:
            raise ValueError('Approved plan integrity check failed.')
        self.state['phase'] = 'APPROVED'
        self.save()
        return True

    def before_save(self, operation):
        if self.state.get('phase') != 'APPROVED':
            raise ValueError('A save requires a fresh approved manifest.')
        if digest(self.state['selected']) != self.state['manifest_hash']:
            raise ValueError('Manifest changed after approval.')
        index = len(self.state['results'])
        if operation != self.state['selected'][index]:
            raise ValueError('Save is not the next approved operation.')
        self.state.update(phase='EXECUTING', write_started=operation['id'])
        self.state.pop('preservation_started',None)
        self.save()

    def before_preservation_save(self, operation):
        """One bounded settings-preservation write inside the approved move."""
        if (self.state.get('phase')!='EXECUTING' or operation.get('capability')!='move_module' or
            digest(self.state['selected'])!=self.state.get('manifest_hash') or
            operation!=self.state['selected'][len(self.state['results'])] or
            self.state.get('write_started')!=operation['id'] or self.state.get('preservation_started')):
            raise ValueError('Visibility preservation requires the current executing move; no retry is authorized.')
        self.state['preservation_started']=operation['id'];self.save()

    def receipt(self, result):
        if self.state.get('phase') != 'EXECUTING' or result.get('verified') is not True:
            raise ValueError('Only verified writes may complete an executing operation.')
        self.state['results'].append(result)
        self.state['phase'] = ('INTERRUPTED' if self.state.get('requires_goal_verification') else 'DONE') if len(self.state['results']) == len(self.state['selected']) else 'APPROVED'
        self.save()

    def fail(self, reason):
        self.state.update(phase='NEEDS_READBACK' if self.state.get('phase')=='EXECUTING' else 'FAILED',
                          reason=str(reason))
        self.save()


def scope_message(state):
    lines = ['Reply with related item numbers, "all", or "requested only".',
             'Related changes found', 'Task ID: '+task_code(state), '', 'Requested:']
    for op in state['candidates']:
        if op['scope']=='requested': lines.append('- '+op['summary'])
    lines += ['', 'Optional related changes:']
    for i,op in enumerate(state['candidates'],1):
        if op['scope']=='related': lines.append(f"{i}. {op['summary']} — {op.get('reason','')}")
    lines += ['', 'You can also describe the scope in your own words. Saving requires a separate OK approval.']
    return '\n'.join(lines)


def display_value(patch, value):
    if patch.get('key')=='id_availabilityconditionsjson':
        from datetime import datetime
        from zoneinfo import ZoneInfo
        def describe(node):
            if node.get('type')=='date' and node.get('d') in {'>=','<'} and isinstance(node.get('t'),(int,float)):
                when=datetime.fromtimestamp(node['t'],ZoneInfo('Asia/Hong_Kong')).strftime('%Y-%m-%d %H:%M:%S HKT')
                return ('available from ' if node['d']=='>=' else 'available before ')+when
            if node.get('type') in {'group','grouping'}:
                return node['type']+' '+str(node.get('id','any'))
            if node.get('op') in {'&','|','!&','!|'} and isinstance(node.get('c'),list):
                parts=[describe(child) for child in node['c']]
                joined=(' and ' if node['op'].endswith('&') else ' or ').join('('+p+')' for p in parts) or 'no access restrictions'
                visibility='; hidden when unmet' if node.get('show') is False or any(v is False for v in node.get('showc',[])) else ''
                return ('not ('+joined+')' if node['op'].startswith('!') else joined)+visibility
            return 'unchanged '+str(node.get('type','other'))+' condition'
        try:return describe(json.loads(value)) if value else 'no access restrictions'
        except (TypeError,json.JSONDecodeError,AttributeError):
            raise ValueError('Access conditions could not be rendered for approval.') from None
    if patch['type'].startswith('select'):
        labels = {o['value']:o['label'] for o in patch['options']}
        return ', '.join(labels.get(v,v) for v in (value if isinstance(value,list) else [value]))
    if isinstance(value,bool): return 'enabled' if value else 'disabled'
    return str(value)


def approval_message(state):
    from chrome_course_scope import course
    from chrome_form_dates import review
    ops = state['selected']
    lines = ['Reply: OK '+state['code']+' to confirm; NO '+state['code']+' to cancel.',
             'Moodle approval', 'Task ID: '+task_code(state), '', 'Plan:']
    section_names={}
    for index,op in enumerate(ops,1):
        lines.append(f"{index}. {course(op['course_id']).label} — {op['title']}")
        lines.append('  '+op['summary'])
        if op.get('schedule_citation'):
            cite=op['schedule_citation']
            label='Syllabus: '
            if cite.get('placement_basis')=='requester':
                lines.append('  Placement: Your explicit instruction; the syllabus confirms the destination week only.')
                label='Destination week reference: '
            lines.append('  '+label+cite['source']+', Week '+str(cite['week']))
        if op.get('placement_review'):
            place=op['placement_review'];p=op.get('params',{})
            source=next((k for k,items in op.get('layout_snapshot',{}).items() if op['target'] in items),'')
            before=section_names.get(source,place['from']);after=section_names.get(p.get('target_section'),place['to'])
            lines.append('  Section: '+before+' → '+after+'; before: '+place['before'])
        if op.get('file_review'):
            file=op['file_review']
            lines.append('  File: '+file['source']+' → '+file['filename']+' ('+file['effect']+')')
        date_rows=review(op.get('date_fields',[]),op.get('fields',[]))
        for row in date_rows:
            lines.append('  '+row['label']+': '+row['before']+' → '+row['after']+' (Moodle local time)')
        date_keys={f['key'] for f in op.get('date_fields',[])} if date_rows else set()
        for patch in op.get('fields',[]):
            if patch['key'] in date_keys:continue
            lines.append(f"  {patch['label']}: {display_value(patch,patch['before'])} → {display_value(patch,patch['value'])}")
        if op.get('target_kind')=='section':
            for patch in op.get('fields',[]):
                if patch['key']=='id_name':section_names[op['target']]=patch['value']
    return '\n'.join(lines)
