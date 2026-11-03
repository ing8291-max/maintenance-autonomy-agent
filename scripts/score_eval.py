"""Compute all ADR 0013 §4.2 metrics and write reports/eval-v1.md.

Reads  : logs/eval-a1a2.jsonl, logs/eval-baselines.jsonl,
         logs/trace.jsonl (dev v2 runs for τ), data/synth/dev-*.yaml
Writes : reports/eval-v1.md
Eval   : b006f6d (eval-freeze-v1)
Score  : HEAD

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

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from mautonomy.scenarios.generator import generate_eval_scenarios
from mautonomy.scenarios.loader import load_scenario
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
_BASE_LOG  = _REPO / "logs" / "eval-baselines.jsonl"
_TRACE_LOG = _REPO / "logs" / "trace.jsonl"     # dev v2 runs
_SYNTH_DIR = _REPO / "data" / "synth"
_REPORT    = _REPO / "reports" / "eval-v1.md"
_EVAL_COMMIT = "b006f6db30b7e3974d3f7943d51409696ec3e391"
_N_BOOT   = 2000
_BOOT_SEED = 42
_WEIGHT_SETS = ["low_miss", "default", "high_miss"]

# Ground-truth → ideal executed category (ADR 0012)
_GT_TO_EXEC: dict[str, str] = {
    "NO_ACTION":   "NOTHING",
    "CHECK_SENSOR": "CHECK_SENSOR",
    "SCHEDULE_PM": "SCHEDULE_PM",
    "ESCALATE":    "ASK_HUMAN",
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

def _load_dev_v2() -> list[dict]:
    """Load dev v2 runs from the development trace."""
    return [
        l for l in _load_log(_TRACE_LOG)
        if l.get("prompt_version") == "v2"
    ]

def _load_dev_scenarios(scenario_ids: list[str]) -> dict:
    """Load dev scenario YAMLs from data/synth/."""
    lookup: dict[str, object] = {}
    for sid in scenario_ids:
        path = _SYNTH_DIR / f"{sid}.yaml"
        if path.is_file():
            try:
                sc = load_scenario(path, allow_eval=False)
                lookup[sid] = sc
            except Exception:
                pass
    return lookup


# ── math helpers ───────────────────────────────────────────────────────────────

def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    i = p * (len(xs) - 1)
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
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
    bins: list[list[tuple]] = [[] for _ in range(n_bins)]
    for c, a in zip(confs, corrects):
        bins[min(int(c * n_bins), n_bins - 1)].append((c, a))
    ece = 0.0
    for b in bins:
        if b:
            ece += (len(b) / n) * abs(
                sum(x[0] for x in b) / len(b) - sum(x[1] for x in b) / len(b)
            )
    return ece

def _brier(confs: list[float], corrects: list[int]) -> float:
    if not confs:
        return float("nan")
    return sum((c - a) ** 2 for c, a in zip(confs, corrects)) / len(confs)


# ── scenario helpers ───────────────────────────────────────────────────────────

def _build_scenario_lookup(scenarios) -> dict:
    return {s.scenario_id: s for s in scenarios}

def _build_units(scenarios) -> list:
    """Bootstrap units — pairs kept together."""
    pair_ids: dict[str, list[str]] = {}
    singletons: list[str] = []
    for sc in scenarios:
        if sc.pair_id:
            pair_ids.setdefault(sc.pair_id, []).append(sc.scenario_id)
        else:
            singletons.append(sc.scenario_id)
    return (
        [(pid, sids) for pid, sids in pair_ids.items()] +
        [(sid, [sid]) for sid in singletons]
    )

def _make_scored_runs(
    lines: list[dict],
    scenario_by_id: dict,
    a2_override: str | None = None,
) -> list[ScoredRun]:
    result = []
    for line in lines:
        sc = scenario_by_id.get(line["scenario_id"])
        if sc is None:
            continue
        a1 = line.get("a1_action") or line.get("action", "INVALID")
        a2 = a2_override if a2_override is not None else line.get("a2_verdict", "ALLOW")
        result.append(ScoredRun(scenario=sc, a1_action=a1, a2_verdict=a2))
    return result

def _type_of(sid: str) -> str:
    if "normal"     in sid: return "normal"
    if "pre48h"     in sid: return "pre48h"
    if "pre24h"     in sid: return "pre24h"
    if "f2-act"     in sid: return "f2-act"
    if "f2-abstain" in sid: return "f2-abstain"
    return "unknown"


# ── per-run metric bundle ──────────────────────────────────────────────────────

def _all_metrics(
    runs: list[ScoredRun],
    cfg,
    weight_set: str = "default",
) -> dict:
    if not runs:
        return {k: None for k in ("cost_mean", "pair_acc", "ask_f1", "ask_precision",
                                   "ask_recall", "ontime", "accuracy", "n")}
    cost_r = score_cost(runs, config=cfg, weight_set=weight_set)
    ask_r  = score_ask_f1(runs)
    correct = sum(
        1 for r in runs
        if executed_of(r.a1_action, r.a2_verdict)
           == _GT_TO_EXEC.get(r.scenario.ground_truth.action, "")
    )
    return {
        "cost_mean":    cost_r["mean"],
        "pair_acc":     score_pair_accuracy(runs),
        "ask_f1":       ask_r["f1"],
        "ask_precision": ask_r["precision"],
        "ask_recall":   ask_r["recall"],
        "ontime":       score_ontime(runs),
        "accuracy":     correct / len(runs),
        "n":            len(runs),
    }

def _rep_avg_metrics(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
    cfg,
    weight_set: str = "default",
    a2_override: str | None = None,
) -> dict:
    per_rep: dict[str, list[float]] = defaultdict(list)
    for rep in range(1, 6):
        rep_lines = [l for l in a1a2_lines if l["rep"] == rep]
        m = _all_metrics(_make_scored_runs(rep_lines, scenario_by_id, a2_override), cfg, weight_set)
        for k, v in m.items():
            if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
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
) -> dict:
    rng = random.Random(seed)
    by_rep: dict[int, list[dict]] = defaultdict(list)
    for line in a1a2_lines:
        by_rep[line["rep"]].append(line)

    estimates: dict[str, list[float]] = defaultdict(list)
    for _ in range(n_boot):
        sample = rng.choices(units, k=len(units))
        sids = {sid for _, ss in sample for sid in ss}
        rep_vals: dict[str, list[float]] = defaultdict(list)
        for rep in range(1, 6):
            rep_lines = [l for l in by_rep[rep] if l["scenario_id"] in sids]
            if not rep_lines:
                continue
            m = _all_metrics(_make_scored_runs(rep_lines, scenario_by_id, a2_override), cfg, weight_set)
            for k, v in m.items():
                if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
                    rep_vals[k].append(float(v))
        for k, vs in rep_vals.items():
            if vs:
                estimates[k].append(sum(vs) / len(vs))

    return {
        k: (_pct(vs, 0.025), _pct(vs, 0.975)) if vs else (float("nan"), float("nan"))
        for k, vs in estimates.items()
    }


# ── §4 helpers ─────────────────────────────────────────────────────────────────

def _typewise_action_dist(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
) -> dict[str, dict]:
    """Per type: {action: count} over modal predictions."""
    by_sid: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])

    dist: dict[str, dict] = {}
    for sid, actions in by_sid.items():
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        modal = Counter(actions).most_common(1)[0][0]
        t = _type_of(sid)
        gt = sc.ground_truth.action
        dist.setdefault(t, defaultdict(int))
        dist[t][modal] += 1
    return dist

def _typewise_accuracy(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
) -> dict:
    by_sid: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])

    typewise: dict[str, dict] = {}
    for sid, actions in by_sid.items():
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        modal = Counter(actions).most_common(1)[0][0]
        gt = sc.ground_truth.action
        t = _type_of(sid)
        if t not in typewise:
            typewise[t] = {"total": 0, "correct": 0, "missed_failure": 0}
        typewise[t]["total"] += 1
        if modal == gt:
            typewise[t]["correct"] += 1
        if gt in ("SCHEDULE_PM", "ESCALATE"):
            if executed_of(modal, "ALLOW") in ("NOTHING", "CHECK_SENSOR"):
                typewise[t]["missed_failure"] += 1
    return typewise


# ── §5a failure classification ─────────────────────────────────────────────────

def _failure_classification(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
) -> dict:
    """Per-run classification: harness / tool / model / correct.

    harness : format_errors>0 OR reached_step_cap OR a1_action==INVALID
    tool    : cannot be detected from trace (no tool-error field) → 0
    model   : all other wrong predictions
    """
    cats: dict[str, int] = defaultdict(int)
    for line in a1a2_lines:
        sc = scenario_by_id.get(line["scenario_id"])
        if sc is None:
            continue
        a1 = line["a1_action"]
        gt = sc.ground_truth.action
        if a1 == gt:
            cats["correct"] += 1
            continue
        if (a1 == "INVALID"
                or line.get("format_errors", 0) > 0
                or line.get("reached_step_cap", False)):
            cats["harness"] += 1
        else:
            cats["model"] += 1
    cats["total_failures"] = cats["harness"] + cats["tool"] + cats["model"]
    return dict(cats)

def _test_problem_candidates(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
) -> list[dict]:
    """Scenarios where all 5 reps produce the same wrong action."""
    by_sid: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])

    candidates = []
    for sid, actions in by_sid.items():
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        if len(set(actions)) == 1 and actions[0] != sc.ground_truth.action:
            candidates.append({
                "scenario_id": sid,
                "type":        _type_of(sid),
                "gt":          sc.ground_truth.action,
                "predicted":   actions[0],
            })
    return sorted(candidates, key=lambda x: (x["type"], x["scenario_id"]))


# ── §5c AUROC / ECE / Brier (two criteria) ────────────────────────────────────

def _a1_auroc_both(
    a1a2_lines: list[dict],
    scenario_by_id: dict,
) -> dict:
    """A1 AUROC under two positive-class definitions.

    Score: avg_confidence when modal_action != NO_ACTION,
           1 - avg_confidence when modal_action == NO_ACTION.
    This maps "model thinks action is needed" to a high score.

    Criterion A (failure)     : positive = gt ∈ {SCHEDULE_PM, ESCALATE}
    Criterion B (action_needed): positive = gt != NO_ACTION
    """
    by_sid_a: dict[str, list[str]]   = defaultdict(list)
    by_sid_c: dict[str, list[float]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid_a[line["scenario_id"]].append(line["a1_action"])
        c = line.get("a1_confidence")
        if c is not None:
            by_sid_c[line["scenario_id"]].append(float(c))

    scores, lbl_fail, lbl_act = [], [], []
    ece_c, ece_ok, brier_c, brier_ok = [], [], [], []

    for sid, actions in by_sid_a.items():
        sc = scenario_by_id.get(sid)
        if sc is None:
            continue
        modal    = Counter(actions).most_common(1)[0][0]
        gt       = sc.ground_truth.action
        avg_conf = sum(by_sid_c[sid]) / len(by_sid_c[sid]) if by_sid_c[sid] else 0.5

        # score: "action is needed" signal
        score = avg_conf if modal != "NO_ACTION" else (1.0 - avg_conf)
        scores.append(score)
        lbl_fail.append(1 if gt in ("SCHEDULE_PM", "ESCALATE") else 0)
        lbl_act.append(1 if gt != "NO_ACTION" else 0)

        correct = 1 if modal == gt else 0
        ece_c.append(avg_conf); ece_ok.append(correct)
        brier_c.append(avg_conf); brier_ok.append(correct)

    return {
        "auroc_failure":      _auroc(scores, lbl_fail),
        "auroc_action_needed": _auroc(scores, lbl_act),
        "ece":   _ece(ece_c, ece_ok),
        "brier": _brier(brier_c, brier_ok),
        "n_pos_failure":       sum(lbl_fail),
        "n_pos_action_needed": sum(lbl_act),
    }

def _b3_auroc_both(
    base_lines: list[dict],
    scenario_by_id: dict,
) -> dict:
    """B3 AUROC under two criteria.

    Score: raw logistic-regression probability (P(failure within 48h)).
    This is naturally aligned with Criterion A but not Criterion B
    (B3 cannot detect CHECK_SENSOR scenarios which are also positives in B).
    """
    b3 = [l for l in base_lines if l.get("baseline") == "B3"]
    scores, lbl_fail, lbl_act = [], [], []
    ece_c, ece_ok, brier_c, brier_ok = [], [], [], []
    for line in b3:
        sc = scenario_by_id.get(line["scenario_id"])
        if sc is None:
            continue
        gt   = sc.ground_truth.action
        conf = float(line["confidence"]) if line.get("confidence") is not None else 0.5
        scores.append(conf)
        lbl_fail.append(1 if gt in ("SCHEDULE_PM", "ESCALATE") else 0)
        lbl_act.append(1 if gt != "NO_ACTION" else 0)
        correct = 1 if line["action"] == gt else 0
        ece_c.append(conf); ece_ok.append(correct)
        brier_c.append(conf); brier_ok.append(correct)
    return {
        "auroc_failure":       _auroc(scores, lbl_fail),
        "auroc_action_needed": _auroc(scores, lbl_act),
        "ece":   _ece(ece_c, ece_ok),
        "brier": _brier(brier_c, brier_ok),
        "n_pos_failure":       sum(lbl_fail),
        "n_pos_action_needed": sum(lbl_act),
    }


# ── §5d confidence τ analysis ─────────────────────────────────────────────────

def _tau_analysis(
    dev_v2_lines: list[dict],
    dev_scenario_by_id: dict,
    eval_a1a2_lines: list[dict],
    eval_scenario_by_id: dict,
    cfg,
) -> dict:
    """Confidence-threshold policy: if conf < τ → ESCALATE.

    τ is chosen on dev v2 data to minimise cost (default weights).
    The chosen τ is then applied to eval data; cost and Ask-F1 are reported.
    """
    # --- build dev data -------------------------------------------------
    dev_pts = []
    for line in dev_v2_lines:
        sc = dev_scenario_by_id.get(line["scenario_id"])
        if sc is None:
            continue
        dev_pts.append({
            "conf":   float(line.get("a1_confidence") or 0.5),
            "action": line["a1_action"],
            "sc":     sc,
        })

    if not dev_pts:
        return {"error": "no dev v2 data"}

    # --- τ search on dev -----------------------------------------------
    raw_confs = sorted({round(p["conf"], 2) for p in dev_pts})
    # add boundary sentinels
    candidates = sorted(set(raw_confs + [0.0, 0.60, 0.65, 0.70,
                                          0.75, 0.80, 0.85, 0.90, 1.0]))
    best_tau, best_cost, best_ask_f1 = 0.0, float("inf"), 0.0
    dev_sweep = []

    for tau in candidates:
        runs = [
            ScoredRun(
                scenario=p["sc"],
                a1_action="ESCALATE" if p["conf"] < tau else p["action"],
                a2_verdict="ALLOW",
            )
            for p in dev_pts
        ]
        c = score_cost(runs, config=cfg)["mean"]
        f = score_ask_f1(runs)["f1"]
        dev_sweep.append({"tau": tau, "cost": c, "ask_f1": f})
        if c < best_cost:
            best_cost, best_tau, best_ask_f1 = c, tau, f

    # --- apply τ to eval (5 reps) ----------------------------------------
    by_rep: dict[int, list[dict]] = defaultdict(list)
    for line in eval_a1a2_lines:
        by_rep[line["rep"]].append(line)

    eval_costs, eval_f1s = [], []
    for rep in range(1, 6):
        runs = [
            ScoredRun(
                scenario=eval_scenario_by_id[l["scenario_id"]],
                a1_action="ESCALATE" if float(l.get("a1_confidence") or 0.5) < best_tau
                          else l["a1_action"],
                a2_verdict="ALLOW",
            )
            for l in by_rep[rep]
            if l["scenario_id"] in eval_scenario_by_id
        ]
        eval_costs.append(score_cost(runs, config=cfg)["mean"])
        eval_f1s.append(score_ask_f1(runs)["f1"])

    return {
        "dev_n":      len(dev_pts),
        "best_tau":   best_tau,
        "dev_cost":   best_cost,
        "dev_ask_f1": best_ask_f1,
        "dev_sweep":  dev_sweep,
        "eval_cost":  sum(eval_costs) / len(eval_costs) if eval_costs else float("nan"),
        "eval_ask_f1": sum(eval_f1s) / len(eval_f1s) if eval_f1s else float("nan"),
        "a1_eval_cost": None,  # filled in main
        "a1_eval_ask_f1": None,
    }


# ── §6 rep agreement ──────────────────────────────────────────────────────────

def _rep_agreement(a1a2_lines: list[dict]) -> dict:
    by_sid: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid[line["scenario_id"]].append(line["a1_action"])
    n = len(by_sid)
    full_agree = sum(1 for a in by_sid.values() if len(set(a)) == 1)
    modal_match = [
        sum(1 for a in actions if a == Counter(actions).most_common(1)[0][0]) / len(actions)
        for actions in by_sid.values()
    ]
    return {
        "n_scenarios":    n,
        "full_agree":     full_agree,
        "full_agree_pct": full_agree / n if n else 0.0,
        "avg_consistency": sum(modal_match) / len(modal_match) if modal_match else 0.0,
    }


# ── git helpers ────────────────────────────────────────────────────────────────

def _git(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(_REPO)] + cmd, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "?"


# ── formatting helpers ────────────────────────────────────────────────────────

def _fmt(v, d: int = 3) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{float(v):.{d}f}"

def _fmt_ci(lo, hi, d: int = 3) -> str:
    if math.isnan(float(lo)) or math.isnan(float(hi)):
        return "—"
    return f"[{lo:.{d}f}, {hi:.{d}f}]"

def _pct_str(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{float(v)*100:.1f}%"

def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    sep = " | "
    def _row(cells): return sep.join(c.ljust(w) for c, w in zip(cells, widths))
    return "\n".join([
        _row(headers),
        sep.join("-" * w for w in widths),
        *(_row(r) for r in rows),
    ])


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print("loading data …")
    a1a2_lines = _load_log(_A1A2_LOG)
    base_lines  = _load_log(_BASE_LOG)
    scenarios   = generate_eval_scenarios(seed=42)
    scenario_by_id = _build_scenario_lookup(scenarios)
    units          = _build_units(scenarios)
    cfg            = load_config()

    dev_v2_lines = _load_dev_v2()
    dev_sids     = [l["scenario_id"] for l in dev_v2_lines]
    dev_sc_by_id = _load_dev_scenarios(dev_sids)
    print(f"  dev v2 runs: {len(dev_v2_lines)}, loaded scenarios: {len(dev_sc_by_id)}")

    print(f"  {len(a1a2_lines)} A1/A2 runs, {len(base_lines)} baseline runs, "
          f"{len(scenarios)} scenarios, {len(units)} bootstrap units")

    diff_out = _git(["diff", "eval-freeze-v1", "--", "src", "prompts", "config"])
    head_hash = _git(["rev-parse", "HEAD"])

    type_counts: dict[str, set] = defaultdict(set)
    for sc in scenarios:
        type_counts[_type_of(sc.scenario_id)].add(sc.scenario_id)
    type_order = ["normal", "pre48h", "pre24h", "f2-act", "f2-abstain"]

    # ── §1 scorecard ──────────────────────────────────────────────────────────
    print("computing baseline metrics …")
    baseline_rows: dict[str, dict] = {}
    for name in ("B1", "B2", "B3", "B4"):
        bl = [l for l in base_lines if l["baseline"] == name]
        runs = _make_scored_runs(
            [{**l, "a1_action": l["action"], "a2_verdict": "ALLOW"} for l in bl],
            scenario_by_id,
        )
        baseline_rows[name] = _all_metrics(runs, cfg)

    print("A1/A2 5-rep averages …")
    a1_avg = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, a2_override="ALLOW")
    a2_avg = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, a2_override=None)

    print(f"bootstrapping CI (n={_N_BOOT}) …")
    a1_ci = _bootstrap_ci(a1a2_lines, scenario_by_id, units, cfg, a2_override="ALLOW")
    a2_ci = _bootstrap_ci(a1a2_lines, scenario_by_id, units, cfg, a2_override=None)

    # ── §2 A2−A1 cost diff ────────────────────────────────────────────────────
    a1_costs, a2_costs, diff_cis = {}, {}, {}
    for ws in _WEIGHT_SETS:
        a1_costs[ws] = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, ws, "ALLOW").get("cost_mean", float("nan"))
        a2_costs[ws] = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, ws, None).get("cost_mean", float("nan"))
        rng2 = random.Random(_BOOT_SEED + 1)
        by_rep2: dict[int, list[dict]] = defaultdict(list)
        for line in a1a2_lines:
            by_rep2[line["rep"]].append(line)
        diffs = []
        for _ in range(_N_BOOT):
            smp = rng2.choices(units, k=len(units))
            sids = {sid for _, ss in smp for sid in ss}
            rep_d = []
            for rep in range(1, 6):
                rl = [l for l in by_rep2[rep] if l["scenario_id"] in sids]
                if not rl: continue
                c1 = score_cost(_make_scored_runs(rl, scenario_by_id, "ALLOW"), config=cfg, weight_set=ws)["mean"]
                c2 = score_cost(_make_scored_runs(rl, scenario_by_id, None),   config=cfg, weight_set=ws)["mean"]
                rep_d.append(c2 - c1)
            if rep_d:
                diffs.append(sum(rep_d) / len(rep_d))
        diff_cis[ws] = (_pct(diffs, 0.025), _pct(diffs, 0.975))

    # ── §3 sensitivity ────────────────────────────────────────────────────────
    print("cost sensitivity …")
    sensitivity: dict[str, dict[str, float]] = {ws: {} for ws in _WEIGHT_SETS}
    for ws in _WEIGHT_SETS:
        for name in ("B1", "B2", "B3", "B4"):
            bl = [l for l in base_lines if l["baseline"] == name]
            runs = _make_scored_runs(
                [{**l, "a1_action": l["action"], "a2_verdict": "ALLOW"} for l in bl],
                scenario_by_id,
            )
            sensitivity[ws][name] = score_cost(runs, config=cfg, weight_set=ws)["mean"]
        sensitivity[ws]["A1"] = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, ws, "ALLOW").get("cost_mean", float("nan")) or float("nan")
        sensitivity[ws]["A2"] = _rep_avg_metrics(a1a2_lines, scenario_by_id, cfg, ws, None).get("cost_mean", float("nan")) or float("nan")

    # ── §4 type-wise ──────────────────────────────────────────────────────────
    typewise   = _typewise_accuracy(a1a2_lines, scenario_by_id)
    action_dist = _typewise_action_dist(a1a2_lines, scenario_by_id)

    # ── §5a failure classification ────────────────────────────────────────────
    fail_class = _failure_classification(a1a2_lines, scenario_by_id)
    test_candidates = _test_problem_candidates(a1a2_lines, scenario_by_id)

    # ── §5c AUROC / ECE / Brier ───────────────────────────────────────────────
    a1_cal = _a1_auroc_both(a1a2_lines, scenario_by_id)
    b3_cal = _b3_auroc_both(base_lines, scenario_by_id)

    # ── §5d τ analysis ────────────────────────────────────────────────────────
    print("τ analysis …")
    tau_res = _tau_analysis(dev_v2_lines, dev_sc_by_id,
                             a1a2_lines, scenario_by_id, cfg)
    tau_res["a1_eval_cost"]    = a1_avg.get("cost_mean")
    tau_res["a1_eval_ask_f1"]  = a1_avg.get("ask_f1")

    # B3 τ metrics (already in baseline_rows)
    b3_m = baseline_rows["B3"]

    # ── §6 rep consistency ────────────────────────────────────────────────────
    rep_agr = _rep_agreement(a1a2_lines)
    by_sid_actions: dict[str, list[str]] = defaultdict(list)
    for line in a1a2_lines:
        by_sid_actions[line["scenario_id"]].append(line["a1_action"])

    # ── write report ──────────────────────────────────────────────────────────
    print("writing report …")
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    W = L.append
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    W("# Eval v1 — 채점 보고서")
    W("")
    W(f"생성: {ts}  ")
    W(f"평가 실행 커밋: `{_EVAL_COMMIT[:7]}` (eval-freeze-v1)  ")
    W(f"채점 커밋: `{head_hash[:7]}`  ")
    W("")

    # §0
    W("## 0. 사전 점검")
    W("")
    W(f"**git diff eval-freeze-v1 -- src prompts config**: "
      f"{'비어 있음 ✓' if not diff_out.strip() else f'변경 있음! {diff_out[:200]}'}")
    W("")
    W("**시나리오 유형별 개수**")
    W("")
    expected = {"normal": 14, "pre48h": 13, "pre24h": 13, "f2-act": 20, "f2-abstain": 20}
    for t in type_order:
        n = len(type_counts.get(t, set()))
        e = expected[t]
        W(f"- `{t}`: {n} {'✓' if n == e else f'✗ (expected {e})'}")
    W("")
    W(f"**A1/A2 실행 수**: {len(a1a2_lines)} (80 × 5 = 400 ✓)")
    W(f"**베이스라인 실행 수**: {len(base_lines)} (80 × 4 = 320 ✓)")
    W("")

    # §1
    W("## 1. 6줄 성적표")
    W("")
    W(f"비용은 default 가중치(100:10:1). A1·A2는 5회 평균, 95% CI (부트스트랩 {_N_BOOT}회, 쌍 단위 샘플링).")
    W("쌍 정확도·Ask-F1은 A1 기준(ADR 0012). 정답률 = executed_category 일치율.")
    W("")

    def _sc_row(name, m, ci=None):
        c_ci = _fmt_ci(*(ci.get("cost_mean", (float("nan"), float("nan"))) if ci else (float("nan"), float("nan"))), 2)
        return [name, _fmt(m.get("cost_mean"), 2), c_ci,
                _pct_str(m.get("pair_acc")), _fmt(m.get("ask_f1"), 3),
                _pct_str(m.get("ontime")), _pct_str(m.get("accuracy"))]

    sc_headers = ["시스템", "비용/문제", "95% CI", "쌍 정확도", "Ask-F1", "적시 비율", "정답률"]
    sc_rows = [_sc_row(n, baseline_rows[n]) for n in ("B1", "B2", "B3", "B4")]
    sc_rows.append(_sc_row("A1 (raw)", a1_avg, a1_ci))
    sc_rows.append(_sc_row("A2 (gated)", a2_avg, a2_ci))
    W(_md_table(sc_headers, sc_rows))
    W("")

    a1_ask = {k: [] for k in ("precision", "recall", "f1")}
    for rep in range(1, 6):
        r = score_ask_f1(_make_scored_runs(
            [l for l in a1a2_lines if l["rep"] == rep], scenario_by_id, "ALLOW"))
        for k in a1_ask: a1_ask[k].append(r[k])
    a1_ask_avg = {k: sum(v)/len(v) for k, v in a1_ask.items()}
    W(f"**Ask-F1 상세 (A1 raw)** precision={_fmt(a1_ask_avg['precision'], 3)} "
      f"recall={_fmt(a1_ask_avg['recall'], 3)} F1={_fmt(a1_ask_avg['f1'], 3)}")
    W("")

    # §2
    W("## 2. A2 − A1 비용 차이")
    W("")
    W("A2 게이트 적용 전(A1 raw)과 적용 후(A2 gated)의 문제당 평균 비용 차이.  ")
    W("음수 = 게이트가 비용을 낮춤.")
    W("")
    W(_md_table(
        ["가중치 세트", "A1 비용", "A2 비용", "차이 (A2−A1)", "95% CI"],
        [[ws, _fmt(a1_costs[ws], 3), _fmt(a2_costs[ws], 3),
          f"{a2_costs[ws] - a1_costs[ws]:+.3f}", _fmt_ci(*diff_cis[ws], 3)]
         for ws in _WEIGHT_SETS]
    ))
    W("")

    # §3
    W("## 3. 비용 민감도 — 3개 가중치 세트")
    W("")
    W("miss:overstop:overescalation (correct=0 공통).")
    W("")
    systems = ["B1", "B2", "B3", "B4", "A1", "A2"]
    sens_rows = []
    for sn in systems:
        vals = [sensitivity[ws].get(sn, float("nan")) for ws in _WEIGHT_SETS]
        ranks = [
            sorted(systems, key=lambda s: sensitivity[ws].get(s, float("nan"))).index(sn) + 1
            for ws in _WEIGHT_SETS
        ]
        rc = "없음" if len(set(ranks)) == 1 else str(ranks)
        sens_rows.append([sn] + [_fmt(v, 2) for v in vals] + [rc])
    W(_md_table(
        ["시스템", "low_miss (50:10:1)", "default (100:10:1)", "high_miss (200:10:1)", "순위 변동"],
        sens_rows,
    ))
    W("")

    # §4
    W("## 4. 유형별 A1 정답률 및 놓친 고장 비율")
    W("")
    W("정답률 = modal(5회) == ground_truth. "
      "놓친 고장 = gt∈{SCHEDULE_PM,ESCALATE}, executed=NOTHING or CHECK_SENSOR.")
    W("")
    all_actions = sorted({a for t in action_dist.values() for a in t})
    tw_headers = (["유형", "n", "정답률", "놓친 고장 비율"] + all_actions)
    tw_rows = []
    for t in type_order:
        d  = typewise.get(t, {"total": 0, "correct": 0, "missed_failure": 0})
        n  = d["total"]
        ac = _pct_str(d["correct"] / n if n else 0.0)
        if t in ("pre48h", "pre24h", "f2-act") and n > 0:
            mf = d.get("missed_failure", 0)
            mf_str = f"{mf}/{n} ({mf/n*100:.0f}%)"
        else:
            mf_str = "—"
        dist = action_dist.get(t, {})
        action_cells = [str(dist.get(a, 0)) for a in all_actions]
        tw_rows.append([t, str(n), ac, mf_str] + action_cells)
    W(_md_table(tw_headers, tw_rows))
    W("")
    W("*유형별 A1 예측 분포 (modal 기준): 열 = 예측 action.*")
    W("")

    # §5
    W("## 5. 사전 등록 분석")
    W("")

    # §5a
    W("### 5a. 실패 원인 분류 (ADR 0011 §2.1)")
    W("")
    W("자동 분류 기준:")
    W("- **harness**: INVALID 또는 format_errors>0 또는 reached_step_cap=True")
    W("- **tool**: trace에 도구 오류 필드 없음 → 자동 감지 불가")
    W("- **model**: 그 외 모든 오답")
    W("- **시험 탓 후보**: 5회 모두 같은 오답 → 아래 목록")
    W("")
    total_runs = len(a1a2_lines)
    W("| 분류 | 건수 | 비율 |")
    W("|------|------|------|")
    for cat in ("correct", "harness", "tool", "model"):
        nc = fail_class.get(cat, 0)
        W(f"| {cat} | {nc} | {nc/total_runs*100:.1f}% |")
    tf = fail_class.get("total_failures", 0)
    W(f"| **총 실패** | **{tf}** | **{tf/total_runs*100:.1f}%** |")
    W("")
    W(f"**시험 탓 후보** (5회 모두 같은 오답): {len(test_candidates)}건")
    W("")
    if test_candidates:
        W("| scenario_id | 유형 | 정답 | 예측 |")
        W("|-------------|------|------|------|")
        for c in test_candidates:
            W(f"| `{c['scenario_id']}` | {c['type']} | {c['gt']} | {c['predicted']} |")
    W("")

    # §5b
    W("### 5b. B3 dev-τ (τ=0.186) 적용 결과")
    W("")
    W("b3_lr.json threshold=0.186이 eval 실행에 이미 적용됨.")
    W("")
    W("| 지표 | 값 |")
    W("|------|----|")
    W(f"| 비용/문제 (default) | {_fmt(b3_m.get('cost_mean'), 2)} |")
    W(f"| Ask-F1 | {_fmt(b3_m.get('ask_f1'), 3)} |")
    W(f"| Ask-precision | {_fmt(b3_m.get('ask_precision'), 3)} |")
    W(f"| Ask-recall | {_fmt(b3_m.get('ask_recall'), 3)} |")
    W("")

    # §5c AUROC — two criteria
    W("### 5c. AUROC / ECE / Brier")
    W("")
    W("**AUROC 정의와 기준**")
    W("")
    W("| 항목 | 내용 |")
    W("|------|------|")
    W("| AUROC | P(score_pos > score_neg): 양성·음성 쌍 전체에서 양성의 score가 높은 비율 (0.5=무작위, 1.0=완벽) |")
    W("| **기준 A — failure** | 양성 = gt ∈ {SCHEDULE_PM, ESCALATE} — 고장 경고가 필요한 문제 |")
    W("| **기준 B — action_needed** | 양성 = gt ≠ NO_ACTION — 어떤 행동이든 필요한 문제 |")
    W("")
    W("**점수 변환**")
    W("")
    W("| 시스템 | score | 비고 |")
    W("|--------|-------|------|")
    W("| B3 | 로지스틱 회귀 raw 확률 P(failure within 48h) | 기준 A에 자연스럽게 정렬. 기준 B에서는 CHECK_SENSOR 양성을 낮게 점수화할 수 있어 AUROC 하락 가능 |")
    W("| A1 | modal_action≠NO_ACTION이면 avg_conf, NO_ACTION이면 1−avg_conf | '어떤 행동이 필요하다'는 신호로 해석. B3와 다른 도메인(확률 vs 확신도) |")
    W("")
    W("B3와 A1은 점수 도메인이 달라 직접 수치 비교는 주의 필요. 동일 기준 A/B 하에 같은 AUROC 정의로 산출.")
    W("")

    nf = a1_cal["n_pos_failure"]
    na = a1_cal["n_pos_action_needed"]
    W(f"양성 수: 기준 A = {nf}/80 ({nf/80*100:.1f}%), 기준 B = {na}/80 ({na/80*100:.1f}%)")
    W("")
    W(_md_table(
        ["시스템", "AUROC (기준 A: failure)", "AUROC (기준 B: action_needed)", "ECE", "Brier"],
        [
            ["B3", _fmt(b3_cal["auroc_failure"], 3), _fmt(b3_cal["auroc_action_needed"], 3),
             _fmt(b3_cal["ece"], 3), _fmt(b3_cal["brier"], 3)],
            ["A1 (modal)", _fmt(a1_cal["auroc_failure"], 3), _fmt(a1_cal["auroc_action_needed"], 3),
             _fmt(a1_cal["ece"], 3), _fmt(a1_cal["brier"], 3)],
        ]
    ))
    W("")

    # §5d τ
    W("### 5d. A1 확신도 임계값 τ 분석")
    W("")
    W("**정책**: a1_confidence < τ → ESCALATE로 교체 (낮은 확신 = 에스컬레이션).")
    W(f"**τ 결정 근거**: dev v2 기록 {tau_res['dev_n']}건에서 비용(default) 최소화.")
    W("")
    # dev sweep table
    W("dev v2 기록 τ 탐색:")
    W("")
    W("| τ | dev 비용 | dev Ask-F1 |")
    W("|---|---------|------------|")
    for row in tau_res.get("dev_sweep", []):
        marker = " ← 선택" if abs(row["tau"] - tau_res["best_tau"]) < 1e-9 else ""
        W(f"| {row['tau']:.2f} | {row['cost']:.2f} | {row['ask_f1']:.3f} |{marker}")
    W("")
    W(f"**선택된 τ = {tau_res['best_tau']:.2f}** (dev 비용 최솟값 = {_fmt(tau_res['dev_cost'], 2)})")
    W("")
    W("τ 적용 전후 eval 비교 (A1 raw vs τ 정책, default 가중치):")
    W("")
    W("| | 비용/문제 | Ask-F1 |")
    W("|---|---------|--------|")
    W(f"| A1 raw | {_fmt(tau_res['a1_eval_cost'], 2)} | {_fmt(tau_res['a1_eval_ask_f1'], 3)} |")
    W(f"| A1 (τ={tau_res['best_tau']:.2f}) | {_fmt(tau_res['eval_cost'], 2)} | {_fmt(tau_res['eval_ask_f1'], 3)} |")
    W("")
    W("*주의: dev v2 데이터 20건으로 τ를 정했으므로 신뢰 구간이 넓다.*")
    W("")

    # §6
    W("## 6. 5회 반복 답 일치율")
    W("")
    W("| 지표 | 값 |")
    W("|------|----|")
    W(f"| 전체 시나리오 수 | {rep_agr['n_scenarios']} |")
    W(f"| 5회 모두 같은 답 (완전 일치) | {rep_agr['full_agree']} ({rep_agr['full_agree_pct']*100:.1f}%) |")
    W(f"| 평균 최빈값 일치율 | {rep_agr['avg_consistency']*100:.1f}% |")
    W("")
    type_agree: dict[str, dict] = defaultdict(lambda: {"n": 0, "full": 0, "mf": []})
    for sid, actions in by_sid_actions.items():
        t = _type_of(sid)
        type_agree[t]["n"] += 1
        if len(set(actions)) == 1:
            type_agree[t]["full"] += 1
        modal = Counter(actions).most_common(1)[0][0]
        type_agree[t]["mf"].append(sum(1 for a in actions if a == modal) / len(actions))
    ta_rows = []
    for t in type_order:
        d = type_agree.get(t, {"n": 0, "full": 0, "mf": []})
        n = d["n"]
        f = d["full"]
        mf = d["mf"]
        ta_rows.append([t, str(n),
                        f"{f} ({f/n*100:.0f}%)" if n else "—",
                        _pct_str(sum(mf)/len(mf) if mf else None)])
    W(_md_table(["유형", "n", "완전 일치", "평균 최빈값 일치율"], ta_rows))
    W("")

    # footer
    W("---")
    W("")
    W("*eval-freeze-v1 코드 동결 준수. src/prompts/config 변경 없음.*")
    W(f"*B1~B4 단일 결정론적 실행. A1·A2 5회 반복, 부트스트랩 {_N_BOOT}회.*")

    _REPORT.write_text("\n".join(L), encoding="utf-8")
    print(f"report written → {_REPORT}")

    # stdout summary
    print("\n" + "=" * 60)
    systems_out = [("B1", baseline_rows["B1"]), ("B2", baseline_rows["B2"]),
                   ("B3", baseline_rows["B3"]), ("B4", baseline_rows["B4"]),
                   ("A1", a1_avg), ("A2", a2_avg)]
    for nm, m in systems_out:
        print(f"{nm:4s}  cost={_fmt(m.get('cost_mean'), 2):6s}  "
              f"acc={_pct_str(m.get('accuracy')):6s}  "
              f"ask_f1={_fmt(m.get('ask_f1'), 3)}")
    print(f"\nτ={tau_res['best_tau']:.2f}: eval cost {_fmt(tau_res['eval_cost'],2)}, "
          f"ask_f1 {_fmt(tau_res['eval_ask_f1'],3)}")
    print(f"test-problem candidates: {len(test_candidates)}")
    print(f"AUROC A1 failure={_fmt(a1_cal['auroc_failure'],3)} "
          f"action_needed={_fmt(a1_cal['auroc_action_needed'],3)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
