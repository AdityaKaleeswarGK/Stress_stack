; Java was not supported before this file existed. It needed no new
; dependency, no engine change and no resolver work — one spec entry and this
; query. That is the whole point of the layout.

(class_declaration name: (identifier) @name) @definition.class
(class_declaration superclass: (superclass (type_identifier) @base)) @definition.class
(class_declaration
  interfaces: (super_interfaces (type_list (type_identifier) @base))) @definition.class

(interface_declaration name: (identifier) @name) @definition.interface
(enum_declaration name: (identifier) @name) @definition.enum
(record_declaration name: (identifier) @name) @definition.class

(method_declaration name: (identifier) @name) @definition.method
(constructor_declaration name: (identifier) @name) @definition.method

; @Test / @ParameterizedTest — TestRules matches these by substring, so JUnit 4
; and 5 both land without a second pattern.
(method_declaration (modifiers (marker_annotation name: (identifier) @annotation))) @definition.method
(method_declaration (modifiers (annotation name: (identifier) @annotation))) @definition.method

(import_declaration (scoped_identifier) @import.module) @import
(import_declaration (identifier) @import.module) @import

(method_invocation name: (identifier) @call.name) @call
(object_creation_expression type: (type_identifier) @call.name) @call
