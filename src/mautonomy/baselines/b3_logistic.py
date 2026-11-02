"""B3 — logistic regression on dev machines.

Features (via as_of-aware read tools only):
  - 24h sensor mean / std / slope for each of volt/rotate/pressure/vibration
  - 48h error count per errorID (error1..5)
  - hours since last maintenance per component (comp1..4)

Target: at least one failure in (as_of, as_of + 48h] on this machine.

Training and threshold selection use dev machines only. The fitted
parameters (weights, intercept, feature mean/std for standardisation,
chosen probability threshold) are persisted to
``data/models/b3_lr.json``. Inference loads that file and uses stdlib
only — no numpy/sklearn needed at prediction time.

Confidence returned is the predicted probability. The policy map:
  prob >= threshold → SCHEDULE_PM
  prob <  threshold → NO_ACTION
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mautonomy.baselines.base import BaselineOutput
from mautonomy.baselines.features import (
    FEATURE_NAMES,
    extract_features,
    feature_vector,
)

_MODELS_DIR = Path(__file__).parents[3] / "data" / "models"
_MODEL_PATH = _MODELS_DIR / "b3_lr.json"

_CACHE: dict[str, Any] | None = None


def _load_model() -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _MODEL_PATH.exists():
        raise FileNotFoundError(
            f"B3 model not found at {_MODEL_PATH}. "
            "Run `python -m mautonomy.baselines.b3_logistic --fit` first."
        )
    _CACHE = json.loads(_MODEL_PATH.read_text())
    return _CACHE


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _predict_proba(x: list[float], model: dict[str, Any]) -> float:
    means = model["feature_mean"]
    stds = model["feature_std"]
    weights = model["weights"]
    intercept = model["intercept"]
    z = intercept
    for xi, mu, sd, w in zip(x, means, stds, weights):
        xs = (xi - mu) / sd if sd > 0 else 0.0
        z += w * xs
    return _sigmoid(z)


def predict(
    machine_id: int, as_of: datetime, *, allow_eval: bool = False
) -> BaselineOutput:
    model = _load_model()
    feats = extract_features(machine_id, as_of, allow_eval=allow_eval)
    x = feature_vector(feats)
    prob = _predict_proba(x, model)
    action = "SCHEDULE_PM" if prob >= model["threshold"] else "NO_ACTION"
    return BaselineOutput(action=action, confidence=round(prob, 6))


# ----- fitting (dev only) ---------------------------------------------------


_PREDICT_HORIZON_HOURS = 48
_TRAIN_SAMPLES_PER_MACHINE = 60
_TRAIN_SEED = 42
_LR = 0.05
_EPOCHS = 400
_L2 = 0.01


def _build_training_set(
    seed: int = _TRAIN_SEED,
) -> tuple[list[list[float]], list[int]]:
    """Draw (feature_vector, label) pairs from dev machines.

    For each dev machine we take all failure times (positives: as_of 1-48h
    before a failure) and a sampled set of negatives (as_of ≥ 48h before any
    failure and ≥ 48h after any failure).
    """
    import csv

    repo = Path(__file__).parents[3]
    splits = json.loads((repo / "data" / "splits.json").read_text())
    dev = sorted(splits["dev"])

    failures_by_machine: dict[int, list[datetime]] = {}
    with (repo / "data" / "raw" / "PdM_failures.csv").open() as f:
        for r in csv.DictReader(f):
            mid = int(r["machineID"])
            if mid in dev:
                failures_by_machine.setdefault(mid, []).append(
                    datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
                )

    telemetry_times: dict[int, list[datetime]] = {}
    with (repo / "data" / "raw" / "PdM_telemetry.csv").open() as f:
        for r in csv.DictReader(f):
            mid = int(r["machineID"])
            if mid in dev:
                telemetry_times.setdefault(mid, []).append(
                    datetime.strptime(r["datetime"], "%Y-%m-%d %H:%M:%S")
                )
    for v in telemetry_times.values():
        v.sort()

    rng = random.Random(seed)
    X: list[list[float]] = []
    y: list[int] = []

    for mid in dev:
        times = telemetry_times.get(mid, [])
        if not times:
            continue
        fails = sorted(failures_by_machine.get(mid, []))

        # Positives: one as_of per failure, 1-47h before failure
        pos: list[datetime] = []
        for ft in fails:
            offset_h = rng.randint(1, 47)
            as_of = ft - timedelta(hours=offset_h)
            # Need at least 30 days of telemetry history
            if as_of >= times[0] + timedelta(days=1):
                pos.append(as_of)

        # Negatives: random telemetry hours ≥ 48h from any failure
        neg_pool = [
            t for t in times
            if t >= times[0] + timedelta(days=1)
            and all(abs((t - ft).total_seconds()) >= 48 * 3600 for ft in fails)
        ]
        rng.shuffle(neg_pool)
        n_neg = min(_TRAIN_SAMPLES_PER_MACHINE, len(neg_pool))
        neg = neg_pool[:n_neg]

        for as_of in pos:
            feats = extract_features(mid, as_of, allow_eval=False)
            X.append(feature_vector(feats))
            y.append(1)
        for as_of in neg:
            feats = extract_features(mid, as_of, allow_eval=False)
            X.append(feature_vector(feats))
            y.append(0)

    return X, y


def _standardise(
    X: list[list[float]],
) -> tuple[list[list[float]], list[float], list[float]]:
    n = len(X)
    d = len(X[0])
    means = [sum(row[j] for row in X) / n for j in range(d)]
    stds = []
    for j in range(d):
        var = sum((row[j] - means[j]) ** 2 for row in X) / n
        stds.append(math.sqrt(var) if var > 0 else 1.0)
    Xs = [[(row[j] - means[j]) / stds[j] for j in range(d)] for row in X]
    return Xs, means, stds


def _fit_logistic(
    Xs: list[list[float]], y: list[int], *, seed: int = _TRAIN_SEED,
) -> tuple[list[float], float]:
    n = len(Xs)
    d = len(Xs[0])
    rng = random.Random(seed)
    w = [rng.gauss(0.0, 0.01) for _ in range(d)]
    b = 0.0

    for _ in range(_EPOCHS):
        gw = [0.0] * d
        gb = 0.0
        for xi, yi in zip(Xs, y):
            z = b + sum(wj * xj for wj, xj in zip(w, xi))
            p = _sigmoid(z)
            err = p - yi
            for j in range(d):
                gw[j] += err * xi[j]
            gb += err
        for j in range(d):
            gw[j] = gw[j] / n + _L2 * w[j]
            w[j] -= _LR * gw[j]
        b -= _LR * (gb / n)
    return w, b


def _choose_threshold(
    probs: list[float], y: list[int]
) -> float:
    """Pick the probability threshold that maximises Youden's J on dev."""
    candidates = sorted(set(round(p, 3) for p in probs))
    best_t = 0.5
    best_j = -1.0
    pos = sum(y)
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return 0.5
    for t in candidates:
        tp = sum(1 for p, yi in zip(probs, y) if p >= t and yi == 1)
        fp = sum(1 for p, yi in zip(probs, y) if p >= t and yi == 0)
        tpr = tp / pos
        fpr = fp / neg
        j = tpr - fpr
        if j > best_j:
            best_j = j
            best_t = t
    return best_t


def _fit_and_save() -> dict[str, Any]:
    print("building dev training set...")
    X, y = _build_training_set()
    print(f"  n_samples={len(X)}  pos={sum(y)}  neg={len(y) - sum(y)}")

    Xs, means, stds = _standardise(X)
    print("fitting logistic regression...")
    w, b = _fit_logistic(Xs, y)

    probs = [_sigmoid(b + sum(wj * xj for wj, xj in zip(w, row))) for row in Xs]
    t = _choose_threshold(probs, y)
    print(f"  chosen threshold (Youden's J on dev) = {t:.3f}")

    model = {
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": means,
        "feature_std": stds,
        "weights": w,
        "intercept": b,
        "threshold": t,
        "train_seed": _TRAIN_SEED,
        "n_samples": len(X),
        "n_positive": sum(y),
    }
    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    _MODEL_PATH.write_text(json.dumps(model, indent=2))
    print(f"wrote {_MODEL_PATH}")
    return model


if __name__ == "__main__":
    import sys
    if "--fit" in sys.argv:
        _fit_and_save()
    else:
        print("usage: python -m mautonomy.baselines.b3_logistic --fit")
