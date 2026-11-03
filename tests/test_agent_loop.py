"""Tests for the agent loop (step 2/3).

Uses :class:`FakeAdapter` only; no real HTTP. The ScenarioEnvironment is
constructed directly with trivial readers so the loop runs against pure
in-memory data.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mautonomy.agent import (
    ALLOWED_ACTIONS,
    DEFAULT_CONFIG,
    FakeAdapter,
    INVALID,
    MAX_STEPS,
    ModelResponse,
    ResponseCache,
    TOOL_NAMES,
    ToolCall,
    run_agent,
    tool_schemas,
)
from mautonomy.agent import run_log
from mautonomy.harness.environment import ScenarioEnvironment


# ----- fixtures -------------------------------------------------------

@pytest.fixture
def tmp_log(tmp_path, monkeypatch) -> Path:
    log = tmp_path / "logs" / "model-runs.jsonl"
    monkeypatch.setattr(run_log, "_LOG_PATH", log)
    return log


@pytest.fixture
def env() -> ScenarioEnvironment:
    as_of = datetime(2016, 6, 1, 12, 0, 0)
    tel = [
        {"datetime": as_of - timedelta(hours=h), "volt": 170.0 + h, "rotate": 440.0,
         "pressure": 100.0, "vibration": 40.0}
        for h in range(1, 25)
    ]
    err = [{"datetime": as_of - timedelta(hours=5), "errorID": "error1"}]
    return ScenarioEnvironment(
        machine_id=7,
        as_of=as_of,
        read_telemetry=lambda: tel,
        read_errors=lambda: err,
        read_maintenance_history=lambda: [],
        read_failure_history=lambda: [],
    )


def _final(action: str, confidence: float = 0.7, reason: str = "r", evidence: str = "e") -> ModelResponse:
    return ModelResponse(
        text=json.dumps(
            {"action": action, "confidence": confidence, "evidence": evidence, "reason": reason}
        )
    )


def _tool(name: str, hours_back: int = 24) -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[ToolCall(name=name, arguments={"hours_back": hours_back}, id="t1")],
    )


# ----- schema shape ---------------------------------------------------

def test_tool_schemas_count_and_names():
    schemas = tool_schemas()
    assert len(schemas) == 4
    names = [s["function"]["name"] for s in schemas]
    assert set(names) == set(TOOL_NAMES)


def test_tool_schemas_only_expose_hours_back():
    """Per-tool maximum differs (ADR 0011 §1.3): telemetry/errors cap at
    72 hours; maintenance_history / failure_history go out to 365 days."""
    expected_max = {
        "read_telemetry": 72,
        "read_errors": 72,
        "read_maintenance_history": 8760,
        "read_failure_history": 8760,
    }
    for s in tool_schemas():
        params = s["function"]["parameters"]
        props = params["properties"]
        name = s["function"]["name"]
        assert list(props.keys()) == ["hours_back"]
        assert params["required"] == ["hours_back"]
        assert props["hours_back"]["minimum"] == 1
        assert props["hours_back"]["maximum"] == expected_max[name]


def test_tool_schemas_never_mention_scenario_coordinates():
    """``machine_id``, ``as_of``, ``allow_eval`` must stay bound in the env."""
    blob = json.dumps(tool_schemas())
    for forbidden in ("machine_id", "as_of", "allow_eval"):
        assert forbidden not in blob


# ----- happy path -----------------------------------------------------

def test_loop_returns_decision_from_first_valid_json(env, tmp_log):
    adapter = FakeAdapter(queue=[_final("NO_ACTION", confidence=0.9)])
    decision = run_agent(env, adapter)
    assert decision.action == "NO_ACTION"
    assert decision.confidence == pytest.approx(0.9)
    assert decision.format_errors == 0
    assert decision.steps_used == 1
    assert decision.reached_step_cap is False
    assert decision.prompt_version == "v1"
    assert len(decision.prompt_hash) == 64


def test_loop_runs_tool_then_decides(env, tmp_log):
    adapter = FakeAdapter(queue=[_tool("read_telemetry", 12), _final("SCHEDULE_PM")])
    decision = run_agent(env, adapter)
    assert decision.action == "SCHEDULE_PM"
    assert decision.tool_calls_made == 1
    # The second call to the adapter should have received a tool-result message.
    second_call_messages = adapter.calls[1]["messages"]
    tool_results = [m for m in second_call_messages if m.get("role") == "tool"]
    assert tool_results, "expected a tool-result message in the second turn"
    payload = json.loads(tool_results[-1]["content"])
    assert payload["tool"] == "read_telemetry"
    assert payload["hours_back"] == 12


# ----- hygiene --------------------------------------------------------

def test_messages_never_leak_scenario_internals(env, tmp_log):
    """Scenario-schema identifiers must never appear in model-facing messages."""
    adapter = FakeAdapter(queue=[_final("NO_ACTION")])
    run_agent(env, adapter)
    # Only inspect the messages, not the prompt body (which may use the
    # English word "truth" in prose).
    model_facing = json.dumps(
        [c["messages"][1:] for c in adapter.calls],  # drop the system prompt
        default=str,
    )
    for forbidden in ("scenario_type", "perturbation", "ground_truth", "pair_role"):
        assert forbidden not in model_facing, (
            f"scenario schema field {forbidden!r} leaked into model messages"
        )


# ----- step cap and format errors ------------------------------------

def test_eight_step_cap_forces_final_answer(env, tmp_log):
    """Model that only ever calls tools must stop at step 8 with INVALID."""
    adapter = FakeAdapter(default=_tool("read_errors", 1))
    decision = run_agent(env, adapter)
    assert decision.steps_used == MAX_STEPS == 8
    assert decision.reached_step_cap is True
    assert decision.action == INVALID
    # The last adapter call must have been invoked without tools so the
    # model is forced to answer in text.
    assert adapter.calls[-1]["tools"] is None


def test_format_errors_are_counted_and_recoverable(env, tmp_log):
    adapter = FakeAdapter(
        queue=[
            ModelResponse(text="not json"),
            ModelResponse(text=json.dumps({"action": "BOGUS", "confidence": 0.5})),
            _final("ESCALATE"),
        ]
    )
    decision = run_agent(env, adapter)
    assert decision.action == "ESCALATE"
    assert decision.format_errors == 2
    assert decision.action in ALLOWED_ACTIONS


def test_bad_confidence_counts_as_format_error(env, tmp_log):
    adapter = FakeAdapter(
        queue=[
            ModelResponse(text=json.dumps({"action": "NO_ACTION", "confidence": 1.5})),
            _final("NO_ACTION"),
        ]
    )
    decision = run_agent(env, adapter)
    assert decision.format_errors == 1
    assert decision.action == "NO_ACTION"


# ----- rep in cache key ----------------------------------------------

def test_cache_key_differs_across_reps(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path)
    args = dict(
        model=DEFAULT_CONFIG.name,
        messages=[{"role": "user", "content": "same"}],
        tools=None,
        config=DEFAULT_CONFIG,
    )
    k0 = cache.key(**args, rep=0)
    k1 = cache.key(**args, rep=1)
    k2 = cache.key(**args, rep=2)
    assert len({k0, k1, k2}) == 3


def test_adapter_rep_param_is_threaded_to_cache(tmp_path, tmp_log):
    """Two runs with different ``rep`` must not share a cache file."""
    cache = ResponseCache(cache_dir=tmp_path)
    from mautonomy.agent import MuseSparkAdapter

    adapter = MuseSparkAdapter(api_key="dummy", cache=cache)
    messages = [{"role": "user", "content": "x"}]
    key_a = cache.key(
        model=DEFAULT_CONFIG.name, messages=messages, tools=None,
        config=DEFAULT_CONFIG, rep=0,
    )
    key_b = cache.key(
        model=DEFAULT_CONFIG.name, messages=messages, tools=None,
        config=DEFAULT_CONFIG, rep=1,
    )
    cache.put(key_a, {"text": "A", "tool_calls": [], "usage": {}, "cost_usd": None, "model": DEFAULT_CONFIG.name})
    cache.put(key_b, {"text": "B", "tool_calls": [], "usage": {}, "cost_usd": None, "model": DEFAULT_CONFIG.name})

    import urllib.request
    def _no_net(*a, **kw):
        raise AssertionError("cache hit expected")
    original = urllib.request.urlopen
    urllib.request.urlopen = _no_net
    try:
        a0 = adapter.complete(messages=messages, rep=0)
        a1 = adapter.complete(messages=messages, rep=1)
    finally:
        urllib.request.urlopen = original
    assert a0.text == "A"
    assert a1.text == "B"
