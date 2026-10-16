import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_shared_chrome_agent as runtime
from course_agent.codex_runtime import codex_model


class PersistentPlannerModelTests(unittest.TestCase):
    def test_restart_without_shell_model_uses_persisted_selection(self):
        config=json.loads((runtime.ROOT/'chrome_operator_config.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'chrome_operator_config.json').write_text(json.dumps(config),encoding='utf-8')
            # An unrelated old global default must not replace the configured planner.
            with patch.object(runtime,'ROOT',root),patch.dict(runtime.os.environ,{'CODEX_MODEL':'old-default'},clear=True):
                _,env=runtime.load_config()
                self.assertEqual(codex_model(env,'planner'),config['planner_model'])
            with patch.object(runtime,'ROOT',root),patch.dict(runtime.os.environ,{'CODEX_PLANNER_MODEL':'explicit-model'},clear=True):
                _,env=runtime.load_config()
                self.assertEqual(codex_model(env,'planner'),'explicit-model')


if __name__=='__main__':unittest.main()
