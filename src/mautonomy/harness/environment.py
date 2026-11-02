"""ScenarioEnvironment — the only interface the agent and baselines see."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from mautonomy.data import read_tools
from mautonomy.harness import perturbation as _perturbation
from mautonomy.scenarios.schema import Scenario


Reader = Callable[[], list[dict[str, Any]]]


@dataclass(frozen=True)
class ScenarioEnvironment:
    """Scoped read access for one scenario.

    Only the four tools and the two public coordinates (``machine_id``,
    ``as_of``) are exposed. The underlying ``Scenario`` object — its
    perturbation parameters, ground truth, pair role, and timestamps —
    is intentionally **not** stored on this dataclass so no agent or
    baseline can read it.
    """
    machine_id: int
    as_of: datetime
    read_telemetry: Reader
    read_errors: Reader
    read_maintenance_history: Reader
    read_failure_history: Reader


def build_environment(
    scenario: Scenario, *, allow_eval: bool = False
) -> ScenarioEnvironment:
    """Return the sandbox for ``scenario``.

    The eval guard fires eagerly (before any tool call) so an eval-split
    scenario cannot be opened without ``allow_eval=True``.
    """
    read_tools._check_eval_guard(scenario.machine_id, allow_eval)
    return _build(
        machine_id=scenario.machine_id,
        as_of=scenario.as_of,
        perturbation=scenario.perturbation,
        allow_eval=allow_eval,
    )


def build_raw_environment(
    machine_id: int, as_of: datetime, *, allow_eval: bool = False
) -> ScenarioEnvironment:
    """Return a perturbation-free sandbox for a given ``(machine_id, as_of)``.

    Used by B3 training and by harness smoke paths where no synthesised
    scenario exists.
    """
    read_tools._check_eval_guard(machine_id, allow_eval)
    return _build(
        machine_id=machine_id,
        as_of=as_of,
        perturbation=None,
        allow_eval=allow_eval,
    )


def _build(
    *,
    machine_id: int,
    as_of: datetime,
    perturbation,  # Optional[Perturbation]
    allow_eval: bool,
) -> ScenarioEnvironment:
    def _tel() -> list[dict[str, Any]]:
        rows = read_tools.read_telemetry(
            machine_id, as_of, allow_eval=allow_eval
        )
        if perturbation is None:
            return rows
        return _perturbation.apply(rows, perturbation)

    def _err() -> list[dict[str, Any]]:
        return read_tools.read_errors(machine_id, as_of, allow_eval=allow_eval)

    def _maint() -> list[dict[str, Any]]:
        return read_tools.read_maintenance_history(
            machine_id, as_of, allow_eval=allow_eval
        )

    def _fail() -> list[dict[str, Any]]:
        return read_tools.read_failure_history(
            machine_id, as_of, allow_eval=allow_eval
        )

    return ScenarioEnvironment(
        machine_id=machine_id,
        as_of=as_of,
        read_telemetry=_tel,
        read_errors=_err,
        read_maintenance_history=_maint,
        read_failure_history=_fail,
    )
