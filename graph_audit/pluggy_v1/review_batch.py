"""Review all shortlisted Pluggy changes and selected rejection audit controls."""
import concurrent.futures,importlib.util,json,os,threading
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('reviewer',HERE.parent/'agent_review/pilot.py')
reviewer=importlib.util.module_from_spec(spec);spec.loader.exec_module(reviewer)

class Budget:
 def __init__(self):self.lock=threading.Lock();self.calls=0;self.reserved=0
 def reserve(self,payload):
  estimate=(len(json.dumps(payload,ensure_ascii=False).encode())+4000)*1e-7+payload.get('max_tokens',1800)*5e-7
  with self.lock:
   if self.calls>=90 or self.reserved+estimate>.50:raise ValueError('Pluggy review budget exhausted')
   self.calls+=1;self.reserved+=estimate

def bundles():
 catalog=json.loads((HERE/'filtered.json').read_text());runs={r['candidate']:r for r in json.loads((HERE/'sandbox_summary.json').read_text())};out=[]
 for c in catalog['candidates']:
  runtime=runs[c['sha']];evidence={'candidate_metadata':c,'runtime_scope':runtime.get('scope'),'paired_measurements':runtime.get('comparison',{}),'repeat_measurements':runtime.get('repeat_comparison',{}),'environment_resolution':next(r for r in json.loads((HERE/'runtime_decisions.json').read_text()) if r['candidate']==c['sha'])}
  for state,run in runtime['runs'].items():
   evidence[state]={k:run.get(k) for k in ['status','exit_code','counts','environment']}
   evidence[state]['nonpassing_tests']={k:v for k,v in run.get('tests',{}).items() if v['status']!='pass'}
   if run['status']=='setup_or_report_failure':evidence[state]['log_tail']=(HERE/'sandbox'/c['sha']/state/'run.log').read_text()[-5000:]
  evidence['baseline']=evidence.get('base_original',{})
  out.append({'candidate':c,'clone':catalog['clone'],'evidence':evidence})
 # Audit the source-file cap and non-executable screening on actual rejected changes.
 for c in json.loads((HERE/'rejection_audit_inputs.json').read_text()):
  if c['reason'] not in ['too_many_source_files','no_executable_source_change']:continue
  clean={k:v for k,v in c.items() if k!='diff'};clean['fixed_sha']=c['sha']
  out.append({'candidate':clean,'clone':catalog['clone'],'evidence':{'candidate_metadata':clean,'runtime_scope':'Rejected control: no sandbox execution. Assess whether rejection loses a meaningful task; developer tooling can be meaningful but may require a separate environment.'}})
 metadata=json.loads((HERE/'github_metadata.json').read_text())
 for b in out:
  c=b['candidate'];number=c.get('pr_number')
  if not number:
   import re
   match=re.search(r'#(\d+)',c['subject']);number=int(match.group(1)) if match else None
  pr=metadata.get(str(number))
  if pr:
   b['evidence']['github_pr']={**pr,'matches_candidate_merge_sha':pr.get('merge_commit_sha')==c['sha'],'issue_linkage':'PR body may contain closing claims; no authoritative closed-issue linkage fetched.'}
 return out

def main():
 prompt=(HERE.parent/'agent_review/SYSTEM.md').read_text().replace('at most four model calls','at most six model calls').replace('Execution requests','Execution requests')
 prompt+='\n\n## Pluggy batch evidence\nRecorded baseline and paired sandbox measurements may now be supplied. Use them accurately; never say paired runs are absent if supplied. Treat a repeated paired signal as evidence requiring semantic review, not universal correctness. The decision ready_for_runtime_validation also means retain a candidate with an existing paired signal for final corpus review. The wrapper certification field stays unverified because the model does not certify tasks. Source-only behavior improvements with no F2P should be held for a regression test, not discarded merely for missing tests. Separate core library behavior, developer tooling, type/API contracts and mechanical cleanup. Analyze subtle changes even if the subject says refactor. Each patch page has up to 300 lines; use next_line and read several pages in parallel when needed. Six calls maximum. Keep the entire finish response under 650 words, each rubric entry at most 45 words.\n'
 (HERE/'REVIEW_PROMPT.md').write_text(prompt)
 key=os.environ.get('OPENROUTER_API_KEY') or json.loads((Path(os.environ.get('STRESS_STACK_CONFIG_DIR',str(Path.home()/'.config/stress-stack')))/'config.json').read_text()).get('api_key')
 if not key:raise SystemExit('No API key')
 budget=Budget();work=bundles()
 def run(b):
  path=HERE/'agent'/b['candidate']['sha']/'result.json'
  if path.exists():return json.loads(path.read_text())
  return reviewer.review(b,key.strip(),budget,directory=HERE/'agent',system=prompt,max_steps=6,page_lines=300)
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,work))
 usage=[s['usage'] for r in results for s in r['steps']]
 summary={'model':reviewer.MODEL,'workers':2,'calls_this_invocation':budget.calls,'reserved_this_invocation_usd':budget.reserved,'ceiling_usd':.50,'reported_cost_usd':sum(u.get('cost',0) or 0 for u in usage),'all_calls_have_cost':all('cost' in u for u in usage),'results':[{'candidate':r['candidate'],'status':r['status'],'assessment':r.get('assessment'),'reason':r.get('reason')} for r in results]}
 reviewer.write(HERE/'agent_summary.json',summary);print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
