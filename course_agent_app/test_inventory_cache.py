import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from chrome_moodle import MoodleBrowser
from run_shared_chrome_agent import Runtime


class InventoryCacheTests(unittest.TestCase):
    def test_successful_layout_refresh_discards_only_disappeared_target_forms(self):
        page=Mock();page.locator.return_value.count.return_value=0
        browser=MoodleBrowser(page);browser.goto=Mock()
        gone='140147:module:2';keep='140147:module:1';other='140148:module:2'
        browser.targets={gone:{'course_id':'140147','kind':'module','title':'Removed'},
                         keep:{'course_id':'140147','kind':'module','title':'Kept'},
                         other:{'course_id':'140148','kind':'module','title':'Other course'}}
        browser.forms={gone:{'fields':['old']},keep:{'fields':['keep']},other:{'fields':['other']}}
        raw={'title':'Class A','sections':[{'id':'section-1','dbid':'12','number':'1','title':'Week 1','visibility':'shown',
             'modules':[{'id':'1','title':'Kept','text':'Kept','module_type':'resource'}]}]}
        page.evaluate.return_value=raw
        browser._layout_once('140147')
        self.assertNotIn(gone,browser.forms)
        self.assertIn(keep,browser.forms);self.assertIn(other,browser.forms)
        self.assertEqual(browser.unavailable_targets[gone]['title'],'Removed')
        self.assertEqual(len(browser.overviews['140147']['modules']),1)

    def test_reappeared_identity_clears_notice_without_restoring_old_form(self):
        browser=MoodleBrowser(Mock());old={'a:module:1':{'kind':'module','course_id':'a','title':'Old'}}
        browser.forms={'a:module:1':{'old':True}};browser.reconcile_inventory_cache(old)
        browser.targets=copy.deepcopy(old);browser.reconcile_inventory_cache(old)
        self.assertFalse(browser.unavailable_targets);self.assertFalse(browser.forms)

    def test_empty_failed_read_keeps_previous_inventory_and_forms(self):
        page=Mock();page.locator.return_value.count.return_value=0
        browser=MoodleBrowser(page);browser.goto=Mock()
        browser.targets={'140147:module:1':{'kind':'module','course_id':'140147','title':'Old'}}
        browser.forms={'140147:module:1':{'old':True}};page.evaluate.return_value={'sections':[]}
        with self.assertRaisesRegex(ValueError,'empty'):browser._layout_once('140147')
        self.assertIn('140147:module:1',browser.forms);self.assertIn('140147:module:1',browser.targets)
        self.assertFalse(browser.unavailable_targets)

    def test_actual_59_to_58_refresh_excludes_menti_form_from_planner_context(self):
        data=json.loads((Path(__file__).parent/'eval_fixtures/inventory_grounding.json').read_text(encoding='utf-8'))
        browser=MoodleBrowser(Mock());browser.targets=data['after_targets'];browser.overviews={}
        browser.forms={key:{'target':key,'title':data['before_targets'][key]['title'],'fields':[]} for key in data['form_keys']}
        browser.reconcile_inventory_cache(data['before_targets'])
        missing='140147:module:4254911'
        self.assertNotIn(missing,browser.forms)
        self.assertIn(missing,browser.unavailable_targets)
        with tempfile.TemporaryDirectory() as tmp:
            runtime=Runtime(Mock(),browser,['chat'],[],Mock(),Path(tmp),Mock())
            flow=runtime.flows['chat'];flow.begin('Align the course','staff','msg')
            context=runtime.context(flow)
            self.assertNotIn(missing,{f['target'] for f in context['forms']})
            self.assertIn(missing,{x['target'] for x in context['unavailable_targets']})
            with self.assertRaisesRegex(ValueError,'absent from the current course inventory'):
                runtime.ensure_plan_evidence('chat',data['stale_plan'],{'rounds':1,'repairs':0})

