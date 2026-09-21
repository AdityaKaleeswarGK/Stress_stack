"""JavaScript, TypeScript and TSX.

Three specs, one query file, one family. They are separate grammars — a `.ts`
file parsed with the JavaScript grammar errors on every type annotation, and
`.tsx` needs its own grammar again — but they share every node name this
project extracts, and their imports resolve against each other: a `.ts` file
importing `"./foo"` may legitimately land on `foo.tsx`.

`family` is what expresses that last part. The previous design had no way to
say it and worked around the gap by de-duplicating resolver functions with
`id(resolver) in already_run`.
"""

from __future__ import annotations

from fleet.graph.spec import LanguageSpec, TestRules, register

_FAMILY = "javascript"
# Only "test". The previous implementation also treated an `it`/`describe`
# name prefix as a test marker, which quietly flagged `items`, `iterate` and
# `iterator` as tests. Real JS tests are registered by *calling* `describe`/
# `it`, and the query catches those as @definition.test — so the prefixes were
# buying false positives and nothing else.
_TESTS = TestRules(name_prefixes=("test",))
_INDEX = ("index",)

JAVASCRIPT = register(
    LanguageSpec(
        name="javascript",
        extensions=(".js", ".jsx", ".mjs", ".cjs"),
        family=_FAMILY,
        binder="jsts",
        index_names=_INDEX,
        tests=_TESTS,
    )
)

TYPESCRIPT = register(
    LanguageSpec(
        name="typescript",
        extensions=(".ts",),
        grammar="typescript",
        query="javascript",
        family=_FAMILY,
        binder="jsts",
        index_names=_INDEX,
        tests=_TESTS,
    )
)

TSX = register(
    LanguageSpec(
        name="tsx",
        extensions=(".tsx",),
        grammar="tsx",
        query="javascript",
        family=_FAMILY,
        binder="jsts",
        index_names=_INDEX,
        tests=_TESTS,
    )
)
