import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('pilot',Path(__file__).with_name('pilot.py'))
pilot=importlib.util.module_from_spec(spec);spec.loader.exec_module(pilot)

class Guards(unittest.TestCase):
    def decision(self):
        return dict(decision='ready_for_runtime_validation',change_type='bugfix',rubric={k:'Evidence pending' for k in pilot.DIMENSIONS},evidence=['patch'],next_checks=['Run paired regression test'],problem_statement='Boundary access should raise IndexError')
    def test_no_certification_decision(self):
        args=self.decision();args['decision']='corpus_ready'
        with self.assertRaises(ValueError):pilot.validate_finish(args,{'patch'},True)
    def test_missing_patch_or_fabricated_evidence(self):
        with self.assertRaises(ValueError):pilot.validate_finish(self.decision(),{'patch'},False)
        args=self.decision();args['evidence']=['imaginary_run']
        with self.assertRaises(ValueError):pilot.validate_finish(args,{'patch'},True)
    def test_budget_refuses_extra_or_oversized_calls(self):
        b=pilot.Budget()
        with self.assertRaises(ValueError):b.reserve({'content':'x'*600000})
        self.assertEqual(b.calls,0)
        for _ in range(12):b.reserve({})
        with self.assertRaises(ValueError):b.reserve({})
    def test_paging_no_silent_truncation(self):
        content='\n'.join(str(i) for i in range(350));start=1;seen=[]
        while start:
            p=pilot.page(content,start);seen.extend(range(p['start_line'],p['end_line']+1));start=p['next_line']
        self.assertEqual(seen,list(range(1,351)))
    def test_pilot_baselines_never_claim_pairs(self):
        bundles=pilot.bundles();self.assertEqual(len(bundles),3)
        self.assertIn('F2P/P2P not measured',bundles[0]['evidence']['validation_scope'])
        self.assertIn('compatibility_baseline',bundles[1]['evidence'])
        self.assertNotIn('baseline',bundles[2]['evidence'])
if __name__=='__main__':unittest.main()

class LoopRecovery(unittest.TestCase):
    def test_malformed_finish_is_repairable_without_leaking_key(self):
        import tempfile,json
        from unittest.mock import patch
        def response(name,args,n):
            return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':str(n),'type':'function','function':{'name':name,'arguments':args}}]}}],'usage':{'cost':0}}
        args=Guards().decision()
        responses=[response('read_patch','{"start_line":1}',1),response('finish','{"decision":',2),response('finish',json.dumps(args),3)]
        class Reply:
            def __init__(self,data):self.data=data
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(self.data).encode()
        with tempfile.TemporaryDirectory() as d:
            with patch.object(pilot.urllib.request,'urlopen',side_effect=[Reply(r) for r in responses]),patch.object(pilot,'git',return_value='one line'):
                result=pilot.review({'candidate':{'sha':'fixed','base_sha':'base','fixed_sha':'fixed'},'clone':'unused','evidence':{}},'secret-test-key',pilot.Budget(),directory=Path(d),system='Test reviewer')
            self.assertEqual(result['status'],'reviewed')
            self.assertIn('Malformed',result['steps'][1]['actions'][0]['observation']['error'])
            self.assertNotIn('secret-test-key',(Path(d)/'fixed/result.json').read_text())
