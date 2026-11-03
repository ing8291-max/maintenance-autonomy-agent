"""Run every scenario in data/synth/ once at rep=1 and dump the raw
decisions to a JSON-lines file.

The interface-change comparison script (``compare_dev_runs.py``) reads
these JSONL files, so each row needs enough fields to match a trace
line to its ground truth without re-opening YAMLs. One row per scenario.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mautonomy.agent import DEFAULT_CONFIG, MuseSparkAdapter, evaluate_scenario
from mautonomy.scenarios.loader import load_scenario


_SYNTH_DIR = Path(__file__).resolve().parents[1] / "data" / "synth"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prompt-version", default="v1")
    args = parser.parse_args()

    paths = sorted(_SYNTH_DIR.glob("*.yaml"))
    adapter = MuseSparkAdapter()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with args.out.open("w", encoding="utf-8") as fh:
        for path in paths:
            scenario = load_scenario(path)
            result = evaluate_scenario(
                scenario,
                adapter,
                rep=1,
                config=DEFAULT_CONFIG,
                prompt_version=args.prompt_version,
            )
            fh.write(
                json.dumps(
                    {
                        "scenario_id": result.scenario_id,
                        "ground_truth": result.ground_truth,
                        "pair_id": scenario.pair_id,
                        "pair_role": scenario.pair_role,
                        "a1_action": result.a1_action,
                        "a2_verdict": result.a2_verdict,
                        "format_errors": result.format_errors,
                        "steps_used": result.steps_used,
                        "cost_usd": result.cost_usd,
                        "tokens": result.tokens,
                    }
                )
                + "\n"
            )
            print(
                f"{result.scenario_id:<30} truth={result.ground_truth:<13} "
                f"a1={result.a1_action:<13} a2={result.a2_verdict:<12} "
                f"fmt_err={result.format_errors} cost=${result.cost_usd:.4f}"
            )
    print(f"wrote {len(paths)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
