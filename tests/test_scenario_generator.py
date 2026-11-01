"""Generator tests.

Completion criteria tested here:
  (1) as_of < failure_datetime for all non-normal scenarios
  (2) same seed → same scenarios
  (3) eval machines not used (guard test)
  (4) F2 pair one causal diff
  (5) all generated files pass loader
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

_REPO_ROOT = Path(__file__).parents[1]
_SPLITS = json.loads((_REPO_ROOT / "data" / "splits.json").read_text())
_EVAL_MACHINES: set[int] = set(_SPLITS["eval"])

_CAUSAL_FIELDS = frozenset(
    ["scenario_type", "machine_id", "split", "as_of", "seed",
     "failure_datetime", "failure_component", "timestamps", "perturbation"]
)


@pytest.fixture(scope="module")
def generated():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    return generate_easy_scenarios(seed=42)


@pytest.fixture(scope="module")
def generated_again():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    return generate_easy_scenarios(seed=42)


# ---------------------------------------------------------------------------
# Criterion (2): same seed → identical scenarios
# ---------------------------------------------------------------------------


def test_same_seed_same_count(generated, generated_again):
    assert len(generated) == len(generated_again)


def test_same_seed_same_ids(generated, generated_again):
    ids_a = [s.scenario_id for s in generated]
    ids_b = [s.scenario_id for s in generated_again]
    assert ids_a == ids_b


def test_same_seed_same_as_of(generated, generated_again):
    for a, b in zip(generated, generated_again):
        assert a.as_of == b.as_of


# ---------------------------------------------------------------------------
# Criterion (3): eval machines never used
# ---------------------------------------------------------------------------


def test_no_eval_machine_in_generated(generated):
    for sc in generated:
        assert sc.machine_id not in _EVAL_MACHINES, (
            f"scenario {sc.scenario_id} uses eval machine {sc.machine_id}"
        )


def test_eval_machine_guard_in_generator():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    from mautonomy.scenarios.schema import SchemaError
    # generator must only draw from dev split; eval guard in loader catches slip-through
    scenarios = generate_easy_scenarios(seed=42)
    for sc in scenarios:
        assert sc.machine_id not in _EVAL_MACHINES


# ---------------------------------------------------------------------------
# Criterion (1): as_of < failure_datetime
# ---------------------------------------------------------------------------


def test_as_of_before_failure_all(generated):
    for sc in generated:
        if sc.failure_datetime is not None:
            assert sc.as_of < sc.failure_datetime, (
                f"{sc.scenario_id}: as_of {sc.as_of} >= failure {sc.failure_datetime}"
            )


# ---------------------------------------------------------------------------
# Scenario type coverage
# ---------------------------------------------------------------------------


def test_all_scenario_types_present(generated):
    types = {sc.scenario_type for sc in generated}
    assert "normal" in types
    assert "pre_failure_24_48h" in types
    assert "pre_failure_0_24h" in types


def test_scenario_count_by_type(generated):
    from collections import Counter
    counts = Counter(sc.scenario_type for sc in generated)
    print("\nScenario counts by type:")
    for t, n in sorted(counts.items()):
        print(f"  {t}: {n}")
    assert counts["normal"] >= 1
    assert counts["pre_failure_24_48h"] >= 1
    assert counts["pre_failure_0_24h"] >= 1


# ---------------------------------------------------------------------------
# Criterion (4): F2 pair present and one causal diff
# ---------------------------------------------------------------------------


def test_f2_pair_present(generated):
    f2_scenarios = [sc for sc in generated
                    if sc.perturbation is not None and sc.perturbation.family == "F2"]
    assert len(f2_scenarios) == 2, "Expected an F2 act+abstain pair"
    pair_ids = {sc.pair_id for sc in f2_scenarios}
    assert len(pair_ids) == 1, "Both F2 members must share one pair_id"
    roles = {sc.pair_role for sc in f2_scenarios}
    assert roles == {"act", "abstain"}


def test_f2_pair_one_causal_diff(generated):
    """The only *causal* difference between act and abstain is the perturbation.

    timestamps also differ, but as a derived consequence of the perturbation
    (act has an injected timeline; abstain is a sensor fault with no onset).
    """
    pairs: dict[str, list] = {}
    for sc in generated:
        if sc.pair_id:
            pairs.setdefault(sc.pair_id, []).append(sc)

    f2_pairs = {pid: scs for pid, scs in pairs.items() if any(
        sc.perturbation is not None and sc.perturbation.family == "F2" for sc in scs
    )}
    assert f2_pairs, "No F2 pairs found"

    derived = {"timestamps"}
    for pair_id, scs in f2_pairs.items():
        assert len(scs) == 2, f"pair {pair_id} has {len(scs)} scenarios, expected 2"
        act = next(s for s in scs if s.pair_role == "act")
        abstain = next(s for s in scs if s.pair_role == "abstain")
        causal_diffs = [
            f for f in _CAUSAL_FIELDS
            if f not in derived and getattr(act, f) != getattr(abstain, f)
        ]
        assert causal_diffs == ["perturbation"], (
            f"F2 pair {pair_id}: expected only 'perturbation' causal diff, "
            f"got {causal_diffs}"
        )


# ---------------------------------------------------------------------------
# F2 pair normal-window design (both members injected, start_at near as_of)
# ---------------------------------------------------------------------------


def _f2_pair(scenarios):
    f2 = [sc for sc in scenarios
          if sc.perturbation is not None and sc.perturbation.family == "F2"]
    act = next(s for s in f2 if s.pair_role == "act")
    abstain = next(s for s in f2 if s.pair_role == "abstain")
    return act, abstain


def test_f2_pair_both_hardcase_on_normal_window(generated):
    act, abstain = _f2_pair(generated)
    # Both members live on a normal window with no real failure.
    for sc in (act, abstain):
        assert sc.scenario_type == "hardcase"
        assert sc.failure_datetime is None
    # Timestamps live on the act side only (abstain has no onset to time against).
    assert abstain.timestamps is None


def test_f2_pair_actions(generated):
    act, abstain = _f2_pair(generated)
    assert act.ground_truth.action == "SCHEDULE_PM"
    assert abstain.ground_truth.action == "CHECK_SENSOR"


def test_f2_pair_kinds_differ(generated):
    act, abstain = _f2_pair(generated)
    assert act.perturbation.kind == "precursor_rise"
    assert abstain.perturbation.kind == "sensor_stuck"


def test_f2_pair_start_at_close_to_as_of(generated):
    """F2 injection start must land shortly before as_of, not at data start.

    This is the invariant the previous design violated: stuck_from used to be
    pinned to failure_datetime - 72h, which drifted far from as_of.
    """
    act, abstain = _f2_pair(generated)
    for sc in (act, abstain):
        gap_hours = (sc.as_of - sc.perturbation.start_at).total_seconds() / 3600
        assert 0 < gap_hours <= 48, (
            f"{sc.scenario_id}: start_at {sc.perturbation.start_at} is "
            f"{gap_hours:.1f}h before as_of {sc.as_of}; must be within 48h"
        )


def test_f2_act_start_at_range(generated):
    """act's precursor must begin 6-18h before as_of (partial precursor only)."""
    act, _ = _f2_pair(generated)
    offset_hours = (act.as_of - act.perturbation.start_at).total_seconds() / 3600
    assert 6 <= offset_hours <= 18, (
        f"{act.scenario_id}: start_at offset {offset_hours:.1f}h outside [6, 18]"
    )


def test_f2_act_implied_failure_at_least_24h_after_as_of(generated):
    """implied_failure must sit ≥24h past as_of so SCHEDULE_PM is correct."""
    from datetime import timedelta

    act, _ = _f2_pair(generated)
    implied = act.perturbation.implied_failure
    assert implied is not None, "act must record implied_failure"
    assert implied - act.as_of >= timedelta(hours=24), (
        f"implied_failure {implied} is only "
        f"{(implied - act.as_of).total_seconds() / 3600:.1f}h after as_of "
        f"{act.as_of}; must be ≥24h"
    )


def test_f2_act_lead_matches_implied_failure(generated):
    """lead = start_at → implied_failure must stay in the measured 35-47h band."""
    from datetime import timedelta

    act, _ = _f2_pair(generated)
    pert = act.perturbation
    lead = (pert.implied_failure - pert.start_at).total_seconds() / 3600
    assert 35 <= lead <= 47, (
        f"{act.scenario_id}: lead {lead:.1f}h outside measured [35, 47]"
    )
    assert pert.rise_duration_hours == int(lead), (
        "rise_duration_hours must equal start_at→implied_failure lead"
    )


def test_f2_act_rise_rate_targets_two_sigma_at_implied_failure(generated):
    """rise_rate · rise_duration ≈ 2σ (vib) — full rise reached at implied_failure."""
    import csv
    import statistics
    from datetime import datetime, timedelta
    from pathlib import Path

    act, _ = _f2_pair(generated)
    pert = act.perturbation
    raw = Path(__file__).parents[1] / "data" / "raw" / "PdM_telemetry.csv"
    end = act.as_of
    start = end - timedelta(days=30)
    vals = []
    with raw.open() as f:
        for row in csv.DictReader(f):
            if int(row["machineID"]) != act.machine_id:
                continue
            dt = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
            if start <= dt < end:
                vals.append(float(row["vibration"]))
    sigma = statistics.stdev(vals)
    total_rise_sd = pert.rise_rate_per_hour * pert.rise_duration_hours / sigma
    assert abs(total_rise_sd - 2.0) < 0.01, (
        f"full-trajectory rise {total_rise_sd:.3f}σ, expected 2.0σ"
    )


def test_f2_act_has_timestamps_abstain_does_not(generated):
    """ADR 0006 timestamps: act's PONR = implied_failure − 12h (not as_of)."""
    from datetime import timedelta

    act, abstain = _f2_pair(generated)
    assert act.timestamps is not None
    assert act.timestamps.anomaly_onset == act.perturbation.start_at
    expected_ponr = act.perturbation.implied_failure - timedelta(hours=12)
    assert act.timestamps.point_of_no_return == expected_ponr
    assert act.timestamps.point_of_no_return > act.as_of, (
        "PONR must be strictly after as_of"
    )
    assert abstain.timestamps is None


def test_f2_pair_as_of_in_normal_window(generated):
    """F2 as_of must sit in a window with no failure within ±7 days."""
    import csv
    from datetime import datetime, timedelta
    from pathlib import Path

    act, _abstain = _f2_pair(generated)
    mid = act.machine_id
    as_of = act.as_of
    gap = timedelta(days=7)

    raw = Path(__file__).parents[1] / "data" / "raw" / "PdM_failures.csv"
    with raw.open() as f:
        for row in csv.DictReader(f):
            if int(row["machineID"]) != mid:
                continue
            fdt = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S")
            assert abs((as_of - fdt).total_seconds()) >= gap.total_seconds(), (
                f"F2 as_of {as_of} is within ±7d of failure {fdt} on machine {mid}"
            )


# ---------------------------------------------------------------------------
# Criterion (5): all generated scenarios pass loader
# ---------------------------------------------------------------------------


def test_all_generated_scenarios_have_valid_ids(generated):
    ids = [sc.scenario_id for sc in generated]
    assert len(ids) == len(set(ids)), "Duplicate scenario IDs"


def test_generated_scenarios_timestamps_ordered(generated):
    for sc in generated:
        if sc.timestamps is not None:
            ts = sc.timestamps
            assert ts.anomaly_onset <= ts.first_observable <= ts.point_of_no_return, (
                f"{sc.scenario_id}: timestamps out of order"
            )


def test_generated_scenarios_written_and_loadable(tmp_path):
    from mautonomy.scenarios.generator import generate_easy_scenarios
    from mautonomy.scenarios.loader import load_scenario, write_scenario

    scenarios = generate_easy_scenarios(seed=42)
    for sc in scenarios:
        p = tmp_path / f"{sc.scenario_id}.yaml"
        write_scenario(sc, p)
        loaded = load_scenario(p)
        assert loaded.scenario_id == sc.scenario_id
        assert loaded.as_of == sc.as_of
        assert loaded.ground_truth.action == sc.ground_truth.action
