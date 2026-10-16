"""Run deterministic capability checks and write a reviewable JSON/HTML report.

Uses synthetic data and locally fulfilled browser routes. No live Moodle,
WhatsApp, model calls, credentials or course library are required.
"""
import argparse
import html
import json
import subprocess
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


MODULES = {
    'test_simple_command_regressions': 'Exact rename, hide, move and confirmation',
    'test_shared_chrome_agent': 'Date form, approval, group identity and intake',
    'test_resource_files': 'Upload and replace exact source bytes',
    'test_gradebook_access': 'Participants, grades and exact student identity',
    'test_html_library_reading': 'HTML tables, Unicode and pagination',
    'test_chrome_file_library': 'Office files, source versions and hash changes',
    'test_simple_command_report': 'Report failure, error and skip accounting',
}
LIMITS = (
    'Deterministic unit and local-browser integration checks only. '
    'Command text is descriptive; typed operations are supplied by the tests. '
    'No natural-language planner evaluation, live Moodle write, Teams/GitHub '
    'synchronization or multi-user deployment is exercised. A pass establishes '
    'only the listed cases, not general command reliability.'
)


class ReportResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def record(self, test, status, detail=''):
        self.records.append({'test': test.id(), 'status': status, 'detail': detail,
                             'seconds': round(time.monotonic() - getattr(self, 'started', time.monotonic()), 3)})

    def addSuccess(self, test):
        super().addSuccess(test)
        self.record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.record(test, 'failed', self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self.record(test, 'error', self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.record(test, 'skipped', reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.record(test, 'expected_failure', self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.record(test, 'unexpected_success')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            status = 'failed' if issubclass(err[0], test.failureException) else 'error'
            self.record(subtest, status, self._exc_info_to_string(err, test))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'reports' / 'simple_command_checks')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(MODULES))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=ReportResult).run(suite)
    repo = Path(__file__).resolve().parents[1]
    commit = subprocess.run(['git', '-c', f'safe.directory={repo.as_posix()}', 'rev-parse', 'HEAD'],
                            cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(['git', '-c', f'safe.directory={repo.as_posix()}', 'status', '--porcelain'],
                           cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    complete = result.wasSuccessful() and not result.skipped and not result.expectedFailures
    report = {'generated_utc': datetime.now(timezone.utc).isoformat(), 'commit': commit,
              'working_tree_dirty': bool(dirty), 'all_passed': complete,
              'tests_run': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skipped': len(result.skipped),
              'expected_failures': len(result.expectedFailures),
              'unexpected_successes': len(result.unexpectedSuccesses),
              'seconds': round(time.monotonic() - started, 3),
              'limits': LIMITS, 'coverage': MODULES, 'results': result.records}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    esc = html.escape
    rows = ''.join(f'<tr><td>{esc(row["test"])}</td><td>{esc(row["status"])}</td>'
                   f'<td>{row["seconds"]}</td><td><pre>{esc(row["detail"])}</pre></td></tr>' for row in result.records)
    document = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Simple command checks</title>'
                '<style>body{font:16px system-ui;margin:32px;max-width:1400px}table{border-collapse:collapse}'
                'td,th{border:1px solid #ccc;padding:8px;text-align:left}pre{white-space:pre-wrap}</style>'
                f'<h1>Simple command checks: {"PASS" if complete else "INCOMPLETE / FAILED"}</h1>'
                f'<p>{result.testsRun} tests; {report["failures"]} failures; {report["errors"]} errors; '
                f'{report["skipped"]} skipped. {report["seconds"]} seconds.</p>'
                f'<p>Commit: {esc(commit)}; working tree dirty: {bool(dirty)}. {esc(report["generated_utc"])}</p>'
                f'<p>{esc(LIMITS)}</p><table><tr><th>Case</th><th>Result</th><th>Seconds</th><th>Detail</th></tr>'
                f'{rows}</table></html>')
    (args.output / 'report.html').write_text(document, encoding='utf-8')
    print(f'Report: {args.output.resolve() / "report.html"}')
    return 0 if complete else 1


if __name__ == '__main__':
    raise SystemExit(main())
