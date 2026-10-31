.PHONY: install test splits

install:
	uv pip install -e ".[dev]"

test:
	uv run pytest -v

splits:
	uv run python scripts/make_splits.py
