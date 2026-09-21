# Small repository for checking import graphs

`python/` is a Python package with a runtime check. `rust/` is a dependency-free
Cargo library with an integration test. These are runnable source examples,
not only strings embedded in parser tests.

Expected examples:

| Source | Local import | Used inside | Original definition |
|---|---|---|---|
| Python service | `Box` | `build` | `model.py::Basket` |
| Python service | `twice` | `build` | `model.py::double` |
| Python service | `local_double` | `nested.run` | `model.py::double` |
| Rust service | `Box` | `build` (`Box::new`) | `model.rs::Basket.new` |
| Rust service | `twice` | `nested.run` | `model.rs::double` |
| Rust integration test | `Basket` | `imported_paths_work` | `model.rs::Basket`, through the library re-export |

Python `shadow(twice)` takes a parameter. Its `twice(3)` must **not** be linked
to the imported function. External `json` / `std::cmp::max` retain their uses
without a fabricated local definition.

See [the graph guide](../../GRAPH.md) for scan/query and runtime commands.
The assertions are in [test_import_bindings.py](../test_import_bindings.py).
