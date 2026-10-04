"""Recover wrapper/schema failures without changing model judgments or redoing reads."""
import copy,importlib.util,json,os,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('r',P.parent/'agent_review/pilot.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
summary=json.loads((P/'agent_summary.json').read_text());records=[];extra_reserved=0
for path in sorted((P/'agent').glob('*/result.json')):
 d=json.loads(path.read_text())
 if d['status']=='reviewed':continue
 old=copy.deepcopy(d);bundle=json.loads((path.parent/'input.json').read_text())
 ids=set(bundle['evidence']);ids.add('baseline') # explicit alias of supplied base_original evidence
 covered=set();total=None;decision=None;reads=[]
 for step in d['steps']:
  for action in step['actions']:
   name=action['tool'];args=action['arguments'];obs=action.get('observation',{})
   if name=='read_patch' and 'error' not in obs:
    ids.add('patch');covered.update(range(obs['start_line'],obs['end_line']+1));total=obs['total_lines'];reads.append(action)
   elif name=='read_file' and 'error' not in obs:
    ids.add('file:'+args['snapshot']+':'+args['path']);reads.append(action)
   elif name=='finish' and decision is None:
    try:r.validate_finish(args,ids,total is not None and len(covered)==total);decision=args
    except ValueError:pass
 r.write(path.parent/'attempt1/result.json',old)
 if decision is not None:
  d.update(status='reviewed',assessment=decision,recovery={'method':'deterministic_citation_alias','alias':{'baseline':'base_original'},'model_judgment_changed':False,'additional_model_calls':0})
 else:
  # Exactly one bounded repair call; the complete patch and prior reads are supplied.
  key=os.environ.get('OPENROUTER_API_KEY') or json.loads((Path(os.environ.get('STRESS_STACK_CONFIG_DIR',str(Path.home()/'.config/stress-stack')))/'config.json').read_text()).get('api_key')
  prompt=(P/'REVIEW_PROMPT.md').read_text()+'\nYour previous output was truncated. All prior read observations are attached. Return finish only, under 600 words. Allowed evidence IDs: '+json.dumps(sorted(ids))
  payload={'model':r.MODEL,'messages':[{'role':'system','content':prompt},{'role':'user','content':json.dumps({'input':bundle,'prior_read_observations':reads,'evidence_aliases':{'baseline':'base_original'}})}],'tools':[r.TOOLS[-1]],'tool_choice':{'type':'function','function':{'name':'finish'}},'max_tokens':3200,'provider':{'require_parameters':True,'allow_fallbacks':False,'max_price':{'prompt':.10,'completion':.50}}}
  estimate=(len(json.dumps(payload).encode())+4000)*1e-7+3200*5e-7
  if summary['reserved_this_invocation_usd']+extra_reserved+estimate>.50:raise RuntimeError('Batch ceiling would be exceeded')
  extra_reserved+=estimate
  request=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',json.dumps(payload).encode(),{'Authorization':'Bearer '+key.strip(),'Content-Type':'application/json'})
  with urllib.request.urlopen(request,timeout=120) as response:data=json.load(response)
  r.write(path.parent/'repair_usage.json',{'usage':data.get('usage'),'response_id':data.get('id')})
  msg=data['choices'][0]['message'];call=msg['tool_calls'][0];decision=json.loads(call['function']['arguments'])
  r.validate_finish(decision,ids,total is not None and len(covered)==total)
  d['steps'].append({'step':len(d['steps'])+1,'response_id':data.get('id'),'served_model':data.get('model'),'usage':data.get('usage',{}),'actions':[{'tool':'finish','arguments':decision}],'recovery':True})
  d.update(status='reviewed',assessment=decision,recovery={'method':'one_bounded_completion_repair','additional_model_calls':1,'max_tokens':3200})
 d.pop('reason',None);r.write(path,d);records.append({'candidate':d['candidate'],**d['recovery']})
all_results=[json.loads(path.read_text()) for path in sorted((P/'agent').glob('*/result.json'))]
usage=[s['usage'] for item in all_results for s in item['steps']]
summary.update(recovery=records,total_calls=len(usage),reserved_upper_estimate_usd=summary['reserved_this_invocation_usd']+extra_reserved,reported_cost_usd=sum(u.get('cost',0) or 0 for u in usage),all_calls_have_cost=all('cost' in u for u in usage),results=[{'candidate':d['candidate'],'status':d['status'],'assessment':d.get('assessment'),'recovery':d.get('recovery')} for d in all_results])
r.write(P/'agent_summary.json',summary);print(json.dumps({'total_calls':summary['total_calls'],'cost':summary['reported_cost_usd'],'recovery':records},indent=2))
