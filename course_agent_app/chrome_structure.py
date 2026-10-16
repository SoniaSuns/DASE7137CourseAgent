"""Exact-ID course placement, using Moodle's own authenticated course editor.

No planner-supplied URLs or scripts. A move preserves the existing module ID.
The approved manifest stays immutable; earlier verified moves derive the next
expected layout, so our own changes are not confused with external drift.
"""
import copy
from chrome_course_scope import course, course_identity


class MoveVisibilityConflict(ValueError):
    """Resolve the destination visibility before any move, with fresh approval."""


def visibility_label(field):
    matches=[o for o in field.get('options',[]) if o['value']==field.get('value')]
    return ' '.join(matches[0]['label'].split()) if len(matches)==1 else None


def layout(overview):
    return {s['key']: list(s['module_keys']) for s in overview['sections'] if s.get('key')}


def moved(before, target, destination, anchor=''):
    after = copy.deepcopy(before)
    sources = [key for key, modules in after.items() if target in modules]
    if len(sources) != 1 or destination not in after:
        raise ValueError('Move target or destination is absent from the inspected course.')
    if anchor == target or (anchor and anchor not in after[destination]):
        raise ValueError('The ordering anchor must be a different activity in the destination section.')
    after[sources[0]].remove(target)
    index = after[destination].index(anchor) if anchor else len(after[destination])
    after[destination].insert(index, target)
    return after


def expected_layout(op, completed):
    expected = copy.deepcopy(op['layout_snapshot'])
    for previous, receipt in completed:
        if previous['course_id'] != op['course_id']:
            continue
        if previous['capability'] == 'move_module':
            p = previous['params']
            expected = moved(expected, previous['target'], p['target_section'], p.get('before_module', ''))
        elif previous['capability'] == 'upload_file_resource':
            expected[previous['params']['target_section']].append(receipt['created_target'])
    return expected


class CourseStructure:
    def __init__(self, browser):
        self.browser = browser

    def resolve(self, op):
        b = self.browser
        target = b.targets.get(op.get('target'), {})
        p = op.get('params', {})
        section = b.targets.get(p.get('target_section'), {})
        if op.get('fields') or target.get('kind') != 'module' or section.get('kind') != 'section':
            raise ValueError('A move requires an observed activity and destination section, without field edits.')
        if target['course_id'] != section['course_id']:
            raise ValueError('Moving an existing activity cannot cross courses; copying needs a separate workflow.')
        if target['key'] not in b.forms:
            raise ValueError('Inspect the existing activity settings before moving it.')
        snapshot = layout(b.overviews[target['course_id']])
        moved(snapshot, target['key'], section['key'], p.get('before_module', ''))
        source = next(k for k,v in snapshot.items() if target['key'] in v)
        policy=p.get('visibility_policy') or 'preserve'
        if policy not in {'preserve','inherit_hidden'}:raise ValueError('Unknown move visibility policy.')
        destination_visibility=section.get('visibility','unknown')
        current=next((f for f in b.forms[target['key']]['fields'] if f['key']=='id_visible'),{})
        if destination_visibility=='unknown':raise MoveVisibilityConflict('Inspect the destination section visibility before proposing this move.')
        if policy=='inherit_hidden' and destination_visibility!='hidden':
            raise MoveVisibilityConflict('The selected destination is not hidden; re-read its current visibility.')
        if destination_visibility=='hidden' and current.get('value')!='0' and policy=='preserve':
            raise MoveVisibilityConflict('Moving '+target['title']+' into '+section['title']+
                ' would place it in a hidden section and may remove its course-page visibility. Ask whether to keep it hidden there, or change the destination/section visibility first. Do not move yet.')
        anchor = b.targets.get(p.get('before_module'), {}).get('title', 'end of section')
        summary=op['summary']+(' The destination section is hidden; this activity will be hidden from students.' if policy=='inherit_hidden' else '')
        return {**op, 'summary':summary,'course_id':target['course_id'], 'title':target['title'],
                'destination_visibility':destination_visibility,
                'layout_snapshot':snapshot, 'settings_snapshot':copy.deepcopy(b.forms[target['key']]),
                'placement_review': {'from':b.targets[source]['title'], 'to':section['title'], 'before':anchor}}

    def settings_snapshot_expected(self, op, completed):
        snapshot = copy.deepcopy(op['settings_snapshot'])
        for previous, _ in completed:
            if previous['target'] == op['target'] and previous['capability'] in {'update_moodle_form','replace_file_resource'}:
                values = {p['key']:p['value'] for p in previous['fields']}
                for field in snapshot['fields']:
                    if field['key'] in values:field['value'] = values[field['key']]
        return {'fields':snapshot['fields'],'submit':snapshot['submit']}

    def settings_expected(self, op, completed):
        from chrome_moodle import form_fingerprint
        return form_fingerprint(self.settings_snapshot_expected(op,completed))

    def preflight(self, op, completed=()):
        b = self.browser
        current = b.read_layout(op['course_id'])
        destination=next((s for s in current['sections'] if s.get('key')==op['params']['target_section']),{})
        observed=destination.get('visibility','unknown')
        if observed!=op.get('destination_visibility','shown'):
            raise MoveVisibilityConflict('Destination section visibility changed after planning. No move attempted; inspect it and choose the visibility outcome again.')
        expected = expected_layout(op, completed)
        if layout(current) != expected:
            raise ValueError('Course placement changed outside the approved moves; inspect the new order before saving.')
        current_form = b.inspect(op['target'])
        from chrome_moodle import form_fingerprint,changed_form_fields
        if form_fingerprint({'fields':current_form['fields'],'submit':current_form['submit']}) != self.settings_expected(op, completed):
            changes=changed_form_fields(self.settings_snapshot_expected(op,completed),current_form)
            raise ValueError('Activity settings changed for '+op['title']+': '+(', '.join(changes) or 'save controls')+'. No move attempted; re-read this task before requesting a new approval.')
        return expected

    def visibility_drift(self, op, completed=()):
        """Read-only proof of an applied move with ONLY the visibility value changed."""
        from chrome_moodle import form_fingerprint
        b=self.browser
        b.check_submission_settled(op)
        expected=moved(expected_layout(op,completed),op['target'],op['params']['target_section'],op['params'].get('before_module',''))
        if layout(b.read_layout(op['course_id']))!=expected:return None
        current=b.inspect(op['target'])
        before=self.settings_snapshot_expected(op,completed)
        original=next((f for f in before['fields'] if f['key']=='id_visible'),None)
        observed=next((f for f in current['fields'] if f['key']=='id_visible'),None)
        if not original or not observed or original.get('disabled') or observed.get('disabled'):return None
        if ({k:v for k,v in original.items() if k not in {'value','options'}}!=
            {k:v for k,v in observed.items() if k not in {'value','options'}}):return None
        same_meaning=visibility_label(original)==visibility_label(observed) and visibility_label(original) is not None
        patched={'fields':copy.deepcopy(current['fields']),'submit':current['submit']}
        # Only the visibility control may have contextual options; every other
        # field and save control must still match the approved settings.
        patched['fields']=[copy.deepcopy(original) if f['key']=='id_visible' else f for f in patched['fields']]
        if form_fingerprint(patched)!=form_fingerprint(before):return None
        hidden_policy=op.get('params',{}).get('visibility_policy')=='inherit_hidden'
        expected_label=visibility_label(original)
        matches=[o for o in observed.get('options',[]) if not o.get('disabled') and
                 ((o['value']=='0') if hidden_policy else ' '.join(o['label'].split())==expected_label)]
        wanted=matches[0]['value'] if len(matches)==1 else None
        satisfied=(observed['value']=='0') if hidden_policy else same_meaning
        if form_fingerprint({'fields':current['fields'],'submit':current['submit']})==form_fingerprint(before) and not hidden_policy:return None
        return {'source_operation_id':op['id'],'target':op['target'],'title':op['title'],
                'target_section':op['params']['target_section'],'before_module':op['params'].get('before_module',''),
                'layout':expected,'field':'id_visible','expected_value':wanted,'observed_value':observed['value'],
                'expected_label':expected_label,'observed_label':visibility_label(observed),
                'current_options':copy.deepcopy(observed.get('options',[])),
                'can_restore':wanted is not None,'outcome_satisfied':satisfied,
                'requires_visibility_choice':wanted is None,
                'classification':'move_applied_visibility_changed','move_replay_forbidden':True}

    def execute(self, op, before_save, completed=(), *, before_preserve=None):
        b = self.browser
        self.preflight(op, completed)
        selected = course(op['course_id'])
        b.goto(selected, selected.url)
        course_identity(b.page, selected)
        p = op['params']
        args = {'action':'cm_move', 'courseid':int(selected.id), 'ids':[int(b.targets[op['target']]['id'])],
                'targetsectionid':int(b.targets[p['target_section']]['id'])}
        if p.get('before_module'):args['targetcmid'] = int(b.targets[p['before_module']]['id'])
        b.editor_action(op, args, before_save)
        try:return self.verify(op, completed)
        except RuntimeError:
            if before_preserve is None:raise
            drift=self.visibility_drift(op,completed)
            if not drift or not drift.get('can_restore',True):raise
            if drift.get('outcome_satisfied'):return self.receipt(op)
            # A confirmed move may change Moodle's inherited visibility. Preserve
            # the explicitly approved setting once; never repeat the move.
            repair=b.resolve({'id':op['id']+'_preserve_visibility','capability':'update_moodle_form',
                'target':op['target'],'fields':[{'key':'id_visible','value':drift['expected_value']}],
                'params':{},'summary':'Preserve the approved visibility after moving '+op['title'],
                'scope':op['scope'],'reason':'Preserve approved settings','depends_on':[]})
            b.execute(repair,before_preserve)
            return self.verify(op,completed)

    def verify(self, op, completed=()):
        b = self.browser
        b.check_submission_settled(op)
        expected = moved(expected_layout(op, completed), op['target'], op['params']['target_section'], op['params'].get('before_module',''))
        if layout(b.read_layout(op['course_id'])) != expected:
            raise RuntimeError('The saved section/order does not match the approved move; no automatic replay.')
        from chrome_moodle import form_fingerprint,changed_form_fields
        current=b.inspect(op['target'])
        if form_fingerprint({'fields':current['fields'],'submit':current['submit']}) != self.settings_expected(op, completed):
            proof=self.visibility_drift(op,completed)
            if proof and proof.get('outcome_satisfied'):return self.receipt(op)
            differences=changed_form_fields(self.settings_snapshot_expected(op,completed),current)
            raise RuntimeError('The moved activity settings differ from the approved preserved settings: '+', '.join(differences)+'.')
        if op.get('params',{}).get('visibility_policy')=='inherit_hidden':
            visible=next((f for f in current['fields'] if f['key']=='id_visible'),{})
            if visible.get('value')!='0':raise RuntimeError('The approved hidden visibility was not retained after moving.')
        return self.receipt(op)

    @staticmethod
    def receipt(op):
        return {'status':'Done','target':op['title'],'verified':True,'change':op['summary'],'course_id':op['course_id']}
