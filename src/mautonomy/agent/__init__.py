"""Model adapter layer for the maintenance-autonomy agent.

Three implementations are exposed:

* :class:`MuseSparkAdapter` — Muse Spark 1.1 over the OpenAI-compatible
  ``/chat/completions`` endpoint. Reads the API key from ``.env``.
* :class:`FakeAdapter` — scripted responses for CI / unit tests.
* :class:`LlamaAdapter` — placeholder for a future Llama fallback.

The settings used for every call live in :class:`ModelConfig` and are
appended to ``logs/model-runs.jsonl`` on each ``complete`` call so runs
are reproducible from the log alone.
"""

from mautonomy.agent.adapter import (
    FakeAdapter,
    LlamaAdapter,
    ModelAdapter,
    ModelResponse,
    MuseSparkAdapter,
    ToolCall,
)
from mautonomy.agent.cache import ResponseCache
from mautonomy.agent.config import DEFAULT_CONFIG, ModelConfig

__all__ = [
    "DEFAULT_CONFIG",
    "FakeAdapter",
    "LlamaAdapter",
    "ModelAdapter",
    "ModelConfig",
    "ModelResponse",
    "MuseSparkAdapter",
    "ResponseCache",
    "ToolCall",
]
