(function_item name: (identifier) @name) @definition.function
(struct_item name: (type_identifier) @name) @definition.struct
(trait_item name: (type_identifier) @name) @definition.trait
(enum_item name: (type_identifier) @name) @definition.enum
(mod_item name: (identifier) @name body: (declaration_list)) @definition.module

; An impl block produces no symbol of its own, but it is what names the
; functions inside it: `impl GlobalAlloc for Bump` makes `alloc` into
; `Bump.alloc` and hands it `GlobalAlloc` as a base. That is exactly what
; @scope is for.
(impl_item type: (type_identifier) @scope.name) @scope
(impl_item type: (generic_type type: (type_identifier) @scope.name)) @scope
(impl_item trait: (type_identifier) @scope.base) @scope
(impl_item trait: (generic_type type: (type_identifier) @scope.base)) @scope

; Reading the `argument` field means `pub use ...` needs no visibility
; stripping — the path alone is captured. Each `use` shape splits into a
; module and the names it brings in, so `crate::models::{ASTNode, MathError}`
; resolves against the file `crate::models` rather than being one opaque
; string. `use crate::models` (no item) still works: the resolver tries the
; module and then module-plus-symbol, the same two shapes Python's
; `from pkg import submodule` needs.
(use_declaration
  argument: (scoped_identifier
    path: (_) @import.module
    name: (identifier) @import.symbol)) @import
(use_declaration
  argument: (scoped_use_list
    path: (_) @import.module
    list: (use_list (identifier) @import.symbol))) @import
(use_declaration
  argument: (use_as_clause
    path: (scoped_identifier
      path: (_) @import.module
      name: (identifier) @import.symbol))) @import
; A bare `use foo;` has no path segment to split.
(use_declaration argument: (identifier) @import.module) @import

; #[test] is an attribute_item *sibling* of the function, not a child, so it
; is picked up structurally by the engine rather than captured here.

(call_expression function: (identifier) @call.name) @call
(call_expression function: (field_expression field: (field_identifier) @call.name)) @call
(call_expression function: (scoped_identifier name: (identifier) @call.name)) @call
(macro_invocation macro: (identifier) @call.name) @call
