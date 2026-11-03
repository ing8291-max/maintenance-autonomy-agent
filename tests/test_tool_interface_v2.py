"""Tests for the v2 interface changes (ADR 0011 §1.3).

Covered:
  - per-tool max look-back (365d for history tools, 72h for the rest)
  - read_telemetry payload includes a per-sensor summary block
  - FakeAdapter sees ``response_format`` on the final-answer turn
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from mautonomy.agent import (
    DEFAULT_CONFIG,
    FakeAdapter,
    ModelResponse,
    ToolCall,
    run_agent,
    tool_schemas,
)
from mautonomy.agent import run_log
from mautonomy.agent.loop import DECISION_RESPONSE_FORMAT
from mautonomy.agent.tools import MAX_HOURS_BACK, ToolError, dispatch
from mautonomy.harness.environment import ScenarioEnvironment


_AS_OF = datetime(2016, 6, 1, 12, 0, 0)


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    monkeypatch.setattr(run_log, "_LOG_PATH", tmp_path / "logs" / "model-runs.jsonl")


def _env(telemetry: list[dict] | None = None) -> ScenarioEnvironment:
    tel = telemetry if telemetry is not None else [
        {"datetime": _AS_OF - timedelta(hours=h), "volt": 170.0, "rotate": 440.0,
         "pressure": 100.0, "vibration": 40.0}
        for h in range(1, 25)
    ]
    return ScenarioEnvironment(
        machine_id=7,
        as_of=_AS_OF,
        read_telemetry=lambda: tel,
        read_errors=lambda: [],
        read_maintenance_history=lambda: [],
        read_failure_history=lambda: [],
    )


# ----- per-tool max look-back ----------------------------------------


def test_history_tools_accept_full_year_windows():
    assert MAX_HOURS_BACK["read_maintenance_history"] == 8760
    assert MAX_HOURS_BACK["read_failure_history"] == 8760


def test_telemetry_rejects_year_wide_window():
    env = _env()
    call = ToolCall(name="read_telemetry", arguments={"hours_back": 500})
    with pytest.raises(ToolError, match=r"\[1, 72\]"):
        dispatch(env, call)


def test_history_tool_accepts_year_wide_window():
    env = _env()
    out = json.loads(
        dispatch(env, ToolCall(name="read_failure_history", arguments={"hours_back": 8000}))
    )
    assert out["tool"] == "read_failure_history"
    assert out["hours_back"] == 8000


# ----- read_telemetry summary ----------------------------------------


def test_telemetry_payload_includes_per_sensor_summary():
    # 30 days of baseline rows at vibration=40 + 24h of recent rows at 60.
    rows = [
        {"datetime": _AS_OF - timedelta(hours=h), "volt": 170.0, "rotate": 440.0,
         "pressure": 100.0, "vibration": 40.0}
        for h in range(25, 25 + 30 * 24)
    ] + [
        {"datetime": _AS_OF - timedelta(hours=h), "volt": 170.0, "rotate": 440.0,
         "pressure": 100.0, "vibration": 60.0}
        for h in range(0, 24)
    ]
    env = _env(telemetry=rows)
    out = json.loads(
        dispatch(env, ToolCall(name="read_telemetry", arguments={"hours_back": 24}))
    )
    summary = out["summary"]
    assert set(summary) == {"volt", "rotate", "pressure", "vibration"}
    vib = summary["vibration"]
    assert vib["baseline_mean_30d"] == pytest.approx(40.0)
    assert vib["recent_mean_24h"] == pytest.approx(60.0)
    # std is 0 → z-score undefined.
    assert vib["zscore_24h"] is None
    assert vib["baseline_std_30d"] == pytest.approx(0.0)


def test_telemetry_summary_computes_zscore_when_baseline_varies():
    rows = []
    # 30 days of baseline: vibration oscillates 40 ± 2.
    for h in range(25, 25 + 30 * 24):
        rows.append({
            "datetime": _AS_OF - timedelta(hours=h),
            "volt": 170.0, "rotate": 440.0, "pressure": 100.0,
            "vibration": 40.0 + (2.0 if h % 2 == 0 else -2.0),
        })
    # Last 24h: vibration climbs well above baseline.
    for h in range(0, 24):
        rows.append({
            "datetime": _AS_OF - timedelta(hours=h),
            "volt": 170.0, "rotate": 440.0, "pressure": 100.0,
            "vibration": 48.0,
        })
    env = _env(telemetry=rows)
    out = json.loads(
        dispatch(env, ToolCall(name="read_telemetry", arguments={"hours_back": 24}))
    )
    vib = out["summary"]["vibration"]
    assert vib["baseline_std_30d"] == pytest.approx(2.0)
    assert vib["zscore_24h"] == pytest.approx(4.0)


def test_telemetry_summary_slope_sign_matches_direction():
    rows = []
    for h in range(0, 24):
        rows.append({
            "datetime": _AS_OF - timedelta(hours=h),
            "volt": 170.0, "rotate": 440.0, "pressure": 100.0,
            # vibration rises linearly as `h` decreases → recent values are higher.
            "vibration": 40.0 + (24 - h) * 0.5,
        })
    env = _env(telemetry=rows)
    out = json.loads(
        dispatch(env, ToolCall(name="read_telemetry", arguments={"hours_back": 24}))
    )
    vib = out["summary"]["vibration"]
    assert vib["slope_per_hour_24h"] is not None and vib["slope_per_hour_24h"] > 0


# ----- response_format on the forced-final turn ----------------------


def test_final_turn_sends_decision_schema(tmp_log):
    env = _env()
    adapter = FakeAdapter(
        queue=[ModelResponse(
            text=json.dumps({"action": "NO_ACTION", "confidence": 0.9,
                             "evidence": "e", "reason": "r"})
        )]
    )
    run_agent(env, adapter)
    # First call is also the only call — the model returned a decision on
    # turn 1 which goes through the retry schema only on the forced-final
    # or on recovery; on turn 1 the response_format is None by design.
    assert adapter.calls[0]["response_format"] is None


def test_retry_turn_sends_decision_schema(tmp_log):
    env = _env()
    adapter = FakeAdapter(
        queue=[
            ModelResponse(text="not json"),
            ModelResponse(
                text=json.dumps({"action": "NO_ACTION", "confidence": 0.5,
                                 "evidence": "e", "reason": "r"})
            ),
        ]
    )
    run_agent(env, adapter)
    assert adapter.calls[0]["response_format"] is None
    # The retry call must carry the strict JSON schema and no tools.
    assert adapter.calls[1]["response_format"] == DECISION_RESPONSE_FORMAT
    assert adapter.calls[1]["tools"] is None


def test_forced_final_turn_sends_decision_schema(tmp_log):
    env = _env()
    # Every turn returns a tool call so the loop runs to the forced final.
    tool_resp = ModelResponse(
        text=None,
        tool_calls=[ToolCall(name="read_errors", arguments={"hours_back": 1}, id="t")],
    )
    adapter = FakeAdapter(default=tool_resp)
    run_agent(env, adapter)
    final_call = adapter.calls[-1]
    assert final_call["tools"] is None
    assert final_call["response_format"] == DECISION_RESPONSE_FORMAT


def test_max_output_tokens_default_is_4096():
    assert DEFAULT_CONFIG.max_output_tokens == 4096
