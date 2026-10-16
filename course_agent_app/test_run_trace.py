import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from run_trace import RunTrace
from trace_report import inspect_trace,build


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.trace=RunTrace(self.root,['secret-value'])
        self.run='a'*12;self.task='T-'+'b'*12

    def test_full_observable_io_redacts_credentials_and_approval(self):
        value={'request':'A long request\n'+'x'*20000,'password':'secret-value','reply':'OK 654321',
               'url':'https://example.test/?sesskey=abc','callback':lambda:None}
        self.trace.call(self.run,self.task,'model','planner',value,lambda:{'decision':'inspect','targets':['one']})
        folder=self.root/self.run;data=''.join(p.read_text(encoding='utf-8') for p in folder.glob('*.json'))
        self.assertIn('x'*20000,data)
        for secret in ('secret-value','654321','sesskey=abc'):self.assertNotIn(secret,data)
        events,summary=inspect_trace(folder)
        self.assertEqual([e['event'] for e in events],['input','output']);self.assertFalse(summary['incomplete_spans'])

    def test_nested_error_identifies_tool_before_outer_final_failure(self):
        def inner():raise ValueError('Observed field changed')
        def outer():return self.trace.call(self.run,self.task,'moodle_browser','preflight',{},inner)
        with self.assertRaises(ValueError):self.trace.call(self.run,self.task,'workflow','execute',{},outer)
        _,summary=inspect_trace(self.root/self.run)
        self.assertEqual(summary['first_observed_error']['operation'],'preflight')
        path=build(self.root/self.run);self.assertTrue(path.exists())

    def test_trace_io_failure_never_prevents_real_call_or_masks_its_error(self):
        with patch.object(Path,'mkdir',side_effect=OSError('disk')):
            self.assertEqual(self.trace.call(self.run,self.task,'tool','read',{},lambda:42),42)
            with self.assertRaisesRegex(ValueError,'actual'):
                self.trace.call(self.run,self.task,'tool','save',{},lambda:(_ for _ in ()).throw(ValueError('actual')))
        self.assertEqual(self.trace.last_error,'OSError')

    def test_paths_and_incomplete_span_are_bounded_and_visible(self):
        self.trace.note('../outside',self.task,'tool','read',{})
        self.assertEqual(list(self.root.iterdir()),[])
        self.trace.write(self.run,self.task,'c'*32,'input','model','planner',{})
        _,summary=inspect_trace(self.root/self.run);self.assertEqual(summary['incomplete_spans'],['c'*32])

    def test_wrapping_preserves_return_and_invokes_tool_exactly_once(self):
        class Tool:
            calls=0
            def save(self,arg):self.calls+=1;return {'value':arg}
        tool=Tool();self.trace.wrap(tool,['save'],'tool',lambda:(self.run,self.task))
        self.assertEqual(tool.save(3),{'value':3});self.assertEqual(tool.calls,1)

    def test_prompt_embedded_json_secrets_are_redacted(self):
        prompt='Context: '+json.dumps({'cookie':'private-cookie','code':'123456','request':'read course'})
        cleaned=self.trace.clean(prompt)
        self.assertNotIn('private-cookie',cleaned);self.assertNotIn('123456',cleaned)
        self.assertIn('read course',cleaned)

    def test_runtime_nested_tool_is_bound_to_current_chat_not_first_active_chat(self):
        from unittest.mock import Mock
        from run_shared_chrome_agent import Runtime
        from chrome_structure import CourseStructure
        class FixtureRuntime(Runtime):
            def start(self,chat,record):return self.structure.verify({})
        with patch.object(CourseStructure,'verify',return_value={'verified':True}):
            runtime=FixtureRuntime(Mock(),Mock(),['one','two'],[],Mock(),self.root,Mock())
            for chat,flow in runtime.flows.items():flow.begin('Request','staff',chat)
            run=runtime.flows['two'].state['run']
            runtime.start('two',{})
        events,_=inspect_trace(self.root/'traces'/run)
        self.assertTrue(events);self.assertTrue(all(e['run']==run for e in events))
        other,_=inspect_trace(self.root/'traces'/runtime.flows['one'].state['run'])
        self.assertFalse(any(e['operation']=='verify' for e in other))


if __name__=='__main__':unittest.main()
