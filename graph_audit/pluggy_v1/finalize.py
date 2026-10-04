"""Persist evidence-backed audit decisions, separate from raw model recommendations."""
import hashlib,json
from pathlib import Path
P=Path(__file__).resolve().parent
load=lambda name:json.loads((P/name).read_text())
initial=load('filtered.json');agents={r['candidate']:r for r in load('agent_summary.json')['results']};runs={r['candidate']:r for r in load('sandbox_summary.json')}
choices={
 '09baae8733':('exclude','Mechanical lint/style cleanup; no demonstrated task-relevant behavior difference.'),
 '735edb0ab6':('include_in_v1_candidate_corpus','Public distribution-listing feature is meaningful. Count the public API test as the useful F2P; three private-module import failures are structural evidence, not three independent bug reproductions.'),
 '5d7bd2cc08':('include_in_v1_candidate_corpus','Deferred-annotation compatibility is a real bug. Independent public registration regression reproduces issue #629 on base and passes on fixed twice; bundled tests primarily exposed warning/API changes.'),
 '20d8143f12':('include_in_v1_candidate_corpus','Duplicate hook caller bug has a direct repeated assertion F2P and 125 P2P tests. The other added unregister test passes on both and is preservation coverage.'),
 '108f2250fa':('hold_tooling_environment','Useful downstream tooling rather than core library fix. Existing tests do not execute the script; mutable dependencies and || true need a separate reproducible test contract.'),
 '584be1e372':('hold_performance_or_supported_behavior_specification','Cleanup/performance work may be useful, but the agent-suggested wrapper-factory problem is rejected during registration on both versions. No supported-API F2P established; do not use that proposed problem statement.'),
 'dbe21314fb':('reject_reference_patch_for_v1','Independent tracing probe passes on base and fails on fixed twice: a registered falsey callable is no longer called. The 124 existing passing tests missed this behavior regression.'),
 '0c501acaca':('exclude','Agent configuration and guidance only. Fixed the settings JSON classification; the corrected filter now excludes it before sandbox work.'),
 '1672de415f':('hold_regression_test','Lazy version access is a plausible source-only task. Existing 124 tests provide no distinguishing F2P; needs an installed-package/lazy-lookup regression.')}
records=[]
for c in initial['candidates']:
 prefix=c['sha'][:10];decision,reason=choices[prefix];runtime=runs[c['sha']];comparison=runtime['comparison']
 records.append({'id':c['sha'],'base_sha':c['base_sha'],'fixed_sha':c['fixed_sha'],'pr_number':c['pr_number'],'pr_url':'https://github.com/pytest-dev/pluggy/pull/'+str(c['pr_number']),'subject':c['subject'],'agent_decision':agents[c['sha']]['assessment']['decision'],'audited_decision':decision,'reason':reason,'observed_existing_test_suite':{'fail_to_pass':comparison['fail_to_pass'],'pass_to_pass_count':len(comparison['pass_to_pass']),'pass_to_fail':comparison['pass_to_fail']},'training_ready':False,'scope':'Initial candidate corpus decision under the recorded environment; not a guarantee of all behavior or a completed training task specification.'})
for prefix,reason in [('7a700d0907','Recover as tooling candidate: substantive downstream runner was rejected by the 10-source-file cap.'),('2c1fd62786','Retain optional coverage-tooling task: meaningful configuration effect despite no executable Python AST change.')]:
 a=next(a for sha,a in agents.items() if sha.startswith(prefix));records.append({'id':a['candidate'],'audited_decision':'defer_recovered_tooling_candidate','agent_decision':a['assessment']['decision'],'reason':reason,'training_ready':False,'sandbox_executed':False})
output={'version':'pluggy-pipeline-v1-audited','ref':initial['ref'],'history_scope':initial['limits'],'original_selected':9,'selected_after_config_classification_fix':8,'candidate_cap_hit':False,'decisions':records,'included_ids':[r['id'] for r in records if r['audited_decision']=='include_in_v1_candidate_corpus'],'model_cost_usd':load('agent_summary.json')['reported_cost_usd'],'limitations':['100 first-parent changes only; no completeness claim for older history.','Linux amd64 Docker Desktop runc, not gVisor/Firecracker; Python 3.14 except version-lazy candidate on 3.13.','Dependency set frozen by shared image after current resolution; not historical uv.lock replay. Synthetic SCM version 1.6.0 for archive packaging.','Paired tests replace the entire testing/ tree; relocated private imports need semantic audit.','Agents have read-only tools; additional probe executions were performed by the orchestrating assistant, not the OpenRouter reviewer.','PR merge SHAs verified against GitHub; closed issue references are textual claims plus issue state, not authoritative closing-event linkage.']}
(P/'final_corpus.json').write_text(json.dumps(output,indent=2)+'\n')
files=['filtered.json','filtered_after_config_fix.json','runtime_decisions.json','sandbox_summary.json','agent_summary.json','final_corpus.json','REVIEW_PROMPT.md','rejection_audit.json']
manifest={'version':output['version'],'ref':initial['ref'],'max_agent_workers':2,'max_sandbox_workers':2,'successful_batch_sandbox_invocations':33,'targeted_probe_invocations':12,'pretest_copy_failure_invocations':27,'paid_model_calls':load('agent_summary.json')['total_calls'],'sha256':{f:hashlib.sha256((P/f).read_bytes()).hexdigest() for f in files}}
(P/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'include':len(output['included_ids']),'decisions':len(records),'cost':output['model_cost_usd']},indent=2))
