"""Read observed gradebook identities; edit exact grades through normal mforms."""
from decimal import Decimal,InvalidOperation
import re
from urllib.parse import parse_qs,urlparse

from chrome_course_scope import course,course_identity


CATALOG_SCRIPT=r"""() => [...document.querySelectorAll('#user-grades th[data-itemid]')].map(h=>({
 id:h.dataset.itemid,title:h.querySelector('.gradeitemheader')?.textContent.trim()||'',
 numeric:h.classList.contains('grade_type_value'),
 activity_url:h.querySelector('.gradeitemheader[href]')?.href||'',
 single_url:h.querySelector('a[href*="/grade/report/singleview/"]')?.href||''
}))"""

ROWS_SCRIPT=r"""item => ({rows:[...document.querySelectorAll('#user-grades tr[data-uid]')].map(r=>{
 const c=r.querySelector('td[data-itemid="'+item+'"]');if(!c)return null;
 const input=c.querySelector('input[name^="grade["]');const clone=c.cloneNode(true);
 clone.querySelectorAll('.action-menu,label,.grade_icons,.accesshide,.visually-hidden').forEach(x=>x.remove());
 const name=r.querySelector('th a[href*="/user/"]')?.cloneNode(true);
 name?.querySelectorAll('.userinitials,.userpicture,img').forEach(x=>x.remove());
 return {user_id:r.dataset.uid,name:name?.textContent.trim()||'',
 email:r.querySelector('.useremail')?.textContent.trim()||'',
 value:input?input.value:(c.querySelector('.gradevalue')?.textContent||clone.textContent).trim(),
 edit_url:c.querySelector('a[href*="/grade/edit/tree/grade.php?"]')?.href||'',
 overridden:c.classList.contains('overridden'),excluded:c.classList.contains('excluded')};
 }).filter(Boolean),pages:[...document.querySelectorAll('.pagination a[href]')].map(x=>x.href)})"""


def register(browser,overview):
    cid=overview['course_id'];key=f'{cid}:gradebook:course'
    browser.targets[key]={'key':key,'id':cid,'course_id':cid,'kind':'gradebook','title':'Grades'}
    overview['gradebook_target']=key
    key=f'{cid}:participants:course'
    browser.targets[key]={'key':key,'id':cid,'course_id':cid,'kind':'participants','title':'Participants and groups'}
    overview['participants_target']=key


def participants(browser,target):
    selected=course(target['course_id']);pending={0};visited=set();rows={};expected=None
    script=r"""() => {
      const clean=e=>{if(!e)return '';const x=e.cloneNode(true);x.querySelectorAll('.quickediticon,.userinitials,img,.accesshide,.visually-hidden').forEach(n=>n.remove());return x.textContent.replace(/\s+/g,' ').trim()};
      const memberships=e=>{const w=e?.querySelector('[data-itemtype="user_groups"]');if(!w)return null;
        const ids=JSON.parse(w.dataset.value||'[]').map(String),options=JSON.parse(w.dataset.options||'{}').options||[];
        return {ids,names:ids.map(id=>options.find(o=>String(o.key)===id)?.value||'')};};
      const table=document.querySelector('#participants');
      const headers=[...table.querySelectorAll('thead th')].map(x=>x.textContent.trim());
      const email=headers.findIndex(x=>/email/i.test(x)),groups=headers.findIndex(x=>/^groups$/i.test(x)),roles=headers.findIndex(x=>/^roles$/i.test(x));
      if(email<0||groups<0||roles<0)throw new Error('Participant columns could not be identified');
      return {rows:[...table.querySelectorAll('tbody tr')].map(r=>{
        const a=r.querySelector('a[href*="/user/"]');const id=a?new URL(a.href).searchParams.get('id'):null;
        if(!a||!id)return null;
        return {user_id:id,name:clean(a),email:clean(r.children[email]),groups:clean(r.children[groups]),group_membership:memberships(r.children[groups]),roles:clean(r.children[roles])};
      }).filter(Boolean),filters:JSON.parse(document.querySelector('[data-table-filters]')?.getAttribute('data-table-filters')||'null'),
      pages:[...document.querySelectorAll('.pagination a[href]')].map(x=>x.href),
      total:(document.querySelector('#region-main')?.innerText||'').match(/([\d,]+) participants? found/i)?.[1]||null};
    }"""
    while pending:
        number=min(pending);pending.remove(number)
        if number in visited:continue
        if len(visited)>=100:raise ValueError('Participant pagination is incomplete.')
        visited.add(number)
        browser.goto(selected,selected.url_for('/user/index.php',{'id':selected.id,'perpage':200,'page':number,'tifirst':'','tilast':''}))
        browser.page.wait_for_function("document.readyState==='complete' && !window.M?.util?.pending_js?.length")
        browser.page.locator('#participants').wait_for(state='attached')
        data=browser.page.evaluate(script)
        if not unfiltered_participants(data.get('filters'),selected.id):
            reset=browser.page.locator('[data-filteraction="reset"]')
            if reset.count()!=1:raise ValueError('Participant filters are active or unavailable; cannot establish a complete roster.')
            reset.click()
            browser.page.wait_for_function(r"""cid=>{
              const f=JSON.parse(document.querySelector('[data-table-filters]')?.getAttribute('data-table-filters')||'null')?.filters;
              return f && Object.keys(f).length===1 && f.courseid?.values?.length===1 && String(f.courseid.values[0])===cid;
            }""",arg=selected.id,timeout=10000)
            data=browser.page.evaluate(script)
            if not unfiltered_participants(data.get('filters'),selected.id):raise ValueError('Participant filters could not be cleared.')
        if data['total'] is not None:
            total=int(data['total'].replace(',',''))
            if expected is not None and total!=expected:raise ValueError('Participant count changed during inspection.')
            expected=total
        for row in data['rows']:
            if not re.fullmatch(r'[1-9]\d*',row['user_id']) or row['user_id'] in rows:raise ValueError('Participant identities repeated or are unavailable.')
            rows[row['user_id']]=row
        for url in data['pages']:
            query=checked_query(url,selected,'/user/index.php',{'id':selected.id})
            page=query.get('page',['0'])[0]
            if not page.isdigit():raise ValueError('Invalid participant pagination.')
            if int(page) not in visited:pending.add(int(page))
    if expected is not None and len(rows)!=expected:raise ValueError('Participant table is filtered or incomplete; do not claim all members were checked.')
    result={'target':target['key'],'course_id':selected.id,'title':target['title'],'fields':[],'submit':[],
        'read_only':True,'participants':list(rows.values()),'complete':expected is not None,'pages_read':len(visited)}
    browser.forms[target['key']]=result
    return result


def unfiltered_participants(value,cid):
    if not isinstance(value,dict) or not isinstance(value.get('filters'),dict):return False
    filters=value['filters']
    return set(filters)=={'courseid'} and [str(v) for v in filters['courseid'].get('values',[])]==[cid]


def restore_grade(browser,operation):
    """Rediscover saved identities from current read-only reports after restart."""
    identity=operation.get('grade_identity')
    if not identity:raise ValueError('Historical grade operation lacks exact assessment/student evidence; re-plan before any write.')
    cid=operation['course_id'];key=operation['target']
    if key!=f'{cid}:grade:{identity.get("id")}' or identity.get('courseid')!=cid:raise ValueError('Historical grade identity does not match its operation.')
    overview={'course_id':cid};register(browser,overview)
    catalog=browser.inspect(overview['gradebook_target'])
    items=[x for x in catalog['grade_items'] if x['id']==identity.get('itemid') and x['title']==identity.get('assessment_title')]
    if len(items)!=1:raise ValueError('The original assessment is absent or changed; do not retarget the grade.')
    grades=browser.inspect(items[0]['key'])
    matches=[x for x in grades['students'] if x['grade_target']==key and x['user_id']==identity.get('userid')
             and x['name']==identity.get('student_name') and x['email']==identity.get('student_email')]
    if len(matches)!=1:raise ValueError('The original student grade cannot be uniquely rediscovered.')


def checked_query(url,selected,path,expected):
    selected.check_origin(url)
    if urlparse(url).path!=path:raise ValueError('Gradebook link has an unexpected endpoint.')
    query=parse_qs(urlparse(url).query)
    if any(query.get(k)!=[v] for k,v in expected.items()):raise ValueError('Gradebook link identity changed.')
    return query


def read_range(browser,selected,item):
    browser.goto(selected,selected.url_for('/grade/report/singleview/index.php',{'id':selected.id,'item':'grade','itemid':item,'group':0}))
    checked_query(browser.page.url,selected,'/grade/report/singleview/index.php',{'id':selected.id,'item':'grade','itemid':item})
    browser.page.locator('#singleview-grades').wait_for(state='attached')
    ranges=browser.page.locator('#singleview-grades td.range').all_text_contents()
    parsed=[]
    for text in ranges:
        match=re.fullmatch(r'\s*([+-]?\d+(?:\.\d+)?)\s*[–−-]\s*([+-]?\d+(?:\.\d+)?)\s*',text)
        if match:parsed.append(match.groups())
    return parsed[0] if parsed and len(parsed)==len(ranges) and len(set(parsed))==1 else None


def inspect(browser,target):
    selected=course(target['course_id'])
    browser.last_action={'stage':'read_gradebook','target':target['key'],'course_id':selected.id,'title':target['title']}
    result={'target':target['key'],'course_id':selected.id,'title':target['title'],
            'fields':[],'submit':[],'read_only':True}
    if target['kind']=='gradebook':
        browser.goto(selected,selected.url_for('/grade/report/grader/index.php',{'id':selected.id,'page':0,'group':0,'sifirst':'','silast':''}))
        browser.page.locator('#user-grades').wait_for(state='attached')
        items=[];seen=set()
        for row in browser.page.evaluate(CATALOG_SCRIPT):
            if not row['single_url']:continue  # Course/category totals are not assessment edits.
            if not re.fullmatch(r'[1-9]\d*',row['id']) or not row['title'] or row['id'] in seen:continue
            seen.add(row['id']);key=f'{selected.id}:grade_item:{row["id"]}'
            checked_query(row['single_url'],selected,'/grade/report/singleview/index.php',{'id':selected.id,'item':'grade','itemid':row['id']})
            module=''
            if row['activity_url']:
                selected.check_origin(row['activity_url']);query=parse_qs(urlparse(row['activity_url']).query)
                if urlparse(row['activity_url']).path.startswith('/mod/') and re.fullmatch(r'[1-9]\d*',query.get('id',[''])[0]):
                    module=f'{selected.id}:module:{query["id"][0]}'
            item={'key':key,'id':row['id'],'kind':'grade_item','course_id':selected.id,
                  'title':row['title'],'numeric':row['numeric'],'module_target':module}
            browser.targets[key]=item;items.append(item)
        if not items:raise ValueError('The gradebook returned no identifiable assessment items.')
        result['grade_items']=items
    else:
        # Read only the selected assessment, following observed pagination links.
        item=target['id'];pending={0};visited=set();students={}
        while pending:
            page_number=min(pending);pending.remove(page_number)
            if page_number in visited:continue
            if len(visited)>=100:raise ValueError('Gradebook pagination exceeds this inspection; no complete-grade claim is permitted.')
            visited.add(page_number)
            browser.goto(selected,selected.url_for('/grade/report/grader/index.php',{'id':selected.id,'page':page_number,'group':0,'sifirst':'','silast':''}))
            browser.page.locator('#user-grades').wait_for(state='attached')
            catalog=browser.page.evaluate(CATALOG_SCRIPT)
            if not any(r['id']==item and r['title']==target['title'] for r in catalog):raise ValueError('Assessment identity changed during grade inspection.')
            page=browser.page.evaluate(ROWS_SCRIPT,item)
            for row in page['rows']:
                uid=row['user_id']
                if not re.fullmatch(r'[1-9]\d*',uid) or not row['name']:raise ValueError('A grade row has no reliable student identity.')
                if uid in students:raise ValueError('Gradebook pagination repeated a student; inspect again before claiming complete coverage.')
                row['grade_target']=''
                if row['edit_url']:
                    query=checked_query(row['edit_url'],selected,'/grade/edit/tree/grade.php',{'courseid':selected.id})
                    gid=query.get('id',[''])[0]
                    if not re.fullmatch(r'[1-9]\d*',gid):raise ValueError('Grade record identity is unavailable.')
                    key=f'{selected.id}:grade:{gid}'
                    browser.targets[key]={'key':key,'id':gid,'kind':'grade','course_id':selected.id,
                        'item_id':item,'user_id':uid,'student_name':row['name'],'student_email':row['email'],
                        'assessment_title':target['title'],'title':target['title']+' — '+row['name'],
                        'numeric':target['numeric'],
                        'edit_url':selected.url_for('/grade/edit/tree/grade.php',{'courseid':selected.id,'id':gid})}
                    row['grade_target']=key
                row.pop('edit_url');students[uid]=row
            for url in page['pages']:
                query=checked_query(url,selected,'/grade/report/grader/index.php',{'id':selected.id})
                number=query.get('page',['0'])[0]
                if not number.isdigit():raise ValueError('Invalid gradebook pagination.')
                if int(number) not in visited:pending.add(int(number))
        result.update(assessment_id=item,numeric=target['numeric'],students=list(students.values()),
                      pages_read=len(visited),complete=True)
        bounds=read_range(browser,selected,item)
        result['grade_range']=bounds
        for row in students.values():
            if row['grade_target']:browser.targets[row['grade_target']]['grade_range']=bounds
    browser.forms[target['key']]=result
    return result


def bind_form(page,target,result,identity):
    expected={'courseid':target['course_id'],'id':target['id'],'itemid':target['item_id'],'userid':target['user_id']}
    if any(identity.get(k)!=v for k,v in expected.items()):raise ValueError('Grade form does not identify the exact course, assessment and student.')
    checked_query(page.url,course(target['course_id']),'/grade/edit/tree/grade.php',{'courseid':target['course_id'],'id':target['id']})
    result['grade_identity']={**expected,'student_name':target['student_name'],'student_email':target['student_email'],
                              'assessment_title':target['assessment_title'],'numeric':target['numeric'],
                              'grade_range':target.get('grade_range')}
    for field in result['fields']:
        if field['key']=='id_overridden':field['label']='Override activity grade (gradebook takes precedence)'


def validate_patch(target,snapshot,patches):
    if not target['numeric']:raise ValueError('This assessment uses a non-numeric scale; numeric grade changes are not valid.')
    values={f['key']:f['value'] for f in snapshot['fields']}
    proposed={p['key']:p['value'] for p in patches}
    if set(proposed)-{'id_finalgrade','id_overridden'}:raise ValueError('Score editing preserves feedback, exclusion, visibility and locks.')
    if 'id_finalgrade' not in proposed:raise ValueError('A grade operation must include the exact new score.')
    try:
        number=Decimal(proposed['id_finalgrade'])
        if not number.is_finite() or number<0:raise InvalidOperation()
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Grade must be a finite non-negative numeric score; do not clear it implicitly.') from None
    bounds=target.get('grade_range')
    if not bounds or not Decimal(bounds[0])<=number<=Decimal(bounds[1]):
        raise ValueError('The score must fit the observed assessment range; inspect its range before planning.')
    if proposed.get('id_overridden',values.get('id_overridden')) is not True:
        raise ValueError('Gradebook editing needs an explicitly approved Override activity grade setting.')
    if values.get('id_locked') is True:raise ValueError('The grade is locked; do not silently unlock it to change scores.')
    # Enabling override is an explicit first patch, never an unapproved side effect.
    return sorted(patches,key=lambda p:0 if p['key']=='id_overridden' else 1)


def numeric_equal(left,right):
    try:return Decimal(str(left)).is_finite() and Decimal(str(left))==Decimal(str(right))
    except (InvalidOperation,ValueError,TypeError):return False


def validate_operation_identity(operation,snapshot):
    expected=operation.get('grade_identity',{});observed=snapshot.get('grade_identity',{})
    keys=('courseid','id','itemid','userid','student_name','student_email','assessment_title','numeric')
    if any(k not in expected or k not in observed or expected[k]!=observed[k] for k in keys):
        raise ValueError('The grade no longer matches the immutable approved student and assessment identity.')
