"""Pipeline registry. Each entry implements `Pipeline` (see base.py).

`history` (phase A: candidate discovery) is the first real entry. `excision`
— porting stress_stack's coverage-driven function removal — is next; see
../../../deep_research/notes/candidate-discovery.md for the design fork it
has to resolve first.
"""

from __future__ import annotations

from fleet.pipelines.base import Pipeline
from fleet.pipelines.history import HistoryPipeline

PIPELINES: dict[str, type[Pipeline]] = {
    HistoryPipeline.name: HistoryPipeline,
}
