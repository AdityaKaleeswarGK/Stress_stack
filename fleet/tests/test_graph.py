from __future__ import annotations

from pathlib import Path

from fleet.graph.resolve import resolve_edges
from fleet.graph.spec import detect_language
from fleet.graph.parser import parse_source
from fleet.graph.scan import scan_repo, write_graph


def test_detect_language() -> None:
    assert detect_language("a/b.py") == "python"
    assert detect_language("a/b.rs") == "rust"
    assert detect_language("a/b.go") == "go"
    assert detect_language("a/b.tsx") == "tsx"
    assert detect_language("a/b.txt") is None


def test_python_symbols_and_imports() -> None:
    code = """
import os
from pathlib import Path

class Greeter:
    def hello(self, name):
        return f"hi {name}"

def add(a, b):
    return a + b

def test_add():
    assert add(1, 2) == 3
"""
    parsed = parse_source("pkg/greet.py", code)
    assert parsed.language == "python"
    assert parsed.parser == "ast"
    assert not parsed.has_syntax_error

    names = {s.qualified_name: s for s in parsed.symbols}
    assert names["Greeter"].kind == "class"
    assert names["Greeter.hello"].kind == "method"
    assert names["add"].kind == "function"
    assert names["test_add"].is_test is True
    assert names["add"].is_test is False

    modules = {i.module for i in parsed.imports}
    assert "os" in modules
    assert "pathlib" in modules


def test_python_class_bases_are_captured() -> None:
    code = """
class Animal:
    def speak(self):
        pass

class Dog(Animal):
    def speak(self):
        return "Woof"
"""
    parsed = parse_source("animals.py", code)
    by_name = {s.name: s for s in parsed.symbols}
    assert by_name["Animal"].bases == ()
    assert by_name["Dog"].bases == ("Animal",)


def test_python_syntax_error_is_reported_not_swallowed() -> None:
    parsed = parse_source("broken.py", "def f(:\n")
    assert parsed.has_syntax_error is True
    assert parsed.symbols == []


def test_unknown_extension_is_unparsed_not_guessed() -> None:
    parsed = parse_source("README.md", "# hello")
    assert parsed.language is None
    assert parsed.parser == "none"


def test_go_symbols_via_tree_sitter() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return  # grammar pack not installed in this environment; ast path is covered above

    code = """
package mathutil

func Add(a, b int) int {
    return a + b
}

func TestAdd(t *testing.T) {
    if Add(1, 2) != 3 {
        t.Fail()
    }
}
"""
    parsed = parse_source("mathutil.go", code)
    assert parsed.language == "go"
    assert parsed.parser == "tree_sitter"
    names = {s.name: s for s in parsed.symbols}
    assert names["Add"].kind == "function"
    assert names["Add"].is_test is False
    assert names["TestAdd"].is_test is True


def test_go_method_receiver_and_grouped_imports() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = """
package geometry

import (
	"fmt"
	"math"
)

import "errors"

type Circle struct {
	Radius float64
}

func (c *Circle) Area() float64 {
	return math.Pi * c.Radius * c.Radius
}

func (c Circle) String() string {
	return fmt.Sprintf("circle(%v)", c.Radius)
}
"""
    parsed = parse_source("geometry.go", code)
    assert parsed.parser == "tree_sitter"
    by_qualified = {s.qualified_name: s for s in parsed.symbols}
    # Pointer receiver (*Circle) and value receiver (Circle) both qualify to "Circle".
    assert by_qualified["Circle.Area"].kind == "method"
    assert by_qualified["Circle.String"].kind == "method"

    modules = {i.module for i in parsed.imports}
    assert modules == {"fmt", "math", "errors"}  # one entry per path, not one blob per statement


def test_rust_impl_and_trait_impl_qualify_methods_by_self_type() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = """
pub use crate::state::{get_stats, ArenaStats};
use core::alloc::{GlobalAlloc, Layout};

pub struct BumpAllocator;

unsafe impl GlobalAlloc for BumpAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        1 as *mut u8
    }
}

impl BumpAllocator {
    fn helper(&self) -> u8 { 2 }
}
"""
    parsed = parse_source("allocator.rs", code)
    assert parsed.parser == "tree_sitter"
    by_qualified = {s.qualified_name: s for s in parsed.symbols}
    # Trait impl (`impl Trait for Type`) qualifies by the Self type, not the trait.
    assert "BumpAllocator.alloc" in by_qualified
    assert "GlobalAlloc.alloc" not in by_qualified
    # Inherent impl (`impl Type`) qualifies the same way.
    assert "BumpAllocator.helper" in by_qualified

    by_module = {i.module: i for i in parsed.imports}
    # A use-list splits into the module it addresses plus the names it brings
    # in, rather than staying one opaque string — that split is what lets
    # `crate::state::{...}` resolve to the file `src/state.rs`. The original
    # point of this assertion still stands: no "pub " left stuck on the front.
    assert "crate::state" in by_module
    assert by_module["crate::state"].symbols == ["get_stats", "ArenaStats"]
    assert by_module["crate::state"].raw.startswith("pub use")
    assert "core::alloc" in by_module
    assert by_module["core::alloc"].symbols == ["GlobalAlloc", "Layout"]

    # Trait impl's methods report the trait being implemented; a plain
    # inherent impl (no `for Trait`) correctly reports none.
    assert by_qualified["BumpAllocator.alloc"].bases == ("GlobalAlloc",)
    assert by_qualified["BumpAllocator.helper"].bases == ()


def test_js_arrow_and_function_expression_consts_are_captured() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = """
const handleClick = () => { doThing(); };
const helper = async function(x) { return x; };
const [a, setA] = useState(false);
export const Named = () => { return 1; };
const testSomething = () => { assert(true); };
"""
    parsed = parse_source("component.jsx", code)
    assert parsed.parser == "tree_sitter"
    by_name = {s.name: s for s in parsed.symbols}

    assert by_name["handleClick"].kind == "function"
    assert by_name["helper"].is_async is True
    assert by_name["Named"].kind == "function"  # reachable through export_statement
    assert by_name["testSomething"].is_test is True  # name-based is_test still applies
    # `const [a, setA] = useState(false)` is a destructuring pattern, not a
    # function binding - must not appear as a symbol at all.
    assert "a" not in by_name and "setA" not in by_name


def test_js_class_method_qualified_name_and_async_flag() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = """
class Greeter {
  async fetch(name) {
    return `hi ${name}`;
  }
}

async function standalone() {
  return 1;
}

function* countUp() {
  yield 1;
}
"""
    parsed = parse_source("greeter.ts", code)
    assert parsed.parser == "tree_sitter"
    by_qualified = {s.qualified_name: s for s in parsed.symbols}

    assert by_qualified["Greeter.fetch"].kind == "method"
    assert by_qualified["Greeter.fetch"].is_async is True
    assert by_qualified["standalone"].is_async is True
    assert by_qualified["countUp"].is_generator is True
    assert by_qualified["standalone"].is_generator is False


def test_js_class_extends_is_captured() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = """
class Shape {
  area() { return 0; }
}

class Circle extends Shape {
  area() { return 3.14; }
}
"""
    parsed = parse_source("shapes.js", code)
    by_name = {s.name: s for s in parsed.symbols if s.kind == "class"}
    assert by_name["Shape"].bases == ()
    assert by_name["Circle"].bases == ("Shape",)


def test_ts_specific_syntax_uses_the_real_typescript_grammar() -> None:
    """`.ts`/`.tsx` share javascript's node names for everything extracted
    here (they're the same node shapes for functions/classes/imports), but
    that must not mean parsing them *with* the JavaScript grammar — a type
    annotation or interface has no meaning there and is reported as a syntax
    error. Regression test for exactly that: this snippet parses clean under
    the real TypeScript grammar but reports `has_error` under plain JS."""
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = "interface Foo { bar: string; }\nfunction greet(name: string): string { return name; }\n"
    parsed = parse_source("greet.ts", code)
    assert parsed.parser == "tree_sitter"
    assert parsed.has_syntax_error is False


def test_tsx_uses_its_own_grammar_not_plain_typescript_or_javascript() -> None:
    """`.tsx` needs a grammar that accepts *both* JSX elements (which plain
    TypeScript's grammar rejects) and type annotations (which plain
    JavaScript's grammar rejects) - this snippet needs both at once, so it
    only parses clean if PARSERS["tsx"] actually reaches the tsx grammar
    rather than silently falling back to one of the other two."""
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = (
        "interface Props { name: string; }\n"
        "function Greeting(props: Props) {\n"
        "  return <div>Hello {props.name}</div>;\n"
        "}\n"
    )
    parsed = parse_source("Greeting.tsx", code)
    assert parsed.parser == "tree_sitter"
    assert parsed.has_syntax_error is False
    # The interface is a symbol in its own right: an importer writing
    # `import { Props } from "./Greeting"` has to have something to land on.
    assert [s.name for s in parsed.symbols] == ["Props", "Greeting"]


def test_js_import_module_is_the_specifier_not_the_whole_statement() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = 'import { authHandler, type AppEnv } from "./auth";\n'
    parsed = parse_source("index.ts", code)
    assert len(parsed.imports) == 1
    assert parsed.imports[0].module == "./auth"  # not the whole multi-line statement


def test_js_import_symbols_cover_named_type_default_and_namespace() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    code = (
        'import { authHandler, type AppEnv } from "./auth";\n'
        'import OAuthProvider from "@cloudflare/workers-oauth-provider";\n'
        'import * as ns from "./util";\n'
    )
    parsed = parse_source("index.ts", code)
    by_module = {i.module: i.symbols for i in parsed.imports}
    assert by_module["./auth"] == ["authHandler", "AppEnv"]  # `type` keyword stripped
    assert by_module["@cloudflare/workers-oauth-provider"] == ["OAuthProvider"]
    assert by_module["./util"] == ["ns"]


def test_js_edges_resolve_relative_imports_with_and_without_extension() -> None:
    from fleet.graph.engine import TREE_SITTER_AVAILABLE

    if not TREE_SITTER_AVAILABLE:
        return

    files = [
        parse_source(
            "src/index.ts",
            'import { authHandler } from "./auth";\nimport { z } from "zod";\n',
        ),
        parse_source("src/auth.ts", "export function authHandler() {}\n"),
        parse_source(
            "tests/auth.test.ts",
            'import { authHandler } from "../src/auth.ts";\n',
        ),
    ]
    edges = resolve_edges(files)
    pairs = {(e.source, e.target) for e in edges}
    assert ("src/index.ts", "src/auth.ts") in pairs  # extension omitted
    assert ("tests/auth.test.ts", "src/auth.ts") in pairs  # extension included
    # "zod" is a package, not a repo file - no edge, not a guess.
    assert not any(e.source == "src/index.ts" and "zod" in e.raw for e in edges)

    edge = next(e for e in edges if e.source == "src/index.ts" and e.target == "src/auth.ts")
    assert edge.symbols == ("authHandler",)  # what was actually imported, not just that something was


def test_python_edges_resolve_absolute_relative_and_from_submodule() -> None:
    files = [
        parse_source("pkg/__init__.py", "from .core import Path\nfrom . import helpers\n"),
        parse_source("pkg/core.py", "import pkg.helpers\n\ndef Path():\n    pass\n"),
        parse_source("pkg/helpers.py", "def util():\n    pass\n"),
        parse_source("pkg/sub/deep.py", "from ..core import Path\n\ndef f():\n    pass\n"),
        parse_source("pkg/external.py", "import os\nimport requests\n"),
    ]
    edges = resolve_edges(files)
    pairs = {(e.source, e.target) for e in edges}

    assert ("pkg/__init__.py", "pkg/core.py") in pairs  # from .core import Path
    assert ("pkg/__init__.py", "pkg/helpers.py") in pairs  # from . import helpers
    assert ("pkg/core.py", "pkg/helpers.py") in pairs  # import pkg.helpers (absolute)
    assert ("pkg/sub/deep.py", "pkg/core.py") in pairs  # from ..core import Path

    # os / requests aren't part of this repo's file set - no edge, not a guess.
    external_sources = {e.source for e in edges if e.source == "pkg/external.py"}
    assert external_sources == set()


def test_scan_repo_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# not code\n", encoding="utf-8")
    ignored = tmp_path / ".venv" / "lib"
    ignored.mkdir(parents=True)
    (ignored / "vendored.py").write_text("def should_not_appear():\n    pass\n", encoding="utf-8")

    graph = scan_repo(tmp_path)
    stats = graph.statistics()

    assert stats["files_total"] == 1  # README.md has no recognized language; .venv is pruned
    assert stats["files_by_language"] == {"python": 1}
    assert stats["symbols_total"] == 1

    out = tmp_path / "repo_graph.json"
    write_graph(graph, out)
    assert out.exists()
    assert "schema_version" in out.read_text(encoding="utf-8")


def test_fleetignore_defaults_cover_python_js_ts_go(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("def f():\n    pass\n", encoding="utf-8")

    for junk_dir, junk_file, code in [
        ("__pycache__", "cached.pyc", ""),
        ("node_modules/left-pad", "index.js", "function f() {}"),
        ("vendor/pkg", "lib.go", "package pkg\nfunc F() {}\n"),
        ("dist", "bundle.min.js", "!function(){}();"),
    ]:
        d = tmp_path / junk_dir
        d.mkdir(parents=True)
        (d / junk_file).write_text(code, encoding="utf-8")

    graph = scan_repo(tmp_path)
    paths = {f.path for f in graph.files}
    assert paths == {"src/main.py"}


def test_fleetignore_file_adds_project_specific_excludes(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "codegen.py").write_text("def g():\n    pass\n", encoding="utf-8")
    (tmp_path / ".fleetignore").write_text("# project-specific\ngenerated/\n", encoding="utf-8")

    graph = scan_repo(tmp_path)
    paths = {f.path for f in graph.files}
    assert paths == {"keep.py"}
