"""Agent loop: up to 8 model turns, then a forced final answer.

Each turn, the model either calls one of the four read tools or replies
with the final JSON decision. On the last turn the tools are stripped so
the model *must* reply with the decision. Malformed replies are counted
as format errors; if the forced final turn is still malformed, the
decision is recorded as ``INVALID``.

The user prompt is intentionally boring: it says only "decide the
action now". Nothing from the :class:`Scenario` object — ground truth,
perturbation, scenario type, pair role — ever enters the messages.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from mautonomy.agent.adapter import ModelAdapter, ToolCall
from mautonomy.agent.config import DEFAULT_CONFIG, ModelConfig
from mautonomy.agent.prompt import load_prompt
from mautonomy.agent.run_log import record_run
from mautonomy.agent.tools import ToolError, dispatch, tool_schemas
from mautonomy.harness.environment import ScenarioEnvironment


ALLOWED_ACTIONS: frozenset[str] = frozenset(
    {"NO_ACTION", "CHECK_SENSOR", "SCHEDULE_PM", "ESCALATE", "REQUEST_STOP"}
)
INVALID = "INVALID"
MAX_STEPS = 8

_FINAL_INSTRUCTION = (
    "You must now reply with ONLY the final JSON object described in the "
    "system prompt. Do not call any more tools."
)
_RETRY_INSTRUCTION = (
    "Your previous reply was not a valid JSON object matching the required "
    "schema. Reply with ONLY the JSON object: "
    '{"action": "...", "confidence": 0..1, "evidence": "...", "reason": "..."}'
)


@dataclass(frozen=True)
class Decision:
    action: str
    confidence: float | None
    evidence: str
    reason: str
    steps_used: int
    format_errors: int
    prompt_hash: str
    prompt_version: str
    rep: int
    tool_calls_made: int
    reached_step_cap: bool
    total_tokens: dict[str, int] = field(default_factory=dict)
    total_cost_usd: float | None = None
    raw_final_text: str | None = None


@dataclass
class _State:
    messages: list[dict[str, Any]] = field(default_factory=list)
    format_errors: int = 0
    tool_calls_made: int = 0
    total_input: int = 0
    total_output: int = 0
    cost_known: bool = True
    total_cost: float = 0.0

    def record(self, usage: dict[str, int], cost: float | None) -> None:
        self.total_input += int(usage.get("input_tokens", 0))
        self.total_output += int(usage.get("output_tokens", 0))
        if cost is None:
            self.cost_known = False
        else:
            self.total_cost += cost

    def tokens(self) -> dict[str, int]:
        return {
            "input_tokens": self.total_input,
            "output_tokens": self.total_output,
            "total_tokens": self.total_input + self.total_output,
        }

    def cost(self) -> float | None:
        return self.total_cost if self.cost_known else None


_JSON_SPAN = re.compile(r"\{.*\}", re.DOTALL)


def _try_parse_decision(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    match = _JSON_SPAN.search(text)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    action = obj.get("action")
    confidence = obj.get("confidence")
    if action not in ALLOWED_ACTIONS:
        return None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None
    if not (0.0 <= float(confidence) <= 1.0):
        return None
    return {
        "action": action,
        "confidence": float(confidence),
        "evidence": str(obj.get("evidence", "")),
        "reason": str(obj.get("reason", "")),
    }


def _tool_call_to_assistant_msg(tcs: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc.id or f"call_{i}",
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
            }
            for i, tc in enumerate(tcs)
        ],
    }


def _invalid(
    state: _State,
    *,
    prompt_hash: str,
    prompt_version: str,
    rep: int,
    steps_used: int,
    raw: str | None,
    reached_cap: bool,
) -> Decision:
    return Decision(
        action=INVALID,
        confidence=None,
        evidence="",
        reason="format error",
        steps_used=steps_used,
        format_errors=state.format_errors,
        prompt_hash=prompt_hash,
        prompt_version=prompt_version,
        rep=rep,
        tool_calls_made=state.tool_calls_made,
        reached_step_cap=reached_cap,
        total_tokens=state.tokens(),
        total_cost_usd=state.cost(),
        raw_final_text=raw,
    )


def run_agent(
    env: ScenarioEnvironment,
    adapter: ModelAdapter,
    *,
    config: ModelConfig = DEFAULT_CONFIG,
    rep: int = 0,
    max_steps: int = MAX_STEPS,
    prompt_version: str = "v1",
) -> Decision:
    """Run the loop against ``env`` and return a :class:`Decision`.

    The adapter is responsible for the HTTP call (or scripted response)
    and for the response cache; the loop only threads ``rep`` through so
    repeated runs under the same scenario produce distinct cache keys.
    """
    system_text, prompt_hash = load_prompt(prompt_version)
    state = _State(
        messages=[
            {"role": "system", "content": system_text},
            {
                "role": "user",
                "content": "Decide the correct action for this machine now.",
            },
        ]
    )
    tools = tool_schemas()

    for step in range(1, max_steps + 1):
        is_last = step == max_steps
        if is_last:
            state.messages.append({"role": "user", "content": _FINAL_INSTRUCTION})

        resp = adapter.complete(
            messages=state.messages,
            tools=None if is_last else tools,
            config=config,
            rep=rep,
        )
        state.record(resp.usage, resp.cost_usd)

        if resp.tool_calls and not is_last:
            state.messages.append(_tool_call_to_assistant_msg(resp.tool_calls))
            for tc in resp.tool_calls:
                state.tool_calls_made += 1
                try:
                    result = dispatch(env, tc)
                except ToolError as exc:
                    result = json.dumps({"error": str(exc)})
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id or f"call_{state.tool_calls_made}",
                        "content": result,
                    }
                )
            continue

        parsed = _try_parse_decision(resp.text)
        if parsed is not None:
            return Decision(
                action=parsed["action"],
                confidence=parsed["confidence"],
                evidence=parsed["evidence"],
                reason=parsed["reason"],
                steps_used=step,
                format_errors=state.format_errors,
                prompt_hash=prompt_hash,
                prompt_version=prompt_version,
                rep=rep,
                tool_calls_made=state.tool_calls_made,
                reached_step_cap=is_last,
                total_tokens=state.tokens(),
                total_cost_usd=state.cost(),
                raw_final_text=resp.text,
            )

        state.format_errors += 1
        if is_last:
            return _invalid(
                state,
                prompt_hash=prompt_hash,
                prompt_version=prompt_version,
                rep=rep,
                steps_used=step,
                raw=resp.text,
                reached_cap=True,
            )
        state.messages.append({"role": "assistant", "content": resp.text or ""})
        state.messages.append({"role": "user", "content": _RETRY_INSTRUCTION})

    # Loop exits only via return; the explicit fall-through below is
    # defensive and should not be reachable.
    return _invalid(
        state,
        prompt_hash=prompt_hash,
        prompt_version=prompt_version,
        rep=rep,
        steps_used=max_steps,
        raw=None,
        reached_cap=True,
    )


__all__ = [
    "ALLOWED_ACTIONS",
    "Decision",
    "INVALID",
    "MAX_STEPS",
    "run_agent",
]
