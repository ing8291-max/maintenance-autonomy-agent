"""Tests for the model adapter layer.

Everything here runs against :class:`FakeAdapter` so CI never needs the
Muse Spark API key. The MuseSpark client itself is covered only at the
surface level (constructor, no network).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mautonomy.agent import (
    DEFAULT_CONFIG,
    FakeAdapter,
    LlamaAdapter,
    ModelConfig,
    ModelResponse,
    MuseSparkAdapter,
    ResponseCache,
    ToolCall,
)
from mautonomy.agent import run_log


@pytest.fixture
def tmp_log(tmp_path, monkeypatch) -> Path:
    log = tmp_path / "logs" / "model-runs.jsonl"
    monkeypatch.setattr(run_log, "_LOG_PATH", log)
    return log


def _read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_fake_adapter_returns_scripted_response(tmp_log):
    adapter = FakeAdapter(
        scripted={"ping": ModelResponse(text="pong", usage={"input_tokens": 4, "output_tokens": 4, "total_tokens": 8})}
    )
    resp = adapter.complete(messages=[{"role": "user", "content": "ping"}])
    assert resp.text == "pong"
    assert resp.usage["total_tokens"] == 8
    assert resp.model == DEFAULT_CONFIG.name


def test_fake_adapter_returns_tool_call(tmp_log):
    scripted = {
        "look up telemetry": ModelResponse(
            text=None,
            tool_calls=[ToolCall(name="read_telemetry", arguments={"machine_id": 1})],
        )
    }
    adapter = FakeAdapter(scripted=scripted)
    resp = adapter.complete(messages=[{"role": "user", "content": "look up telemetry"}])
    assert resp.text is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "read_telemetry"
    assert resp.tool_calls[0].arguments == {"machine_id": 1}


def test_fake_adapter_falls_back_to_default(tmp_log):
    adapter = FakeAdapter()
    resp = adapter.complete(messages=[{"role": "user", "content": "anything"}])
    assert resp.text == "ok"


def test_llama_adapter_is_placeholder():
    with pytest.raises(NotImplementedError):
        LlamaAdapter().complete(messages=[{"role": "user", "content": "hi"}])


def test_cache_key_stable_across_identical_inputs(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path)
    args = dict(
        model="muse-spark-1.1",
        messages=[{"role": "user", "content": "same"}],
        tools=None,
        config=DEFAULT_CONFIG,
    )
    assert cache.key(**args) == cache.key(**args)


def test_cache_key_changes_when_config_changes(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path)
    base = dict(
        model="muse-spark-1.1",
        messages=[{"role": "user", "content": "same"}],
        tools=None,
    )
    k1 = cache.key(**base, config=ModelConfig(temperature=0.0))
    k2 = cache.key(**base, config=ModelConfig(temperature=0.7))
    assert k1 != k2


def test_cache_round_trip(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path)
    key = "deadbeef"
    assert cache.get(key) is None
    cache.put(key, {"text": "hi", "usage": {}})
    assert cache.get(key) == {"text": "hi", "usage": {}}


def test_cache_hit_short_circuits_muse_spark(tmp_path, tmp_log, monkeypatch):
    cache = ResponseCache(cache_dir=tmp_path)
    adapter = MuseSparkAdapter(api_key="dummy", cache=cache)

    messages = [{"role": "user", "content": "cached?"}]
    key = cache.key(
        model=DEFAULT_CONFIG.name, messages=messages, tools=None, config=DEFAULT_CONFIG
    )
    cache.put(
        key,
        {
            "text": "from-cache",
            "tool_calls": [],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "cost_usd": None,
            "model": DEFAULT_CONFIG.name,
        },
    )

    def _no_network(*a, **kw):
        raise AssertionError("cache hit must not touch the network")

    monkeypatch.setattr("urllib.request.urlopen", _no_network)

    resp = adapter.complete(messages=messages)
    assert resp.text == "from-cache"
    assert resp.cache_hit is True

    log = _read_log(tmp_log)
    assert log and log[-1]["cache_hit"] is True


def test_run_log_captures_config(tmp_log):
    adapter = FakeAdapter()
    cfg = ModelConfig(name="muse-spark-1.1", temperature=0.1, reasoning_effort="high", max_output_tokens=512)
    adapter.complete(messages=[{"role": "user", "content": "hi"}], config=cfg)
    log = _read_log(tmp_log)
    assert log, "expected one run entry"
    entry = log[-1]
    assert entry["config"] == {
        "name": "muse-spark-1.1",
        "temperature": 0.1,
        "reasoning_effort": "high",
        "max_output_tokens": 512,
    }
    assert entry["adapter"] == "fake"


def test_api_key_never_appears_in_log_or_cache(tmp_path, tmp_log, monkeypatch):
    """The key must not leak into the cache file or the run log."""
    secret = "SECRET_KEY_VALUE_123"
    cache = ResponseCache(cache_dir=tmp_path)
    adapter = MuseSparkAdapter(api_key=secret, cache=cache)

    messages = [{"role": "user", "content": "noop"}]
    key = cache.key(
        model=DEFAULT_CONFIG.name, messages=messages, tools=None, config=DEFAULT_CONFIG
    )
    cache.put(
        key,
        {
            "text": "ok",
            "tool_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "cost_usd": None,
            "model": DEFAULT_CONFIG.name,
        },
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should hit cache")),
    )
    adapter.complete(messages=messages)

    for cached_file in tmp_path.glob("*.json"):
        assert secret not in cached_file.read_text()
    assert secret not in tmp_log.read_text()
