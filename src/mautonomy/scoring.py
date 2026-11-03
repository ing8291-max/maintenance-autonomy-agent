"""Scoring — ADR 0012.

Four axes from ADR 0005, computed over a list of :class:`ScoredRun`:

* ``cost``: weighted sum over the 25-cell cost matrix. Uses *executed category*
  (A2 effect applied to A1), not raw A1.
* ``ontime``: fraction of ``pair_role == "act"`` scenarios where the executed
  category is an "action" (``SCHEDULE_PM`` / ``REQUEST_STOP`` / ``ASK_HUMAN``)
  AND ``as_of`` falls in the 적시 window ``[anomaly_onset, point_of_no_return]``.
* ``pair_accuracy``: fraction of ``pair_id`` groups where every member has
  ``a1_action == ground_truth.action``.
* ``ask_f1``: precision/recall/F1 for the "ask the human" decision; ask-needed
  is ``ground_truth.action == "ESCALATE"``, asked is ``a1_action == "ESCALATE"``
  (gate verdicts are deliberately not counted — see ADR 0012).

Weights and the cost matrix are read from ``config/cost.yaml``. Baselines
(B1~B4) call the same functions with ``a2_verdict="ALLOW"`` so the four
axes stay directly comparable (ADR 0005).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from mautonomy.scenarios.schema import Scenario


EXECUTED_CATEGORIES: tuple[str, ...] = (
    "NOTHING",
    "CHECK_SENSOR",
    "SCHEDULE_PM",
    "REQUEST_STOP",
    "ASK_HUMAN",
)

_TRUTH_ACTIONS: tuple[str, ...] = (
    "NO_ACTION",
    "CHECK_SENSOR",
    "SCHEDULE_PM",
    "ESCALATE",
    "REQUEST_STOP",
)

_ACTION_EXECUTED: tuple[str, ...] = ("SCHEDULE_PM", "REQUEST_STOP", "ASK_HUMAN")

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "cost.yaml"


@dataclass(frozen=True)
class ScoredRun:
    """One scenario's A1/A2 pair, carried alongside the scenario itself."""

    scenario: Scenario
    a1_action: str
    a2_verdict: str  # "ALLOW" | "NEEDS_HUMAN" | "BLOCKED" | "N/A"


@dataclass(frozen=True)
class CostConfig:
    weights: dict[str, dict[str, int]]
    cost_matrix: dict[str, dict[str, str]]
    approver_accuracy: float


def load_config(path: Path | str | None = None) -> CostConfig:
    """Parse ``config/cost.yaml`` into a :class:`CostConfig`."""
    source = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    with source.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return CostConfig(
        weights={k: dict(v) for k, v in raw["weights"].items()},
        cost_matrix={k: dict(v) for k, v in raw["cost_matrix"].items()},
        approver_accuracy=float(raw.get("approver_accuracy", 1.0)),
    )


def executed_of(a1_action: str, a2_verdict: str) -> str:
    """Map (A1, A2) → one of :data:`EXECUTED_CATEGORIES`.

    Rules (ADR 0012):

    * ``A1 == INVALID``                → ``NOTHING``
    * ``A2 == BLOCKED``                → ``NOTHING``
    * ``A2 == NEEDS_HUMAN``            → ``ASK_HUMAN``
    * ``A2 == N/A``                    → ``NOTHING``
    * else (``A2 == ALLOW``):
        * ``NO_ACTION``                → ``NOTHING``
        * ``ESCALATE``                 → ``ASK_HUMAN``
        * otherwise                    → the A1 action itself.
    """
    if a1_action == "INVALID":
        return "NOTHING"
    if a2_verdict == "BLOCKED":
        return "NOTHING"
    if a2_verdict == "NEEDS_HUMAN":
        return "ASK_HUMAN"
    if a2_verdict == "N/A":
        return "NOTHING"
    # A2 == "ALLOW"
    if a1_action == "NO_ACTION":
        return "NOTHING"
    if a1_action == "ESCALATE":
        return "ASK_HUMAN"
    return a1_action


def cost_of(
    truth_action: str,
    executed: str,
    *,
    config: CostConfig,
    weight_set: str = "default",
) -> int:
    """Look up the bucket from the 25-cell matrix and resolve its weight."""
    bucket = config.cost_matrix[truth_action][executed]
    return int(config.weights[weight_set][bucket])


def score_cost(
    runs: Iterable[ScoredRun],
    *,
    config: CostConfig,
    weight_set: str = "default",
) -> dict[str, float | int]:
    """Sum the per-run cost and return totals + bucket counts."""
    total = 0
    bucket_counts: dict[str, int] = {}
    n = 0
    for run in runs:
        n += 1
        truth = run.scenario.ground_truth.action
        executed = executed_of(run.a1_action, run.a2_verdict)
        bucket = config.cost_matrix[truth][executed]
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        total += int(config.weights[weight_set][bucket])
    return {
        "weight_set": weight_set,
        "n": n,
        "total": total,
        "mean": (total / n) if n else 0.0,
        "buckets": bucket_counts,
    }


def score_ontime(runs: Iterable[ScoredRun]) -> float | None:
    """적시 비율 across ``pair_role == "act"`` scenarios.

    Returns ``None`` when the denominator is zero (no act-role scenarios).
    """
    act_runs = [r for r in runs if r.scenario.pair_role == "act"]
    if not act_runs:
        return None
    hit = 0
    for run in act_runs:
        executed = executed_of(run.a1_action, run.a2_verdict)
        if executed not in _ACTION_EXECUTED:
            continue
        ts = run.scenario.timestamps
        if ts is None:
            continue
        if ts.anomaly_onset <= run.scenario.as_of <= ts.point_of_no_return:
            hit += 1
    return hit / len(act_runs)


def score_pair_accuracy(runs: Iterable[ScoredRun]) -> float:
    """Fraction of ``pair_id`` groups where every member has ``A1 == truth``."""
    groups: dict[str, list[ScoredRun]] = {}
    for run in runs:
        pid = run.scenario.pair_id
        if pid is None:
            continue
        groups.setdefault(pid, []).append(run)
    if not groups:
        return 0.0
    matched = sum(
        1
        for members in groups.values()
        if all(r.a1_action == r.scenario.ground_truth.action for r in members)
    )
    return matched / len(groups)


def score_ask_f1(runs: Iterable[ScoredRun]) -> dict[str, float]:
    """Precision / recall / F1 for the "ask the human" decision (A1-based)."""
    asked_needed = 0
    asked = 0
    needed = 0
    for run in runs:
        is_asked = run.a1_action == "ESCALATE"
        is_needed = run.scenario.ground_truth.action == "ESCALATE"
        if is_asked:
            asked += 1
        if is_needed:
            needed += 1
        if is_asked and is_needed:
            asked_needed += 1
    precision = (asked_needed / asked) if asked else 0.0
    recall = (asked_needed / needed) if needed else 0.0
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def score_all(
    runs: Sequence[ScoredRun],
    *,
    config: CostConfig | None = None,
    weight_set: str = "default",
) -> dict:
    """Compute all four axes in one pass."""
    cfg = config if config is not None else load_config()
    return {
        "cost": score_cost(runs, config=cfg, weight_set=weight_set),
        "ontime": score_ontime(runs),
        "pair_accuracy": score_pair_accuracy(runs),
        "ask_f1": score_ask_f1(runs),
    }


def baseline_run(scenario: Scenario, action: str) -> ScoredRun:
    """Wrap a baseline prediction as a :class:`ScoredRun`.

    Baselines have no gate, so A2 is fixed to ``"ALLOW"`` (ADR 0005).
    """
    return ScoredRun(scenario=scenario, a1_action=action, a2_verdict="ALLOW")
