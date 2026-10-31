"""as_of leak tests — per ADR 0009: read tools must never return records after as_of."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

_REPO_ROOT = Path(__file__).parents[1]
_SPLITS = json.loads((_REPO_ROOT / "data" / "splits.json").read_text())
_DEV_MACHINES: list[int] = _SPLITS["dev"]
_EVAL_MACHINES: list[int] = _SPLITS["eval"]

_DATA_START = datetime(2015, 1, 1)
_DATA_END = datetime(2016, 1, 1)

# machine 1 has a failure at this timestamp with a coincident maintenance record
_FAILURE_DT = datetime(2015, 1, 5, 6, 0, 0)

_ALL_TOOLS_NAMES = [
    "read_telemetry",
    "read_errors",
    "read_maintenance_history",
    "read_failure_history",
]


def _import_tool(name: str):
    from mautonomy.data import read_tools
    return getattr(read_tools, name)


# ---------------------------------------------------------------------------
# Criterion 1: no tool returns records after as_of (Hypothesis property test)
# machine drawn randomly from 70 dev machines
# ---------------------------------------------------------------------------


@given(
    machine_id=st.sampled_from(_DEV_MACHINES),
    as_of=st.datetimes(min_value=_DATA_START, max_value=_DATA_END),
)
@settings(max_examples=200, deadline=None)
def test_no_future_records_hypothesis(machine_id: int, as_of: datetime) -> None:
    for name in _ALL_TOOLS_NAMES:
        tool = _import_tool(name)
        for record in tool(machine_id, as_of):
            assert record["datetime"] <= as_of, (
                f"{name}: record {record['datetime']} > as_of {as_of} (machine {machine_id})"
            )


# ---------------------------------------------------------------------------
# Criterion 2: coincident maintenance record hidden when as_of is just before failure
# ---------------------------------------------------------------------------


def test_coincident_maintenance_hidden_just_before_failure() -> None:
    from mautonomy.data.read_tools import read_maintenance_history

    as_of = _FAILURE_DT - timedelta(seconds=1)
    records = read_maintenance_history(1, as_of)
    assert _FAILURE_DT not in {r["datetime"] for r in records}, (
        f"maintenance record at {_FAILURE_DT} must not appear when as_of={as_of}"
    )


def test_coincident_maintenance_visible_at_failure_time() -> None:
    from mautonomy.data.read_tools import read_maintenance_history

    records = read_maintenance_history(1, _FAILURE_DT)
    assert _FAILURE_DT in {r["datetime"] for r in records}, (
        f"maintenance record at {_FAILURE_DT} must appear when as_of={_FAILURE_DT}"
    )


# ---------------------------------------------------------------------------
# Criterion 3: calling without as_of raises TypeError
# ---------------------------------------------------------------------------


def test_missing_as_of_raises() -> None:
    for name in _ALL_TOOLS_NAMES:
        tool = _import_tool(name)
        with pytest.raises(TypeError):
            tool(1)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Eval guard: read tools must refuse eval machines without allow_eval=True
# ---------------------------------------------------------------------------


def test_eval_machine_guard_raises() -> None:
    eval_machine = _EVAL_MACHINES[0]
    as_of = datetime(2015, 6, 1)
    for name in _ALL_TOOLS_NAMES:
        tool = _import_tool(name)
        with pytest.raises(RuntimeError, match="eval"):
            tool(eval_machine, as_of)


def test_eval_machine_allowed_with_flag() -> None:
    eval_machine = _EVAL_MACHINES[0]
    as_of = datetime(2015, 6, 1)
    for name in _ALL_TOOLS_NAMES:
        tool = _import_tool(name)
        result = tool(eval_machine, as_of, allow_eval=True)
        assert isinstance(result, list)
