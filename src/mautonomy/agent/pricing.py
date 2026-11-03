"""Best-effort token → USD conversion.

Rates are tracked by model name. A rate of ``None`` means pricing is
unknown and :func:`estimate_cost_usd` returns ``None`` so a run can be
reported honestly as "cost: unknown" rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rate:
    """USD per 1M tokens, broken out by direction."""

    input_per_million: float
    output_per_million: float
    source: str


# Muse Spark 1.1 — rates taken from the hackathon briefing; re-confirm in
# the vendor console before publishing numbers externally.
_RATES: dict[str, Rate | None] = {
    "muse-spark-1.1": Rate(
        input_per_million=1.25,
        output_per_million=4.25,
        source="해커톤 안내 기준, 콘솔에서 재확인 필요",
    ),
}


def get_rate(model: str) -> Rate | None:
    return _RATES.get(model)


def estimate_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> float | None:
    rate = _RATES.get(model)
    if rate is None:
        return None
    return (
        input_tokens * rate.input_per_million
        + output_tokens * rate.output_per_million
    ) / 1_000_000
