"""Tool schemas and dispatch for the agent loop.

Only four read tools are exposed, and each one accepts a single
``hours_back`` argument. The scenario-level coordinates (``machine_id``,
``as_of``, ``allow_eval``) are *bound by the environment* and must never
appear in a schema — if they did, the agent could read or exploit the
scenario identity.

The look-back window differs per tool (ADR 0011 §1.3, interface-change-log
entry 2026-11-03): telemetry and errors cap at 72 hours; maintenance and
failure history go out to 8760 hours (365 days). Rows after ``env.as_of``
are already stripped upstream by ``mautonomy.data.read_tools``.

The dispatcher executes a :class:`~mautonomy.agent.adapter.ToolCall`
against a :class:`ScenarioEnvironment` and returns a JSON string that
the loop appends to the message history as the tool result.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from mautonomy.agent.adapter import ToolCall
from mautonomy.harness.environment import ScenarioEnvironment


MIN_HOURS_BACK = 1
MAX_ROWS = 200  # cap serialized rows to keep token cost bounded

# Per-tool max look-back in hours (ADR 0011 §1.3 interface change).
MAX_HOURS_BACK: dict[str, int] = {
    "read_telemetry": 72,
    "read_errors": 72,
    "read_maintenance_history": 8760,  # 365 days
    "read_failure_history": 8760,  # 365 days
}

_TELEMETRY_SENSORS: tuple[str, ...] = ("volt", "rotate", "pressure", "vibration")
_BASELINE_DAYS = 30
_SUMMARY_WINDOW_HOURS = 24


def _hours_back_schema(name: str) -> dict[str, Any]:
    label = name.removeprefix("read_")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": _DESCRIPTIONS[label],
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": {
                        "type": "integer",
                        "minimum": MIN_HOURS_BACK,
                        "maximum": MAX_HOURS_BACK[name],
                        "description": "Look-back window in hours, ending at now.",
                    }
                },
                "required": ["hours_back"],
                "additionalProperties": False,
            },
        },
    }


_DESCRIPTIONS: dict[str, str] = {
    "telemetry": (
        "Return hourly sensor readings (volt, rotate, pressure, vibration) "
        "for the machine over the last `hours_back` hours (max 72). "
        "The payload also includes a `summary` block with per-sensor "
        "30-day pre-now mean/std, the last-24h z-score, and the last-24h "
        "slope — descriptive statistics only."
    ),
    "errors": (
        "Return non-breaking error codes raised by the machine over the "
        "last `hours_back` hours (max 72)."
    ),
    "maintenance_history": (
        "Return completed preventive maintenance and repair records over "
        "the last `hours_back` hours (max 8760 = 365 days)."
    ),
    "failure_history": (
        "Return past component failures on this machine over the last "
        "`hours_back` hours (max 8760 = 365 days)."
    ),
}


TOOL_NAMES: tuple[str, ...] = (
    "read_telemetry",
    "read_errors",
    "read_maintenance_history",
    "read_failure_history",
)


def tool_schemas() -> list[dict[str, Any]]:
    """Return the four tool schemas handed to the model."""
    return [_hours_back_schema(name) for name in TOOL_NAMES]


def _reader_for(env: ScenarioEnvironment, name: str) -> Callable[[], list[dict[str, Any]]]:
    return {
        "read_telemetry": env.read_telemetry,
        "read_errors": env.read_errors,
        "read_maintenance_history": env.read_maintenance_history,
        "read_failure_history": env.read_failure_history,
    }[name]


def _window(rows: Iterable[dict[str, Any]], as_of: datetime, hours_back: int) -> list[dict[str, Any]]:
    cutoff = as_of - timedelta(hours=hours_back)
    return [r for r in rows if r.get("datetime") is None or r["datetime"] >= cutoff]


def _serialise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows[-MAX_ROWS:]:
        row = {}
        for k, v in r.items():
            row[k] = v.isoformat() if isinstance(v, datetime) else v
        out.append(row)
    return out


def _round(x: float | None, digits: int = 3) -> float | None:
    return None if x is None else round(float(x), digits)


def _slope_per_hour(values: list[float]) -> float | None:
    """Ordinary-least-squares slope over an evenly-spaced hourly series."""
    n = len(values)
    if n < 2:
        return None
    xs = list(range(n))
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return None
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    return num / denom


def _telemetry_summary(
    full_rows: list[dict[str, Any]], as_of: datetime
) -> dict[str, dict[str, float | int | None]]:
    """Per-sensor descriptive stats; no recommendation.

    Baseline window is ``[as_of - 30d, as_of - 24h)``: it excludes the
    recent 24h so the 24h-mean z-score compares against earlier data
    only, not against itself.
    """
    baseline_start = as_of - timedelta(days=_BASELINE_DAYS)
    recent_cutoff = as_of - timedelta(hours=_SUMMARY_WINDOW_HOURS)

    baseline = [
        r for r in full_rows
        if r.get("datetime") is not None
        and baseline_start <= r["datetime"] < recent_cutoff
    ]
    recent = [
        r for r in full_rows
        if r.get("datetime") is not None and recent_cutoff <= r["datetime"] <= as_of
    ]
    recent_sorted = sorted(recent, key=lambda r: r["datetime"])

    summary: dict[str, dict[str, float | int | None]] = {}
    for sensor in _TELEMETRY_SENSORS:
        base_vals = [float(r[sensor]) for r in baseline if sensor in r]
        rec_vals = [float(r[sensor]) for r in recent_sorted if sensor in r]
        if len(base_vals) >= 2:
            mean = statistics.fmean(base_vals)
            std = statistics.pstdev(base_vals)
        else:
            mean = std = None  # type: ignore[assignment]
        recent_mean = statistics.fmean(rec_vals) if rec_vals else None
        if (
            mean is not None
            and std is not None
            and std > 0
            and recent_mean is not None
        ):
            zscore = (recent_mean - mean) / std
        else:
            zscore = None
        slope = _slope_per_hour(rec_vals) if len(rec_vals) >= 2 else None
        summary[sensor] = {
            "baseline_mean_30d": _round(mean),
            "baseline_std_30d": _round(std),
            "recent_mean_24h": _round(recent_mean),
            "zscore_24h": _round(zscore),
            "slope_per_hour_24h": _round(slope),
            "baseline_row_count": len(base_vals),
            "recent_row_count": len(rec_vals),
        }
    return summary


class ToolError(ValueError):
    """Raised when a tool call fails validation before dispatch."""


def dispatch(env: ScenarioEnvironment, call: ToolCall) -> str:
    """Run ``call`` against ``env`` and return its JSON-serialised result."""
    if call.name not in TOOL_NAMES:
        raise ToolError(f"unknown tool {call.name!r}")
    hours_back = call.arguments.get("hours_back")
    if not isinstance(hours_back, int) or isinstance(hours_back, bool):
        raise ToolError("hours_back must be an integer")
    tool_max = MAX_HOURS_BACK[call.name]
    if not (MIN_HOURS_BACK <= hours_back <= tool_max):
        raise ToolError(
            f"hours_back must be in [{MIN_HOURS_BACK}, {tool_max}] for {call.name}"
        )
    reader = _reader_for(env, call.name)
    rows = reader()
    windowed = _window(rows, env.as_of, hours_back)
    payload: dict[str, Any] = {
        "tool": call.name,
        "hours_back": hours_back,
        "row_count": len(windowed),
        "rows": _serialise(windowed),
    }
    if call.name == "read_telemetry":
        payload["summary"] = _telemetry_summary(rows, env.as_of)
    return json.dumps(payload, ensure_ascii=False)
