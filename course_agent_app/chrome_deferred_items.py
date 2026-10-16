"""Terminal, explicit partial outcomes; deferred work never grants write authority."""
import re
from chrome_task_memory import task_code


def items(values):
    return list(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def report_items(state, targets):
    """Group presentation by observed identity; retain every original finding.

    Planner explanations, title-prefixed review reasons and raw contract reasons
    can describe the same activity. Only exact contract-reason matches or an
    unambiguous observed course/title header bind prose to an activity. Never
    merge same-named activities across courses or guess from a substring.
    """
    from chrome_course_scope import course
    normalize=lambda value:re.sub(r'\s+',' ',value).strip().casefold()
    known={}
    for key,target in targets.items():
        if target.get('kind')!='module':continue
        cid=target.get('course_id','');title=target.get('title','')
        try:label=course(cid).label
        except ValueError:label=cid
        aliases=[title]
        short,sep,suffix=title.rpartition('  ')
        if sep and suffix in {'File','URL','Assignment','Quiz','Folder','Choice','Group choice','Peer Assessment'}:
            aliases.append(short)
        known[key]={'course':label,'title':aliases[-1],'aliases':aliases}
    rows=state.get('schedule_contract',{}).get('items',[])
    reasons={}
    for row in rows:
        if row.get('disposition')=='unresolved' and row.get('target') in known and row.get('reason'):
            reasons.setdefault(row['reason'].strip(),[]).append(row['target'])
    grouped={}
    for value in items(state['goal_unresolved']):
        matched=[]
        for reason,keys in reasons.items():
            if value==reason:
                matched=keys;break
            exact=[k for k in keys if value==targets[k].get('title','')+': '+reason]
            if exact:
                matched=exact;break
        if not matched:
            text=normalize(value)
            for key,target in known.items():
                for alias in target['aliases']:
                    prefix=normalize(target['course']+' '+alias)
                    if any(text.startswith(prefix+end) for end in (' — ',': ',' in ')):
                        matched.append(key);break
            if len(matched)!=1:matched=[]
        if not matched:
            # Unknown or ambiguous identity remains a distinct, visible issue.
            matched=['unbound:'+value]
        for key in matched:
            entry=grouped.setdefault(key,{'target':key if key in known else '',
                'course':known.get(key,{}).get('course',''),'title':known.get(key,{}).get('title',''),
                'details':[]})
            if value not in entry['details']:entry['details'].append(value)
    result=[]
    for entry in grouped.values():
        # Preserve distinct full findings: a later sentence may qualify a date
        # or contain the actual missing decision. Never truncate by sentence.
        summaries=[]
        for text in entry['details']:
            if entry['target']:
                for delimiter in (' — ',': '):
                    head,sep,body=text.partition(delimiter)
                    headers={normalize(prefix+alias) for alias in known[entry['target']]['aliases']
                             for prefix in ('',entry['course']+' ')}
                    if sep and normalize(head) in headers:
                        text=body;break
            if text not in summaries:summaries.append(text)
        prefix=entry['course']+' — '+entry['title']+': ' if entry['target'] else ''
        result.append(entry|{'summary':prefix+' '.join(summaries)})
    return result


def report(state):
    completed = sum(r.get('verified') is True for r in state.get('verified_previous_results', []) + state.get('results', []))
    lines = ['Status: Partial', 'Task ID: ' + task_code(state),
             'Verified operations: ' + str(completed) + '. Unresolved items were skipped.', '', 'Not completed:']
    values=[r['summary'] for r in state.get('deferred_report_items',[])] or state['goal_unresolved']
    lines.append('\n\n'.join(str(i) + '. ' + value for i, value in enumerate(values, 1)))
    lines.extend(['', 'Next: Mention this account and start a new task for each item above. No automatic retry.'])
    return '\n'.join(lines)


def close(flow, unresolved, *, targets=None):
    state = flow.state
    if state.get('pending_delivery'):
        raise ValueError('Reconcile the pending message before partial closure.')
    if state.get('phase') not in {'PLANNING', 'QUESTION', 'SCOPE', 'INTERRUPTED', 'DONE'}:
        raise ValueError('An approval or possible save cannot be closed as deferred work.')
    selected = state.get('selected', [])
    results = state.get('results', [])
    if len(results) != len(selected) or any(r.get('verified') is not True for r in results):
        raise ValueError('Selected operations require execution/readback before partial closure.')
    if 'write_started' in state and (not state['write_started'] or not any(o.get('id') == state['write_started'] for o in selected)):
        raise ValueError('Unconfirmed save cannot be deferred.')
    unresolved = items(unresolved)
    if not unresolved:
        raise ValueError('Partial closure requires specific unresolved items.')
    state.update(phase='PARTIAL', goal_verified=False, goal_unresolved=unresolved,
                 deferred_items=unresolved, deferred_closed=True)
    if targets is not None:
        state['deferred_report_items']=report_items(state,targets)
    for key in ('pending_question', 'resume_phase', 'reply_boundary'):
        state.pop(key, None)
    flow.save()
    return report(state)
