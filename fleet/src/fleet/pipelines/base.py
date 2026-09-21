"""Pipeline contract every task-generation strategy implements.

Shape independently inspired by Repo2RLEnv's `Pipeline` Protocol
(github.com/huggingface/Repo2RLEnv, Apache-2.0) and by stress_stack's
mine/validate/select/emit sequence (../../../../stress_stack) — no code
copied from either. See ../../../deep_research/notes/decisions.md for why
this replaces stress_stack's single hardcoded pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol


@dataclass(slots=True)
class PipelineResult:
    """What a pipeline run produced, independent of how it produced it."""

    tasks_emitted: int
    tasks_rejected: int
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)


class Pipeline(Protocol):
    """A task-generation strategy: read a repo, emit Harbor-shaped tasks.

    Each implementation owns one source of candidates (a mined PR, a removed
    function, ...) and is responsible for every stress_stack-style gate that
    decides whether a candidate becomes a shipped task — a pipeline may reject
    freely, but must never emit an unvalidated one.
    """

    name: ClassVar[str]

    def __init__(self, repo_root: Path, options: dict[str, Any]) -> None: ...

    def run(self, out_dir: Path) -> PipelineResult:
        """Write task directories under `out_dir` and report the outcome."""
        ...
