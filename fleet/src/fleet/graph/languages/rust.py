"""Rust.

`#[test]` is an attribute node preceding the function, not a name convention
and not a child — the engine picks up preceding attribute siblings for any
language, so this spec only has to say which text marks a test.
"""

from __future__ import annotations

from fleet.graph.spec import LanguageSpec, TestRules, register

RUST = register(
    LanguageSpec(
        name="rust",
        extensions=(".rs",),
        # Same "dotted" strategy Python and Java use, told how Rust spells
        # things: `::` between segments, absolute paths rooted at `crate`, and
        # `mod.rs`/`lib.rs`/`main.rs` standing in for what contains them, so
        # `src/models.rs` is `crate::models` and `src/main.rs` is `crate`.
        resolver="dotted",
        # Use trees, module declarations and lexical uses, read from the tree
        # the shared engine already built. See `engine.BINDERS`.
        binder="rust",
        source_roots=("src",),
        index_names=("mod", "lib", "main"),
        separator="::",
        root_name="crate",
        self_marker="self",
        parent_marker="super",
        tests=TestRules(name_prefixes=("test_",), annotations=("test",)),
    )
)
