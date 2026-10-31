"""Read tools for PdM data. Every function requires as_of — records after it are never returned."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

_RAW = Path(__file__).parents[3] / "data" / "raw"
_DT_FMT = "%Y-%m-%d %H:%M:%S"

# Lazy per-file index: file_stem -> {machineID: sorted list of rows}
_INDEX: dict[str, dict[int, list[dict[str, Any]]]] = {}


def _dt(s: str) -> datetime:
    return datetime.strptime(s, _DT_FMT)


def _build_index(
    file_stem: str,
    parse_row: Callable[[dict[str, str]], dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    if file_stem in _INDEX:
        return _INDEX[file_stem]

    idx: dict[int, list[dict[str, Any]]] = {}
    with (_RAW / f"{file_stem}.csv").open() as f:
        for raw in csv.DictReader(f):
            row = parse_row(raw)
            idx.setdefault(row["machineID"], []).append(row)

    for rows in idx.values():
        rows.sort(key=lambda r: r["datetime"])

    _INDEX[file_stem] = idx
    return idx


def _slice(rows: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    return [r for r in rows if r["datetime"] <= as_of]


def read_telemetry(machine_id: int, as_of: datetime) -> list[dict[str, Any]]:
    def _parse(raw: dict[str, str]) -> dict[str, Any]:
        return {
            "datetime": _dt(raw["datetime"]),
            "machineID": int(raw["machineID"]),
            "volt": float(raw["volt"]),
            "rotate": float(raw["rotate"]),
            "pressure": float(raw["pressure"]),
            "vibration": float(raw["vibration"]),
        }

    idx = _build_index("PdM_telemetry", _parse)
    return _slice(idx.get(machine_id, []), as_of)


def read_errors(machine_id: int, as_of: datetime) -> list[dict[str, Any]]:
    def _parse(raw: dict[str, str]) -> dict[str, Any]:
        return {
            "datetime": _dt(raw["datetime"]),
            "machineID": int(raw["machineID"]),
            "errorID": raw["errorID"].strip('"'),
        }

    idx = _build_index("PdM_errors", _parse)
    return _slice(idx.get(machine_id, []), as_of)


def read_maintenance_history(machine_id: int, as_of: datetime) -> list[dict[str, Any]]:
    def _parse(raw: dict[str, str]) -> dict[str, Any]:
        return {
            "datetime": _dt(raw["datetime"]),
            "machineID": int(raw["machineID"]),
            "comp": raw["comp"].strip('"'),
        }

    idx = _build_index("PdM_maint", _parse)
    return _slice(idx.get(machine_id, []), as_of)


def read_failure_history(machine_id: int, as_of: datetime) -> list[dict[str, Any]]:
    def _parse(raw: dict[str, str]) -> dict[str, Any]:
        return {
            "datetime": _dt(raw["datetime"]),
            "machineID": int(raw["machineID"]),
            "failure": raw["failure"].strip('"'),
        }

    idx = _build_index("PdM_failures", _parse)
    return _slice(idx.get(machine_id, []), as_of)
