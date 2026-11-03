"""Load the versioned agent prompt and expose its sha256.

The prompt body lives in ``prompts/agent_v1.md`` so the content is diffable
on its own line. The hash goes in the run log so a reader can always
pin which prompt text produced a given run.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


@lru_cache(maxsize=8)
def load_prompt(version: str = "v1") -> tuple[str, str]:
    """Return (prompt_text, sha256_hex) for the named version."""
    path = _PROMPTS_DIR / f"agent_{version}.md"
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, digest
