"""Bounded existing date-restriction updates through Moodle's native form model.

The official date plugin stores exact epoch seconds on pluginNode; its selects
cannot represent every existing timestamp (five-minute options). Use that same
backing value and native update/validation, without rounding or changing rules.
This fixed executor accepts data only, never planner-supplied code or selectors.
"""
import json


def normalized(value):
    def clean(node):
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items() if k != 'nodeUID'}
        if isinstance(node, list):return [clean(v) for v in node]
        return node
    return clean(json.loads(value)) if value else {'op':'&','c':[],'showc':[]}


def validate_tree(before,after,targets,course_id):
    """Allow native date/group trees while preserving unknown plugin conditions."""
    old,new=normalized(before),normalized(after)
    def opaque(node):
        if isinstance(node,dict) and 'c' in node:
            return [item for child in node['c'] for item in opaque(child)]
        return [] if node.get('type') in {'date','group','grouping'} else [json.dumps(node,sort_keys=True)]
    def check(node,root=False,depth=0):
        if not isinstance(node,dict) or depth>8:raise ValueError('Availability tree is invalid or too deeply nested.')
        if 'c' in node:
            if node.get('op') not in {'&','|','!&','!|'} or not isinstance(node['c'],list) or len(node['c'])>50:
                raise ValueError('Availability list needs a supported operator and bounded conditions.')
            allowed={'op','c','showc','show'}
            if set(node)-allowed:raise ValueError('Unknown availability list properties.')
            if 'showc' in node and (not isinstance(node['showc'],list) or len(node['showc'])!=len(node['c']) or any(type(x)!=bool for x in node['showc'])):
                raise ValueError('Each availability condition needs a boolean visibility flag.')
            if 'show' in node and type(node['show'])!=bool:raise ValueError('Availability visibility must be boolean.')
            if root and not ({'show','showc'}&set(node)):raise ValueError('Root availability needs explicit visibility.')
            if not root and not node['c']:raise ValueError('Nested availability lists cannot be empty.')
            for child in node['c']:check(child,depth=depth+1)
        elif node.get('type')=='date':
            if set(node)!={'type','d','t'} or node['d'] not in {'>=','<'} or type(node['t'])!=int or not 0<node['t']<253402300800:
                raise ValueError('Availability dates need an exact supported comparator and timestamp.')
        elif node.get('type') in {'group','grouping'}:
            if set(node)!={'type','id'} or type(node['id'])!=int or not any(t['kind']==node['type'] and t['course_id']==course_id and t['id']==str(node['id']) for t in targets.values()):
                raise ValueError('Availability group must identify an observed group in this course.')
        elif json.dumps(node,sort_keys=True) not in opaque(old):
            raise ValueError('Adding this availability plugin is not supported.')
    check(new,root=True)
    if sorted(opaque(old))!=sorted(opaque(new)):
        raise ValueError('Preserve conditions belonging to unsupported availability plugins.')
    return new


def apply_tree(page,before,after):
    return page.evaluate("""({before,after})=>{
      const model=window.M?.core_availability;
      const form=model?.form;
      const clean=v=>Array.isArray(v)?v.map(clean):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).filter(k=>k!=='nodeUID').sort().map(k=>[k,clean(v[k])])):v;
      const equal=(a,b)=>JSON.stringify(clean(a))===JSON.stringify(clean(b));
      if(!form?.rootList||!model.List||!equal(form.rootList.getValue(),before))throw Error('Availability model changed.');
      const fresh=new model.List(structuredClone(after),true);
      const errors=[];fresh.fillErrors(errors);
      if(errors.length||!equal(fresh.getValue(),after))throw Error('The native availability model rejected this condition tree.');
      form.rootList.node.getDOMNode().replaceWith(fresh.node.getDOMNode());
      form.rootList=fresh;form.update();fresh.renumber();
      if(!equal(JSON.parse(form.field.get('value')),after))throw Error('Availability tree did not retain approved values.');
    }""",{'before':normalized(before),'after':normalized(after)})


def date_changes(before, after):
    old, new = normalized(before), normalized(after)
    changes = []
    def walk(a, b, path):
        if a == b:return
        if not isinstance(a, dict) or not isinstance(b, dict) or set(a) != set(b):
            raise ValueError('Date restriction edits must preserve the existing rule structure.')
        if a.get('type') == 'date':
            if (a.get('d') not in {'>=', '<'} or any(a[k] != b[k] for k in a if k != 't')
                    or type(b.get('t')) is not int or not 0 < b['t'] < 253402300800):
                raise ValueError('Only the exact timestamp of an existing date restriction may change.')
            changes.append({'path': path, 'before': a['t'], 'value': b['t']})
        elif 'c' in a and isinstance(a['c'], list) and isinstance(b['c'], list):
            if len(a['c']) != len(b['c']) or any(a[k] != b[k] for k in a if k != 'c'):
                raise ValueError('Preserve restriction operators, visibility, order and other conditions.')
            for i, (left, right) in enumerate(zip(a['c'], b['c'])):walk(left, right, path + [i])
        else:raise ValueError('This restriction edit needs a dedicated capability.')
    walk(old, new, [])
    if not changes:raise ValueError('No date restriction timestamp changed.')
    return changes


APPLY_DATES = r"""({before, after, changes}) => {
 const form = window.M?.core_availability?.form;
 const field = document.getElementById('id_availabilityconditionsjson');
 const clean = value => Array.isArray(value) ? value.map(clean) : value && typeof value === 'object'
   ? Object.fromEntries(Object.keys(value).filter(k=>k!=='nodeUID').sort().map(k=>[k,clean(value[k])])) : value;
 const equal = (a,b) => JSON.stringify(clean(a)) === JSON.stringify(clean(b));
 if (!form?.rootList?.getValue || !form.update || !field || !equal(JSON.parse(field.value),before)
     || !equal(form.rootList.getValue(),before)) throw Error('Date restriction model changed or is unavailable.');
 const nodes = changes.map(change => {
   let item = form.rootList;
   for (const index of change.path) item = item?.children?.[index];
   if (item?.pluginType !== 'date' || !item.pluginNode?.setData || !item.pluginNode?.getData
       || Number(item.pluginNode.getData('time')) !== change.before)
     throw Error('Exact date restriction node could not be verified.');
   return item.pluginNode;
 });
 // All identities checked before changing any local form state. No save here.
 nodes.forEach((node,i) => node.setData('time',changes[i].value));
 form.update();
 const actual = JSON.parse(field.value);
 if (!equal(actual,after) || !equal(form.rootList.getValue(),after))
   throw Error('Native date restriction validation did not match the approved change.');
 return field.value;
}"""


def apply_dates(page, before, after):
    changes = date_changes(before, after)
    return page.evaluate(APPLY_DATES, {'before': normalized(before), 'after': normalized(after), 'changes': changes})
