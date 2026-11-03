"""Full evaluation runner — eval-freeze-v1.

A1/A2 : 80 eval scenarios × 5 reps, 8 parallel workers.
        Rate-limit (429) → exponential backoff + retry.
        Cache makes re-runs skip already-completed LLM turns.
B1–B4 : 80 eval scenarios × 1 run each, deterministic.

Outputs (gitignored, never committed)
--------------------------------------
logs/eval-a1a2.jsonl       — header line + one JSON line per A1/A2 run
logs/eval-baselines.jsonl  — header line + one JSON line per baseline run

The commit hash and tag are written into each file's first line.
Scores are NOT computed here (see ADR 0013 §4.2).
"""
from __future__ import annotations

import concurrent.futures
import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import mautonomy.agent.evaluate as _eval_mod
from mautonomy.agent import DEFAULT_CONFIG, MuseSparkAdapter, evaluate_scenario
from mautonomy.harness import build_environment
from mautonomy.scenarios.generator import generate_eval_scenarios

import mautonomy.baselines.b1_always_escalate as _b1
import mautonomy.baselines.b2_always_schedule_pm as _b2
import mautonomy.baselines.b3_logistic as _b3
import mautonomy.baselines.b4_sensor_rule as _b4

_LOGS = _REPO / "logs"
_A1A2_PATH = _LOGS / "eval-a1a2.jsonl"
_BASE_PATH = _LOGS / "eval-baselines.jsonl"

_REPS = 5
_MAX_WORKERS = 8
_MAX_RETRIES = 6
_BACKOFF_START = 5  # seconds
_PROMPT_VERSION = "v2"
_PROGRESS_INTERVAL = 600  # 10 minutes in seconds

_write_lock = threading.Lock()
_orig_write_trace = _eval_mod._write_trace


def _locked_write_trace(entry: dict[str, Any], path: Path) -> None:
    with _write_lock:
        _orig_write_trace(entry, path)


_eval_mod._write_trace = _locked_write_trace


# ── git provenance ─────────────────────────────────────────────────────────────

def _git(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(_REPO)] + cmd,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _commit_hash() -> str:
    return _git(["rev-parse", "HEAD"])


def _commit_tag() -> str:
    return _git(["tag", "--points-at", "HEAD"])


# ── log helpers ────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_json(path: Path, obj: dict[str, Any]) -> None:
    with _write_lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n")


def _write_header(path: Path, commit: str, tag: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _append_json(path, {
            "type": "header",
            "commit_hash": commit,
            "tag": tag,
            "ts": _ts(),
        })


def _load_done_a1a2(path: Path) -> set[tuple[str, int]]:
    """Return set of (scenario_id, rep) pairs already recorded."""
    done: set[tuple[str, int]] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") in ("header", "summary"):
            continue
        sid = obj.get("scenario_id")
        rep = obj.get("rep")
        if sid and rep is not None:
            done.add((sid, int(rep)))
    return done


def _load_done_baselines(path: Path) -> set[tuple[str, str]]:
    """Return set of (scenario_id, baseline) pairs already recorded."""
    done: set[tuple[str, str]] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") in ("header", "summary"):
            continue
        sid = obj.get("scenario_id")
        bl = obj.get("baseline")
        if sid and bl:
            done.add((sid, bl))
    return done


# ── A1/A2 runner ───────────────────────────────────────────────────────────────

def _run_one_a1a2(
    scenario,
    adapter: MuseSparkAdapter,
    rep: int,
    counters: dict[str, Any],
    counters_lock: threading.Lock,
) -> None:
    backoff = _BACKOFF_START
    for attempt in range(_MAX_RETRIES):
        try:
            result = evaluate_scenario(
                scenario,
                adapter,
                rep=rep,
                config=DEFAULT_CONFIG,
                allow_eval=True,
                trace_path=_A1A2_PATH,
                prompt_version=_PROMPT_VERSION,
            )
            # evaluate_scenario already wrote the trace line via _locked_write_trace.
            with counters_lock:
                counters["done"] += 1
                if result.cost_usd is not None:
                    counters["total_cost"] += result.cost_usd
            return
        except RuntimeError as exc:
            msg = str(exc)
            if "429" in msg and attempt < _MAX_RETRIES - 1:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            # non-recoverable or exhausted retries
            _append_json(_A1A2_PATH, {
                "type": "error",
                "scenario_id": scenario.scenario_id,
                "rep": rep,
                "error": msg,
                "ts": _ts(),
            })
            with counters_lock:
                counters["done"] += 1
                counters["failed"] += 1
            return


# ── B1–B4 runner ───────────────────────────────────────────────────────────────

_BASELINES = {
    "B1": _b1.predict,
    "B2": _b2.predict,
    "B3": _b3.predict,
    "B4": _b4.predict,
}


def _run_baselines(scenarios, done_set: set[tuple[str, str]]) -> tuple[int, int]:
    total = 0
    failed = 0
    for scenario in scenarios:
        for name, fn in _BASELINES.items():
            if (scenario.scenario_id, name) in done_set:
                continue
            try:
                env = build_environment(scenario, allow_eval=True)
                out = fn(env)
                _append_json(_BASE_PATH, {
                    "scenario_id": scenario.scenario_id,
                    "baseline": name,
                    "ground_truth": scenario.ground_truth.action,
                    "action": out.action,
                    "confidence": out.confidence,
                    "ts": _ts(),
                })
                total += 1
            except Exception as exc:
                _append_json(_BASE_PATH, {
                    "type": "error",
                    "scenario_id": scenario.scenario_id,
                    "baseline": name,
                    "error": str(exc),
                    "ts": _ts(),
                })
                total += 1
                failed += 1
    return total, failed


# ── progress thread ────────────────────────────────────────────────────────────

def _progress_loop(
    a1a2_counters: dict[str, Any],
    a1a2_counters_lock: threading.Lock,
    total_a1a2: int,
    stop_event: threading.Event,
) -> None:
    while not stop_event.wait(timeout=_PROGRESS_INTERVAL):
        with a1a2_counters_lock:
            done = a1a2_counters["done"]
            failed = a1a2_counters["failed"]
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(f"[{now}] A1/A2: {done}/{total_a1a2} done ({failed} failures)")
        sys.stdout.flush()


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    commit = _commit_hash()
    tag = _commit_tag()
    print(f"commit: {commit}  tag: {tag or '(none)'}")
    print("generating eval scenarios …")
    scenarios = generate_eval_scenarios(seed=42)
    print(f"  {len(scenarios)} scenarios generated")

    _write_header(_A1A2_PATH, commit, tag)
    _write_header(_BASE_PATH, commit, tag)

    # ── A1/A2 ──────────────────────────────────────────────────────────────────
    done_a1a2 = _load_done_a1a2(_A1A2_PATH)
    tasks = [
        (sc, rep)
        for sc in scenarios
        for rep in range(1, _REPS + 1)
        if (sc.scenario_id, rep) not in done_a1a2
    ]
    total_a1a2 = len(scenarios) * _REPS
    skipped = total_a1a2 - len(tasks)
    if skipped:
        print(f"  A1/A2: resuming — {skipped} runs already done, {len(tasks)} remaining")

    counters: dict[str, Any] = {"done": skipped, "failed": 0, "total_cost": 0.0}
    counters_lock = threading.Lock()
    stop_event = threading.Event()

    progress_thread = threading.Thread(
        target=_progress_loop,
        args=(counters, counters_lock, total_a1a2, stop_event),
        daemon=True,
    )
    progress_thread.start()

    adapter = MuseSparkAdapter()
    print(f"running {len(tasks)} A1/A2 tasks (workers={_MAX_WORKERS}) …")
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = [
            pool.submit(_run_one_a1a2, sc, adapter, rep, counters, counters_lock)
            for sc, rep in tasks
        ]
        for f in concurrent.futures.as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                print(f"  unexpected error: {exc}", file=sys.stderr)

    stop_event.set()

    with counters_lock:
        a1a2_done = counters["done"]
        a1a2_failed = counters["failed"]
        a1a2_cost = counters["total_cost"]

    # ── B1–B4 ──────────────────────────────────────────────────────────────────
    done_baselines = _load_done_baselines(_BASE_PATH)
    total_b = len(scenarios) * len(_BASELINES)
    print(f"running baselines B1–B4 ({total_b} runs) …")
    b_total, b_failed = _run_baselines(scenarios, done_baselines)

    # ── summary ────────────────────────────────────────────────────────────────
    total_runs = a1a2_done + b_total
    total_failed = a1a2_failed + b_failed
    summary = {
        "type": "summary",
        "commit_hash": commit,
        "tag": tag,
        "a1a2_total": total_a1a2,
        "a1a2_failed": a1a2_failed,
        "a1a2_cost_usd": round(a1a2_cost, 6),
        "baselines_total": b_total,
        "baselines_failed": b_failed,
        "ts": _ts(),
    }
    _append_json(_A1A2_PATH, summary)
    _append_json(_BASE_PATH, summary)

    print()
    print("=" * 50)
    print(f"total runs  : {total_runs}")
    print(f"failures    : {total_failed}")
    print(f"total cost  : ${a1a2_cost:.6f} (A1/A2 only; baselines are free)")
    print("=" * 50)

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
