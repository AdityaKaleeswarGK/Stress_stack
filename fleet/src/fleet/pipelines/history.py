"""History pipeline — build the commit database for a repository.

Phase 1 of history mining: clone the full history, walk the mainline, keep
the integration points whose change clears the size caps, write
`commits.json`. Pure git — no GitHub API, no token, no network past the
clone.

It does not write task directories, and reports `tasks_emitted=0` rather
than overstating what it produced: base.py's contract says a pipeline must
never emit an unvalidated task, and a commit with a measured diff is not yet
a task. Phase 2 attaches the issue that each commit fixed (the linkage code
is in ../history/linkage.py, unwired for now); phase 3 validates by running
the suite before and after the patch.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, ClassVar

from fleet.history.commits import CommitDatabase, CommitFilter, build_database
from fleet.history.window import GitRepo, parse_since
from fleet.pipelines.base import PipelineResult

logger = logging.getLogger(__name__)


class HistoryPipeline:
    """Mainline integration points, measured and filtered. Implements `Pipeline`."""

    name: ClassVar[str] = "history"

    def __init__(self, repo_root: Path | None, options: dict[str, Any]) -> None:
        self.repo_root = repo_root
        self.options = options
        # `owner/name`, used to clone and to label the database. Optional when
        # a local clone is supplied.
        self.repo = str(options.get("repo") or "").strip()
        self.ref = str(options.get("ref") or "HEAD")
        # Optional bounds on the *walk*. The clone always holds full history,
        # so narrowing here never costs reachability.
        since = options.get("since")
        until = options.get("until")
        self.since = parse_since(str(since)) if since else None
        self.until = parse_since(str(until)) if until else None
        self.filters = CommitFilter(
            **{
                key: value
                for key, value in options.items()
                if key in CommitFilter.__slots__ and value is not None
            }
        )
        self._clone_tempdir: tempfile.TemporaryDirectory[str] | None = None

    def _open_clone(self) -> GitRepo:
        """The clone to walk: one the caller already has, or a fresh full one.

        Reusing an existing clone matters — re-cloning a repo sitting on disk
        is waste that only shows up as a slow run.
        """
        if self.repo_root and (self.repo_root / ".git").exists():
            if self.repo:
                from fleet.workspace import check_identity
                check_identity(self.repo_root, self.repo)
            return GitRepo.at(self.repo_root)
        if not self.repo:
            raise RuntimeError(
                "no repository to walk: pass a local clone path, or options['repo'] as 'owner/name'"
            )
        self._clone_tempdir = tempfile.TemporaryDirectory(prefix="fleet-history-")
        destination = Path(self._clone_tempdir.name) / "repo"
        logger.info("cloning %s (full history, blobs included)", self.repo)
        return GitRepo.clone_full(f"https://github.com/{self.repo}.git", destination)

    def cleanup(self) -> None:
        if self._clone_tempdir is not None:
            self._clone_tempdir.cleanup()
            self._clone_tempdir = None

    def build(self, *, progress: Any = None) -> CommitDatabase:
        """Build and return the commit database."""
        clone = self._open_clone()
        return build_database(
            self.repo or clone.path.name,
            clone,
            filters=self.filters,
            since=self.since,
            until=self.until,
            ref=self.ref,
            progress=progress,
        )

    def run(self, out_dir: Path) -> PipelineResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            database = self.build()
        finally:
            self.cleanup()

        manifest = out_dir / "commits.json"
        manifest.write_text(json.dumps(database.to_dict(), indent=2), encoding="utf-8")

        return PipelineResult(
            tasks_emitted=0,  # phase 1 ships measured commits, not tasks
            tasks_rejected=database.dropped_total,
            rejection_reasons=dict(database.dropped),
            detail={
                "phase": "1: commit database",
                "commits": len(database.records),
                "manifest": str(manifest),
                **database.detail,
            },
        )
