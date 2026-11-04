.PHONY: install test splits baselines eval

install:
	uv pip install -e ".[dev]"

test:
	uv run pytest -v

splits:
	uv run python scripts/make_splits.py

# Fit the B3 (logistic regression) and B4 (sensor threshold) baselines on
# dev-split machines. Writes JSON artifacts under data/models/. Required
# locally before running baseline tests (Azure PdM data must be in data/raw/).
baselines:
	uv run python -m mautonomy.baselines.b4_sensor_rule --fit
	uv run python -m mautonomy.baselines.b3_logistic --fit

eval:
	uv run python scripts/run_eval.py
	uv run python scripts/score_eval.py
