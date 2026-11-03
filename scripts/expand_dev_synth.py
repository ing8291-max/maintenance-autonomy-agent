"""Expand data/synth/ to ~20 dev-split scenarios (ADR 0011 §1.3).

Call :func:`generate_easy_scenarios` with multiple seeds; each seed yields
up to 5 scenarios (normal / pre48h / pre24h / F2-act / F2-abstain) picked
from different dev machines. Idempotent: files that already exist on disk
are overwritten with identical bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mautonomy.scenarios.generator import generate_easy_scenarios
from mautonomy.scenarios.loader import write_scenario


_SYNTH_DIR = Path(__file__).resolve().parents[1] / "data" / "synth"
_SEEDS = (42, 43, 44, 45)


def main() -> int:
    _SYNTH_DIR.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    total = 0
    for seed in _SEEDS:
        for sc in generate_easy_scenarios(seed=seed):
            if sc.scenario_id in seen:
                continue
            seen.add(sc.scenario_id)
            write_scenario(sc, _SYNTH_DIR / f"{sc.scenario_id}.yaml")
            total += 1
    print(f"wrote {total} scenarios to {_SYNTH_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
