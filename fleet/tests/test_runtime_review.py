import pytest
from fleet.runtime_review import RuntimeInvestigator, transitions


def test_added_tests_passing_both_are_coverage_not_f2p():
    original={'old':{'status':'pass'}}
    base={**original,'new':{'status':'pass'},'fix':{'status':'fail'}}
    fixed={k:{'status':'pass'} for k in base}
    result=transitions(original,base,fixed)
    assert result['newly_collected_pass_on_both']==['new']
    assert result['newly_collected_fail_to_pass']==['fix']
    assert result['pass_to_pass']==['new','old']


@pytest.mark.parametrize('selector',['--override-ini=x','/tmp/test.py','testing/../../secret','testing;whoami','testing/test.py && true'])
def test_runtime_selectors_cannot_escape_or_inject_options(selector):
    with pytest.raises(ValueError):
        RuntimeInvestigator.validate({'purpose':'test','code':'','existing_tests':[selector],'repeat':False})


def test_empty_probe_and_large_code_rejected():
    for code in ['', 'x'*16001]:
        with pytest.raises(ValueError):
            RuntimeInvestigator.validate({'purpose':'test','code':code,'existing_tests':[],'repeat':False})


def test_errors_skips_and_absent_tests_do_not_become_passes():
    result=transitions({}, {'error':{'status':'error'},'skip':{'status':'skip'},'lost':{'status':'pass'}}, {'error':{'status':'pass'},'skip':{'status':'pass'}})
    assert not result['fail_to_pass'] and not result['pass_to_pass']
    assert result['base_errors']==['error'] and result['unmatched_base']==['lost']


def test_setup_failures_stop_repeats_and_further_attempts(tmp_path, monkeypatch):
    base=tmp_path/'base';fixed=tmp_path/'fixed';base.mkdir();fixed.mkdir()
    runner=RuntimeInvestigator(base=base,fixed=fixed,image_id='sha256:'+'a'*64,directory=tmp_path/'output')
    calls=[]
    def failed(snapshot,folder,state):
        calls.append(state)
        return {'status':'setup_or_report_error','tests':{},'log_tail':'setup failure'}
    monkeypatch.setattr(runner,'_run',failed)
    result=runner({'purpose':'probe','code':'def test_ok(): pass','existing_tests':[],'repeat':True})
    assert calls==['base','fixed'] and not result['repeat_stable']
    with pytest.raises(ValueError,match='controller repair'):
        runner({'purpose':'retry','code':'def test_ok(): pass','existing_tests':[],'repeat':False})
    assert runner.calls==1


def test_runtime_experiment_budget_is_enforced(tmp_path, monkeypatch):
    base=tmp_path/'base';fixed=tmp_path/'fixed';base.mkdir();fixed.mkdir()
    runner=RuntimeInvestigator(base=base,fixed=fixed,image_id='sha256:'+'a'*64,directory=tmp_path/'output',max_experiments=1)
    monkeypatch.setattr(runner,'_run',lambda *a:{'status':'measured','tests':{'x':{'status':'pass'}},'log_tail':''})
    args={'purpose':'probe','code':'def test_ok(): pass','existing_tests':[],'repeat':False}
    assert runner(args)['remaining_experiments']==0
    with pytest.raises(ValueError,match='budget exhausted'):runner(args)


def test_glom_layout_allows_only_its_test_tree(tmp_path):
    args={'purpose':'Glom suite','code':'','existing_tests':['glom/test/test_core.py::test_path'],'repeat':False}
    RuntimeInvestigator.validate(args,test_root='glom/test')
    with pytest.raises(ValueError):RuntimeInvestigator.validate(args)
    args['existing_tests']=['glom/test/../../../etc/passwd']
    with pytest.raises(ValueError):RuntimeInvestigator.validate(args,test_root='glom/test')
    for name in ['base','fixed']:(tmp_path/name).mkdir()
    with pytest.raises(ValueError,match='Layout roots'):
        RuntimeInvestigator(base=tmp_path/'base',fixed=tmp_path/'fixed',image_id='sha256:'+'a'*64,directory=tmp_path/'out',source_roots=('/etc',))
