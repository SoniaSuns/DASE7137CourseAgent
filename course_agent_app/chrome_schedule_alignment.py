"""Source-row grounding and whole-course coverage for schedule-based placement.

Code checks identities, citations, week values and coverage. A separate model
review checks the semantic match and source/class selection, never authorizes a save.
Requester exceptions retain the syllabus destination coordinate but separately
cite an exact current-task instruction for the material's placement.
"""
import copy
import re


class AlignmentReviewRequired(ValueError):
    """The current batch touches an unresolved semantic/scope decision."""


def week(title):
    match=re.fullmatch(r'Week\s+(\d+)(?:\s*[:(\-].*)?',title.strip(),re.I)
    if not match:raise ValueError('The destination has no unambiguous observed Week label; inspect or rename it first.')
    return int(match[1])


def requester_placement(row):
    return row.get('disposition')=='place' and row.get('placement_basis','syllabus')=='requester'


def requester_decision(row):
    return row.get('disposition') in {'place','unchanged'} and row.get('placement_basis')=='requester'


def instruction_reference(row):
    index=row.get('instruction_index')
    text=row.get('instruction_text')
    if type(index) is not int or index < -1 or not isinstance(text,str) or not text.strip():
        raise ValueError('A requester placement needs an exact current-task instruction index and complete text.')
    return index,text


def check_instruction(row,request,conversation):
    """Check attribution only; meaning and supersession belong to independent review."""
    index,text=instruction_reference(row)
    if index==-1:
        actual=request
    elif isinstance(conversation,list) and index<len(conversation):
        actual=conversation[index]
    else:
        actual=None
    if not isinstance(actual,str) or text!=actual:
        raise ValueError('The requester placement citation does not match its exact current-task instruction.')


def requester_review_snapshot(row):
    # This records what the separate reviewer checked, never write authorization.
    return {key:copy.deepcopy(row.get(key)) for key in (
        'target','target_section','file_id','row_id','week_column','expected_week',
        'placement_basis','instruction_index','instruction_text','visibility')}


def unchanged_review_snapshot(row):
    # Preservation is a reviewed location decision, not a syllabus match or save.
    return {key:copy.deepcopy(row.get(key)) for key in (
        'target','disposition','target_section','file_id','row_id','week_column',
        'placement_basis','instruction_index','instruction_text','visibility','reason')}


def check_unchanged(row,browser):
    target=browser.targets.get(row.get('target'),{})
    section=browser.targets.get(row.get('target_section'),{})
    if (target.get('kind')!='module' or section.get('kind')!='section' or
            not target.get('course_id') or section.get('course_id')!=target['course_id'] or
            not row.get('target_section') or target.get('section_key')!=row['target_section']):
        raise ValueError('An unchanged placement must retain its exact observed section in the same course.')
    if (row.get('file_id','')!='' or row.get('row_id','')!='' or
            type(row.get('week_column')) is not int or row['week_column']!=0 or
            'expected_week' in row or row.get('visibility')!='preserve'):
        raise ValueError('An unchanged placement cannot carry a syllabus destination or visibility change.')
    if requester_decision(row):
        try:instruction_reference(row)
        except ValueError as exc:
            raise ValueError('An unchanged placement cannot carry an incomplete requester citation.') from exc
    elif (row.get('placement_basis','syllabus')!='syllabus' or
          type(row.get('instruction_index',-1)) is not int or row.get('instruction_index',-1)!=-1 or
          row.get('instruction_text','')!=''):
        raise ValueError('An unchanged placement cannot carry an unbound requester citation.')


def reviewed_checks(result,contract):
    """Generic acceptance alone cannot validate a requester-directed exception."""
    checks=copy.deepcopy(result.get('items',[]))
    rows={r['target']:r for r in contract['items']}
    for check in checks:
        row=rows.get(check.get('target'),{})
        if (requester_decision(row) and check.get('accepted') is True and
                check.get('requester_instruction_verified') is not True):
            check.update(accepted=False,reason='Independent review did not verify the requester placement instruction.',
                         blocker_type='plan_error',question='')
    return checks


def clarification_questions(result,contract,browser):
    """Only explicit independent-review questions can be sent to the requester.

    No inference from reason prose, missing classification or the first N rejects.
    This never changes an acceptance verdict or authorizes a write.
    """
    checks=reviewed_checks(result,contract)
    wanted={r['target'] for r in contract['items']}
    if len(checks)!=len(wanted) or {r.get('target') for r in checks}!=wanted:return []
    questions=[]
    for check in checks:
        if check.get('accepted') is not False or check.get('blocker_type')!='user_decision':continue
        question=check.get('question')
        if not isinstance(question,str) or not question.strip():continue
        title=browser.targets.get(check['target'],{}).get('title')
        if not title:continue
        text=title+': '+question.strip()
        if text not in questions:questions.append(text)
    return questions


def record_requester_reviews(contract,checks):
    revised=copy.deepcopy(contract)
    accepted={r['target'] for r in checks if r.get('accepted') is True and
              r.get('requester_instruction_verified') is True}
    preserved={r['target'] for r in checks if r.get('accepted') is True}
    for row in revised['items']:
        row.pop('reviewed_requester_placement',None)
        row.pop('reviewed_unchanged_placement',None)
        if requester_placement(row) and row['target'] in accepted:
            row['reviewed_requester_placement']=requester_review_snapshot(row)
        if (row.get('disposition')=='unchanged' and row['target'] in preserved and
                (not requester_decision(row) or row['target'] in accepted)):
            row['reviewed_unchanged_placement']=unchanged_review_snapshot(row)
    return revised


def clear_placement(row,reason):
    row.update(disposition='unresolved',reason=reason,target_section='',row_id='',file_id='',week_column=0,visibility='preserve')
    row.pop('expected_week',None)
    row.pop('reviewed_requester_placement',None)
    row.pop('reviewed_unchanged_placement',None)
    if 'placement_basis' in row:row['placement_basis']='syllabus'
    if 'instruction_index' in row:row['instruction_index']=-1
    if 'instruction_text' in row:row['instruction_text']=''


def normalize_bindings(alignment,evidence):
    """Separate supplemental reads from an unambiguous cited schedule source.

    This does not select between competing syllabuses or infer authority from a
    filename. All placement citations must already use the sole structured
    source. Unread, conflicting, or cross-course bindings remain validation errors.
    """
    revised=copy.deepcopy(alignment);notes=[]
    if not revised.get('required'):return revised,notes
    bindings=revised.get('sources',[]);courses=revised.get('course_ids',[])
    if any(b.get('course_id') not in courses for b in bindings):return revised,notes
    normalized=[]
    for cid in courses:
        group=list({(b['course_id'],b['file_id']):b for b in bindings if b['course_id']==cid}.values())
        if len(group)<=1:
            normalized.extend(group);continue
        reads={b['file_id']:[e for e in evidence if e['file_id']==b['file_id'] and e.get('status')=='read'] for b in group}
        structured=[b for b in group if len(reads[b['file_id']])==1 and reads[b['file_id']][0].get('table_rows')]
        # The source set is per course; target identities are validated later.
        cited={r.get('file_id') for r in revised.get('items',[]) if r.get('disposition')=='place'
               and r.get('file_id') in reads}
        if (len(structured)!=1 or cited!={structured[0]['file_id']} or
                any(len(rows)!=1 for rows in reads.values())):
            normalized.extend(group);continue
        primary=structured[0];normalized.append(primary)
        notes.append({'course_id':cid,'authoritative_file_id':primary['file_id'],
                      'supplemental_file_ids':[b['file_id'] for b in group if b!=primary]})
    revised['sources']=normalized
    return revised,notes


def inherit_citation_sources(alignment,browser,evidence):
    """Compile omitted per-item source IDs from the existing course binding.

    Only an empty field may inherit. The exact observed target's course must
    have one binding, read structured evidence, and the exact cited row.
    Explicit conflicting citations are never overwritten.
    """
    revised=copy.deepcopy(alignment);notes=[]
    if not revised.get('required'):return revised,notes
    for item in revised.get('items',[]):
        if item.get('disposition')!='place' or item.get('file_id') not in ('',None):continue
        target=browser.targets.get(item.get('target'),{})
        cid=target.get('course_id')
        if target.get('kind')!='module' or cid not in revised.get('course_ids',[]):continue
        bindings=[b for b in revised.get('sources',[]) if b.get('course_id')==cid]
        if len(bindings)!=1:continue
        fid=bindings[0].get('file_id')
        reads=[e for e in evidence if e['file_id']==fid and e.get('status')=='read' and e.get('table_rows')]
        if len(reads)!=1:continue
        if len([r for r in reads[0]['table_rows'] if r['row_id']==item.get('row_id')])!=1:continue
        item['file_id']=fid
        notes.append({'target':item['target'],'course_id':cid,'file_id':fid,'row_id':item['row_id']})
    return revised,notes


def resolve_missing_destinations(alignment,operations,browser,evidence):
    """Compile unknown same-course section IDs from exact source Week cells.

    Never change module identity, an existing conflicting destination, or an
    explicit cross-course reference. Ambiguous observed weeks remain errors.
    """
    revised=copy.deepcopy(alignment);ops=copy.deepcopy(operations);notes=[]
    if not revised.get('required'):return revised,ops,notes
    for item in revised.get('items',[]):
        old=item.get('target_section','')
        if item.get('disposition')!='place' or old in browser.targets:continue
        target=browser.targets.get(item.get('target'),{});cid=target.get('course_id')
        if target.get('kind')!='module' or cid not in revised.get('course_ids',[]):continue
        if old and not old.startswith(cid+':section:'):continue
        bindings=[b for b in revised.get('sources',[]) if b.get('course_id')==cid]
        if len(bindings)!=1 or bindings[0]['file_id']!=item.get('file_id'):continue
        reads=[e for e in evidence if e['file_id']==item['file_id'] and e.get('status')=='read' and e.get('table_rows')]
        if len(reads)!=1:continue
        rows=reads[0]['table_rows'];cited=[r for r in rows if r['row_id']==item.get('row_id')]
        if len(cited)!=1:continue
        table=item['row_id'].split(':r')[0]
        table_rows=[r for r in rows if r['row_id'].split(':r')[0]==table]
        columns=[i for i,cell in enumerate(table_rows[0]['cells']) if cell.strip().casefold()=='week']
        if len(columns)!=1 or columns[0]>=len(cited[0]['cells']):continue
        cell=cited[0]['cells'][columns[0]].strip()
        if not cell.isdecimal():continue
        sections=[]
        for key,section in browser.targets.items():
            if section.get('kind')!='section' or section.get('course_id')!=cid:continue
            try:observed=week(section.get('title',''))
            except ValueError:continue
            if observed==int(cell):sections.append(key)
        if len(sections)!=1:continue
        new=sections[0];item['target_section']=new
        for op in ops:
            if op.get('capability')=='move_module' and op.get('target')==item['target'] and op.get('params',{}).get('target_section')==old:
                op['params']['target_section']=new
        notes.append({'target':item['target'],'previous_section':old,'section':new,'row_id':item['row_id'],'week':int(cell)})
    return revised,ops,notes


def normalize_week_columns(alignment,browser,evidence):
    """Resolve the Week column from an explicit source-table header only.

    The cited row and destination never change. Conflicting weeks, ambiguous
    headers, and missing evidence remain errors for the strict validator.
    """
    revised=copy.deepcopy(alignment);notes=[]
    for item in revised.get('items',[]):
        if item.get('disposition')!='place':continue
        reads=[e for e in evidence if e['file_id']==item.get('file_id') and e.get('status')=='read' and e.get('table_rows')]
        if len(reads)!=1:continue
        rows=reads[0]['table_rows'];cited=[r for r in rows if r['row_id']==item.get('row_id')]
        if len(cited)!=1:continue
        table=item['row_id'].split(':r')[0]
        table_rows=[r for r in rows if r['row_id'].split(':r')[0]==table]
        if not table_rows:continue
        columns=[i for i,cell in enumerate(table_rows[0]['cells']) if cell.strip().casefold()=='week']
        if len(columns)!=1 or columns[0]>=len(cited[0]['cells']):continue
        col=columns[0];cell=cited[0]['cells'][col].strip()
        try:destination_week=week(browser.targets.get(item.get('target_section'),{}).get('title',''))
        except ValueError:continue
        if not cell.isdecimal() or int(cell)!=destination_week or item.get('week_column')==col:continue
        notes.append({'target':item['target'],'row_id':item['row_id'],'previous_column':item.get('week_column'),'week_column':col})
        item['week_column']=col
    return revised,notes


def validate(alignment,operations,browser,evidence,*,required=False):
    if not alignment or not alignment.get('required'):
        if required:raise ValueError('This goal requires a complete source-grounded schedule alignment; history cannot replace the syllabus.')
        return None
    alignment=copy.deepcopy(alignment)
    bindings=alignment.get('sources',[])
    courses=alignment.get('course_ids',[])
    if not courses or len(set(courses))!=len(courses):raise ValueError('Schedule review requires exact distinct course IDs.')
    sources={}
    for binding in bindings:
        cid=binding['course_id']
        if cid not in courses or cid in sources:raise ValueError('Bind exactly one authoritative inspected syllabus per course.')
        matches=[e for e in evidence if e['file_id']==binding['file_id'] and e.get('status')=='read' and e.get('table_rows')]
        if len(matches)!=1:raise ValueError('Read the authoritative syllabus with structured table rows before proposing schedule changes.')
        sources[cid]=matches[0]
    if set(sources)!=set(courses):raise ValueError('Every reviewed course needs its own authoritative syllabus binding.')
    expected={k for k,t in browser.targets.items() if t.get('kind')=='module' and t.get('course_id') in courses}
    rows=alignment.get('items',[]);keys=[r['target'] for r in rows]
    if len(set(keys))!=len(keys) or set(keys)!=expected:
        raise ValueError('Alignment must account for every current activity in the reviewed courses, including unchanged, out-of-scope and unresolved items. Missing: '+', '.join(sorted(expected-set(keys)))[:1200])
    for row in rows:
        target=browser.targets[row['target']];cid=target['course_id']
        if row['disposition'] not in {'place','unchanged','out_of_scope','unresolved'} or not row.get('reason','').strip():
            raise ValueError('Each material needs an explicit disposition and justification.')
        if row['disposition']=='unchanged':
            check_unchanged(row,browser)
            continue
        if row['disposition']!='place':continue
        basis=row.get('placement_basis','syllabus')
        if basis not in {'syllabus','requester'}:raise ValueError('Unknown placement basis.')
        if basis=='requester':
            instruction_reference(row)
        elif row.get('instruction_text','')!='' or row.get('instruction_index',-1)!=-1:
            raise ValueError('A syllabus placement cannot carry a requester exception citation.')
        dest=browser.targets.get(row['target_section'],{})
        if dest.get('kind')!='section' or dest.get('course_id')!=cid:raise ValueError('A schedule destination must be an exact section in the same course.')
        source=sources[cid]
        if not row.get('file_id'):
            raise ValueError('Missing syllabus citation for '+target['title']+'. Check the course source binding and exact source row; this is not evidence of a cross-course citation.')
        if row['file_id']!=source['file_id']:
            raise ValueError('The explicit source citation for '+target['title']+' differs from its course-bound syllabus. Review the source identity; no substitution was made.')
        matches=[r for r in source['table_rows'] if r['row_id']==row['row_id']]
        col=row['week_column']
        if len(matches)!=1 or type(col) is not int or not 0<=col<len(matches[0]['cells']):
            raise ValueError('Use an exact supplied syllabus row and week column.')
        cell=matches[0]['cells'][col].strip()
        if not cell.isdecimal() or int(cell)!=week(dest['title']):
            raise ValueError('Syllabus row week and proposed destination differ for '+target['title']+'.')
        row['expected_week']=int(cell)
        if row['visibility'] not in {'preserve','shown','hidden'}:raise ValueError('Unknown requested visibility outcome.')
    by_target={r['target']:r for r in rows}
    for op in operations:
        if op['capability']!='move_module':continue
        row=by_target.get(op['target'],{})
        if row.get('disposition')!='place' or row.get('target_section')!=op['params']['target_section']:
            raise ValueError('Every move must match the reviewed source-grounded placement; no uncited move is allowed.')
    return copy.deepcopy(alignment)


def review_context(contract,browser,evidence,request,conversation):
    # Supply explicit, mechanically checked coordinates. Raw edit forms also
    # contain the CURRENT section and previously confused it with the proposal.
    # The semantic reviewer needs topic content, not another interpretation of
    # numeric section IDs or unrelated editable controls.
    aligned=copy.deepcopy(contract)
    targets=getattr(browser,'targets',{})
    if not targets:
        targets={t['key']:t for c in browser.overviews.values() for t in c.get('modules',[])+c.get('sections',[])}
    forms=getattr(browser,'forms',{})
    for row in aligned['items']:
        # A fresh review cannot inherit the prior review's conclusion.
        row.pop('reviewed_requester_placement',None)
        row.pop('reviewed_unchanged_placement',None)
        if row['disposition'] not in {'place','unchanged'}:
            # Non-placement rows carry no proposed action, even if the model
            # left irrelevant schema fields populated. Never show a ghost move.
            row.update(target_section='',file_id='',row_id='',week_column=0,visibility='preserve')
            row.pop('expected_week',None)
            if 'placement_basis' in row:row['placement_basis']='syllabus'
            if 'instruction_index' in row:row['instruction_index']=-1
            if 'instruction_text' in row:row['instruction_text']=''
        target=targets[row['target']]
        row['material_title']=target['title']
        row['current_section_title']=target.get('section') or targets.get(target.get('section_key'),{}).get('title','')
        row['proposed_section_title']=targets.get(row.get('target_section'),{}).get('title','')
        row['observed_content']={'inventory_text':target.get('text',''),
            'resource_files':copy.deepcopy(forms.get(row['target'],{}).get('resource_files',[])),
            'text_fields':[{'label':f.get('label',''),'value':f['value']}
                           for f in forms.get(row['target'],{}).get('fields',[])
                           if f.get('type') in {'text','textarea','url'} and f.get('value')]}
        if requester_decision(row):check_instruction(row,request,conversation)
        if row['disposition']=='unchanged':
            row['review_requirements']={'decision':'preserve_current_location','syllabus_week_required':False,
                'requester_instruction_required':requester_decision(row)}
        if row['disposition']=='place':
            source=next(e for e in evidence if e['file_id']==row['file_id'] and e.get('table_rows'))
            row['cited_row_cells']=next(r['cells'] for r in source['table_rows'] if r['row_id']==row['row_id'])
            row['cited_row_role']='destination_week_only' if requester_placement(row) else 'material_topic_and_week'
    return {'review_kind':'schedule_alignment','request':request,'conversation':conversation,
            'alignment':aligned,'courses':[{'course_id':c['course_id'],'label':c['label']}
                                          for c in browser.overviews.values() if c['course_id'] in contract['course_ids']],
            'coordinate_validation':'For place rows code has validated source row week == proposed section week. Unchanged rows retain the exact observed current section without claiming syllabus-week agreement; independently verify the preservation decision against current scope and refinements. current_section_title is the BEFORE state; proposed_section_title is the intended AFTER state. For syllabus placements judge the topic-to-source match. For requester exceptions the row grounds only the destination week: code checked the exact current-task instruction citation, and independent review must verify its meaning, material identity, scope, visibility and continued applicability.',
            'sources':[{k:v for k,v in e.items() if k not in {'sha256'}} for e in evidence]}


def check_review(result,contract):
    checks=reviewed_checks(result,contract)
    wanted={r['target'] for r in contract['items']}
    if ({r.get('target') for r in checks}!=wanted or len(checks)!=len(wanted) or
        result.get('accepted') is not True or any(r.get('accepted') is not True for r in checks)):
        reasons=[r.get('reason','') for r in checks if r.get('accepted') is not True]
        raise ValueError('Independent syllabus review rejected the mapping: '+'; '.join(reasons+result.get('findings',[]))[:2200])


def apply_review(result,contract,operations,browser,*,allow_empty=False):
    """Hold uncertain materials without blocking an independent grounded batch.

No rejected item is written or declared aligned. A section action must also be
independent of rejected descendants and have an accepted placement purpose.
"""
    checks=reviewed_checks(result,contract);wanted={r['target'] for r in contract['items']}
    if {r.get('target') for r in checks}!=wanted or len(checks)!=len(wanted):
        raise AlignmentReviewRequired('Independent review did not account for every target; rebuild its evidence.')
    rejected={r['target']:r.get('reason','Unresolved source mapping') for r in checks if r.get('accepted') is not True}
    if not rejected:
        if result.get('accepted') is not True:
            raise AlignmentReviewRequired('Independent review reported an unresolved overall concern: '+'; '.join(result.get('findings',[]))[:1600])
        return record_requester_reviews(contract,checks),[]
    affected=set()
    for op in operations:
        if op['capability']=='set_section_visibility':
            affected.update(k for k,t in browser.targets.items() if t.get('kind')=='module' and t.get('section_key')==op['target'])
            if not any(r['target'] not in rejected and r['disposition']=='place' and r['target_section']==op['target'] for r in contract['items']):
                raise AlignmentReviewRequired('The section prerequisite has no independently accepted placement purpose.')
        else:affected.add(op['target'])
    if (not operations and not allow_empty) or affected.intersection(rejected):
        raise AlignmentReviewRequired('These proposed changes need source/scope review before a write: '+
                                      '; '.join(rejected.values())[:2200])
    revised=record_requester_reviews(contract,checks);notes=[]
    for row in revised['items']:
        if row['target'] not in rejected:continue
        title=browser.targets[row['target']]['title']
        reason=title+': '+rejected[row['target']]
        clear_placement(row,reason);notes.append(reason)
    return revised,notes


def partition_review(result,contract,operations,browser,*,allow_empty=False):
    """Defer rejected writes and all dependent writes before offering approval.

    Never splice dependencies or substitute ordering anchors. The full coverage
    contract keeps deferred targets unresolved for the next goal review.
    """
    checks=reviewed_checks(result,contract);wanted={r['target'] for r in contract['items']}
    if {r.get('target') for r in checks}!=wanted or len(checks)!=len(wanted):
        raise AlignmentReviewRequired('Independent review did not account for every target; rebuild its evidence.')
    rejected={r['target'] for r in checks if r.get('accepted') is not True}
    deferred=set()
    for op in operations:
        affected={op['target']}
        if op['capability']=='set_section_visibility':
            affected.update(k for k,t in browser.targets.items() if t.get('section_key')==op['target'])
            if not any(r['target'] not in rejected and r['disposition']=='place' and r['target_section']==op['target'] for r in contract['items']):
                deferred.add(op['id'])
        if affected & rejected:deferred.add(op['id'])
    while True:
        targets=rejected|{op['target'] for op in operations if op['id'] in deferred}
        expanded=deferred|{op['id'] for op in operations
            if set(op.get('depends_on',[])) & deferred or
            op.get('params',{}).get('before_module') in targets}
        if expanded==deferred:break
        deferred=expanded
    safe=[copy.deepcopy(op) for op in operations if op['id'] not in deferred]
    revised,notes=apply_review(result,contract,safe,browser,allow_empty=allow_empty)
    held_targets={op['target'] for op in operations if op['id'] in deferred}
    for row in revised['items']:
        if row['target'] in held_targets and row['target'] not in rejected:
            reason=browser.targets[row['target']]['title']+': waiting for a deferred prerequisite or ordering anchor.'
            clear_placement(row,reason);notes.append(reason)
    return revised,safe,notes


def verify_current(contract,browser):
    """Fresh complete readback, not just the last selected write manifest."""
    for cid in contract['course_ids']:browser.read_layout(cid)
    problems=[]
    now={k for k,t in browser.targets.items() if t.get('kind')=='module' and t.get('course_id') in contract['course_ids']}
    if now!={r['target'] for r in contract['items']}:problems.append('Course inventory changed; rebuild full coverage.')
    for row in contract['items']:
        if row['disposition']=='unresolved':problems.append(row['reason']);continue
        if row['disposition']=='unchanged':
            title=browser.targets.get(row['target'],{}).get('title',row['target'])
            if row.get('reviewed_unchanged_placement')!=unchanged_review_snapshot(row):
                problems.append(title+': unchanged placement needs independent review');continue
            try:check_unchanged(row,browser)
            except ValueError:
                problems.append(title+': unchanged placement no longer matches current evidence')
            continue
        if row['disposition']!='place':continue
        target=browser.targets.get(row['target'],{})
        if requester_placement(row) and row.get('reviewed_requester_placement')!=requester_review_snapshot(row):
            problems.append(target.get('title',row['target'])+': requester placement needs independent review');continue
        if target.get('section_key')!=row['target_section']:
            suffix=': not in its requester-directed section' if requester_placement(row) else ': not in its syllabus section'
            problems.append(target.get('title',row['target'])+suffix);continue
        if 'expected_week' in row:
            try:observed_week=week(browser.targets.get(row['target_section'],{}).get('title',''))
            except ValueError:observed_week=None
            if observed_week!=row['expected_week']:
                problems.append(target.get('title',row['target'])+': destination week label changed');continue
        if row['visibility']=='preserve':continue
        section=browser.targets[row['target_section']]
        form=browser.inspect(row['target'])
        field=next((f for f in form['fields'] if f['key']=='id_visible'),{})
        actual=field.get('value')
        if row['visibility']=='shown' and (section.get('visibility')!='shown' or actual!='1'):
            problems.append(target['title']+': not shown on its course page')
        if row['visibility']=='hidden' and actual!='0' and section.get('visibility')!='hidden':
            problems.append(target['title']+': not hidden')
    return problems
