"""Pluggy v1: all selected base runs plus identical fixed-test-suite comparisons."""
from __future__ import annotations
import concurrent.futures,io,json,os,subprocess,tarfile,time,uuid,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
CAT=json.loads((OUT/'filtered.json').read_text())
DEC={r['candidate']:r for r in json.loads((OUT/'runtime_decisions.json').read_text())}
REPO=CAT['clone']

def save(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,indent=2)+'\n')
def git(*args):return subprocess.check_output(['git','-C',REPO,*args],timeout=60)
def call(args,log,timeout=600):
 r=subprocess.run(args,capture_output=True,timeout=timeout);log.write_bytes(r.stdout+r.stderr);return r

def prepare(version):
 d=OUT/'environments'/version;d.mkdir(parents=True,exist_ok=True)
 image='python:'+version+'-slim'
 pulled=call(['docker','pull','--platform','linux/amd64',image],d/'pull.log')
 if pulled.returncode:raise RuntimeError('Python image pull failed')
 info=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]
 digest=(info.get('RepoDigests') or [info['Id']])[0]
 # Resolve once per interpreter, then reuse exact image for every state in a pair.
 (d/'Dockerfile').write_text('FROM '+digest+'\nRUN python -m pip install pytest pytest-benchmark coverage setuptools setuptools-scm packaging && python -m pip uninstall -y pluggy\nENV PYTHONDONTWRITEBYTECODE=1 HOME=/tmp SETUPTOOLS_SCM_PRETEND_VERSION=1.6.0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1\n')
 tag='fleet-pluggy-v1:py'+version
 r=call(['docker','build','--platform','linux/amd64','-t',tag,str(d)],d/'build.log')
 if r.returncode:raise RuntimeError('Dependency environment build failed')
 ident=subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip()
 evidence={'python':version,'base_digest':digest,'image_id':ident,'recipe':'Latest resolver-compatible pytest/pytest-benchmark/coverage/build tools resolved once, reused within pairs; not historical lock replay. SCM version override 1.6.0 for Git archives. Third-party pytest plugin autoload disabled.'}
 save(d/'environment.json',evidence);return evidence

def extract(sha,d):
 d.mkdir(parents=True,exist_ok=True)
 with tarfile.open(fileobj=io.BytesIO(git('archive',sha))) as arc:arc.extractall(d,filter='data')

def results(xml):
 cases={}
 for t in ET.parse(xml).iter('testcase'):
  ident=t.get('classname','')+'::'+t.get('name','')
  if ident in cases:raise ValueError('Duplicate test identity')
  node=next((t.find(tag) for tag in ['error','failure','skipped'] if t.find(tag) is not None),None)
  status={'error':'error','failure':'fail','skipped':'skip'}.get(node.tag,'pass') if node is not None else 'pass'
  cases[ident]={'status':status,'message':(node.get('message','')+'\n'+(node.text or ''))[:8000] if node is not None else ''}
 return cases

def run_state(c,state,env,base,fixed):
 d=OUT/'sandbox'/c['sha']/state
 if (d/'result.json').exists():return json.loads((d/'result.json').read_text())
 d.mkdir(parents=True,exist_ok=True);ev=d/'evidence';ev.mkdir(exist_ok=True);ev.chmod(0o777)
 name='fleet-pluggy-'+uuid.uuid4().hex[:12]
 r={'candidate':c['sha'],'state':state,'base_sha':c['base_sha'],'fixed_sha':c['fixed_sha'],'environment':env,'status':'pending'}
 source=fixed if state.startswith('fixed') else base
 transplant=state.startswith('base_with_fixed_tests')
 shell='cp -R /snapshot/. /workspace/ && '
 if transplant:shell+='rm -rf /workspace/testing && cp -R /fixed/testing /workspace/testing && '
 shell+='cd /workspace && python -m pip install --no-index --no-deps --no-build-isolation --target /tmp/site . && export PYTHONPATH=/workspace/src:/tmp/site && python -m pip freeze > /results/dependencies.txt && python -c "import pluggy; print(pluggy.__file__); assert pluggy.__file__.startswith(\'/workspace/src/pluggy/\')" && python -m pytest testing -ra -p no:cacheprovider --junitxml=/results/junit.xml'
 cmd=['docker','run','--rm','--name',name,'--platform','linux/amd64','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=2g','--cpus=1','--pids-limit=256','--read-only','--tmpfs','/tmp:rw,nosuid,size=512m','--tmpfs','/workspace:rw,nosuid,size=128m','--user','65534:65534','--mount',f'type=bind,src={source},dst=/snapshot,readonly','--mount',f'type=bind,src={fixed},dst=/fixed,readonly','--mount',f'type=bind,src={ev},dst=/results',env['image_id'],'sh','-c',shell]
 r['command']=cmd;start=time.time()
 try:
  done=call(cmd,d/'run.log',timeout=300);r['exit_code']=done.returncode
  if (ev/'junit.xml').exists() and not (ev/'junit.xml').is_symlink():
   r['tests']=results(ev/'junit.xml');r['counts']={s:sum(v['status']==s for v in r['tests'].values()) for s in ['pass','fail','error','skip']}
   r['status']='passed' if done.returncode==0 and r['counts']['pass'] and not r['counts']['fail'] and not r['counts']['error'] else 'tests_failed'
  else:r['status']='setup_or_report_failure'
 except subprocess.TimeoutExpired:
  subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30);r['status']='timeout'
 except Exception as exc:r['status']='infrastructure_error';r['error']=str(exc)
 r['seconds']=round(time.time()-start,2);save(d/'result.json',r);return r

def compare(base,fixed):
 a=base.get('tests',{});b=fixed.get('tests',{});same=set(a)&set(b)
 return {'fail_to_pass':[k for k in sorted(same) if a[k]['status']=='fail' and b[k]['status']=='pass'],
 'pass_to_pass':[k for k in sorted(same) if a[k]['status']==b[k]['status']=='pass'],
 'pass_to_fail':[k for k in sorted(same) if a[k]['status']=='pass' and b[k]['status'] in ['fail','error']],
 'base_errors':[k for k,v in a.items() if v['status']=='error'],
 'base_only_tests':sorted(set(a)-set(b)),'fixed_only_tests':sorted(set(b)-set(a))}

def worker(c,environments):
 folder=OUT/'sandbox'/c['sha'];folder.mkdir(parents=True,exist_ok=True)
 result={'candidate':c['sha'],'subject':c['subject'],'certification':'unverified','runs':{}}
 try:
  resolution=DEC[c['sha']];version=resolution['common_python']
  if not version:raise ValueError('No common declared Python interpreter')
  env=environments[version];base=folder/'base_source';fixed=folder/'fixed_source'
  if not base.exists():extract(c['base_sha'],base)
  if not fixed.exists():extract(c['fixed_sha'],fixed)
  states=['base_original','base_with_fixed_tests','fixed']
  for state in states:result['runs'][state]=run_state(c,state,env,base,fixed)
  comparison=compare(result['runs']['base_with_fixed_tests'],result['runs']['fixed']);result['comparison']=comparison
  if comparison['fail_to_pass'] and result['runs']['fixed']['status']=='passed' and not comparison['base_errors'] and not comparison['base_only_tests'] and not comparison['fixed_only_tests'] and not comparison['pass_to_fail']:
   for state in ['base_with_fixed_tests_repeat','fixed_repeat']:result['runs'][state]=run_state(c,state,env,base,fixed)
   repeat=compare(result['runs']['base_with_fixed_tests_repeat'],result['runs']['fixed_repeat']);result['repeat_comparison']=repeat
   if repeat==comparison and result['runs']['fixed_repeat']['status']=='passed':result['certification']='paired_signal_reproduced_needs_semantic_review'
  result['scope']='Entire fixed testing/ tree on both sides; baseline uses original tests. No generated tests. Defaults from each snapshot configuration; not full upstream CI. Compare per-test failures, not exit codes alone.'
 except Exception as exc:result['error']=str(exc)
 save(folder/'summary.json',result);return result

if __name__=='__main__':
 versions=sorted({r['common_python'] for r in DEC.values() if r['common_python']})
 envs={v:(json.loads((OUT/'environments'/v/'environment.json').read_text()) if (OUT/'environments'/v/'environment.json').exists() else prepare(v)) for v in versions}
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:all_results=list(pool.map(lambda c:worker(c,envs),CAT['candidates']))
 save(OUT/'sandbox_summary.json',all_results)
 print(json.dumps([{'sha':r['candidate'][:10],'states':{s:v['status'] for s,v in r['runs'].items()},'f2p':len(r.get('comparison',{}).get('fail_to_pass',[])),'certification':r['certification'],'error':r.get('error')} for r in all_results],indent=2))
