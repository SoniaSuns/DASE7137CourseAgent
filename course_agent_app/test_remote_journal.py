"""Execute protocol fault tests against the shipped service-worker JavaScript."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class RemoteJournalTests(unittest.TestCase):
    def test_browser_journal_survives_transport_failures_without_duplicate_saves(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which('node'), str(Path(__file__).parent/'test_assets/remote_journal.cjs'),
                                 str(root/'moodle_remote_extension/background.js')], capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['passed'], 6)
