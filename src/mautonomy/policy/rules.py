"""Policy rule registry — ADR 0004."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ActionType(str, Enum):
    NO_ACTION = "NO_ACTION"
    CHECK_SENSOR = "CHECK_SENSOR"
    ESCALATE = "ESCALATE"
    SCHEDULE_PM = "SCHEDULE_PM"
    REQUEST_STOP = "REQUEST_STOP"


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    reason: str = ""  # boundary field; no rule reads this


@dataclass
class PolicyRule:
    id: str
    severity: str
    description: str
    applies_to: Callable[[ActionType], bool]
    violates: Callable[[ProposedAction], bool]
    verdict_on_violation: Verdict


def _always(_action: ProposedAction) -> bool:
    return True


RULES: list[PolicyRule] = [
    PolicyRule(
        id="schedule_pm_needs_human",
        severity="hard",
        description="예방 정비 일정 변경은 항상 사람 승인이 필요하다.",
        applies_to=lambda a: a == ActionType.SCHEDULE_PM,
        violates=_always,
        verdict_on_violation=Verdict.NEEDS_HUMAN,
    ),
    PolicyRule(
        id="request_stop_needs_human",
        severity="hard",
        description="설비 정지 요청은 항상 사람이 결정한다. (ADR 0010 지키는 선 1)",
        applies_to=lambda a: a == ActionType.REQUEST_STOP,
        violates=_always,
        verdict_on_violation=Verdict.NEEDS_HUMAN,
    ),
]
