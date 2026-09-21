"""Repository-relative .fleetignore rules using Git-style pattern semantics.

Built-in exclusions are overridable with later patterns. Re-including a file
under an excluded directory requires re-including its parent directory first.
Only .fleetignore is read; machine/global Git settings do not affect a scan.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from pathspec import GitIgnoreSpec

IGNORE_FILENAME = ".fleetignore"

DEFAULT_PATTERNS: tuple[str, ...] = (
    # version control, and known tools' own working-state directories —
    # .stress_stack/ is a predecessor tool's, kept for the same reason .git/
    # and .fleet/ are: a repo that's been run through it accumulates full
    # copies of itself underneath (glom scanned 32 files without this line,
    # 3,572 with it missing — every past benchmark run's input/solution
    # trees got walked as if they were the repository's own source).
    ".git/",
    ".fleet/",
    ".stress_stack/",
    "repo_graph.json",
    # python
    "__pycache__/",
    ".venv/",
    "venv/",
    ".tox/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    "*.egg-info",
    # javascript / typescript
    "node_modules/",
    ".pnpm-store/",
    ".yarn/",
    "dist/",
    "build/",
    ".next/",
    "*.min.js",
    "*.min.css",
    "*.tsbuildinfo",
    # Generated ambient declaration files. Named individually rather than
    # excluding `*.d.ts`, because a hand-written one is real API surface a
    # repository's own imports can point at. These are not: they are a tool's
    # snapshot of a platform's globals, regenerated on demand. agustus is six
    # source files and 1,101 symbols, 1,045 of which came from Wrangler's
    # worker-configuration.d.ts — the repository's own code was 5% of its own
    # graph.
    "worker-configuration.d.ts",
    "vite-env.d.ts",
    "next-env.d.ts",
    "*.generated.d.ts",
    # go
    "vendor/",
    # generic: build output / code that isn't the repository's own, carried
    # over from before this file existed rather than newly scoped in
    "target/",
    "third_party/",
    "external/",
)

@dataclass
class IgnoreRules:
    spec: GitIgnoreSpec

    def matches(self, path: str, *, is_dir: bool) -> bool:
        parts = Path(path).parts
        if any(p in {".git", ".fleet", ".stress_stack"} for p in parts):
            return True
        return self.spec.match_file(path.replace("\\", "/") + ("/" if is_dir else ""))


def load_ignore_rules(root: Path) -> IgnoreRules:
    lines = list(DEFAULT_PATTERNS)
    ignore_file = root / IGNORE_FILENAME
    if ignore_file.is_file():
        lines += ignore_file.read_text(encoding="utf-8").splitlines()
    return IgnoreRules(GitIgnoreSpec.from_lines(lines))
