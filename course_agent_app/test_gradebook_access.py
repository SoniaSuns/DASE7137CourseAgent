"""Grades use observed identities and the existing approval/save boundary."""
import copy
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from unittest.mock import Mock

from playwright.sync_api import sync_playwright
from chrome_moodle import MoodleBrowser
from chrome_gradebook import register,unfiltered_participants,restore_grade
from chrome_approval import ChromeApproval


class GradebookIdentityTests(unittest.TestCase):
    def test_filtered_participant_count_does_not_establish_complete_roster(self):
        clean={'filters':{'courseid':{'values':[140147]}}}
        self.assertTrue(unfiltered_participants(clean,'140147'))
        self.assertFalse(unfiltered_participants(clean,'140148'))
        filtered=copy.deepcopy(clean);filtered['filters']['roles']={'values':[5]}
        self.assertFalse(unfiltered_participants(filtered,'140147'))
        self.assertFalse(unfiltered_participants(None,'140147'))

    def test_grade_cache_is_invalidated_when_course_refresh_discards_children(self):
        browser=MoodleBrowser(Mock());overview={'course_id':'140147'};register(browser,overview)
        old={**browser.targets,'140147:grade_item:7':{'kind':'grade_item','course_id':'140147'}}
        browser.forms={key:{'stale':True} for key in old}
        browser.reconcile_inventory_cache(old)
        self.assertEqual(browser.forms,{})

    def test_rediscovery_rejects_different_student_in_same_saved_grade_record(self):
        browser=Mock();browser.targets={}
        identity={'id':'9','courseid':'140147','itemid':'7','userid':'3','student_name':'Student One',
                  'student_email':'one@example.test','assessment_title':'Prototype'}
        browser.inspect.side_effect=[{'grade_items':[{'id':'7','title':'Prototype','key':'item'}]},
            {'students':[{'grade_target':'140147:grade:9','user_id':'4','name':'Student One','email':'one@example.test'}]}]
        with self.assertRaisesRegex(ValueError,'student grade'):
            restore_grade(browser,{'course_id':'140147','target':'140147:grade:9','grade_identity':identity})


class GradebookBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p=sync_playwright().start();cls.chrome=cls.p.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):cls.chrome.close();cls.p.stop()

    def setUp(self):
        self.context=self.chrome.new_context();self.page=self.context.new_page()
        self.browser=MoodleBrowser(self.page);self.writes=0;self.value='72.00';self.override=False
        self.maximum='100.00';self.userid='3';self.itemid='7';self.locked=False
        self.context.route('**/*',self.route)
        overview={'course_id':'140147'};register(self.browser,overview)
        self.catalog=self.browser.inspect(overview['gradebook_target'])
        self.grades=self.browser.inspect('140147:grade_item:7')
        self.key='140147:grade:9';self.browser.inspect(self.key)

    def tearDown(self):self.context.close()

    def route(self,route):
        request=route.request;url=urlparse(request.url);query=parse_qs(url.query)
        if url.path=='/grade/report/grader/index.php':
            second=query.get('page')==['1'];uid='4' if second else '3';gid='10' if second else '9'
            body=f'''<table id="user-grades"><tr><th data-itemid="7" class="grade_type_value"><a class="gradeitemheader" href="/mod/assign/view.php?id=11">Prototype</a><a href="/grade/report/singleview/index.php?id=140147&item=grade&itemid=7">Single</a></th></tr>
            <tr data-uid="{uid}"><th><a href="/user/view.php?id={uid}&course=140147"><span class="userinitials">SO</span>Student {uid}</a></th><td class="useremail">{uid}@example.test</td>
            <td data-itemid="7"><input name="grade[{uid}][7]" value="{self.value}"><a href="/grade/edit/tree/grade.php?courseid=140147&id={gid}">Edit</a></td></tr></table>
            <div class="pagination"><a href="/grade/report/grader/index.php?id=140147&page=1">2</a></div>'''
        elif url.path=='/grade/report/singleview/index.php':
            body=f'<table id="singleview-grades"><tr><td class="range">0.00–{self.maximum}</td></tr></table>'
        elif url.path=='/user/index.php':
            second=query.get('page')==['1'];uid='4' if second else '3'
            body=f'''<div id="region-main">2 participants found</div>
            <div data-table-filters='{{"filters":{{"courseid":{{"values":[140147]}}}}}}'></div>
            <table id="participants"><thead><tr><th>Select</th><th>Name</th><th>Email address</th><th>Roles</th><th>Groups</th></tr></thead><tbody>
            <tr><td><input class="usercheckbox" value=""></td><td><a href="/user/view.php?id={uid}&course=140147"><span class="userinitials">SO</span>Student {uid}</a></td><td>{uid}@example.test</td>
            <td><a class="quickeditlink">Student<span class="quickediticon">Edit roles</span></a></td>
            <td><a class="quickeditlink">Group 1<span class="quickediticon">Edit groups</span></a></td></tr><tr><td></td></tr></tbody></table>
            <div class="pagination"><a href="/user/index.php?id=140147&page=1">2</a></div>'''
        elif url.path=='/grade/edit/tree/grade.php':
            if request.method=='POST':
                values=parse_qs(request.post_data);self.writes+=1
                self.value=f'{float(values["finalgrade"][0]):.2f}';self.override='overridden' in values
            body=f'''<form class="mform" method="post">
            <input type="hidden" name="courseid" value="140147"><input type="hidden" name="id" value="9">
            <input type="hidden" name="itemid" value="{self.itemid}"><input type="hidden" name="userid" value="{self.userid}">
            <label for="id_overridden">Overridden</label><input type="checkbox" name="overridden" id="id_overridden" {'checked' if self.override else ''} onchange="document.getElementById('id_finalgrade').disabled=!this.checked">
            <label for="id_finalgrade">Final grade</label><input name="finalgrade" id="id_finalgrade" value="{self.value}" {'disabled' if not self.override else ''}>
            <input type="checkbox" name="locked" id="id_locked" {'checked' if self.locked else ''}>
            <input type="submit" name="submitbutton" id="id_submitbutton" value="Save"></form>'''
        else:return route.abort()
        route.fulfill(content_type='text/html; charset=utf-8',body='<meta charset="utf-8"><body class="course-140147">'+body+'</body>')

    def operation(self,score='80',override=True):
        fields=[{'key':'id_finalgrade','value':score}]
        if override:fields.append({'key':'id_overridden','value':True})
        return self.browser.resolve({'id':'score','capability':'update_moodle_form','target':self.key,
            'fields':fields,'scope':'requested','summary':'Set the verified group score','depends_on':[]})

    def test_multipage_grades_preserve_student_email_and_record_identity(self):
        self.assertEqual(self.grades['pages_read'],2)
        self.assertEqual([s['user_id'] for s in self.grades['students']],['3','4'])
        self.assertEqual(self.grades['students'][0]['name'],'Student 3')
        self.assertEqual(self.grades['students'][0]['email'],'3@example.test')
        self.assertEqual(self.grades['students'][0]['grade_target'],self.key)
        self.assertEqual(self.writes,0)

    def test_live_participant_rows_preserve_group_role_labels_and_skip_padding(self):
        people=self.browser.inspect('140147:participants:course')
        self.assertTrue(people['complete']);self.assertEqual(people['pages_read'],2)
        self.assertEqual([r['user_id'] for r in people['participants']],['3','4'])
        self.assertTrue(all(r['groups']=='Group 1' and r['roles']=='Student' for r in people['participants']))
        self.assertEqual(self.writes,0)

    def test_exact_approved_score_saves_once_then_numeric_readback_accepts_formatting(self):
        op=self.operation()
        self.assertEqual(op['fields'][0]['key'],'id_overridden')
        with tempfile.TemporaryDirectory() as temp:
            flow=ChromeApproval(Path(temp)/'state.json','fixture');flow.begin('Set group grade','staff','request')
            flow.offer({},[op]);flow.mark_sent();flow.approve('OK '+flow.state['code'],'staff','approval')
            result=self.browser.execute(op,lambda:flow.before_save(op))
            self.assertTrue(result['verified']);self.assertEqual(self.writes,1);self.assertEqual(self.value,'80.00')
            self.browser.targets.clear();self.browser.forms.clear()
            self.assertTrue(self.browser.verify(op)['verified']);self.assertEqual(self.writes,1)

    def test_no_approval_prevents_grade_post(self):
        with self.assertRaisesRegex(ValueError,'No approval'):
            self.browser.execute(self.operation(),Mock(side_effect=ValueError('No approval')))
        self.assertEqual(self.writes,0)

    def test_student_or_assessment_drift_rejected_before_save(self):
        for attribute in ('userid','itemid'):
            op=self.operation();old=getattr(self,attribute);setattr(self,attribute,'999')
            with self.assertRaisesRegex(ValueError,'exact course, assessment and student'):self.browser.preflight(op)
            setattr(self,attribute,old)
        self.assertEqual(self.writes,0)

    def test_grade_range_drift_invalidates_current_plan(self):
        op=self.operation();self.maximum='75.00'
        with self.assertRaisesRegex(ValueError,'approved form changed'):self.browser.preflight(op)
        self.assertEqual(self.writes,0)

    def test_rejected_rediscovery_cannot_verify_on_second_attempt_using_populated_cache(self):
        op=self.operation(score='72')
        op['grade_identity']['student_email']='different@example.test'
        self.browser.targets.clear();self.browser.forms.clear()
        with self.assertRaisesRegex(ValueError,'rediscovered'):self.browser.verify(op)
        self.assertIn(self.key,self.browser.targets)
        with self.assertRaisesRegex(ValueError,'immutable approved'):self.browser.verify(op)
        with self.assertRaisesRegex(ValueError,'immutable approved'):self.browser.preflight(op)
        self.assertEqual(self.writes,0)

    def test_invalid_scores_locked_or_non_numeric_items_and_missing_override_rejected(self):
        for score in ('NaN','Infinity','-1','101',''):
            with self.assertRaises(ValueError):self.operation(score)
        with self.assertRaisesRegex(ValueError,'Override'):self.operation(override=False)
        self.browser.targets[self.key]['numeric']=False
        with self.assertRaisesRegex(ValueError,'non-numeric'):self.operation()
        self.browser.targets[self.key]['numeric']=True;self.locked=True;self.browser.inspect(self.key)
        with self.assertRaisesRegex(ValueError,'locked'):self.operation()


if __name__=='__main__':unittest.main()
