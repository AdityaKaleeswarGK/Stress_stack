"""History mining: a repository's own issue/PR record as a task source.

Phase A — discovery and selection only. This package decides *which*
(issue, PR, base commit) triples are worth building a task from and proves
the linkage; turning one into a runnable Harbor task is the next phase.

Read `discover.py` first — the gate ordering there is the design.
"""

from __future__ import annotations

from fleet.history.discover import (
    MainlineIndex,
    SelectionPolicy,
    discover_via_api,
    discover_via_git,
)
from fleet.history.models import (
    SCHEMA_VERSION,
    Candidate,
    DiscoveryReport,
    Issue,
    LinkEvidence,
    MergedPR,
    MergePoint,
    MergeStyle,
    Rejection,
)
from fleet.history.window import GitRepo, parse_since

__all__ = [
    "SCHEMA_VERSION",
    "Candidate",
    "DiscoveryReport",
    "GitRepo",
    "Issue",
    "LinkEvidence",
    "MainlineIndex",
    "MergePoint",
    "MergeStyle",
    "MergedPR",
    "Rejection",
    "SelectionPolicy",
    "discover_via_api",
    "discover_via_git",
    "parse_since",
]
