"""Enforce the Pipeline contract for every registered entry — mirrors
Repo2RLEnv's tests/test_pipeline_contract.py in spirit, not in code.

Currently vacuous (PIPELINES is empty); starts failing usefully the moment a
real pipeline is registered without a matching `name` or a working `run`.
"""

from __future__ import annotations

from fleet.pipelines import PIPELINES


def test_every_pipeline_satisfies_the_protocol() -> None:
    for pipeline_name, cls in PIPELINES.items():
        assert isinstance(cls.name, str) and cls.name == pipeline_name
        assert callable(cls.run)
