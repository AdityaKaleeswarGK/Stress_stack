; JavaScript, TypeScript and TSX all share this file: the node names below are
; identical across the three grammars. Only the *grammar* differs (see
; LanguageSpec.grammar) — parsing a .ts file with the JavaScript grammar
; reports a syntax error on every type annotation, and .tsx needs its own
; grammar again or the first JSX element errors.

(function_declaration name: (identifier) @name) @definition.function
(generator_function_declaration name: (identifier) @name) @definition.function

; A wildcard name node, because JavaScript spells a class name `identifier`
; and TypeScript spells it `type_identifier`.
(class_declaration name: (_) @name) @definition.class
; Merged onto the pattern above by node span, so `extends` needs no repeat of
; the whole class pattern. The three heritage shapes are genuinely
; dialect-specific — plain JS puts the superclass straight under
; class_heritage, TypeScript wraps it in extends_clause and adds
; implements_clause. A pattern impossible in one grammar is dropped for that
; grammar alone (see engine._compile), which is what lets all three languages
; share this file.
(class_declaration (class_heritage (identifier) @base)) @definition.class
(class_declaration (class_heritage (extends_clause (identifier) @base))) @definition.class
(class_declaration (class_heritage (implements_clause (type_identifier) @base))) @definition.class

(method_definition name: (property_identifier) @name) @definition.method

; TypeScript's type-level declarations. Plain JavaScript has no node for any
; of these, so its grammar rejects each pattern and `engine._compile` drops
; them for that grammar alone — which is the same mechanism the class_heritage
; patterns above rely on.
;
; These are not decoration. `import { GameSnapshot } from '../types/game'`
; names an interface, and until it was extracted the file exporting it
; produced no symbols at all, so the import had nothing to resolve onto.
(interface_declaration name: (type_identifier) @name) @definition.interface
(enum_declaration name: (identifier) @name) @definition.enum
(type_alias_declaration name: (type_identifier) @name) @definition.type
(abstract_class_declaration name: (type_identifier) @name) @definition.class

; Enum members, so `GamePhase.Menu` resolves to the member rather than
; stopping at the enum. Both captures sit on the same node for the bare form:
; the member *is* its own name. Anchoring on `enum_body` instead would give
; one match for the whole body no matter how many members it holds.
(enum_body (property_identifier) @name @definition.member)
(enum_assignment name: (property_identifier) @name) @definition.member

; `const handleClick = () => {}` — JS's other way to define a function. The
; declarator carries the name and line span; @value carries the body, and the
; async/generator keywords that live on the function expression itself.
; `const [a, setA] = useState()` is excluded for free: its name field is an
; array_pattern, not an identifier.
(variable_declarator
  name: (identifier) @name
  value: [(arrow_function) (function_expression) (generator_function)] @value) @definition.function

; Tests registered by calling a framework function with a name string produce
; no declaration node at all, so they need their own pattern.
(call_expression
  function: [
    (identifier) @call.name
    (member_expression object: (identifier) @call.name)
  ]
  arguments: (arguments . [(string) (template_string)] @test.label)
  (#any-of? @call.name "describe" "it" "test" "suite" "bench")) @definition.test

(import_statement source: (string) @import.module) @import
(import_statement (import_clause (identifier) @import.symbol)) @import
(import_statement (import_clause (namespace_import (identifier) @import.symbol))) @import
; `{ Foo as Bar }` — the alias is the name actually visible in this file, and
; it is the specifier's last identifier. A TypeScript `type`-only specifier
; drops its `type` keyword for free: that is a sibling token, not part of the
; name.
(import_statement
  (import_clause (named_imports (import_specifier name: (identifier) @import.symbol)))) @import
(import_statement
  (import_clause (named_imports (import_specifier alias: (identifier) @import.symbol)))) @import

(call_expression function: (identifier) @call.name) @call
(call_expression function: (member_expression property: (property_identifier) @call.name)) @call
