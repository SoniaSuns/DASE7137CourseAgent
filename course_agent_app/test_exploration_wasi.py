"""Run real generated Python in WASI; synthetic canaries, no Moodle connection."""
import json
from pathlib import Path
import tempfile
import unittest

from exploration_python import AnalysisPython


class WasiIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root/'work';self.work.mkdir()
        self.worker = AnalysisPython(self.work)

    def test_arbitrary_python_reads_html_and_calculates_group_mean(self):
        (self.work/'people.html').write_text('<table><tr><td>Group 1</td><td>70</td></tr><tr><td>Group 1</td><td>90</td></tr></table>')
        result = self.worker.run("""
from html.parser import HTMLParser
import statistics,json
class Table(HTMLParser):
    def __init__(self):super().__init__();self.values=[]
    def handle_data(self,text):
        if text.isdigit():self.values.append(int(text))
t=Table();t.feed(open('/work/people.html').read())
print(json.dumps({'mean':statistics.mean(t.values),'count':len(t.values)}))
""")
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(json.loads(result['stdout']),{'mean':80,'count':2})

    def test_host_files_loopback_and_process_execution_are_unavailable(self):
        sentinel = self.root/'credential.txt';sentinel.write_text('SYNTHETIC_ONLY')
        code = """
import os,json
r={}
for key,path in [('parent','/work/../credential.txt'),('host',HOST)]:
    try:open(path).read();r[key]=False
    except OSError:r[key]=True
try:
    import socket
    socket.create_connection(('127.0.0.1',9222),timeout=1);r['socket']=False
except (OSError,ImportError,AttributeError):r['socket']=True
try:
    import subprocess
    subprocess.run(['cmd.exe','/c','echo','BAD']);r['process']=False
except (OSError,ImportError,AttributeError):r['process']=True
try:
    open('/runtime/canary-write','w').write('BAD');r['runtime_read_only']=False
except OSError:r['runtime_read_only']=True
r['no_credentials']=not any('KEY' in k or 'TOKEN' in k or 'PASSWORD' in k for k in os.environ)
print(json.dumps(r))
""".replace('HOST',repr(str(sentinel)))
        result = self.worker.run(code)
        self.assertEqual(result['exit_code'],0,result)
        checks = json.loads(result['stdout'])
        self.assertEqual(set(checks),{'parent','host','socket','process','runtime_read_only','no_credentials'})
        self.assertTrue(all(checks.values()),checks)
        self.assertEqual(sentinel.read_text(),'SYNTHETIC_ONLY')

    def test_python_packages_can_read_excel_without_host_native_extensions(self):
        from openpyxl import Workbook
        workbook=Workbook();sheet=workbook.active
        sheet.append(['Group','Score']);sheet.append([1,70]);sheet.append([1,90])
        workbook.save(self.work/'scores.xlsx')
        from chrome_exploration import analysis_zip
        original=(self.work/'scores.xlsx').read_bytes()
        (self.work/'scores.xlsx').write_bytes(analysis_zip(original))
        result = self.worker.run("import openpyxl; w=openpyxl.load_workbook('/work/scores.xlsx',read_only=True); print(sum(row[1] for row in list(w.active.values)[1:])/2)")
        self.assertEqual(result['exit_code'],0,result)
        self.assertEqual(result['stdout'].strip(),'80.0')

    def test_runaway_code_is_stopped_by_fuel(self):
        result = self.worker.run('while True: pass')
        self.assertNotEqual(result['exit_code'],0)


if __name__ == '__main__':
    unittest.main()
