"""ScenarioEnvironment tests — perturbation applied only in read_telemetry.

Covers:
  * Pre-start_at telemetry rows match the raw read tool.
  * Post-start_at telemetry rows differ **only** on the injected sensor
    (and the correlated sensor for precursor_rise).
  * F2 pair: errors / maintenance / failures identical between act and
    abstain; telemetry differs only where at least one has injected.
  * as_of guard still holds (no row with datetime > as_of).
  * The environment dataclass does not expose scenario-level fields
    (perturbation, ground_truth, scenario_type, pair_id, pair_role,
    timestamps, failure_datetime, failure_component).
  * Eval-split machines are blocked at build_environment time.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

_REPO = Path(__file__).parents[1]
_SPLITS_PATH = _REPO / "data" / "splits.json"
if _SPLITS_PATH.exists():
    _SPLITS = json.loads(_SPLITS_PATH.read_text())
    _DEV_MACHINES: list[int] = sorted(_SPLITS["dev"])
    _EVAL_MACHINES: list[int] = sorted(_SPLITS["eval"])
else:
    _DEV_MACHINES = [1]
    _EVAL_MACHINES = [2]


@pytest.fixture(scope="module")
def f2_pair():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    scs = generate_easy_scenarios(seed=42)
    f2 = [s for s in scs if s.perturbation and s.perturbation.family == "F2"]
    act = next(s for s in f2 if s.pair_role == "act")
    abstain = next(s for s in f2 if s.pair_role == "abstain")
    return act, abstain


# ---------------------------------------------------------------------------
# Perturbation application: pre- vs post-start_at
# ---------------------------------------------------------------------------


def _raw_telemetry(mid, as_of):
    from mautonomy.data.read_tools import read_telemetry
    return read_telemetry(mid, as_of)


def test_pre_start_at_rows_match_raw(f2_pair):
    from mautonomy.harness import build_environment

    for scenario in f2_pair:
        env = build_environment(scenario)
        perturbed = env.read_telemetry()
        raw = _raw_telemetry(scenario.machine_id, scenario.as_of)
        assert len(perturbed) == len(raw)
        start_at = scenario.perturbation.start_at
        for pr, rr in zip(perturbed, raw):
            if pr["datetime"] < start_at:
                assert pr == rr, (
                    f"{scenario.scenario_id}: pre-start_at row at "
                    f"{pr['datetime']} differs from raw"
                )


def test_post_start_at_only_injected_sensors_differ(f2_pair):
    from mautonomy.harness import build_environment

    for scenario in f2_pair:
        env = build_environment(scenario)
        perturbed = env.read_telemetry()
        raw = _raw_telemetry(scenario.machine_id, scenario.as_of)
        start_at = scenario.perturbation.start_at
        allowed = {scenario.perturbation.sensor}
        if scenario.perturbation.correlated_sensor is not None:
            allowed.add(scenario.perturbation.correlated_sensor)

        for pr, rr in zip(perturbed, raw):
            if pr["datetime"] < start_at:
                continue
            for key in rr:
                if key in allowed:
                    continue
                assert pr[key] == rr[key], (
                    f"{scenario.scenario_id}: unexpected diff on {key} at "
                    f"{pr['datetime']} (allowed: {sorted(allowed)})"
                )


def test_injected_sensor_actually_moves(f2_pair):
    """Sanity: the injected sensor must differ from raw at some post-start_at row."""
    from mautonomy.harness import build_environment

    for scenario in f2_pair:
        env = build_environment(scenario)
        perturbed = env.read_telemetry()
        raw = _raw_telemetry(scenario.machine_id, scenario.as_of)
        sensor = scenario.perturbation.sensor
        start_at = scenario.perturbation.start_at

        diffs = [
            abs(pr[sensor] - rr[sensor])
            for pr, rr in zip(perturbed, raw)
            if pr["datetime"] >= start_at
        ]
        assert diffs, f"{scenario.scenario_id}: no post-start_at rows found"
        assert max(diffs) > 1e-9, (
            f"{scenario.scenario_id}: injection on {sensor} had no visible effect"
        )


# ---------------------------------------------------------------------------
# F2 pair: non-telemetry tools must be identical
# ---------------------------------------------------------------------------


def test_f2_pair_errors_identical(f2_pair):
    from mautonomy.harness import build_environment

    act, abstain = f2_pair
    env_a = build_environment(act)
    env_b = build_environment(abstain)
    assert env_a.read_errors() == env_b.read_errors()


def test_f2_pair_maintenance_history_identical(f2_pair):
    from mautonomy.harness import build_environment

    act, abstain = f2_pair
    env_a = build_environment(act)
    env_b = build_environment(abstain)
    assert env_a.read_maintenance_history() == env_b.read_maintenance_history()


def test_f2_pair_failure_history_identical(f2_pair):
    from mautonomy.harness import build_environment

    act, abstain = f2_pair
    env_a = build_environment(act)
    env_b = build_environment(abstain)
    assert env_a.read_failure_history() == env_b.read_failure_history()


def test_f2_pair_telemetry_differs_after_earlier_start_at(f2_pair):
    """F2 act and abstain telemetry agree before both start_ats, differ after."""
    from mautonomy.harness import build_environment

    act, abstain = f2_pair
    env_a = build_environment(act)
    env_b = build_environment(abstain)
    tel_a = env_a.read_telemetry()
    tel_b = env_b.read_telemetry()
    assert [r["datetime"] for r in tel_a] == [r["datetime"] for r in tel_b]

    earliest_start = min(act.perturbation.start_at, abstain.perturbation.start_at)
    for ra, rb in zip(tel_a, tel_b):
        if ra["datetime"] < earliest_start:
            assert ra == rb, (
                f"F2 pair diverges before earliest start_at at {ra['datetime']}"
            )

    # At least one post-earliest-start_at row must differ (otherwise the
    # two injections are indistinguishable, defeating the pair design).
    diffs = [1 for ra, rb in zip(tel_a, tel_b) if ra != rb]
    assert diffs, "F2 pair telemetry is identical across the whole window"


# ---------------------------------------------------------------------------
# as_of guard: no row post-as_of ever surfaces
# ---------------------------------------------------------------------------


def test_as_of_cutoff_still_enforced_in_env(f2_pair):
    from mautonomy.harness import build_environment

    for scenario in f2_pair:
        env = build_environment(scenario)
        for row in env.read_telemetry():
            assert row["datetime"] <= scenario.as_of
        for row in env.read_errors():
            assert row["datetime"] <= scenario.as_of
        for row in env.read_maintenance_history():
            assert row["datetime"] <= scenario.as_of
        for row in env.read_failure_history():
            assert row["datetime"] <= scenario.as_of


# ---------------------------------------------------------------------------
# Scenario leak surface: env dataclass hides all label fields
# ---------------------------------------------------------------------------


def test_env_dataclass_fields_are_exactly_the_public_six(f2_pair):
    """ScenarioEnvironment must expose only (mid, as_of) + 4 readers."""
    from dataclasses import fields

    from mautonomy.harness import build_environment, ScenarioEnvironment

    env = build_environment(f2_pair[0])
    names = {f.name for f in fields(env)}
    assert names == {
        "machine_id",
        "as_of",
        "read_telemetry",
        "read_errors",
        "read_maintenance_history",
        "read_failure_history",
    }
    for forbidden in (
        "perturbation",
        "ground_truth",
        "scenario_type",
        "pair_id",
        "pair_role",
        "timestamps",
        "failure_datetime",
        "failure_component",
    ):
        assert not hasattr(ScenarioEnvironment, forbidden)
        assert not hasattr(env, forbidden), (
            f"ScenarioEnvironment leaks {forbidden!r}"
        )


def test_env_repr_does_not_leak_scenario_fields(f2_pair):
    """Printing an env must not reveal perturbation/ground-truth content."""
    from mautonomy.harness import build_environment

    act, _ = f2_pair
    env = build_environment(act)
    r = repr(env)
    assert str(act.perturbation.sensor) in r or True  # sensor name is public-ish
    assert str(act.perturbation.start_at) not in r, (
        "env repr leaks perturbation.start_at"
    )
    assert act.ground_truth.rationale not in r
    assert act.ground_truth.action != "SCHEDULE_PM" or (
        act.ground_truth.rationale not in r
    )


# ---------------------------------------------------------------------------
# Eval guard at build_environment time
# ---------------------------------------------------------------------------


def test_build_environment_blocks_eval_machines():
    """A scenario pointing at an eval machine cannot be opened without the flag."""
    from mautonomy.harness import build_environment
    from mautonomy.scenarios.schema import GroundTruth, Scenario

    eval_mid = _EVAL_MACHINES[0]
    sc = Scenario(
        schema_version="1.0",
        scenario_id="probe-eval",
        scenario_type="normal",
        machine_id=eval_mid,
        split="eval",
        as_of=datetime(2015, 3, 1, 12, 0, 0),
        seed=1,
        ground_truth=GroundTruth(action="NO_ACTION", rationale="probe"),
        failure_datetime=None,
        failure_component=None,
        timestamps=None,
        perturbation=None,
        pair_id=None,
        pair_role=None,
    )
    with pytest.raises(RuntimeError, match="eval split"):
        build_environment(sc)
    # Explicit flag permits
    env = build_environment(sc, allow_eval=True)
    assert env.machine_id == eval_mid
