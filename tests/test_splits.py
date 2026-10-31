"""Split contract tests — written before any implementation (TDD red phase)."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

# Allow imports from src/ without an editable install during the red phase
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

_REPO_ROOT = Path(__file__).parents[1]
_MACHINES_CSV = _REPO_ROOT / "data" / "raw" / "PdM_machines.csv"
_SPLITS_JSON = _REPO_ROOT / "data" / "splits.json"


def _load_machines_csv() -> list[dict]:
    with _MACHINES_CSV.open() as f:
        reader = csv.DictReader(f)
        return [
            {"machineID": int(row["machineID"]), "model": row["model"].strip('"')}
            for row in reader
        ]


# ---------------------------------------------------------------------------
# Test 1: same seed → same split
# ---------------------------------------------------------------------------

def test_same_seed_same_split():
    from mautonomy.data.splits import make_split

    result_a = make_split(seed=42)
    result_b = make_split(seed=42)

    assert result_a["dev"] == result_b["dev"], "dev lists must be equal for same seed"
    assert result_a["eval"] == result_b["eval"], "eval lists must be equal for same seed"


# ---------------------------------------------------------------------------
# Test 2: model stratification — each model's dev ratio is within 0.05 of 0.70
# ---------------------------------------------------------------------------

def test_model_stratification():
    from mautonomy.data.splits import make_split

    manifest = make_split(seed=42)
    dev_set = set(manifest["dev"])

    machines = _load_machines_csv()
    by_model: dict[str, list[int]] = {}
    for m in machines:
        by_model.setdefault(m["model"], []).append(m["machineID"])

    for model, ids in by_model.items():
        dev_count = sum(1 for mid in ids if mid in dev_set)
        ratio = dev_count / len(ids)
        assert abs(ratio - 0.70) <= 0.05, (
            f"Model {model}: dev ratio {ratio:.3f} is not within 0.05 of 0.70 "
            f"(dev={dev_count}, total={len(ids)})"
        )


# ---------------------------------------------------------------------------
# Test 3: eval guard — load_eval() without flag raises RuntimeError
# ---------------------------------------------------------------------------

def test_eval_guard():
    if not _SPLITS_JSON.exists():
        pytest.skip("data/splits.json not yet generated — run scripts/make_splits.py first")

    from mautonomy.data.splits import load_eval

    # No flag → must raise RuntimeError mentioning "allow_eval"
    with pytest.raises(RuntimeError, match="allow_eval"):
        load_eval()

    # Explicit allow → returns 30 items
    result = load_eval(allow_eval=True)
    assert len(result) == 30, f"Expected 30 eval machines, got {len(result)}"
