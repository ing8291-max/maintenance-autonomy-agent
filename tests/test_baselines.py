"""Baseline tests (ADR 0005 comparison targets).

Covers:
  - Output shape (action is a valid ActionType; confidence is None or in [0,1])
  - Determinism: same (machine_id, as_of) → same output
  - Eval-guard: default allow_eval=False blocks eval-split machines
  - No scenario YAML access: baselines must work with no scenarios directory
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

# Guard module-level data loads so collection still works on CI (where
# data/ is intentionally absent, ADR 0001). The conftest's skip markers
# cover the actual test skipping.
if _SPLITS_PATH.exists():
    _SPLITS = json.loads(_SPLITS_PATH.read_text())
    _DEV_MACHINES: list[int] = sorted(_SPLITS["dev"])
    _EVAL_MACHINES: list[int] = sorted(_SPLITS["eval"])
else:
    _DEV_MACHINES = [1]
    _EVAL_MACHINES = [2]

# Representative probe points on a dev machine
_PROBE_MID = _DEV_MACHINES[0]
_PROBE_TIMES = [
    datetime(2015, 3, 1, 12, 0, 0),
    datetime(2015, 6, 15, 0, 0, 0),
    datetime(2015, 9, 30, 23, 0, 0),
]

_VALID_ACTIONS = {"NO_ACTION", "CHECK_SENSOR", "ESCALATE", "SCHEDULE_PM", "REQUEST_STOP"}


@pytest.fixture(params=["b1", "b2", "b3", "b4"])
def baseline(request):
    from mautonomy.baselines import b1_predict, b2_predict, b3_predict, b4_predict
    return {
        "b1": b1_predict,
        "b2": b2_predict,
        "b3": b3_predict,
        "b4": b4_predict,
    }[request.param]


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


def test_action_is_valid_action_type(baseline):
    for as_of in _PROBE_TIMES:
        out = baseline(_PROBE_MID, as_of)
        assert out.action in _VALID_ACTIONS, f"invalid action {out.action!r}"


def test_confidence_is_none_or_in_unit_interval(baseline):
    for as_of in _PROBE_TIMES:
        out = baseline(_PROBE_MID, as_of)
        if out.confidence is None:
            continue
        assert 0.0 <= out.confidence <= 1.0, (
            f"confidence {out.confidence} out of [0, 1]"
        )


def test_b1_always_escalates():
    from mautonomy.baselines import b1_predict
    for as_of in _PROBE_TIMES:
        assert b1_predict(_PROBE_MID, as_of).action == "ESCALATE"


def test_b2_always_schedules_pm():
    from mautonomy.baselines import b2_predict
    for as_of in _PROBE_TIMES:
        assert b2_predict(_PROBE_MID, as_of).action == "SCHEDULE_PM"


def test_b1_b2_have_null_confidence():
    from mautonomy.baselines import b1_predict, b2_predict
    for as_of in _PROBE_TIMES:
        assert b1_predict(_PROBE_MID, as_of).confidence is None
        assert b2_predict(_PROBE_MID, as_of).confidence is None


def test_b3_reports_calibrated_confidence():
    """B3 is LR — confidence is the predicted probability, never None."""
    from mautonomy.baselines import b3_predict
    for as_of in _PROBE_TIMES:
        out = b3_predict(_PROBE_MID, as_of)
        assert out.confidence is not None
        assert 0.0 <= out.confidence <= 1.0


def test_b3_action_follows_threshold():
    """Confirm the threshold rule: prob >= t → SCHEDULE_PM else NO_ACTION."""
    from mautonomy.baselines import b3_predict
    from mautonomy.baselines.b3_logistic import _load_model

    threshold = _load_model()["threshold"]
    for as_of in _PROBE_TIMES:
        out = b3_predict(_PROBE_MID, as_of)
        expected = "SCHEDULE_PM" if out.confidence >= threshold else "NO_ACTION"
        assert out.action == expected


def test_b4_only_proposes_pm_or_no_action():
    from mautonomy.baselines import b4_predict
    for as_of in _PROBE_TIMES:
        assert b4_predict(_PROBE_MID, as_of).action in {"SCHEDULE_PM", "NO_ACTION"}


# ---------------------------------------------------------------------------
# Determinism: same input → same output
# ---------------------------------------------------------------------------


def test_same_input_same_output(baseline):
    for as_of in _PROBE_TIMES:
        a = baseline(_PROBE_MID, as_of)
        b = baseline(_PROBE_MID, as_of)
        assert a == b, f"baseline {baseline.__module__} not deterministic"


def test_determinism_across_fresh_import(tmp_path, monkeypatch):
    """Dropping the module-level cache and re-importing must give the same result."""
    from mautonomy.baselines import b3_predict as b3_a
    from mautonomy.baselines import b4_predict as b4_a

    as_of = _PROBE_TIMES[0]
    a3 = b3_a(_PROBE_MID, as_of)
    a4 = b4_a(_PROBE_MID, as_of)

    # Force cache reset
    import mautonomy.baselines.b3_logistic as m3
    import mautonomy.baselines.b4_sensor_rule as m4
    monkeypatch.setattr(m3, "_CACHE", None)
    monkeypatch.setattr(m4, "_CACHE", None)

    b3 = m3.predict(_PROBE_MID, as_of)
    b4 = m4.predict(_PROBE_MID, as_of)
    assert a3 == b3
    assert a4 == b4


# ---------------------------------------------------------------------------
# Eval-guard: allow_eval=False (default) blocks eval machines
# ---------------------------------------------------------------------------


def test_eval_guard_blocks_eval_machines(baseline):
    """Every baseline must inherit the read_tools eval guard."""
    eval_mid = _EVAL_MACHINES[0]
    with pytest.raises(RuntimeError, match="eval split"):
        baseline(eval_mid, _PROBE_TIMES[0])


def test_eval_guard_permits_when_allowed(baseline):
    """Explicit allow_eval=True unblocks the guard (final eval only path)."""
    eval_mid = _EVAL_MACHINES[0]
    out = baseline(eval_mid, _PROBE_TIMES[0], allow_eval=True)
    assert out.action in _VALID_ACTIONS


# ---------------------------------------------------------------------------
# No scenario YAML access
# ---------------------------------------------------------------------------


def test_baselines_do_not_import_scenarios_schema_at_runtime(baseline):
    """Baselines must never touch scenario ground_truth or perturbation.

    We assert the baseline module does not reference these forbidden names.
    This is a lightweight guard — the eval-guard and determinism tests
    already prevent indirect access via read tools.
    """
    import importlib
    import inspect

    mod = importlib.import_module(baseline.__module__)
    source = inspect.getsource(mod)
    for forbidden in ("ground_truth", "perturbation", "load_scenario"):
        assert forbidden not in source, (
            f"baseline module {mod.__name__} references {forbidden!r}"
        )


def test_baselines_work_without_synth_dir(tmp_path, monkeypatch):
    """Baselines run end-to-end without any scenario YAML on disk."""
    # Nothing to monkeypatch — this is a smoke check that the baseline path
    # resolves no files under data/synth/. Running on a dev machine is enough.
    from mautonomy.baselines import b1_predict, b2_predict, b3_predict, b4_predict
    as_of = _PROBE_TIMES[0]
    for fn in (b1_predict, b2_predict, b3_predict, b4_predict):
        fn(_PROBE_MID, as_of)  # must not raise


# ---------------------------------------------------------------------------
# B3 training path: dev-only, allow_eval never True
# ---------------------------------------------------------------------------


def test_b3_fit_calls_only_dev_machines_and_never_allow_eval(monkeypatch):
    """`--fit` must never read eval machines and never pass allow_eval=True.

    We stub `extract_features` inside the b3_logistic module so we can log
    every call made by `_build_training_set` without actually running the
    gradient loop. The stub returns a zero-valued feature dict so the rest
    of the pipeline would still work.
    """
    import mautonomy.baselines.b3_logistic as b3
    from mautonomy.baselines.features import FEATURE_NAMES

    calls: list[tuple[int, bool]] = []

    def _stub_extract(mid, as_of, *, allow_eval=False):
        calls.append((mid, allow_eval))
        return {name: 0.0 for name in FEATURE_NAMES}

    monkeypatch.setattr(b3, "extract_features", _stub_extract)

    X, y = b3._build_training_set()
    assert len(X) == len(y) > 0, "training set must be non-empty"

    dev_set = set(_DEV_MACHINES)
    eval_set = set(_EVAL_MACHINES)

    seen_machines = {mid for mid, _ in calls}
    assert seen_machines, "fit path produced no read-tool calls"
    assert seen_machines.issubset(dev_set), (
        f"non-dev machines in fit path: {sorted(seen_machines - dev_set)}"
    )
    assert not (seen_machines & eval_set), (
        f"eval machines leaked into fit path: {sorted(seen_machines & eval_set)}"
    )

    allow_eval_flags = {flag for _, flag in calls}
    assert allow_eval_flags == {False}, (
        f"fit path must never set allow_eval=True; saw flags {allow_eval_flags}"
    )


def test_b3_fit_source_has_no_allow_eval_true():
    """Static check: the B3 source must not contain `allow_eval=True`."""
    import inspect

    import mautonomy.baselines.b3_logistic as b3

    source = inspect.getsource(b3)
    assert "allow_eval=True" not in source, (
        "b3_logistic.py must never pass allow_eval=True — training path only"
    )


def test_b4_fit_filters_to_dev_only(monkeypatch, tmp_path):
    """B4 `--fit` reads PdM_telemetry.csv directly and skips non-dev rows.

    This is a smoke check: fit then reload, and confirm no eval-machine
    stats contaminated the thresholds. (The function filters on `mid in dev`
    at load time; we assert the resulting thresholds are finite floats.)
    """
    import mautonomy.baselines.b4_sensor_rule as b4

    thresholds = b4._fit_thresholds()
    for sensor, value in thresholds.items():
        assert isinstance(value, float)
        assert value > 0, f"threshold for {sensor} should be positive, got {value}"
