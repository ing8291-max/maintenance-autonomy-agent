"""Run one scenario, score A1 and A2, append one trace line.

A single invocation of :func:`run_agent` produces the agent's raw action
(**A1**). The policy gate is then applied in pure Python to produce the
post-gate verdict (**A2**) — there is no second LLM call. ADR 0012 will
later define the cost-bucket / 적시 / Ask-F1 mappings; for now the trace
line records only facts: action, verdict, tokens, cost.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mautonomy.agent.adapter import ModelAdapter
from mautonomy.agent.config import DEFAULT_CONFIG, ModelConfig
from mautonomy.agent.loop import INVALID, Decision, run_agent
from mautonomy.harness.environment import build_environment
from mautonomy.policy.gate import GateResult, evaluate as gate_evaluate
from mautonomy.policy.rules import ProposedAction
from mautonomy.scenarios.schema import Scenario


_TRACE_PATH = Path(__file__).resolve().parents[3] / "logs" / "trace.jsonl"


@dataclass(frozen=True)
class RunResult:
    scenario_id: str
    rep: int
    ground_truth: str
    # A1 — the agent's proposed action, taken verbatim from the LLM.
    a1_action: str
    a1_confidence: float | None
    # A2 — the result of passing A1 through the policy gate (no new LLM call).
    a2_verdict: str
    a2_triggered_rules: tuple[str, ...]
    # Loop bookkeeping
    steps_used: int
    tool_calls: int
    retries: int
    format_errors: int
    reached_step_cap: bool
    # Cost accounting
    tokens: dict[str, int]
    cost_usd: float | None
    # Prompt + config provenance
    prompt_hash: str
    prompt_version: str
    config: ModelConfig


def _a2_from_a1(a1_action: str) -> GateResult:
    """Run A1 through the policy gate. INVALID stays INVALID (no gate call)."""
    return gate_evaluate(ProposedAction(action_type=a1_action))


def _trace_entry(result: RunResult) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenario_id": result.scenario_id,
        "rep": result.rep,
        "prompt_hash": result.prompt_hash,
        "prompt_version": result.prompt_version,
        "config": asdict(result.config),
        "steps_used": result.steps_used,
        "tool_calls": result.tool_calls,
        "retries": result.retries,
        "format_errors": result.format_errors,
        "reached_step_cap": result.reached_step_cap,
        "a1_action": result.a1_action,
        "a1_confidence": result.a1_confidence,
        "a2_verdict": result.a2_verdict,
        "a2_triggered_rules": list(result.a2_triggered_rules),
        "tokens": result.tokens,
        "cost_usd": result.cost_usd,
    }


def _write_trace(entry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")


def evaluate_scenario(
    scenario: Scenario,
    adapter: ModelAdapter,
    *,
    rep: int = 1,
    config: ModelConfig = DEFAULT_CONFIG,
    allow_eval: bool = False,
    trace_path: Path | None = None,
) -> RunResult:
    """Run ``scenario`` once and return the A1/A2 pair as a RunResult.

    Only one agent invocation happens — A2 is derived from A1 by the
    pure-Python policy gate. The trace line is appended to
    ``logs/trace.jsonl`` (or ``trace_path`` when overridden for tests).
    """
    env = build_environment(scenario, allow_eval=allow_eval)
    decision: Decision = run_agent(env, adapter, config=config, rep=rep)

    a1_action = decision.action
    if a1_action == INVALID:
        a2_verdict = "N/A"
        a2_rules: tuple[str, ...] = ()
    else:
        gate_result = _a2_from_a1(a1_action)
        a2_verdict = gate_result.verdict.value
        a2_rules = gate_result.triggered_rules

    result = RunResult(
        scenario_id=scenario.scenario_id,
        rep=rep,
        ground_truth=scenario.ground_truth.action,
        a1_action=a1_action,
        a1_confidence=decision.confidence,
        a2_verdict=a2_verdict,
        a2_triggered_rules=a2_rules,
        steps_used=decision.steps_used,
        tool_calls=decision.tool_calls_made,
        retries=decision.format_errors,
        format_errors=decision.format_errors,
        reached_step_cap=decision.reached_step_cap,
        tokens=decision.total_tokens,
        cost_usd=decision.total_cost_usd,
        prompt_hash=decision.prompt_hash,
        prompt_version=decision.prompt_version,
        config=config,
    )
    _write_trace(_trace_entry(result), trace_path or _TRACE_PATH)
    return result


__all__ = ["RunResult", "evaluate_scenario"]
