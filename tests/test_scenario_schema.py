"""Schema and loader tests for scenario YAMLs.

Completion criteria tested here:
  (1) as_of < failure_datetime  (test_as_of_after_failure_raises)
  (3) eval machines rejected    (test_eval_machine_rejected)
  (4) F2 pair one causal diff   (test_f2_pair_one_causal_diff)
  (5) loader rejects invalid    (test_missing_field_raises, test_invalid_type_raises)
"""
from __future__ import annotations

import json
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

_REPO_ROOT = Path(__file__).parents[1]
_SPLITS = json.loads((_REPO_ROOT / "data" / "splits.json").read_text())
_DEV_MACHINES: list[int] = _SPLITS["dev"]
_EVAL_MACHINES: list[int] = _SPLITS["eval"]

_DT_FMT = "%Y-%m-%d %H:%M:%S"


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "scenario.yaml"
    p.write_text(textwrap.dedent(content))
    return p


def _valid_normal_yaml(machine_id: int = 1) -> str:
    return f"""\
        schema_version: "1.0"
        scenario_id: "test-normal-001"
        scenario_type: normal
        machine_id: {machine_id}
        split: dev
        as_of: "2015-07-15 12:00:00"
        seed: 42
        ground_truth:
          action: NO_ACTION
          rationale: "No anomaly signals present"
        failure_datetime: null
        failure_component: null
        timestamps: null
        perturbation: null
        pair_id: null
        pair_role: null
    """


def _valid_pre_failure_yaml(machine_id: int = 1) -> str:
    return f"""\
        schema_version: "1.0"
        scenario_id: "test-prefail-001"
        scenario_type: pre_failure_24_48h
        machine_id: {machine_id}
        split: dev
        as_of: "2015-01-03 06:00:00"
        seed: 42
        failure_datetime: "2015-01-05 06:00:00"
        failure_component: comp4
        timestamps:
          anomaly_onset: "2015-01-03 04:00:00"
          first_observable: "2015-01-03 06:00:00"
          point_of_no_return: "2015-01-04 18:00:00"
        ground_truth:
          action: SCHEDULE_PM
          rationale: "Vibration rising steadily for 48h"
        perturbation: null
        pair_id: null
        pair_role: null
    """


# ---------------------------------------------------------------------------
# Happy-path loads
# ---------------------------------------------------------------------------


def test_valid_normal_scenario_loads(tmp_path):
    from mautonomy.scenarios.loader import load_scenario

    p = _write_yaml(tmp_path, _valid_normal_yaml())
    sc = load_scenario(p)
    assert sc.scenario_type == "normal"
    assert sc.machine_id == 1
    assert sc.as_of == datetime(2015, 7, 15, 12, 0, 0)
    assert sc.ground_truth.action == "NO_ACTION"


def test_valid_pre_failure_scenario_loads(tmp_path):
    from mautonomy.scenarios.loader import load_scenario

    p = _write_yaml(tmp_path, _valid_pre_failure_yaml())
    sc = load_scenario(p)
    assert sc.scenario_type == "pre_failure_24_48h"
    assert sc.timestamps is not None
    assert sc.timestamps.anomaly_onset < sc.timestamps.first_observable
    assert sc.as_of < sc.failure_datetime


# ---------------------------------------------------------------------------
# Criterion (5): loader rejects malformed files
# ---------------------------------------------------------------------------


def test_missing_as_of_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_normal_yaml().replace('as_of: "2015-07-15 12:00:00"', "")
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError, match="as_of"):
        load_scenario(p)


def test_missing_schema_version_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_normal_yaml().replace('schema_version: "1.0"', "")
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError):
        load_scenario(p)


def test_invalid_action_type_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_normal_yaml().replace("NO_ACTION", "EXPLODE")
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError, match="action"):
        load_scenario(p)


def test_invalid_schema_version_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_normal_yaml().replace('"1.0"', '"99.0"')
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError, match="schema_version"):
        load_scenario(p)


def test_non_yaml_file_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    p = tmp_path / "bad.yaml"
    p.write_text("{{not: valid: yaml: [")
    with pytest.raises(SchemaError):
        load_scenario(p)


# ---------------------------------------------------------------------------
# Criterion (1): as_of < failure_datetime
# ---------------------------------------------------------------------------


def test_as_of_after_failure_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_pre_failure_yaml().replace(
        'as_of: "2015-01-03 06:00:00"',
        'as_of: "2015-01-06 06:00:00"',
    )
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError, match="as_of"):
        load_scenario(p)


def test_as_of_equal_failure_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_pre_failure_yaml().replace(
        'as_of: "2015-01-03 06:00:00"',
        'as_of: "2015-01-05 06:00:00"',
    )
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError, match="as_of"):
        load_scenario(p)


# ---------------------------------------------------------------------------
# Criterion (3): eval machines not used in generation
# ---------------------------------------------------------------------------


def test_eval_machine_rejected(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    eval_machine = _EVAL_MACHINES[0]
    p = _write_yaml(tmp_path, _valid_normal_yaml(machine_id=eval_machine))
    with pytest.raises(SchemaError, match="eval"):
        load_scenario(p)


def test_eval_machine_allowed_with_flag(tmp_path):
    from mautonomy.scenarios.loader import load_scenario

    eval_machine = _EVAL_MACHINES[0]
    p = _write_yaml(tmp_path, _valid_normal_yaml(machine_id=eval_machine))
    sc = load_scenario(p, allow_eval=True)
    assert sc.machine_id == eval_machine


# ---------------------------------------------------------------------------
# Criterion (4): F2 pair exactly one causal diff
# ---------------------------------------------------------------------------


def _f2_act_yaml(machine_id: int = 1) -> str:
    return f"""\
        schema_version: "1.0"
        scenario_id: "dev-f2-act-001"
        scenario_type: hardcase
        machine_id: {machine_id}
        split: dev
        as_of: "2015-03-04 06:00:00"
        seed: 42
        failure_datetime: null
        failure_component: null
        timestamps: null
        ground_truth:
          action: SCHEDULE_PM
          rationale: "Vibration rising steadily with correlated rotate drift — real precursor"
        perturbation:
          family: F2
          kind: precursor_rise
          sensor: vibration
          start_at: "2015-03-03 06:00:00"
          description: "vibration rises 0.4/h for 24h with rotate drifting by -75"
          rise_rate_per_hour: 0.4
          rise_duration_hours: 24
          correlated_sensor: rotate
          correlated_delta: -75.0
        pair_id: "f2-pair-001"
        pair_role: act
    """


def _f2_abstain_yaml(machine_id: int = 1) -> str:
    return f"""\
        schema_version: "1.0"
        scenario_id: "dev-f2-abstain-001"
        scenario_type: hardcase
        machine_id: {machine_id}
        split: dev
        as_of: "2015-03-04 06:00:00"
        seed: 42
        failure_datetime: null
        failure_component: null
        timestamps: null
        ground_truth:
          action: CHECK_SENSOR
          rationale: "Vibration sensor stuck — verify sensor before scheduling PM"
        perturbation:
          family: F2
          kind: sensor_stuck
          sensor: vibration
          start_at: "2015-03-03 12:00:00"
          description: "vibration sensor stuck at baseline mean; other signals unchanged"
          stuck_at: 40.39
        pair_id: "f2-pair-001"
        pair_role: abstain
    """


_CAUSAL_FIELDS = frozenset(
    ["scenario_type", "machine_id", "split", "as_of", "seed",
     "failure_datetime", "failure_component", "timestamps", "perturbation"]
)


def _load_pair(tmp_path: Path):
    from mautonomy.scenarios.loader import load_scenario

    p_act = tmp_path / "f2_act.yaml"
    p_abstain = tmp_path / "f2_abstain.yaml"
    p_act.write_text(textwrap.dedent(_f2_act_yaml()))
    p_abstain.write_text(textwrap.dedent(_f2_abstain_yaml()))
    return load_scenario(p_act), load_scenario(p_abstain)


def test_f2_pair_one_causal_diff(tmp_path):
    act, abstain = _load_pair(tmp_path)
    # timestamps are a derived consequence of the perturbation, not a separate
    # causal knob (act carries an injected timeline, abstain has none).
    derived = {"timestamps"}
    diffs = [
        f for f in _CAUSAL_FIELDS
        if f not in derived and getattr(act, f) != getattr(abstain, f)
    ]
    assert diffs == ["perturbation"], (
        f"Expected only 'perturbation' to differ, got: {diffs}"
    )


def test_f2_pair_roles(tmp_path):
    act, abstain = _load_pair(tmp_path)
    assert act.pair_role == "act"
    assert abstain.pair_role == "abstain"
    assert act.pair_id == abstain.pair_id


def test_f2_ground_truth_differs(tmp_path):
    act, abstain = _load_pair(tmp_path)

    assert act.ground_truth.action == "SCHEDULE_PM"
    assert abstain.ground_truth.action == "CHECK_SENSOR"


# ---------------------------------------------------------------------------
# Timestamps ordering invariant
# ---------------------------------------------------------------------------


def test_timestamps_out_of_order_raises(tmp_path):
    from mautonomy.scenarios.loader import load_scenario
    from mautonomy.scenarios.schema import SchemaError

    content = _valid_pre_failure_yaml().replace(
        'anomaly_onset: "2015-01-03 04:00:00"',
        'anomaly_onset: "2015-01-03 08:00:00"',
    )
    p = _write_yaml(tmp_path, content)
    with pytest.raises(SchemaError, match="ordered"):
        load_scenario(p)
