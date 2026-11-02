"""Pytest hooks shared by the test suite.

Primary responsibility: let the pure-logic tests (schema, policy gate,
hypothesis fuzz) run on CI where Azure PdM data is intentionally absent
(ADR 0001, ADR 0008). Tests that read ``data/raw/PdM_*.csv`` or depend on
baseline model artifacts under ``data/models/`` are skipped automatically
when their prerequisites are missing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_RAW = _REPO / "data" / "raw"
_MODELS = _REPO / "data" / "models"

_AZURE_FILES = (
    "PdM_telemetry.csv",
    "PdM_errors.csv",
    "PdM_failures.csv",
    "PdM_maint.csv",
    "PdM_machines.csv",
)
_BASELINE_MODEL_FILES = ("b3_lr.json", "b4_thresholds.json")

AZURE_DATA_AVAILABLE = all((_RAW / f).exists() for f in _AZURE_FILES)
BASELINE_MODELS_AVAILABLE = all((_MODELS / f).exists() for f in _BASELINE_MODEL_FILES)

# Test modules whose every test reads data/raw/.
_AZURE_ONLY_MODULES = {
    "test_scenario_generator",
    "test_as_of_leak",
    "test_scenario_environment",
}

# Specific test IDs in otherwise-pure modules that still need Azure data.
_AZURE_ONLY_TESTS: set[str] = {
    "test_splits::test_same_seed_same_split",
    "test_splits::test_model_stratification",
    "test_scenario_schema::test_eval_machine_rejected",
    "test_scenario_schema::test_eval_machine_allowed_with_flag",
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_azure_data: test needs data/raw/PdM_*.csv to be present",
    )
    config.addinivalue_line(
        "markers",
        "requires_baseline_models: test needs data/models/b{3,4}_*.json",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    skip_azure = pytest.mark.skip(
        reason="Azure PdM data not in data/raw/ (ADR 0001). Load it locally to run."
    )
    skip_models = pytest.mark.skip(
        reason="Baseline model artifacts missing. Run `make baselines`."
    )

    for item in items:
        mod_name = item.module.__name__.rsplit(".", 1)[-1]
        test_id = f"{mod_name}::{item.name}"

        needs_azure = (
            mod_name in _AZURE_ONLY_MODULES
            or test_id in _AZURE_ONLY_TESTS
            or mod_name == "test_baselines"
        )
        needs_models = mod_name == "test_baselines"

        if needs_azure and not AZURE_DATA_AVAILABLE:
            item.add_marker(skip_azure)
        elif needs_models and not BASELINE_MODELS_AVAILABLE:
            item.add_marker(skip_models)
