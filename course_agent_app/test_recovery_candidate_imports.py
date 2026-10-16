import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import auto_block_recovery as recovery


class CandidateImportTests(unittest.TestCase):
    def test_regression_imports_staged_sibling_package_without_external_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'candidate' / 'course_agent_app'
            package = app.parent / 'course_agent'
            outside = root / 'external'
            app.mkdir(parents=True)
            package.mkdir()
            outside.mkdir()
            (package / '__init__.py').write_text('VALUE = "staged"\n', encoding='utf-8')
            (outside / 'course_agent.py').write_text('VALUE = "external"\n', encoding='utf-8')
            (outside / 'unstaged_dependency.py').write_text('VALUE = 1\n', encoding='utf-8')
            (app / 'test_probe.py').write_text(
                'import importlib.util\nimport unittest\nimport course_agent\n'
                'class Probe(unittest.TestCase):\n'
                ' def test_staged(self):\n'
                '  self.assertEqual(course_agent.VALUE, "staged")\n'
                '  self.assertIsNone(importlib.util.find_spec("unstaged_dependency"))\n',
                encoding='utf-8')
            log = root / 'regression.log'
            with patch.dict(os.environ, {'PYTHONPATH': str(outside)}):
                code = recovery.run_tests(app, ['test_probe'], log)
            self.assertEqual(code, 0, log.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
