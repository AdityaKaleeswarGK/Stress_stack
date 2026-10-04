"""Focused sandbox checks of independently resolved runtime choices."""
import concurrent.futures,io,json,pathlib,subprocess,tarfile,time,uuid,sys,xml.etree.ElementTree as ET
ROOT=pathlib.Path(__file__).resolve().parents[2]
OUT=ROOT/'graph_audit/runtime_resolution'
D=json.loads((OUT/'decisions.json').read_text())
row=next(x for x in D['glom'] if x['candidate'].startswith('d285'))
cases=[('glom-pr271-base','glom',row['base'],'python -m pip install -r requirements.txt && python -m pip install --no-deps --no-build-isolation -e .','glom/test'),('click-head','click',D['other_repositories']['click'],'python -m pip install . pytest','tests')]

def run(args,log,timeout=600):
 r=subprocess.run(args,capture_output=True,timeout=timeout);log.write_bytes(r.stdout+r.stderr);return r

def work(case):
 name,repo,decision,install,tests=case; d=OUT/name;d.mkdir(exist_ok=True)
 r={'name':name,'sha':decision['sha'],'selected_python':decision['selected_python'],'resolver_decision':decision,'status':'pending','recipe_scope':'Focused test execution; not a replay of the complete upstream CI/lockfile workflow'}
 cname='fleet-resolver-'+uuid.uuid4().hex[:12];start=time.time()
 try:
  image='python:'+decision['selected_python']+'-slim'
  pull=run(['docker','pull','--platform','linux/amd64',image],d/'pull.log')
  if pull.returncode:raise RuntimeError('image pull failed')
  data=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0];pin=(data.get('RepoDigests') or [data['Id']])[0];r['base_image_digest']=pin
  src=d/'source';src.mkdir(exist_ok=True)
  raw=subprocess.check_output(['git','-C','/Users/adityagk/Desktop/projects/'+repo,'archive',decision['sha']])
  with tarfile.open(fileobj=io.BytesIO(raw)) as arc:arc.extractall(src,filter='data')
  (d/'Dockerfile').write_text(f'FROM {pin}\nWORKDIR /workspace\nCOPY source/ /workspace/\nRUN {install}\nENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1\n')
  tag='fleet-resolver:'+name
  build=run(['docker','build','--platform','linux/amd64','-t',tag,'-f',str(d/'Dockerfile'),str(d)],d/'build.log')
  if build.returncode:raise RuntimeError('environment build failed')
  imageid=subprocess.check_output(['docker','image','inspect',tag,'--format','{{.Id}}'],text=True).strip();r['image_id']=imageid
  ev=d/'evidence';ev.mkdir(exist_ok=True);ev.chmod(0o777)
  cmd=['docker','run','--rm','--name',cname,'--platform','linux/amd64','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=2g','--cpus=1','--pids-limit=256','--read-only','--tmpfs','/tmp:rw,nosuid,size=512m','--user','65534:65534','--mount',f'type=bind,src={ev},dst=/results','--workdir','/workspace',imageid,'sh','-c',f'python --version; python -m pip freeze > /results/dependencies.txt; python -c "import {repo}; print({repo}.__file__)"; python -m pytest {tests} -ra -p no:cacheprovider --junitxml=/results/junit.xml']
  r['command']=cmd;done=run(cmd,d/'run.log');r['exit_code']=done.returncode
  counts=dict.fromkeys(['pass','fail','error','skip'],0)
  for t in ET.parse(ev/'junit.xml').iter('testcase'):
   status='error' if t.find('error') is not None else 'fail' if t.find('failure') is not None else 'skip' if t.find('skipped') is not None else 'pass';counts[status]+=1
  r['counts']=counts;r['status']='tests_passed' if done.returncode==0 and counts['pass'] else 'tests_failed'
 except Exception as e:
  r['status']='blocked';r['error']=str(e)
  subprocess.run(['docker','rm','-f',cname],capture_output=True,timeout=30)
 finally:
  r['seconds']=round(time.time()-start,2);(d/'result.json').write_text(json.dumps(r,indent=2)+'\n')
 return r

if __name__=='__main__':
 if '--click-with-less' in sys.argv:
  _,repo,decision,install,tests=cases[1]
  cases=[('click-head-with-less',repo,decision,'apt-get update && apt-get install -y --no-install-recommends less && '+install,tests)]
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(work,cases))
 (OUT/('execution-less-summary.json' if '--click-with-less' in sys.argv else 'execution-summary.json')).write_text(json.dumps(results,indent=2)+'\n')
 print([(r['name'],r['status'],r.get('counts')) for r in results])
