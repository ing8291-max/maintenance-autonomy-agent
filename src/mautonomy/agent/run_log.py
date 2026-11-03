"""Append one JSON line per model call to ``logs/model-runs.jsonl``.

Only the settings and token/cost summary go in — never the API key, the
message contents, or the response text. The log answers "what config did
we actually use" without being a secondary data leak.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mautonomy.agent.config import ModelConfig

_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "model-runs.jsonl"


def record_run(
    *,
    adapter: str,
    config: ModelConfig,
    usage: dict[str, int] | None,
    cost_usd: float | None,
    cache_hit: bool,
    log_path: Path | None = None,
) -> None:
    path = log_path or _LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "adapter": adapter,
        "config": asdict(config),
        "usage": usage or {},
        "cost_usd": cost_usd,
        "cache_hit": cache_hit,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
