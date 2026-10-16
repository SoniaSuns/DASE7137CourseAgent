"""Browser evidence and bounded, observed-form operations for current courses.

All JavaScript here is fixed application code. Planners can only name observed
targets/fields/values, never selectors, URLs, scripts or arbitrary clicks.
"""
from __future__ import annotations

import hashlib
import json
import copy
import re
import time
import traceback
import secrets
from pathlib import Path
from playwright.sync_api import Error as BrowserError
from urllib.parse import parse_qs, urlparse

from chrome_course_scope import course, course_identity, MoodleLoginRequired
from chrome_availability import normalized as availability_value, date_changes, apply_dates, validate_tree, apply_tree
from chrome_rich_text import canonical as rich_value, apply as apply_rich
from chrome_form_dates import date_fields,validate as validate_dates,review as date_review
from chrome_draft_content import inspect_draft_content,comparable as comparable_content


class MoodleReadUnavailable(ValueError):
    """Bounded browser recovery exhausted, with sanitized diagnostic evidence."""


class MoodleFormNotReady(ValueError):
    pass


class MoodleSubmissionUnconfirmed(RuntimeError):
    """The original submit page must remain intact until its response completes."""


class MoodleSaveNotDispatched(RuntimeError):
    """Save did not reach its input/submit boundary; inspect and re-plan."""


def reveal_form_field(page, locator):
    """Expand only observed structural ancestors of this exact form control."""
    containers = locator.evaluate(r"""el => {
      const ancestors=[];
      for(let node=el.parentElement;node && !node.matches('form.mform');node=node.parentElement) {
        if(node.matches('details:not([open])')) ancestors.push({kind:'details',id:node.id});
        else if(node.matches('.collapse:not(.show)') && node.id) ancestors.push({kind:'collapse',id:node.id});
      }
      return ancestors.reverse();
    }""")
    for container in containers:
        if container['kind']=='details':
            # A details ancestor may have no id; resolve it relative to the exact field.
            details=locator.locator('xpath=ancestor::details[not(@open)]').first
            if details.count():details.locator(':scope > summary').click(timeout=5000)
        else:
            panel=page.locator('[id='+json.dumps(container['id'])+']')
            toggles=page.locator(
                'form.mform [data-bs-toggle="collapse"][aria-controls='+json.dumps(container['id'])+'], '
                'form.mform [data-toggle="collapse"][aria-controls='+json.dumps(container['id'])+']')
            if toggles.count()!=1:raise ValueError('The collapsed Moodle setting has no unique expand control.')
            safe_toggle=toggles.evaluate("(e,id)=>(e.tagName==='A' && e.getAttribute('href')==='#'+id) || (e.tagName==='BUTTON' && e.type==='button')",container['id'])
            if not safe_toggle:raise ValueError('The expand control is not a verified local display-only control.')
            if toggles.get_attribute('aria-expanded')=='false':toggles.click(timeout=5000)
            panel.wait_for(state='visible',timeout=5000)
    locator.wait_for(state='visible',timeout=5000)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def comparable_form(snapshot):
    canonical = copy.deepcopy(snapshot)
    canonical.pop('fingerprint', None)
    fields = {f['name']: f for f in canonical['fields']}
    for field in canonical['fields']:
        field['value']=comparable_content(field)
        field.pop('draft_content',None)
        match = re.fullmatch(r'(.+)\[(day|month|year|hour|minute)\]', field['name'])
        if match and field['disabled']:
            enabled = fields.get(match[1] + '[enabled]', {})
            if enabled.get('type') == 'checkbox' and enabled.get('value') is False:
                # Moodle fills unused optional date controls with the current time.
                field['value'] = None
        if field['key'] == 'id_availabilityconditionsjson' and field['value']:
            field['value'] = availability_value(field['value'])
    return canonical


def form_fingerprint(snapshot):
    return digest(comparable_form(snapshot))


def changed_form_fields(before,after):
    left={f['key']:f for f in comparable_form(before)['fields']}
    right={f['key']:f for f in comparable_form(after)['fields']}
    return [str(right.get(k,left.get(k,{})).get('label',k))+' ('+k+')'
            for k in sorted(set(left)|set(right)) if left.get(k)!=right.get(k)]


def stable_form(page):
    """Read only after Moodle's registered initializers and controls have settled."""
    page.wait_for_function("() => document.readyState === 'complete' && !window.M?.util?.pending_js?.length",
                           polling=100, timeout=30000)
    previous = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = page.evaluate(FORM_SCRIPT)
        if current and previous == current:return current
        previous = current
        page.wait_for_timeout(250)
    raise MoodleFormNotReady('Moodle form controls did not finish initializing; no save attempted.')


FORM_SCRIPT = r"""() => {
 const f = document.querySelector('form.mform');
 if (!f) return null;
 const supported = new Set(['text','number','url','email','date','datetime-local','time','checkbox','radio','select-one','select-multiple','textarea']);
 const fields = [...f.elements].filter(e => e.name && e.id && supported.has(e.type) &&
   !/(password|secret|sesskey|token|enrolmentkey)/i.test(e.name)).map(e => ({
   key: e.id, name: e.name, type: e.type,
   label: [...(e.labels || [])].map(l=>l.textContent.trim()).join(' ') || e.getAttribute('aria-label') || e.name,
   value: e.type === 'checkbox' || e.type === 'radio' ? e.checked : e.multiple ? [...e.selectedOptions].map(o=>o.value) : e.value,
   disabled: e.disabled,
   options: e.options ? [...e.options].map(o=>({value:o.value,label:o.textContent.trim(),disabled:o.disabled})) : [],
   rich: !!(window.tinymce && tinymce.get(e.id)) || !!document.getElementById(e.id+'_ifr')
 }));
 return {fields, submit: [...f.elements].filter(e=>e.type==='submit' && ['submitbutton','submitbutton2','savechanges'].includes(e.name)).map(e=>({key:e.id,name:e.name,label:e.value||e.textContent.trim()}))};
}"""


class MoodleBrowser:
    def __init__(self, page, env=None):
        self.page = page
        self.login_env = env or {}
        self.login_attempted = False
        self.targets = {}
        self.forms = {}
        self.unavailable_targets = {}
        self.overviews = {}
        self.last_action = {}
        self.recover_read_page = None
        self.diagnostic_path = None
        self.recovery_events = []
        self._read_depth = 0
        self.submission_observation = {}
        self.submission_diagnostic_path = None
        self.submit_timeout_seconds = 45
        self.save_click_timeout_ms = 10000
        self.submission_page = None

    def _read(self, operation, call):
        """Repeat only a GET/read or unsaved preparation, never a Save click.

        Nested calls share one budget. Every retry runs the original entry point,
        including target identity, fresh form and approved-fingerprint checks.
        """
        if self._read_depth:return call()
        self._read_depth += 1
        event={'operation':operation,'attempts':[],'outcome':'running'}
        try:
            for attempt in range(3):
                try:
                    if attempt and (attempt==2 or self.page.is_closed()):
                        if self.recover_read_page:
                            self.page=self.recover_read_page(self.page)
                        else:
                            self.page=self.page.context.new_page()
                    result=call()
                    if event['attempts']:
                        event['outcome']='recovered';self._record_recovery(event)
                    return result
                except (BrowserError,MoodleFormNotReady) as exc:
                    event['attempts'].append({'attempt':attempt+1,'error_type':type(exc).__name__,
                        'action':dict(self.last_action),
                        'frames':[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                                  for f in traceback.extract_tb(exc.__traceback__)
                                  if Path(f.filename).parent==Path(__file__).parent]})
                    self._record_recovery(event)
            event['outcome']='exhausted';self._record_recovery(event)
            action=self.last_action
            raise MoodleReadUnavailable('Moodle could not '+operation.replace('_',' ')+' for '+
                action.get('title','the selected course')+' after 3 read attempts (stage: '+
                action.get('stage','read')+'). The page was reopened automatically; no Save was retried.') from None
        finally:self._read_depth -= 1

    def _record_recovery(self,event):
        # No raw exception text, URLs, form values, credentials or cookie data.
        if not self.recovery_events or self.recovery_events[-1] is not event:
            self.recovery_events.append(event)
        self.recovery_events=self.recovery_events[-30:]
        if self.diagnostic_path:
            path=Path(self.diagnostic_path);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(self.recovery_events,ensure_ascii=False,indent=2),encoding='utf-8')

    def goto(self, selected, url):
        if self.submission_observation.get('completion')=='pending':
            raise MoodleSubmissionUnconfirmed('The original Moodle submission is still pending; its page is preserved.')
        return self._read('open_page',lambda:self._goto_once(selected,url))

    def _goto_once(self, selected, url):
        selected.check_origin(url)
        self.last_action.update(stage='navigate',course_id=selected.id)
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.last_action['stage']='check_course_identity'
        try:
            course_identity(self.page, selected)
        except MoodleLoginRequired:
            if self.login_attempted:raise
            self.login_attempted = True
            portal=self.page.locator('a[href*="authCAS=CAS"]')
            if portal.count()==1:
                portal_url=portal.get_attribute('href')
                selected.check_origin(portal_url)
                # Navigate the observed HKU Portal entry using the existing Chrome
                # SSO session. Never fill credentials on an external identity page.
                self.page.goto(portal_url,wait_until='domcontentloaded',timeout=60000)
                if urlparse(self.page.url).hostname!=urlparse(selected.origin).hostname:
                    raise MoodleLoginRequired('HKU Portal requires login or verification in the dedicated Chrome window.')
                self.page.goto(url,wait_until='domcontentloaded',timeout=60000)
                course_identity(self.page,selected)
                self.login_attempted=False
                return
            username=self.login_env.get('MOODLE_LOGIN_USERNAME','')
            password=self.login_env.get('MOODLE_LOGIN_PASSWORD','')
            if not username or not password:raise
            # Only the observed, same-host Moodle login form; never submit to an SSO redirect.
            form=self.page.locator('form').filter(has=self.page.locator('#loginbtn'))
            if form.count()!=1 or form.locator('#username').count()!=1 or form.locator('#password').count()!=1:
                raise MoodleLoginRequired('Moodle requires manual login in the dedicated Chrome window.')
            selected.check_origin(form.evaluate('(f)=>f.action'))
            form.locator('#username').fill(username)
            form.locator('#password').fill(password)
            form.locator('#loginbtn').click(timeout=10000)
            # Return to precisely the interrupted read URL, once; never replay a save.
            self.page.goto(url,wait_until='domcontentloaded',timeout=60000)
            course_identity(self.page,selected)
        self.login_attempted = False

    def overview(self, course_id):
        return self._read('read_course_inventory',lambda:self._overview_once(course_id))

    def _overview_once(self, course_id):
        selected = course(course_id)
        result = self.read_layout(course_id)
        return self._relations_overview(selected,result)

    def read_layout(self, course_id):
        return self._read('read_course_layout',lambda:self._layout_once(course_id))

    def _layout_once(self, course_id):
        selected = course(course_id)
        self.last_action={'stage':'read_course','title':selected.label,'course_id':selected.id}
        self.goto(selected, selected.url)
        # Editing mode is a session UI preference, not a course-content write.
        # Read the actual allowed section actions instead of guessing permission.
        switch=self.page.locator('input[name="setmode"][type="checkbox"]')
        if switch.count()==1 and not switch.is_checked():
            self.page.bring_to_front()
            with self.page.expect_navigation(wait_until='domcontentloaded',timeout=15000):
                switch.check(timeout=10000)
            course_identity(self.page,selected)
        result = self.page.evaluate(r"""() => {
          const sections = [...document.querySelectorAll('li.section, [data-for="section"][id^="section-"]')].map(s=>({
            id:s.id, dbid:s.dataset.id||'', number:s.dataset.number||s.id.replace('section-',''),
            title:(s.dataset.sectionname || s.querySelector('.sectionname')?.textContent || s.querySelector('h3[data-for="section_title"],h3')?.textContent || '').replace(/\s+/g,' ').trim(),
            visibility:s.classList.contains('hidden') ? 'hidden' : 'shown',
            visibility_actions:[...s.querySelectorAll('[data-action="sectionShow"], [data-action="sectionHide"]')]
              .filter(e=>e.dataset.id===s.dataset.id).map(e=>e.dataset.action==='sectionShow'?'show':'hide'),
            modules:[...s.querySelectorAll('[id^="module-"]')].map(m=>({
              id:m.id.replace('module-',''), title:(m.querySelector('.instancename, .activityname')||m).textContent.trim(),
              module_type:[...m.classList].find(c=>c.startsWith('modtype_'))?.replace('modtype_','')||'',
              text:m.innerText.trim(), links:[...m.querySelectorAll('a[href]')].map(a=>a.href)
            }))
          }));
          return {title:document.querySelector('h1')?.textContent.trim() || document.title,sections};
        }""")
        if not result["sections"]:
            raise ValueError("Course section inventory was empty; refresh the course view before planning.")
        previous_targets={k:v for k,v in self.targets.items() if v['course_id']==selected.id and v['kind'] not in {'group','grouping','new_group','new_grouping'}}
        self.targets = {k:v for k,v in self.targets.items() if v['course_id']!=selected.id or v['kind'] in {'group','grouping','new_group','new_grouping'}}
        records = []
        seen = set()
        for section in result["sections"]:
            sid = str(section.pop('dbid',''))
            section['module_keys'] = []
            if sid.isdigit() and int(sid)>0:
                section['key'] = f'{selected.id}:section:{sid}'
                section['section_id'] = sid
                self.targets[section['key']] = {'key':section['key'],'id':sid,'kind':'section','course_id':selected.id,
                    'number':section['number'],'title':section['title'],'visibility':section['visibility'],
                    'visibility_actions':section.get('visibility_actions',[]),
                    'edit_url':selected.url_for('/course/editsection.php',{'id':sid})}
                newkey=f'{selected.id}:new_resource:{sid}'
                self.targets[newkey]={'key':newkey,'id':'new','kind':'new_resource','course_id':selected.id,
                    'section_key':section['key'],'title':'New file in '+section['title'],
                    'edit_url':selected.url_for('/course/modedit.php',{'add':'resource','type':'','course':selected.id,'section':section['number']})}
                section['new_resource_target']=newkey
            for module in section.pop("modules"):
                mid = str(module["id"])
                if not mid.isdigit() or mid in seen:
                    continue
                seen.add(mid)
                key = f"{selected.id}:module:{mid}"
                entry = {"key": key, "course_id": selected.id, "kind": "module", "id": mid,
                         "title": module["title"], "section": section["title"],
                         'section_key':section.get('key',''), 'module_type':module.get('module_type',''),
                         "text": module["text"][:6000],
                         "edit_url": selected.url_for('/course/modedit.php', {'update': mid})}
                self.targets[key] = entry
                section['module_keys'].append(key)
                records.append({k: v for k, v in entry.items() if k != "edit_url"})
        result.update(course_id=selected.id, label=selected.label, modules=records)
        from chrome_gradebook import register
        register(self,result)
        self.overviews[selected.id] = {**self.overviews.get(selected.id,{}),**result}
        self.reconcile_inventory_cache(previous_targets)
        return result

    def reconcile_inventory_cache(self,previous_targets):
        """Invalidate only targets absent from a successfully refreshed layout.

        A historical form is not evidence that a removed activity still exists.
        Retain a small identity notice for planning; never retarget by title.
        """
        for key,target in previous_targets.items():
            if target.get('kind') in {'gradebook','grade_item','grade','participants'}:
                self.forms.pop(key,None)
            if key in self.targets:continue
            self.forms.pop(key,None)
            if target.get('kind') in {'module','section','gradebook','grade_item','grade','participants'}:
                self.unavailable_targets[key]={'target':key,'course_id':target['course_id'],
                    'title':target.get('title',''),'status':'absent_from_refreshed_inventory'}
        for key in self.targets:self.unavailable_targets.pop(key,None)

    def _relations_overview(self,selected,result):
        self.targets = {k:v for k,v in self.targets.items() if v['course_id']!=selected.id or v['kind'] not in {'group','grouping','new_group','new_grouping'}}
        # Group names/counts and grouping links only. Never collect participants.
        self.goto(selected, selected.url_for('/group/index.php', {'id': selected.id}))
        groups = self.page.locator('select#groups option').evaluate_all(
            "els=>els.filter(e=>/^[1-9][0-9]*$/.test(e.value)).map(e=>({id:e.value,title:e.textContent.trim()}))")
        result["groups"] = []
        for item in groups:
            key = f"{selected.id}:group:{item['id']}"
            entry = {**item, "key": key, "kind": "group", "course_id": selected.id,
                     "edit_url": selected.url_for('/group/group.php', {'id': item['id'], 'courseid': selected.id})}
            self.targets[key] = entry
            result["groups"].append({k:v for k,v in entry.items() if k != "edit_url"})
        self.goto(selected, selected.url_for('/group/groupings.php', {'id': selected.id}))
        groupings = self.page.locator('a[href*="/group/grouping.php?"]').evaluate_all(
            "els=>els.map(e=>({url:e.href,text:e.closest('tr')?.innerText||e.textContent}))")
        result["groupings"] = []
        seen = set()
        for item in groupings:
            query = parse_qs(urlparse(item["url"]).query)
            gid = query.get('id', [''])[0]
            if not gid.isdigit() or gid == '0' or gid in seen:
                continue
            seen.add(gid)
            key = f"{selected.id}:grouping:{gid}"
            entry = {"key": key, "id": gid, "course_id": selected.id, "kind": "grouping",
                     "title": item['text'].strip(),
                     "edit_url": selected.url_for('/group/grouping.php', {'id': gid, 'courseid': selected.id})}
            self.targets[key] = entry
            result["groupings"].append({k:v for k,v in entry.items() if k != "edit_url"})
        for kind, path in [('new_group', '/group/group.php'), ('new_grouping', '/group/grouping.php')]:
            key = f"{selected.id}:{kind}:new"
            self.targets[key] = {"key":key, "id":"new", "course_id":selected.id, "kind":kind,
                                 "title":kind.replace('_',' '),
                                 "edit_url":selected.url_for(path, {'courseid':selected.id})}
        result['creation_targets'] = [f"{selected.id}:new_group:new", f"{selected.id}:new_grouping:new"] + [s['new_resource_target'] for s in result['sections'] if s.get('new_resource_target')]
        self.overviews[selected.id] = result
        return result

    def inspect(self, key):
        return self._read('read_settings',lambda:self._inspect_once(key))

    def _inspect_once(self, key):
        if key not in self.targets:
            raise ValueError("Inspection target is absent from the current course inventory.")
        target = self.targets[key]
        if target['kind']=='participants':
            from chrome_gradebook import participants
            return participants(self,target)
        if target['kind'] in {'gradebook','grade_item'}:
            from chrome_gradebook import inspect
            return inspect(self,target)
        if target['kind']=='grade':
            from chrome_gradebook import read_range
            target['grade_range']=read_range(self,course(target['course_id']),target['item_id'])
        self.last_action = {'stage':'read_form','target':key,'title':target['title']}
        self.goto(course(target['course_id']), target['edit_url'])
        # DOM observation includes collapsed fields; no expand clicks are needed
        # until an approved field is filled by prepare().
        self.last_action['stage']='wait_form_ready'
        result = stable_form(self.page)
        if not result or not result['fields'] or not result['submit']:
            raise ValueError(f"No supported editable Moodle form found for {target['title']}.")
        result.update(target=key, course_id=target['course_id'], title=target['title'])
        # Identity fields are inspected separately and never exposed (sesskey etc).
        self.last_action['stage']='check_form_identity'
        identity = self.page.locator('form.mform input[type=hidden]').evaluate_all(
            "els=>Object.fromEntries(els.filter(e=>['course','courseid','id','update','itemid','userid'].includes(e.name)).map(e=>[e.name,e.value]))")
        if target['kind']=='grade':
            from chrome_gradebook import bind_form
            bind_form(self.page,target,result,identity)
        for name in ('course', 'courseid'):
            if name in identity and identity[name] != target['course_id']:
                raise ValueError("Form course identity does not match its inventory target.")
        if target['kind'] == 'module' and identity.get('update') != target['id']:
            raise ValueError("Module edit form did not identify the selected activity.")
        if target['kind'] in {'group', 'grouping'} and identity.get('id') != target['id']:
            raise ValueError("Group edit form did not identify the selected target.")
        if target['kind']=='section' and identity.get('id')!=target['id']:
            raise ValueError('Section form does not match its exact inventory target.')
        if target['kind']=='new_resource':
            query=parse_qs(urlparse(self.page.url).query)
            if query.get('add')!=['resource'] or query.get('course')!=[target['course_id']]:
                raise ValueError('New resource form identity changed.')
        if target.get('module_type')=='resource' or target['kind']=='new_resource':
            manager=self.page.locator('#fitem_id_files .filemanager.fm-loaded')
            manager.wait_for(state='attached',timeout=15000)
            if manager.count()!=1:raise ValueError('Resource file manager is not unique.')
            result['resource_files']=manager.locator('.fp-file .fp-filename').all_text_contents()
            result['resource_files']=[name.strip() for name in result['resource_files']]
        inspect_draft_content(self.page,result['fields'])
        result['fingerprint'] = form_fingerprint(result)
        self.forms[key] = result
        return result

    def resolve(self, proposed):
        if proposed.get('capability') != 'update_moodle_form':
            raise ValueError("Use the observed update_moodle_form capability; arbitrary browser steps are unavailable.")
        key = proposed.get('target')
        if key not in self.forms:
            raise ValueError("Inspect the exact form before proposing any changes.")
        snapshot = self.forms[key]
        patches = proposed.get('fields')
        if not isinstance(patches, list) or not patches:
            raise ValueError("A form operation needs at least one observed field change.")
        fields = {f['key']: f for f in snapshot['fields']}
        if len(fields) != len(snapshot['fields']):
            raise ValueError("Form field identities are ambiguous.")
        seen = set()
        resolved = []
        for patch in patches:
            field_key = patch.get('key')
            if field_key not in fields or field_key in seen:
                raise ValueError("A proposed field was not observed, or was selected twice.")
            seen.add(field_key)
            observed = fields[field_key]
            value = patch.get('value')
            if observed['type'] in {'checkbox', 'radio'}:
                if type(value) is not bool:
                    raise ValueError("Checkbox/radio values must be boolean.")
                if observed['type'] == 'radio' and value is not True:
                    raise ValueError("Select the desired radio option, rather than clearing an option.")
            elif observed['type'] == 'select-multiple':
                if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                    raise ValueError("Multiple choice values must be a list of exact observed values.")
            elif not isinstance(value, str):
                raise ValueError("Text/select values must be strings.")
            if observed['type'].startswith('select'):
                allowed = {o['value'] for o in observed['options'] if not o['disabled']}
                requested = value if isinstance(value, list) else [value]
                if not set(requested) <= allowed:
                    raise ValueError("Selected option is absent or disabled in the live form.")
            if observed['rich']:rich_value(value)
            if field_key == 'id_availabilityconditionsjson':
                validate_tree(observed['value'],value,self.targets,snapshot['course_id'])
            resolved.append({'key':field_key, 'label':observed['label'], 'before':observed['value'],
                             'value':value, 'type':observed['type'], 'options':observed['options'],'rich':observed['rich']})
        if self.targets[key]['kind']=='grade':
            from chrome_gradebook import validate_patch
            resolved=validate_patch(self.targets[key],snapshot,resolved)
        return {**proposed, 'fields':resolved, 'course_id':snapshot['course_id'],
                **({'grade_identity':copy.deepcopy(snapshot['grade_identity'])} if self.targets[key]['kind']=='grade' else {}),
                'date_fields':date_fields(snapshot['fields']),
                'title':snapshot['title'], 'fingerprint':snapshot['fingerprint'],
                'target_kind':self.targets[key]['kind'],
                'existing_ids':sorted(k for k,v in self.targets.items() if v['course_id']==snapshot['course_id'])}

    def preflight(self, operation):
        if operation.get('target_kind')=='grade' and operation['target'] not in self.targets:self.restore_grade(operation)
        current = self.inspect(operation['target'])
        if operation.get('target_kind')=='grade':
            from chrome_gradebook import validate_operation_identity
            validate_operation_identity(operation,current)
        if current['fingerprint'] != operation['fingerprint']:
            raise ValueError("The approved form changed. No save attempted; review the changed state.")
        validate_dates(current['fields'],operation['fields'],operation['title'])
        return current

    def restore_grade(self,operation):
        from chrome_gradebook import restore_grade
        return restore_grade(self,operation)

    def prepare(self, operation):
        """Recheck and fill a local form only; never submit. Also used for rehearsal."""
        return self._read('prepare_settings',lambda:self._prepare_once(operation))

    def _prepare_once(self, operation):
        current = self.preflight(operation)
        for patch in operation['fields']:
            self.last_action = {'stage':'fill_field','target':operation['target'],
                                'title':operation['title'],'field':patch['key'],'label':patch['label']}
            locator = self.page.locator('[id=' + json.dumps(patch['key']) + ']')
            if locator.count() != 1:
                raise ValueError("The observed field is no longer unique.")
            # Moodle dates can be disabled until their approved enable checkbox is changed.
            if not locator.is_enabled():
                raise ValueError("Field is disabled; inspect/approve its controlling setting first.")
            if patch['key'] != 'id_availabilityconditionsjson' and not patch.get('rich'):reveal_form_field(self.page,locator)
            if patch['key'] == 'id_availabilityconditionsjson':
                try:date_changes(patch['before'],patch['value'])
                except ValueError:apply_tree(self.page,patch['before'],patch['value'])
                else:apply_dates(self.page, patch['before'], patch['value'])
            elif patch.get('rich'):
                apply_rich(self.page,patch['key'],patch['value'])
            elif patch['type'] in {'checkbox','radio'}:
                locator.set_checked(patch['value'])
            elif patch['type'].startswith('select'):
                locator.select_option(patch['value'])
            else:
                locator.fill(patch['value'])
        self.last_action = {'stage':'verify_prepared_form','target':operation['target'],'title':operation['title']}
        course_identity(self.page, course(operation['course_id']))
        submit = current['submit'][0]
        button = self.page.locator('[id=' + json.dumps(submit['key']) + ']')
        if button.count() != 1:
            raise ValueError("The observed save button is no longer unique.")
        for patch in operation['fields']:
            if patch['key'] == 'id_availabilityconditionsjson':
                actual = self.page.locator('#id_availabilityconditionsjson').input_value()
                if availability_value(actual) != availability_value(patch['value']):
                    raise ValueError('Date restriction changed before save; no save attempted.')
            else:
                locator=self.page.locator('[id='+json.dumps(patch['key'])+']')
                actual=locator.evaluate("e=>e.type==='checkbox'||e.type==='radio'?e.checked:e.multiple?[...e.selectedOptions].map(o=>o.value):e.value")
                equal=rich_value(actual)==rich_value(patch['value']) if patch.get('rich') else actual==patch['value']
                if not equal:raise ValueError('A Moodle field did not retain its approved value; no save attempted.')
        reveal_form_field(self.page,button)
        return button

    def execute(self, operation, before_save):
        # Keep the agent's Moodle renderer active while WhatsApp or the operator
        # has another tab in front. Playwright stability checks use animation
        # frames, which a background renderer can stop producing.
        self.activate_form_renderer()
        self.page.bring_to_front()
        button = self.prepare(operation)
        # A trial performs actionability checks only. One fresh GET/preparation
        # is allowed here because no real click or approval boundary ran yet.
        for attempt in range(2):
            try:
                button.click(trial=True,timeout=self.save_click_timeout_ms)
                break
            except BrowserError as exc:
                from run_trace import RunTrace,environment_secrets
                self.last_action={'stage':'save_readiness','target':operation['target'],'title':operation['title'],
                    'attempt':attempt+1,'readiness_error':RunTrace(Path('.'),environment_secrets(self.login_env)).clean(str(exc))[:5000]}
                if attempt:
                    raise MoodleSaveNotDispatched('The Save button was not actionable after a fresh inspection. No Save click was attempted.') from exc
                self.page.bring_to_front()
                button=self.prepare(operation)
        self.last_action = {'stage':'save_form','target':operation['target'],'title':operation['title']}
        self.submit_and_wait(operation,button,before_save)
        if self.submission_observation.get('response_status',200)>=400:
            raise RuntimeError('Moodle returned HTTP '+str(self.submission_observation['response_status'])+
                               ' to the save request. Readback is required; no Save is replayed.')
        course_identity(self.page, course(operation['course_id']))
        messages=self.form_errors()
        if messages:
            raise MoodleFormRejected('Moodle rejected the form: '+'; '.join(messages)[:1500])
        return self.verify(operation)

    def activate_form_renderer(self):
        if getattr(self,'_active_form_page',None) is self.page:return
        previous=getattr(self,'_active_form_session',None)
        if previous:
            try:previous.detach()
            except BrowserError:pass
        session=self.page.context.new_cdp_session(self.page)
        try:session.send('Emulation.setFocusEmulationEnabled',{'enabled':True})
        except Exception:
            session.detach();raise
        # Retain the session even for a pending response. Detaching at timeout
        # could throttle the original delayed submit; never close its page.
        self._active_form_session=session;self._active_form_page=self.page

    def form_errors(self):
        errors=self.page.locator('form.mform .invalid-feedback, form.mform [id^="id_error_"]')
        return list(dict.fromkeys(' '.join(text.split()) for text in errors.all_text_contents() if text.strip()))

    def submit_and_wait(self,operation,button,before_save):
        """Observe the new POST and resulting document, not the old load state.

        Listeners are installed before the one permitted click. A delayed client
        submit or response must not be cancelled by readback navigation.
        """
        page=self.page
        form=button.evaluate('(e)=>({action:e.form.action,method:e.form.method})')
        selected=course(operation['course_id']);selected.check_origin(form['action'])
        if form['method'].lower()!='post':raise ValueError('The observed save form is not a POST form.')
        action=urlparse(form['action'])
        observation={'target':operation['target'],'started':time.time(),'post_requests':0,
                     'write_requests':0,'background_requests':0,'request_metadata':[],
                     'response_status':None,'completion':'pending',
                     'page_marker':'course-agent-submit-'+secrets.token_hex(12),'probe_token':secrets.token_hex(12)}
        self.submission_observation=observation
        self.submission_page=page
        def persist():
            if self.submission_diagnostic_path:
                path=Path(self.submission_diagnostic_path);path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(json.dumps(observation,indent=2),encoding='utf-8')
        def matches(request):
            parsed=urlparse(request.url)
            return request.method=='POST' and request.is_navigation_request() and request.frame==page.main_frame and (
                parsed.scheme,parsed.netloc,parsed.path)==(action.scheme,action.netloc,action.path)
        def requested(request):
            if request.method not in {'GET','HEAD','OPTIONS'} and urlparse(request.url).netloc==action.netloc:
                parsed=urlparse(request.url);methods=[]
                try:
                    payload=request.post_data_json
                    if isinstance(payload,list) and payload and all(isinstance(x,dict) for x in payload):
                        methods=[x.get('methodname','') for x in payload]
                except Exception:pass
                # These exact Moodle RPCs read course/template state or resume
                # the editor's autosave session; none submits the course form.
                background=(request.method=='POST' and not request.is_navigation_request() and
                    parsed.path in {'/lib/ajax/service.php','/lib/ajax/service-nologin.php'} and
                    bool(methods) and all(isinstance(m,str) and m in {
                        'core_courseformat_get_state','core_output_load_template_with_dependencies',
                        'tiny_autosave_resume_session'} for m in methods))
                observation['background_requests' if background else 'write_requests']+=1
                if len(observation['request_metadata'])<30:
                    observation['request_metadata'].append({'path':parsed.path,'method':request.method,
                        'rpc_methods':[m for m in methods if isinstance(m,str) and re.fullmatch(r'[a-z0-9_]{1,100}',m)],
                        'background':background,'navigation':request.is_navigation_request()})
                persist()
            if matches(request):observation['post_requests']+=1;persist()
        def responded(response):
            if matches(response.request):observation['response_status']=response.status;persist()
        def loaded():
            if observation['post_requests'] and observation['response_status'] is not None:
                observation['completion']='response_loaded';persist();cleanup()
        def cleanup():
            for name,callback in [('request',requested),('response',responded),('domcontentloaded',loaded)]:
                page.remove_listener(name,callback)
        page.on('request',requested);page.on('response',responded);page.on('domcontentloaded',loaded)
        started=False
        try:
            page.evaluate('marker=>window.name=marker',observation['page_marker'])
            page.evaluate('''token=>{
              window.__courseAgentSubmitProbe?.cleanup?.();
              const probe={token,events:{pointerdown:0,mousedown:0,click:0,submit:0},document};
              const handler=e=>{probe.events[e.type]++;};
              for(const name of Object.keys(probe.events))window.addEventListener(name,handler,true);
              probe.cleanup=()=>{for(const name of Object.keys(probe.events))window.removeEventListener(name,handler,true);};
              window.__courseAgentSubmitProbe=probe;
            }''',observation['probe_token'])
            before_save()  # Durable intent immediately before the only possible click.
            started=True;persist()
            try:button.click(timeout=self.save_click_timeout_ms)
            except BrowserError as exc:
                from run_trace import RunTrace,environment_secrets
                observation['click_error_type']=type(exc).__name__
                observation['click_error']=RunTrace(Path('.'),environment_secrets(self.login_env)).clean(str(exc))[:5000]
                try:
                    probe=page.evaluate('''token=>{
                      const p=window.__courseAgentSubmitProbe;
                      return p?.token===token && p.document===document ?
                        {events:p.events,ready:document.readyState,hidden:document.hidden,focused:document.hasFocus()}:null;
                    }''',observation['probe_token'])
                except BrowserError:probe=None
                observation['dispatch_probe']=probe;persist()
                if (probe and probe['ready']=='complete' and not any(probe['events'].values()) and
                    not observation['post_requests'] and not observation['write_requests']):
                    observation['completion']='not_dispatched';persist()
                    raise MoodleSaveNotDispatched('The Save click timed out before any pointer/click/submit event or write request was observed. Returning to inspection and fresh approval; no Save replay.') from exc
            self.last_action['stage']='wait_save_response'
            deadline=time.monotonic()+self.submit_timeout_seconds
            while observation['completion']=='pending' and time.monotonic()<deadline:
                if not observation['post_requests']:
                    messages=self.form_errors()
                    if messages:
                        observation['completion']='client_rejected';persist()
                        raise MoodleFormRejected('Moodle rejected the form: '+'; '.join(messages)[:1500])
                page.wait_for_timeout(100)
            if observation['completion']=='pending':
                raise MoodleSubmissionUnconfirmed('The Moodle save response has not finished. Keeping its page open; no readback navigation or Save replay is allowed yet.')
        finally:
            # A late response can still complete while WhatsApp intake runs. Keep
            # its event observation alive, and never navigate this pending page.
            if not started:observation['completion']='not_submitted'
            if not started or observation['completion']!='pending':
                cleanup()
                try:page.evaluate('()=>window.__courseAgentSubmitProbe?.cleanup?.()')
                except BrowserError:pass

    def retire_unobserved_section_submit(self,operation,observation):
        """Explicit operator recovery for a legacy, uninstrumented rename click.

        Retire ONLY its marked agent tab, then let the caller independently read
        back. This never asserts the old save succeeded/failed and never clicks.
        """
        patches=operation.get('fields',[])
        captured=observation.get('dispatch_probe') or {}
        old_zero_events=(bool(observation.get('probe_token')) and 'background_requests' not in observation and
            captured.get('ready')=='complete' and captured.get('events')==
            {'pointerdown':0,'mousedown':0,'click':0,'submit':0})
        if (operation.get('capability')!='update_moodle_form' or operation.get('target_kind')!='section' or
            len(patches)!=1 or patches[0]['key']!='id_name' or
            observation.get('target')!=operation['target'] or observation.get('completion')!='pending' or
            observation.get('post_requests')!=0 or observation.get('response_status') is not None or
            observation.get('click_error_type')!='TimeoutError' or (observation.get('probe_token') and not old_zero_events)):
            raise ValueError('Only the exact legacy zero-POST section-name click can be retired; observed or instrumented submits stay protected.')
        marker=observation.get('page_marker')
        if not marker:raise ValueError('The original submit tab has no identity marker.')
        matches=[p for p in self.page.context.pages if urlparse(p.url).hostname==urlparse(course(operation['course_id']).origin).hostname and
                 p.evaluate('()=>window.name')==marker]
        if len(matches)!=1:raise ValueError('The exact original submit tab could not be identified.')
        source=matches[0];url=urlparse(source.url)
        if old_zero_events:
            still_undispatched=source.evaluate('''token=>{
              const p=window.__courseAgentSubmitProbe;
              return p?.token===token && p.document===document && document.readyState==='complete' &&
                ['pointerdown','mousedown','click','submit'].every(k=>p.events[k]===0);
            }''',observation['probe_token'])
            if not still_undispatched:raise ValueError('The original dispatch probe changed; do not retire its page.')
        if url.path!='/course/editsection.php' or parse_qs(url.query).get('id')!=[operation['target'].split(':')[-1]]:
            raise ValueError('The original tab is no longer the exact section editor.')
        course_identity(source,course(operation['course_id']))
        if source.evaluate('()=>document.readyState')!='complete' or source.locator('#id_name').input_value()!=patches[0]['value']:
            raise ValueError('The original section editor is not the observed prepared form.')
        if self.page==source:self.page=source.context.new_page()
        source.close(run_before_unload=False)
        self.submission_page=None
        self.submission_observation={**observation,'completion':'retired_unobserved_attempt',
            'recovery':'operator_retired_marked_legacy_section_tab; independent readback required'}
        return dict(self.submission_observation)

    def check_submission_settled(self,operation):
        self.refresh_editor_submission()
        evidence=self.submission_observation
        if evidence.get('target')==operation['target'] and evidence.get('completion')=='pending':
            raise MoodleSubmissionUnconfirmed('The original Moodle submit is still pending. Its page is preserved; no Save is replayed.')

    def refresh_editor_submission(self):
        evidence=self.submission_observation
        if evidence.get('kind')=='course_editor' and evidence.get('completion')=='pending':
            state=(self.submission_page or self.page).evaluate('()=>window.__courseAgentWrite')
            if state and state.get('done'):evidence['completion']='response_loaded'

    def rebind_pending_page(self,context):
        evidence=self.submission_observation
        if evidence.get('completion')!='pending':return False
        marker=evidence.get('page_marker')
        if not marker:raise MoodleSubmissionUnconfirmed('Cannot relocate the pending submit tab; preserving readback state.')
        matches=[]
        for page in context.pages:
            try:
                if page.evaluate('()=>window.name')==marker:matches.append(page)
            except BrowserError:continue
        if len(matches)!=1:raise MoodleSubmissionUnconfirmed('Pending submit tab is unavailable; no write is replayed.')
        self.page=self.submission_page=matches[0]
        return True

    def editor_action(self,operation,args,before_save):
        """One bounded built-in Moodle AJAX call, never arbitrary planner code."""
        allowed={'move_module':{'cm_move'},'set_section_visibility':{'section_show','section_hide'}}
        if args.get('action') not in allowed.get(operation.get('capability'),set()):raise ValueError('Unsupported course editor action.')
        if args.get('action') in {'section_show','section_hide'}:
            target=self.targets.get(operation['target'],{})
            if (target.get('kind')!='section' or target.get('course_id')!=operation['course_id'] or
                args.get('courseid')!=int(operation['course_id']) or args.get('ids')!=[int(target['id'])] or
                args['action']!='section_'+operation.get('params',{}).get('action','') or
                set(args)!={'action','courseid','ids'}):
                raise ValueError('Section action does not match its approved exact target.')
        selected=course(operation['course_id']);course_identity(self.page,selected)
        self.page.wait_for_function("()=>typeof window.require==='function' && !!window.M?.cfg?.sesskey",timeout=10000)
        observation={'target':operation['target'],'started':time.time(),'completion':'pending','kind':'course_editor',
                     'page_marker':'course-agent-submit-'+secrets.token_hex(12)}
        before_save()
        self.submission_observation=observation
        self.submission_page=self.page
        self.page.evaluate('marker=>window.name=marker',observation['page_marker'])
        # Start asynchronously: a slow response must not lead to a second write.
        self.page.evaluate("""args=>{
          window.__courseAgentWrite={done:false};
          require(['core/ajax'],ajax=>{
            ajax.call([{methodname:'core_courseformat_update_course',args}])[0]
              .then(()=>{window.__courseAgentWrite={done:true,ok:true}})
              .catch(()=>{window.__courseAgentWrite={done:true,ok:false}});
          },()=>{window.__courseAgentWrite={done:true,ok:false}});
        }""",args)
        deadline=time.monotonic()+self.submit_timeout_seconds
        while time.monotonic()<deadline:
            state=self.page.evaluate('()=>window.__courseAgentWrite')
            if state and state.get('done'):
                observation['completion']='response_loaded'
                if not state.get('ok'):raise RuntimeError('Moodle rejected the course editor action; checking the actual state before any further action.')
                return
            self.page.wait_for_timeout(100)
        raise MoodleSubmissionUnconfirmed('Moodle course editor response is pending; preserving its page without replay.')

    def verify(self, operation):
        """Read only. Also usable after a timeout; never resubmits the save."""
        self.check_submission_settled(operation)
        key = operation['target']
        if operation.get('target_kind')=='grade' and key not in self.targets:self.restore_grade(operation)
        if operation['target_kind'] in {'new_group','new_grouping'}:
            # Resolve new identity from fresh course inventory, not a success toast.
            before_ids = set(operation['existing_ids'])
            self.overview(operation['course_id'])
            kind = operation['target_kind'].removeprefix('new_')
            candidates = [k for k,v in self.targets.items() if k not in before_ids and
                          v['course_id'] == operation['course_id'] and v['kind'] == kind]
            matching=[]
            for candidate in candidates:
                observed=self.inspect(candidate)
                values={f['key']:f['value'] for f in observed['fields']}
                if all(values.get(p['key'])==p['value'] for p in operation['fields']):matching.append(candidate)
            if len(matching) != 1:
                raise RuntimeError("Saved creation could not be uniquely resolved in fresh readback.")
            key = matching[0]
        verified = self.inspect(key)
        if operation.get('target_kind')=='grade':
            from chrome_gradebook import validate_operation_identity
            validate_operation_identity(operation,verified)
        fields = {f['key']:f for f in verified['fields']}
        def matches(patch):
            actual = fields.get(patch['key'], {}).get('value')
            if operation['target_kind']=='grade' and patch['key']=='id_finalgrade':
                from chrome_gradebook import numeric_equal
                return numeric_equal(actual,patch['value'])
            if patch['key'] == 'id_availabilityconditionsjson' and actual:
                return availability_value(actual) == availability_value(patch['value'])
            if patch.get('rich') and isinstance(actual,str):return rich_value(actual)==rich_value(patch['value'])
            return actual == patch['value']
        expected_dates={row['label']:row for row in date_review(verified['fields'],operation['fields'])}
        mismatches = [p['label'] for p in operation['fields'] if not matches(p)]
        if mismatches:
            detail='; '.join(row['label']+': saved '+row['before']+', approved '+row['after'] for row in expected_dates.values())
            raise RuntimeError("Saved values did not match approved changes: " + (detail or ', '.join(mismatches)))
        return {'status':'Done','target':operation['title'],'verified':True,
                'change':operation['summary'], 'course_id':operation['course_id']}

    def unchanged_after_attempt(self,operation):
        """Fresh full observed-state comparison only; never infer no-save from a toast."""
        self.check_submission_settled(operation)
        if operation.get('target_kind') not in {'module','section','group','grouping','grade'}:return False
        if operation.get('target_kind')=='grade' and operation['target'] not in self.targets:self.restore_grade(operation)
        current=self.inspect(operation['target'])
        if operation.get('target_kind')=='grade':
            from chrome_gradebook import validate_operation_identity
            validate_operation_identity(operation,current)
        return current['fingerprint']==operation['fingerprint']


class MoodleFormRejected(RuntimeError):
    """Retain server validation evidence across the mandatory readback attempt."""
