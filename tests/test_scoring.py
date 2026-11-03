"""Scoring tests — ADR 0012.

The 25-cell cost matrix is parameterised over all (truth, executed) pairs.
Formula tests cover the three non-cost axes and baseline wiring confirms
B1~B4 can call the same functions with ``a2_verdict="ALLOW"``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from mautonomy.scenarios.schema import GroundTruth, Scenario, Timestamps
from mautonomy.scoring import (
    EXECUTED_CATEGORIES,
    ScoredRun,
    baseline_run,
    cost_of,
    executed_of,
    load_config,
    score_all,
    score_ask_f1,
    score_cost,
    score_ontime,
    score_pair_accuracy,
)


_CONFIG = load_config()
_AS_OF = datetime(2016, 6, 1, 12, 0, 0)
_TRUTH_ACTIONS = ("NO_ACTION", "CHECK_SENSOR", "SCHEDULE_PM", "ESCALATE", "REQUEST_STOP")


def _scenario(
    *,
    truth: str = "SCHEDULE_PM",
    pair_id: str | None = None,
    pair_role: str | None = None,
    timestamps: Timestamps | None = None,
    as_of: datetime = _AS_OF,
    scenario_id: str = "dev-test",
) -> Scenario:
    return Scenario(
        schema_version="1.0",
        scenario_id=scenario_id,
        scenario_type="normal",
        machine_id=1,
        split="dev",
        as_of=as_of,
        seed=0,
        ground_truth=GroundTruth(action=truth, rationale="t"),
        failure_datetime=None,
        failure_component=None,
        timestamps=timestamps,
        perturbation=None,
        pair_id=pair_id,
        pair_role=pair_role,
    )


# ---------- executed_of: covers every A1 × A2 combination -------------------


def test_executed_of_invalid_is_nothing():
    assert executed_of("INVALID", "N/A") == "NOTHING"


@pytest.mark.parametrize(
    "a1",
    ["NO_ACTION", "CHECK_SENSOR", "SCHEDULE_PM", "REQUEST_STOP", "ESCALATE"],
)
def test_executed_of_blocked_is_nothing(a1):
    assert executed_of(a1, "BLOCKED") == "NOTHING"


@pytest.mark.parametrize(
    "a1",
    ["NO_ACTION", "CHECK_SENSOR", "SCHEDULE_PM", "REQUEST_STOP", "ESCALATE"],
)
def test_executed_of_needs_human_is_ask(a1):
    assert executed_of(a1, "NEEDS_HUMAN") == "ASK_HUMAN"


def test_executed_of_allow_direct_mapping():
    assert executed_of("NO_ACTION", "ALLOW") == "NOTHING"
    assert executed_of("CHECK_SENSOR", "ALLOW") == "CHECK_SENSOR"
    assert executed_of("SCHEDULE_PM", "ALLOW") == "SCHEDULE_PM"
    assert executed_of("REQUEST_STOP", "ALLOW") == "REQUEST_STOP"
    assert executed_of("ESCALATE", "ALLOW") == "ASK_HUMAN"


# ---------- 25-cell cost matrix ---------------------------------------------


_EXPECTED_MATRIX: dict[tuple[str, str], str] = {
    # NO_ACTION row
    ("NO_ACTION", "NOTHING"): "correct",
    ("NO_ACTION", "CHECK_SENSOR"): "overescalation",
    ("NO_ACTION", "SCHEDULE_PM"): "overstop",
    ("NO_ACTION", "REQUEST_STOP"): "overstop",
    ("NO_ACTION", "ASK_HUMAN"): "overescalation",
    # CHECK_SENSOR row
    ("CHECK_SENSOR", "NOTHING"): "overstop",
    ("CHECK_SENSOR", "CHECK_SENSOR"): "correct",
    ("CHECK_SENSOR", "SCHEDULE_PM"): "overstop",
    ("CHECK_SENSOR", "REQUEST_STOP"): "overstop",
    ("CHECK_SENSOR", "ASK_HUMAN"): "overescalation",
    # SCHEDULE_PM row
    ("SCHEDULE_PM", "NOTHING"): "missed_failure",
    ("SCHEDULE_PM", "CHECK_SENSOR"): "missed_failure",
    ("SCHEDULE_PM", "SCHEDULE_PM"): "correct",
    ("SCHEDULE_PM", "REQUEST_STOP"): "overstop",
    ("SCHEDULE_PM", "ASK_HUMAN"): "overescalation",
    # ESCALATE row
    ("ESCALATE", "NOTHING"): "missed_failure",
    ("ESCALATE", "CHECK_SENSOR"): "missed_failure",
    ("ESCALATE", "SCHEDULE_PM"): "overstop",
    ("ESCALATE", "REQUEST_STOP"): "overstop",
    ("ESCALATE", "ASK_HUMAN"): "overescalation",
    # REQUEST_STOP row
    ("REQUEST_STOP", "NOTHING"): "missed_failure",
    ("REQUEST_STOP", "CHECK_SENSOR"): "missed_failure",
    ("REQUEST_STOP", "SCHEDULE_PM"): "overstop",
    ("REQUEST_STOP", "REQUEST_STOP"): "correct",
    ("REQUEST_STOP", "ASK_HUMAN"): "overescalation",
}


_BUCKET_TO_WEIGHT = {
    "default": {"missed_failure": 100, "overstop": 10, "overescalation": 1, "correct": 0},
    "low_miss": {"missed_failure": 50, "overstop": 10, "overescalation": 1, "correct": 0},
    "high_miss": {"missed_failure": 200, "overstop": 10, "overescalation": 1, "correct": 0},
}


@pytest.mark.parametrize(
    ("truth", "executed", "bucket"),
    [(t, e, b) for (t, e), b in _EXPECTED_MATRIX.items()],
)
def test_cost_matrix_cell(truth, executed, bucket):
    """Every cell of the 25-cell matrix matches the ADR 0012 declaration."""
    assert _CONFIG.cost_matrix[truth][executed] == bucket


@pytest.mark.parametrize("weight_set", ["default", "low_miss", "high_miss"])
@pytest.mark.parametrize(
    ("truth", "executed", "bucket"),
    [(t, e, b) for (t, e), b in _EXPECTED_MATRIX.items()],
)
def test_cost_of_resolves_weight(truth, executed, bucket, weight_set):
    expected = _BUCKET_TO_WEIGHT[weight_set][bucket]
    assert cost_of(truth, executed, config=_CONFIG, weight_set=weight_set) == expected


def test_cost_matrix_has_all_25_cells():
    cells = {
        (t, e)
        for t in _TRUTH_ACTIONS
        for e in EXECUTED_CATEGORIES
    }
    for t, e in cells:
        assert e in _CONFIG.cost_matrix[t], f"missing ({t}, {e})"


# ---------- score_cost totals ----------------------------------------------


def test_score_cost_sums_weights():
    # correct + overstop(10) + missed_failure(100) = 110 under default.
    runs = [
        ScoredRun(_scenario(truth="NO_ACTION"), "NO_ACTION", "ALLOW"),
        ScoredRun(_scenario(truth="NO_ACTION"), "SCHEDULE_PM", "NEEDS_HUMAN"),  # overescalation(1)
        ScoredRun(_scenario(truth="SCHEDULE_PM"), "NO_ACTION", "ALLOW"),  # missed_failure(100)
    ]
    out = score_cost(runs, config=_CONFIG, weight_set="default")
    assert out["total"] == 0 + 1 + 100
    assert out["n"] == 3
    assert out["buckets"]["correct"] == 1
    assert out["buckets"]["overescalation"] == 1
    assert out["buckets"]["missed_failure"] == 1


def test_score_cost_weight_set_scales_missed_failure():
    runs = [ScoredRun(_scenario(truth="SCHEDULE_PM"), "NO_ACTION", "ALLOW")]
    assert score_cost(runs, config=_CONFIG, weight_set="low_miss")["total"] == 50
    assert score_cost(runs, config=_CONFIG, weight_set="default")["total"] == 100
    assert score_cost(runs, config=_CONFIG, weight_set="high_miss")["total"] == 200


# ---------- 적시 ratio ------------------------------------------------------


def _ts(as_of: datetime, *, offset_hours: int = 0, window_hours: int = 24) -> Timestamps:
    """Window centred so ``as_of + offset_hours`` lands inside / outside it."""
    onset = as_of - timedelta(hours=4)
    pnr = as_of + timedelta(hours=window_hours)
    first = onset + timedelta(hours=1)
    return Timestamps(anomaly_onset=onset, first_observable=first, point_of_no_return=pnr)


def test_ontime_counts_only_act_pairs_with_action_in_window():
    ts_in = _ts(_AS_OF, window_hours=24)
    # as_of outside the window.
    ts_out = Timestamps(
        anomaly_onset=_AS_OF - timedelta(hours=20),
        first_observable=_AS_OF - timedelta(hours=19),
        point_of_no_return=_AS_OF - timedelta(hours=1),
    )
    runs = [
        # act in-window + action → hit
        ScoredRun(_scenario(pair_id="p1", pair_role="act", timestamps=ts_in), "SCHEDULE_PM", "ALLOW"),
        # act in-window but CHECK_SENSOR is NOT a 조치 → miss
        ScoredRun(_scenario(pair_id="p2", pair_role="act", timestamps=ts_in), "CHECK_SENSOR", "ALLOW"),
        # act in-window + ESCALATE routed to human (ASK_HUMAN counts as 조치)
        ScoredRun(_scenario(pair_id="p3", pair_role="act", timestamps=ts_in), "ESCALATE", "NEEDS_HUMAN"),
        # act out-of-window → miss
        ScoredRun(_scenario(pair_id="p4", pair_role="act", timestamps=ts_out), "SCHEDULE_PM", "ALLOW"),
        # abstain pair → not in denominator
        ScoredRun(_scenario(pair_id="p5", pair_role="abstain", timestamps=ts_in), "SCHEDULE_PM", "ALLOW"),
    ]
    # Denominator = 4 (act), hits = 2.
    assert score_ontime(runs) == pytest.approx(2 / 4)


def test_ontime_returns_none_when_no_act_pairs():
    runs = [ScoredRun(_scenario(pair_id="p", pair_role="abstain"), "NO_ACTION", "ALLOW")]
    assert score_ontime(runs) is None


# ---------- Pair accuracy ---------------------------------------------------


def test_pair_accuracy_requires_both_members_correct():
    runs = [
        # Pair A: both correct → 1 point
        ScoredRun(_scenario(scenario_id="a1", pair_id="A", truth="SCHEDULE_PM"), "SCHEDULE_PM", "NEEDS_HUMAN"),
        ScoredRun(_scenario(scenario_id="a2", pair_id="A", truth="NO_ACTION"), "NO_ACTION", "ALLOW"),
        # Pair B: one wrong → 0 points
        ScoredRun(_scenario(scenario_id="b1", pair_id="B", truth="SCHEDULE_PM"), "SCHEDULE_PM", "NEEDS_HUMAN"),
        ScoredRun(_scenario(scenario_id="b2", pair_id="B", truth="NO_ACTION"), "CHECK_SENSOR", "ALLOW"),
    ]
    assert score_pair_accuracy(runs) == pytest.approx(0.5)


def test_pair_accuracy_uses_a1_not_a2():
    """Even when the gate routes SCHEDULE_PM to NEEDS_HUMAN, A1 is what we grade."""
    runs = [
        ScoredRun(_scenario(scenario_id="x1", pair_id="P", truth="SCHEDULE_PM"), "SCHEDULE_PM", "NEEDS_HUMAN"),
        ScoredRun(_scenario(scenario_id="x2", pair_id="P", truth="NO_ACTION"), "NO_ACTION", "ALLOW"),
    ]
    assert score_pair_accuracy(runs) == 1.0


# ---------- Ask-F1 ----------------------------------------------------------


def test_ask_f1_formula():
    runs = [
        # TP: needed and asked
        ScoredRun(_scenario(truth="ESCALATE"), "ESCALATE", "NEEDS_HUMAN"),
        # FN: needed but did not ask
        ScoredRun(_scenario(truth="ESCALATE"), "SCHEDULE_PM", "NEEDS_HUMAN"),
        # FP: asked but not needed
        ScoredRun(_scenario(truth="NO_ACTION"), "ESCALATE", "NEEDS_HUMAN"),
        # TN: not needed, not asked
        ScoredRun(_scenario(truth="NO_ACTION"), "NO_ACTION", "ALLOW"),
    ]
    out = score_ask_f1(runs)
    assert out["precision"] == pytest.approx(0.5)  # 1 / (1+1)
    assert out["recall"] == pytest.approx(0.5)  # 1 / (1+1)
    assert out["f1"] == pytest.approx(0.5)


def test_ask_f1_ignores_gate_verdict():
    """Gate routing SCHEDULE_PM to NEEDS_HUMAN must not count as "asked"."""
    runs = [
        ScoredRun(_scenario(truth="ESCALATE"), "SCHEDULE_PM", "NEEDS_HUMAN"),
    ]
    out = score_ask_f1(runs)
    # Nothing was asked (A1 != ESCALATE), so precision denominator is 0.
    assert out["precision"] == 0.0
    assert out["recall"] == 0.0
    assert out["f1"] == 0.0


def test_ask_f1_zero_when_no_asks_or_needs():
    runs = [ScoredRun(_scenario(truth="NO_ACTION"), "NO_ACTION", "ALLOW")]
    out = score_ask_f1(runs)
    assert out == {"precision": 0.0, "recall": 0.0, "f1": 0.0}


# ---------- B1~B4 baseline wiring ------------------------------------------


def test_baselines_share_scoring_via_allow_verdict():
    """B1~B4 outputs flow through the same scoring with a2_verdict="ALLOW"."""
    scenarios = [
        _scenario(scenario_id="s1", truth="SCHEDULE_PM"),
        _scenario(scenario_id="s2", truth="NO_ACTION"),
        _scenario(scenario_id="s3", truth="ESCALATE"),
    ]
    b1_runs = [baseline_run(sc, "ESCALATE") for sc in scenarios]  # always-escalate
    out = score_all(b1_runs, config=_CONFIG, weight_set="default")
    # Verdict is ALLOW: ESCALATE → ASK_HUMAN executed.
    # (SCHEDULE_PM, ASK_HUMAN)=overescalation(1); (NO_ACTION, ASK_HUMAN)=overescalation(1);
    # (ESCALATE, ASK_HUMAN)=overescalation(1).
    assert out["cost"]["total"] == 3
    # Ask-F1 for always-escalate: precision 1/3, recall 1/1.
    assert out["ask_f1"]["precision"] == pytest.approx(1 / 3)
    assert out["ask_f1"]["recall"] == 1.0


def test_baseline_run_fixes_allow_verdict():
    sc = _scenario(truth="NO_ACTION")
    run = baseline_run(sc, "CHECK_SENSOR")
    assert run.a2_verdict == "ALLOW"
    assert run.a1_action == "CHECK_SENSOR"


# ---------- score_all contract ----------------------------------------------


def test_score_all_returns_four_axes():
    runs = [
        ScoredRun(_scenario(truth="NO_ACTION", pair_id="P", pair_role="act",
                            timestamps=_ts(_AS_OF)), "NO_ACTION", "ALLOW"),
    ]
    out = score_all(runs, config=_CONFIG)
    assert set(out) == {"cost", "ontime", "pair_accuracy", "ask_f1"}
