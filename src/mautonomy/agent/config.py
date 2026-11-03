"""Model-call settings — one place, one record per call.

Changing any field here changes the cache key and the run log, so a stale
cache can never silently serve a response fitted to a different config.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelConfig:
    name: str = "muse-spark-1.1"
    temperature: float = 0.0
    reasoning_effort: str = "medium"
    max_output_tokens: int = 4096

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_CONFIG = ModelConfig()
