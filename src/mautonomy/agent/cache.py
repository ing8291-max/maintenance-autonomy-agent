"""Content-addressed response cache.

Key = sha256 of a canonical JSON of ``(model, messages, tools, config)``.
Any drift in any of those four fields produces a different key, so a
cached hit is always a response that was obtained under *exactly* the
same inputs. Cache files live under ``.agent-cache/`` which is
gitignored (see ``.gitignore``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mautonomy.agent.config import ModelConfig


_DEFAULT_DIR = Path(__file__).resolve().parents[3] / ".agent-cache"


class ResponseCache:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_DIR
        self.dir.mkdir(parents=True, exist_ok=True)

    def key(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        config: ModelConfig,
        rep: int = 0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools or [],
            "config": asdict(config),
            "rep": rep,
            "response_format": response_format or None,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        path = self.dir / f"{key}.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text())

    def put(self, key: str, response: dict[str, Any]) -> None:
        path = self.dir / f"{key}.json"
        path.write_text(json.dumps(response, ensure_ascii=False, sort_keys=True))
