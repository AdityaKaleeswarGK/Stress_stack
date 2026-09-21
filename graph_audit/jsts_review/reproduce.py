"""Read-only review probes; run with fleet/.venv/bin/python graph_audit/jsts_review/reproduce.py."""
from pathlib import Path
import json, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'fleet/src'))
from fleet.graph.parser import parse_source
from fleet.graph.resolve import resolve_edges
CASES = [
 ('require_use', {'lib.cjs':'module.exports = {run() {}};', 'a.cjs':'const lib = require("./lib.cjs"); function f() { lib.run(); }'}, {'uses': [('f','lib.run')]}),
 ('arrow_parameter', {'lib.js':'export function run() {}','a.js':'import {run} from "./lib"; const f = run => run();'}, {'uses': []}),
 ('var_scope', {'lib.js':'export function run() {}','a.js':'import {run} from "./lib"; function f() { if (true) { var run = () => 1; } run(); }'}, {'uses': []}),
 ('default_is_not_named', {'lib.js':'export default function run() {}','a.js':'import {run} from "./lib"; run();'}, {'status':'unresolved','target_symbol':None}),
 ('star_excludes_default', {'lib.js':'export default function run() {}','barrel.js':'export * from "./lib";','a.js':'import run from "./barrel"; run();'}, {'status':'unresolved','target_symbol':None}),
 ('explicit_overrides_star', {'lib.js':'export function run() {}','barrel.js':'export * from "./lib"; export function run() {}','a.js':'import {run} from "./barrel"; run();'}, {'status':'internal','target_symbol':'barrel.js::run'}),
 ('namespace_reexport', {'lib.js':'export function run() {}','barrel.js':'export * as tools from "./lib";','a.js':'import {tools} from "./barrel"; tools.run();'}, {'status':'internal','use_targets':['lib.js::run']}),
 ('typescript_source_priority', {'lib.js':'export function run() {}','lib.ts':'export function run() {}','a.ts':'import {run} from "./lib.js"; run();'}, {'target_symbol':'lib.ts::run'}),
]
results=[]
for name, sources, expected in CASES:
 files=[parse_source(p,s) for p,s in sources.items()]
 assert not any(f.has_syntax_error for f in files)
 resolve_edges(files)
 b=files[-1].bindings[0]
 observed={'status':b.status,'target_symbol':b.target_symbol,'uses':[(u['owner'],u['expression']) for u in b.uses], 'use_targets':[u.get('target_symbol') for u in b.uses]}
 results.append({'case':name,'source':sources,'expected':expected,'observed':observed,'passed':all(observed[k]==v for k,v in expected.items())})
output=Path(__file__).with_name('results.json')
output.write_text(json.dumps(results,indent=2)+'\n')
for r in results: print(r['case'], 'PASS' if r['passed'] else 'FAIL', r['observed'])
