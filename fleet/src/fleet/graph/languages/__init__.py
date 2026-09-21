"""Language registrations. Python/Rust have import-use resolution; JS/TS keep
the existing syntax/file-import support. Go/Java remain legacy opt-in parsers.
"""

from __future__ import annotations

from fleet.graph.languages import go, java, javascript, python, rust

__all__ = ["go", "java", "javascript", "python", "rust"]
