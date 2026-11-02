"""B2 — always SCHEDULE_PM. No confidence to report."""
from __future__ import annotations

from mautonomy.baselines.base import BaselineOutput
from mautonomy.harness import ScenarioEnvironment


def predict(env: ScenarioEnvironment) -> BaselineOutput:
    env.read_telemetry()
    return BaselineOutput(action="SCHEDULE_PM", confidence=None)
