from __future__ import annotations
import csv
import json
import random
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_MACHINES_CSV = _REPO_ROOT / "data" / "raw" / "PdM_machines.csv"
_SPLITS_JSON = _REPO_ROOT / "data" / "splits.json"


def _load_machines() -> list[dict]:
    with _MACHINES_CSV.open() as f:
        reader = csv.DictReader(f)
        return [
            {"machineID": int(row["machineID"]), "model": row["model"].strip('"')}
            for row in reader
        ]


def make_split(seed: int = 42, train_ratio: float = 0.7) -> dict:
    machines = _load_machines()
    by_model: dict[str, list[int]] = {}
    for m in machines:
        by_model.setdefault(m["model"], []).append(m["machineID"])

    # Track per-model assignments to allow model-aware correction later.
    machine_model: dict[int, str] = {m["machineID"]: m["model"] for m in machines}
    model_sizes: dict[str, int] = {model: len(ids) for model, ids in by_model.items()}

    rng = random.Random(seed)
    dev: list[int] = []
    eval_: list[int] = []
    for model_ids in by_model.values():
        ids = sorted(model_ids)
        rng.shuffle(ids)
        split_at = round(len(ids) * train_ratio)
        dev.extend(ids[:split_at])
        eval_.extend(ids[split_at:])

    # Correct global totals to exactly round(n_total * train_ratio) dev machines.
    # Rounding at the per-model level can leave the total off by ±1.
    # To preserve stratification, move machines from the largest model first.
    target_dev = round(len(machines) * train_ratio)
    dev_set = set(dev)
    eval_set = set(eval_)

    while len(dev_set) < target_dev:
        # Move one machine from eval to dev: pick from largest model to minimise ratio shift.
        best = max(eval_set, key=lambda mid: model_sizes[machine_model[mid]])
        eval_set.remove(best)
        dev_set.add(best)

    while len(dev_set) > target_dev:
        # Move one machine from dev to eval: pick from largest model.
        best = max(dev_set, key=lambda mid: model_sizes[machine_model[mid]])
        dev_set.remove(best)
        eval_set.add(best)

    return {"seed": seed, "dev": sorted(dev_set), "eval": sorted(eval_set)}


def load_dev() -> list[int]:
    manifest = json.loads(_SPLITS_JSON.read_text())
    return manifest["dev"]


def load_eval(*, allow_eval: bool = False) -> list[int]:
    if not allow_eval:
        raise RuntimeError(
            "Eval machines are off-limits during development. "
            "Pass allow_eval=True only for final evaluation runs."
        )
    manifest = json.loads(_SPLITS_JSON.read_text())
    return manifest["eval"]
