"""Connect imported names to modules/definitions, then resolve their witnessed uses.

No filename/suffix guesses and no target-code execution. Python, Rust and the
JS/TS family have separate module rules; graph projection is shared.
Unsupported forms stay visible.
"""
from __future__ import annotations
import posixpath
import re
import sys
from collections import defaultdict
from pathlib import PurePosixPath

from fleet.graph.models import Edge, ImportBinding, ParsedFile
from fleet.graph.spec import specs_in_family

JS_FAMILY = "javascript"

# Modules Node resolves itself. A bare specifier that is one of these is not a
# missing dependency and not a repo file.
NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http",
    "http2", "https", "inspector", "module", "net", "os", "path", "perf_hooks", "process",
    "punycode", "querystring", "readline", "repl", "stream", "string_decoder", "sys",
    "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
})


class BindingResolver:
    def __init__(self, files: list[ParsedFile], metadata: dict | None = None):
        self.files = {f.path: f for f in files}
        self.metadata = metadata or {}
        self.modules = defaultdict(list)  # logical module -> (file, inline scope)
        self.contexts = defaultdict(list)  # file -> (crate, file's logical module)
        self.python_modules = {}
        self.crate_names = defaultdict(list)
        self.crate_dirs = {}
        self.crate_dependencies = defaultdict(dict)
        self.external = defaultdict(set)
        self._index_python()
        self._index_rust()
        self._index_javascript()
        self.by_binding = {b.id: b for f in files for b in f.bindings}

    def add_module(self, key, path, scope=""):
        value = (path, scope)
        if value not in self.modules[key]:
            self.modules[key].append(value)

    def _index_python(self):
        roots = ["src", ""]
        for path, config in self.metadata.items():
            if path.endswith("pyproject.toml"):
                prefix = str(PurePosixPath(path).parent)
                prefix = "" if prefix == "." else prefix + "/"
                options = config.get("tool", {}).get("fleet", {}).get("graph", {})
                roots += [prefix + r.strip("/") for r in options.get("python_roots", [])]
                package_dir = config.get("tool", {}).get("setuptools", {}).get("package-dir", {})
                roots += [prefix + r.strip("/") for r in package_dir.values() if isinstance(r, str)]
                roots += [prefix + "src", prefix.rstrip("/")]
                for dependency in config.get("project", {}).get("dependencies", []):
                    match = re.match(r"[A-Za-z0-9_.-]+", dependency)
                    if match:
                        self.external["python"].add(match.group().replace("-", "_"))
        # A directory named src can itself be the imported Python package.
        roots = sorted({r for r in roots if not r or r + "/__init__.py" not in self.files}, key=len, reverse=True)
        for path, file in self.files.items():
            if file.language != "python":
                continue
            root = next((r for r in roots if not r or path.startswith(r + "/")), "")
            stem = path[len(root)+1:] if root else path
            stem = stem.removesuffix(".py").replace("/", ".")
            module = stem.removesuffix(".__init__")
            if module == "__init__":
                module = ""
            self.python_modules[path] = module
            self.add_module(("python", module), path)
            # Namespace package inventory: it has no defining file of its own.
            # Child modules can still resolve through their full module key.
        self.external["python"].update(sys.stdlib_module_names)

    def _index_rust(self):
        rust = {p: f for p, f in self.files.items() if f.language == "rust"}
        roots = []
        for manifest, config in self.metadata.items():
            if not manifest.endswith("Cargo.toml"):
                continue
            base = str(PurePosixPath(manifest).parent)
            base = "" if base == "." else base
            package = config.get("package", {})
            lib = config.get("lib", {})
            name = lib.get("name", package.get("name", "")).replace("-", "_")
            libpath = posixpath.join(base, lib.get("path", "src/lib.rs"))
            if libpath in rust:
                self.crate_names[name].append(libpath)
                roots.append((libpath, base))
            main = posixpath.join(base, "src/main.rs")
            if main in rust:
                roots.append((main, base))
            for target in config.get("bin", []) + config.get("test", []) + config.get("example", []):
                if target.get("path"):
                    p = posixpath.normpath(posixpath.join(base, target["path"]))
                    if p in rust:
                        roots.append((p, base))
            for p in rust:
                rel = p[len(base)+1:] if base else p
                if re.fullmatch(r"(?:tests|examples|src/bin)/[^/]+\.rs", rel):
                    roots.append((p, base))
            for section in ("dependencies", "dev-dependencies", "build-dependencies"):
                self.external[base].update(key.replace("-", "_") for key in config.get(section, {}))
                for alias, dependency in config.get(section, {}).items():
                    if not isinstance(dependency, dict) or "path" not in dependency:
                        continue
                    directory = posixpath.normpath(posixpath.join(base, dependency["path"]))
                    dep_config = self.metadata.get(posixpath.join(directory, "Cargo.toml"), {})
                    target = posixpath.normpath(posixpath.join(directory, dep_config.get("lib", {}).get("path", "src/lib.rs")))
                    if target in rust:
                        self.crate_dependencies[base][alias.replace("-", "_")] = target
        # No manifest: identify ordinary crate entry points; partial file sets
        # used by parse_source callers retain a documented inferred context.
        if not roots:
            roots = [(p, p.split("/src/")[0] if "/src/" in p else "") for p in rust if p.endswith(("/lib.rs", "/main.rs")) or p in {"lib.rs", "main.rs"}]
        visited = set()

        def register(crate, path, logical="", inline=""):
            key = (crate, path, logical, inline)
            if key in visited:
                return
            visited.add(key)
            self.add_module((crate, logical), path, inline)
            if not inline and (crate, logical) not in self.contexts[path]:
                self.contexts[path].append((crate, logical))
            file = rust[path]
            for mod in file.modules:
                if mod["parent"] != inline:
                    continue
                child_logical = "::".join(filter(None, [logical, mod["name"]]))
                if mod["inline"]:
                    child_scope = ".".join(filter(None, [inline, mod["name"]]))
                    register(crate, path, child_logical, child_scope)
                    continue
                if any("cfg" in a for a in mod["attributes"]):
                    file.diagnostics.append({"line": mod["line"], "reason": "conditional_module_not_selected"})
                    continue
                directory = str(PurePosixPath(path).parent)
                if PurePosixPath(path).name not in {"lib.rs", "main.rs", "mod.rs"} and path != crate:
                    directory = posixpath.join(directory, PurePosixPath(path).stem)
                if inline:
                    directory = posixpath.join(directory, inline.replace(".", "/"))
                override = next((re.search(r'path\s*=\s*"([^"]+)"', a) for a in mod["attributes"] if "path" in a), None)
                options = [posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent), override.group(1)))] if override else [posixpath.normpath(posixpath.join(directory, mod["name"] + ".rs")), posixpath.normpath(posixpath.join(directory, mod["name"], "mod.rs"))]
                candidates = [p for p in options if p in rust]
                if len(candidates) == 1:
                    register(crate, candidates[0], child_logical)
                else:
                    file.diagnostics.append({"line": mod["line"], "reason": "module_file_missing_or_ambiguous", "candidates": candidates})
        for crate, directory in roots:
            self.crate_dirs[crate] = directory
            register(crate, crate)
        # In-memory partial collections don't contain a full module tree. The
        # fallback is explicitly restricted to callers without manifest metadata.
        if not any(p.endswith("Cargo.toml") for p in self.metadata):
            for path in rust:
                if self.contexts[path]:
                    continue
                before, sep, after = path.rpartition("/src/")
                if path.startswith("src/"):
                    before, after = "", path[4:]
                elif not sep:
                    before, after = "", path
                candidates = [c for c, d in roots if d == before]
                crate = candidates[0] if len(candidates) == 1 else before + "/<inferred>"
                module = after.removesuffix(".rs").removesuffix("/mod").replace("/", "::")
                self.crate_dirs[crate] = before
                self.contexts[path].append((crate, module))
                register(crate, path, module)
                rust[path].diagnostics.append({"reason": "inferred_rust_module_without_manifest"})

    def _index_javascript(self):
        """Node resolves a specifier to a *path*, so there is no module-name
        table to build here — only the extension/index candidates a specifier
        may stand for, and the dependencies package.json declares."""
        specs = specs_in_family(JS_FAMILY)
        self.js_languages = {spec.name for spec in specs}
        # Ordered: the first candidate that exists wins, and an explicit
        # extension has to be tried before any we would append.
        self.js_extensions = tuple(dict.fromkeys(e for spec in specs for e in spec.extensions))
        self.js_index_names = tuple(dict.fromkeys(n for spec in specs for n in spec.index_names))
        self.js_files = {p for p, f in self.files.items() if f.language in self.js_languages}
        for path, config in self.metadata.items():
            if PurePosixPath(path).name != "package.json":
                continue
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                self.external[JS_FAMILY].update(config.get(section, {}) or {})
        for path in self.js_files:
            self.add_module((JS_FAMILY, path), path)

    def specifier(self, importer: str, module: str) -> str | None:
        """The repo file a specifier names, or None.

        Relative only. A bare specifier ("zod", "@scope/pkg") is a package by
        Node's own rules, never a file in this repository, so it is not
        attempted — and neither is a tsconfig `paths` alias, which would need
        that file's compiler options to mean anything.
        """
        if not module.startswith("."):
            return None
        directory = posixpath.dirname(importer)
        base = posixpath.normpath(posixpath.join(directory, module) if directory else module)
        candidates = [base]
        # TypeScript's NodeNext style writes the *emitted* extension:
        # `./util.js` in a .ts file means util.ts. Tried before the plain
        # appended extensions, since the specifier really does name a file.
        stem, dot, extension = base.rpartition(".")
        if dot and "." + extension in {".js", ".jsx", ".mjs", ".cjs"}:
            candidates += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
        candidates += [base + e for e in self.js_extensions]
        candidates += [f"{base}/{index}{e}" for index in self.js_index_names for e in self.js_extensions]
        for candidate in candidates:
            if candidate in self.js_files and candidate != importer:
                return candidate
        return None

    def lookup_js(self, path: str, name: str, seen=frozenset()):
        """Resolve an exported name inside `path`, following re-export chains.

        Only what the file actually exports can be found. A module's top-level
        definitions are not searched directly: `export { internal as public }`
        and `export { x } from "./other"` both mean the visible name and the
        defining name are different, and matching definitions by name would
        get those exact cases wrong.
        """
        if (path, name) in seen or len(seen) > 40:
            return []
        seen = seen | {(path, name)}
        file = self.files.get(path)
        if file is None:
            return []
        key = (JS_FAMILY, path)
        if not name:
            return [(path, None, key)]
        first, _, rest = name.partition(".")
        matches = []
        starred = []
        for export in file.exports:
            if export["exported"] == "*" and export["module"]:
                starred.append(export["module"])
                continue
            if export["exported"] != first:
                continue
            target = self.specifier(path, export["module"]) if export["module"] else None
            if export["module"]:
                # `export { a as b } from "./m"` — b here is a there.
                if target is not None:
                    forwarded = ".".join(filter(None, [export["local"] or first, rest]))
                    matches.extend(self.lookup_js(target, forwarded, seen))
                continue
            local = export["local"]
            if not local:
                # An exported expression with no name behind it. The file is
                # the honest answer; there is no definition to point at.
                matches.append((path, None, key))
                continue
            qualified = ".".join(filter(None, [local, rest]))
            found = [s for s in file.symbols if s.qualified_name == qualified]
            matches.extend((path, s.id, key) for s in found)
            if not found:
                # The exported name may itself have been imported here.
                for b in file.bindings:
                    if b.local != local or b.scope:
                        continue
                    forwarded = ".".join(filter(None, [b.name, rest])) if b.kind != "namespace" else rest
                    for bkey in self.keys(file, b):
                        matches.extend(self.lookup_js(bkey[1], forwarded, seen))
                if not matches and not rest:
                    # Exported, but backed by no definition node — a plain
                    # `export const WIDTH = 400`. The file is resolved and the
                    # export is real; there is simply no symbol for a constant.
                    matches.append((path, None, key))
        for module in starred:
            target = self.specifier(path, module)
            if target is not None:
                matches.extend(self.lookup_js(target, name, seen))
        return list(dict.fromkeys(matches))

    def keys(self, file: ParsedFile, b: ImportBinding, module=None):
        module = b.module if module is None else module
        if file.language in self.js_languages:
            target = self.specifier(file.path, module)
            return [(JS_FAMILY, target)] if target else []
        if file.language == "python":
            if module.startswith("."):
                level = len(module) - len(module.lstrip("."))
                own = self.python_modules[file.path]
                package = own if file.path.endswith("/__init__.py") else own.rpartition(".")[0]
                parts = package.split(".") if package else []
                if level > len(parts):
                    return []
                module = ".".join(parts[:len(parts)-level+1] + ([module[level:]] if module[level:] else []))
            return [("python", module)]
        keys = []
        for crate, own in self.contexts[file.path]:
            own = "::".join(filter(None, [own, b.module_scope.replace(".", "::")]))
            parts = module.split("::") if module else []
            if parts and parts[0] == "crate":
                keys.append((crate, "::".join(parts[1:])))
            elif parts and parts[0] in {"self", "super"}:
                base = own.split("::") if own else []
                while parts and parts[0] in {"self", "super"}:
                    marker = parts.pop(0)
                    if marker == "super":
                        if not base:
                            break
                        base.pop()
                else:
                    keys.append((crate, "::".join(base + parts)))
            else:
                # Library crate names can be used from this package's bins/tests.
                if parts:
                    dependency = self.crate_dependencies[self.crate_dirs.get(crate, "")].get(parts[0])
                    if dependency:
                        keys.append((dependency, "::".join(parts[1:])))
                    for lib in self.crate_names.get(parts[0], []):
                        if self.crate_dirs.get(lib) == self.crate_dirs.get(crate):
                            keys.append((lib, "::".join(parts[1:])))
                keys.append((crate, "::".join(filter(None, [own, module]))))
        return list(dict.fromkeys(keys))

    def lookup(self, key, name="", seen=frozenset()):
        """Return (file, symbol or None, logical-module-key) candidates."""
        if key[0] == JS_FAMILY:
            return self.lookup_js(key[1], name)
        if (key, name) in seen or len(seen) > 40:
            return []
        seen = seen | {(key, name)}
        entries = self.modules.get(key, [])
        if not name:
            return [(path, None, key) for path, scope in entries]
        matches = []
        blocked_submodule = False
        sep = "." if key[0] == "python" else "::"
        first, _, rest = name.partition(sep)
        for path, scope in entries:
            file = self.files[path]
            direct = [s for s in file.symbols if s.name == first and s.parent == scope]
            for symbol in direct:
                if rest:
                    matches.extend((path, s.id, key) for s in file.symbols if s.qualified_name == symbol.qualified_name + "." + rest.replace(sep, "."))
                else:
                    matches.append((path, symbol.id, key))
            if direct:
                continue
            if file.language == "python" and not scope and first in file.bound_names:
                blocked_submodule = True
                continue
            bindings = [b for b in file.bindings if b.local == first and b.scope == scope]
            for b in bindings:
                if b.conditional:
                    continue
                for bkey in self.keys(file, b):
                    target_name = b.name
                    if rest:
                        target_name = sep.join(filter(None, [target_name, rest]))
                    matches.extend(self.lookup(bkey, target_name, seen))
        if not matches and not blocked_submodule:
            childkey = (key[0], sep.join(filter(None, [key[1], first])))
            if childkey in self.modules:
                matches.extend(self.lookup(childkey, rest, seen))
        return list(dict.fromkeys(matches))

    def classify_js(self, b: ImportBinding):
        """Why a JS/TS specifier resolved to no file in this repository.

        A bare specifier is a package by Node's rules, so it is external even
        when package.json does not list it — what differs is how well that is
        evidenced, which the reason records. A relative specifier that found
        nothing is a genuine miss (an asset import, an ignored path, or a
        tsconfig alias), and stays unresolved rather than being called
        external.
        """
        if b.module.startswith("."):
            return "unresolved", "relative_specifier_not_found"
        name = b.module.removeprefix("node:")
        if b.module.startswith("node:") or name.split("/")[0] in NODE_BUILTINS:
            return "external", "builtin_module"
        parts = name.split("/")
        package = "/".join(parts[:2]) if name.startswith("@") else parts[0]
        if package in self.external[JS_FAMILY]:
            return "external", "stdlib_or_declared_dependency"
        return "external", "no_matching_local_package"

    def resolve(self):
        edges = []
        for file in self.files.values():
            if file.language not in {"python", "rust"} | self.js_languages:
                continue
            for b in file.bindings:
                b.target_file = b.target_symbol = None
                keys = self.keys(file, b)
                targets = list(dict.fromkeys(t for key in keys for t in self.lookup(key, b.name)))
                modules = list(dict.fromkeys(t for key in keys for t in self.lookup(key)))
                b.status, b.reason = "unresolved", "module_or_symbol_not_found"
                if b.name == "*":
                    b.status, b.reason = "unsupported", "wildcard_import"
                elif len(targets) == 1:
                    b.target_file, b.target_symbol, _ = targets[0]
                    b.status, b.reason = ("conditional", "target_resolved_import_condition_not_evaluated") if b.conditional else ("internal", "module_and_binding_resolved")
                elif len(targets) > 1:
                    b.status, b.reason = "ambiguous", "multiple_targets"
                elif len(modules) == 1:
                    b.target_file = modules[0][0]
                    b.reason = "module_resolved_symbol_unknown"
                elif file.language in self.js_languages:
                    b.status, b.reason = self.classify_js(b)
                else:
                    root = b.module.lstrip(".").split("::" if file.language == "rust" else ".")[0]
                    known_external = root in self.external["python"] if file.language == "python" else root in {"std", "core", "alloc"} or any(root in self.external[self.crate_dirs.get(c, "")] for c, _ in self.contexts[file.path])
                    if known_external:
                        b.status, b.reason = "external", "stdlib_or_declared_dependency"
                    else:
                        if file.language == "python":
                            local = b.module.startswith(".") or any(
                                module == root or module.startswith(root + ".")
                                for module in self.python_modules.values()
                            )
                        else:
                            local = root in {"crate", "self", "super"} or root in self.crate_names or any(
                                namespace == crate and (module == root or module.startswith(root + "::"))
                                for crate, _ in self.contexts[file.path]
                                for namespace, module in self.modules
                            )
                        if not local:
                            b.status, b.reason = "external", "no_matching_local_package"
                # A from import loads the package even when the declaration is
                # re-exported from another file, and a JS specifier names a
                # file whether or not the name inside it could be resolved —
                # an unexported name is still evidence the file was loaded.
                imports = ({t[0] for t in modules}
                           if len(modules) == 1 and (file.language == "python" or file.language in self.js_languages)
                           else set())
                if b.target_file:
                    imports.add(b.target_file)
                for target in sorted(imports - {file.path}):
                    # `import "./polyfill"` and `export * from "./m"` bind no
                    # local name. An empty tuple says that; `("",)` would claim
                    # a name was imported and that it is the empty string.
                    names = (b.local,) if b.local else ()
                    edges.append(Edge(file.path, target, "import", b.line, b.raw, names, b.conditional))
                for use in b.uses:
                    use.update({"target_file": None, "target_symbol": None, "status": b.status,
                                "binding_target_file": b.target_file, "binding_target_symbol": b.target_symbol})
                    if b.status not in {"internal", "conditional"}:
                        continue
                    sep = "::" if file.language == "rust" else "."
                    tail = use["expression"].split(sep)[1:]
                    usekeys = keys
                    name = b.name
                    if b.kind == "module":
                        usekeys = self.keys(file, b, b.bound_module or b.module)
                    full = sep.join(filter(None, [name, *tail]))
                    resolved = list(dict.fromkeys(t for key in usekeys for t in self.lookup(key, full)))
                    if len(resolved) != 1:
                        use["status"] = "ambiguous" if len(resolved) > 1 else "unresolved"
                        use["reason"] = "member_not_resolved"
                        continue
                    path, symbol, _ = resolved[0]
                    use.update({"target_file": path, "target_symbol": symbol, "status": b.status})
                    owners = [s.id for s in file.symbols if s.qualified_name == use["owner"]]
                    owner = owners[0] if len(owners) == 1 else file.path
                    kind = "inherit" if use["kind"] == "base" else "invoke" if use["kind"] == "call" and symbol else "reference"
                    edges.append(Edge(owner, symbol or path, kind, use["line"], use["expression"], (b.local,), b.conditional))
        for path, file in self.files.items():
            if file.language != "rust":
                continue
            for mod in file.modules:
                if mod["inline"] or any("cfg" in a for a in mod["attributes"]):
                    continue
                targets = set()
                for crate, own in self.contexts[path]:
                    logical = "::".join(filter(None, [own, mod["parent"].replace(".", "::"), mod["name"]]))
                    targets.update(p for p, _ in self.modules.get((crate, logical), []))
                if len(targets) == 1:
                    target = targets.pop()
                    if target != path:
                        edges.append(Edge(path, target, "import", mod["line"], "mod " + mod["name"], (mod["name"],)))
        unique = {(e.source, e.target, e.kind, e.line, e.raw, e.symbols): e for e in edges}
        return list(unique.values())
