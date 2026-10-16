"""Observed section show/hide actions, approved separately before dependent edits.

Moodle state action contract:
https://github.com/moodle/moodle/blob/MOODLE_404_STABLE/course/format/classes/stateactions.php
"""
import copy
from chrome_course_scope import course


class SectionVisibility:
    def __init__(self,browser):self.browser=browser

    def snapshot(self,key):
        b=self.browser;target=b.targets.get(key,{})
        if target.get('kind')!='section':raise ValueError('Section visibility needs an exact observed section.')
        overview=b.overviews[target['course_id']]
        section=next(s for s in overview['sections'] if s.get('key')==key)
        return {'visibility':section['visibility'],'title':section['title'],
                'modules':[{'key':k,'title':b.targets[k]['title'],'module_type':b.targets[k].get('module_type','')}
                           for k in section['module_keys']]}

    def resolve(self,op):
        b=self.browser;key=op.get('target');target=b.targets.get(key,{})
        action=op.get('params',{}).get('action')
        if op.get('fields') or action not in {'show','hide'}:
            raise ValueError('Section visibility requires action show/hide and no form patches.')
        before=self.snapshot(key)
        wanted='shown' if action=='show' else 'hidden'
        if before['visibility']==wanted:raise ValueError('The section already has that visibility; omit this write and inspect the remaining goal.')
        if action not in target.get('visibility_actions',[]):
            raise ValueError('The requested section visibility action is not available in the current course editor.')
        # A section action changes descendant availability. Keep that impact
        # visible in the immutable approval rather than claiming file-only scope.
        names='; '.join(m['title'] for m in before['modules']) or '(empty section)'
        summary=(op['summary']+' Section visibility: '+before['visibility']+' -> '+wanted+
                 '. This affects access to all contents of this section, including: '+names+
                 '. After readback, inspect file visibility and re-plan the remaining goal with fresh approval.')
        return {**copy.deepcopy(op),'summary':summary,'course_id':target['course_id'],
                'title':target['title'],'target_kind':'section','visibility_snapshot':before,
                'desired_visibility':wanted}

    def preflight(self,op):
        self.browser.read_layout(op['course_id'])
        if self.snapshot(op['target'])!=op['visibility_snapshot']:
            raise ValueError('Section visibility or contents changed after approval; inspect the new scope before writing.')
        if op['params']['action'] not in self.browser.targets[op['target']].get('visibility_actions',[]):
            raise ValueError('The approved section visibility control is no longer available.')

    def execute(self,op,before_save):
        self.preflight(op)
        args={'action':'section_'+op['params']['action'],'courseid':int(course(op['course_id']).id),
              'ids':[int(self.browser.targets[op['target']]['id'])]}
        self.browser.editor_action(op,args,before_save)
        return self.verify(op)

    def verify(self,op):
        self.browser.check_submission_settled(op)
        self.browser.read_layout(op['course_id'])
        current=self.snapshot(op['target'])
        if current['visibility']!=op['desired_visibility'] or current['modules']!=op['visibility_snapshot']['modules']:
            raise RuntimeError('Section visibility readback does not match the approved outcome; no write replayed.')
        return {'status':'Done','target':op['title'],'verified':True,'change':op['summary'],'course_id':op['course_id']}
