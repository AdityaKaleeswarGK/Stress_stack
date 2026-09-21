"""Rust use bindings and lexical references from the existing Tree-sitter tree.

Module loading is resolved separately. Macro expansion and receiver type inference
are deliberately not attempted here.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from fleet.graph.models import ExtractedImport, ImportBinding, ParsedFile


def text(node):
    return node.text.decode() if node else ""


def use_leaves(node, prefix=""):
    """Flatten nested use trees, preserving aliases and `self` imports."""
    if node.type == "scoped_use_list":
        path = text(node.child_by_field_name("path"))
        yield from use_leaves(node.child_by_field_name("list"), prefix + path + "::")
    elif node.type == "use_list":
        for child in node.named_children:
            yield from use_leaves(child, prefix)
    elif node.type == "use_as_clause":
        path = text(node.child_by_field_name("path"))
        full = prefix.rstrip(":") if path == "self" else prefix + path
        yield full, text(node.child_by_field_name("alias"))
    elif node.type == "use_wildcard":
        value = text(node).rstrip("*").rstrip(":")
        yield prefix + value + "::*", "*"
    else:
        value = text(node)
        full = prefix.rstrip(":") if value == "self" else prefix + value
        yield full, full.split("::")[-1]


@dataclass
class Scope:
    owner: str
    module: str
    parent: Scope | None
    imports: dict = field(default_factory=dict)
    locals: set = field(default_factory=set)


def extract_bindings(result: ParsedFile, root):
    result.bindings.clear()
    result.modules.clear()
    imports = []
    symbols = {(s.start_byte, s.end_byte): s for s in result.symbols}
    pending = []

    def attrs(node):
        values = []
        prev = node.prev_named_sibling
        while prev and prev.type == "attribute_item":
            values.append(text(prev))
            prev = prev.prev_named_sibling
        return values

    def pattern_names(node):
        if node is None:
            return []
        if node.type in {"scoped_identifier", "scoped_type_identifier", "type_identifier"}:
            return []
        if node.type in {"identifier", "shorthand_field_identifier"}:
            return [text(node)]
        type_node = node.child_by_field_name("type")
        return [name for child in node.named_children if type_node is None or child.id != type_node.id
                for name in pattern_names(child)]

    def walk(node, scope, conditional=False):
        if node.type == "use_declaration":
            arg = node.child_by_field_name("argument")
            for full, local in use_leaves(arg):
                module, _, name = full.rpartition("::")
                if not module:
                    module, name = full, ""
                b = ImportBinding(f"{result.path}@{node.start_byte}:{len(result.bindings)}", module, name, local,
                                  scope.owner, node.start_point[0]+1, text(node), "use",
                                  reexport=any(c.type == "visibility_modifier" for c in node.named_children),
                                  conditional=conditional or any("cfg" in a for a in attrs(node)), module_scope=scope.module,
                                  start_byte=node.start_byte)
                result.bindings.append(b)
                scope.imports.setdefault(local, []).append(b)
                imports.append(ExtractedImport(text(node), module, [name] if name else [], b.line))
            return
        if node.type == "mod_item":
            name = text(node.child_by_field_name("name"))
            body = node.child_by_field_name("body")
            result.modules.append({"name": name, "parent": scope.module, "inline": body is not None,
                                   "line": node.start_point[0]+1, "attributes": attrs(node)})
            if body:
                symbol = symbols.get((node.start_byte, node.end_byte))
                # Parent Rust module imports are not implicitly in scope in a child.
                sub = Scope(symbol.qualified_name if symbol else name,
                            ".".join(filter(None, [scope.module, name])), None)
                walk(body, sub, conditional or any("cfg" in a for a in attrs(node)))
            return
        symbol = symbols.get((node.start_byte, node.end_byte))
        if symbol:
            # Name declarations shadow imported names in the surrounding scope.
            scope.locals.add(symbol.name)
            scope = Scope(symbol.qualified_name, scope.module, scope)
            if node.type == "function_item":
                params = node.child_by_field_name("parameters")
                for param in params.named_children if params else []:
                    scope.locals.update(pattern_names(param.child_by_field_name("pattern")))
        elif node.type in {"block", "closure_expression", "match_arm", "for_expression"}:
            scope = Scope(scope.owner, scope.module, scope)
            if node.type == "closure_expression":
                scope.locals.update(pattern_names(node.child_by_field_name("parameters")))
            if node.type in {"match_arm", "for_expression"}:
                scope.locals.update(pattern_names(node.child_by_field_name("pattern")))
        conditional = conditional or any("cfg" in a for a in attrs(node))
        if node.type == "let_declaration":
            scope.locals.update(pattern_names(node.child_by_field_name("pattern")))
        if node.type in {"attribute_item", "line_comment", "block_comment", "token_tree"}:
            if node.type == "token_tree":
                result.diagnostics.append({"line": node.start_point[0]+1, "reason": "macro_tokens_not_resolved"})
            return
        if node.type in {"scoped_identifier", "scoped_type_identifier", "identifier", "type_identifier"}:
            # Skip declaration/field labels and patterns; visit expression/type paths once.
            parent = node.parent
            if parent:
                for field_name in ("name", "pattern", "field"):
                    field_node = parent.child_by_field_name(field_name)
                    if field_node and field_node.id == node.id:
                        return
            expression = text(node)
            kind = "reference"
            if parent and parent.type == "call_expression" and parent.child_by_field_name("function").id == node.id:
                kind = "call"
            pending.append((scope, node, expression, kind))
            return
        for child in node.named_children:
            walk(child, scope, conditional)

    walk(root, Scope("", "", None))
    for scope, node, expr, kind in pending:
        name = expr.split("::")[0]
        current = scope
        while current:
            bindings = current.imports.get(name, [])
            if bindings or name in current.locals:
                if len(bindings) == 1 and name not in current.locals:
                    bindings[0].uses.append({"owner": scope.owner, "expression": expr, "kind": kind,
                                            "source_text": expr,
                                            "line": node.start_point[0]+1, "start_byte": node.start_byte,
                                            "end_byte": node.end_byte})
                elif bindings:
                    result.diagnostics.append({"line": node.start_point[0]+1, "reason": "ambiguous_or_shadowed_import", "name": name})
                break
            if "*" in current.imports:
                break
            current = current.parent
    # Preserve statement groups for the old file-import viewer and callers.
    grouped = {}
    for imp in imports:
        key = (imp.line, imp.module, imp.raw)
        if key not in grouped:
            grouped[key] = imp
        else:
            grouped[key].symbols.extend(imp.symbols)
    result.imports = list(grouped.values())
