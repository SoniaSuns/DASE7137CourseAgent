import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from chrome_planner import CodexPlanner


class PlannerPayloadTests(unittest.TestCase):
    def test_repeated_form_options_are_losslessly_shared(self):
        from chrome_planner import compact_context
        options=[{'value':str(n),'label':str(n),'disabled':False} for n in range(1970,2101)]
        context={'request':'Adjust dates','forms':[{'target':str(i),'fingerprint':str(i),
                    'fields':[{'key':'year','type':'select-one','value':str(2026+i%2),'options':copy.deepcopy(options)}]}
                    for i in range(31)],'file_evidence':[{'text':'Preserve every syllabus instruction.'}]}
        original=copy.deepcopy(context)
        compact=compact_context(context)
        self.assertLess(len(json.dumps(compact)),len(json.dumps(context))/8)
        restored=copy.deepcopy(compact);catalog=restored.pop('option_catalog')
        specs=restored.pop('field_catalog',{})
        for form in restored['forms']:
            for field in form['fields']:
                if 'field_ref' in field:
                    spec=copy.deepcopy(specs[field.pop('field_ref')]);field.update(spec)
                if 'options_ref' in field:field['options']=catalog[field.pop('options_ref')]
        self.assertEqual(restored,original)
        self.assertEqual(context,original)

    def test_failed_process_keeps_specific_error_without_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            planner=CodexPlanner({'CODEX_BIN':__file__,'PRIVATE_TOKEN':'sensitive-test-token'},Path(temp))
            result=subprocess.CompletedProcess([],1,stdout='',stderr='ERROR: context_length_exceeded; PRIVATE_TOKEN=sensitive-test-token')
            with patch('chrome_planner.subprocess.run',return_value=result):
                with self.assertRaisesRegex(RuntimeError,'context'):
                    planner({'request':'Check timing','forms':[]})
            reports=list(Path(temp).glob('diagnostic-*.json'))
            self.assertEqual(len(reports),1)
            report=reports[0].read_text(encoding='utf-8')
            self.assertIn('context_length_exceeded',report)
            self.assertNotIn('sensitive-test-token',report)
            self.assertNotIn('Check timing',report)


if __name__=='__main__':unittest.main()
