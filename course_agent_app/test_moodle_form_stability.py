import copy
import json
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs

from chrome_moodle import MoodleBrowser, form_fingerprint, stable_form, reveal_form_field
from chrome_availability import date_changes, apply_dates, normalized
from test_shared_chrome_agent import BrowserIntegrationTests


class FingerprintTests(unittest.TestCase):
    def snapshot(self):
        return {'fields': [
            {'key':'enabled','name':'completionexpected[enabled]','type':'checkbox','value':False,'disabled':False},
            {'key':'minute','name':'completionexpected[minute]','type':'select-one','value':'10','disabled':True},
            {'key':'id_availabilityconditionsjson','name':'availabilityconditionsjson','type':'textarea','disabled':False,
             'value':json.dumps({'op':'&','c':[{'type':'date','d':'>=','t':1700000035,'nodeUID':1}],'showc':[True]})}]}

    def test_inactive_date_clock_and_client_node_id_do_not_change_fingerprint(self):
        before=self.snapshot();after=copy.deepcopy(before)
        after['fields'][1]['value']='11'
        rule=json.loads(after['fields'][2]['value']);rule['c'][0]['nodeUID']=2
        after['fields'][2]['value']=json.dumps(rule)
        self.assertEqual(form_fingerprint(before),form_fingerprint(after))
        self.assertEqual(before['fields'][1]['value'],'10')

    def test_real_changes_still_invalidate_approval(self):
        before=self.snapshot()
        for change in ('enabled','disabled','timestamp','visibility','direction','newfield'):
            with self.subTest(change=change):
                after=copy.deepcopy(before);rule=json.loads(after['fields'][2]['value'])
                if change=='enabled':after['fields'][0]['value']=True
                elif change=='disabled':after['fields'][1]['disabled']=False
                elif change=='newfield':after['fields'].append({'key':'new','name':'new','value':'x','disabled':False})
                elif change=='timestamp':rule['c'][0]['t']+=60
                elif change=='visibility':rule['showc'][0]=False
                else:rule['c'][0]['d']='<'
                after['fields'][2]['value']=json.dumps(rule)
                self.assertNotEqual(form_fingerprint(before),form_fingerprint(after))
        before['fields'][0]['value']=True
        after=copy.deepcopy(before);after['fields'][1]['value']='11'
        self.assertNotEqual(form_fingerprint(before),form_fingerprint(after))

    def test_disabled_non_date_values_are_not_ignored(self):
        before=self.snapshot();before['fields'][1]['name']='groupingid'
        after=copy.deepcopy(before);after['fields'][1]['value']='20'
        self.assertNotEqual(form_fingerprint(before),form_fingerprint(after))

    def test_date_only_edit_rejects_condition_removal_or_visibility_change(self):
        before=self.snapshot()['fields'][2]['value'];original=json.loads(before)
        for change in ('remove','visibility','direction','string'):
            after=copy.deepcopy(original)
            if change=='remove':after['c']=[]
            elif change=='visibility':after['showc']=[False]
            elif change=='direction':after['c'][0]['d']='<'
            else:after['c'][0]['t']='1700000060'
            with self.assertRaises(ValueError):date_changes(before,json.dumps(after))


class StableDOMTests(BrowserIntegrationTests):
    # Reuse the real-browser local fixture; never connect to production here.
    def test_waits_for_moodle_initializers_before_snapshot(self):
        self.page.set_content('''<form class="mform"><input id="date" name="date" value="loading">
          <input id="save" type="submit" name="submitbutton"></form>
          <script>window.M={util:{pending_js:['editor']}};
          setTimeout(()=>{document.getElementById('date').value='ready';M.util.pending_js=[]},500)</script>''')
        self.assertEqual(stable_form(self.page)['fields'][0]['value'],'ready')

    def test_real_moodle_collapsed_structure_is_expanded_before_edit(self):
        def collapsed_route(route):
            # Same form with a Bootstrap 5 fieldset, as observed on live Moodle.
            from urllib.parse import parse_qs
            if route.request.method=='POST':
                self.writes+=1;self.value=parse_qs(route.request.post_data)['duedate[day]'][0]
            route.fulfill(content_type='text/html',body=f'''<body class="course-140148">
              <style>.collapse:not(.show){{display:none}}</style><form class="mform" method="post">
              <input type="hidden" name="course" value="140148"><input type="hidden" name="update" value="7">
              <fieldset class="collapsible collapsed"><a role="button" data-bs-toggle="collapse"
                href="#dates" aria-expanded="false" aria-controls="dates" onclick="this.setAttribute('aria-expanded','true');document.getElementById('dates').classList.add('show')">Date settings</a>
              <div id="dates" class="collapse"><select id="id_due_day" name="duedate[day]">
              <option value="10" {'selected' if self.value=='10' else ''}>10</option>
              <option value="20" {'selected' if self.value=='20' else ''}>20</option></select></div></fieldset>
              <input type="submit" id="save" name="submitbutton" value="Save"></form>''')
        self.page.unroute('**/*');self.page.route('**/*',collapsed_route)
        op=self.resolved()
        self.assertFalse(self.page.locator('#id_due_day').is_visible())
        result=self.browser.execute(op,Mock())
        self.assertTrue(result['verified']);self.assertEqual(self.writes,1)

    def test_bootstrap4_nested_controls_and_no_arbitrary_button_clicks(self):
        self.page.set_content('''<style>.collapse:not(.show){display:none}</style><form class="mform">
          <button type="submit" onclick="window.saved=true">Save</button>
          <a href="#outer" data-toggle="collapse" aria-controls="outer" aria-expanded="false" onclick="this.setAttribute('aria-expanded','true');document.getElementById('outer').classList.add('show')">Expand</a>
          <div class="collapse" id="outer"><details><summary>Inner</summary><input id="target"></details></div>
          </form>''')
        reveal_form_field(self.page,self.page.locator('#target'))
        self.assertTrue(self.page.locator('#target').is_visible())
        self.assertIsNone(self.page.evaluate('window.saved'))

    def test_ambiguous_expand_control_is_rejected_without_save(self):
        self.page.set_content('''<style>.collapse{display:none}</style><form class="mform">
          <a data-bs-toggle="collapse" aria-controls="dates">One</a>
          <a data-bs-toggle="collapse" aria-controls="dates">Two</a>
          <div class="collapse" id="dates"><input id="target"></div></form>''')
        with self.assertRaisesRegex(ValueError,'unique expand'):
            reveal_form_field(self.page,self.page.locator('#target'))

    def test_native_date_model_keeps_exact_seconds_and_preserves_other_rules(self):
        before={'op':'&','c':[{'type':'date','d':'>=','t':1700000035,'nodeUID':4},
                             {'type':'group','id':7}],'showc':[True,False]}
        self.page.set_content('<textarea id="id_availabilityconditionsjson" hidden></textarea>')
        self.page.evaluate('''value=>{
          const field=document.getElementById('id_availabilityconditionsjson');field.value=JSON.stringify(value);
          const node={time:value.c[0].t,setData(k,v){this[k]=v},getData(k){return this[k]}};
          const root={children:[{pluginType:'date',pluginNode:node},{pluginType:'group'}],
            getValue(){return {...value,c:[{...value.c[0],t:node.time},value.c[1]]}}};
          window.M={core_availability:{form:{rootList:root,update(){field.value=JSON.stringify(root.getValue())}}}};
        }''',before)
        after=copy.deepcopy(before);after['c'][0]['t']+=86400;after['c'][0]['nodeUID']=999
        result=apply_dates(self.page,json.dumps(before),json.dumps(after))
        self.assertEqual(normalized(result),normalized(json.dumps(after)))
        self.assertEqual(json.loads(result)['c'][0]['t']%60,before['c'][0]['t']%60)
        self.assertEqual(self.writes,0)
        with self.assertRaises(Exception):apply_dates(self.page,json.dumps(before),json.dumps(after))


class AvailabilitySaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.p=sync_playwright().start();cls.chrome=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.chrome.close();cls.p.stop()

    def setUp(self):
        self.context=self.chrome.new_context();self.page=self.context.new_page();self.writes=0;self.loads=0
        self.rule={'op':'&','c':[{'type':'date','d':'>=','t':1700000035}],'showc':[True]}
        self.page.route('**/*',self.route)
        self.browser=MoodleBrowser(self.page)
        self.browser.targets['140148:module:7']={'key':'140148:module:7','course_id':'140148',
            'id':'7','kind':'module','title':'Materials','edit_url':'https://moodle.hku.hk/course/modedit.php?update=7'}

    def tearDown(self):self.context.close()

    def route(self,route):
        self.loads+=1
        if route.request.method=='POST':
            self.writes+=1
            self.rule=normalized(parse_qs(route.request.post_data)['availabilityconditionsjson'][0])
        rule=copy.deepcopy(self.rule);rule['c'][0]['nodeUID']=self.loads
        html='''<body class="course-140148"><form class="mform" method="post">
          <input type="hidden" name="course" value="140148"><input type="hidden" name="update" value="7">
          <textarea hidden id="id_availabilityconditionsjson" name="availabilityconditionsjson"></textarea>
          <input id="id_submitbutton" type="submit" name="submitbutton" value="Save"></form>
          <script>
          const value=RULE,field=document.getElementById('id_availabilityconditionsjson');field.value=JSON.stringify(value);
          const node={time:value.c[0].t,setData(k,v){this[k]=v},getData(k){return this[k]}};
          const root={children:[{pluginType:'date',pluginNode:node}],
            getValue(){return {...value,c:[{...value.c[0],t:node.time}]}}};
          window.M={core_availability:{form:{rootList:root,update(){field.value=JSON.stringify(root.getValue())}}}};
          </script>'''.replace('RULE',json.dumps(rule))
        route.fulfill(status=200,content_type='text/html',body=html)

    def operation(self):
        self.browser.inspect('140148:module:7');updated=copy.deepcopy(self.rule);updated['c'][0]['t']+=86400
        return self.browser.resolve({'capability':'update_moodle_form','target':'140148:module:7',
            'summary':'Move release date','fields':[{'key':'id_availabilityconditionsjson','value':json.dumps(updated)}]})

    def test_exact_timestamp_save_once_and_fresh_readback_with_new_node_ids(self):
        op=self.operation();guard=Mock()
        result=self.browser.execute(op,guard)
        self.assertTrue(result['verified']);self.assertEqual(self.writes,1);guard.assert_called_once()
        self.assertEqual(self.rule['c'][0]['t'],1700000035+86400)

    def test_no_permission_means_no_post(self):
        op=self.operation()
        with self.assertRaises(ValueError):self.browser.execute(op,Mock(side_effect=ValueError('No current approval')))
        self.assertEqual(self.writes,0)

    def test_real_restriction_drift_stops_before_save(self):
        op=self.operation();self.rule['c'][0]['t']+=60;guard=Mock()
        with self.assertRaises(ValueError):self.browser.execute(op,guard)
        self.assertEqual(self.writes,0);guard.assert_not_called()


if __name__=='__main__':unittest.main()
