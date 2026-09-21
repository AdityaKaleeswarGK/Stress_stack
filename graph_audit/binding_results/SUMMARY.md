# Python/Rust/JS-TS import-use validation

Fresh source scans; real repository code was not executed. The sample repository has separately passing Python, Rust and TypeScript runtime checks.

| Repository | Files | Symbols | File dependency pairs | Import bindings | Recorded uses | Scan seconds |
|---|---:|---:|---:|---:|---:|---:|
| glom | 33 | 637 | 64 | 393 | 2167 | 0.7135 |
| pluggy | 31 | 733 | 52 | 217 | 601 | 0.2659 |
| click | 81 | 1937 | 164 | 673 | 4674 | 4.1566 |
| calc-rs | 8 | 57 | 17 | 76 | 150 | 0.075 |
| rust-bump-allocator | 6 | 19 | 5 | 16 | 18 | 0.0359 |
| tui-calculator | 9 | 20 | 9 | 23 | 23 | 0.0354 |
| FlappyBird-TS-Canvas | 9 | 54 | 14 | 32 | 69 | 0.1512 |
| agustus | 5 | 56 | 4 | 19 | 107 | 0.0655 |
| sample | 12 | 28 | 13 | 33 | 36 | 0.0364 |

Counts are inventory measurements, not precision/recall. A binding can have zero recorded uses; that does not prove it is unused.

| Source-checked use | Result |
|---|---|
| glom: Assign.__init__ → Path.from_text → glom/core.py::Path.from_text | PASS |
| glom: Delete.__init__ → Path.from_text → glom/core.py::Path.from_text | PASS |
| pluggy: PluginManager.__init__ → _multicall → src/pluggy/_callers.py::_multicall | PASS |
| pluggy: test_pm → PluginManager → src/pluggy/_manager.py::PluginManager | PASS |
| calc-rs: MathEngine.evaluate → ASTNode → src/models.rs::ASTNode | PASS |
| rust-bump-allocator: BumpAllocator.alloc → reserve_space → src/state.rs::reserve_space | PASS |
| rust-bump-allocator: tests.test_state_mutation → get_stats → src/state.rs::get_stats | PASS |
| rust-bump-allocator: tests.test_bounds_detection → reserve_space → src/state.rs::reserve_space | PASS |
| FlappyBird-TS-Canvas: Renderer.render → GameSnapshot → types/game.ts::GameSnapshot | PASS |
| FlappyBird-TS-Canvas: Renderer.render → GamePhase.Menu → types/game.ts::GamePhase.Menu | PASS |
| FlappyBird-TS-Canvas: gameLoop → GamePhase.Active → types/game.ts::GamePhase.Active | PASS |
| agustus: createServer → groupBySavedPeriod → src/time-groups.ts::groupBySavedPeriod | PASS |
| agustus: fetch → AppEnv → src/auth.ts::AppEnv | PASS |
| sample: build → Box → typescript/src/model.ts::Basket | PASS |

14/14 focused source checks pass. These are development checks, not a held-out accuracy benchmark.

Saved spans, symbol-ID uniqueness, edge endpoints and reverse file-import queries were checked across every scan. Conditional imports remain labeled; macro tokens, wildcard resolution, string annotations, receiver type inference, `module.exports` assignment, dynamic `import()` and tsconfig path aliases remain outside this resolver.
