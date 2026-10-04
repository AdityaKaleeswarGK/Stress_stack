"""Bounded runtime tools for evidence-driven historical task review.

Agents submit pytest probes or repository test selectors, never host commands.
Snapshots and environment image IDs are selected by the controller.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import uuid
import xml.etree.ElementTree as ET

SLOTS = threading.BoundedSemaphore(2)


def transitions(original: dict, base: dict, fixed: dict) -> dict:
    """Keep newly collected passing tests distinct from change-detecting tests."""
    common = set(base) & set(fixed)
    new = set(fixed) - set(original)
    status = lambda rows, k: rows[k]['status']
    return {
        'fail_to_pass': sorted(k for k in common if status(base,k)=='fail' and status(fixed,k)=='pass'),
        'pass_to_pass': sorted(k for k in common if status(base,k)==status(fixed,k)=='pass'),
        'pass_to_fail': sorted(k for k in common if status(base,k)=='pass' and status(fixed,k) in ('fail','error')),
        'newly_collected_pass_on_both': sorted(k for k in new & common if status(base,k)==status(fixed,k)=='pass'),
        'newly_collected_fail_to_pass': sorted(k for k in new & common if status(base,k)=='fail' and status(fixed,k)=='pass'),
        'base_errors': sorted(k for k,v in base.items() if v['status']=='error'),
        'fixed_errors': sorted(k for k,v in fixed.items() if v['status']=='error'),
        'unmatched_base': sorted(set(base)-set(fixed)),
        'unmatched_fixed': sorted(set(fixed)-set(base)),
        'meaning': 'New test IDs may also reflect renames/parameterization. Passing on both can be useful coverage, not proof of a fix. Review test semantics.',
    }


def parse_tests(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        raise ValueError('Missing or oversized test report')
    tests = {}
    for case in ET.parse(path).iter('testcase'):
        key = case.get('classname','')+'::'+case.get('name','')
        if key in tests: raise ValueError('Duplicate test identifier')
        node = next((case.find(n) for n in ('error','failure','skipped') if case.find(n) is not None),None)
        state = {'error':'error','failure':'fail','skipped':'skip'}.get(node.tag,'pass') if node is not None else 'pass'
        tests[key] = {'status':state,'message':((node.get('message','')+'\n'+(node.text or ''))[:5000] if node is not None else '')}
    return tests


# Runs in the container, never on the host. The probe is executed by a separate pytest process.
DRIVER = '''import hashlib,json,os,pathlib,shutil,subprocess,sys
p=json.loads(pathlib.Path('/request/request.json').read_text())
for item in pathlib.Path('/snapshot').iterdir():
    target=pathlib.Path('/workspace')/item.name
    if item.is_dir():shutil.copytree(item,target,symlinks=True)
    else:shutil.copy2(item,target,follow_symlinks=False)
if p['existing_tests']:
    target=pathlib.Path('/workspace')/p['test_root']
    shutil.rmtree(target,ignore_errors=True)
    shutil.copytree(pathlib.Path('/fixed')/p['test_root'],target)
os.chdir('/workspace')
installed=subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-build-isolation','--target','/tmp/site','.'])
if installed.returncode:sys.exit(installed.returncode)
os.environ['PYTHONPATH']=':'.join('/workspace/'+x for x in p['source_roots'])+':/tmp/site'
args=[sys.executable,'-m','pytest','-q','-p','no:cacheprovider','--junitxml=/results/junit.xml']
if p['code']:
    pathlib.Path('/tmp/test_agent_probe.py').write_text(p['code'])
    args.append('/tmp/test_agent_probe.py')
args.extend(p['existing_tests'])
def hashes():
    return {str(f):hashlib.sha256(f.read_bytes()).hexdigest() for root in p['integrity_roots'] for f in (pathlib.Path('/workspace')/root).rglob('*') if f.is_file() and '__pycache__' not in str(f)}
before=hashes()
code=subprocess.run(args).returncode
pathlib.Path('/results/source_integrity.json').write_text(json.dumps({'unchanged':before==hashes()}))
sys.exit(code)
'''


class RuntimeInvestigator:
    """One candidate's sandbox budget; a semaphore bounds the whole process to two."""
    def __init__(self, *, base: Path, fixed: Path, image_id: str, directory: Path,
                 max_experiments: int = 6, timeout: int = 90,
                 test_root: str = "testing", source_roots: tuple[str, ...] = ("src",),
                 integrity_roots: tuple[str, ...] = ("src",)):
        self.base, self.fixed = base.resolve(), fixed.resolve()
        if not self.base.is_dir() or not self.fixed.is_dir(): raise ValueError('Prepared snapshots required')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',image_id): raise ValueError('Use a pinned local image ID')
        self.image_id, self.directory = image_id, directory.resolve()
        self.max_experiments, self.timeout = max_experiments, timeout
        for root in (test_root, *source_roots, *integrity_roots):
            if not root or Path(root).is_absolute() or ".." in Path(root).parts or not re.fullmatch(r"[\w./-]+",root):
                raise ValueError("Layout roots must be safe repository-relative paths")
        self.test_root, self.source_roots, self.integrity_roots = test_root, source_roots, integrity_roots
        self.calls = 0
        self.observations = []
        self.blocked = False

    @staticmethod
    def validate(args, test_root="testing"):
        if set(args) != {'purpose','code','existing_tests','repeat'}: raise ValueError('Invalid runtime request fields')
        if not isinstance(args['purpose'],str) or not args['purpose'].strip(): raise ValueError('State a hypothesis/purpose')
        code=args['code']; selectors=args['existing_tests']
        if not isinstance(code,str) or len(code)>16000: raise ValueError('Probe must be at most 16000 characters')
        if type(args['repeat']) is not bool: raise ValueError('repeat must be boolean')
        if not isinstance(selectors,list) or len(selectors)>8: raise ValueError('At most eight existing test selectors')
        for value in selectors:
            if not isinstance(value,str) or len(value)>300 or not re.fullmatch(re.escape(test_root)+r'(?:/[\w./-]+)?(?:::[\w.\[\]-]+)*',value) or '..' in value.split('::')[0].split('/'):
                raise ValueError('Use '+test_root+'/ paths or simple test node IDs, not options or commands')
        if not code.strip() and not selectors: raise ValueError('Supply a probe or existing tests')

    def _run(self, snapshot, request_dir, state):
        folder=request_dir/state;folder.mkdir();evidence=folder/'evidence';evidence.mkdir();evidence.chmod(0o777)
        name='fleet-agent-'+uuid.uuid4().hex[:16]
        command=['docker','run','--rm','--name',name,'--platform','linux/amd64','--network=none',
                 '--cap-drop=ALL','--security-opt=no-new-privileges','--memory=1g','--cpus=1','--pids-limit=128',
                 '--read-only','--tmpfs','/tmp:rw,nosuid,size=256m','--tmpfs','/workspace:rw,nosuid,size=128m,mode=1777','--user','65534:65534',
                 '--mount',f'type=bind,src={snapshot},dst=/snapshot,readonly',
                 '--mount',f'type=bind,src={self.fixed},dst=/fixed,readonly',
                 '--mount',f'type=bind,src={request_dir / "request"},dst=/request,readonly',
                 '--mount',f'type=bind,src={evidence},dst=/results',self.image_id,'python','/request/driver.py']
        result={'state':state,'command':command,'status':'unverified'}
        with SLOTS:
            try:
                with (folder/'run.log').open('wb') as log:
                    done=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=self.timeout)
                result['exit_code']=done.returncode
                result['tests']=parse_tests(evidence/'junit.xml')
                integrity=evidence/'source_integrity.json'
                result['source_unchanged']=not integrity.is_symlink() and json.loads(integrity.read_text()).get('unchanged') is True
                result['status']='measured' if result['source_unchanged'] else 'source_modified_invalid'
            except subprocess.TimeoutExpired:
                subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
                result['status']='timeout'
            except (OSError,ValueError,ET.ParseError) as exc:
                result['status']='setup_or_report_error';result['error']=str(exc)[:400]
        with (folder/'run.log').open('rb') as log:
            log.seek(0,2);log.seek(max(0,log.tell()-6000));result['log_tail']=log.read().decode(errors='replace')
        (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        return result

    def read_log(self, args):
        number, state, start = args['experiment'], args['state'], args['start_line']
        if type(number) is not int or not 1 <= number <= self.calls or state not in ('base','fixed','base_repeat','fixed_repeat') or type(start) is not int or start < 1:
            raise ValueError('Select a completed experiment/state and positive start line')
        path=self.directory/f'experiment-{number:02d}'/state/'run.log'
        if path.is_symlink() or not path.is_file(): raise ValueError('No log for this state')
        lines=[]
        with path.open(errors='replace') as stream:
            for index,line in enumerate(stream,1):
                if index < start: continue
                if index >= start+120: break
                lines.append(f'{index}: {line[:1000]}')
        return {'evidence_id':f'runtime:{number}','content':''.join(lines)[:16000], 'next_line':start+len(lines) if len(lines)==120 else None}

    def __call__(self,args):
        self.validate(args, self.test_root)
        if self.blocked: raise ValueError('Prepared environment failed before test measurement; controller repair needed, do not repeat the same setup')
        if self.calls >= self.max_experiments: raise ValueError('Sandbox investigation budget exhausted; finish with remaining uncertainty')
        self.calls+=1
        folder=self.directory/f'experiment-{self.calls:02d}';(folder/'request').mkdir(parents=True,exist_ok=False)
        (folder/'request/request.json').write_text(json.dumps({**args,'test_root':self.test_root,'source_roots':self.source_roots,'integrity_roots':self.integrity_roots},indent=2)+'\n')
        (folder/'request/driver.py').write_text(DRIVER)
        runs={}
        for state in (['base','fixed','base_repeat','fixed_repeat'] if args['repeat'] else ['base','fixed']):
            if state.endswith('repeat') and any(r['status']!='measured' for r in runs.values()): break
            runs[state]=self._run(self.base if state.startswith('base') else self.fixed,folder,state)
        pairs=transitions({},runs['base'].get('tests',{}),runs['fixed'].get('tests',{}))
        repeat=transitions({},runs['base_repeat'].get('tests',{}),runs['fixed_repeat'].get('tests',{})) if 'fixed_repeat' in runs else None
        self.blocked=any(r['status']=='setup_or_report_error' for r in runs.values())
        stable=bool(repeat is not None and all(r['status']=='measured' for r in runs.values()) and
                    {k:v['status'] for k,v in runs['base']['tests'].items()}=={k:v['status'] for k,v in runs['base_repeat']['tests'].items()} and
                    {k:v['status'] for k,v in runs['fixed']['tests'].items()}=={k:v['status'] for k,v in runs['fixed_repeat']['tests'].items()})
        observation={'evidence_id':f'runtime:{self.calls}','purpose':args['purpose'],'comparison':pairs,'repeat_stable':stable,
                     'remaining_experiments':self.max_experiments-self.calls,'runs':runs,'certification':'unverified_agent_authored_probe'}
        (folder/'summary.json').write_text(json.dumps(observation,indent=2)+'\n');self.observations.append(observation)
        # Compact tool observation; full commands, logs and test records stay on disk.
        return {**{k:v for k,v in observation.items() if k!='runs'},'runs':{s:{'status':r['status'],'exit_code':r.get('exit_code'),
                'counts':{v:sum(t['status']==v for t in r.get('tests',{}).values()) for v in ('pass','fail','error','skip')},
                'nonpassing':{k:v for k,v in r.get('tests',{}).items() if v['status']!='pass'},
                'log_tail':r['log_tail'] if r['status']!='measured' else None} for s,r in runs.items()}}
