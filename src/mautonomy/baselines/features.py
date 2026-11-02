"""Feature extraction from as_of-aware read tools (shared by B3 and B4).

All features are a function of (machine_id, as_of) through the read-tool
interface only. No scenario YAML is consulted here. The eval guard is
inherited from the read tools — pass allow_eval only during final eval runs.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any

from mautonomy.data.read_tools import (
    read_errors,
    read_maintenance_history,
    read_telemetry,
)

SENSOR_FIELDS = ("volt", "rotate", "pressure", "vibration")
ERROR_IDS = ("error1", "error2", "error3", "error4", "error5")
COMPONENTS = ("comp1", "comp2", "comp3", "comp4")

# Sentinel for "no maintenance of this comp ever recorded ≤ as_of".
NEVER_MAINTAINED_HOURS = 10_000.0

_TELEMETRY_WINDOW_HOURS = 24
_ERROR_WINDOW_HOURS = 48


def _slope(ys: list[float]) -> float:
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den > 0 else 0.0


def _telemetry_stats(rows: list[dict[str, Any]], as_of: datetime) -> dict[str, float]:
    cutoff = as_of - timedelta(hours=_TELEMETRY_WINDOW_HOURS)
    window = [r for r in rows if cutoff < r["datetime"] <= as_of]
    out: dict[str, float] = {}
    for sensor in SENSOR_FIELDS:
        vals = [r[sensor] for r in window]
        if not vals:
            out[f"{sensor}_mean"] = 0.0
            out[f"{sensor}_std"] = 0.0
            out[f"{sensor}_slope"] = 0.0
            continue
        out[f"{sensor}_mean"] = statistics.mean(vals)
        out[f"{sensor}_std"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
        out[f"{sensor}_slope"] = _slope(vals)
    return out


def _error_counts(rows: list[dict[str, Any]], as_of: datetime) -> dict[str, float]:
    cutoff = as_of - timedelta(hours=_ERROR_WINDOW_HOURS)
    window = [r for r in rows if cutoff < r["datetime"] <= as_of]
    counts = {f"{eid}_count_48h": 0.0 for eid in ERROR_IDS}
    for r in window:
        key = f"{r['errorID']}_count_48h"
        if key in counts:
            counts[key] += 1
    return counts


def _maintenance_recency(
    rows: list[dict[str, Any]], as_of: datetime
) -> dict[str, float]:
    last_by_comp: dict[str, datetime] = {}
    for r in rows:
        last_by_comp[r["comp"]] = r["datetime"]
    out: dict[str, float] = {}
    for comp in COMPONENTS:
        last = last_by_comp.get(comp)
        if last is None:
            out[f"{comp}_hours_since_maint"] = NEVER_MAINTAINED_HOURS
        else:
            out[f"{comp}_hours_since_maint"] = (as_of - last).total_seconds() / 3600
    return out


def _any_error_in_last_24h(
    rows: list[dict[str, Any]], as_of: datetime
) -> bool:
    cutoff = as_of - timedelta(hours=24)
    return any(cutoff < r["datetime"] <= as_of for r in rows)


def extract_features(
    machine_id: int, as_of: datetime, *, allow_eval: bool = False
) -> dict[str, float]:
    """Return the full feature dict (sensor stats + error counts + maint recency)."""
    telem = read_telemetry(machine_id, as_of, allow_eval=allow_eval)
    errs = read_errors(machine_id, as_of, allow_eval=allow_eval)
    maint = read_maintenance_history(machine_id, as_of, allow_eval=allow_eval)

    feats: dict[str, float] = {}
    feats.update(_telemetry_stats(telem, as_of))
    feats.update(_error_counts(errs, as_of))
    feats.update(_maintenance_recency(maint, as_of))
    return feats


FEATURE_NAMES: tuple[str, ...] = tuple(
    [f"{s}_mean" for s in SENSOR_FIELDS]
    + [f"{s}_std" for s in SENSOR_FIELDS]
    + [f"{s}_slope" for s in SENSOR_FIELDS]
    + [f"{e}_count_48h" for e in ERROR_IDS]
    + [f"{c}_hours_since_maint" for c in COMPONENTS]
)


def feature_vector(feats: dict[str, float]) -> list[float]:
    """Materialise features into the canonical FEATURE_NAMES order."""
    return [feats[name] for name in FEATURE_NAMES]


def b4_signals(
    machine_id: int, as_of: datetime, *, allow_eval: bool = False
) -> tuple[dict[str, float], bool]:
    """B4-specific: return (sensor_means_last_24h, any_error_in_last_24h).

    Returns the 24h-mean per sensor and a flag for recent error presence.
    """
    telem = read_telemetry(machine_id, as_of, allow_eval=allow_eval)
    errs = read_errors(machine_id, as_of, allow_eval=allow_eval)

    cutoff = as_of - timedelta(hours=24)
    window = [r for r in telem if cutoff < r["datetime"] <= as_of]
    means: dict[str, float] = {}
    for sensor in SENSOR_FIELDS:
        vals = [r[sensor] for r in window]
        means[sensor] = statistics.mean(vals) if vals else 0.0

    return means, _any_error_in_last_24h(errs, as_of)
