"""The shapes history mining produces, independent of where they came from.

Nothing in here talks to GitHub or to git — the API path (`client.py` +
`linkage.py`) and the git-local path (`window.py`) both build these same
types, so the selection driver in `discover.py` never branches on which
source found a candidate.

Design note: a candidate is an (issue, merged PR, merge point) triple, not a
PR. Repo2RLEnv's `pr_runtime` finds the issue by regexing `Fixes #N` out of
merged PR bodies (`_linked_issue_number`), which misses issues linked
through GitHub's UI — the link is in GitHub's database and no closing
keyword exists in any body.

A correction to what this note used to say: it claimed that approach
"accepts PRs that claim a fix and were closed unmerged". It does not.
`pr_runtime` lists `--state merged`, so unmerged PRs never enter its pool.
The keyword regex is a recall problem, not a precision one. (Its
`require_linked_issue` option is separately dead — declared in
`spec/options.py` and read nowhere.)

See ../../../../deep_research/notes/decisions.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from fleet.history.patch import PatchStats, difficulty_bucket

# 0.2.0 adds per-candidate patch stats, a difficulty bucket, and the
# itemised `rejected` trail alongside the rejection counts.
SCHEMA_VERSION = "0.2.0"


class MergeStyle(StrEnum):
    """How a pull request landed on the mainline.

    This is not trivia — it decides the *true base commit* the patch applies
    against, and whether the PR's own commits exist in a shallow clone at
    all. See `merge_style.classify`.
    """

    MERGE_COMMIT = "merge_commit"  # 2 parents; head reachable; base = parents[0]
    SQUASH = "squash"  # 1 parent, rewritten; head unreachable
    REBASE = "rebase"  # 1+ parents, replayed with new SHAs
    FAST_FORWARD = "fast_forward"  # head itself became the mainline tip
    UNKNOWN = "unknown"  # no merge point found in the window


class LinkEvidence(StrEnum):
    """What kind of evidence links an issue to a PR, strongest first.

    None of these is proof that the PR's patch solves the described
    behaviour — they are provenance, and even `RECORDED_CLOSURE` only says
    GitHub recorded the closure. Acceptance comes from execution, not here.

    The important distinction is the bottom one. A cross-reference is a
    *mention*: someone typed `#123` somewhere. Treating a mention as a
    closure is the mistake this enum exists to prevent.
    """

    # GitHub recorded the closure itself: a ClosedEvent whose closer is this
    # PR, or the PR's own closingIssuesReferences.
    RECORDED_CLOSURE = "recorded_closure"
    # A commit closed the issue and that commit resolves to this PR.
    CLOSING_REFERENCE = "closing_reference"
    # Text says so — `Fixes #N` in a PR body or commit message. A claim.
    KEYWORD_CLAIM = "keyword_claim"
    # The PR merely referenced the issue. Not a closure.
    MENTION_ONLY = "mention_only"


class LinkConfidence(StrEnum):
    """Whether the linkage is usable, needs a human, or isn't known yet.

    Kept separate from `LinkEvidence` because they answer different
    questions: evidence is *what we found*, confidence is *what to do with
    it*. Missing evidence is `UNKNOWN` (retry with more data), conflicting
    evidence is `NEEDS_REVIEW` (a person decides) — neither is a rejection.
    """

    RESOLVED = "resolved"
    NEEDS_REVIEW = "needs_review"
    UNKNOWN = "unknown"


class Rejection(StrEnum):
    """Every reason a candidate can be dropped.

    Exhaustive and named so a run reports *why* a repo yielded little —
    the difference between "this repo doesn't link issues to PRs" and "our
    window was too tight" is the whole diagnostic signal, and a bare
    `skipped: 47` hides it.
    """

    # --- window / shape ---
    OUTSIDE_WINDOW = "outside_window"
    IS_PULL_REQUEST = "is_pull_request"
    # --- issue quality ---
    NOT_COMPLETED = "not_completed"
    ISSUE_BODY_TOO_THIN = "issue_body_too_thin"
    BOT_AUTHORED = "bot_authored"
    LABEL_EXCLUDED = "label_excluded"
    # --- linkage ---
    NO_MERGED_PR = "no_merged_pr"
    AMBIGUOUS_MULTI_PR = "ambiguous_multi_pr"
    # Evidence exists but is only a mention, or points at a commit we have
    # not yet resolved to a PR. Deferred pending enrichment, not rejected on
    # quality — the distinction matters because these are recoverable.
    LINK_UNCONFIRMED = "link_unconfirmed"
    PR_OUTSIDE_WINDOW = "pr_outside_window"
    TIMELINE_FETCH_FAILED = "timeline_fetch_failed"
    # --- merge point / base commit ---
    MERGE_POINT_NOT_FOUND = "merge_point_not_found"
    BASE_BEFORE_GRAFT = "base_before_graft"
    # --- patch shape ---
    DIFF_UNAVAILABLE = "diff_unavailable"
    DIFF_TOO_SMALL = "diff_too_small"
    DIFF_TOO_LARGE = "diff_too_large"
    TOO_MANY_SOURCE_FILES = "too_many_source_files"
    NO_SOURCE_CHANGE = "no_source_change"
    NO_EXECUTABLE_SOURCE_CHANGE = "no_executable_source_change"
    VERSION_ONLY_SOURCE_CHANGE = "version_only_source_change"
    NO_TEST_CHANGE = "no_test_change"
    # --- not an integration point ---
    NOT_AN_INTEGRATION_POINT = "not_an_integration_point"
    # --- budget, not quality ---
    OVER_BUDGET = "over_budget"


@dataclass(frozen=True, slots=True)
class Issue:
    """A closed issue — the candidate's problem statement.

    An earlier version of this docstring claimed an issue body "physically
    cannot" name the fix, on the theory that it was written before the fix
    existed. That is wrong twice over: bodies are **editable**, so a body
    can be updated after the fix lands, and a reporter can propose the fix
    in the original text. Issue text is *less* leak-prone than a PR body,
    not leak-free.

    So the timestamps are recorded rather than reasoned about.
    `updated_at` later than `created_at` means the text was edited at some
    point and cannot be assumed pre-fix; leakage is something to check, not
    to infer from chronology.
    """

    number: int
    title: str
    body: str
    closed_at: str
    state_reason: str
    url: str
    author: str = ""
    author_is_bot: bool = False
    labels: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    @property
    def body_words(self) -> int:
        return len(self.body.split())

    @property
    def was_edited(self) -> bool:
        """Whether the text changed after it was filed.

        Only a hint: GitHub bumps `updated_at` for comments and label
        changes too, so this over-reports. It flags text that *may* not be
        the original report, and nothing more.
        """
        return bool(self.created_at and self.updated_at and self.updated_at > self.created_at)


@dataclass(frozen=True, slots=True)
class MergedPR:
    number: int
    merged_at: str
    url: str
    title: str = ""


@dataclass(frozen=True, slots=True)
class MergePoint:
    """One integration point on the mainline, from a `--first-parent` walk.

    `base_sha` is `parents[0]` — the mainline commit the work landed on top
    of. This is the commit a patch actually applies against, and it is *not*
    the same as the REST API's `pull_request.base.sha`, which is the base
    branch tip at last sync and goes stale the moment the base advances.
    """

    sha: str
    subject: str
    parents: tuple[str, ...]
    author_date: str = ""
    committer_date: str = ""
    pr_number: int | None = None
    # Set by `merge_style.classify` once the point exists — the classifier
    # reads the point's own fields, so it can't run before construction.
    style: MergeStyle = MergeStyle.UNKNOWN
    style_evidence: str = ""

    @property
    def base_sha(self) -> str:
        return self.parents[0] if self.parents else ""

    @property
    def dates_diverge(self) -> bool:
        """Author and committer timestamps differ — a *hint* of replay.

        Deliberately not used as proof of a rebase: cherry-picks, `commit
        --amend`, and patch-file application all produce the same divergence.
        See `merge_style` for what is actually load-bearing.
        """
        return bool(self.author_date and self.committer_date and self.author_date != self.committer_date)


@dataclass(frozen=True, slots=True)
class Candidate:
    """An accepted (issue, PR, merge point) triple, ready for validation.

    Everything a downstream phase needs to build a task: the leak-free
    problem statement (`issue`), the oracle's identity (`pr`), and the exact
    commit to check out (`merge.base_sha`).
    """

    repo: str
    issue: Issue
    pr: MergedPR
    evidence: LinkEvidence
    merge: MergePoint | None = None
    # Per-file shape of the fix, read from the clone. None when no clone was
    # attached (the API-only path) — the size gates are skipped rather than
    # guessed at in that case.
    patch: PatchStats | None = None

    @property
    def task_id(self) -> str:
        return f"{self.repo.replace('/', '__')}-{self.issue.number}"

    @property
    def base_sha(self) -> str:
        return self.merge.base_sha if self.merge else ""

    @property
    def merge_style(self) -> MergeStyle:
        return self.merge.style if self.merge else MergeStyle.UNKNOWN

    @property
    def difficulty(self) -> str:
        return difficulty_bucket(self.patch) if self.patch else "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repo": self.repo,
            "issue": asdict(self.issue),
            "pr": asdict(self.pr),
            "evidence": str(self.evidence),
            "base_sha": self.base_sha,
            "merge_style": str(self.merge_style),
            "difficulty": self.difficulty,
            "merge": (
                {**asdict(self.merge), "style": str(self.merge.style), "base_sha": self.merge.base_sha}
                if self.merge
                else None
            ),
            "patch": self.patch.to_dict() if self.patch else None,
        }


@dataclass
class DiscoveryReport:
    """What one mining run found, and what it threw away and why.

    Rejections are counted per named reason, so a thin harvest says *which*
    problem the repo has: `no_merged_pr: 40` means "this repo doesn't link
    issues to PRs", `diff_too_small: 40` means "the size floor bit". A bare
    `skipped: 40` would say neither.
    """

    repo: str
    since: str
    until: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    # Free-form per-run facts: issues scanned, API calls spent, clone depth.
    detail: dict[str, Any] = field(default_factory=dict)

    def reject(self, reason: Rejection, count: int = 1) -> None:
        key = str(reason)
        self.rejections[key] = self.rejections.get(key, 0) + count

    @property
    def rejected_total(self) -> int:
        return sum(self.rejections.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "repo": self.repo,
            "window": {"since": self.since, "until": self.until},
            "summary": {
                "accepted": len(self.candidates),
                "rejected": self.rejected_total,
                **self.detail,
            },
            "rejections": dict(sorted(self.rejections.items(), key=lambda kv: -kv[1])),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }
