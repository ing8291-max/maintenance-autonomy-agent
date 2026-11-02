"""Scenario-scoped environment: the only surface the agent and baselines see.

A ``ScenarioEnvironment`` bundles four read-tool closures (telemetry, errors,
maintenance, failures) pre-bound to a scenario's ``machine_id`` and ``as_of``.
Only ``read_telemetry`` applies the scenario's perturbation; the other three
tools return raw records. The scenario object itself (ground truth,
perturbation parameters, pair role, timestamps) is **not** exposed on the
environment, so no downstream consumer can read it.
"""
from mautonomy.harness.environment import (
    ScenarioEnvironment,
    build_environment,
    build_raw_environment,
)

__all__ = [
    "ScenarioEnvironment",
    "build_environment",
    "build_raw_environment",
]
