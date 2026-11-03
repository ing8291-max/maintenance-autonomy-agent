"""Run the five dev-split practice scenarios once each with rep=1.

Uses the real :class:`MuseSparkAdapter`. Appends one JSON line per run
to ``logs/trace.jsonl`` and prints the requested summary table plus a
total cost row.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mautonomy.agent import DEFAULT_CONFIG, MuseSparkAdapter, evaluate_scenario
from mautonomy.scenarios.loader import load_scenario


_SYNTH_DIR = Path(__file__).resolve().parents[1] / "data" / "synth"
_SCENARIO_FILES = sorted(_SYNTH_DIR.glob("*.yaml"))

_COLUMNS = (
    "scenario_id",
    "truth",
    "a1",
    "conf",
    "a2",
    "steps",
    "tools",
    "retries",
    "fmt_err",
    "tokens",
    "cost_usd",
)


def _fmt_cost(c: float | None) -> str:
    return f"${c:.6f}" if c is not None else "unknown"


def _row(result) -> tuple[str, ...]:
    return (
        result.scenario_id,
        result.ground_truth,
        result.a1_action,
        f"{result.a1_confidence:.2f}" if result.a1_confidence is not None else "-",
        result.a2_verdict,
        str(result.steps_used),
        str(result.tool_calls),
        str(result.retries),
        str(result.format_errors),
        str(result.tokens.get("total_tokens", 0)),
        _fmt_cost(result.cost_usd),
    )


def _print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(r[i]) for r in [_COLUMNS, *rows]) for i in range(len(_COLUMNS))]
    sep = "  "
    print(sep.join(h.ljust(w) for h, w in zip(_COLUMNS, widths)))
    print(sep.join("-" * w for w in widths))
    for r in rows:
        print(sep.join(cell.ljust(w) for cell, w in zip(r, widths)))


def main() -> int:
    if not _SCENARIO_FILES:
        print(f"no scenarios found in {_SYNTH_DIR}")
        return 1

    adapter = MuseSparkAdapter()
    rows: list[tuple[str, ...]] = []
    total_cost = 0.0
    cost_unknown = False
    total_tokens = 0

    for path in _SCENARIO_FILES:
        scenario = load_scenario(path)
        result = evaluate_scenario(scenario, adapter, rep=1, config=DEFAULT_CONFIG)
        rows.append(_row(result))
        if result.cost_usd is None:
            cost_unknown = True
        else:
            total_cost += result.cost_usd
        total_tokens += result.tokens.get("total_tokens", 0)

    _print_table(rows)
    print()
    print(f"total tokens: {total_tokens}")
    print(f"total cost:   {_fmt_cost(None if cost_unknown else total_cost)}")
    print(f"scenarios:    {len(rows)}")
    print(f"model:        {DEFAULT_CONFIG.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
