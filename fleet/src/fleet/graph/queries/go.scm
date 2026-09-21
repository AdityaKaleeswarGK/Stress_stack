(function_declaration name: (identifier) @name) @definition.function

; A Go method is not lexically inside its type — a struct and its methods can
; live in different files — so the receiver is the only thing that says which
; type a method belongs to. Capturing it as @scope.name on the method itself
; is how a language supplies a container the tree can't walk up to.
; Field order matters: a query lists fields in the order the *grammar* has
; them, and Go declares `receiver` before `name`. Swapping these two lines
; makes the pattern impossible rather than merely wrong.
(method_declaration
  receiver: (parameter_list
    (parameter_declaration type: [
      (type_identifier) @scope.name
      (pointer_type (type_identifier) @scope.name)
    ]))
  name: (field_identifier) @name) @definition.method

(type_declaration (type_spec name: (type_identifier) @name type: (struct_type))) @definition.struct
(type_declaration (type_spec name: (type_identifier) @name type: (interface_type))) @definition.interface
(type_declaration (type_spec name: (type_identifier) @name)) @definition.type

; Interface satisfaction in Go is structural: nothing in a file's syntax ever
; states "Circle implements Shape" — the compiler infers it by comparing
; method sets across a package. There is no node to capture, so Go emits no
; @base, and that is correct rather than missing.

; One entry per import *path*, not per declaration: a grouped `import (...)`
; block holds several import_specs, and capturing the block would blob every
; path into one string.
(import_spec path: (interpreted_string_literal) @import.module) @import
(import_spec path: (raw_string_literal) @import.module) @import

(call_expression function: (identifier) @call.name) @call
(call_expression function: (selector_expression field: (field_identifier) @call.name)) @call
