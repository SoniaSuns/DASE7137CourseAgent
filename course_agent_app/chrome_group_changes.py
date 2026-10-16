"""Group relations with transient participant resolution and verified move rollback."""
import re
import unicodedata

from chrome_course_scope import course, course_identity


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC',str(text)).casefold().split())


class GroupChanges:
    def __init__(self, browser):
        self.browser = browser

    @property
    def page(self):
        return self.browser.page

    def target(self, key, kind):
        target = self.browser.targets.get(key,{})
        if target.get('kind') != kind:
            raise ValueError('Group relation target is absent from the current exact inventory.')
        return target

    def member_state(self, target, reference):
        selected = course(target['course_id'])
        url = selected.url_for('/group/members.php', {'group':target['id']})
        self.browser.goto(selected,url)
        from urllib.parse import parse_qs, urlparse
        if parse_qs(urlparse(self.page.url).query).get('group') != [target['id']]:
            raise ValueError('Membership page changed the exact target group.')
        if self.page.locator('#addselect').count()!=1 or self.page.locator('#removeselect').count()!=1:
            raise ValueError('Both member selectors must be present.')
        query = reference.get('user_name','')
        if query:
            for prefix in ('addselect','removeselect'):
                box = self.page.locator('#'+prefix+'_searchtext')
                if box.count()==1:
                    box.fill(query)
                    button = self.page.locator('#'+prefix+'_searchbutton')
                    if button.count()==1: button.click()
            self.page.wait_for_timeout(600)
        # Return only numeric match values; display text never leaves the DOM.
        found = self.page.evaluate(r"""ref=>{
          const n=x=>String(x).normalize('NFKC').toLowerCase().replace(/\s+/g,' ').trim();
          const values=id=>[...document.querySelectorAll('#'+id+' option')]
            .filter(o=>/^[1-9][0-9]*$/.test(o.value))
            .filter(o=>ref.user_id ? o.value===ref.user_id : n(o.textContent).includes(n(ref.user_name)))
            .map(o=>o.value);
          return {member:values('removeselect'),candidate:values('addselect')};
        }""", reference)
        ids = set(found['member']+found['candidate'])
        if len(ids)!=1:
            raise ValueError('Participant must resolve uniquely in every relevant group page; no candidate details retained.')
        user_id = next(iter(ids))
        return user_id, {'member':user_id in found['member'],'candidate':user_id in found['candidate']}

    def grouping_state(self, target):
        selected = course(target['course_id'])
        self.browser.goto(selected,selected.url_for('/group/assign.php', {'id':target['id']}))
        from urllib.parse import parse_qs, urlparse
        if parse_qs(urlparse(self.page.url).query).get('id') != [target['id']]:
            raise ValueError('Grouping membership page identity changed.')
        if self.page.locator('#existinggroups').count()!=1 or self.page.locator('#potentialgroups').count()!=1:
            raise ValueError('Grouping selectors are unavailable.')
        return self.page.evaluate("""()=>({
          existing:[...document.querySelectorAll('#existinggroups option')].map(o=>o.value).filter(v=>/^[1-9][0-9]*$/.test(v)).sort(),
          potential:[...document.querySelectorAll('#potentialgroups option')].map(o=>o.value).filter(v=>/^[1-9][0-9]*$/.test(v)).sort()
        })""")

    def resolve(self, op, staff_text):
        p = op.get('params',{})
        if op['capability']=='update_group_membership':
            action = p.get('action')
            if action not in {'add','remove','move'}:
                raise ValueError('Group membership action must be add, remove, or move.')
            keys = [p.get('source_group')] if action=='remove' else [p.get('target_group')]
            if action=='move': keys = [p.get('source_group'),p.get('target_group')]
            if len(set(keys))!=len(keys): raise ValueError('Move source and target must differ.')
            targets = [self.target(k,'group') for k in keys]
            reference = {k:str(p[k]).strip() for k in ('user_id','user_name') if p.get(k)}
            if len(reference)!=1: raise ValueError('Supply exactly one participant ID or name query.')
            if 'user_id' in reference and not re.fullmatch('[1-9][0-9]*',reference['user_id']):
                raise ValueError('Participant ID must be a positive integer.')
            value = next(iter(reference.values()))
            if normalized(value) not in normalized(staff_text):
                raise ValueError('Participant reference must come from the current staff request.')
            states = [self.member_state(t,reference) for t in targets]
            if len({s[0] for s in states})!=1:
                raise ValueError('Participant resolution differs across group pages.')
            if action in {'remove','move'} and not states[0][1]['member']:
                raise ValueError('Participant is not a member of the source group.')
            if action in {'add','move'} and states[-1][1]['member']:
                raise ValueError('Participant is already in the target group; inspect the intended change.')
            snapshot = [s[1] for s in states]
        elif op['capability']=='update_grouping_membership':
            targets = [self.target(op['target'],'grouping')]
            add = p.get('add_groups',[]); remove = p.get('remove_groups',[])
            if not (add or remove) or set(add)&set(remove):
                raise ValueError('Specify disjoint groups to add/remove.')
            group_targets = [self.target(k,'group') for k in add+remove]
            if any(t['course_id']!=targets[0]['course_id'] for t in group_targets):
                raise ValueError('Grouping relations cannot cross courses.')
            snapshot = self.grouping_state(targets[0])
            if not {self.target(k,'group')['id'] for k in add} <= set(snapshot['potential']):
                raise ValueError('Some additions are already linked or unavailable.')
            if not {self.target(k,'group')['id'] for k in remove} <= set(snapshot['existing']):
                raise ValueError('Some removals are not linked to the grouping.')
        else: raise ValueError('Unsupported group relation capability.')
        if len({t['course_id'] for t in targets})!=1:
            raise ValueError('Group relations cannot cross courses.')
        from chrome_moodle import digest
        overview=self.browser.overview(targets[0]['course_id'])
        inventory=digest({'groups':overview['groups'],'groupings':overview['groupings']})
        return {**op,'course_id':targets[0]['course_id'], 'title':' / '.join(t['title'] for t in targets),
                'relation_snapshot':snapshot,'group_inventory_digest':inventory}

    def change_member(self, target, reference, expected_id, add, operation=None):
        user_id,state = self.member_state(target,reference)
        if user_id!=expected_id or state['member']==add:
            raise ValueError('Membership changed immediately before the write.')
        self.page.locator('#addselect' if add else '#removeselect').select_option(user_id)
        course_identity(self.page,course(target['course_id']))
        operation = operation or {'target':target.get('key',target['id']), 'course_id':target['course_id']}
        self.browser.submit_and_wait(operation,self.page.locator('#add' if add else '#remove'),lambda:None)
        after_id,after = self.member_state(target,reference)
        if after_id!=expected_id or after['member']!=add:
            raise RuntimeError('Group membership readback did not match the approved change.')

    def execute(self, op, staff_text, before_save):
        fresh = self.resolve(op,staff_text)
        if fresh['relation_snapshot']!=op['relation_snapshot']:
            raise ValueError('Group relations changed after approval; no write attempted.')
        p=op['params']
        if op['capability']=='update_group_membership':
            reference = {k:str(p[k]).strip() for k in ('user_id','user_name') if p.get(k)}
            action=p['action']
            key=p['source_group'] if action=='remove' else p['target_group']
            target=self.target(key,'group')
            user_id,_=self.member_state(target,reference)
            before_save()
            if action=='move':
                source=self.target(p['source_group'],'group')
                self.change_member(target,reference,user_id,True,op)
                try:
                    self.change_member(source,reference,user_id,False,op)
                except Exception:
                    self.browser.check_submission_settled(op)
                    # Read source before rollback: a timeout may have followed a successful removal.
                    check_id,source_state=self.member_state(source,reference)
                    if check_id==user_id and not source_state['member']:
                        pass
                    else:
                        self.change_member(target,reference,user_id,False,op)
                        raise RuntimeError('Source removal failed; newly added target membership was rolled back and verified.')
            else:
                self.change_member(target,reference,user_id,action=='add',op)
        else:
            target=self.target(op['target'],'grouping')
            before_save()
            for name, selector, button in [('add_groups','#potentialgroups','#add'),('remove_groups','#existinggroups','#remove')]:
                if p.get(name):
                    self.grouping_state(target)
                    self.page.locator(selector).select_option([self.target(k,'group')['id'] for k in p[name]])
                    self.browser.submit_and_wait(op,self.page.locator(button),lambda:None)
            after=self.grouping_state(target)
            expected=(set(op['relation_snapshot']['existing']) |
                      {self.target(k,'group')['id'] for k in p.get('add_groups',[])}) - {
                          self.target(k,'group')['id'] for k in p.get('remove_groups',[])}
            if set(after['existing'])!=expected:
                raise RuntimeError('Grouping membership readback did not match the approved change.')
        return self.verify(op)

    def verify(self, op):
        self.browser.check_submission_settled(op)
        p=op['params']
        if op['capability']=='update_group_membership':
            reference={k:str(p[k]).strip() for k in ('user_id','user_name') if p.get(k)}
            observed=[]
            for key,expected in [('source_group',False),('target_group',True)]:
                if (key=='source_group' and p['action']=='add') or (key=='target_group' and p['action']=='remove'):
                    continue
                uid,state=self.member_state(self.target(p[key],'group'),reference)
                if state['member']!=expected: raise RuntimeError('Membership readback does not yet match the approved final state.')
                observed.append(uid)
            if len(set(observed))!=1: raise RuntimeError('Participant resolution changed across group readback.')
        else:
            after=self.grouping_state(self.target(op['target'],'grouping'))
            expected=(set(op['relation_snapshot']['existing']) |
                      {self.target(k,'group')['id'] for k in p.get('add_groups',[])}) - {
                          self.target(k,'group')['id'] for k in p.get('remove_groups',[])}
            if set(after['existing'])!=expected: raise RuntimeError('Grouping relation readback does not match the approved final state.')
        return {'status':'Done','target':op['title'],'verified':True,'change':op['summary'],'course_id':op['course_id']}
