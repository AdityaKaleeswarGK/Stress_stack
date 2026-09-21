"""Python syntax and lexical import uses, without importing or executing source."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from fleet.graph.models import ExtractedCall, ExtractedImport, ExtractedSymbol, ImportBinding, ParsedFile
from fleet.graph.spec import LanguageSpec


@dataclass
class Scope:
    name: str
    kind: str
    parent: Scope | None
    imports: dict[str, list[ImportBinding]] = field(default_factory=dict)
    locals: set[str] = field(default_factory=set)
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)
    generator: bool = False


class Extractor(ast.NodeVisitor):
    def __init__(self, spec: LanguageSpec, path: str, code: str):
        self.spec, self.path, self.code = spec, path, code
        self.result = ParsedFile(path=path, language=spec.name, parser="ast")
        self.scope = Scope("", "module", None)
        self.pending = []
        self.offsets = [0]
        for line in code.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line.encode()))
        self.condition = 0

    def byte(self, node, end=False):
        return self.offsets[getattr(node, "end_lineno" if end else "lineno") - 1] + getattr(node, "end_col_offset" if end else "col_offset")

    def text(self, node):
        return ast.get_source_segment(self.code, node) or ast.unparse(node)

    def reference(self, node, expression, kind="reference"):
        self.pending.append((self.scope, node, expression, kind))

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.reference(node, node.id)
        else:
            self.scope.locals.add(node.id)

    def visit_Attribute(self, node):
        # Keep the whole chain once; do not also count its root as another use.
        if isinstance(node.ctx, ast.Load) and self.chain(node):
            self.reference(node, ast.unparse(node))
        else:
            self.visit(node.value)

    @staticmethod
    def chain(node):
        while isinstance(node, ast.Attribute):
            node = node.value
        return isinstance(node, ast.Name)

    def visit_Call(self, node):
        expr = ast.unparse(node.func)
        self.result.calls.append(ExtractedCall(expr.split(".")[-1], node.lineno, self.scope.name, expr))
        if self.chain(node.func):
            self.reference(node.func, expr, "call")
        else:
            self.visit(node.func)
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Import(self, node):
        for alias in node.names:
            self.result.imports.append(ExtractedImport(self.text(node), alias.name, [], node.lineno))
            self.binding(node, alias.name, "", alias.asname or alias.name.split(".")[0], "module", alias.name if alias.asname else alias.name.split(".")[0])

    def visit_ImportFrom(self, node):
        module = "." * node.level + (node.module or "")
        self.result.imports.append(ExtractedImport(self.text(node), module, [a.name for a in node.names], node.lineno))
        for alias in node.names:
            self.binding(node, module, alias.name, alias.asname or alias.name, "from")

    def binding(self, node, module, name, local, kind, bound_module=""):
        binding = ImportBinding(f"{self.path}@{self.byte(node)}:{len(self.result.bindings)}", module, name, local,
                                self.scope.name, node.lineno, self.text(node), kind,
                                reexport=self.scope.kind == "module", conditional=bool(self.condition),
                                bound_module=bound_module, start_byte=self.byte(node))
        self.result.bindings.append(binding)
        self.scope.imports.setdefault(local, []).append(binding)

    def visit_Global(self, node):
        self.scope.globals.update(node.names)

    def visit_Nonlocal(self, node):
        self.scope.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.scope.locals.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node):
        if node.name:
            self.scope.locals.add(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node):
        if node.rest:
            self.scope.locals.add(node.rest)
        self.generic_visit(node)

    def visit_If(self, node):
        self.visit(node.test)
        self.condition += 1
        for child in node.body + node.orelse:
            self.visit(child)
        self.condition -= 1

    def visit_Try(self, node):
        self.condition += 1
        self.generic_visit(node)
        self.condition -= 1

    visit_TryStar = visit_Try

    def definition(self, node, is_class=False):
        outer = self.scope
        outer.locals.add(node.name)
        qname = f"{outer.name}.{node.name}" if outer.name else node.name
        for d in node.decorator_list:
            self.visit(d)
        if is_class:
            for base in node.bases:
                if self.chain(base):
                    # A base belongs to the class being declared, but resolves
                    # in its enclosing scope, before entering the class body.
                    self.pending.append((outer, base, ast.unparse(base), "base:" + qname))
                else:
                    self.visit(base)
            for kw in node.keywords:
                self.visit(kw.value)
        else:
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                self.visit(default)
        inner = Scope(qname, "class" if is_class else "function", outer)
        self.scope = inner
        if not is_class:
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            args += [a for a in (node.args.vararg, node.args.kwarg) if a]
            inner.locals.update(a.arg for a in args)
            # Annotation ownership is the declaration; lookup uses the enclosing
            # scope (parameters must not shadow their annotation names).
            for annotation in [a.annotation for a in args] + [node.returns]:
                if annotation:
                    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                        self.result.diagnostics.append({"line": annotation.lineno, "reason": "string_annotation_not_resolved"})
                    else:
                        saved = self.scope
                        self.scope = outer
                        before = len(self.pending)
                        self.visit(annotation)
                        for index in range(before, len(self.pending)):
                            scope, n, expr, _ = self.pending[index]
                            self.pending[index] = (scope, n, expr, "annotation:" + qname)
                        self.scope = saved
        for child in node.body:
            self.visit(child)
        self.scope = outer
        first, last = node.body[0], node.body[-1]
        signature = ("class " + node.name if is_class else ("async " if isinstance(node, ast.AsyncFunctionDef) else "") + "def " + node.name + "(" + ast.unparse(node.args) + ")")
        self.result.symbols.append(ExtractedSymbol(
            name=node.name, qualified_name=qname,
            kind="class" if is_class else ("method" if outer.kind == "class" else "function"),
            start_line=node.lineno, end_line=node.end_lineno,
            first_body_line=first.lineno, last_body_line=last.end_lineno,
            is_test=self.spec.tests.matches(node.name, tuple(ast.unparse(d) for d in node.decorator_list)),
            is_async=isinstance(node, ast.AsyncFunctionDef), is_generator=inner.generator,
            bases=tuple(ast.unparse(b) for b in node.bases) if is_class else (),
            docstring=ast.get_docstring(node) or "", body_start_byte=self.byte(first), body_end_byte=self.byte(last, True),
            path=self.path, parent=outer.name, start_byte=self.byte(node), end_byte=self.byte(node, True), signature=signature))

    def visit_FunctionDef(self, node):
        self.definition(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.definition(node, True)

    def visit_Yield(self, node):
        self.scope.generator = True
        self.generic_visit(node)

    visit_YieldFrom = visit_Yield

    def visit_Lambda(self, node):
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(d)
        saved = self.scope
        # The lambda's uses are attributed to the containing named entity;
        # its parameters still shadow imports within its own lexical scope.
        self.scope = Scope(saved.name, "lambda", saved)
        self.scope.locals.update(a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs)
        self.scope.locals.update(a.arg for a in (node.args.vararg, node.args.kwarg) if a)
        self.visit(node.body)
        self.scope = saved

    def comprehension(self, node):
        saved = self.scope
        self.visit(node.generators[0].iter)
        self.scope = Scope(saved.name, "comprehension", saved)
        for i, gen in enumerate(node.generators):
            if i:
                self.visit(gen.iter)
            self.visit(gen.target)
            for cond in gen.ifs:
                self.visit(cond)
        for name in ("elt", "key", "value"):
            value = getattr(node, name, None)
            if value:
                self.visit(value)
        self.scope = saved

    visit_ListComp = comprehension
    visit_SetComp = comprehension
    visit_DictComp = comprehension
    visit_GeneratorExp = comprehension

    def finish(self):
        self.result.bound_names = sorted(self.scope.locals)
        for scope, node, expression, kind in self.pending:
            name = expression.split(".")[0]
            owner = scope.name
            if ":" in kind:
                kind, owner = kind.split(":", 1)
            current = scope
            skip_class = current.kind in {"function", "lambda", "comprehension"}
            while current:
                if name in current.globals:
                    if name in current.locals:
                        break
                    while current.parent:
                        current = current.parent
                elif name in current.nonlocals:
                    if name in current.locals:
                        break
                    current = current.parent
                    while current and current.kind == "class":
                        current = current.parent
                    continue
                bindings = current.imports.get(name, [])
                if bindings or name in current.locals:
                    if len(bindings) == 1 and name not in current.locals:
                        b = bindings[0]
                        if current is scope and self.byte(node) < b.start_byte:
                            self.result.diagnostics.append({"line": node.lineno, "reason": "reference_before_import", "name": name})
                            break
                        b.uses.append({"owner": owner, "expression": expression, "kind": kind,
                                       "source_text": self.text(node),
                                       "line": node.lineno, "start_byte": self.byte(node), "end_byte": self.byte(node, True)})
                    elif bindings:
                        self.result.diagnostics.append({"line": node.lineno, "name": name, "reason": "ambiguous_or_reassigned_import"})
                    break
                if "*" in current.imports:
                    self.result.diagnostics.append({"line": node.lineno, "name": name, "reason": "wildcard_binding_unknown"})
                    break
                current = current.parent
                while current and skip_class and current.kind == "class":
                    current = current.parent
        self.result.symbols.sort(key=lambda s: (s.start_byte, s.qualified_name))
        self.result.calls.sort(key=lambda c: c.line)
        return self.result


def extract(spec: LanguageSpec, path: str, code: str) -> ParsedFile:
    try:
        tree = ast.parse(code, filename=path)
    except SyntaxError as exc:
        return ParsedFile(path=path, language=spec.name, has_syntax_error=True,
                          diagnostics=[{"line": exc.lineno, "reason": "syntax_error", "message": exc.msg}])
    visitor = Extractor(spec, path, code)
    visitor.visit(tree)
    return visitor.finish()
