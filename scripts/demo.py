#!/usr/bin/env python3
"""Demo: replay one F2 pair from local logs and sensor data.

Picks an F2 pair where the agent correctly identified the stuck-sensor
(abstain) but missed the vibration drift that warranted PM scheduling
(act).  No new API calls are made — all data comes from logs/ and the
sensor parquet files already on disk.

Usage:
    python scripts/demo.py [PAIR_NUM]

PAIR_NUM defaults to 0025.  Any eval F2 pair number works, e.g. 0041.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from mautonomy.agent.adapter import ToolCall
from mautonomy.agent.tools import dispatch
from mautonomy.harness.environment import build_environment
from mautonomy.scenarios.loader import load_scenario
from mautonomy.scoring import ScoredRun, cost_of, executed_of, load_config

DEMO_PAIR = sys.argv[1] if len(sys.argv) > 1 else "0025"

_STANDARD_TOOLS = [
    "read_telemetry(72h)",
    "read_errors(72h)",
    "read_maintenance_history(8760h)",
    "read_failure_history(8760h)",
]

_COST_LABEL: dict[int, str] = {
    0: "✓ correct (0)",
    1: "overescalation (1)",
    10: "overstop (10)",
    100: "miss (100)",
}

_MACHINE_SALT = 0xA3F7


def _mask(machine_id: int) -> str:
    return f"M-{(machine_id ^ _MACHINE_SALT) % 900 + 100:03d}"


def _load_eval_log() -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = {}
    with open(_REPO / "logs" / "eval-a1a2.jsonl") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("type") in ("header", "summary", "error"):
                continue
            rows.setdefault(obj["scenario_id"], []).append(obj)
    return rows


def _rep1(reps: list[dict]) -> dict:
    for r in reps:
        if r["rep"] == 1:
            return r
    return reps[0]


def _modal(reps: list[dict]) -> tuple[str, int]:
    c = Counter(r["a1_action"] for r in reps)
    action, count = c.most_common(1)[0]
    return action, count


def _telemetry(scenario) -> dict:
    env = build_environment(scenario, allow_eval=True)
    raw = dispatch(env, ToolCall(name="read_telemetry", arguments={"hours_back": 72}))
    return json.loads(raw)["summary"]


def _show(title: str, scenario, run: dict, cfg) -> None:
    action = run["a1_action"]
    a2_verdict = run["a2_verdict"]
    a2_rules = run.get("a2_triggered_rules", [])
    executed = executed_of(action, a2_verdict)
    penalty = cost_of(scenario.ground_truth.action, executed, config=cfg)

    mid = _mask(scenario.machine_id)
    sid_masked = scenario.scenario_id.replace(f"{scenario.machine_id:04d}", mid)

    print(f"\n{'━' * 62}")
    print(f"  {title}")
    print(f"  설비 {mid}  ·  {sid_masked}")
    print(f"{'━' * 62}")

    n_tools = run["tool_calls"]
    print(f"\n[1] 도구 호출  ({n_tools}개, step 1에서 일괄 실행)")
    for t in _STANDARD_TOOLS[:n_tools]:
        print(f"     • {t}")

    print("\n[2] 텔레메트리 요약 (24h 창, 30일 베이스라인 기준)")
    summary = _telemetry(scenario)
    print(f"     {'센서':<12}  {'z-score':>8}  {'slope/h':>9}")
    print(f"     {'──────────':<12}  {'────────':>8}  {'─────────':>9}")
    for sensor in ("vibration", "rotate", "volt", "pressure"):
        s = summary[sensor]
        z = f"{s['zscore_24h']:+.3f}" if s["zscore_24h"] is not None else "   N/A"
        sl = f"{s['slope_per_hour_24h']:+.4f}" if s["slope_per_hour_24h"] is not None else "    N/A"
        print(f"     {sensor:<12}  {z:>8}  {sl:>9}")

    print("\n[3] 최종 JSON 답  (rep 1 / 5)")
    print(f"     action     : {action}")
    print(f"     confidence : {run['a1_confidence']:.2f}")
    print(f"     steps_used : {run['steps_used']}")

    print("\n[4] 게이트 판정 (A2)")
    print(f"     verdict  : {a2_verdict}")
    rules_str = ", ".join(a2_rules) if a2_rules else "없음"
    print(f"     rules    : {rules_str}")
    print(f"     executed : {executed}")

    print("\n[5] 정답 / 벌점")
    print(f"     ground truth : {scenario.ground_truth.action}")
    print(f"     penalty      : {_COST_LABEL.get(penalty, str(penalty))}")


def main() -> None:
    log = _load_eval_log()
    cfg = load_config()

    act_sid = f"eval-f2-act-0042-{DEMO_PAIR}"
    abs_sid = f"eval-f2-abstain-0042-{DEMO_PAIR}"

    for sid in (act_sid, abs_sid):
        if sid not in log:
            sys.exit(f"오류: {sid} 를 logs/eval-a1a2.jsonl 에서 찾을 수 없습니다.")

    act_sc = load_scenario(_REPO / "data" / "synth" / f"{act_sid}.yaml", allow_eval=True)
    abs_sc = load_scenario(_REPO / "data" / "synth" / f"{abs_sid}.yaml", allow_eval=True)

    act_run = _rep1(log[act_sid])
    abs_run = _rep1(log[abs_sid])

    print(f"\n{'=' * 62}")
    print(f"  F2 데모  ·  pair {DEMO_PAIR}")
    print(f"  같은 설비, 같은 as_of, 다른 perturbation 두 개")
    print(f"{'=' * 62}")
    print(f"  F2-act    : 진동 drift → SCHEDULE_PM 필요 → 에이전트 놓침")
    print(f"  F2-abstain: 센서 고착 → CHECK_SENSOR 필요 → 에이전트 맞힘")

    _show("F2-act  (놓침)", act_sc, act_run, cfg)
    _show("F2-abstain  (맞힘)", abs_sc, abs_run, cfg)

    print(f"\n{'=' * 62}")
    print("  5회 반복 모달 집계")
    print(f"{'=' * 62}")
    act_modal, act_n = _modal(log[act_sid])
    abs_modal, abs_n = _modal(log[abs_sid])
    print(f"  act    : {act_n}/5 reps → {act_modal}  (정답: SCHEDULE_PM)")
    print(f"  abstain: {abs_n}/5 reps → {abs_modal}  (정답: CHECK_SENSOR)")
    pair_ok = act_modal == "SCHEDULE_PM" and abs_modal == "CHECK_SENSOR"
    print(f"  pair accuracy : {'✓' if pair_ok else '✗'}")
    print()


if __name__ == "__main__":
    main()
