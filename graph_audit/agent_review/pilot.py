"""Bounded, read-only, two-worker OpenRouter reviewer pilot. No automatic retries."""
from __future__ import annotations
import concurrent.futures, json, os, subprocess, threading, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MODEL = 'openai/gpt-6-luna-pro'
DECISIONS = ['ready_for_runtime_validation','needs_regression_test','needs_environment_review','needs_specification_review','retain_coverage_candidate','retain_behavior_preserving_candidate','exclude']
DIMENSIONS = ['behavior','coherence','specification','regression_tests','runtime_evidence','environment','stability']

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n')

def git(repo, *args):
    return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True).stdout

def schema(name, props, required):
    return {'type':'function','function':{'name':name,'description':name.replace('_',' '),'parameters':{'type':'object','properties':props,'required':required,'additionalProperties':False}}}

STRING={'type':'string'}
STRINGS={'type':'array','items':STRING}
TOOLS=[schema('read_patch',{'start_line':{'type':'integer'}},['start_line']),
 schema('read_file',{'snapshot':{'type':'string','enum':['base','fixed']},'path':STRING,'start_line':{'type':'integer'}},['snapshot','path','start_line']),
 schema('finish',{'decision':{'type':'string','enum':DECISIONS},'change_type':STRING,
 'rubric':{'type':'object','properties':{k:STRING for k in DIMENSIONS},'required':DIMENSIONS,'additionalProperties':False},
 'evidence':STRINGS,'next_checks':STRINGS,'problem_statement':STRING},['decision','change_type','rubric','evidence','next_checks','problem_statement'])]

def page(text,start,lines_per_page=160):
    if type(start) is not int or start<1: raise ValueError('start_line must be positive')
    lines=text.splitlines(); chosen=[]; size=0
    for index,line in enumerate(lines[start-1:start-1+lines_per_page],start):
        if size+len(line)>18000: break
        chosen.append(f'{index}: {line}');size+=len(line)
    if start<=len(lines) and not chosen: raise ValueError('Line too long')
    end=start+len(chosen)-1
    return {'content':'\n'.join(chosen),'start_line':start,'end_line':end,'total_lines':len(lines),'next_line':end+1 if end<len(lines) else None}

class Budget:
    def __init__(self): self.lock=threading.Lock();self.reserved=0.;self.calls=0
    def reserve(self,payload):
        # UTF-8 bytes upper-bound ordinary tokenizer token counts, plus ample protocol overhead.
        tokens=len(json.dumps(payload,ensure_ascii=False).encode())+4000
        cost=tokens*.0000001+1800*.0000005
        with self.lock:
            if self.calls>=12 or self.reserved+cost>.05: raise ValueError('Pilot reservation budget exhausted')
            self.calls+=1;self.reserved+=cost


def validate_finish(args,read_ids,patch_complete):
    if not patch_complete: raise ValueError('Read the complete patch before finishing')
    if args.get('decision') not in DECISIONS: raise ValueError('Invalid decision')
    if set(args.get('rubric',{}))!=set(DIMENSIONS) or not all(isinstance(v,str) and v.strip() for v in args['rubric'].values()): raise ValueError('All rubric dimensions required')
    for key in ['change_type','problem_statement']:
        if not isinstance(args.get(key),str): raise ValueError('Expected text')
    for key in ['evidence','next_checks']:
        if not isinstance(args.get(key),list) or not all(isinstance(v,str) for v in args[key]): raise ValueError('Expected string list')
    if not args['evidence'] or not set(args['evidence'])<=read_ids: raise ValueError('Cite only observed evidence IDs')
    if args['decision']!='exclude' and not args['next_checks']: raise ValueError('Unverified candidates need next checks')


def review(bundle,key,budget,*,directory=None,system=None,max_steps=4,page_lines=160,max_tokens=1800,runtime=None):
    folder=(directory or HERE/'results')/bundle['candidate']['sha'];write(folder/'input.json',bundle)
    messages=[{'role':'system','content':system or (HERE/'SYSTEM.md').read_text()},{'role':'user','content':json.dumps(bundle)}]
    active_tools=list(TOOLS)
    if runtime is not None:
        active_tools.insert(-1,schema('read_runtime_log',{'experiment':{'type':'integer'},'state':{'type':'string','enum':['base','fixed','base_repeat','fixed_repeat']},'start_line':{'type':'integer'}},['experiment','state','start_line']))
        active_tools.insert(-1,schema('run_runtime_check',{'purpose':STRING,'code':STRING,'existing_tests':STRINGS,'repeat':{'type':'boolean'}},['purpose','code','existing_tests','repeat']))
    result={'candidate':bundle['candidate']['sha'],'model':MODEL,'status':'incomplete','certification':'unverified','execution_enabled':runtime is not None,'steps':[]}
    read_ids=set(bundle['evidence']);covered=set();total=None
    try:
        for step in range(max_steps):
            payload={'model':MODEL,'messages':messages,'tools':active_tools,'tool_choice':'auto','max_tokens':max_tokens,'provider':{'require_parameters':True,'allow_fallbacks':False,'max_price':{'prompt':.10,'completion':.50}}}
            budget.reserve(payload)
            request=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',json.dumps(payload).encode(),{'Authorization':'Bearer '+key,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(request,timeout=120) as response: data=json.load(response)
            except urllib.error.HTTPError as exc: raise RuntimeError(f'OpenRouter HTTP {exc.code}; no retry') from None
            if data.get('error') or not data.get('choices'): raise RuntimeError('No completion; no retry')
            msg=data['choices'][0]['message'];messages.append(msg)
            event={'step':step+1,'response_id':data.get('id'),'served_model':data.get('model'),'usage':data.get('usage',{}),'actions':[]};result['steps'].append(event)
            calls=msg.get('tool_calls') or []
            if len(calls)>6: raise ValueError('Too many tools')
            if not calls: messages.append({'role':'user','content':'Use tools and finish within remaining call budget.'})
            for call in calls:
                name=call['function']['name'];raw_arguments=call['function']['arguments'];args={}
                try:
                    args=json.loads(raw_arguments)
                    c=bundle['candidate'];repo=bundle['clone']
                    if name=='finish':
                        if runtime is not None and runtime.calls < 1: raise ValueError('Run at least one runtime experiment before finishing this investigation')
                        validate_finish(args,read_ids,total is not None and len(covered)==total)
                        result.update(status='reviewed',assessment=args);event['actions'].append({'tool':name,'arguments':args});return result
                    if name=='read_patch':
                        obs=page(git(repo,'diff','--no-ext-diff','--no-textconv','--no-color',c['base_sha'],c['fixed_sha'],'--'),args['start_line'],page_lines)
                        covered.update(range(obs['start_line'],obs['end_line']+1));total=obs['total_lines'];read_ids.add('patch')
                    elif name=='read_runtime_log' and runtime is not None:
                        obs=runtime.read_log(args);read_ids.add(obs['evidence_id'])
                    elif name=='run_runtime_check' and runtime is not None:
                        obs=runtime(args);read_ids.add(obs['evidence_id'])
                    elif name=='read_file':
                        path=args['path'];snap=args['snapshot']
                        if not isinstance(path,str) or Path(path).is_absolute() or '..' in Path(path).parts or snap not in ['base','fixed']: raise ValueError('Invalid historical file')
                        spec=c[snap+'_sha']+':'+path
                        if int(git(repo,'cat-file','-s',spec))>1000000: raise ValueError('File too large')
                        obs=page(git(repo,'show',spec),args['start_line'],page_lines);read_ids.add('file:'+snap+':'+path)
                    else: raise ValueError('Unknown tool')
                except (ValueError,KeyError,TypeError,subprocess.CalledProcessError) as exc:
                    obs={'error':str(exc) if not isinstance(exc,subprocess.CalledProcessError) else 'Historical file unavailable','available_evidence_ids':sorted(read_ids)}
                    if isinstance(exc,json.JSONDecodeError):obs['error']='Malformed or truncated tool JSON. Shorten your response and submit valid JSON.'
                event['actions'].append({'tool':name,'arguments':args,'observation':obs})
                messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(obs)})
        result['reason']='Model call limit reached'
    except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
        result['reason']=type(exc).__name__+': '+str(exc) if not isinstance(exc,OSError) else 'Network/IO error; no retry'
    finally:
        if runtime is not None: result['runtime_experiments']=runtime.calls
        write(folder/'result.json',result)
    return result


def bundles():
    catalog=json.loads((ROOT/'graph_audit/initial_filter/corrected.json').read_text())
    original=json.loads((ROOT/'graph_audit/initial_filter/original.json').read_text())
    selected=[next(c for c in catalog['candidates'] if c['sha'].startswith(prefix)) for prefix in ['6fd413','d285e7']]
    selected.append(next(c for c in original['candidates'] if c['sha'].startswith('573cb')))
    output=[]
    for c in selected:
        evidence={}
        p=ROOT/'graph_audit/base_validation/results'/c['sha']/'result.json'
        if p.exists():
            raw=json.loads(p.read_text());evidence['baseline']={k:raw[k] for k in ['base_sha','stage','status','python_image_requested','test_exit_code','tests','counts'] if k in raw}
            evidence['baseline']['tests']=[t for t in raw.get('tests',[]) if t.get('status')!='pass']
        if c['sha'].startswith('d285'):
            raw=json.loads((ROOT/'graph_audit/runtime_resolution/glom-pr271-base/result.json').read_text())
            evidence['compatibility_baseline']={k:raw[k] for k in ['sha','selected_python','status','counts','recipe_scope','resolver_decision']}
            evidence['comparison_limits']='Dependencies were not frozen identically across runs. No fixed-sha execution or paired regression test run exists.'
        evidence['validation_scope']='Base-only runs where supplied. F2P/P2P not measured. PR/issue text and verified closing linkage not supplied. Number hints alone are unverified.'
        clean={k:c[k] for k in ['sha','base_sha','fixed_sha','subject','message','files','loc_changed','source_loc','test_loc'] if k in c}
        output.append({'candidate':clean,'clone':catalog['clone'],'evidence':evidence})
    return output


def main():
    if (HERE/'results').exists(): raise SystemExit('Results already exist; refusing accidental paid rerun')
    path=Path(os.environ.get('STRESS_STACK_CONFIG_DIR',str(Path.home()/'.config/stress-stack')))/'config.json'
    key=os.environ.get('OPENROUTER_API_KEY') or json.loads(path.read_text()).get('api_key')
    if not isinstance(key,str) or not key.strip(): raise SystemExit('No configured key')
    budget=Budget()
    write(HERE/'expectations.json',{'6fd413':'Preserve the small behavioral fix; request paired tests; do not certify F2P.','d285e7':'Recognize Python compatibility; request target-version paired tests, not reject for baseline failures.','573cb':'Exclude docstring typo as non-behavioral.','scope':'Three hand-selected cases, not an accuracy benchmark.'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda b:review(b,key.strip(),budget),bundles()))
    usage=[s['usage'] for r in results for s in r['steps']]
    summary={'model':MODEL,'max_workers':2,'max_calls':12,'max_reserved_usd':.05,'calls':budget.calls,'reserved_upper_estimate_usd':budget.reserved,'reported_cost_usd':sum(u.get('cost',0) or 0 for u in usage),'cost_reported_for_all_calls':all('cost' in u for u in usage) and len(usage)==budget.calls,'prompt_tokens':sum(u.get('prompt_tokens',0) for u in usage),'completion_tokens':sum(u.get('completion_tokens',0) for u in usage),'results':[{'candidate':r['candidate'],'status':r['status'],'assessment':r.get('assessment'),'reason':r.get('reason')} for r in results]}
    write(HERE/'summary.json',summary);print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
