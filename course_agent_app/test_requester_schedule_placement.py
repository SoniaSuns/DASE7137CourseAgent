"""Mixed syllabus alignment and explicit requester placement regression.

All browser/model interactions are fixtures. No course actions are executed.
"""
import copy
import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import chrome_schedule_alignment as alignment
from run_shared_chrome_agent import Runtime
from chrome_planner import schema, review_schema
from chrome_approval import approval_message


CID='140148'
SYLLABUS='file:9677e5c991c732ad1fda'
SUPPLEMENT='file:6a488dc4b59ca238f41d'
SLIDES=CID+':module:4255054'
TUTORIAL=CID+':module:4255055'
STORYBOARD=CID+':module:4255049'
LAPTOP=CID+':module:4317674'
W5=CID+':section:1607940'
W9=CID+':section:1607944'
W11=CID+':section:1692730'
REQUEST='Check the Class B internal syllabus and move materials by content name, not workshop number.'
REFINEMENT='directly move them into Week 5, and yes show Week 11 as a prerequisite, move Laptop Borrowing Form into Week 11, then move the two Generative AI files into Week 5 with the Storyboard Development materials while preserving their hidden visibility'


class RequesterSchedulePlacementTests(unittest.TestCase):
    def setUp(self):
        self.conversation=['The two files are Workshop IV Slides and XRCC Workshop IV Tutorial.',REFINEMENT]
        self.browser=Mock()
        self.browser.targets={
            W5:{'kind':'section','course_id':CID,'title':'Week 5','visibility':'shown'},
            W9:{'kind':'section','course_id':CID,'title':'Week 9','visibility':'shown'},
            W11:{'kind':'section','course_id':CID,'title':'Week 11','visibility':'hidden'},
            SLIDES:{'kind':'module','course_id':CID,'title':'Workshop IV Slides  File','section_key':W9},
            TUTORIAL:{'kind':'module','course_id':CID,'title':'XRCC Workshop IV Tutorial  File','section_key':W9},
            STORYBOARD:{'kind':'module','course_id':CID,'title':'Storyboard Development  File','section_key':W5},
            LAPTOP:{'kind':'module','course_id':CID,'title':'Laptop Borrowing Form  File','section_key':W5}}
        self.browser.overviews={CID:{'course_id':CID,'label':'Class B'}}
        self.browser.forms={
            SLIDES:{'resource_files':['Workshop IV Generative AI Application in VR.pdf'],'fields':[]},
            TUTORIAL:{'resource_files':['Generative AI in XR.pdf'],'fields':[]}}
        self.browser.inspect.return_value={'fields':[{'key':'id_visible','value':'0'}]}
        self.evidence=[
            {'file_id':SYLLABUS,'status':'read','relative_path':'internal.docx','sha256':'fixture',
             'table_rows':[{'row_id':'t3:r1','cells':['Week','Topic']},
                           {'row_id':'t3:r6','cells':['5','Storyboard Development']},
                           {'row_id':'t3:r12','cells':['11','Equipment borrow form']}]},
            {'file_id':SUPPLEMENT,'status':'read','relative_path':'workshop.pdf','text':'Generative AI in VR'}]
        self.contract={'required':True,'course_ids':[CID],'sources':[{'course_id':CID,'file_id':SYLLABUS}],
                       'items':[]}
        for target in (SLIDES,TUTORIAL,STORYBOARD,LAPTOP):
            exception=target in (SLIDES,TUTORIAL)
            self.contract['items'].append({
                'target':target,'target_section':W11 if target==LAPTOP else W5,
                'file_id':SYLLABUS,'row_id':'t3:r12' if target==LAPTOP else 't3:r6',
                'week_column':0,'disposition':'place','visibility':'hidden' if exception else 'preserve',
                'placement_basis':'requester' if exception else 'syllabus',
                'instruction_index':1 if exception else -1,
                'instruction_text':REFINEMENT if exception else '',
                'reason':'Explicit requested exception' if exception else 'Content matches syllabus'})
        self.moves=[{'id':str(i),'capability':'move_module','target':target,
                     'summary':'Move as explicitly requested, preserving hidden visibility.',
                     'scope':'requested','reason':'Current requester instruction','depends_on':[],
                     'fields':[],'params':{'target_section':W5,'before_module':'','visibility_policy':'preserve'}}
                    for i,target in enumerate((SLIDES,TUTORIAL))]
        self.prerequisite={'id':'show','capability':'set_section_visibility','target':W11,
                           'params':{'action':'show'},'depends_on':[]}
        self.review={'accepted':True,'findings':[],
                     'items':[{'target':r['target'],'accepted':True,'reason':'Independently checked',
                               'requester_instruction_verified':r['placement_basis']=='requester'}
                              for r in self.contract['items']]}

    def validated(self):
        return alignment.validate(self.contract,self.moves,self.browser,self.evidence,required=True)

    def reviewed(self):
        contract=self.validated()
        alignment.review_context(contract,self.browser,self.evidence,REQUEST,self.conversation)
        alignment.check_review(self.review,contract)
        return alignment.partition_review(self.review,contract,self.moves,self.browser)[0]

    def place_all(self,contract):
        for row in contract['items']:
            self.browser.targets[row['target']]['section_key']=row['target_section']

    def test_mixed_goal_exposes_instruction_separately_from_syllabus_topic(self):
        contract=self.validated()
        context=alignment.review_context(contract,self.browser,self.evidence,REQUEST,self.conversation)
        rows={r['target']:r for r in context['alignment']['items']}
        for target in (SLIDES,TUTORIAL):
            self.assertEqual(rows[target]['cited_row_role'],'destination_week_only')
            self.assertEqual(rows[target]['instruction_text'],REFINEMENT)
            self.assertEqual(rows[target]['cited_row_cells'],['5','Storyboard Development'])
            self.assertTrue(rows[target]['observed_content']['resource_files'])
        self.assertEqual(rows[STORYBOARD]['cited_row_role'],'material_topic_and_week')
        self.assertEqual(len(context['sources']),2)
        reviewed,ops,notes=alignment.partition_review(self.review,contract,[self.prerequisite],self.browser)
        self.assertEqual(ops,[self.prerequisite])
        self.assertFalse(notes)
        self.assertEqual(len(reviewed['items']),4)
        self.assertIn('reviewed_requester_placement',reviewed['items'][0])
        self.assertNotIn('reviewed_requester_placement',self.contract['items'][0])
        self.browser.execute.assert_not_called()

    def test_paraphrased_and_mixed_language_instructions_use_exact_provenance(self):
        for text in (
            'Put both identified GenAI resources alongside Storyboard in Week 5; keep them hidden.',
            '兩個 Generative AI files 放 Week 5，維持 hidden；先 show Week 11 再搬 Laptop Borrowing Form.'):
            with self.subTest(text=text):
                contract=copy.deepcopy(self.contract)
                for row in contract['items'][:2]:row['instruction_text']=text
                context=alignment.review_context(contract,self.browser,self.evidence,REQUEST,[self.conversation[0],text])
                self.assertEqual(context['alignment']['items'][0]['cited_row_role'],'destination_week_only')
                self.assertEqual(context['alignment']['items'][0]['instruction_text'],text)
        contract=copy.deepcopy(self.contract)
        for row in contract['items'][:2]:row.update(instruction_index=-1,instruction_text=REFINEMENT)
        context=alignment.review_context(contract,self.browser,self.evidence,REFINEMENT,[])
        self.assertEqual(context['alignment']['items'][0]['instruction_index'],-1)

    def test_fabricated_truncated_and_wrong_index_instruction_citations_fail(self):
        for index,text in ((1,'Move these files to Week 6.'),(1,'directly move them into Week 5'),
                           (0,REFINEMENT),(2,REFINEMENT),(-1,REFINEMENT),(True,REFINEMENT)):
            with self.subTest(index=index,text=text):
                contract=copy.deepcopy(self.contract)
                contract['items'][0].update(instruction_index=index,instruction_text=text)
                with self.assertRaisesRegex(ValueError,'instruction'):
                    alignment.review_context(contract,self.browser,self.evidence,REQUEST,self.conversation)
        # A quote found only inside supplemental content is not requester provenance.
        self.evidence[1]['text']=REFINEMENT
        with self.assertRaisesRegex(ValueError,'instruction'):
            alignment.review_context(self.contract,self.browser,self.evidence,REQUEST,[])

    def test_requester_basis_does_not_replace_the_traced_conflicting_source(self):
        self.contract['items'][0]['file_id']=SUPPLEMENT
        fixed,notes=alignment.inherit_citation_sources(self.contract,self.browser,self.evidence)
        self.assertFalse(notes)
        self.assertEqual(fixed['items'][0]['file_id'],SUPPLEMENT)
        with self.assertRaisesRegex(ValueError,'explicit source citation'):
            alignment.validate(fixed,self.moves,self.browser,self.evidence,required=True)
        self.browser.execute.assert_not_called()

    def test_syllabus_citations_coverage_and_course_identity_stay_required(self):
        for case in ('uncited_syllabus','uncited_exception','wrong_week','cross_course','missing_coverage','missing_source'):
            with self.subTest(case=case):
                contract=copy.deepcopy(self.contract);evidence=copy.deepcopy(self.evidence)
                if case=='uncited_syllabus':contract['items'][2]['file_id']=''
                if case=='uncited_exception':contract['items'][0]['row_id']=''
                if case=='wrong_week':contract['items'][0]['target_section']=W9
                if case=='cross_course':contract['items'][0]['target_section']='140147:section:5'
                if case=='missing_coverage':contract['items'].pop()
                if case=='missing_source':evidence=[]
                with self.assertRaises(ValueError):
                    alignment.validate(contract,self.moves,self.browser,evidence,required=True)

    def test_exception_requires_explicit_independent_instruction_verification(self):
        for flag in (None,False,'true'):
            with self.subTest(flag=flag):
                review=copy.deepcopy(self.review)
                review['items'][0]['requester_instruction_verified']=flag
                with self.assertRaisesRegex(ValueError,'requester placement instruction'):
                    alignment.check_review(review,self.validated())
                with self.assertRaises(alignment.AlignmentReviewRequired):
                    alignment.partition_review(review,self.validated(),[self.moves[0]],self.browser)
        review=copy.deepcopy(self.review)
        review['items'][0].update(accepted=False,reason='The cited instruction was superseded by the later reply.')
        self.conversation.append('Leave the two Generative AI files where they are.')
        context=alignment.review_context(self.validated(),self.browser,self.evidence,REQUEST,self.conversation)
        self.assertEqual(context['conversation'][-1],self.conversation[-1])
        with self.assertRaisesRegex(ValueError,'superseded'):
            alignment.check_review(review,self.validated())

    def test_partition_cannot_bypass_exception_review_or_its_dependencies(self):
        review=copy.deepcopy(self.review)
        review['items'][0]['requester_instruction_verified']=False
        self.moves[1]['depends_on']=[self.moves[0]['id']]
        contract,ops,notes=alignment.partition_review(
            review,self.validated(),self.moves+[self.prerequisite],self.browser)
        self.assertEqual(ops,[self.prerequisite])
        self.assertEqual([r['disposition'] for r in contract['items'][:2]],['unresolved','unresolved'])
        self.assertTrue(notes)
        for row in contract['items'][:2]:
            self.assertEqual(row['instruction_text'],'')
            self.assertNotIn('reviewed_requester_placement',row)
        self.assertTrue(alignment.verify_current(contract,self.browser))

    def test_readback_requires_reviewed_exception_location_and_hidden_visibility(self):
        contract=self.reviewed()
        self.place_all(contract)
        self.assertEqual(alignment.verify_current(contract,self.browser),[])
        self.browser.targets[SLIDES]['section_key']=W9
        self.assertIn('requester-directed section',alignment.verify_current(contract,self.browser)[0])
        self.browser.targets[SLIDES]['section_key']=W5
        self.browser.inspect.return_value={'fields':[{'key':'id_visible','value':'1'}]}
        self.assertTrue(any('not hidden' in issue for issue in alignment.verify_current(contract,self.browser)))
        self.browser.inspect.return_value={'fields':[{'key':'id_visible','value':'0'}]}
        for key,value in (('instruction_text','Fabricated instruction'),('target_section',W11),('visibility','shown')):
            with self.subTest(key=key):
                changed=copy.deepcopy(contract);changed['items'][0][key]=value
                self.assertIn('independent review',alignment.verify_current(changed,self.browser)[0])
        unreviewed=self.validated()
        self.assertIn('independent review',alignment.verify_current(unreviewed,self.browser)[0])

    def test_schema_can_express_mixed_basis_and_cannot_supply_review_receipt(self):
        item=schema()['properties']['alignment']['properties']['items']['items']
        for key in ('placement_basis','instruction_index','instruction_text'):
            self.assertIn(key,item['required'])
        self.assertEqual(item['properties']['placement_basis']['enum'],['syllabus','requester'])
        self.assertFalse(item['additionalProperties'])
        self.assertNotIn('reviewed_requester_placement',item['properties'])
        review_item=review_schema()['properties']['items']['items']
        self.assertIn('requester_instruction_verified',review_item['required'])

    def test_runtime_reviews_then_offers_exception_without_replaying_receipts(self):
        runtime=Runtime.__new__(Runtime)
        state={'task':REQUEST,'task_id':'T-fixture','run':'fixture','phase':'PLANNING',
               'conversation':self.conversation,'verified_previous_results':[{'target':'Storyboard','verified':True}],
               'completed_operation_memory':[{'operation':{'target':STORYBOARD},'receipt':{'verified':True}}]}
        flow=SimpleNamespace(state=state,save=Mock(),offer=Mock())
        runtime.flows={'chat':flow};runtime.browser=self.browser;runtime.file_evidence=self.evidence
        runtime.library=None;runtime.observer=Mock();runtime.trace=Mock();runtime.jobs={}
        runtime.readable_chats=None;runtime.record_failure=Mock();runtime.send=Mock()
        runtime.ensure_plan_evidence=Mock(return_value=True);runtime.render_pending=Mock()
        runtime.structure=Mock()
        runtime.structure.resolve.side_effect=lambda op:{**copy.deepcopy(op),'course_id':CID,
                                                         'title':self.browser.targets[op['target']]['title']}
        def submit(chat,context,rounds,repairs):
            runtime.jobs[chat]={'context':context,'rounds':rounds,'repairs':repairs}
        runtime.submit=Mock(side_effect=submit)
        candidate={'decision':'plan','alignment':copy.deepcopy(self.contract),'operations':self.moves,
                   'impact_review':{'findings':['Explicit exceptions; preserve completed moves.'],
                                    'inspected_targets':[SLIDES,TUTORIAL],'unresolved_dependencies':[]}}
        previous=copy.deepcopy(state['verified_previous_results'])
        runtime.accept_decision('chat',candidate,{'rounds':1,'repairs':1,'context':{}})
        flow.offer.assert_not_called()
        self.assertEqual(state['phase'],'PLANNING')
        context=runtime.jobs['chat']['context']
        self.assertEqual(context['alignment']['items'][0]['cited_row_role'],'destination_week_only')
        future=Future();future.set_result(self.review);runtime.jobs['chat']['future']=future
        runtime.complete_jobs()
        runtime.record_failure.assert_not_called()
        flow.offer.assert_called_once()
        offered=flow.offer.call_args.args[1]
        self.assertEqual([op['target'] for op in offered],[SLIDES,TUTORIAL])
        self.assertEqual(offered[0]['schedule_citation']['file_id'],SYLLABUS)
        self.assertEqual(offered[0]['schedule_citation']['placement_basis'],'requester')
        approval=approval_message({**state,'selected':offered,'code':'123456'})
        self.assertIn('Your explicit instruction; the syllabus confirms the destination week only.',approval)
        self.assertIn('Destination week reference:',approval)
        self.assertNotIn('  Syllabus:',approval)
        self.assertIn('reviewed_requester_placement',state['schedule_contract']['items'][0])
        self.assertEqual(state['verified_previous_results'],previous)
        self.assertFalse(state.get('approval_sent'))
        self.assertNotIn('write_started',state)
        runtime.structure.execute.assert_not_called();self.browser.execute.assert_not_called()


if __name__=='__main__':
    unittest.main()
