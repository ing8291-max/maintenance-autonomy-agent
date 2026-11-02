"""B2 — always SCHEDULE_PM. No confidence to report."""
from __future__ import annotations

from datetime import datetime

from mautonomy.baselines.base import BaselineOutput
from mautonomy.data.read_tools import read_telemetry


def predict(
    machine_id: int, as_of: datetime, *, allow_eval: bool = False
) -> BaselineOutput:
    read_telemetry(machine_id, as_of, allow_eval=allow_eval)
    return BaselineOutput(action="SCHEDULE_PM", confidence=None)
