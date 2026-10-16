"""Small regression baseline. Never contacts the live model, WhatsApp or Moodle."""
import hashlib
import io
import json
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main():
    dataset=json.loads((ROOT/'eval_cases.json').read_text(encoding='utf-8'))
    output=ROOT/'reports/evaluations';output.mkdir(parents=True,exist_ok=True)
    latest=output/'latest.json';previous=json.loads(latest.read_text(encoding='utf-8')) if latest.exists() else None
    rows=[];start=time.time()
    for case in dataset['cases']:
        stream=io.StringIO();suite=unittest.defaultTestLoader.loadTestsFromName(case['test'])
        before=time.monotonic();result=unittest.TextTestRunner(stream=stream,verbosity=0).run(suite)
        rows.append({**case,'passed':result.wasSuccessful() and result.testsRun>0,'tests':result.testsRun,
                     'seconds':round(time.monotonic()-before,3),'failure':stream.getvalue() if not result.wasSuccessful() else ''})
    passed=sum(row['passed'] for row in rows)
    old={c['id']:c['passed'] for c in (previous or {}).get('cases',[])}
    report={'started':start,'mode':dataset['mode'],'limitation':dataset['limitation'],
            'passed':passed,'total':len(rows),'pass_rate':passed/len(rows),'seconds':round(time.time()-start,3),
            'previous_pass_rate':previous.get('pass_rate') if previous else None,
            'new_regressions':[r['id'] for r in rows if old.get(r['id']) is True and not r['passed']],
            'newly_passing':[r['id'] for r in rows if old.get(r['id']) is False and r['passed']],
            'code_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.py') if not p.name.startswith('test_')},
            'cases':rows}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    (output/('evaluation-'+str(time.time_ns())+'.json')).write_text(text,encoding='utf-8')
    latest.write_text(text,encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('passed','total','pass_rate','new_regressions','newly_passing','seconds')}))
    return 0 if passed==len(rows) else 1


if __name__=='__main__':raise SystemExit(main())
