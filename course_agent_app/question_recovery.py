"""Preconditions for an explicitly authorized interrupted-question recovery.

This is never an automatic deployment exception or Moodle write authority.
"""
import argparse
import json
import re
from pathlib import Path

from chrome_task_memory import ACTIVE,command


def validate_question_recovery(states,queue,run,*,restarted=False):
    if not re.fullmatch(r'[a-f0-9]{12}',run):raise ValueError('Exact run ID required.')
    matches=[s for s in states if s.get('run')==run]
    if len(matches)!=1:raise ValueError('One exact question run is required.')
    state=matches[0]
    phase='INTERRUPTED' if restarted else 'QUESTION'
    pending=state.get('pending_delivery',{})
    if (state.get('phase')!=phase or state.get('resume_phase')!='PLANNING' or
        state.get('mention_authorized') is not True or not state.get('pending_question') or
        state.get('selected') or state.get('results') or state.get('approval_sent') or
        state.get('manifest_hash') or 'write_started' in state or
        pending.get('phase')!='QUESTION' or pending.get('action_required') is not True or
        pending.get('send_attempted') is not True or pending.get('is_approval') or
        pending.get('is_scope') or pending.get('requester') or
        not isinstance(pending.get('exclude_ids'),list) or
        any(not isinstance(mid,str) or not mid for mid in pending.get('exclude_ids',[])) or
        pending.get('parts')!=[pending.get('text')] or pending.get('next_part')!=0 or
        pending.get('part_receipts') or not pending.get('text')):
        raise ValueError('Recovery only supports one unapproved, unconfirmed question with no possible write.')
    if any(s is not state and (s.get('phase') in ACTIVE or s.get('pending_delivery')) for s in states):
        raise ValueError('Another task or delivery is active.')
    if not isinstance(queue,list) or len(queue)!=1:raise ValueError('Exactly one queued answer is required.')
    reply=queue[0]
    if (reply.get('chat')!=state.get('chat') or reply.get('sender')!=state.get('sender') or
        reply.get('self_mentioned') is not True or not reply.get('id') or not reply.get('text') or
        reply['id'] in state.get('message_ids',[]) or reply.get('reply_boundary') or reply.get('reply_to_run') or
        command(reply['text']) is not None or re.fullmatch(r'(?:OK|NO)\s+\d{6}',reply['text'].strip(),re.I) or
        reply['text'].strip().casefold() in {'stop agent','shutdown agent','停止','退出','停止 agent'}):
        raise ValueError('Recovery requires an authenticated same-requester answer, never an approval or command.')
    return state,reply


def validate_failed_retry(states,queue,run):
    """Match the existing simple retry guards before stopping a live listener."""
    if not re.fullmatch(r'[a-f0-9]{12}',run):raise ValueError('Exact run ID required.')
    matches=[s for s in states if s.get('run')==run]
    if len(matches)!=1 or queue:
        raise ValueError('Retry requires one exact failed run and idle intake.')
    if any(s.get('phase') in ACTIVE or s.get('pending_delivery') for s in states):
        raise ValueError('Finish active work or delivery before retrying.')
    state=matches[0]
    if (state.get('phase') not in {'FAILED','INTERRUPTED'} or state.get('mention_authorized') is not True or
        state.get('results') or 'write_started' in state or 'selected' in state or 'manifest_hash' in state or
        state.get('approval_sent') is True or not state.get('task') or not state.get('sender') or
        not state.get('message_ids')):
        raise ValueError('Fresh retry requires an authenticated failure with no offered or possible write.')
    return state


def validate_waiting_replan(states,queue,run,status):
    """Operator-only restart of an exact delivered planning question, no writes."""
    if not re.fullmatch(r'[a-f0-9]{12}',run):raise ValueError('Exact run ID required.')
    matches=[s for s in states if s.get('run')==run]
    if len(matches)!=1 or not isinstance(queue,list) or queue:
        raise ValueError('Replan requires one exact question and idle intake.')
    state=matches[0]
    if (state.get('phase')!='QUESTION' or state.get('resume_phase')!='PLANNING' or
            state.get('mention_authorized') is not True or not state.get('pending_question') or
            state.get('results') or any(k in state for k in ('selected','manifest_hash','write_started')) or
            state.get('approval_sent') is True or not state.get('task') or not state.get('sender') or
            not state.get('message_ids') or not state.get('reply_boundary')):
        raise ValueError('Only an authenticated planning question with no offered or possible write can be replanned.')
    receipts=state.get('delivery_verified_ids')
    if (not isinstance(receipts,list) or not receipts or
            any(not isinstance(r,str) or not r for r in receipts) or
            len(set(receipts))!=len(receipts) or type(state.get('delivery_parts_count')) is not int or
            state['delivery_parts_count']!=len(receipts)):
        raise ValueError('The question delivery must be verified before restart.')
    if any(s.get('pending_delivery') or (s is not state and s.get('phase') in ACTIVE) for s in states):
        raise ValueError('Another task or delivery is active.')
    phases=status.get('phases',{})
    if (status.get('queued')!=0 or status.get('pending_delivery') or
            phases.get(state.get('chat'))!='QUESTION' or
            any(chat!=state['chat'] and phase in ACTIVE for chat,phase in phases.items())):
        raise ValueError('Listener status must confirm the same idle question boundary.')
    return state


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True)
    parser.add_argument('--failed',action='store_true',help='Check simple failed-run retry instead of question recovery.')
    parser.add_argument('--waiting',action='store_true',help='Check operator-authorized replan of a delivered question.')
    parser.add_argument('--state-root',type=Path,default=Path(__file__).resolve().parent/'reports/shared_chrome/state')
    args=parser.parse_args()
    states=[json.loads(p.read_text(encoding='utf-8-sig')) for p in args.state_root.glob('*.json')
            if re.fullmatch(r'[a-f0-9]{12}',p.stem)]
    queue=json.loads((args.state_root/'inbox.json').read_text(encoding='utf-8-sig'))
    if args.failed and args.waiting:parser.error('Choose one recovery mode.')
    if args.waiting:
        status=json.loads((args.state_root.parent/'status.json').read_text(encoding='utf-8-sig'))
        validate_waiting_replan(states,queue,args.run,status)
        print('Delivered-question replan preconditions passed; fresh planning and current approval remain required.')
    elif args.failed:
        validate_failed_retry(states,queue,args.run)
        print('Failed-run retry preconditions passed; fresh planning and current approval remain required.')
    else:
        validate_question_recovery(states,queue,args.run)
        print('Unapproved question recovery preconditions passed; fresh outgoing readback remains required after restart.')


if __name__=='__main__':main()
