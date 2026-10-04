import importlib.util
from pathlib import Path
import tempfile,unittest
spec=importlib.util.spec_from_file_location('runner',Path(__file__).with_name('run_sandboxes.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Checks(unittest.TestCase):
 def test_collection_error_is_not_f2p(self):
  a={'tests':{'collection':{'status':'error'},'regression':{'status':'fail'},'preserve':{'status':'pass'}}}
  b={'tests':{'collection':{'status':'pass'},'regression':{'status':'pass'},'preserve':{'status':'fail'}}}
  c=m.compare(a,b)
  self.assertEqual(c['fail_to_pass'],['regression']);self.assertEqual(c['base_errors'],['collection']);self.assertEqual(c['pass_to_fail'],['preserve'])
 def test_missing_test_is_not_passing(self):
  c=m.compare({'tests':{'gone':{'status':'pass'}}},{'tests':{'new':{'status':'pass'}}})
  self.assertFalse(c['pass_to_pass']);self.assertEqual(c['base_only_tests'],['gone']);self.assertEqual(c['fixed_only_tests'],['new'])
 def test_junit_status_and_duplicate_ids(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'j.xml';p.write_text('<testsuite><testcase classname="a" name="x"><failure message="bad">assertion</failure></testcase></testsuite>')
   self.assertEqual(m.results(p)['a::x']['status'],'fail')
   p.write_text('<testsuite><testcase name="x"/><testcase name="x"/></testsuite>')
   with self.assertRaises(ValueError):m.results(p)
if __name__=='__main__':unittest.main()
