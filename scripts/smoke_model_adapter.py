"""One real call to Muse Spark to verify the adapter wiring.

Usage::

    uv run python scripts/smoke_model_adapter.py

Gives the model a single read-tool definition (``read_telemetry``) and
reports whether a tool call comes back, plus token usage and best-effort
cost. The API key is never printed. Set ``MAUTONOMY_NO_CACHE=1`` to
force a live call even if a prior cached response exists.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mautonomy.agent import DEFAULT_CONFIG, MuseSparkAdapter, ResponseCache


READ_TELEMETRY_TOOL = {
    "type": "function",
    "function": {
        "name": "read_telemetry",
        "description": (
            "Return hourly telemetry rows (volt, rotate, pressure, vibration) "
            "for a machine strictly before the as-of timestamp."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "machine_id": {"type": "integer", "description": "Machine ID (1..100)"},
            },
            "required": ["machine_id"],
            "additionalProperties": False,
        },
    },
}


def main() -> int:
    cache_dir = None
    if os.environ.get("MAUTONOMY_NO_CACHE") == "1":
        cache_dir = Path(".agent-cache-smoke-forced")
    adapter = MuseSparkAdapter(cache=ResponseCache(cache_dir=cache_dir))

    messages = [
        {
            "role": "system",
            "content": (
                "You are a maintenance analyst. When you need machine data, "
                "call the read_telemetry tool. Reply with a tool call only."
            ),
        },
        {
            "role": "user",
            "content": "Pull telemetry for machine 7 so we can assess its condition.",
        },
    ]

    print(f"config:  {json.dumps(DEFAULT_CONFIG.as_dict(), sort_keys=True)}")
    print(f"adapter: {adapter.name} @ {adapter.base_url}")

    try:
        resp = adapter.complete(
            messages=messages,
            tools=[READ_TELEMETRY_TOOL],
            config=DEFAULT_CONFIG,
        )
    except Exception as exc:
        print(f"REAL CALL FAILED: {exc}")
        return 1

    print(f"cache_hit:  {resp.cache_hit}")
    print(f"text:       {resp.text!r}")
    print(f"tool_calls: {len(resp.tool_calls)}")
    for tc in resp.tool_calls:
        print(f"  - {tc.name}({json.dumps(tc.arguments, sort_keys=True)})")
    print(f"usage:      {resp.usage}")
    cost = f"${resp.cost_usd:.6f}" if resp.cost_usd is not None else "unknown (no pricing table)"
    print(f"cost:       {cost}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
