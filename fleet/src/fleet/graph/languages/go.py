"""Go.

No import resolver yet, deliberately. A Go import path is absolute against the
module path declared in `go.mod` (`github.com/user/proj/internal/cast`), so
resolving one to a file in this repo means reading go.mod first — real work,
not a one-line strategy. Until that exists, Go files keep their raw imports and
contribute no import edges, which is honest rather than guessed.
"""

from __future__ import annotations

from fleet.graph.spec import LanguageSpec, TestRules, register

GO = register(
    LanguageSpec(
        name="go",
        extensions=(".go",),
        # Go's testing package requires exactly these prefixes, capital included.
        tests=TestRules(name_prefixes=("Test", "Benchmark", "Fuzz")),
    )
)
