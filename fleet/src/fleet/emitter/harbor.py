"""Write a Task to a Harbor-shaped `task.toml` directory.

Format reference: Repo2RLEnv's docs/reference/SPEC.md
(../../../../deep_research/implementations/Repo2RLEnv/docs/reference/SPEC.md)
and, upstream of that, https://github.com/harbor-framework/harbor. Not
implemented yet — this is a placeholder for the shape decided in
../../../deep_research/notes/decisions.md: `task.toml` (+ `[metadata.fleet]`
provenance) / `instruction.md` / `solution/patch.diff` /
`environment/Dockerfile` / `tests/`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class HarborTask:
    task_id: str
    instruction: str
    patch: str
    reward_kinds: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def write_task(task: HarborTask, out_dir: Path) -> Path:
    raise NotImplementedError("emitter lands once the first real pipeline needs it")
