"""Preservation provenance and terminal review routing; no live browser writes."""
import copy
from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from run_shared_chrome_agent import Runtime
import chrome_schedule_alignment as alignment
from chrome_planner import review_schema
import test_unchanged_schedule_placement as fixtures


class RequesterPreservationTests(unittest.TestCase):
    def fixture(self, instruction):
        case = fixtures.UnchangedSchedulePlacementTests()
        case.setUp()
        case.browser.targets['b:poster']['section_key'] = 'b:general'
        case.contract['items'][0].update(target_section='b:general', placement_basis='requester',
            instruction_index=0, instruction_text=instruction,
            reason='The requester explicitly retained this exact activity.')
        return case

    def test_explicit_preservation_is_reviewed_without_syllabus_week_or_write(self):
        for text in ('Keep the poster assignment in its original section.',
                     'Poster assignment 保留原位; align only the remaining files.'):
            with self.subTest(instruction=text):
                case = self.fixture(text)
                contract = case.validated()
                context = alignment.review_context(contract, case.browser, case.evidence, 'Align files', [text])
                row = context['alignment']['items'][0]
                self.assertEqual(row['instruction_text'], text)
                self.assertNotIn('cited_row_cells', row)
                with self.assertRaisesRegex(ValueError, 'did not verify'):
                    alignment.check_review(case.review, contract)
                case.review['items'][0]['requester_instruction_verified'] = True
                reviewed, _ = alignment.apply_review(case.review, contract, case.operations, case.browser)
                self.assertIn('reviewed_unchanged_placement', reviewed['items'][0])
                case.browser.targets['b:material']['section_key'] = 'b:w5'
                self.assertEqual(alignment.verify_current(reviewed, case.browser), [])
                case.browser.targets['b:poster']['section_key'] = 'b:w10'
                self.assertTrue(alignment.verify_current(reviewed, case.browser))
                case.browser.execute.assert_not_called()

    def test_preservation_citation_cannot_be_fabricated_or_moved(self):
        case = self.fixture('Keep the poster assignment in General.')
        contract = case.validated()
        with self.assertRaisesRegex(ValueError, 'exact current-task instruction'):
            alignment.review_context(contract, case.browser, case.evidence, 'Align files', ['Move it to Week 10.'])
        op = {'capability': 'move_module', 'target': 'b:poster', 'params': {'target_section': 'b:general'}}
        with self.assertRaisesRegex(ValueError, 'Every move must match'):
            alignment.validate(contract, [op], case.browser, case.evidence)


class TerminalReviewRoutingTests(unittest.TestCase):
    def runtime(self, items):
        runtime = Runtime.__new__(Runtime)
        state = {'phase': 'PLANNING', 'task': 'Align files; keep specified items.', 'alignment_repairs': 2,
                 'verified_previous_results': [{'verified': True}] * 9}
        flow = SimpleNamespace(state=state, save=Mock())
        def fail(reason):
            state.update(phase='FAILED', reason=reason)
        flow.fail = Mock(side_effect=fail)
        runtime.flows = {'chat': flow}
        runtime.browser = SimpleNamespace(targets={x['target']: {'title': x['target']} for x in items})
        result = {'accepted': False, 'findings': ['Review has unresolved items.'], 'items': items}
        future = Future(); future.set_result(result)
        runtime.jobs = {'chat': {'future': future, 'context': {'review_kind': 'schedule_alignment'},
            'alignment_candidate': {'operations': []}, 'review_contract': {'items': [{'target': x['target']} for x in items]},
            'rounds': 1, 'repairs': 1}}
        runtime.readable_chats = None
        runtime.record_failure = Mock(); runtime.send = Mock()
        return runtime, state

    def finish(self, runtime):
        with patch.object(alignment, 'partition_review', side_effect=alignment.AlignmentReviewRequired('Review unresolved')):
            runtime.complete_jobs()

    def test_representation_failure_does_not_ask_user_for_week_again(self):
        for extra in ({}, {'blocker_type': 'plan_error', 'question': 'Repeat the week?'}):
            with self.subTest(fields=extra):
                runtime, state = self.runtime([{'target': 'Keep-original document', 'accepted': False,
                    'reason': 'Preservation is correct but encoded incorrectly.', **extra}])
                self.finish(runtime)
                self.assertEqual(state['phase'], 'FAILED')
                message = runtime.send.call_args.args[1]
                self.assertIn('Status: Failed', message)
                self.assertNotIn('Repeat the week', message)
                self.assertNotIn('Please specify', message)
                self.assertEqual(len(state['verified_previous_results']), 9)

    def test_only_concrete_user_decisions_become_questions_and_are_not_truncated(self):
        items = [{'target': 'Encoding issue '+str(n), 'accepted': False, 'reason': 'Fix representation.',
                  'blocker_type': 'plan_error', 'question': ''} for n in range(9)]
        items.append({'target': 'Tutorial conflict', 'accepted': False, 'reason': 'Two selected sources conflict.',
                      'blocker_type': 'user_decision', 'question': 'Should this tutorial follow the internal schedule or the revised arrangement?'})
        runtime, state = self.runtime(items)
        self.finish(runtime)
        self.assertEqual(state['phase'], 'QUESTION')
        self.assertIn('internal schedule or the revised arrangement', state['pending_question'])
        self.assertNotIn('Encoding issue', state['pending_question'])
        self.assertEqual(len(state['verified_previous_results']), 9)

    def test_empty_user_question_and_incomplete_review_fail_technically(self):
        runtime, state = self.runtime([{'target': 'Unknown', 'accepted': False, 'reason': 'No details',
                                        'blocker_type': 'user_decision', 'question': ''}])
        self.finish(runtime)
        self.assertEqual(state['phase'], 'FAILED')

    def test_review_schema_requires_actionable_failure_classification(self):
        item = review_schema()['properties']['items']['items']
        self.assertIn('blocker_type', item['required'])
        self.assertIn('question', item['required'])

    def test_missing_requester_verification_cannot_be_relabelled_as_user_question(self):
        contract = {'items': [{'target': 'file', 'disposition': 'unchanged', 'placement_basis': 'requester'}]}
        result = {'items': [{'target': 'file', 'accepted': True, 'requester_instruction_verified': False,
                            'blocker_type': 'user_decision', 'question': 'Repeat the instruction?'}]}
        browser = SimpleNamespace(targets={'file': {'title': 'Kept file'}})
        self.assertEqual(alignment.clarification_questions(result, contract, browser), [])
        result['items'][0]['accepted'] = False
        result['items'][0]['requester_instruction_verified'] = True
        self.assertEqual(len(alignment.clarification_questions(result, contract, browser)), 1)
        contract['items'].append({'target': 'omitted'})
        self.assertEqual(alignment.clarification_questions(result, contract, browser), [])

    def test_semantic_repair_receives_complete_review_after_short_summary_limit(self):
        items = [{'target': 'item '+str(n), 'accepted': False, 'reason': 'x'*300,
                  'blocker_type': 'plan_error', 'question': ''} for n in range(20)]
        runtime, state = self.runtime(items)
        state['alignment_repairs'] = 0
        runtime.context = Mock(return_value={}); runtime.submit = Mock()
        runtime.complete_jobs()
        feedback = runtime.submit.call_args.args[1]['placement_review']
        self.assertEqual(feedback['review']['items'], items)
        self.assertEqual(len(feedback['proposal']['items']), 20)
        runtime.send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
