"""Per-file dispatch: detect the language, hand it to its declared backend.

This is the whole of it. There is no per-language branch here — a spec names
its backend, and the two backends are language-agnostic. A file whose language
has no working grammar comes back with `parser == "none"` rather than a guess;
there is no regex fallback.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import Callable

from fleet.graph import engine, pyast
from fleet.graph import languages as _languages  # noqa: F401 — registers every spec
from fleet.graph.models import ParsedFile
from fleet.graph.spec import LanguageSpec, detect_language, spec_for

BACKENDS: dict[str, Callable[[LanguageSpec, str, str], ParsedFile]] = {
    "tree_sitter": engine.extract,
    "python_ast": pyast.extract,
}


def parse_source(path: str, code: str) -> ParsedFile:
    """Parse one file's source. `path` is used to detect its language, and is
    recorded on every symbol so they carry repo-unique ids."""
    language = detect_language(path)
    if language is None:
        return ParsedFile(path=path, language=None)

    if not code.strip():
        # Empty is not the same as unparseable, and a graph that conflates them
        # reports a gap as a fact.
        return ParsedFile(path=path, language=language, is_empty=True)

    spec = spec_for(language)
    if spec is None:  # pragma: no cover — registry and detector share a table
        return ParsedFile(path=path, language=language)

    backend = BACKENDS.get(spec.backend)
    if backend is None:
        return ParsedFile(path=path, language=language)
    parsed = backend(spec, path, code)
    parsed.source_hash = hashlib.sha256(code.encode()).hexdigest()
    _disambiguate_ids(parsed)
    return parsed


def _disambiguate_ids(parsed: ParsedFile) -> None:
    """Keep every symbol id in a file unique.

    Two symbols can legitimately share a qualified name — Rust's `impl
    From<A> for E` and `impl From<B> for E` both yield `E.from`, and the
    generic parameter that distinguishes them is not part of this model. Left
    alone they collapse into one graph node, silently merging two different
    functions. Only the colliding ones get a suffix, so ordinary symbols keep
    a clean `path::Qualified.Name` id.
    """
    counts: dict[str, int] = {}
    for symbol in parsed.symbols:
        counts[symbol.qualified_name] = counts.get(symbol.qualified_name, 0) + 1
    if all(count == 1 for count in counts.values()):
        return
    parsed.symbols = [
        replace(symbol, id_suffix=f"@{symbol.start_byte if symbol.start_byte is not None else symbol.start_line}")
        if counts[symbol.qualified_name] > 1
        else symbol
        for symbol in parsed.symbols
    ]
