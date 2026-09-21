"""Time-windowed history: clone only the window, walk only the mainline.

Two ideas, both replacing a count:

**Recency, not `--depth=N`.** `git clone --shallow-since=<date>` puts the
graft point exactly at the window boundary, so a repo's early history is
never fetched — verified on `pallets/click`, where a two-month window is 91
commits and 2.8 seconds against a history of several thousand. `--depth=N`
can only approximate that, and the right N differs per repo by orders of
magnitude. Paired with `--filter=blob:none`, file contents arrive on demand
rather than up front.

**Integration points, not commits.** `git log --first-parent` walks the
mainline and steps *over* the inside of each merged branch, so one entry per
landed PR — which is what "only take merges" means in git terms, without
`--merges` dropping every squash-merged repo on the floor (a squash lands as
a single-parent commit and `--merges` would never see it).

Deepening is incremental: if a candidate's base commit turns out to sit
before the graft, `git fetch --shallow-since=<earlier>` moves the boundary
back once rather than unshallowing the whole repository.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fleet.history.linkage import pr_number_from_subject
from fleet.history.merge_style import classify
from fleet.history.models import MergePoint
from fleet.history.patch import PatchStats, parse_numstat

logger = logging.getLogger(__name__)

# `6mo`, `90d`, `12w`, `2y`, `36h`
_RELATIVE_RE = re.compile(r"^(\d+)\s*(h|d|w|mo|m|y)$", re.IGNORECASE)
_UNIT_DAYS = {"h": 1 / 24, "d": 1.0, "w": 7.0, "mo": 30.44, "m": 30.44, "y": 365.25}
# git log --pretty field separator. Chosen over the usual `|` because commit
# subjects contain pipes often enough to matter.
_FIELD = "\x1f"
_LOG_FORMAT = _FIELD.join(["%H", "%P", "%aI", "%cI", "%s"])


class GitError(RuntimeError):
    pass


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """Parse a window start: `6mo`, `90d`, `2026-01-01`, or a full ISO stamp.

    Relative forms are what a caller actually types; absolute forms are what
    a reproducible run records. Both land on an aware UTC datetime so the
    value can go straight into `--shallow-since` and into the report.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError("empty --since")
    now = now or datetime.now(UTC)

    match = _RELATIVE_RE.match(text)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        if amount <= 0:
            raise ValueError(f"--since must be positive, got {text!r}")
        return now - timedelta(days=amount * _UNIT_DAYS[unit])

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"unrecognized --since {text!r}: use a relative window (6mo, 90d, 12w) "
            "or an ISO date (2026-01-01)"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _run(args: list[str], *, cwd: Path | None = None, timeout: int = 600) -> str:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise GitError(f"{' '.join(args[:3])}… failed ({result.returncode}): {result.stderr.strip()[:400]}")
    return result.stdout


@dataclass(slots=True)
class GitRepo:
    """A local clone, shallow or not, that we only ever read."""

    path: Path

    # --- construction ----------------------------------------------------

    @classmethod
    def clone_since(
        cls,
        url: str,
        dest: Path,
        *,
        since: datetime,
        branch: str | None = None,
        blobless: bool = True,
        timeout: int = 900,
    ) -> GitRepo:
        """Clone `url` with history truncated at `since`.

        `--single-branch` is implied by the shallow options; we pass it
        explicitly so the intent is visible. A repo whose default branch has
        no commits inside the window clones to an empty history rather than
        failing, and the caller's walk simply finds no candidates.
        """
        if dest.exists() and any(dest.iterdir()):
            raise GitError(f"clone destination is not empty: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "git",
            "clone",
            "--quiet",
            f"--shallow-since={since.date().isoformat()}",
            "--single-branch",
        ]
        if blobless:
            # File contents on demand. The walk only needs commit metadata;
            # blobs are fetched later, and only for the candidates that survive.
            args.append("--filter=blob:none")
        if branch:
            args += ["--branch", branch]
        args += [url, str(dest)]
        _run(args, timeout=timeout)
        return cls(path=dest)

    @classmethod
    def clone_full(
        cls,
        url: str,
        dest: Path,
        *,
        branch: str | None = None,
        blobless: bool = False,
        timeout: int = 1800,
    ) -> GitRepo:
        """Clone the complete history, blobs included. The default for mining.

        `blobless=True` exists but is a trap for this workload, and the
        numbers are not close. Mining diffs *every* commit it keeps, so
        deferring blobs converts a local diff into a network round trip per
        commit. Measured on `pallets/click` (1,381 first-parent commits):

            blobless clone        3.3s, then ~15 min of diffs
            blobless, one pass    3.3s, then 1m41s — and it silently
                                  emitted only 99 of the 1,381 commits
            full clone            3.9s, then 0.54s   <- this

        The saving was 3.2 MB against 6.6 MB. Not worth 0.54s becoming
        fifteen minutes, and certainly not worth losing 93% of the history
        to a partial lazy fetch that reports no error.
        """
        if dest.exists() and any(dest.iterdir()):
            raise GitError(f"clone destination is not empty: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["git", "clone", "--quiet"]
        if blobless:
            args.append("--filter=blob:none")
        if branch:
            args += ["--branch", branch]
        args += [url, str(dest)]
        _run(args, timeout=timeout)
        return cls(path=dest)

    @classmethod
    def at(cls, path: Path) -> GitRepo:
        repo = cls(path=path)
        if not (path / ".git").exists() and not repo._is_git_dir():
            raise GitError(f"not a git repository: {path}")
        return repo

    def _is_git_dir(self) -> bool:
        try:
            return _run(["git", "rev-parse", "--is-inside-work-tree"], cwd=self.path).strip() == "true"
        except GitError:
            return False

    # --- shallow state ---------------------------------------------------

    @property
    def is_shallow(self) -> bool:
        return _run(["git", "rev-parse", "--is-shallow-repository"], cwd=self.path).strip() == "true"

    def graft_points(self) -> list[str]:
        """The SHAs where truncated history stops.

        A commit listed here has no parent *locally* even though it has one
        upstream — so a patch whose base is at or before a graft cannot be
        checked out until the clone is deepened.
        """
        shallow_file = self.path / ".git" / "shallow"
        if not shallow_file.is_file():
            return []
        return [line.strip() for line in shallow_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    def deepen_since(self, since: datetime, *, timeout: int = 900) -> None:
        """Move the graft boundary back to `since`. Cheaper than unshallowing.

        Called when a candidate's base commit falls outside the current
        window — a PR opened long before it merged, typically.
        """
        logger.info("deepening clone to %s", since.date().isoformat())
        _run(
            ["git", "fetch", "--quiet", f"--shallow-since={since.date().isoformat()}", "origin"],
            cwd=self.path,
            timeout=timeout,
        )

    # --- reads -----------------------------------------------------------

    def contains(self, sha: str) -> bool:
        """Whether `sha` is present locally *and* an ancestor of HEAD.

        Presence alone isn't enough: a blobless clone can hold a commit
        object that no longer belongs to the mainline.
        """
        if not sha:
            return False
        try:
            _run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=self.path)
            return True
        except GitError:
            return False

    def has_object(self, sha: str) -> bool:
        try:
            _run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=self.path)
            return True
        except GitError:
            return False

    def patch_id(self, sha: str) -> str | None:
        """`git patch-id` for one commit — a hash of its diff alone.

        Survives a rebase or cherry-pick, which is what makes it the best
        available evidence that two differently-named commits are the same
        change. Returns None when the commit's diff can't be produced (a
        graft-boundary commit has no parent to diff against).
        """
        try:
            diff = _run(["git", "diff-tree", "-p", "--no-color", sha], cwd=self.path)
        except GitError:
            return None
        if not diff.strip():
            return None
        result = subprocess.run(
            ["git", "patch-id", "--stable"],
            cwd=str(self.path),
            input=diff,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return result.stdout.split()[0]

    def diff_stats(self, base: str, head: str, *, timeout: int = 300) -> PatchStats | None:
        """Per-file shape of `base..head` — the candidate's oracle patch.

        For a merge commit, `base` is `parents[0]` and `head` the merge
        itself, so this is exactly the net change the branch brought onto
        the mainline. Returns None when the diff can't be produced (a base
        upstream of the graft, or a blob fetch that fails offline) so the
        caller can reject with a reason instead of treating it as empty.

        On a blobless clone the first call fetches the blobs it needs, which
        costs about a second and no API budget.
        """
        try:
            output = _run(
                ["git", "diff", "--numstat", "--no-color", base, head],
                cwd=self.path,
                timeout=timeout,
            )
        except GitError as exc:
            logger.debug("diff %s..%s unavailable: %s", base[:8], head[:8], exc)
            return None
        return parse_numstat(output)

    def has_commits(self, ref: str = "HEAD") -> bool:
        """Whether `ref` resolves to anything.

        A freshly `git init`-ed repository has an unborn HEAD, and `git log
        HEAD` fails there with a bare "ambiguous argument" — which reads
        like a bug in the caller rather than "this repo has no history
        yet". Checked so an empty window stays an empty window.
        """
        try:
            _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=self.path)
            return True
        except GitError:
            return False

    def commit_message(self, sha: str) -> str:
        """The full message of one commit, subject and body.

        Fetched per commit rather than captured in the log walk: `%B` is
        multi-line and would need a record separator threaded through the
        whole parse, and only the handful of commits that reach linkage
        actually need their body.
        """
        try:
            return _run(["git", "log", "-1", "--pretty=format:%B", sha], cwd=self.path)
        except GitError:
            return ""

    def default_branch(self) -> str:
        try:
            ref = _run(["git", "symbolic-ref", "--short", "HEAD"], cwd=self.path).strip()
            return ref or "HEAD"
        except GitError:
            return "HEAD"

    def first_parent_points(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        ref: str = "HEAD",
        max_points: int = 5000,
    ) -> list[MergePoint]:
        """Every integration point on the mainline, newest first.

        `--first-parent` is the whole trick: it follows only the mainline
        side of each merge, so the inside of a merged branch never appears
        and each landed PR shows up exactly once — whether it landed as a
        merge commit, a squash, or a rebase.
        """
        if not self.has_commits(ref):
            logger.info("%s has no commits at %s — empty window", self.path, ref)
            return []

        args = ["git", "log", "--first-parent", f"--pretty=format:{_LOG_FORMAT}"]
        if since:
            args.append(f"--since={since.isoformat()}")
        if until:
            args.append(f"--until={until.isoformat()}")
        args += [f"--max-count={max_points}", ref]

        points: list[MergePoint] = []
        for line in _run(args, cwd=self.path).splitlines():
            if not line.strip():
                continue
            fields = line.split(_FIELD)
            if len(fields) < 5:
                logger.debug("unparseable log line: %r", line[:120])
                continue
            sha, parents_raw, author_date, committer_date, subject = fields[:5]
            point = MergePoint(
                sha=sha,
                subject=subject,
                parents=tuple(p for p in parents_raw.split() if p),
                author_date=author_date,
                committer_date=committer_date,
                pr_number=pr_number_from_subject(subject),
            )
            # Classification reads the point's own parents and subject, so it
            # runs on the constructed value and the verdict is stamped back on.
            style, evidence = classify(point)
            points.append(replace(point, style=style, style_evidence=evidence))
        return points
