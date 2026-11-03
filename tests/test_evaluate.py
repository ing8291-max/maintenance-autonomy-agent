"""Tests for :mod:`mautonomy.agent.evaluate` — the A1/A2 orchestrator.

The test ScenarioEnvironment is injected via monkeypatch so these tests
run without Azure data (CI-safe, ADR 0008).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mautonomy.agent import (
    FakeAdapter,
    ModelResponse,
    evaluate_scenario,
)
from mautonomy.agent import evaluate as evaluate_mod
from mautonomy.harness.environment import ScenarioEnvironment
from mautonomy.scenarios.schema import GroundTruth, Scenario


def _scenario(ground_truth_action: str = "SCHEDULE_PM") -> Scenario:
    return Scenario(
        schema_version="1.0",
        scenario_id="dev-test-0001",
        scenario_type="normal",
        machine_id=1,
        split="dev",
        as_of=datetime(2016, 6, 1, 12, 0, 0),
        seed=0,
        ground_truth=GroundTruth(action=ground_truth_action, rationale="test"),
        failure_datetime=None,
        failure_component=None,
        timestamps=None,
        perturbation=None,
        pair_id=None,
        pair_role=None,
    )


def _env_for(scenario: Scenario) -> ScenarioEnvironment:
    as_of = scenario.as_of
    tel = [
        {"datetime": as_of - timedelta(hours=h), "volt": 170.0, "rotate": 440.0,
         "pressure": 100.0, "vibration": 40.0}
        for h in range(1, 5)
    ]
    return ScenarioEnvironment(
        machine_id=scenario.machine_id,
        as_of=as_of,
        read_telemetry=lambda: tel,
        read_errors=lambda: [],
        read_maintenance_history=lambda: [],
        read_failure_history=lambda: [],
    )


@pytest.fixture
def patched_env(monkeypatch) -> None:
    monkeypatch.setattr(evaluate_mod, "build_environment", lambda sc, **kw: _env_for(sc))


@pytest.fixture
def trace(tmp_path) -> Path:
    return tmp_path / "trace.jsonl"


def _final(action: str, confidence: float = 0.8) -> ModelResponse:
    return ModelResponse(
        text=json.dumps(
            {"action": action, "confidence": confidence, "evidence": "e", "reason": "r"}
        )
    )


def test_one_llm_invocation_produces_both_a1_and_a2(patched_env, trace):
    """A2 is derived from A1 via the policy gate — no second adapter call."""
    adapter = FakeAdapter(queue=[_final("SCHEDULE_PM")])
    result = evaluate_scenario(_scenario(), adapter, rep=1, trace_path=trace)
    assert result.a1_action == "SCHEDULE_PM"
    # SCHEDULE_PM is always routed to a human per rules.py.
    assert result.a2_verdict == "NEEDS_HUMAN"
    assert result.a2_triggered_rules == ("schedule_pm_needs_human",)
    # Exactly one adapter call — no re-prompt for A2.
    assert len(adapter.calls) == 1


def test_no_action_passes_the_gate(patched_env, trace):
    adapter = FakeAdapter(queue=[_final("NO_ACTION")])
    result = evaluate_scenario(_scenario(), adapter, rep=1, trace_path=trace)
    assert result.a1_action == "NO_ACTION"
    assert result.a2_verdict == "ALLOW"


def test_request_stop_is_routed_to_human(patched_env, trace):
    adapter = FakeAdapter(queue=[_final("REQUEST_STOP")])
    result = evaluate_scenario(_scenario(), adapter, rep=1, trace_path=trace)
    assert result.a1_action == "REQUEST_STOP"
    assert result.a2_verdict == "NEEDS_HUMAN"


def test_invalid_a1_yields_na_a2(patched_env, trace):
    adapter = FakeAdapter(default=ModelResponse(text="not json"))
    result = evaluate_scenario(_scenario(), adapter, rep=1, trace_path=trace)
    assert result.a1_action == "INVALID"
    assert result.a2_verdict == "N/A"


def test_trace_line_shape(patched_env, trace):
    adapter = FakeAdapter(queue=[_final("NO_ACTION", confidence=0.42)])
    result = evaluate_scenario(_scenario(), adapter, rep=3, trace_path=trace)
    lines = trace.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    for required in (
        "scenario_id", "rep", "prompt_hash", "config", "steps_used",
        "tool_calls", "retries", "format_errors", "a1_action",
        "a1_confidence", "a2_verdict", "tokens", "cost_usd",
    ):
        assert required in entry, f"missing {required!r} in trace line"
    assert entry["scenario_id"] == "dev-test-0001"
    assert entry["rep"] == 3
    assert entry["a1_action"] == "NO_ACTION"
    assert entry["a1_confidence"] == pytest.approx(0.42)
    assert entry["a2_verdict"] == "ALLOW"
    # muse-spark-1.1 pricing is populated, so cost is a number not None.
    assert entry["cost_usd"] is not None
    assert result.cost_usd == entry["cost_usd"]


def test_cost_matches_rate_table(patched_env, trace):
    """Spot-check: cost = (in×1.25 + out×4.25) / 1e6."""
    adapter = FakeAdapter(
        queue=[
            ModelResponse(
                text=json.dumps(
                    {"action": "NO_ACTION", "confidence": 0.5, "evidence": "", "reason": ""}
                ),
                usage={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
            )
        ]
    )
    result = evaluate_scenario(_scenario(), adapter, rep=1, trace_path=trace)
    expected = (1000 * 1.25 + 100 * 4.25) / 1_000_000
    assert result.cost_usd == pytest.approx(expected)
