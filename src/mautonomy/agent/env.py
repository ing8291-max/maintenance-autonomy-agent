"""Load ``META_API_KEY`` from the repo-local ``.env``.

The key value is never logged, printed, or placed in a cache key. The
public surface is just :func:`load_meta_api_key`, which returns the raw
string so callers can hand it straight to ``Authorization: Bearer`` and
drop the reference.
"""
from __future__ import annotations

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_KEY_NAME = "META_API_KEY"


class MissingAPIKey(RuntimeError):
    """Raised when ``META_API_KEY`` is absent from env and ``.env``."""


def load_meta_api_key() -> str:
    """Return ``META_API_KEY`` from the process env or ``.env``.

    Process env wins over ``.env`` so CI secrets injected by the harness
    take precedence over a developer's local file.
    """
    from_env = os.environ.get(_KEY_NAME)
    if from_env:
        return from_env

    if _ENV_FILE.is_file():
        for raw in _ENV_FILE.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == _KEY_NAME:
                return value.strip().strip('"').strip("'")

    raise MissingAPIKey(
        f"{_KEY_NAME} not found in process env or {_ENV_FILE}. "
        "Confirm the key is set and that .env stays gitignored."
    )
