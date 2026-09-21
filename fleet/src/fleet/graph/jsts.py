"""JavaScript/TypeScript import bindings, exports and lexical references.

Reads the tree Tree-sitter already produced for the file — the same
arrangement Rust uses, and for the same reason: the syntax pass and the naming
pass should not parse the source twice.

Three things come out of here, and only the first two exist for Python or Rust:

* **bindings** — one per imported local name, with every witnessed use.
* **exports** — what the module makes importable, and under which name. A
  JS/TS name can be renamed on the way out (`export { internal as public }`)
  or forwarded to another file entirely (`export { x } from "./other"`), so an
  importer cannot resolve a name by matching top-level definitions the way a
  Python importer can. Guessing that would be wrong exactly where re-exports
  are involved, which is where barrel files live.
* **uses** — resolved lexically, against real scopes, so a parameter or a
  `const` named like an import shadows it rather than being credited to it.

Which *file* a specifier names is Node's question, not the grammar's, and is
answered in bindings.py. Dynamic `import()`, `module.exports = ...` and
tsconfig path aliases are not interpreted here; an unresolved specifier stays
visibly unresolved rather than being matched to a likely-looking filename.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fleet.graph.models import ImportBinding, ParsedFile

# Node types that introduce a lexical scope. `class_body` is absent on
# purpose: a class body holds no bindings a reference can resolve against —
# its methods are reached through `this`, not by bare name.
_SCOPES = frozenset({
    "statement_block", "function_declaration", "generator_function_declaration",
    "function_expression", "generator_function", "arrow_function", "method_definition",
    "for_statement", "for_in_statement", "catch_clause", "switch_body", "class_static_block",
})

# Declarations whose named children bind names rather than reference them.
_DECLARED_FIELDS = frozenset({"name", "pattern", "parameter", "alias", "key", "left"})

# Where a bare identifier is a declaration label, keyed by its parent. The
# check is by parent type rather than by field name alone because JSX reuses
# the `name` field for something that genuinely is a reference: `<Foo />`
# names the imported component.
_DECLARING_PARENTS = frozenset({
    "function_declaration", "generator_function_declaration", "function_expression",
    "class_declaration", "abstract_class_declaration", "interface_declaration",
    "enum_declaration", "enum_assignment", "type_alias_declaration", "method_definition",
    "method_signature", "property_signature", "public_field_definition", "variable_declarator",
    "required_parameter", "optional_parameter", "import_specifier", "namespace_import",
    "namespace_export", "export_specifier", "pair", "pair_pattern", "labeled_statement",
    "catch_clause", "for_in_statement", "abstract_method_signature",
})

# Identifier-shaped nodes that can refer to an imported name. The shorthand
# property in an object *literal* (`{ handler }`) is a real reference to
# `handler`; its destructuring twin
# (`shorthand_property_identifier_pattern`) is a declaration, and is
# deliberately not in here.
_REFERENCE_TYPES = frozenset({"identifier", "type_identifier", "shorthand_property_identifier"})

_PATTERN_TYPES = frozenset({
    "identifier", "shorthand_property_identifier_pattern", "object_pattern",
    "array_pattern", "rest_pattern", "pair_pattern", "assignment_pattern",
})


def text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node else ""


def unquote(node) -> str:
    value = text(node).strip()
    if len(value) >= 2 and value[0] in "\"'`" and value[-1] == value[0]:
        return value[1:-1]
    return value


def pattern_names(node) -> list[str]:
    """Every name a binding pattern introduces: `const { a, b: c, ...rest }`."""
    if node is None:
        return []
    if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
        return [text(node)]
    if node.type == "pair_pattern":
        return pattern_names(node.child_by_field_name("value"))
    if node.type == "assignment_pattern":
        return pattern_names(node.child_by_field_name("left"))
    if node.type in {"object_pattern", "array_pattern", "rest_pattern"}:
        return [name for child in node.named_children for name in pattern_names(child)]
    return []


def dotted(node) -> str | None:
    """`a.b.c` as one dotted string, or None if it isn't a plain static chain.

    Optional chaining (`a?.b`), computed access (`a[k]`) and a call in the
    middle (`a().b`) all return None: the text of those is not a name path,
    and recording it as one would produce a use that resolution then has to
    guess about. The caller falls back to the chain's root identifier.
    """
    parts: list[str] = []
    current = node
    while current.type in {"member_expression", "nested_type_identifier"}:
        if any(child.type == "?." for child in current.children):
            return None
        name = current.child_by_field_name("property") or current.child_by_field_name("name")
        if name is None or name.type not in {"property_identifier", "type_identifier"}:
            return None
        parts.append(text(name))
        current = current.child_by_field_name("object")
        if current is None:
            return None
    if current.type not in {"identifier", "type_identifier"}:
        return None
    parts.append(text(current))
    return ".".join(reversed(parts))


@dataclass
class Scope:
    owner: str
    parent: Scope | None
    imports: dict[str, list[ImportBinding]] = field(default_factory=dict)
    locals: set[str] = field(default_factory=set)


class Extractor:
    def __init__(self, result: ParsedFile):
        self.result = result
        self.symbols = {(s.start_byte, s.end_byte): s for s in result.symbols}
        self.pending = []
        self.module = Scope("", None)

    # -- bindings -----------------------------------------------------------

    def bind(self, node, module: str, name: str, local: str, kind: str, scope: Scope, reexport=False):
        binding = ImportBinding(
            f"{self.result.path}@{node.start_byte}:{len(self.result.bindings)}",
            module, name, local, scope.owner, node.start_point[0] + 1, text(node), kind,
            reexport=reexport, start_byte=node.start_byte,
        )
        self.result.bindings.append(binding)
        if local:
            scope.imports.setdefault(local, []).append(binding)
        return binding

    def import_statement(self, node, scope: Scope):
        """`import def, { a as b, type T } from "m"`, and its two siblings."""
        module = unquote(node.child_by_field_name("source"))
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is None:
            # `import "./polyfill"` binds nothing but really does load the file.
            self.bind(node, module, "", "", "side_effect", scope)
            return
        for child in clause.named_children:
            if child.type == "identifier":
                self.bind(node, module, "default", text(child), "default", scope)
            elif child.type == "namespace_import":
                name = next((c for c in child.named_children if c.type == "identifier"), None)
                self.bind(node, module, "", text(name), "namespace", scope)
            elif child.type == "named_imports":
                for specifier in child.named_children:
                    if specifier.type != "import_specifier":
                        continue
                    imported = text(specifier.child_by_field_name("name"))
                    alias = specifier.child_by_field_name("alias")
                    self.bind(node, module, imported, text(alias) if alias else imported, "named", scope)

    def require(self, declarator, scope: Scope) -> bool:
        """`const x = require("m")` / `const { a, b: c } = require("m")`.

        CommonJS, recognized only in this exact shape. A `require` reached
        through a variable, or inside a larger expression, is not followed.
        """
        value = declarator.child_by_field_name("value")
        if value is None or value.type != "call_expression":
            return False
        function = value.child_by_field_name("function")
        if function is None or text(function) != "require":
            return False
        arguments = value.child_by_field_name("arguments")
        argument = arguments.named_children[0] if arguments and arguments.named_children else None
        if argument is None or argument.type != "string":
            return False
        module = unquote(argument)
        target = declarator.child_by_field_name("name")
        if target is not None and target.type == "identifier":
            self.bind(declarator, module, "", text(target), "namespace", scope)
            return True
        if target is not None and target.type == "object_pattern":
            for child in target.named_children:
                if child.type == "shorthand_property_identifier_pattern":
                    self.bind(declarator, module, text(child), text(child), "named", scope)
                elif child.type == "pair_pattern":
                    key = text(child.child_by_field_name("key"))
                    for local in pattern_names(child.child_by_field_name("value")):
                        self.bind(declarator, module, key, local, "named", scope)
            return True
        return False

    # -- exports ------------------------------------------------------------

    def export(self, exported: str, local: str, module: str, line: int):
        self.result.exports.append({"exported": exported, "local": local, "module": module, "line": line})

    def export_statement(self, node, scope: Scope):
        line = node.start_point[0] + 1
        source = node.child_by_field_name("source")
        module = unquote(source) if source is not None else ""
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        namespace = next((c for c in node.named_children if c.type == "namespace_export"), None)
        declaration = node.child_by_field_name("declaration")
        value = node.child_by_field_name("value")
        default = any(child.type == "default" for child in node.children)

        if clause is not None:
            for specifier in clause.named_children:
                if specifier.type != "export_specifier":
                    continue
                name = text(specifier.child_by_field_name("name"))
                alias = specifier.child_by_field_name("alias")
                self.export(text(alias) if alias else name, name, module, line)
            if module:
                self.bind(node, module, "", "", "reexport", scope, reexport=True)
            return
        if namespace is not None:  # `export * as ns from "m"`
            name = next((c for c in namespace.named_children if c.type == "identifier"), None)
            self.export(text(name), "", module, line)
            self.bind(node, module, "", "", "reexport", scope, reexport=True)
            return
        if source is not None:  # `export * from "m"`
            self.export("*", "", module, line)
            self.bind(node, module, "", "", "reexport", scope, reexport=True)
            return
        if declaration is not None:
            for name in self.declared_names(declaration):
                self.export(name, name, "", line)
                if default:
                    self.export("default", name, "", line)
            return
        if value is not None:
            # `export default foo` forwards a name; `export default new X()`
            # exports an expression with no name behind it, and says so by
            # leaving `local` empty rather than inventing one.
            self.export("default", text(value) if value.type == "identifier" else "", "", line)

    @staticmethod
    def declared_names(declaration) -> list[str]:
        if declaration.type in {"lexical_declaration", "variable_declaration"}:
            return [name for child in declaration.named_children if child.type == "variable_declarator"
                    for name in pattern_names(child.child_by_field_name("name"))]
        name = declaration.child_by_field_name("name")
        return [text(name)] if name is not None else []

    # -- references ---------------------------------------------------------

    def reference(self, scope: Scope, node, expression: str, kind: str, owner: str = ""):
        self.pending.append((scope, node, expression, f"{kind}:{owner}" if owner else kind))

    def heritage(self, node, scope: Scope):
        """`extends Base implements Iface` — the supertypes themselves, not the
        type arguments they are parameterized with. `implements Sink<Frame>`
        inherits from Sink; recording Frame as a base too would put a wrong
        edge in the graph."""
        for clause in node.named_children:
            if clause.type == "extends_clause":
                bases = [clause.child_by_field_name("value")]
            elif clause.type == "implements_clause":
                bases = [c for c in clause.named_children if c.type in {"type_identifier", "nested_type_identifier", "generic_type"}]
            else:
                bases = []
            for base in bases:
                if base is None:
                    continue
                # `extends Base<T>`: the supertype is inside the generic type.
                inner = base.child_by_field_name("name") if base.type == "generic_type" else base
                expression = dotted(inner) if inner is not None else None
                if inner is not None and expression is not None:
                    self.reference(scope, inner, expression, "base", scope.owner)
                arguments = base.child_by_field_name("type_arguments")
                if arguments is not None:
                    self.walk(arguments, scope)
            for child in clause.named_children:
                if child.type == "type_arguments":
                    self.walk(child, scope)

    def declares(self, node) -> bool:
        """Whether this identifier sits where a name is being declared."""
        parent = node.parent
        if parent is None or parent.type.startswith("jsx"):
            return False
        if parent.type not in _DECLARING_PARENTS:
            return False
        return any(
            (field_node := parent.child_by_field_name(name)) is not None and field_node.id == node.id
            for name in _DECLARED_FIELDS
        )

    def walk(self, node, scope: Scope):
        if node.type == "import_statement":
            self.import_statement(node, scope)
            return
        if node.type == "export_statement":
            self.export_statement(node, scope)
            declaration = node.child_by_field_name("declaration")
            for child in node.named_children:
                # The clause/source are handled above; the declaration and any
                # exported expression still hold ordinary references.
                if child.type not in {"export_clause", "namespace_export", "string"}:
                    self.walk(child, scope)
            if declaration is not None:
                scope.locals.update(self.declared_names(declaration))
            return
        if node.type == "class_heritage":
            self.heritage(node, scope)
            return
        if node.type == "variable_declarator":
            names = pattern_names(node.child_by_field_name("name"))
            scope.locals.update(names)
            if self.require(node, scope):
                return
        if node.type == "assignment_expression":
            # Reassignment is treated as a local binding rather than analyzed:
            # the same conservative choice Python's extractor makes.
            left = node.child_by_field_name("left")
            if left is not None and left.type in _PATTERN_TYPES:
                scope.locals.update(pattern_names(left))

        symbol = self.symbols.get((node.start_byte, node.end_byte))
        if symbol is not None:
            scope.locals.add(symbol.name)
            scope = Scope(symbol.qualified_name, scope)
        elif node.type in _SCOPES:
            scope = Scope(scope.owner, scope)
        if node.type in _SCOPES or symbol is not None:
            parameters = node.child_by_field_name("parameters")
            for parameter in parameters.named_children if parameters is not None else []:
                scope.locals.update(pattern_names(parameter.child_by_field_name("pattern") or parameter))
            for name in ("parameter", "left"):
                bound = node.child_by_field_name(name)
                if bound is not None and node.type in {"catch_clause", "for_in_statement"}:
                    scope.locals.update(pattern_names(bound))

        if node.type in {"member_expression", "nested_type_identifier"}:
            expression = dotted(node)
            if expression is not None:
                self.reference(scope, node, expression, self.use_kind(node))
                return
            # Not a plain name path: its root may still be a use, and its
            # arguments/indices hold references of their own.
            for child in node.named_children:
                self.walk(child, scope)
            return
        if node.type in _REFERENCE_TYPES:
            if not self.declares(node):
                self.reference(scope, node, text(node), self.use_kind(node))
            return
        if node.type == "comment":
            return
        for child in node.named_children:
            self.walk(child, scope)

    @staticmethod
    def use_kind(node) -> str:
        parent = node.parent
        if parent is None:
            return "reference"
        if parent.type == "call_expression":
            function = parent.child_by_field_name("function")
            if function is not None and function.id == node.id:
                return "call"
        if parent.type == "new_expression":
            constructor = parent.child_by_field_name("constructor")
            if constructor is not None and constructor.id == node.id:
                return "call"
        return "reference"

    def finish(self):
        for scope, node, expression, kind in self.pending:
            owner = scope.owner
            if ":" in kind:
                kind, owner = kind.split(":", 1)
            name = expression.split(".")[0]
            current: Scope | None = scope
            while current is not None:
                bindings = current.imports.get(name, [])
                if bindings or name in current.locals:
                    if len(bindings) == 1 and name not in current.locals:
                        bindings[0].uses.append({
                            "owner": owner, "expression": expression, "kind": kind,
                            "source_text": text(node), "line": node.start_point[0] + 1,
                            "start_byte": node.start_byte, "end_byte": node.end_byte,
                        })
                    elif bindings:
                        self.result.diagnostics.append({
                            "line": node.start_point[0] + 1, "name": name,
                            "reason": "ambiguous_or_shadowed_import",
                        })
                    break
                current = current.parent


def extract_bindings(result: ParsedFile, root) -> None:
    extractor = Extractor(result)
    extractor.walk(root, extractor.module)
    extractor.finish()
    result.bound_names = sorted(extractor.module.locals)
