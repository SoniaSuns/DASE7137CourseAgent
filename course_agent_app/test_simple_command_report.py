"""Failures in a check must remain failures in the generated report."""
import io
import unittest

from run_simple_command_checks import ReportResult


class SimpleCommandReportTests(unittest.TestCase):
    def run_case(self, case):
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=ReportResult).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(case))

    def test_subtest_assertion_and_error_have_distinct_matching_outcomes(self):
        class Case(unittest.TestCase):
            def test_nested(self):
                with self.subTest(kind='assertion'):
                    self.fail('wrong output')
                with self.subTest(kind='exception'):
                    raise RuntimeError('reader unavailable')
        result = self.run_case(Case)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual((len(result.failures), len(result.errors)), (1, 1))
        self.assertEqual([r['status'] for r in result.records], ['failed', 'error'])
        self.assertFalse(result.wasSuccessful())

    def test_setup_error_is_reported_without_success(self):
        class Case(unittest.TestCase):
            def setUp(self):
                raise RuntimeError('fixture unavailable')
            def test_unused(self):
                pass
        result = self.run_case(Case)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual([r['status'] for r in result.records], ['error'])

    def test_skip_does_not_become_a_passed_record(self):
        class Case(unittest.TestCase):
            @unittest.skip('browser unavailable')
            def test_skipped(self):
                pass
        result = self.run_case(Case)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual([r['status'] for r in result.records], ['skipped'])

    def test_successful_subtests_produce_one_parent_success(self):
        class Case(unittest.TestCase):
            def test_nested(self):
                for value in [1, 2]:
                    with self.subTest(value=value):
                        self.assertGreater(value, 0)
        result = self.run_case(Case)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual([r['status'] for r in result.records], ['passed'])


if __name__ == '__main__':
    unittest.main()
