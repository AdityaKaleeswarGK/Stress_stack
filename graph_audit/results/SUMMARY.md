# Fleet graph hypothesis audit

Generated: 2026-09-11T08:04:23.058235+00:00

Fresh scans of local working trees. Scanner code is unchanged. Saved graphs are copied into this audit directory; source repositories are not modified.

## Corpus measurements

| Repository | Files | Symbols | Import edges | Files with incoming edges | Calls in memory / saved | Missing Python definitions* |
|---|---:|---:|---:|---:|---:|---:|
| fleet | 24 | 124 | 0 | 0 | 493 / 0 | 0 |
| glom | 33 | 570 | 56 | 10 | 3100 / 0 | 67 |
| pluggy | 31 | 297 | 33 | 7 | 1591 / 0 | 436 |
| click | 81 | 1312 | 198 | 15 | 7900 / 0 | 625 |
| agustus | 6 | 83 | 4 | 3 | 352 / 0 | 0 |
| recipehub | 29 | 117 | 39 | 26 | 1142 / 0 | 0 |
| calc-rs | 8 | 55 | 14 | 6 | 305 / 0 | 0 |
| rust-bump-allocator | 6 | 17 | 3 | 2 | 68 / 0 | 0 |
| tui-calculator | 9 | 20 | 9 | 3 | 52 / 0 | 0 |
| TinyGoRPC | 8 | 50 | 0 | 0 | 389 / 0 | 0 |
| tiny-rpc | 7 | 33 | 0 | 0 | 201 / 0 | 0 |
| zero-copy-http-parser | 5 | 16 | 5 | 3 | 42 / 0 | 0 |
| FlappyBird-TS-Canvas | 9 | 46 | 14 | 5 | 120 / 0 | 0 |

*Independent AST traversal includes nested and conditional definitions that Fleet intentionally does not fully index today. This measures a capability gap against the proposed complete hierarchy, not a regression against its existing narrow contract.

Import counts do not measure precision or recall: external imports legitimately have no internal edge. A file-level edge also does not prove which symbol was imported or called.

## Previous saved graphs

| Repository | Previous schema | Previous import edges | Fresh import edges |
|---|---|---:|---:|
| agustus | 0.1.0 | 4 | 4 |
| recipehub | 0.2.0 | 39 | 39 |
| calc-rs | 0.2.0 | 0 | 14 |
| rust-bump-allocator | 0.2.0 | 0 | 3 |
| tui-calculator | 0.1.0 | 9 | 9 |

Old artifacts have no matching source/configuration fingerprints, so differences are observations, not measured improvement on an identical snapshot.

## Explicit acceptance probes

These are hand-specified source fixtures, including missing features. Passing all would establish these cases only. `cases.json` contains the source and expected result; `results.json` contains observed results.

| Probe | Category | Result |
|---|---|---|
| Python file-class-method containment | structure | PASS |
| Python nested function identity | proposed capability | GAP |
| Python conditional definition | proposed capability | GAP |
| Python flat-layout import | resolution | PASS |
| Python src-layout import | resolution | GAP |
| Python relative import | resolution | PASS |
| Python two submodules in one import | resolution | GAP |
| Python alias retained in raw statement | information loss | GAP |
| Local inheritance | resolution | PASS |
| External base must not bind unrelated local class | false positive | GAP |
| Imported aliased base resolves to its declaration | resolution | GAP |
| Cross-language same-name base must not bind | false positive | GAP |
| Python method caller is qualified | scope | GAP |
| Nested call belongs only to nested function | scope | GAP |
| Nested yield must not make outer a generator | scope | GAP |
| Calls survive serialization | information loss | GAP |
| Docstrings survive serialization | information loss | GAP |
| Python exact body span available | proposed capability | GAP |
| Direct Python invocation edge | proposed capability | GAP |
| TypeScript relative file import | resolution | PASS |
| TypeScript function and method caller ownership | scope | GAP |
| JavaScript CommonJS dependency | proposed capability | GAP |
| TypeScript barrel re-export dependency | proposed capability | GAP |
| JavaScript same-file class inheritance | resolution | PASS |
| Rust crate module use | resolution | PASS |
| Rust module declaration dependency | proposed capability | GAP |
| Rust trait implementation is not method inheritance | relationship semantics | GAP |
| Go receiver method same-file membership | structure | PASS |
| Go grouped type declarations retain every type | structure | GAP |
| Java Maven-style source import | resolution | PASS |
| Java same-line overload IDs stay unique | identity | GAP |
| Java overloads on different lines stay unique | identity | PASS |

10/32 acceptance probes pass. This intentionally challenging set is not a representative accuracy benchmark.

## Source-checked repository questions

Expected answers were specified from inspected source; they are not inferred from the graph. Source fragments, locations and observed answers are recorded in results.json. These selected cases measure capabilities, not corpus-wide accuracy.

| Repository | Question | Result |
|---|---|---|
| FlappyBird-TS-Canvas | Find Renderer class | PASS |
| FlappyBird-TS-Canvas | Renderer contains its render method | PASS |
| FlappyBird-TS-Canvas | main.ts appears in renderer.ts imported-by lookup | PASS |
| FlappyBird-TS-Canvas | Find the GameSnapshot interface used by render | GAP |
| FlappyBird-TS-Canvas | Resolve render to its same-class clearCanvas call | GAP |
| glom | Find Path.from_text nested create function | GAP |
| glom | Retrieve Path.from_text docstring from saved graph | GAP |
| pluggy | Resolve test imports to pluggy public package | GAP |
| pluggy | Resolve manager relative import of callers module | PASS |
| pluggy | Find test-local plugin class test_pm.A | GAP |
| calc-rs | Resolve Rust engine import of models | PASS |
| rust-bump-allocator | Resolve integration test import through Cargo crate name | GAP |
| TinyGoRPC | Retain raw Go package import | PASS |
| TinyGoRPC | Resolve Go package-qualified constructor call | GAP |
| agustus | Resolve TypeScript auth import | PASS |

No target repository tests or dependency installers were executed. Coverage edges and LLM enrichment quality were not measured. No assertion of complete semantic resolution, task validity, or historical reproducibility follows from these scans.
