"""Policy gate contract tests — ADR 0004, ADR 0010 bright line 1."""
from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mautonomy.policy.gate import evaluate
from mautonomy.policy.rules import ActionType, ProposedAction, RULES, Verdict

_KNOWN = [a.value for a in ActionType]
_AUTONOMOUS = ["NO_ACTION", "CHECK_SENSOR", "ESCALATE"]
_NEEDS_HUMAN_TYPES = ["SCHEDULE_PM", "REQUEST_STOP"]


# 2 000 cases per rule -------------------------------------------------------


@settings(max_examples=2000)
@given(st.text())
def test_request_stop_always_needs_human(reason: str) -> None:
    result = evaluate(ProposedAction(action_type="REQUEST_STOP", reason=reason))
    assert result.verdict == Verdict.NEEDS_HUMAN


@settings(max_examples=2000)
@given(st.text())
def test_schedule_pm_always_needs_human(reason: str) -> None:
    result = evaluate(ProposedAction(action_type="SCHEDULE_PM", reason=reason))
    assert result.verdict == Verdict.NEEDS_HUMAN


@settings(max_examples=2000)
@given(st.text())
def test_no_action_allow(reason: str) -> None:
    result = evaluate(ProposedAction(action_type="NO_ACTION", reason=reason))
    assert result.verdict == Verdict.ALLOW


@settings(max_examples=2000)
@given(st.text())
def test_check_sensor_allow(reason: str) -> None:
    result = evaluate(ProposedAction(action_type="CHECK_SENSOR", reason=reason))
    assert result.verdict == Verdict.ALLOW


@settings(max_examples=2000)
@given(st.text())
def test_escalate_allow(reason: str) -> None:
    result = evaluate(ProposedAction(action_type="ESCALATE", reason=reason))
    assert result.verdict == Verdict.ALLOW


# Criterion 2: reason never changes verdict (gate ignores free text) ---------


@settings(max_examples=2000)
@given(st.sampled_from(_KNOWN), st.text(), st.text())
def test_reason_does_not_affect_verdict(
    action_type: str, reason_a: str, reason_b: str
) -> None:
    r1 = evaluate(ProposedAction(action_type=action_type, reason=reason_a))
    r2 = evaluate(ProposedAction(action_type=action_type, reason=reason_b))
    assert r1.verdict == r2.verdict


# Criterion 3: unknown actions blocked; 10 000 random strings ----------------


@settings(max_examples=10000)
@given(st.text().filter(lambda s: s not in _KNOWN))
def test_unknown_action_blocked(action_type: str) -> None:
    result = evaluate(ProposedAction(action_type=action_type))
    assert result.verdict == Verdict.BLOCKED


# Rule-order independence: strictest-wins means permutation doesn't matter ---


@settings(max_examples=2000)
@given(
    st.sampled_from(_KNOWN),
    st.text(),
    st.permutations(list(range(len(RULES)))),
)
def test_rule_order_independent(
    action_type: str, reason: str, perm: list[int]
) -> None:
    action = ProposedAction(action_type=action_type, reason=reason)
    canonical = evaluate(action)
    shuffled = [RULES[i] for i in perm]
    result = evaluate(action, rules=shuffled)
    assert result.verdict == canonical.verdict
