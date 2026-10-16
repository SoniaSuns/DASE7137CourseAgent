"""Moodle date widgets and assignment form invariants, not staff-intent routing.

Rules follow Moodle mod/assign/mod_form.php::validation (MOODLE_405_STABLE).
Choosing alternative dates remains the planner's responsibility.
"""
from datetime import datetime
import re

LABELS={'allowsubmissionsfromdate':'Submission opening','duedate':'Due date',
        'cutoffdate':'Cut-off date','gradingduedate':'Grading reminder','fromdate':'Opening',
        'opendate':'Opening','closedate':'Closing','completionexpected':'Expected completion'}


class MoodleDateConflict(ValueError):pass


def date_fields(fields):
    return [{k:f[k] for k in ('key','name','type','value')} for f in fields
            if re.fullmatch(r'.+\[(enabled|year|month|day|hour|minute)\]',f.get('name',''))]


def dates(fields,patches=()):
    edits={p['key']:p['value'] for p in patches};groups={}
    for field in fields:
        match=re.fullmatch(r'(.+)\[(enabled|year|month|day|hour|minute)\]',field.get('name',''))
        if match:groups.setdefault(match[1],{})[match[2]]=edits.get(field['key'],field['value'])
    result={}
    for name,parts in groups.items():
        if not all(k in parts for k in ('year','month','day','hour','minute')):continue
        if parts.get('enabled') is False:result[name]=None;continue
        try:result[name]=datetime(*(int(parts[k]) for k in ('year','month','day','hour','minute')))
        except (ValueError,TypeError):raise MoodleDateConflict(LABELS.get(name,name)+' is not a valid calendar date.') from None
    return result


def describe(value):return value.strftime('%Y-%m-%d %H:%M') if value else 'disabled'


def interval_evidence(fields):
    """Observed local-calendar relationships only; no anchor choice or new dates."""
    observed=dates(fields)
    active=[(name,value) for name,value in observed.items() if value is not None]
    return {'dates':{name:{'label':LABELS.get(name,name),'value':describe(value),'enabled':value is not None}
                     for name,value in observed.items()},
            'intervals':[{'from':left,'to':right,'local_wall_clock_minutes':int((b-a).total_seconds()/60)}
                         for index,(left,a) in enumerate(active) for right,b in active[index+1:]],
            'basis':'current observed form, local calendar time; disabled dates excluded'}


def review(fields,patches):
    old,new=dates(fields),dates(fields,patches)
    return [{'label':LABELS.get(name,name),'before':describe(old[name]),'after':describe(value)}
            for name,value in new.items() if old.get(name)!=value]


def validate(fields,patches,title='Selected activity'):
    if not any(p['key'] in {f['key'] for f in date_fields(fields)} for p in patches):return
    values=dates(fields,patches)
    if 'allowsubmissionsfromdate' not in values:return
    rules=[('allowsubmissionsfromdate','duedate',True),('duedate','cutoffdate',False),
           ('allowsubmissionsfromdate','cutoffdate',False),('allowsubmissionsfromdate','gradingduedate',False),
           ('duedate','gradingduedate',False)]
    for left,right,strict in rules:
        a,b=values.get(left),values.get(right)
        if a and b and (a>=b if strict else a>b):
            raise MoodleDateConflict(title+': '+LABELS[right]+' ('+describe(b)+') must be '+
                ('later than ' if strict else 'at or after ')+LABELS[left]+' ('+describe(a)+
                '). Re-plan the related dates or ask the requester; no automatic date adjustment.')
