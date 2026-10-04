"""Independent issue regression check, separate from the original pair evidence."""
import concurrent.futures,importlib.util,json,subprocess,uuid,sys
from pathlib import Path
P=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('runner',P/'run_sandboxes.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
prefix,probe,output = sys.argv[1:] if len(sys.argv)>1 else ('5d7bd','test_deferred_annotations.py','issue629_probe')
c=next(c for c in m.CAT['candidates'] if c['sha'].startswith(prefix))
env=json.loads((P/'environments/3.14/environment.json').read_text())
def run(state):
 d=P/output/state;d.mkdir(parents=True,exist_ok=True);ev=d/'evidence';ev.mkdir(exist_ok=True);ev.chmod(0o777)
 source=P/'sandbox'/c['sha']/('base_source' if state.startswith('base') else 'fixed_source')
 name='fleet-pluggy-probe-'+uuid.uuid4().hex[:10]
 cmd=['docker','run','--rm','--name',name,'--platform','linux/amd64','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=1g','--cpus=1','--pids-limit=128','--read-only','--tmpfs','/tmp:rw,nosuid,size=128m','--user','65534:65534','--mount',f'type=bind,src={source},dst=/snapshot,readonly','--mount',f'type=bind,src={P / "probes"},dst=/probes,readonly','--mount',f'type=bind,src={ev},dst=/results','-e','PYTHONPATH=/snapshot/src',env['image_id'],'python','-m','pytest','/probes/'+probe,'-q','-p','no:cacheprovider','--junitxml=/results/junit.xml']
 try:
  done=m.call(cmd,d/'run.log',120);tests=m.results(ev/'junit.xml');r={'state':state,'exit_code':done.returncode,'tests':tests,'command':cmd,'environment':env}
 except subprocess.TimeoutExpired:
  subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30);r={'state':state,'status':'timeout'}
 m.save(d/'result.json',r);return r
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(run,['base','fixed','base_repeat','fixed_repeat']))
m.save(P/output/'summary.json',rows)
print([(r['state'],r.get('exit_code'),{k:v['status'] for k,v in r.get('tests',{}).items()}) for r in rows])
