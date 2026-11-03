"""Best-effort token → USD conversion.

Public Muse Spark pricing is not published, so the table starts with a
placeholder entry we treat as "unknown" and :func:`estimate_cost_usd`
returns ``None`` when the model isn't in the table. That keeps the smoke
script honest: it reports a number only when it can prove the number.
"""
from __future__ import annotations

# USD per 1M tokens, (input, output). ``None`` means "pricing unknown".
_RATES: dict[str, tuple[float, float] | None] = {
    "muse-spark-1.1": None,
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    rate = _RATES.get(model)
    if rate is None:
        return None
    in_rate, out_rate = rate
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
