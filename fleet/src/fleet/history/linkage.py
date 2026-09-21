"""Issue → merged-PR linkage, read out of GitHub's own timeline.

This is the module the whole pipeline turns on. The question "which PR
actually fixed this issue?" has a *recorded* answer — GitHub writes a
timeline event when a PR references an issue — and a *guessable* one:
regex `Fixes #\\d+` out of PR bodies. Repo2RLEnv guesses
(pipelines/pr_runtime.py:_linked_issue_number). Guessing is wrong in both
directions, and a live probe of `pallets/click#3822` shows both failures on
one issue:

    xref  #3835  pr=True  merged_at=None        <- claims the fix, never merged
    xref  #3858  pr=True  merged_at=2026-09-08  <- the actual fix
    connected                                   <- linked via UI, no keyword anywhere

A body regex over merged PRs would miss #3858 if its body carried no
keyword (the `connected` event says the link was made in the UI sidebar),
and a body regex over *all* PRs would happily accept #3835, whose "fix"
never landed.

What REST cannot do: a `connected` event's target number is absent from the
REST payload (GraphQL's `ConnectedEvent.subject` has it). We count those
events so a run can report how much linkage a token would recover instead
of silently under-yielding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fleet.history.models import Issue, LinkConfidence, LinkEvidence, MergedPR, Rejection

logger = logging.getLogger(__name__)

# A PR merged *after* an issue closed did not close it — but issues are
# often closed by hand a little late, and merge/close timestamps can differ
# by a few seconds through automation. One day absorbs both.
_CLOSE_GRACE = timedelta(days=1)
# Two merged PRs near an issue's close time: accept the nearer one only if
# the runner-up is clearly further away. Otherwise the oracle is ambiguous
# and the candidate is not worth the validation spend.
_AMBIGUITY_MARGIN = timedelta(days=7)

# `Fixes #12`, `closed #12`, `resolve #12` — the weak, text-derived link
# used only on the git-local path, where a commit subject is all we have.
CLOSING_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b",
    re.IGNORECASE,
)
# GitHub's squash/merge subject suffix: `Make Path generic (#3858)`.
PR_NUMBER_SUFFIX_RE = re.compile(r"\(#(\d+)\)\s*$")
PR_MERGE_SUBJECT_RE = re.compile(r"^Merge pull request #(\d+)\b")


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse an ISO 8601 GitHub timestamp, tolerating a trailing `Z`."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        logger.debug("unparseable timestamp: %r", value)
        return None


@dataclass(slots=True)
class TimelineLinks:
    """Everything a single issue's timeline told us, sorted into buckets."""

    merged_prs: list[MergedPR] = field(default_factory=list)
    # PRs that referenced the issue and were closed unmerged. Kept because
    # their existence is exactly the trap keyword-mining falls into, and
    # counting them proves the timeline path is earning its keep.
    unmerged_pr_numbers: list[int] = field(default_factory=list)
    open_pr_numbers: list[int] = field(default_factory=list)
    # A fix that lives in another repository can't become a task here.
    cross_repo_pr_numbers: list[int] = field(default_factory=list)
    closed_by_commit: str | None = None
    referenced_commits: list[str] = field(default_factory=list)
    # `connected` events whose target REST refuses to name.
    opaque_connected: int = 0


def parse_timeline(events: list[dict[str, Any]], *, repo: str) -> TimelineLinks:
    """Sort one issue's timeline events into linkage buckets.

    Only same-repo pull requests are collected as fixes: a cross-referencing
    PR in another repository may well be the real fix, but we cannot check
    out this repo at that PR's base commit, so it is recorded and dropped.
    """
    links = TimelineLinks()
    for event in events:
        kind = event.get("event")

        if kind == "cross-referenced":
            source_issue = (event.get("source") or {}).get("issue") or {}
            pull_request = source_issue.get("pull_request")
            if not pull_request:
                continue  # a plain issue mentioning this one, not a fix
            number = source_issue.get("number")
            if not isinstance(number, int):
                continue
            source_repo = ((source_issue.get("repository") or {}).get("full_name") or "").lower()
            if source_repo and source_repo != repo.lower():
                links.cross_repo_pr_numbers.append(number)
                continue
            merged_at = pull_request.get("merged_at")
            if merged_at:
                links.merged_prs.append(
                    MergedPR(
                        number=number,
                        merged_at=merged_at,
                        url=source_issue.get("html_url") or "",
                        title=source_issue.get("title") or "",
                    )
                )
            elif source_issue.get("state") == "open":
                links.open_pr_numbers.append(number)
            else:
                links.unmerged_pr_numbers.append(number)

        elif kind == "closed":
            # Non-null only when a commit message closed the issue — the
            # squash-merge case, where there may be no cross-reference at all.
            if event.get("commit_id"):
                links.closed_by_commit = event["commit_id"]

        elif kind == "referenced":
            if event.get("commit_id"):
                links.referenced_commits.append(event["commit_id"])

        elif kind == "connected":
            links.opaque_connected += 1

    return links


@dataclass(frozen=True, slots=True)
class LinkResolution:
    """The outcome of asking "which PR closed this issue?", with provenance.

    Deliberately not a `PR | Rejection` union. "We don't know yet" and "the
    evidence disagrees" are different from "there is no fix here", and
    collapsing them loses candidates that a later enrichment pass could
    resolve. `confidence` says what to do; `notes` says why.
    """

    pr: MergedPR | None
    evidence: LinkEvidence
    confidence: LinkConfidence
    # Every merged PR that referenced the issue, chosen one included. Kept so
    # an ambiguous case can be re-judged without re-fetching the timeline.
    competing: tuple[int, ...] = ()
    # A commit that closed the issue but hasn't been resolved to a PR yet.
    # Resolving it needs GET /repos/{o}/{r}/commits/{sha}/pulls.
    unresolved_closing_commit: str = ""
    notes: str = ""

    @property
    def usable(self) -> bool:
        """Whether this linkage is strong enough to build a task from.

        A mention is never usable on its own: someone typing `#123` in a PR
        description is not a statement that the PR fixes it.
        """
        return (
            self.pr is not None
            and self.confidence is LinkConfidence.RESOLVED
            and self.evidence is not LinkEvidence.MENTION_ONLY
        )


def resolve_fix_pr(issue: Issue, links: TimelineLinks) -> LinkResolution:
    """Work out which PR closed `issue`, and how strongly we know it.

    What this deliberately does *not* do is treat time proximity as
    closure. An earlier version picked the merged PR nearest the issue's
    close timestamp and called that the fix — so an unrelated PR that
    merely mentioned the issue and happened to merge a minute before a
    maintainer closed it by hand would win, and be recorded with the same
    confidence as a real closure. Proximity is now a tie-breaker among
    already-weak candidates, and the result is labelled `MENTION_ONLY`,
    which `usable` refuses.

    The other two fixes here:

    * A closing commit no longer upgrades an unrelated PR's evidence. It
      used to be enough for `closed_by_commit` to be non-empty — the commit
      was never checked against the selected PR. It is now carried as
      `unresolved_closing_commit` for the enrichment pass that can map a SHA
      to its PR (`/commits/{sha}/pulls`).
    * A closing commit with no cross-reference at all is no longer thrown
      away as "no merged PR". That is precisely the case where the API can
      still find the PR, so it returns UNKNOWN with the SHA attached.
    """
    closed_at = parse_timestamp(issue.closed_at)

    if not links.merged_prs:
        if links.closed_by_commit:
            return LinkResolution(
                pr=None,
                evidence=LinkEvidence.CLOSING_REFERENCE,
                confidence=LinkConfidence.UNKNOWN,
                unresolved_closing_commit=links.closed_by_commit or "",
                notes="a commit closed the issue; resolve it to a PR via /commits/{sha}/pulls",
            )
        if links.opaque_connected:
            return LinkResolution(
                pr=None,
                evidence=LinkEvidence.MENTION_ONLY,
                confidence=LinkConfidence.UNKNOWN,
                notes=f"{links.opaque_connected} UI-made link(s) whose target REST will not name; "
                "GraphQL ConnectedEvent.subject would",
            )
        return LinkResolution(
            pr=None,
            evidence=LinkEvidence.MENTION_ONLY,
            confidence=LinkConfidence.UNKNOWN,
            notes="no merged PR referenced this issue",
        )

    competing = tuple(pull_request.number for pull_request in links.merged_prs)

    # Candidates that could plausibly have closed it: merged at or before the
    # close, plus a grace window for automation lag and late manual closes.
    plausible: list[tuple[timedelta, MergedPR]] = []
    for pull_request in links.merged_prs:
        merged_at = parse_timestamp(pull_request.merged_at)
        if merged_at is None or closed_at is None:
            continue
        if merged_at > closed_at + _CLOSE_GRACE:
            continue  # merged after the close — it did not cause it
        plausible.append((abs(closed_at - merged_at), pull_request))

    if closed_at is None:
        # No close timestamp to reason with at all.
        if len(links.merged_prs) == 1:
            return LinkResolution(
                pr=links.merged_prs[0],
                evidence=LinkEvidence.MENTION_ONLY,
                confidence=LinkConfidence.UNKNOWN,
                competing=competing,
                notes="single merged cross-reference, no close timestamp to corroborate it",
            )
        return LinkResolution(
            pr=None,
            evidence=LinkEvidence.MENTION_ONLY,
            confidence=LinkConfidence.NEEDS_REVIEW,
            competing=competing,
            notes="several merged cross-references and no close timestamp",
        )

    if not plausible:
        return LinkResolution(
            pr=None,
            evidence=LinkEvidence.MENTION_ONLY,
            confidence=LinkConfidence.UNKNOWN,
            competing=competing,
            unresolved_closing_commit=links.closed_by_commit or "",
            notes="every merged cross-reference landed after the issue closed",
        )

    plausible.sort(key=lambda pair: pair[0])
    if len(plausible) > 1 and (plausible[1][0] - plausible[0][0]) < _AMBIGUITY_MARGIN:
        return LinkResolution(
            pr=None,
            evidence=LinkEvidence.MENTION_ONLY,
            confidence=LinkConfidence.NEEDS_REVIEW,
            competing=competing,
            unresolved_closing_commit=links.closed_by_commit or "",
            notes="two or more merged PRs landed close to the issue's closure; "
            "which one closed it is not decidable from timestamps",
        )

    # One plausible candidate. Still only a mention — the timeline recorded a
    # cross-reference, not a closure — so it is not usable until an
    # enrichment pass upgrades it with GraphQL closingIssuesReferences or a
    # resolved closing commit.
    return LinkResolution(
        pr=plausible[0][1],
        evidence=LinkEvidence.MENTION_ONLY,
        confidence=LinkConfidence.UNKNOWN,
        competing=competing,
        unresolved_closing_commit=links.closed_by_commit or "",
        notes="sole merged cross-reference near the close; needs recorded-closure "
        "confirmation before use",
    )


def rejection_for(resolution: LinkResolution) -> Rejection | None:
    """Map a resolution onto a report code, or None when it is usable.

    Kept apart from `resolve_fix_pr` so the resolver stays a statement about
    evidence and the caller decides what counts as a rejection.
    """
    if resolution.usable:
        return None
    if resolution.confidence is LinkConfidence.NEEDS_REVIEW:
        return Rejection.AMBIGUOUS_MULTI_PR
    if resolution.pr is None and not resolution.unresolved_closing_commit:
        return Rejection.NO_MERGED_PR
    return Rejection.LINK_UNCONFIRMED


def pr_number_from_subject(subject: str) -> int | None:
    """`Make Path generic (#3858)` -> 3858.

    GitHub appends this suffix to squash and merge commit subjects, which
    makes a PR number recoverable from a clone with no API access at all.
    """
    match = PR_MERGE_SUBJECT_RE.search(subject.strip()) or PR_NUMBER_SUFFIX_RE.search(subject.strip())
    return int(match.group(1)) if match else None


def closed_issue_numbers(text: str) -> list[int]:
    """Issue numbers a commit message or PR body claims to close.

    The weak signal — used on the git-local path when no token is available.
    `#N` here is a *claim*, not GitHub's recorded linkage: on the API path
    `parse_timeline` supersedes it.
    """
    seen: list[int] = []
    for match in CLOSING_KEYWORD_RE.finditer(text or ""):
        number = int(match.group(1))
        if number not in seen:
            seen.append(number)
    return seen
