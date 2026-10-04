"""Small paid pilot with actual model-controlled runtime tools; resumable by result."""
import concurrent.futures,importlib.util,json,os,threading
from pathlib import Path
from fleet.runtime_review import RuntimeInvestigator,transitions
P=Path(__file__).resolve().parent
V1=P.parent/'pluggy_v1'
spec=importlib.util.spec_from_file_location('batch',V1/'review_batch.py');batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)
reviewer=batch.reviewer
class Budget:
 def __init__(self):self.lock=threading.Lock();self.calls=0;self.reserved=json.loads((P/'attempt1_setup_error/summary.json').read_text())['reserved_usd'] if (P/'attempt1_setup_error/summary.json').exists() else 0.
 def reserve(self,payload):
  amount=(len(json.dumps(payload).encode())+4000)*1e-7+payload['max_tokens']*5e-7
  with self.lock:
   if self.calls>=20 or self.reserved+amount>.20:raise ValueError('Runtime pilot API budget exhausted')
   self.calls+=1;self.reserved+=amount

def main():
 key=os.environ.get('OPENROUTER_API_KEY') or json.loads((Path(os.environ.get('STRESS_STACK_CONFIG_DIR',str(Path.home()/'.config/stress-stack')))/'config.json').read_text()).get('api_key')
 if not key:raise SystemExit('No API key')
 bundles=[b for b in batch.bundles() if b['candidate']['sha'].startswith(('1672de','20d814'))]
 old={r['candidate']:r for r in json.loads((V1/'sandbox_summary.json').read_text())}
 budget=Budget();prompt=(P/'SYSTEM.md').read_text()
 def work(b):
  sha=b['candidate']['sha'];folder=P/'reviews'/sha
  if (folder/'result.json').exists():return json.loads((folder/'result.json').read_text())
  prior=P/'attempt1_setup_error/experiments'/sha
  if prior.exists():
   b['evidence']['previous_probe_drafts']=[json.loads(f.read_text()) for f in prior.glob('*/request/request.json')]
   b['evidence']['runner_repair']='Previous attempts failed before tests due to the controller copy operation. Copying is now repaired and smoke-tested on both snapshots. Reuse or improve those probe drafts; their previous failures are not behavior evidence.'
  runs=old[sha]['runs'];e=transitions(runs['base_original']['tests'],runs['base_with_fixed_tests']['tests'],runs['fixed']['tests'])
  e['pass_to_pass_count']=len(e.pop('pass_to_pass'));b['evidence']['test_addition_analysis']=e
  # Avoid repeatedly transmitting hundreds of preservation IDs.
  for name in ('paired_measurements','repeat_measurements'):
   values=b['evidence'].get(name,{})
   if 'pass_to_pass' in values:values['pass_to_pass_count']=len(values.pop('pass_to_pass'))
  env=runs['base_original']['environment']
  runtime=RuntimeInvestigator(base=V1/'sandbox'/sha/'base_source',fixed=V1/'sandbox'/sha/'fixed_source',image_id=env['image_id'],directory=P/'experiments'/sha,max_experiments=4,timeout=90)
  return reviewer.review(b,key.strip(),budget,directory=P/'reviews',system=prompt,max_steps=10,page_lines=200,max_tokens=2200,runtime=runtime)
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(work,bundles))
 usage=[s['usage'] for r in results for s in r['steps']]
 summary={'model':reviewer.MODEL,'max_agents':2,'max_sandbox_containers':2,'api_ceiling_usd':.20,'reserved_usd':budget.reserved,'paid_calls':len(usage),'reported_cost_usd':sum(u.get('cost',0) or 0 for u in usage),'all_calls_report_cost':all('cost' in u for u in usage),'results':[{'candidate':r['candidate'],'status':r['status'],'runtime_experiments':r.get('runtime_experiments'),'assessment':r.get('assessment'),'reason':r.get('reason')} for r in results]}
 reviewer.write(P/'summary.json',summary);print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
