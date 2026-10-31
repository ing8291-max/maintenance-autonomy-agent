"""Scenario YAML schema — ADR 0006 / ADR 0009.

Three timestamps (anomaly_onset, first_observable, point_of_no_return) are
required for act scenarios per ADR 0006. as_of must be before failure per
ADR 0009.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

SCHEMA_VERSION = "1.0"

ActionType = Literal["NO_ACTION", "SCHEDULE_PM", "ESCALATE", "CHECK_SENSOR", "REQUEST_STOP"]
ScenarioType = Literal["normal", "pre_failure_24_48h", "pre_failure_0_24h", "hardcase"]
SplitType = Literal["dev", "eval"]
PairRole = Literal["act", "abstain"]
HardcaseFamily = Literal["F1", "F2", "F3", "F4", "F5"]

_VALID_ACTIONS: frozenset[str] = frozenset(
    ["NO_ACTION", "SCHEDULE_PM", "ESCALATE", "CHECK_SENSOR", "REQUEST_STOP"]
)
_VALID_SCENARIO_TYPES: frozenset[str] = frozenset(
    ["normal", "pre_failure_24_48h", "pre_failure_0_24h", "hardcase"]
)
_VALID_FAMILIES: frozenset[str] = frozenset(["F1", "F2", "F3", "F4", "F5"])


class SchemaError(ValueError):
    """Raised when a scenario YAML fails schema validation."""


@dataclass(frozen=True)
class Timestamps:
    anomaly_onset: datetime
    first_observable: datetime
    point_of_no_return: datetime

    def __post_init__(self) -> None:
        if not (self.anomaly_onset <= self.first_observable <= self.point_of_no_return):
            raise SchemaError(
                f"Timestamps must be ordered: anomaly_onset ({self.anomaly_onset}) "
                f"<= first_observable ({self.first_observable}) "
                f"<= point_of_no_return ({self.point_of_no_return})"
            )


@dataclass(frozen=True)
class GroundTruth:
    action: ActionType
    rationale: str

    def __post_init__(self) -> None:
        if self.action not in _VALID_ACTIONS:
            raise SchemaError(
                f"Invalid action {self.action!r}. "
                f"Valid values: {sorted(_VALID_ACTIONS)}"
            )


@dataclass(frozen=True)
class Perturbation:
    family: HardcaseFamily
    sensor: str
    stuck_at: float
    stuck_from: datetime
    description: str

    def __post_init__(self) -> None:
        if self.family not in _VALID_FAMILIES:
            raise SchemaError(
                f"Invalid perturbation family {self.family!r}. "
                f"Valid values: {sorted(_VALID_FAMILIES)}"
            )


@dataclass(frozen=True)
class Scenario:
    schema_version: str
    scenario_id: str
    scenario_type: ScenarioType
    machine_id: int
    split: SplitType
    as_of: datetime
    seed: int
    ground_truth: GroundTruth
    failure_datetime: Optional[datetime]
    failure_component: Optional[str]
    timestamps: Optional[Timestamps]
    perturbation: Optional[Perturbation]
    pair_id: Optional[str]
    pair_role: Optional[PairRole]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise SchemaError(
                f"Unsupported schema_version: {self.schema_version!r}. "
                f"Expected {SCHEMA_VERSION!r}."
            )
        if self.scenario_type not in _VALID_SCENARIO_TYPES:
            raise SchemaError(
                f"Invalid scenario_type {self.scenario_type!r}. "
                f"Valid values: {sorted(_VALID_SCENARIO_TYPES)}"
            )
        if self.scenario_type != "normal":
            if self.failure_datetime is None:
                raise SchemaError(
                    f"failure_datetime is required for scenario_type={self.scenario_type!r}"
                )
            if self.as_of >= self.failure_datetime:
                raise SchemaError(
                    f"as_of ({self.as_of}) must be strictly before "
                    f"failure_datetime ({self.failure_datetime})"
                )
        if self.scenario_type in ("pre_failure_24_48h", "pre_failure_0_24h"):
            if self.timestamps is None:
                raise SchemaError(
                    f"timestamps is required for scenario_type={self.scenario_type!r}"
                )
