"""Model adapters.

The adapter interface collapses an OpenAI-style ``chat.completions``
call down to a single :meth:`ModelAdapter.complete` method so the rest
of the agent never sees raw HTTP, auth headers, or vendor-specific
response shapes. Three concrete adapters live here:

* :class:`MuseSparkAdapter` — Muse Spark 1.1 at ``api.meta.ai``.
* :class:`FakeAdapter` — scripted answers for tests and CI.
* :class:`LlamaAdapter` — placeholder for the future Llama fallback.

All three share the cache + run-log path so switching adapters doesn't
also switch bookkeeping.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from mautonomy.agent.cache import ResponseCache
from mautonomy.agent.config import DEFAULT_CONFIG, ModelConfig
from mautonomy.agent.env import load_meta_api_key
from mautonomy.agent.pricing import estimate_cost_usd
from mautonomy.agent.run_log import record_run


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    model: str = ""
    cache_hit: bool = False


class ModelAdapter(Protocol):
    name: str

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ModelConfig = DEFAULT_CONFIG,
        rep: int = 0,
    ) -> ModelResponse: ...


def _parse_openai_response(data: dict[str, Any], model: str) -> ModelResponse:
    choice = data["choices"][0]["message"]
    tool_calls: list[ToolCall] = []
    for tc in choice.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args, id=tc.get("id")))
    usage_raw = data.get("usage") or {}
    usage = {
        "input_tokens": int(usage_raw.get("prompt_tokens", 0)),
        "output_tokens": int(usage_raw.get("completion_tokens", 0)),
        "total_tokens": int(
            usage_raw.get("total_tokens")
            or usage_raw.get("prompt_tokens", 0) + usage_raw.get("completion_tokens", 0)
        ),
    }
    cost = estimate_cost_usd(model, usage["input_tokens"], usage["output_tokens"])
    return ModelResponse(
        text=choice.get("content"),
        tool_calls=tool_calls,
        usage=usage,
        cost_usd=cost,
        model=model,
    )


def _response_to_cache_dict(resp: ModelResponse) -> dict[str, Any]:
    return {
        "text": resp.text,
        "tool_calls": [
            {"name": tc.name, "arguments": tc.arguments, "id": tc.id}
            for tc in resp.tool_calls
        ],
        "usage": resp.usage,
        "cost_usd": resp.cost_usd,
        "model": resp.model,
    }


def _response_from_cache_dict(d: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        text=d.get("text"),
        tool_calls=[
            ToolCall(name=tc["name"], arguments=tc.get("arguments", {}), id=tc.get("id"))
            for tc in d.get("tool_calls", [])
        ],
        usage=d.get("usage", {}),
        cost_usd=d.get("cost_usd"),
        model=d.get("model", ""),
        cache_hit=True,
    )


class MuseSparkAdapter:
    """OpenAI-compatible client for Muse Spark 1.1."""

    name = "muse-spark"
    base_url = "https://api.meta.ai/v1"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        cache: ResponseCache | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key or load_meta_api_key()
        self.cache = cache if cache is not None else ResponseCache()
        self.timeout = timeout

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ModelConfig = DEFAULT_CONFIG,
        rep: int = 0,
    ) -> ModelResponse:
        key = self.cache.key(
            model=config.name, messages=messages, tools=tools, config=config, rep=rep
        )
        cached = self.cache.get(key)
        if cached is not None:
            resp = _response_from_cache_dict(cached)
            record_run(
                adapter=self.name,
                config=config,
                usage=resp.usage,
                cost_usd=resp.cost_usd,
                cache_hit=True,
            )
            return resp

        payload: dict[str, Any] = {
            "model": config.name,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
        }
        if config.reasoning_effort:
            payload["reasoning_effort"] = config.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as fh:
                data = json.loads(fh.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"muse-spark HTTP {exc.code}: {body}") from None

        resp = _parse_openai_response(data, config.name)
        self.cache.put(key, _response_to_cache_dict(resp))
        record_run(
            adapter=self.name,
            config=config,
            usage=resp.usage,
            cost_usd=resp.cost_usd,
            cache_hit=False,
        )
        return resp


class FakeAdapter:
    """Deterministic adapter for CI.

    Pass a ``scripted`` mapping to tie specific user messages to canned
    :class:`ModelResponse` values; anything unmatched falls back to the
    ``default`` response. Usage counts are set to the length of the last
    user message so tests can still assert on token bookkeeping.
    """

    name = "fake"

    def __init__(
        self,
        *,
        scripted: dict[str, ModelResponse] | None = None,
        default: ModelResponse | None = None,
        queue: list[ModelResponse] | None = None,
    ) -> None:
        self.scripted = scripted or {}
        self.default = default or ModelResponse(
            text="ok", usage={"input_tokens": 0, "output_tokens": 1, "total_tokens": 1}
        )
        self.queue = list(queue) if queue else []
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ModelConfig = DEFAULT_CONFIG,
        rep: int = 0,
    ) -> ModelResponse:
        last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        self.calls.append({"messages": messages, "tools": tools, "config": config, "rep": rep})
        if self.queue:
            resp = self.queue.pop(0)
        else:
            resp = self.scripted.get(last_user, self.default)
        if resp.model == "":
            resp = ModelResponse(
                text=resp.text,
                tool_calls=resp.tool_calls,
                usage=resp.usage or {
                    "input_tokens": len(last_user),
                    "output_tokens": len(resp.text or ""),
                    "total_tokens": len(last_user) + len(resp.text or ""),
                },
                cost_usd=resp.cost_usd,
                model=config.name,
                cache_hit=False,
            )
        record_run(
            adapter=self.name,
            config=config,
            usage=resp.usage,
            cost_usd=resp.cost_usd,
            cache_hit=False,
        )
        return resp


class LlamaAdapter:
    """Placeholder for a future Llama fallback.

    Only the surface is here; wire a real client once we commit to a
    deployment target (Groq, Together, local vLLM, ...).
    """

    name = "llama"

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        config: ModelConfig = DEFAULT_CONFIG,
        rep: int = 0,
    ) -> ModelResponse:
        raise NotImplementedError(
            "LlamaAdapter is a placeholder; pick a backend before enabling."
        )
