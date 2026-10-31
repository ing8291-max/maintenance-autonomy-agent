"""Scenario YAML loader — validates schema and enforces eval guard."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from mautonomy.scenarios.schema import (
    GroundTruth,
    Perturbation,
    Scenario,
    SchemaError,
    Timestamps,
)

_SPLITS_JSON = Path(__file__).parents[3] / "data" / "splits.json"
_DT_FMT = "%Y-%m-%d %H:%M:%S"
_EVAL_MACHINES: set[int] | None = None


def _get_eval_machines() -> set[int]:
    global _EVAL_MACHINES
    if _EVAL_MACHINES is None:
        splits = json.loads(_SPLITS_JSON.read_text())
        _EVAL_MACHINES = set(splits["eval"])
    return _EVAL_MACHINES


def _require(d: dict[str, Any], key: str) -> Any:
    if key not in d:
        raise SchemaError(f"Required field {key!r} missing from scenario")
    return d[key]


def _parse_dt(val: Any, field: str) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.strptime(val, _DT_FMT)
        except ValueError as e:
            raise SchemaError(f"Field {field!r}: invalid datetime {val!r}: {e}") from e
    raise SchemaError(f"Field {field!r}: expected datetime string, got {type(val).__name__}")


def _parse_timestamps(raw: dict[str, Any]) -> Timestamps:
    return Timestamps(
        anomaly_onset=_parse_dt(_require(raw, "anomaly_onset"), "anomaly_onset"),
        first_observable=_parse_dt(_require(raw, "first_observable"), "first_observable"),
        point_of_no_return=_parse_dt(_require(raw, "point_of_no_return"), "point_of_no_return"),
    )


def _parse_ground_truth(raw: dict[str, Any]) -> GroundTruth:
    return GroundTruth(
        action=_require(raw, "action"),
        rationale=_require(raw, "rationale"),
    )


def _parse_perturbation(raw: dict[str, Any]) -> Perturbation:
    return Perturbation(
        family=_require(raw, "family"),
        sensor=_require(raw, "sensor"),
        stuck_at=float(_require(raw, "stuck_at")),
        stuck_from=_parse_dt(_require(raw, "stuck_from"), "stuck_from"),
        description=_require(raw, "description"),
    )


def load_scenario(path: Path | str, *, allow_eval: bool = False) -> Scenario:
    """Load and validate a scenario YAML file.

    Raises SchemaError for any validation failure.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise SchemaError(f"YAML parse error in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise SchemaError(f"Expected a YAML mapping, got {type(raw).__name__}")
    return _parse_raw(raw, allow_eval=allow_eval)


def _parse_raw(raw: dict[str, Any], *, allow_eval: bool = False) -> Scenario:
    machine_id_val = _require(raw, "machine_id")
    try:
        machine_id = int(machine_id_val)
    except (TypeError, ValueError):
        raise SchemaError(f"machine_id must be an integer, got {machine_id_val!r}")

    if not allow_eval and machine_id in _get_eval_machines():
        raise SchemaError(
            f"machine_id {machine_id} is in the eval split. "
            "Scenarios must reference dev machines only. "
            "Pass allow_eval=True only for final evaluation runs."
        )

    as_of = _parse_dt(_require(raw, "as_of"), "as_of")

    failure_datetime_raw = raw.get("failure_datetime")
    failure_datetime = (
        _parse_dt(failure_datetime_raw, "failure_datetime")
        if failure_datetime_raw is not None
        else None
    )

    timestamps_raw = raw.get("timestamps")
    timestamps = _parse_timestamps(timestamps_raw) if timestamps_raw else None

    ground_truth_raw = _require(raw, "ground_truth")
    if not isinstance(ground_truth_raw, dict):
        raise SchemaError("ground_truth must be a mapping")
    ground_truth = _parse_ground_truth(ground_truth_raw)

    perturbation_raw = raw.get("perturbation")
    perturbation = _parse_perturbation(perturbation_raw) if perturbation_raw else None

    return Scenario(
        schema_version=str(_require(raw, "schema_version")),
        scenario_id=str(_require(raw, "scenario_id")),
        scenario_type=str(_require(raw, "scenario_type")),
        machine_id=machine_id,
        split=str(_require(raw, "split")),
        as_of=as_of,
        seed=int(_require(raw, "seed")),
        ground_truth=ground_truth,
        failure_datetime=failure_datetime,
        failure_component=raw.get("failure_component"),
        timestamps=timestamps,
        perturbation=perturbation,
        pair_id=raw.get("pair_id"),
        pair_role=raw.get("pair_role"),
    )


def write_scenario(scenario: Scenario, path: Path | str) -> None:
    """Serialise a Scenario to YAML at path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(_to_dict(scenario), allow_unicode=True, sort_keys=False))


def _to_dict(sc: Scenario) -> dict[str, Any]:
    def _dt(d: datetime | None) -> str | None:
        return d.strftime(_DT_FMT) if d else None

    ts = None
    if sc.timestamps:
        ts = {
            "anomaly_onset": _dt(sc.timestamps.anomaly_onset),
            "first_observable": _dt(sc.timestamps.first_observable),
            "point_of_no_return": _dt(sc.timestamps.point_of_no_return),
        }

    pert = None
    if sc.perturbation:
        pert = {
            "family": sc.perturbation.family,
            "sensor": sc.perturbation.sensor,
            "stuck_at": sc.perturbation.stuck_at,
            "stuck_from": _dt(sc.perturbation.stuck_from),
            "description": sc.perturbation.description,
        }

    return {
        "schema_version": sc.schema_version,
        "scenario_id": sc.scenario_id,
        "scenario_type": sc.scenario_type,
        "machine_id": sc.machine_id,
        "split": sc.split,
        "as_of": _dt(sc.as_of),
        "seed": sc.seed,
        "failure_datetime": _dt(sc.failure_datetime),
        "failure_component": sc.failure_component,
        "timestamps": ts,
        "ground_truth": {
            "action": sc.ground_truth.action,
            "rationale": sc.ground_truth.rationale,
        },
        "perturbation": pert,
        "pair_id": sc.pair_id,
        "pair_role": sc.pair_role,
    }
