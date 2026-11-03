"""Tool schemas and dispatch for the agent loop.

Only four read tools are exposed, and each one accepts a single
``hours_back`` argument (1 to 72). The scenario-level coordinates
(``machine_id``, ``as_of``, ``allow_eval``) are *bound by the
environment* and must never appear in a schema — if they did, the agent
could read or exploit the scenario identity.

The dispatcher executes a :class:`~mautonomy.agent.adapter.ToolCall`
against a :class:`ScenarioEnvironment` and returns a JSON string that
the loop appends to the message history as the tool result.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from mautonomy.agent.adapter import ToolCall
from mautonomy.harness.environment import ScenarioEnvironment


MIN_HOURS_BACK = 1
MAX_HOURS_BACK = 72
MAX_ROWS = 200  # cap serialized rows to keep token cost bounded


def _hours_back_schema(label: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": f"read_{label}",
            "description": _DESCRIPTIONS[label],
            "parameters": {
                "type": "object",
                "properties": {
                    "hours_back": {
                        "type": "integer",
                        "minimum": MIN_HOURS_BACK,
                        "maximum": MAX_HOURS_BACK,
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
        "for the machine over the last `hours_back` hours."
    ),
    "errors": (
        "Return non-breaking error codes raised by the machine over the "
        "last `hours_back` hours."
    ),
    "maintenance_history": (
        "Return completed preventive maintenance and repair records over "
        "the last `hours_back` hours."
    ),
    "failure_history": (
        "Return past component failures on this machine over the last "
        "`hours_back` hours."
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
    return [
        _hours_back_schema("telemetry"),
        _hours_back_schema("errors"),
        _hours_back_schema("maintenance_history"),
        _hours_back_schema("failure_history"),
    ]


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


class ToolError(ValueError):
    """Raised when a tool call fails validation before dispatch."""


def dispatch(env: ScenarioEnvironment, call: ToolCall) -> str:
    """Run ``call`` against ``env`` and return its JSON-serialised result."""
    if call.name not in TOOL_NAMES:
        raise ToolError(f"unknown tool {call.name!r}")
    hours_back = call.arguments.get("hours_back")
    if not isinstance(hours_back, int) or isinstance(hours_back, bool):
        raise ToolError("hours_back must be an integer")
    if not (MIN_HOURS_BACK <= hours_back <= MAX_HOURS_BACK):
        raise ToolError(
            f"hours_back must be in [{MIN_HOURS_BACK}, {MAX_HOURS_BACK}]"
        )
    reader = _reader_for(env, call.name)
    rows = reader()
    windowed = _window(rows, env.as_of, hours_back)
    return json.dumps(
        {
            "tool": call.name,
            "hours_back": hours_back,
            "row_count": len(windowed),
            "rows": _serialise(windowed),
        },
        ensure_ascii=False,
    )
