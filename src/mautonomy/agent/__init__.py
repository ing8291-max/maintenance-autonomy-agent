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
from mautonomy.agent.evaluate import RunResult, evaluate_scenario
from mautonomy.agent.loop import ALLOWED_ACTIONS, Decision, INVALID, MAX_STEPS, run_agent
from mautonomy.agent.tools import TOOL_NAMES, tool_schemas

__all__ = [
    "ALLOWED_ACTIONS",
    "DEFAULT_CONFIG",
    "Decision",
    "FakeAdapter",
    "INVALID",
    "LlamaAdapter",
    "MAX_STEPS",
    "ModelAdapter",
    "ModelConfig",
    "ModelResponse",
    "MuseSparkAdapter",
    "ResponseCache",
    "RunResult",
    "TOOL_NAMES",
    "ToolCall",
    "evaluate_scenario",
    "run_agent",
    "tool_schemas",
]
