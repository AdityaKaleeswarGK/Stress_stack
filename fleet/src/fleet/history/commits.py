"""The commit database: every integration point on the mainline, measured.

Phase 1 of history mining, and deliberately the whole of it. No GitHub API,
no token, no network beyond the clone — just git. What comes out is a sorted,
filtered list of the commits where work actually landed, each carrying the
size of the change it brought.

    full clone  ->  first-parent walk  ->  integration points only
                ->  diff numstat per point  ->  size caps  ->  commits.json

Two decisions worth naming.

**Full history, not a window.** A date-truncated clone is cheap but it caps
what can ever be mined, and the graft boundary then shows up as candidates
with no reachable base commit. The clone includes blobs: `--filter=blob:none`
was tried and is a trap here, because mining diffs everything it keeps, so
deferring blobs turns each local diff into a network round trip. See
`window.GitRepo.clone_full` for the measurements.

**Size is measured in source lines.** Measured on `pallets/click` PR #3739,
the diff is 1 line of fix, 71 lines of test, 5 lines of changelog. A cap
reading the 78-line total calls that a medium change; it is a one-line fix.
So the caps read `source_loc` and the totals are recorded alongside for
reference. See patch.py for the path classifier this rests on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fleet.history.linkage import closed_issue_numbers
from fleet.history.models import MergePoint, MergeStyle, Rejection
from fleet.history.patch import PatchStats, difficulty_bucket
from fleet.history.window import GitRepo

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "0.1.0"

# Styles that represent work landing from a branch. `UNKNOWN` is the
# leftover: a single-parent commit with no `(#N)` suffix.
#
# Those are *not* dropped. An earlier version discarded them as
# `not_an_integration_point`, which conflated three different things: a
# genuine direct push, a rebase-merge (which preserves the author's subject
# and so never carries `(#N)`), and a squash whose message someone edited.
# Only the first is really not an integration point; the other two are
# landed PRs whose metadata a clone cannot recover. They go to
# `needs_enrichment` instead, where `/commits/{sha}/pulls` can resolve them.
#
# A second limitation to name rather than paper over: a first-parent walk
# sees each replayed commit of a rebase-merge separately and has no way to
# group them back into one PR. Grouping needs the PR's commit list.
INTEGRATION_STYLES = frozenset({MergeStyle.MERGE_COMMIT, MergeStyle.SQUASH, MergeStyle.REBASE})


@dataclass(slots=True)
class CommitFilter:
    """The hard caps. Every one reads source lines, never the diff total."""

    # Nonempty, by default. A floor of 3 looked reasonable and was wrong:
    # a one-line replacement measures 2 (one added, one removed), so it
    # rejected `pallets/click#3739` — a one-line fix shipped with a 71-line
    # regression test, which is the *best* shape a mined task can have.
    # Verified: that PR is absent from a real run under the old default.
    # Size is scope, not difficulty; record it and let ranking use it.
    min_source_loc: int = 1
    # Above this it is a refactor or a feature, not a fix.
    max_source_loc: int = 400
    # A change spread this wide is a rename or a sweep.
    max_source_files: int = 10
    # A diff with no application code in it leaves nothing to fix: test-only,
    # docs-only, and CI-only changes all land here.
    require_source_change: bool = True
    # Keep only merge/squash/rebase points, dropping direct pushes.
    require_integration_point: bool = True

    def rejection_for(
        self, point: MergePoint, stats: PatchStats, *, skip_integration_check: bool = False
    ) -> Rejection | None:
        """The first cap this commit fails, or None if it passes them all.

        Ordered most-fundamental first: a test-only diff that is also tiny
        reports `no_source_change`, because telling someone to lower the
        size floor would never help there.
        """
        if (
            not skip_integration_check
            and self.require_integration_point
            and point.style not in INTEGRATION_STYLES
        ):
            return Rejection.NOT_AN_INTEGRATION_POINT
        if self.require_source_change and not stats.has_source_change:
            return Rejection.NO_SOURCE_CHANGE
        if len(stats.source_files) > self.max_source_files:
            return Rejection.TOO_MANY_SOURCE_FILES
        if stats.source_loc < self.min_source_loc:
            return Rejection.DIFF_TOO_SMALL
        if stats.source_loc > self.max_source_loc:
            return Rejection.DIFF_TOO_LARGE
        return None


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """One integration point, with the change it brought measured.

    `base_sha` is `parents[0]` — the mainline commit the work landed on, and
    the commit a patch for this change applies against. Not the same as the
    GitHub API's `pull_request.base.sha`, which is the base branch tip at
    last sync and is stale whenever the base advanced during review.
    """

    sha: str
    base_sha: str
    subject: str
    style: MergeStyle
    style_evidence: str = ""
    pr_number: int | None = None
    # Issue numbers the commit message claims to close. A *claim* read from
    # text, not GitHub's recorded linkage — most repos put nothing here, and
    # confirming it needs the API.
    closes_issues: tuple[int, ...] = ()
    author_date: str = ""
    committer_date: str = ""
    patch: PatchStats = field(default_factory=PatchStats)

    @property
    def added(self) -> int:
        return sum(change.added for change in self.patch.files)

    @property
    def deleted(self) -> int:
        return sum(change.removed for change in self.patch.files)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "base_sha": self.base_sha,
            "subject": self.subject,
            "style": str(self.style),
            "style_evidence": self.style_evidence,
            "pr_number": self.pr_number,
            "closes_issues": list(self.closes_issues),
            "author_date": self.author_date,
            "committer_date": self.committer_date,
            "added": self.added,
            "deleted": self.deleted,
            "source_loc": self.patch.source_loc,
            "test_loc": self.patch.test_loc,
            "difficulty": difficulty_bucket(self.patch),
            "files": [
                {
                    "path": change.path,
                    "added": change.added,
                    "deleted": change.removed,
                    "kind": str(change.kind),
                    **({"binary": True} if change.binary else {}),
                }
                for change in self.patch.files
            ],
        }


@dataclass
class CommitDatabase:
    """Everything one run kept, and a count of everything it dropped."""

    repo: str
    ref: str = "HEAD"
    built_at: str = ""
    records: list[CommitRecord] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    # Commits whose provenance a clone can't settle — a rebase-merge, or a
    # squash with an edited message, both indistinguishable from a direct
    # push locally. Deferred rather than dropped: `/commits/{sha}/pulls`
    # resolves them, and discarding them here would silently lose every
    # rebase-merged PR in repos that use that button.
    needs_enrichment: list[CommitRecord] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def drop(self, reason: Rejection) -> None:
        key = str(reason)
        self.dropped[key] = self.dropped.get(key, 0) + 1

    @property
    def dropped_total(self) -> int:
        return sum(self.dropped.values())

    def by_style(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            key = str(record.style)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "repo": self.repo,
            "ref": self.ref,
            "built_at": self.built_at,
            "summary": {
                "kept": len(self.records),
                "dropped": self.dropped_total,
                "needs_enrichment": len(self.needs_enrichment),
                "by_style": self.by_style(),
                **self.detail,
            },
            "dropped_reasons": dict(sorted(self.dropped.items(), key=lambda kv: -kv[1])),
            "commits": [record.to_dict() for record in self.records],
            "needs_enrichment": [record.to_dict() for record in self.needs_enrichment],
        }


def build_database(
    repo_name: str,
    clone: GitRepo,
    *,
    filters: CommitFilter | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    ref: str = "HEAD",
    max_points: int = 50_000,
    progress: Any = None,
) -> CommitDatabase:
    """Walk the mainline and measure every integration point that survives.

    `since`/`until` bound the *walk*, not the clone — the clone holds full
    history, so narrowing the walk never costs reachability the way a
    truncated clone does.

    Provenance is settled before the diff, but an unresolved commit is
    still measured — it may be a rebase-merge worth enriching, and the size
    gates apply to it either way.
    """
    filters = filters or CommitFilter()
    database = CommitDatabase(
        repo=repo_name, ref=ref, built_at=datetime.now(UTC).isoformat()
    )

    points = clone.first_parent_points(since=since, until=until, ref=ref, max_points=max_points)
    logger.info("%s: %d integration points on %s", repo_name, len(points), ref)

    measured = 0
    for index, point in enumerate(points):
        if progress is not None:
            progress(index, len(points))

        unresolved_provenance = point.style not in INTEGRATION_STYLES
        if not point.base_sha:
            # A root commit, or the graft boundary of a truncated clone.
            # Nothing beneath it to diff against or check out.
            database.drop(Rejection.BASE_BEFORE_GRAFT)
            continue

        stats = clone.diff_stats(point.base_sha, point.sha)
        measured += 1
        if stats is None or not stats.files:
            database.drop(Rejection.DIFF_UNAVAILABLE)
            continue

        # Size and shape still apply to unresolved commits: a docs-only
        # direct push is not worth enriching either.
        rejection = filters.rejection_for(point, stats, skip_integration_check=True)
        if rejection:
            database.drop(rejection)
            continue

        record = CommitRecord(
            sha=point.sha,
            base_sha=point.base_sha,
            subject=point.subject,
            style=point.style,
            style_evidence=point.style_evidence,
            pr_number=point.pr_number,
            closes_issues=tuple(closed_issue_numbers(clone.commit_message(point.sha))),
            author_date=point.author_date,
            committer_date=point.committer_date,
            patch=stats,
        )
        if unresolved_provenance and filters.require_integration_point:
            database.needs_enrichment.append(record)
        else:
            database.records.append(record)

    database.detail.update(
        mainline_points=len(points),
        commits_measured=measured,
        needs_enrichment=len(database.needs_enrichment),
        walk_since=since.isoformat() if since else None,
        walk_until=until.isoformat() if until else None,
    )
    return database
