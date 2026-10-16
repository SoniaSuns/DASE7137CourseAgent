"""Regression coverage for reviewed preservation versus syllabus placement."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import chrome_schedule_alignment as alignment
from chrome_planner import schema


class UnchangedSchedulePlacementTests(unittest.TestCase):
    def setUp(self):
        self.browser=SimpleNamespace(
            targets={
                'b:poster':{'kind':'module','course_id':'b','title':'Demo Day Poster',
                            'section_key':'b:w11','module_type':'assign'},
                'b:material':{'kind':'module','course_id':'b','title':'Storyboard Development',
                              'section_key':'b:w9','module_type':'resource'},
                **{f'b:w{n}':{'kind':'section','course_id':'b','title':f'Week {n}',
                              'visibility':'shown'} for n in (5,9,10,11)},
                'b:general':{'kind':'section','course_id':'b','title':'General','visibility':'shown'},
                'a:w11':{'kind':'section','course_id':'a','title':'Week 11','visibility':'shown'}},
            forms={},overviews={'b':{'course_id':'b','label':'Class B'}},
            read_layout=Mock(),inspect=Mock(),execute=Mock())
        self.evidence=[{'file_id':'syllabus-b','status':'read','table_rows':[
            {'row_id':'t3:r1','cells':['Week','Topic']},
            {'row_id':'t3:r6','cells':['5','Storyboard Development']},
            {'row_id':'t3:r11','cells':['10','Interaction Demo, Poster']},
            {'row_id':'t3:r12','cells':['11','Equipment borrow form']}]}]
        self.contract={'required':True,'course_ids':['b'],
            'sources':[{'course_id':'b','file_id':'syllabus-b'}],
            'items':[
                {'target':'b:poster','target_section':'b:w11','file_id':'','row_id':'',
                 'week_column':0,'disposition':'unchanged','placement_basis':'syllabus',
                 'instruction_index':-1,'instruction_text':'','visibility':'preserve',
                 'reason':'Retain the submission activity location under the current files-only scope.'},
                {'target':'b:material','target_section':'b:w5','file_id':'syllabus-b',
                 'row_id':'t3:r6','week_column':0,'disposition':'place','placement_basis':'syllabus',
                 'instruction_index':-1,'instruction_text':'','visibility':'preserve',
                 'reason':'The material topic matches the Week 5 source row.'}]}
        self.operations=[{'id':'move','capability':'move_module','target':'b:material',
                          'params':{'target_section':'b:w5'},'depends_on':[]}]
        self.review={'accepted':True,'findings':[],'items':[
            {'target':r['target'],'accepted':True,'reason':'Checked against the current request.',
             'requester_instruction_verified':False} for r in self.contract['items']]}

    def validated(self):
        return alignment.validate(self.contract,self.operations,self.browser,self.evidence,required=True)

    def reviewed(self):
        contract=self.validated()
        alignment.check_review(self.review,contract)
        revised,notes=alignment.apply_review(self.review,contract,self.operations,self.browser)
        self.assertEqual(notes,[])
        return revised

    def test_schema_can_express_preserved_location_without_false_week_citation(self):
        item=schema()['properties']['alignment']['properties']['items']['items']
        self.assertIn('unchanged',item['properties']['disposition']['enum'])
        self.assertEqual(set(self.contract['items'][0]),set(item['required']))
        self.assertNotIn('reviewed_unchanged_placement',item['properties'])

    def test_traced_week10_citation_week11_destination_stays_invalid(self):
        bad=copy.deepcopy(self.contract)
        bad['items'][0].update(disposition='place',file_id='syllabus-b',row_id='t3:r11',
                              reason='Tracked only; no move in this prerequisite stage.')
        with self.assertRaisesRegex(ValueError,'row week and proposed destination differ'):
            alignment.validate(bad,[],self.browser,self.evidence,required=True)
        self.browser.execute.assert_not_called()

    def test_reviewed_preservation_passes_but_remaining_move_still_blocks_done(self):
        original=copy.deepcopy(self.contract)
        reviewed=self.reviewed()
        self.assertEqual(self.contract,original)
        self.assertEqual(alignment.verify_current(reviewed,self.browser),[
            'Storyboard Development: not in its syllabus section'])
        self.browser.targets['b:material']['section_key']='b:w5'
        self.assertEqual(alignment.verify_current(reviewed,self.browser),[])
        self.browser.read_layout.assert_called_with('b')
        self.browser.execute.assert_not_called()

    def test_unchanged_in_general_needs_no_invented_week(self):
        self.browser.targets['b:poster']['section_key']='b:general'
        self.contract['items'][0]['target_section']='b:general'
        reviewed=self.reviewed()
        self.browser.targets['b:material']['section_key']='b:w5'
        self.assertEqual(alignment.verify_current(reviewed,self.browser),[])

    def test_unreviewed_preservation_cannot_count_as_complete(self):
        self.browser.targets['b:material']['section_key']='b:w5'
        self.assertEqual(alignment.verify_current(self.contract,self.browser),[
            'Demo Day Poster: unchanged placement needs independent review'])

    def test_preservation_review_is_bound_to_exact_row_and_reason(self):
        reviewed=self.reviewed()
        self.browser.targets['b:material']['section_key']='b:w5'
        for key,value in [('reason','A different scope decision'),('target_section','b:w10'),
                          ('visibility','shown'),('instruction_text','Keep everything')]:
            with self.subTest(key=key):
                changed=copy.deepcopy(reviewed)
                changed['items'][0][key]=value
                self.assertIn('needs independent review',alignment.verify_current(changed,self.browser)[0])

    def test_fresh_readback_and_preflight_detect_preserved_location_drift(self):
        reviewed=self.reviewed()
        self.browser.targets['b:material']['section_key']='b:w5'
        def refresh(course_id):
            self.browser.targets['b:poster']['section_key']='b:w10'
        self.browser.read_layout.side_effect=refresh
        self.assertEqual(alignment.verify_current(reviewed,self.browser),[
            'Demo Day Poster: unchanged placement no longer matches current evidence'])
        with self.assertRaisesRegex(ValueError,'exact observed section'):
            alignment.validate(reviewed,[],self.browser,self.evidence,required=True)

    def test_removed_preserved_identity_is_not_satisfied_by_same_title(self):
        reviewed=self.reviewed()
        self.browser.targets['b:material']['section_key']='b:w5'
        self.browser.targets['b:replacement']=self.browser.targets.pop('b:poster')
        problems=alignment.verify_current(reviewed,self.browser)
        self.assertIn('Course inventory changed; rebuild full coverage.',problems)
        self.assertTrue(any('no longer matches current evidence' in p for p in problems))

    def test_preservation_requires_observed_same_course_section(self):
        for destination in ('','b:missing','a:w11','b:w10','b:material'):
            with self.subTest(destination=destination):
                bad=copy.deepcopy(self.contract)
                bad['items'][0]['target_section']=destination
                with self.assertRaisesRegex(ValueError,'exact observed section'):
                    alignment.validate(bad,[],self.browser,self.evidence)

    def test_preservation_rejects_placement_citations_and_visibility_changes(self):
        self.validated()
        for key,value in [('file_id','syllabus-b'),('row_id','t3:r11'),('week_column',1),
                          ('week_column',False),('expected_week',11),('visibility','hidden'),
                          ('placement_basis','requester'),('instruction_index',0),
                          ('instruction_text','Keep this item')]:
            with self.subTest(key=key,value=value):
                bad=copy.deepcopy(self.contract)
                bad['items'][0][key]=value
                with self.assertRaisesRegex(ValueError,'unchanged placement cannot carry'):
                    alignment.validate(bad,[],self.browser,self.evidence)

    def test_unchanged_never_authorizes_move_even_to_its_current_section(self):
        self.validated()
        for destination in ('b:w11','b:w10'):
            with self.subTest(destination=destination):
                op={'id':'forbidden','capability':'move_module','target':'b:poster',
                    'params':{'target_section':destination}}
                with self.assertRaisesRegex(ValueError,'Every move must match'):
                    alignment.validate(self.contract,[op],self.browser,self.evidence)
        self.browser.execute.assert_not_called()

    def test_review_context_keeps_preserved_location_without_ghost_citation(self):
        reviewed=self.reviewed()
        for request,conversation in [
                ('Leave the submission activity where it is.',[]),
                ('Align the materials',['Submission 保持原位; only move the teaching files.'])]:
            with self.subTest(request=request):
                context=alignment.review_context(reviewed,self.browser,self.evidence,request,conversation)
                row=context['alignment']['items'][0]
                self.assertEqual(row['target_section'],'b:w11')
                self.assertEqual(row['current_section_title'],'Week 11')
                self.assertEqual(row['proposed_section_title'],'Week 11')
                self.assertEqual(row['file_id'],'')
                self.assertNotIn('cited_row_cells',row)
                self.assertNotIn('reviewed_unchanged_placement',row)
                self.assertEqual(context['request'],request)
                self.assertEqual(context['conversation'],conversation)
        self.assertIn('reviewed_unchanged_placement',reviewed['items'][0])

    def test_normalizers_never_convert_preservation_into_syllabus_placement(self):
        self.validated()
        candidate=copy.deepcopy(self.contract)
        candidate['items'][0].update(target_section='',row_id='t3:r11')
        candidate,_=alignment.inherit_citation_sources(candidate,self.browser,self.evidence)
        candidate,operations,notes=alignment.resolve_missing_destinations(
            candidate,self.operations,self.browser,self.evidence)
        candidate,_=alignment.normalize_week_columns(candidate,self.browser,self.evidence)
        self.assertEqual(candidate['items'][0]['target_section'],'')
        self.assertEqual(candidate['items'][0]['file_id'],'')
        self.assertEqual(operations,self.operations)
        self.assertEqual(notes,[])
        with self.assertRaises(ValueError):
            alignment.validate(candidate,operations,self.browser,self.evidence)

    def test_rejected_preservation_remains_unresolved_in_independent_batch(self):
        reviewed=self.reviewed()
        rejected=copy.deepcopy(self.review)
        rejected['accepted']=False
        rejected['items'][0].update(accepted=False,reason='The current scope requires this later move.')
        revised,ops,notes=alignment.partition_review(rejected,reviewed,self.operations,self.browser)
        self.assertEqual(ops,self.operations)
        self.assertEqual(revised['items'][0]['disposition'],'unresolved')
        self.assertNotIn('reviewed_unchanged_placement',revised['items'][0])
        self.assertTrue(notes)
        self.browser.targets['b:material']['section_key']='b:w5'
        self.assertTrue(any('requires this later move' in p for p in alignment.verify_current(revised,self.browser)))

    def test_rejected_preserved_descendant_still_blocks_section_prerequisite(self):
        self.contract['items'][1].update(target_section='b:w11',row_id='t3:r12')
        self.operations=[]
        contract=self.validated()
        rejected=copy.deepcopy(self.review)
        rejected['accepted']=False
        rejected['items'][0].update(accepted=False,reason='Preservation scope is not established.')
        section={'id':'show','capability':'set_section_visibility','target':'b:w11',
                 'params':{'action':'show'},'depends_on':[]}
        with self.assertRaises(alignment.AlignmentReviewRequired):
            alignment.partition_review(rejected,contract,[section],self.browser)
        self.browser.execute.assert_not_called()

    def test_requester_identity_rejection_is_not_converted_to_unchanged(self):
        self.contract['items'][1].update(placement_basis='requester',instruction_index=0,
            instruction_text='Move the two Generative AI files to Week 5.',
            reason='A proposed identity association requires independent verification.')
        self.review['items'][1]['accepted']=True
        self.review['items'][1]['requester_instruction_verified']=False
        contract=self.validated()
        with self.assertRaisesRegex(ValueError,'did not verify the requester placement instruction'):
            alignment.check_review(self.review,contract)
        with self.assertRaises(alignment.AlignmentReviewRequired):
            alignment.partition_review(self.review,contract,self.operations,self.browser)
        self.assertEqual(contract['items'][1]['disposition'],'place')
        self.browser.execute.assert_not_called()

    def test_full_coverage_and_source_guards_still_apply_to_preservation(self):
        self.validated()
        missing=copy.deepcopy(self.contract)
        missing['items'].pop(0)
        with self.assertRaisesRegex(ValueError,'every current activity'):
            alignment.validate(missing,self.operations,self.browser,self.evidence)
        with self.assertRaisesRegex(ValueError,'Read the authoritative syllabus'):
            alignment.validate(self.contract,self.operations,self.browser,[])
        self.review['items'].pop(0)
        with self.assertRaises(ValueError):
            alignment.check_review(self.review,self.contract)


if __name__=='__main__':
    unittest.main()
