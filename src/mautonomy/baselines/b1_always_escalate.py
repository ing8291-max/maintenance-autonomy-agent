"""B1 — always ESCALATE. No confidence to report."""
from __future__ import annotations

from mautonomy.baselines.base import BaselineOutput
from mautonomy.harness import ScenarioEnvironment


def predict(env: ScenarioEnvironment) -> BaselineOutput:
    # Touch a tool so the eval-guard path exercises consistently with other
    # baselines — this is a fair comparison harness, not a lookup table.
    env.read_telemetry()
    return BaselineOutput(action="ESCALATE", confidence=None)
