"""B4 — sensor threshold + recent-error rule.

Rule: if any sensor's 24h mean exceeds its dev-tuned threshold, OR any
error was logged in the last 24h, propose SCHEDULE_PM; else NO_ACTION.

Thresholds are the 95th percentile of the sensor's 24h-mean distribution
across dev machines. They are computed once from
``data/raw/PdM_telemetry.csv`` and persisted to
``data/models/b4_thresholds.json``. Confidence is None (rule-based).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path

from mautonomy.baselines.base import BaselineOutput
from mautonomy.baselines.features import SENSOR_FIELDS, b4_signals
from mautonomy.harness import ScenarioEnvironment

_MODELS_DIR = Path(__file__).parents[3] / "data" / "models"
_THRESHOLDS_PATH = _MODELS_DIR / "b4_thresholds.json"
_PERCENTILE = 0.95

_CACHE: dict[str, float] | None = None


def _load_thresholds() -> dict[str, float]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _THRESHOLDS_PATH.exists():
        raise FileNotFoundError(
            f"B4 thresholds not found at {_THRESHOLDS_PATH}. "
            "Run `python -m mautonomy.baselines.b4_sensor_rule --fit` on a dev "
            "snapshot to generate them."
        )
    _CACHE = json.loads(_THRESHOLDS_PATH.read_text())
    return _CACHE


def predict(env: ScenarioEnvironment) -> BaselineOutput:
    thresholds = _load_thresholds()
    means, any_err = b4_signals(env)
    exceeded = any(means[s] >= thresholds[s] for s in SENSOR_FIELDS)
    if exceeded or any_err:
        return BaselineOutput(action="SCHEDULE_PM", confidence=None)
    return BaselineOutput(action="NO_ACTION", confidence=None)


# ----- fitting (dev only) ---------------------------------------------------


def _fit_thresholds() -> dict[str, float]:
    """Compute 95th-percentile 24h-mean per sensor over dev machines.

    Each dev machine contributes one 24h-mean sample per hour of telemetry,
    using a rolling 24h window up to and including that hour. The 95th
    percentile across all samples becomes the threshold.
    """
    import csv

    raw = Path(__file__).parents[3] / "data"
    splits = json.loads((raw / "splits.json").read_text())
    dev = set(splits["dev"])

    per_sensor_samples: dict[str, list[float]] = {s: [] for s in SENSOR_FIELDS}

    rows_by_machine: dict[int, list[dict]] = {}
    with (raw / "raw" / "PdM_telemetry.csv").open() as f:
        for r in csv.DictReader(f):
            mid = int(r["machineID"])
            if mid not in dev:
                continue
            rows_by_machine.setdefault(mid, []).append({
                "datetime": datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S"),
                "volt": float(r["volt"]),
                "rotate": float(r["rotate"]),
                "pressure": float(r["pressure"]),
                "vibration": float(r["vibration"]),
            })

    for rows in rows_by_machine.values():
        rows.sort(key=lambda r: r["datetime"])
        for i, row in enumerate(rows):
            cutoff = row["datetime"] - timedelta(hours=24)
            window = [r for r in rows[max(0, i - 24): i + 1] if r["datetime"] > cutoff]
            if not window:
                continue
            for sensor in SENSOR_FIELDS:
                per_sensor_samples[sensor].append(
                    statistics.mean(r[sensor] for r in window)
                )

    thresholds: dict[str, float] = {}
    for sensor, samples in per_sensor_samples.items():
        samples.sort()
        k = int(_PERCENTILE * (len(samples) - 1))
        thresholds[sensor] = samples[k]
    return thresholds


def _write_thresholds(thresholds: dict[str, float]) -> None:
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    _THRESHOLDS_PATH.write_text(json.dumps(thresholds, indent=2, sort_keys=True))


if __name__ == "__main__":
    import sys
    if "--fit" in sys.argv:
        th = _fit_thresholds()
        _write_thresholds(th)
        print(f"wrote {_THRESHOLDS_PATH}")
        for s, v in sorted(th.items()):
            print(f"  {s}: {v:.4f}")
    else:
        print("usage: python -m mautonomy.baselines.b4_sensor_rule --fit")
