"""The three reporting sources must not become three reports for one file."""
import copy
import unittest
from chrome_deferred_items import report_items


class DeferredReportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.targets={cid+':module:'+mid:{'key':cid+':module:'+mid,'course_id':cid,
            'kind':'module','title':'Reference Video  URL'}
            for cid,mid in [('140147','1'),('140148','2')]}
        self.reason='Video topic is unknown. Leave untouched.'
        self.state={'schedule_contract':{'items':[
            {'target':k,'disposition':'unresolved','reason':self.reason} for k in self.targets]},
            'goal_unresolved':[
                'Class A Reference Video — The linked video topic is unknown. A new task needs its content.',
                'Class B Reference Video — The linked video topic is unknown. A new task needs its content.',
                'Reference Video  URL: '+self.reason,self.reason]}

    def test_three_sources_group_by_identity_without_merging_classes(self):
        before=copy.deepcopy(self.state);rows=report_items(self.state,self.targets)
        self.assertEqual(self.state,before)
        self.assertEqual(len(rows),2)
        self.assertEqual({r['target'] for r in rows},set(self.targets))
        for row in rows:
            self.assertEqual(len(row['details']),3)
            self.assertIn('The linked video topic is unknown.',row['summary'])
            self.assertIn('A new task needs its content.',row['summary'])
            self.assertEqual(row['summary'].count(self.reason),1)

    def test_distinct_named_issues_stay_in_one_file_entry(self):
        self.state['goal_unresolved'].append('Class A Reference Video — Release date is unconfirmed. Ask for a date.')
        rows=report_items(self.state,self.targets)
        self.assertEqual(len(rows),2)
        self.assertIn('Release date is unconfirmed.',rows[0]['summary'])

    def test_unknown_or_ambiguous_identity_is_not_silently_dropped(self):
        self.state['goal_unresolved']+=['Reference Video: another problem without a class.',
                                      'Class B historical Missing Slides — Exact activity is absent. Do not recreate.']
        rows=report_items(self.state,self.targets)
        self.assertEqual(len(rows),4)
        self.assertEqual(sum(not r['target'] for r in rows),2)
        self.assertTrue(any('Missing Slides' in r['summary'] for r in rows))

    def test_title_mentioned_inside_reason_cannot_bind_a_different_file(self):
        self.state['goal_unresolved'].append('General issue — Class A Reference Video needs a related syllabus.')
        rows=report_items(self.state,self.targets)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[-1]['target'],'')

    def test_title_prefixed_reason_only_binds_its_exact_title(self):
        self.targets['140148:module:2']['title']='Different Video  URL'
        self.state['goal_unresolved']=['Reference Video  URL: '+self.reason]
        rows=report_items(self.state,self.targets)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['target'],'140147:module:1')

    def test_qualifying_sentence_and_distinct_review_issue_are_preserved(self):
        self.state['goal_unresolved']=[
            'Class A Reference Video — The deadline is 1 November. However, sources conflict; confirm 1 or 8 November.',
            'Reference Video  URL: '+self.reason]
        rows=report_items(self.state,self.targets)
        self.assertIn('However, sources conflict; confirm 1 or 8 November.',rows[0]['summary'])
        self.assertIn(self.reason,rows[0]['summary'])

    def test_title_inside_factual_clause_before_colon_is_not_a_header(self):
        reason='Reference Video conflicts with the selected syllabus: confirm which source is authoritative.'
        self.state['schedule_contract']['items'][0]['reason']=reason
        self.state['goal_unresolved']=[reason]
        rows=report_items(self.state,self.targets)
        self.assertEqual(len(rows),1)
        self.assertIn(reason,rows[0]['summary'])


if __name__=='__main__':unittest.main()
