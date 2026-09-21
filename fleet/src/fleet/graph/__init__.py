"""Repository graph API: source parsing, import bindings, typed edges and queries.

Python AST and Rust Tree-sitter extraction feed deterministic import resolution.
JS/TS file navigation remains available. No LLM or target-code execution is used.
"""

from __future__ import annotations

from fleet.graph.models import (
    CONTAIN,
    IMPORT,
    INHERIT,
    INVOKE,
    SCHEMA_VERSION,
    Edge,
    ExtractedCall,
    ExtractedImport,
    ExtractedSymbol,
    ParsedFile,
)
from fleet.graph.parser import parse_source
from fleet.graph.scan import RepoGraph, scan_repo, write_graph
from fleet.graph.spec import (
    LanguageSpec,
    TestRules,
    detect_language,
    register,
    supported_extensions,
    supported_languages,
)

__all__ = [
    "CONTAIN",
    "IMPORT",
    "INHERIT",
    "INVOKE",
    "SCHEMA_VERSION",
    "Edge",
    "ExtractedCall",
    "ExtractedImport",
    "ExtractedSymbol",
    "LanguageSpec",
    "ParsedFile",
    "RepoGraph",
    "TestRules",
    "detect_language",
    "parse_source",
    "register",
    "scan_repo",
    "supported_extensions",
    "supported_languages",
    "write_graph",
]
