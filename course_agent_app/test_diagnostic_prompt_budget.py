import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import auto_block_recovery as recovery


def unpack(payload):
    if payload.get('encoding')!='shared-json-v1':return payload
    marker=payload['reference_key'];references=payload['references']
    def visit(value):
        if isinstance(value,dict):
            if set(value)=={marker}:return visit(references[value[marker]])
            return {k:visit(v) for k,v in value.items()}
        if isinstance(value,list):return [visit(v) for v in value]
        return value
    return visit(payload['data'])


class DiagnosticPromptBudgetTests(unittest.TestCase):
    def test_large_repeated_evidence_fits_without_losing_source_or_error(self):
        shared={'text':'Internal teaching material. '*5000,'table_rows':[['5','topic']]*40,
                '__diagnostic_reference__':'literal user data, not a reference'}
        payload={'failure':'source bytes changed','trace':[{'round':i,'source':shared} for i in range(9)],
                 'current_code':{'module.py':'print("do not execute")'},'unicode':'\U0001f642'*4}
        self.assertGreater(len(json.dumps(payload)),1_048_576)
        with tempfile.TemporaryDirectory() as tmp:
            job=Path(tmp);captured=[]
            def run(command,**kwargs):
                captured.append(kwargs['input'])
                recovery.write(job/'diagnose-output.json',{'ok':True})
                return Mock(returncode=0)
            with patch.object(recovery.subprocess,'run',side_effect=run):
                result=recovery.model_call(job,'diagnose',payload,recovery.obj({'ok':{'type':'boolean'}}),{'CODEX_BIN':__file__})
            prompt=captured[0]
            self.assertLess(len(prompt.encode('utf-16-le'))//2,900_000)
            encoded=json.loads(prompt.split('\nEVIDENCE:\n',1)[1])
            self.assertEqual(unpack(encoded),payload)
            self.assertTrue(result['ok'])

    def test_unique_oversized_payload_is_rejected_before_process_launch_at_every_stage(self):
        payload={'source':'x'*910_000}
        for stage in ('diagnose','repair','review_patch'):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as tmp:
                with patch.object(recovery.subprocess,'run') as run:
                    with self.assertRaisesRegex(ValueError,'input budget'):
                        recovery.model_call(Path(tmp),stage,payload,recovery.obj({'ok':{'type':'boolean'}}),{'CODEX_BIN':__file__})
                    run.assert_not_called()


if __name__=='__main__':unittest.main()
