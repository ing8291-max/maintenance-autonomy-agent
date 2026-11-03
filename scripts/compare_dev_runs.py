"""Compare two trace JSONL files produced by record_dev_runs.py.

Prints a per-scenario before/after row (truth, A1 before, A1 after,
cost before/after, format errors before/after) plus a footer with
summary counts, F2 pair accuracy, and total cost.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


_HIT = "✓"
_MISS = "✗"


def _load(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        out[row["scenario_id"]] = row
    return out


def _pair_accuracy(rows: dict[str, dict]) -> tuple[int, int]:
    pairs: dict[str, list[dict]] = {}
    for row in rows.values():
        pid = row.get("pair_id")
        if pid is None:
            continue
        pairs.setdefault(pid, []).append(row)
    hits = sum(
        1
        for members in pairs.values()
        if all(m["a1_action"] == m["ground_truth"] for m in members)
    )
    return hits, len(pairs)


def _print_table(before: dict[str, dict], after: dict[str, dict]) -> None:
    cols = (
        ("scenario", 30),
        ("truth", 13),
        ("A1 before", 14),
        ("A1 after", 14),
        ("hit before", 11),
        ("hit after", 10),
        ("fmt b/a", 8),
        ("cost before", 11),
        ("cost after", 10),
    )
    header = "  ".join(name.ljust(w) for name, w in cols)
    print(header)
    print("  ".join("-" * w for _, w in cols))
    for sid in sorted(before):
        b = before[sid]
        a = after.get(sid)
        if a is None:
            continue
        hit_b = _HIT if b["a1_action"] == b["ground_truth"] else _MISS
        hit_a = _HIT if a["a1_action"] == a["ground_truth"] else _MISS
        row = (
            sid,
            b["ground_truth"],
            b["a1_action"],
            a["a1_action"],
            hit_b,
            hit_a,
            f'{b["format_errors"]}/{a["format_errors"]}',
            f'${b["cost_usd"]:.4f}',
            f'${a["cost_usd"]:.4f}',
        )
        print("  ".join(str(v).ljust(w) for v, (_, w) in zip(row, cols)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, default=Path("logs/trace-before.jsonl"))
    parser.add_argument("--after", type=Path, default=Path("logs/trace-after.jsonl"))
    args = parser.parse_args()

    before = _load(args.before)
    after = _load(args.after)
    _print_table(before, after)

    def _agg(rows: dict[str, dict]) -> dict[str, float | int]:
        correct = sum(1 for r in rows.values() if r["a1_action"] == r["ground_truth"])
        fmt = sum(r["format_errors"] for r in rows.values())
        cost = sum(r["cost_usd"] for r in rows.values())
        return {"n": len(rows), "correct": correct, "fmt": fmt, "cost": cost}

    bagg = _agg(before)
    aagg = _agg(after)
    bpair, bpair_n = _pair_accuracy(before)
    apair, apair_n = _pair_accuracy(after)

    print()
    print(f"A1 accuracy:    before {bagg['correct']}/{bagg['n']}   after {aagg['correct']}/{aagg['n']}")
    print(f"F2 pair hits:   before {bpair}/{bpair_n}   after {apair}/{apair_n}")
    print(f"format errors:  before {bagg['fmt']}        after {aagg['fmt']}")
    print(f"total cost:     before ${bagg['cost']:.4f}  after ${aagg['cost']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
