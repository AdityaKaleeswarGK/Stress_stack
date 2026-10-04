"""Run the existing 21 Glom candidates with the runtime reviewer; saved and resumable."""
import concurrent.futures,importlib.util,io,json,os,subprocess,tarfile,threading,sys
from pathlib import Path
from fleet.runtime_review import RuntimeInvestigator
P=Path(__file__).resolve().parent
ROOT=P.parents[1]
CAT=json.loads((P.parent/'initial_filter/corrected.json').read_text())
spec=importlib.util.spec_from_file_location('reviewer',P.parent/'agent_review/pilot.py');reviewer=importlib.util.module_from_spec(spec);spec.loader.exec_module(reviewer)
def save(path,data):reviewer.write(path,data)
def prior(c):return json.loads((P.parent/'base_validation/results'/c['sha']/'result.json').read_text())
def prepare(c):
 d=P/'snapshots'/c['sha'];d.mkdir(parents=True,exist_ok=True)
 for state in ['base','fixed']:
  dest=d/state
  if dest.exists():continue
  raw=subprocess.check_output(['git','-C',CAT['clone'],'archive',c[state+'_sha']],timeout=60)
  dest.mkdir()
  with tarfile.open(fileobj=io.BytesIO(raw)) as archive:archive.extractall(dest,filter='data')
 return d

def runtime(c,directory,max_experiments=4):
 d=prepare(c);e=prior(c)
 return RuntimeInvestigator(base=d/'base',fixed=d/'fixed',image_id=e['environment_image_id'],directory=directory,
   max_experiments=max_experiments,timeout=120,test_root='glom/test',source_roots=('.',),integrity_roots=('glom',))

class Budget:
 def __init__(self):
  self.lock=threading.Lock();self.calls=0;self.reserved=0
  if (P/'budget.json').exists():
   d=json.loads((P/'budget.json').read_text());self.calls=d['calls'];self.reserved=d['reserved_usd']
 def reserve(self,payload):
  amount=(len(json.dumps(payload).encode())+4000)*1e-7+payload['max_tokens']*5e-7
  with self.lock:
   if self.calls>=210 or self.reserved+amount>1.0:raise ValueError('Glom API budget exhausted')
   self.calls+=1;self.reserved+=amount;save(P/'budget.json',{'calls':self.calls,'reserved_usd':self.reserved,'ceiling_usd':1.0})

def bundle(c):
 old=prior(c);tests=old.get('tests',[])
 evidence={'candidate_metadata':c,'baseline':{k:old.get(k) for k in ['stage','status','python_image_requested','counts','environment_image_id']},
  'nonpassing_base_tests':[t for t in tests if t['status']!='pass'],
  'runtime_scope':'Only original-base execution previously recorded for this candidate. Fixed and same-test paired execution are not supplied. Runtime tool provides actual new paired evidence. Base/fixed use the SAME existing pinned dependency image. This is not historical dependency lock replay.',
  'environment_policy':'Reuse the previously executed baseline environment, not assume the resolver newest version was tested. If this interpreter differs from the feature target or fixed dependencies change, flag incomplete matrix/dependency coverage. Missing dependencies are environment gaps, not behavioral failures.',
  'available_budget':{'experiments':4,'model_calls':10,'timeout_per_container_seconds':120},
  'repository_layout':{'test_root':'glom/test','import_root':'/workspace','language_version':old['python_image_requested']}}
 rpath=P.parent/'runtime_resolution/glom-pr271-base/result.json'
 if c['sha'].startswith('d285'):
  r=json.loads(rpath.read_text());evidence['older_interpreter_baseline']={k:r[k] for k in ['sha','selected_python','counts','status']}
 # PR links from commit hints remain unverified; do not invent issue bodies.
 return {'candidate':c,'clone':CAT['clone'],'evidence':evidence}

def smoke():
 results=[]
 for prefix in ['6fd413','343150','db6fee']:
  c=next(c for c in CAT['candidates'] if c['sha'].startswith(prefix))
  result=runtime(c,P/'smoke'/c['sha'],1)({'purpose':'Verify Glom layout, offline installation and test import on both snapshots','code':'import glom\ndef test_import():\n    assert callable(glom.glom)\n','existing_tests':[],'repeat':False})
  results.append({'candidate':c['sha'],**result})
 save(P/'smoke_summary.json',results)
 if any(r['status']!='measured' or r['counts']['pass']!=1 for item in results for r in item['runs'].values()):raise SystemExit('Smoke gate failed; inspect saved logs before paid calls')
 print('Three interpreter families passed base/fixed smoke checks.')

def main():
 if '--smoke' in sys.argv:smoke();return
 smoke_results=json.loads((P/'smoke_summary.json').read_text())
 assert len(smoke_results)==3 and all(r['status']=='measured' and r['counts']['pass']==1 for item in smoke_results for r in item['runs'].values())
 key=os.environ.get('OPENROUTER_API_KEY') or json.loads((Path(os.environ.get('STRESS_STACK_CONFIG_DIR',str(Path.home()/'.config/stress-stack')))/'config.json').read_text()).get('api_key')
 if not key:raise SystemExit('Missing OpenRouter key')
 prompt=(P.parent/'runtime_agent_v2/SYSTEM.md').read_text().replace('testing/', 'glom/test/')
 prompt+='\nGlom full batch: review all supplied candidates independently. Use the actual recorded Python version when writing probes (some are Python 3.7). Existing baseline may have known failures on a compatibility target. Source and test files can share the glom/ package. To provide preservation evidence efficiently, include existing_tests=["glom/test"] with a focused new probe or run the complete fixed test suite first. The tool transplants identical fixed tests on both snapshots. For broad patches, read all pages using next_line; do not finish without the complete diff. Paired all-pass results can justify retention for coverage or behavior-preserving work when the contract is meaningful; avoid inventing a bug to force F2P. Diagnose failed assertions before claiming useful tasks. Keep final answer under 600 words.\n'
 (P/'SYSTEM.md').write_text(prompt)
 budget=Budget()
 def work(c):
  path=P/'reviews'/c['sha']/'result.json'
  if path.exists():return json.loads(path.read_text())
  return reviewer.review(bundle(c),key.strip(),budget,directory=P/'reviews',system=prompt,max_steps=10,page_lines=300,max_tokens=2200,runtime=runtime(c,P/'experiments'/c['sha']))
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(work,CAT['candidates']))
 usage=[s['usage'] for r in rows for s in r['steps']]
 save(P/'summary.json',{'candidate_count':len(rows),'model':reviewer.MODEL,'max_agents':2,'max_containers':2,'api_ceiling_usd':1.,'reserved_usd':budget.reserved,'calls':budget.calls,'reported_cost_usd':sum(u.get('cost',0) or 0 for u in usage),'all_completed_calls_report_cost':all('cost' in u for u in usage),'results':[{'candidate':r['candidate'],'status':r['status'],'assessment':r.get('assessment'),'runtime_experiments':r.get('runtime_experiments'),'reason':r.get('reason')} for r in rows]})
 print('Glom batch finished; results saved.')
if __name__=='__main__':main()
