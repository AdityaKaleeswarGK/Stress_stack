"""Base-only pilot: execute historical code exclusively in restricted Docker containers."""
import concurrent.futures, hashlib, io, json, os, pathlib, subprocess, tarfile, threading, time, uuid
ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / 'graph_audit/base_validation/results'
REPORT = json.loads((ROOT/'graph_audit/initial_filter/corrected.json').read_text())
LOCK = threading.Lock()

def call(args, timeout=600, **kw):
    return subprocess.run(args, capture_output=True, timeout=timeout, **kw)

def git(*args):
    return subprocess.check_output(['git','-C',REPORT['clone'],*args])

def save(path, obj):
    path.write_text(json.dumps(obj, indent=2)+'\n')

def worker(c):
    d=OUT/c['sha']; d.mkdir(parents=True, exist_ok=True)
    result={'candidate':c['sha'],'base_sha':c['base_sha'],'subject':c['subject'], 'stage':'base_only','status':'pending','runs':[], 'runtime':'Docker Desktop runc; not gVisor/Firecracker'}
    started=time.time(); name='fleet-base-'+uuid.uuid4().hex[:16]
    try:
        req=git('show',c['base_sha']+':requirements.txt').decode()
        version='3.7' if 'pytest==4.1.1' in req else ('3.9' if 'PyYAML==5.4.1' in req else '3.11')
        image='python:'+version+'-slim'
        result['python_image_requested']=image
        inspect=call(['docker','image','inspect',image],timeout=30)
        if inspect.returncode: raise RuntimeError('Required base image unavailable')
        data=json.loads(inspect.stdout)[0]; pinned=(data.get('RepoDigests') or [data['Id']])[0]
        result['base_image']=pinned
        src=d/'source'; src.mkdir(exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(git('archive',c['base_sha']))) as archive: archive.extractall(src,filter='data')
        dockerfile='\n'.join(['FROM '+pinned,'WORKDIR /workspace','COPY source/ /workspace/', 'RUN python -m pip install -r requirements.txt && python -m pip install --no-deps --no-build-isolation -e .','ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1',''])
        (d/'Dockerfile').write_text(dockerfile)
        tag='fleet-base-pilot:'+c['base_sha'][:16]
        build=call(['docker','build','--platform','linux/amd64','-t',tag,'-f',str(d/'Dockerfile'),str(d)],timeout=600)
        (d/'build.log').write_bytes(build.stdout+build.stderr)
        result['build_exit_code']=build.returncode
        if build.returncode: result['status']='environment_build_failed'; return result
        ident=call(['docker','image','inspect',tag,'--format','{{.Id}}']); result['environment_image_id']=ident.stdout.decode().strip()
        evidence=d/'evidence'; evidence.mkdir(exist_ok=True); evidence.chmod(0o777)
        command=['docker','run','--rm','--name',name,'--platform','linux/amd64','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--memory=2g','--cpus=1','--pids-limit=256','--read-only','--tmpfs','/tmp:rw,nosuid,size=512m','--user','65534:65534','--mount',f'type=bind,src={evidence},dst=/results','--workdir','/workspace',result['environment_image_id'],'sh','-c','python -m pip freeze > /results/dependencies.txt; python -c "import glom; print(glom.__file__)"; python -m pytest glom/test -ra -p no:cacheprovider --junitxml=/results/junit.xml']
        result['command']=command
        run=call(command,timeout=600); (d/'run.log').write_bytes(run.stdout+run.stderr)
        result['test_exit_code']=run.returncode
        import xml.etree.ElementTree as ET
        xml=evidence/'junit.xml'
        if xml.exists() and not xml.is_symlink():
            cases=[]
            for t in ET.parse(xml).iter('testcase'):
                status='error' if t.find('error') is not None else 'fail' if t.find('failure') is not None else 'skip' if t.find('skipped') is not None else 'pass'
                cases.append({'id':t.get('classname','')+'::'+t.get('name',''),'status':status})
            result['tests']=cases; result['counts']={s:sum(x['status']==s for x in cases) for s in ['pass','fail','error','skip']}
            result['status']='base_healthy' if run.returncode==0 and result['counts']['pass']>0 and not result['counts']['fail'] and not result['counts']['error'] else 'base_tests_failed'
        else: result['status']='test_report_missing'
    except subprocess.TimeoutExpired:
        call(['docker','rm','-f',name],timeout=30); result['status']='timeout'
    except Exception as exc:
        result['status']='infrastructure_error'; result['error']=str(exc)
    finally:
        result['seconds']=round(time.time()-started,2); save(d/'result.json',result)
        with LOCK: print(c['sha'][:10],result['status'],result.get('counts',{}),flush=True)
    return result

if __name__=='__main__':
    OUT.mkdir(parents=True,exist_ok=True)
    for version in ['3.7','3.9','3.11']:
        pull=call(['docker','pull','--platform','linux/amd64','python:'+version+'-slim'],timeout=600)
        (OUT/('pull-'+version+'.log')).write_bytes(pull.stdout+pull.stderr)
        print('image',version,pull.returncode,flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,REPORT['candidates']))
    save(OUT/'summary.json',{'stage':'base_only','candidate_count':len(results),'workers':2,'results':results})
    print('COMPLETE',len(results),flush=True)
