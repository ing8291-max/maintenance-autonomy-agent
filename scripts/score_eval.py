"""Compute all ADR 0013 §4.2 metrics and write reports/eval-v1.md.

Reads  : logs/eval-a1a2.jsonl, logs/eval-baselines.jsonl
Writes : reports/eval-v1.md
Commit : 2f7c39a (run_eval.py + ADR 0013 note)

Frozen items (not touched): src/, prompts/, config/  (eval-freeze-v1).
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from mautonomy.scenarios.generator import generate_eval_scenarios
from mautonomy.scoring import (
    ScoredRun,
    executed_of,
    load_config,
    score_ask_f1,
    score_cost,
    score_ontime,
    score_pair_accuracy,
)

_A1A2_LOG = _REPO / "logs" / "eval-a1a2.jsonl"
_BASE_LOG = _REPO / "logs" / "eval-baselines.jsonl"
_REPORT = _REPO / "reports" / "eval-v1.md"
_SCORING_COMMIT = "2f7c39a092ad22942fd1e732f75144ee5c045fe0"
_EVAL_COMMIT = "b006f6db30b7e3974d3f7943d51409696ec3e391"
_N_BOOT = 2000
_BOOT_SEED = 42
_WEIGHT_SETS = ["low_miss", "default", "high_miss"]

# Ground-truth → ideal executed category (ADR 0012)
_GT_TO_EXEC: dict[str, str] = {
    "NO_ACTION": "NOTHING",
    "CHECK_SENSOR": "CHECK_SENSOR",
    "SCHEDULE_PM": "SCHEDULE_PM",
    "ESCALATE": "ASK_HUMAN",
    "REQUEST_STOP": "REQUEST_STOP",
}

# ── data loading ───────────────────────────────────────────────────────────────

def _load_log(path: Path) -> list[dict]:
    rows = []
    for raw in path.read_text().splitlines():
        obj = json.loads(raw)
        if obj.get("type") in ("header", "summary", "error"):
            continue
        rows.append(obj)
    return rows


def _load_a1a2() -> list[dict]:
    return _load_log(_A1A2_LOG)


def _load_baselines() -> list[dict]:
    return _load_log(_BASE_LOG)


# ── math helpers ───────────────────────────────────────────────────────────────

def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = p * (len(xs) - 1)
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (i - lo) * (xs[hi] - xs[lo])


def _auroc(scores: list[float], labels: list[int]) -> float:
    pos = [s for s, l in zip(scores, labels) if l == 1]
    neg = [s for s, l in zip(scores, labels) if l == 0]
    if not pos or not neg:
        return float("nan")
    concordant = sum(1.0 for p in pos for n in neg if p > n)
    ties = sum(0.5 for p in pos for n in neg if p == n)
    return (concordant + ties) / (len(pos) * len(neg))


def _ece(confs: list[float], corrects: list[int], n_bins: int = 10) -> float:
    n = len(confs)
    if n == 0:
        return float("nan")
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for c, a in zip(confs, corrects):
        idx = min(int(c * n_bins), n_bins - 1)
        bins[idx].append((c, a))
    ece = 0.0
    for b in bins:
        if not b:
            continue
        ece += (len(b) / n) * abs(
            sum(x[0] for x in b) / len(b) - sum(x[1] for x in b) / len(b)
        )
    return ece


def _brier(confs: list[float], corrects: list[int]) -> float:
    if not confs:
        return float("nan")
    return sum((c - a) ** 2 for c, a in zip(confs, corrects)) / len(confs)


# ── scenario helpers ───────────────────────────────────────────────────────────

def _build_scenario_lookup(scenarios):
    return {s.scenario_id: s for s in scenarios}


def _build_units(scenarios):
    """Bootstrap units: pair scenarios are kept together."""
    pair_ids: dict[str, list[str]] = {}
    singletons: list[str] = []
    for sc in scenarios:
        if sc.pair_id:
            pair_ids.setdefault(sc.pair_id, []).append(sc.scenario_id)
        else:
            singletons.append(sc.scenario_id)
    units = [(pid, sids) for pid, sids in pair_ids.items()] + [
        (sid, [sid]) for sid in singletons
    ]
    return units  # list of (name, [scenario_ids])


def _make_scored_runs(
    lines: list[dict],
    scenario_by_id: dict,
    a2_override: str | None = None,
) -> list[ScoredRun]:
    result = []
    for line in lines:
        sid = line["scenario_id"]
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        a1 = line.get("a1_action") or line.get("action", "INVALID")
        a2 = a2_override if a2_override is not None else line.get("a2_verdict", "ALLOW")
        result.append(ScoredRun(scenario=sc, a1_action=a1, a2_verdict=a2))
    return result


# ── per-run metric bundle ──────────────────────────────────────────────────────

def _all_metrics(
    runs: list[ScoredRun],
    cfg,
    weight_set: str = "default",
) -> dict[str, float | None]:
    if not runs:
        return {k: None for k in ("cost_mean", "pair_acc", "ask_f1", "ontime", "accuracy")}
    cost_r = score_cost(runs, config=cfg, weight_set=weight_set)
    ask_r = score_ask_f1(runs)
    pair_acc = score_pair_accuracy(runs)
    ontime = score_ontime(runs)
    correct = sum(
        1 for r in runs
        if executed_of(r.a1_action, r.a2_verdict) == _GT_TO_EXEC.get(r.scenario.ground_truth.action, "")
    )
    accuracy = correct / len(runs) if runs else 0.0
    return {
        "cost_mean": cost_r["mean"],
        "pair_acc": pair_acc,
        "ask_f1": ask_r["f1"],
        "ask_precision": ask_r["precision"],
        "ask_recall": ask_r["recall"],
        "ontime": ontime,
        "accuracy": accuracy,
        "n": len(runs),
    }


def _rep_avg_metrics(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
    cfg,
    weight_set: str = "default",
    a2_override: str | None = None,
) -> dict[str, float | None]:
    """Compute metrics for each rep, return average over 5 reps."""
    per_rep: dict[str, list[float]] = defaultdict(list)
    for rep in range(1, 6):
        rep_lines = [l for l in a1a2_lines if l["rep"] == rep]
        runs = _make_scored_runs(rep_lines, scenario_by_id, a2_override)
        m = _all_metrics(runs, cfg, weight_set)
        for k, v in m.items():
            if isinstance(v, (int, float)) and not math.isnan(float(v)):
                per_rep[k].append(float(v))
    return {k: sum(vs) / len(vs) if vs else None for k, vs in per_rep.items()}


# ── bootstrap CI ──────────────────────────────────────────────────────────────

def _bootstrap_ci(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
    units: list,
    cfg,
    weight_set: str = "default",
    a2_override: str | None = None,
    n_boot: int = _N_BOOT,
    seed: int = _BOOT_SEED,
) -> dict[str, tuple[float, float]]:
    rng = random.Random(seed)
    by_rep: dict[int, list[dict]] = defaultdict(list)
    for line in a1a2_lines:
        by_rep[line["rep"]].append(line)

    estimates: dict[str, list[float]] = defaultdict(list)

    for _ in range(n_boot):
        sample = rng.choices(units, k=len(units))
        sample_sids = {sid for _, sids in sample for sid in sids}

        rep_vals: dict[str, list[float]] = defaultdict(list)
        for rep in range(1, 6):
            rep_lines = [l for l in by_rep[rep] if l["scenario_id"] in sample_sids]
            if not rep_lines:
                continue
            runs = _make_scored_runs(rep_lines, scenario_by_id, a2_override)
            m = _all_metrics(runs, cfg, weight_set)
            for k, v in m.items():
                if isinstance(v, (int, float)) and not math.isnan(float(v)):
                    rep_vals[k].append(float(v))

        for k, vs in rep_vals.items():
            if vs:
                estimates[k].append(sum(vs) / len(vs))

    ci: dict[str, tuple[float, float]] = {}
    for k, vals in estimates.items():
        if vals:
            ci[k] = (_pct(vals, 0.025), _pct(vals, 0.975))
        else:
            ci[k] = (float("nan"), float("nan"))
    return ci


# ── type-wise breakdown ────────────────────────────────────────────────────────

def _type_of(sid: str) -> str:
    if "normal" in sid:
        return "normal"
    if "pre48h" in sid:
        return "pre48h"
    if "pre24h" in sid:
        return "pre24h"
    if "f2-act" in sid:
        return "f2-act"
    if "f2-abstain" in sid:
        return "f2-abstain"
    return "unknown"


def _typewise_accuracy(a1a2_lines: list[dict], scenario_by_id: dict) -> dict:
    # Aggregate per scenario: modal action across 5 reps
    by_sid: dict[str, list[str]] = defaultdict(list)
    by_sid_conf: dict[str, list[float]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])
        if line.get("a1_confidence") is not None:
            by_sid_conf[line["scenario_id"]].append(float(line["a1_confidence"]))

    typewise: dict[str, dict] = {}
    for sid, actions in by_sid.items():
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        modal = Counter(actions).most_common(1)[0][0]
        gt = sc.ground_truth.action
        sc_type = _type_of(sid)
        if sc_type not in typewise:
            typewise[sc_type] = {"total": 0, "correct": 0, "missed_failure": 0}
        typewise[sc_type]["total"] += 1
        if modal == gt:
            typewise[sc_type]["correct"] += 1
        # missed failure: gt is SCHEDULE_PM or ESCALATE and executed = NOTHING or CHECK_SENSOR
        if gt in ("SCHEDULE_PM", "ESCALATE"):
            exc = executed_of(modal, "ALLOW")
            if exc in ("NOTHING", "CHECK_SENSOR"):
                typewise[sc_type]["missed_failure"] += 1
    return typewise


# ── rep agreement ──────────────────────────────────────────────────────────────

def _rep_agreement(a1a2_lines: list[dict]) -> dict:
    by_sid: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])
    n = len(by_sid)
    full_agree = sum(1 for actions in by_sid.values() if len(set(actions)) == 1)
    # fraction of reps matching modal per scenario
    modal_match_fracs = []
    for actions in by_sid.values():
        modal = Counter(actions).most_common(1)[0][0]
        modal_match_fracs.append(sum(1 for a in actions if a == modal) / len(actions))
    avg_consistency = sum(modal_match_fracs) / len(modal_match_fracs) if modal_match_fracs else 0.0
    return {
        "n_scenarios": n,
        "full_agree": full_agree,
        "full_agree_pct": full_agree / n if n else 0.0,
        "avg_consistency": avg_consistency,
    }


# ── failure classification ─────────────────────────────────────────────────────

def _failure_classification(a1a2_lines: list[dict], scenario_by_id: dict) -> dict:
    """Classify prediction failures per (scenario, rep) automatically."""
    cats: dict[str, int] = defaultdict(int)
    failure_count = 0
    for line in a1a2_lines:
        sc = scenario_by_id.get(line["scenario_id"])
        if sc is None:
            continue
        a1 = line["a1_action"]
        gt = sc.ground_truth.action
        if a1 == gt:
            cats["correct"] += 1
            continue
        failure_count += 1
        if a1 == "INVALID":
            cats["model"] += 1
        elif line.get("format_errors", 0) > 0:
            cats["model"] += 1
        elif line.get("reached_step_cap", False):
            cats["model"] += 1  # tentative; requires manual review
        else:
            cats["unclassified"] += 1
    cats["total_failures"] = failure_count
    return dict(cats)


# ── AUROC / ECE / Brier ────────────────────────────────────────────────────────

def _calibration_metrics(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
) -> dict[str, float]:
    """Aggregate over scenarios (modal action, avg confidence) for A1."""
    by_sid: dict[str, list] = defaultdict(list)
    by_sid_conf: dict[str, list] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])
        c = line.get("a1_confidence")
        if c is not None:
            by_sid_conf[line["scenario_id"]].append(float(c))

    auroc_scores: list[float] = []
    auroc_labels: list[int] = []
    ece_confs: list[float] = []
    ece_corrects: list[int] = []
    brier_confs: list[float] = []
    brier_corrects: list[int] = []

    for sid, actions in by_sid.items():
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        modal = Counter(actions).most_common(1)[0][0]
        gt = sc.ground_truth.action
        confs = by_sid_conf.get(sid, [])
        avg_conf = sum(confs) / len(confs) if confs else 0.5

        # AUROC: failure detection (SCHEDULE_PM or ESCALATE needed)
        label = 1 if gt in ("SCHEDULE_PM", "ESCALATE") else 0
        # Score: high = "model thinks failure likely"
        if modal in ("SCHEDULE_PM", "ESCALATE", "REQUEST_STOP"):
            auroc_score = avg_conf
        else:
            auroc_score = 1.0 - avg_conf
        auroc_scores.append(auroc_score)
        auroc_labels.append(label)

        # ECE / Brier: confidence vs correctness
        correct = 1 if modal == gt else 0
        ece_confs.append(avg_conf)
        ece_corrects.append(correct)
        brier_confs.append(avg_conf)
        brier_corrects.append(correct)

    return {
        "auroc": _auroc(auroc_scores, auroc_labels),
        "ece": _ece(ece_confs, ece_corrects),
        "brier": _brier(brier_confs, brier_corrects),
    }


def _b3_calibration_metrics(
    base_lines: list[dict],
    scenario_by_id: dict,
) -> dict[str, float]:
    """AUROC / ECE / Brier for B3 (confidence = logistic probability)."""
    b3_lines = [l for l in base_lines if l.get("baseline") == "B3"]
    scores: list[float] = []
    labels: list[int] = []
    ece_confs: list[float] = []
    ece_corrects: list[int] = []
    brier_confs: list[float] = []
    brier_corrects: list[int] = []

    for line in b3_lines:
        sc = scenario_by_id.get(line["scenario_id"])
        if sc is None:
            continue
        gt = sc.ground_truth.action
        conf = float(line["confidence"]) if line.get("confidence") is not None else 0.5
        label = 1 if gt in ("SCHEDULE_PM", "ESCALATE") else 0
        scores.append(conf)
        labels.append(label)
        correct = 1 if line["action"] == gt else 0
        ece_confs.append(conf)
        ece_corrects.append(correct)
        brier_confs.append(conf)
        brier_corrects.append(correct)

    return {
        "auroc": _auroc(scores, labels),
        "ece": _ece(ece_confs, ece_corrects),
        "brier": _brier(brier_confs, brier_corrects),
    }


# ── git helpers ────────────────────────────────────────────────────────────────

def _git(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_REPO)] + cmd, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "?"


# ── report formatting ──────────────────────────────────────────────────────────

def _fmt(v: float | None, decimals: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:.{decimals}f}"


def _fmt_ci(lo: float, hi: float, decimals: int = 3) -> str:
    if math.isnan(lo) or math.isnan(hi):
        return "—"
    return f"[{lo:.{decimals}f}, {hi:.{decimals}f}]"


def _pct_str(v: float | None) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:.1f}%"


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), max((len(r[i]) for r in rows), default=0)) for i, h in enumerate(headers)]
    sep = " | "
    lines = []
    lines.append(sep.join(h.ljust(w) for h, w in zip(headers, widths)))
    lines.append(sep.join("-" * w for w in widths))
    for row in rows:
        lines.append(sep.join(cell.ljust(w) for cell, w in zip(row, widths)))
    return "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("loading data …")
    a1a2_lines = _load_a1a2()
    base_lines = _load_baselines()
    scenarios = generate_eval_scenarios(seed=42)
    scenario_by_id = _build_scenario_lookup(scenarios)
    units = _build_units(scenarios)
    cfg = load_config()

    print(f"  {len(a1a2_lines)} A1/A2 runs, {len(base_lines)} baseline runs, "
          f"{len(scenarios)} scenarios, {len(units)} bootstrap units")

    # ── step 0: pre-flight ─────────────────────────────────────────────────────
    diff_out = _git(["diff", "eval-freeze-v1", "--", "src", "prompts", "config"])
    head_hash = _git(["rev-parse", "HEAD"])
    type_counts: dict[str, set] = defaultdict(set)
    for sc in scenarios:
        type_counts[_type_of(sc.scenario_id)].add(sc.scenario_id)

    # ── step 1: six-system scorecard ───────────────────────────────────────────
    print("computing baseline metrics …")
    baseline_rows = {}
    for name in ("B1", "B2", "B3", "B4"):
        bl_lines = [l for l in base_lines if l["baseline"] == name]
        runs = _make_scored_runs(
            [{**l, "a1_action": l["action"], "a2_verdict": "ALLOW"} for l in bl_lines],
            scenario_by_id,
        )
        baseline_rows[name] = _all_metrics(runs, cfg)

    print("computing A1/A2 5-rep averages …")
    a1_avg = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, a2_override="ALLOW")
    a2_avg = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, a2_override=None)

    print(f"bootstrapping CI (n={_N_BOOT}) …")
    a1_ci = _bootstrap_ci(a1a2_lines, scenario_by_id, units, cfg, a2_override="ALLOW")
    a2_ci = _bootstrap_ci(a1a2_lines, scenario_by_id, units, cfg, a2_override=None)

    # ── step 2: A2 - A1 cost difference ───────────────────────────────────────
    a1_costs: dict[str, float] = {}
    a2_costs: dict[str, float] = {}
    a1_cost_ci: dict[str, tuple] = {}
    a2_cost_ci: dict[str, tuple] = {}
    diff_ci: dict[str, tuple] = {}

    for ws in _WEIGHT_SETS:
        a1_costs[ws] = (_rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, weight_set=ws, a2_override="ALLOW") or {}).get("cost_mean", float("nan"))
        a2_costs[ws] = (_rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, weight_set=ws, a2_override=None) or {}).get("cost_mean", float("nan"))

        ci1 = _bootstrap_ci(a1a2_lines, scenario_by_id, units, cfg, weight_set=ws, a2_override="ALLOW")
        ci2 = _bootstrap_ci(a1a2_lines, scenario_by_id, units, cfg, weight_set=ws, a2_override=None)
        a1_cost_ci[ws] = ci1.get("cost_mean", (float("nan"), float("nan")))
        a2_cost_ci[ws] = ci2.get("cost_mean", (float("nan"), float("nan")))

        # Bootstrap the difference directly
        rng = random.Random(_BOOT_SEED + 1)
        by_rep: dict[int, list[dict]] = defaultdict(list)
        for line in a1a2_lines:
            by_rep[line["rep"]].append(line)
        diff_estimates = []
        for _ in range(_N_BOOT):
            sample = rng.choices(units, k=len(units))
            sample_sids = {sid for _, sids in sample for sid in sids}
            rep_diffs = []
            for rep in range(1, 6):
                rep_lines = [l for l in by_rep[rep] if l["scenario_id"] in sample_sids]
                if not rep_lines:
                    continue
                r1 = _make_scored_runs(rep_lines, scenario_by_id, "ALLOW")
                r2 = _make_scored_runs(rep_lines, scenario_by_id, None)
                c1 = score_cost(r1, config=cfg, weight_set=ws)["mean"]
                c2 = score_cost(r2, config=cfg, weight_set=ws)["mean"]
                rep_diffs.append(c2 - c1)
            if rep_diffs:
                diff_estimates.append(sum(rep_diffs) / len(rep_diffs))
        diff_ci[ws] = (_pct(diff_estimates, 0.025), _pct(diff_estimates, 0.975))

    # ── step 3: sensitivity (cost ranks) ──────────────────────────────────────
    print("computing cost sensitivity …")
    sensitivity: dict[str, dict[str, float]] = {}
    for ws in _WEIGHT_SETS:
        sensitivity[ws] = {}
        for name in ("B1", "B2", "B3", "B4"):
            bl_lines = [l for l in base_lines if l["baseline"] == name]
            runs = _make_scored_runs(
                [{**l, "a1_action": l["action"], "a2_verdict": "ALLOW"} for l in bl_lines],
                scenario_by_id,
            )
            sensitivity[ws][name] = score_cost(runs, config=cfg, weight_set=ws)["mean"]
        m1 = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, weight_set=ws, a2_override="ALLOW")
        m2 = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, weight_set=ws, a2_override=None)
        sensitivity[ws]["A1"] = m1.get("cost_mean", float("nan")) or float("nan")
        sensitivity[ws]["A2"] = m2.get("cost_mean", float("nan")) or float("nan")

    # ── step 4: type-wise A1 accuracy ─────────────────────────────────────────
    typewise = _typewise_accuracy(a1a2_lines, scenario_by_id)

    # ── step 5: failure classification ────────────────────────────────────────
    fail_class = _failure_classification(a1a2_lines, scenario_by_id)

    # ── step 5: calibration (AUROC / ECE / Brier) ─────────────────────────────
    a1_cal = _calibration_metrics(a1a2_lines, scenario_by_id)
    b3_cal = _b3_calibration_metrics(base_lines, scenario_by_id)

    # B3 τ validation: B3 eval cost + Ask-F1 (already computed above)
    b3_metrics = baseline_rows["B3"]

    # ── step 6: rep agreement ─────────────────────────────────────────────────
    rep_agr = _rep_agreement(a1a2_lines)

    # ── write report ──────────────────────────────────────────────────────────
    print("writing report …")
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    lines_out: list[str] = []
    W = lines_out.append

    ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    W(f"# Eval v1 — 채점 보고서")
    W(f"")
    W(f"생성: {ts_now}  ")
    W(f"평가 실행 커밋: `{_EVAL_COMMIT[:7]}` (eval-freeze-v1)  ")
    W(f"채점 커밋: `{head_hash[:7]}`  ")
    W(f"")

    # ── § 0 pre-flight ─────────────────────────────────────────────────────────
    W(f"## 0. 사전 점검")
    W(f"")
    W(f"**git diff eval-freeze-v1 -- src prompts config**: "
      f"{'비어 있음 ✓' if not diff_out.strip() else f'변경 있음! {diff_out[:200]}'}")
    W(f"")
    W(f"**시나리오 유형별 개수**")
    W(f"")
    type_order = ["normal", "pre48h", "pre24h", "f2-act", "f2-abstain"]
    for t in type_order:
        n = len(type_counts.get(t, set()))
        expected = {"normal": 14, "pre48h": 13, "pre24h": 13, "f2-act": 20, "f2-abstain": 20}[t]
        status = "✓" if n == expected else f"✗ (expected {expected})"
        W(f"- `{t}`: {n} {status}")
    W(f"")
    W(f"**A1/A2 실행 수**: {len(a1a2_lines)} (80 × 5 = 400 ✓)")
    W(f"**베이스라인 실행 수**: {len(base_lines)} (80 × 4 = 320 ✓)")
    W(f"")

    # ── § 1 scorecard ──────────────────────────────────────────────────────────
    W(f"## 1. 6줄 성적표")
    W(f"")
    W(f"비용은 default 가중치(100/10/1). A1·A2는 5회 평균, 95% CI (부트스트랩 {_N_BOOT}회, 쌍 단위 샘플링).")
    W(f"쌍 정확도·Ask-F1은 A1 기준(ADR 0012). 정답률 = executed_category 일치율.")
    W(f"")

    sc_headers = ["시스템", "비용/문제", "95% CI", "쌍 정확도", "Ask-F1", "적시 비율", "정답률"]
    sc_rows = []

    def _sc_row(name, m, ci=None):
        cost = _fmt(m.get("cost_mean"), 2)
        ci_str = _fmt_ci(*(ci.get("cost_mean", (float("nan"), float("nan"))) if ci else (float("nan"), float("nan"))), 2)
        return [
            name,
            cost,
            ci_str,
            _pct_str(m.get("pair_acc")),
            _fmt(m.get("ask_f1"), 3),
            _pct_str(m.get("ontime")),
            _pct_str(m.get("accuracy")),
        ]

    for name in ("B1", "B2", "B3", "B4"):
        sc_rows.append(_sc_row(name, baseline_rows[name]))
    sc_rows.append(_sc_row("A1 (raw)", a1_avg, a1_ci))
    sc_rows.append(_sc_row("A2 (gated)", a2_avg, a2_ci))

    W(_md_table(sc_headers, sc_rows))
    W(f"")

    # Ask-F1 detail
    W(f"**Ask-F1 상세 (A1 raw)**")
    a1_ask = {}
    for rep in range(1, 6):
        rep_lines = [l for l in a1a2_lines if l["rep"] == rep]
        runs = _make_scored_runs(rep_lines, scenario_by_id, "ALLOW")
        r = score_ask_f1(runs)
        for k in ("precision", "recall", "f1"):
            a1_ask.setdefault(k, []).append(r[k])
    a1_ask_avg = {k: sum(v) / len(v) for k, v in a1_ask.items()}
    W(f"precision={_fmt(a1_ask_avg['precision'], 3)} recall={_fmt(a1_ask_avg['recall'], 3)} F1={_fmt(a1_ask_avg['f1'], 3)}")
    W(f"")

    # ── § 2 A2 − A1 cost ──────────────────────────────────────────────────────
    W(f"## 2. A2 − A1 비용 차이")
    W(f"")
    W(f"A2 게이트 적용 전(A1 raw)과 적용 후(A2 gated)의 문제당 평균 비용 차이.")
    W(f"음수 = 게이트가 비용을 낮춤.")
    W(f"")

    diff_headers = ["가중치 세트", "A1 비용", "A2 비용", "차이 (A2−A1)", "95% CI"]
    diff_rows = []
    for ws in _WEIGHT_SETS:
        c1 = a1_costs[ws]
        c2 = a2_costs[ws]
        diff = c2 - c1
        diff_rows.append([
            ws,
            _fmt(c1, 3),
            _fmt(c2, 3),
            f"{diff:+.3f}",
            _fmt_ci(*diff_ci[ws], 3),
        ])
    W(_md_table(diff_headers, diff_rows))
    W(f"")

    # ── § 3 cost sensitivity ──────────────────────────────────────────────────
    W(f"## 3. 비용 민감도 — 3개 가중치 세트")
    W(f"")
    W(f"miss:overstop:overescalation 비율 (correct=0은 공통).")
    W(f"")

    systems = ["B1", "B2", "B3", "B4", "A1", "A2"]
    sens_headers = ["시스템", "low_miss (50:10:1)", "default (100:10:1)", "high_miss (200:10:1)", "순위 변동"]
    sens_rows = []
    for sys_name in systems:
        vals = [sensitivity[ws].get(sys_name, float("nan")) for ws in _WEIGHT_SETS]
        rank_by_ws = [
            sorted(systems, key=lambda s: sensitivity[ws].get(s, float("nan"))).index(sys_name) + 1
            for ws in _WEIGHT_SETS
        ]
        rank_change = "없음" if len(set(rank_by_ws)) == 1 else f"{rank_by_ws}"
        sens_rows.append([sys_name] + [_fmt(v, 2) for v in vals] + [rank_change])
    W(_md_table(sens_headers, sens_rows))
    W(f"")

    # ── § 4 type-wise ─────────────────────────────────────────────────────────
    W(f"## 4. 유형별 A1 정답률 및 놓친 고장 비율")
    W(f"")
    W(f"정답률 = modal(5회) action == ground_truth. 놓친 고장 = gt∈{{SCHEDULE_PM,ESCALATE}}, "
      f"executed=NOTHING or CHECK_SENSOR.")
    W(f"")

    type_headers = ["유형", "n", "정답률", "놓친 고장 비율"]
    type_rows = []
    for t in type_order:
        d = typewise.get(t, {"total": 0, "correct": 0, "missed_failure": 0})
        n = d["total"]
        acc = d["correct"] / n if n else 0.0
        missed_n = d.get("missed_failure", 0)
        gt_failure_types = {"pre48h", "pre24h", "f2-act"}
        if t in gt_failure_types and n > 0:
            missed_str = f"{missed_n}/{n} ({missed_n/n*100:.0f}%)"
        else:
            missed_str = "—"
        type_rows.append([t, str(n), _pct_str(acc), missed_str])
    W(_md_table(type_headers, type_rows))
    W(f"")

    # ── § 5 pre-registered analyses ───────────────────────────────────────────
    W(f"## 5. 사전 등록 분석")
    W(f"")

    # 5a. failure classification
    W(f"### 5a. 실패 원인 4분류 (ADR 0011 §2.1)")
    W(f"")
    W(f"자동 분류 규칙: INVALID → model, format_errors>0 → model, reached_step_cap → model(잠정), 나머지 → unclassified.")
    W(f"'correct' = a1_action == ground_truth (정답 제외).")
    W(f"")
    total_runs_fc = len(a1a2_lines)
    W(f"| 분류 | 건수 | 비율 |")
    W(f"|------|------|------|")
    for cat in ("correct", "model", "unclassified"):
        n_cat = fail_class.get(cat, 0)
        W(f"| {cat} | {n_cat} | {n_cat/total_runs_fc*100:.1f}% |")
    W(f"| **총 실패** | **{fail_class.get('total_failures', 0)}** | "
      f"**{fail_class.get('total_failures', 0)/total_runs_fc*100:.1f}%** |")
    W(f"")
    W(f"주의: '미분류' 항목은 수동 검토 필요 (ADR 0011 §2.1의 harness/tools/problem 범주 포함 가능성).")
    W(f"")

    # 5b. B3 τ validation
    W(f"### 5b. B3 dev-τ (τ=0.186) 적용 결과")
    W(f"")
    W(f"b3_lr.json threshold=0.186이 eval 실행에 이미 적용됨.")
    W(f"")
    W(f"| 지표 | 값 |")
    W(f"|------|----|")
    W(f"| 비용/문제 (default) | {_fmt(b3_metrics.get('cost_mean'), 2)} |")
    W(f"| Ask-F1 | {_fmt(b3_metrics.get('ask_f1'), 3)} |")
    W(f"| Ask-precision | {_fmt(b3_metrics.get('ask_precision'), 3)} |")
    W(f"| Ask-recall | {_fmt(b3_metrics.get('ask_recall'), 3)} |")
    W(f"")

    # 5c. AUROC / ECE / Brier
    W(f"### 5c. AUROC / ECE / Brier")
    W(f"")
    W(f"AUROC 양성: gt ∈ {{SCHEDULE_PM, ESCALATE}} (46/80 = 57.5%).")
    W(f"A1 score: confidence (action=SCHEDULE_PM/ESCALATE/REQUEST_STOP), 1−confidence (그 외).")
    W(f"B3 score: 로지스틱 회귀 확률 (raw probability before threshold).")
    W(f"ECE: 10-bin. Brier: 정답 여부 기준.")
    W(f"")
    cal_headers = ["시스템", "AUROC", "ECE", "Brier"]
    cal_rows = [
        ["B3", _fmt(b3_cal["auroc"], 3), _fmt(b3_cal["ece"], 3), _fmt(b3_cal["brier"], 3)],
        ["A1 (modal)", _fmt(a1_cal["auroc"], 3), _fmt(a1_cal["ece"], 3), _fmt(a1_cal["brier"], 3)],
    ]
    W(_md_table(cal_headers, cal_rows))
    W(f"")

    # ── § 6 rep consistency ───────────────────────────────────────────────────
    W(f"## 6. 5회 반복 답 일치율")
    W(f"")
    W(f"| 지표 | 값 |")
    W(f"|------|----|")
    W(f"| 전체 80 시나리오 수 | {rep_agr['n_scenarios']} |")
    W(f"| 5회 모두 같은 답 (완전 일치) | {rep_agr['full_agree']} ({rep_agr['full_agree_pct']*100:.1f}%) |")
    W(f"| 평균 최빈값 일치율 (per scenario) | {rep_agr['avg_consistency']*100:.1f}% |")
    W(f"")

    # Per-type agreement
    type_agree: dict[str, dict] = defaultdict(lambda: {"n": 0, "full": 0, "match_fracs": []})
    by_sid_actions: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid_actions[line["scenario_id"]].append(line["a1_action"])
    for sid, actions in by_sid_actions.items():
        t = _type_of(sid)
        type_agree[t]["n"] += 1
        if len(set(actions)) == 1:
            type_agree[t]["full"] += 1
        modal = Counter(actions).most_common(1)[0][0]
        type_agree[t]["match_fracs"].append(sum(1 for a in actions if a == modal) / len(actions))
    W(f"유형별 완전 일치율:")
    W(f"")
    type_agree_rows = []
    for t in type_order:
        d = type_agree.get(t, {})
        n = d.get("n", 0)
        full = d.get("full", 0)
        mfs = d.get("match_fracs", [])
        avg_m = sum(mfs) / len(mfs) if mfs else 0.0
        type_agree_rows.append([t, str(n), f"{full} ({full/n*100:.0f}%)" if n else "—", _pct_str(avg_m)])
    W(_md_table(["유형", "n", "완전 일치", "평균 최빈값 일치율"], type_agree_rows))
    W(f"")

    # ── footer ────────────────────────────────────────────────────────────────
    W(f"---")
    W(f"")
    W(f"*eval-freeze-v1 코드 동결 준수. src/prompts/config 변경 없음.*")
    W(f"*B1~B4 단일 결정론적 실행. A1·A2 5회 반복, 부트스트랩 {_N_BOOT}회.*")

    report_text = "\n".join(lines_out)
    _REPORT.write_text(report_text, encoding="utf-8")
    print(f"report written → {_REPORT}")

    # Print summary to stdout
    print("\n" + "=" * 60)
    print("성적표 요약 (default 가중치)")
    print("=" * 60)
    for name in ("B1", "B2", "B3", "B4"):
        m = baseline_rows[name]
        print(f"{name:4s}  cost={_fmt(m['cost_mean'], 2):6s}  pair_acc={_pct_str(m['pair_acc']):6s}  "
              f"ask_f1={_fmt(m['ask_f1'], 3):5s}  acc={_pct_str(m['accuracy']):6s}")
    print(f"A1   cost={_fmt(a1_avg.get('cost_mean'), 2):6s}  pair_acc={_pct_str(a1_avg.get('pair_acc')):6s}  "
          f"ask_f1={_fmt(a1_avg.get('ask_f1'), 3):5s}  acc={_pct_str(a1_avg.get('accuracy')):6s}  "
          f"CI=[{_fmt(a1_ci.get('cost_mean', (float('nan'),float('nan')))[0], 2)}, "
          f"{_fmt(a1_ci.get('cost_mean', (float('nan'),float('nan')))[1], 2)}]")
    print(f"A2   cost={_fmt(a2_avg.get('cost_mean'), 2):6s}  pair_acc={_pct_str(a2_avg.get('pair_acc')):6s}  "
          f"ask_f1={_fmt(a2_avg.get('ask_f1'), 3):5s}  acc={_pct_str(a2_avg.get('accuracy')):6s}  "
          f"CI=[{_fmt(a2_ci.get('cost_mean', (float('nan'),float('nan')))[0], 2)}, "
          f"{_fmt(a2_ci.get('cost_mean', (float('nan'),float('nan')))[1], 2)}]")
    print("=" * 60)
    print(f"A2−A1 cost diff (default): {a2_costs['default'] - a1_costs['default']:+.3f}  "
          f"95% CI {_fmt_ci(*diff_ci['default'], 3)}")
    print(f"Rep agreement: {rep_agr['full_agree']}/{rep_agr['n_scenarios']} "
          f"({rep_agr['full_agree_pct']*100:.0f}%) full, "
          f"avg consistency {rep_agr['avg_consistency']*100:.1f}%")


if __name__ == "__main__":
    main()
