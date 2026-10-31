"""Policy gate — ADR 0004."""
from __future__ import annotations

from dataclasses import dataclass

from .rules import ActionType, PolicyRule, RULES, ProposedAction, Verdict

_VERDICT_RANK: dict[Verdict, int] = {
    Verdict.ALLOW: 0,
    Verdict.NEEDS_HUMAN: 1,
    Verdict.BLOCKED: 2,
}


@dataclass(frozen=True)
class GateResult:
    verdict: Verdict
    triggered_rules: tuple[str, ...]


def evaluate(
    action: ProposedAction,
    rules: list[PolicyRule] | None = None,
) -> GateResult:
    _rules = RULES if rules is None else rules

    try:
        action_type = ActionType(action.action_type)
    except ValueError:
        return GateResult(verdict=Verdict.BLOCKED, triggered_rules=())

    triggered = [
        rule for rule in _rules
        if rule.applies_to(action_type) and rule.violates(action)
    ]

    if not triggered:
        return GateResult(verdict=Verdict.ALLOW, triggered_rules=())

    worst = max(triggered, key=lambda r: _VERDICT_RANK[r.verdict_on_violation])
    return GateResult(
        verdict=worst.verdict_on_violation,
        triggered_rules=tuple(r.id for r in triggered),
    )
