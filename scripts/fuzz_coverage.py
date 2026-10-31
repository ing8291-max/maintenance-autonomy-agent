"""Fuzz coverage counter — runs TOTAL random actions and records per-rule hit counts."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from mautonomy.policy.gate import evaluate
from mautonomy.policy.rules import ActionType, ProposedAction, RULES, Verdict

TOTAL = 100_000
SEED = 0

_KNOWN = [a.value for a in ActionType]
_UNKNOWN_POOL = ["", "STOP_NOW", "INVALID", "restart", "오류", "???", "halt"]

hits: dict[str, int] = {rule.id: 0 for rule in RULES}
hits["unknown_blocked"] = 0
by_verdict: dict[str, int] = {v.value: 0 for v in Verdict}

rng = random.Random(SEED)
action_pool = _KNOWN * 4 + _UNKNOWN_POOL

for _ in range(TOTAL):
    action_type = rng.choice(action_pool)
    reason_chars = "abcdefghijklmnopqrstuvwxyz 운영자 설비 정비 점검 중단"
    reason = "".join(rng.choices(reason_chars, k=rng.randint(0, 40)))
    result = evaluate(ProposedAction(action_type=action_type, reason=reason))
    by_verdict[result.verdict.value] += 1
    if result.triggered_rules:
        for rid in result.triggered_rules:
            hits[rid] += 1
    elif result.verdict == Verdict.BLOCKED:
        hits["unknown_blocked"] += 1

output = {
    "total_runs": TOTAL,
    "seed": SEED,
    "hits_per_rule": hits,
    "by_verdict": by_verdict,
}

out_path = Path(__file__).parents[1] / "reports" / "fuzz-coverage.json"
out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False))

print(f"{'Rule':<32} {'Hits':>8}  {'Hit%':>6}")
print("-" * 52)
for rule_id, count in hits.items():
    print(f"{rule_id:<32} {count:>8}  {count / TOTAL * 100:>5.1f}%")
print("-" * 52)
print(f"\nVerdicts over {TOTAL:,} runs:")
for verdict, count in by_verdict.items():
    print(f"  {verdict:<14} {count:>8}  {count / TOTAL * 100:>5.1f}%")
print(f"\nWrote {out_path}")
