"""B1 — always ESCALATE. No confidence to report."""
from __future__ import annotations

from datetime import datetime

from mautonomy.baselines.base import BaselineOutput
from mautonomy.data.read_tools import read_telemetry


def predict(
    machine_id: int, as_of: datetime, *, allow_eval: bool = False
) -> BaselineOutput:
    # Call a read tool so the eval-guard path still runs — this is a fair
    # comparison harness, not a lookup table that ignores machine_id.
    read_telemetry(machine_id, as_of, allow_eval=allow_eval)
    return BaselineOutput(action="ESCALATE", confidence=None)
