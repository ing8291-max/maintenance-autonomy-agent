"""Shared output type for all baselines (ADR 0005)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BaselineOutput:
    """Baseline prediction shape matching the agent's output contract.

    action: one of the five ActionType values.
    confidence: calibrated probability in [0, 1], or None when the baseline
        has no probability to report. Null-confidence rows must be excluded
        from ECE/Brier computation.
    """
    action: str
    confidence: Optional[float] = None
