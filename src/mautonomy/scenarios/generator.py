"""Easy scenario generator — uses dev machines only (ADR 0007).

Timestamp derivation rules (documented in docs/synthesis-protocol.md):
  anomaly_onset:       first hour where 4h rolling mean of vibration > baseline_mean + 1*std
  first_observable:    first hour where vibration > baseline_mean + 2*std
  point_of_no_return:  failure_datetime - 12h

Baseline for pre-failure scenarios is the 30 days before (failure_datetime - 72h).

The F2 pair runs on a *normal* window (no failures within ±7 days of as_of)
with two injections: a precursor_rise (act → SCHEDULE_PM) and a sensor_stuck
(abstain → CHECK_SENSOR). Only the perturbation differs. See
docs/synthesis-protocol.md § F2 patterns.
"""
from __future__ import annotations

import csv
import json
import random
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from mautonomy.scenarios.schema import (
    GroundTruth,
    Perturbation,
    Scenario,
    SchemaError,
    Timestamps,
)

_RAW = Path(__file__).parents[3] / "data" / "raw"
_SPLITS_JSON = Path(__file__).parents[3] / "data" / "splits.json"
_DT_FMT = "%Y-%m-%d %H:%M:%S"

_BASELINE_DAYS = 30
_ROLLING_HOURS = 4
_ANOMALY_THRESHOLD_SD = 1.0
_OBSERVABLE_THRESHOLD_SD = 2.0
_POINT_OF_NO_RETURN_HOURS_BEFORE_FAILURE = 12

# F2 pair injection parameters (see docs/synthesis-protocol.md § F2 patterns)
_F2_NORMAL_HALF_WINDOW_DAYS = 7
# Precursor lead time (start_at → implied_failure). Matches the measured
# precursor-to-failure band on dev machines (docs/synthesis-protocol.md).
_F2_PRECURSOR_LEAD_MIN_HOURS = 35
_F2_PRECURSOR_LEAD_MAX_HOURS = 47
# How far before as_of the precursor begins: a visible-but-not-mature chunk
# of the trajectory. Keeps as_of strictly before the implied failure.
_F2_START_AT_OFFSET_MIN_HOURS = 6
_F2_START_AT_OFFSET_MAX_HOURS = 18
# Guard: implied_failure must be at least this far after as_of so that
# SCHEDULE_PM is still a legitimate "schedule, don't rush" answer.
_F2_MIN_IMPLIED_FAILURE_GAP_HOURS = 24
# Total rise of vibration from start_at to implied_failure, in σ. Tuned so
# that at a typical seed=42 offset/lead (12h/40h), the as_of exposure lands
# near the measured per-failure median of 0.74σ on dev machines (24-48h
# pre-failure window; see docs/synthesis-protocol.md § F2 precursor realism).
_F2_PRECURSOR_RISE_SD = 2.5
# Correlated drift of rotate across the SAME [start_at, implied_failure]
# window, in σ units. Tuned so as_of exposure ≈ measured rotate median
# (−0.81σ). Not the 30-day baseline — the baseline is only used to measure
# σ; the drift lives inside the injection window.
_F2_CORRELATED_DELTA_SD = -2.75
_F2_STUCK_LEAD_HOURS = 18
_F2_PONR_HOURS_BEFORE_FAILURE = 12

# Eval guard: evaluated once, then cached
_EVAL_MACHINES: set[int] | None = None


def _get_eval_machines() -> set[int]:
    global _EVAL_MACHINES
    if _EVAL_MACHINES is None:
        splits = json.loads(_SPLITS_JSON.read_text())
        _EVAL_MACHINES = set(splits["eval"])
    return _EVAL_MACHINES


def _load_dev_machines() -> list[int]:
    splits = json.loads(_SPLITS_JSON.read_text())
    return sorted(splits["dev"])


def _load_eval_machines() -> list[int]:
    splits = json.loads(_SPLITS_JSON.read_text())
    return sorted(splits["eval"])


def _load_telemetry(machine_id: int) -> list[tuple[datetime, float]]:
    result = []
    with (_RAW / "PdM_telemetry.csv").open() as f:
        for row in csv.DictReader(f):
            if int(row["machineID"]) == machine_id:
                dt = datetime.strptime(row["datetime"], _DT_FMT)
                result.append((dt, float(row["vibration"])))
    result.sort(key=lambda r: r[0])
    return result


def _load_telemetry_full(machine_id: int) -> list[dict[str, Any]]:
    result = []
    with (_RAW / "PdM_telemetry.csv").open() as f:
        for row in csv.DictReader(f):
            if int(row["machineID"]) == machine_id:
                result.append({
                    "datetime": datetime.strptime(row["datetime"], _DT_FMT),
                    "volt": float(row["volt"]),
                    "rotate": float(row["rotate"]),
                    "pressure": float(row["pressure"]),
                    "vibration": float(row["vibration"]),
                })
    result.sort(key=lambda r: r["datetime"])
    return result


def _sensor_stats(
    telemetry: list[dict[str, Any]],
    sensor: str,
    window_end: datetime,
    window_days: int = _BASELINE_DAYS,
) -> tuple[float, float]:
    window_start = window_end - timedelta(days=window_days)
    vals = [r[sensor] for r in telemetry if window_start <= r["datetime"] < window_end]
    if len(vals) < 2:
        return (40.0, 5.0) if sensor == "vibration" else (440.0, 55.0)
    return (statistics.mean(vals), statistics.stdev(vals))


def _load_failures(machine_id: int) -> list[tuple[datetime, str]]:
    result = []
    with (_RAW / "PdM_failures.csv").open() as f:
        for row in csv.DictReader(f):
            if int(row["machineID"]) == machine_id:
                dt = datetime.strptime(row["datetime"], _DT_FMT)
                result.append((dt, row["failure"].strip('"')))
    result.sort(key=lambda r: r[0])
    return result


def _baseline_stats(
    telemetry: list[tuple[datetime, float]],
    window_end: datetime,
    window_days: int = _BASELINE_DAYS,
) -> tuple[float, float]:
    window_start = window_end - timedelta(days=window_days)
    vals = [v for dt, v in telemetry if window_start <= dt < window_end]
    if len(vals) < 2:
        return (40.0, 5.0)
    return (statistics.mean(vals), statistics.stdev(vals))


def _infer_timestamps(
    telemetry: list[tuple[datetime, float]],
    failure_dt: datetime,
) -> Optional[Timestamps]:
    baseline_window_end = failure_dt - timedelta(hours=72)
    mean_v, std_v = _baseline_stats(telemetry, baseline_window_end)

    threshold_onset = mean_v + _ANOMALY_THRESHOLD_SD * std_v
    threshold_observable = mean_v + _OBSERVABLE_THRESHOLD_SD * std_v

    search_start = failure_dt - timedelta(hours=72)
    window_data = [
        (dt, v) for dt, v in telemetry if search_start <= dt < failure_dt
    ]
    if not window_data:
        return None

    # anomaly_onset: first time 4h rolling mean > threshold_onset
    anomaly_onset: Optional[datetime] = None
    for i, (dt, _) in enumerate(window_data):
        window_vals = [window_data[j][1] for j in range(max(0, i - _ROLLING_HOURS + 1), i + 1)]
        if statistics.mean(window_vals) > threshold_onset:
            anomaly_onset = dt
            break

    # first_observable: first single reading > threshold_observable
    first_observable: Optional[datetime] = None
    for dt, v in window_data:
        if v > threshold_observable:
            first_observable = dt
            break

    if anomaly_onset is None or first_observable is None:
        return None

    # Ensure ordering: anomaly_onset <= first_observable
    if anomaly_onset > first_observable:
        anomaly_onset = first_observable

    point_of_no_return = failure_dt - timedelta(hours=_POINT_OF_NO_RETURN_HOURS_BEFORE_FAILURE)

    # point_of_no_return must be >= first_observable
    if point_of_no_return < first_observable:
        point_of_no_return = first_observable

    return Timestamps(
        anomaly_onset=anomaly_onset,
        first_observable=first_observable,
        point_of_no_return=point_of_no_return,
    )


def _normal_as_of(
    machine_id: int,
    failures: list[tuple[datetime, str]],
    rng: random.Random,
    data_start: datetime,
    data_end: datetime,
    min_gap_days: int = 30,
) -> Optional[datetime]:
    failure_times = [dt for dt, _ in failures]

    # Collect candidate as_of slots: windows with no failure ±30 days
    candidates: list[datetime] = []
    cursor = data_start + timedelta(days=min_gap_days)
    while cursor < data_end - timedelta(days=min_gap_days):
        too_close = any(
            abs((cursor - ft).total_seconds()) < min_gap_days * 86400
            for ft in failure_times
        )
        if not too_close:
            candidates.append(cursor)
        cursor += timedelta(hours=24)

    if not candidates:
        return None
    return rng.choice(candidates)


def generate_easy_scenarios(seed: int = 42) -> list[Scenario]:
    """Generate easy (natural-distribution) scenarios from dev machines.

    Returns one scenario of each type:
      - normal
      - pre_failure_24_48h
      - pre_failure_0_24h
    Plus one F2 hard-case pair (act + abstain), totalling 5 scenarios.

    Same seed always produces identical output (criterion 2).
    Only dev machines are used (criterion 3).
    """
    rng = random.Random(seed)
    dev_machines = _load_dev_machines()
    eval_machines = _get_eval_machines()

    # Sanity: dev_machines must not overlap with eval
    for mid in dev_machines:
        if mid in eval_machines:
            raise SchemaError(f"Dev machine {mid} found in eval split — splits corrupted")

    data_start = datetime(2015, 1, 1)
    data_end = datetime(2016, 1, 1)

    # Deterministically pick machines for each role
    machines_shuffled = sorted(dev_machines)
    rng.shuffle(machines_shuffled)

    scenarios: list[Scenario] = []

    # --- 1. Normal scenario ---
    normal_machine = _pick_normal_machine(machines_shuffled, rng, data_start, data_end)
    if normal_machine:
        mid, as_of = normal_machine
        scenarios.append(Scenario(
            schema_version="1.0",
            scenario_id=f"dev-easy-normal-{seed:04d}-{mid:04d}",
            scenario_type="normal",
            machine_id=mid,
            split="dev",
            as_of=as_of,
            seed=seed,
            ground_truth=GroundTruth(
                action="NO_ACTION",
                rationale="No anomaly signals present in the observation window",
            ),
            failure_datetime=None,
            failure_component=None,
            timestamps=None,
            perturbation=None,
            pair_id=None,
            pair_role=None,
        ))

    # --- 2 & 3. Pre-failure scenarios (no F2 pair here) ---
    failure_scenarios = _pick_failure_scenarios(machines_shuffled, rng, seed)
    scenarios.extend(failure_scenarios)

    # --- 4. F2 pair on a normal window (independent of failure scenarios) ---
    f2 = _pick_f2_pair(machines_shuffled, rng, seed, data_start, data_end)
    if f2 is not None:
        scenarios.extend(f2)

    return scenarios


# ADR 0013 — eval composition (pre-registered)
_EVAL_NORMAL_CAP = 14
_EVAL_PRE48_CAP = 13
_EVAL_PRE24_CAP = 13
_EVAL_F2_PAIR_CAP = 20


def generate_eval_scenarios(seed: int = 42) -> list[Scenario]:
    """Generate the pre-registered eval scenarios on eval-split machines.

    Composition is fixed by ADR 0013: 14 normal + 13 pre_failure_24_48h +
    13 pre_failure_0_24h + 20 F2 pairs (= 40 scenarios) = 80 total. Each
    eval machine contributes at most one scenario per type; machine order
    is the same seed-shuffled list used elsewhere in this module. Determinism
    is per (seed, splits.json).
    """
    rng = random.Random(seed)
    eval_machines = _load_eval_machines()
    machines_shuffled = sorted(eval_machines)
    rng.shuffle(machines_shuffled)

    data_start = datetime(2015, 1, 1)
    data_end = datetime(2016, 1, 1)

    scenarios: list[Scenario] = []

    # --- Normal (cap 14) ---
    normal_rng = random.Random(seed)  # mirrors _normal_as_of consumption order
    normal_count = 0
    for mid in machines_shuffled:
        if normal_count >= _EVAL_NORMAL_CAP:
            break
        failures = _load_failures(mid)
        as_of = _normal_as_of(mid, failures, normal_rng, data_start, data_end)
        if as_of is None:
            continue
        scenarios.append(Scenario(
            schema_version="1.0",
            scenario_id=f"eval-easy-normal-{seed:04d}-{mid:04d}",
            scenario_type="normal",
            machine_id=mid,
            split="eval",
            as_of=as_of,
            seed=seed,
            ground_truth=GroundTruth(
                action="NO_ACTION",
                rationale="No anomaly signals present in the observation window",
            ),
            failure_datetime=None,
            failure_component=None,
            timestamps=None,
            perturbation=None,
            pair_id=None,
            pair_role=None,
        ))
        normal_count += 1

    # --- pre_48h and pre_24h (caps 13 / 13) ---
    pre48_count = 0
    pre24_count = 0
    for mid in machines_shuffled:
        if pre48_count >= _EVAL_PRE48_CAP and pre24_count >= _EVAL_PRE24_CAP:
            break
        failures = _load_failures(mid)
        if not failures:
            continue
        telemetry = _load_telemetry(mid)
        picked48 = False
        picked24 = False
        for failure_dt, failure_comp in failures:
            if picked48 and picked24:
                break
            ts = _infer_timestamps(telemetry, failure_dt)
            if ts is None:
                continue
            if not picked48 and pre48_count < _EVAL_PRE48_CAP:
                as_of_36h = failure_dt - timedelta(hours=36)
                if as_of_36h > ts.anomaly_onset:
                    scenarios.append(Scenario(
                        schema_version="1.0",
                        scenario_id=f"eval-easy-pre48h-{seed:04d}-{mid:04d}",
                        scenario_type="pre_failure_24_48h",
                        machine_id=mid,
                        split="eval",
                        as_of=as_of_36h,
                        seed=seed,
                        ground_truth=GroundTruth(
                            action="SCHEDULE_PM",
                            rationale=(
                                f"Vibration drift detected; failure of {failure_comp} "
                                f"expected within 48h"
                            ),
                        ),
                        failure_datetime=failure_dt,
                        failure_component=failure_comp,
                        timestamps=ts,
                        perturbation=None,
                        pair_id=None,
                        pair_role=None,
                    ))
                    pre48_count += 1
                    picked48 = True
            if not picked24 and pre24_count < _EVAL_PRE24_CAP:
                as_of_12h = failure_dt - timedelta(hours=12)
                if as_of_12h >= ts.anomaly_onset:
                    scenarios.append(Scenario(
                        schema_version="1.0",
                        scenario_id=f"eval-easy-pre24h-{seed:04d}-{mid:04d}",
                        scenario_type="pre_failure_0_24h",
                        machine_id=mid,
                        split="eval",
                        as_of=as_of_12h,
                        seed=seed,
                        ground_truth=GroundTruth(
                            action="ESCALATE",
                            rationale=(
                                f"Imminent failure of {failure_comp}; "
                                "vibration at critical level, escalate immediately"
                            ),
                        ),
                        failure_datetime=failure_dt,
                        failure_component=failure_comp,
                        timestamps=ts,
                        perturbation=None,
                        pair_id=None,
                        pair_role=None,
                    ))
                    pre24_count += 1
                    picked24 = True

    # --- F2 pairs (cap 20 pairs = 40 scenarios) ---
    f2_rng = random.Random(seed + 1)  # distinct stream from normal_rng
    f2_pairs_count = 0
    for mid in machines_shuffled:
        if f2_pairs_count >= _EVAL_F2_PAIR_CAP:
            break
        pair = _pick_f2_pair_for_machine(mid, f2_rng, seed)
        if pair is None:
            continue
        # Re-tag ids with the eval prefix
        pair = [_retag_eval_f2(sc) for sc in pair]
        scenarios.extend(pair)
        f2_pairs_count += 1

    return scenarios


def _pick_f2_pair_for_machine(
    mid: int,
    rng: random.Random,
    seed: int,
) -> Optional[list[Scenario]]:
    """F2 act/abstain pair bound to a specific machine (eval generator helper).

    Mirrors _pick_f2_pair but takes mid explicitly instead of searching the
    machine list, so the eval generator can enumerate eval machines directly.
    """
    failures = _load_failures(mid)
    telemetry = _load_telemetry_full(mid)
    if not telemetry:
        return None
    times = [r["datetime"] for r in telemetry]
    as_of = _normal_window_as_of(times, failures, rng)
    if as_of is None:
        return None

    vib_mean, vib_std = _sensor_stats(telemetry, "vibration", as_of)
    rot_mean, rot_std = _sensor_stats(telemetry, "rotate", as_of)
    if vib_std <= 0 or rot_std <= 0:
        return None

    pair_id = f"f2-pair-{seed:04d}-{mid:04d}"
    start_at_offset = rng.randint(
        _F2_START_AT_OFFSET_MIN_HOURS, _F2_START_AT_OFFSET_MAX_HOURS
    )
    precursor_start = as_of - timedelta(hours=start_at_offset)
    min_lead = max(
        _F2_PRECURSOR_LEAD_MIN_HOURS,
        start_at_offset + _F2_MIN_IMPLIED_FAILURE_GAP_HOURS,
    )
    lead_hours = rng.randint(min_lead, _F2_PRECURSOR_LEAD_MAX_HOURS)
    implied_failure = precursor_start + timedelta(hours=lead_hours)
    rise_rate = round(_F2_PRECURSOR_RISE_SD * vib_std / lead_hours, 4)
    total_rise_at_as_of_sd = round(
        _F2_PRECURSOR_RISE_SD * start_at_offset / lead_hours, 3
    )
    correlated_delta = round(_F2_CORRELATED_DELTA_SD * rot_std, 2)
    point_of_no_return = implied_failure - timedelta(
        hours=_F2_PONR_HOURS_BEFORE_FAILURE
    )

    act = Scenario(
        schema_version="1.0",
        scenario_id=f"dev-f2-act-{seed:04d}-{mid:04d}",
        scenario_type="hardcase",
        machine_id=mid,
        split="dev",
        as_of=as_of,
        seed=seed,
        ground_truth=GroundTruth(
            action="SCHEDULE_PM",
            rationale=(
                f"Vibration drifting +{total_rise_at_as_of_sd:.2f}σ since "
                f"{start_at_offset}h ago with correlated rotate decline; "
                f"implied failure ~{lead_hours - start_at_offset}h away — "
                "schedule PM"
            ),
        ),
        failure_datetime=None,
        failure_component=None,
        timestamps=Timestamps(
            anomaly_onset=precursor_start,
            first_observable=precursor_start,
            point_of_no_return=point_of_no_return,
        ),
        perturbation=Perturbation(
            family="F2",
            kind="precursor_rise",
            sensor="vibration",
            start_at=precursor_start,
            description=(
                f"vibration rises {rise_rate:.4f}/h for {lead_hours}h to "
                f"+{_F2_PRECURSOR_RISE_SD:.1f}σ at implied failure; by as_of "
                f"({start_at_offset}h in) the signal has moved "
                f"+{total_rise_at_as_of_sd:.2f}σ. rotate drifts "
                f"{correlated_delta:.2f} over the same [start_at, "
                "implied_failure] window"
            ),
            rise_rate_per_hour=rise_rate,
            rise_duration_hours=lead_hours,
            correlated_sensor="rotate",
            correlated_delta=correlated_delta,
            implied_failure=implied_failure,
        ),
        pair_id=pair_id,
        pair_role="act",
    )

    stuck_start = as_of - timedelta(hours=_F2_STUCK_LEAD_HOURS)
    abstain = Scenario(
        schema_version="1.0",
        scenario_id=f"dev-f2-abstain-{seed:04d}-{mid:04d}",
        scenario_type="hardcase",
        machine_id=mid,
        split="dev",
        as_of=as_of,
        seed=seed,
        ground_truth=GroundTruth(
            action="CHECK_SENSOR",
            rationale=(
                f"Vibration sensor frozen since "
                f"{stuck_start.strftime(_DT_FMT)} while rotate/pressure "
                "stay nominal — verify sensor before scheduling PM"
            ),
        ),
        failure_datetime=None,
        failure_component=None,
        timestamps=None,
        perturbation=Perturbation(
            family="F2",
            kind="sensor_stuck",
            sensor="vibration",
            start_at=stuck_start,
            description=(
                f"vibration sensor stuck at baseline mean "
                f"({vib_mean:.2f}); other signals unchanged"
            ),
            stuck_at=round(vib_mean, 2),
        ),
        pair_id=pair_id,
        pair_role="abstain",
    )
    return [act, abstain]


def _retag_eval_f2(sc: Scenario) -> Scenario:
    """Rewrite an F2 pair member built on an eval machine to carry eval ids."""
    new_id = sc.scenario_id.replace("dev-f2-", "eval-f2-", 1)
    from dataclasses import replace
    return replace(sc, scenario_id=new_id, split="eval")


def _pick_normal_machine(
    machines: list[int],
    rng: random.Random,
    data_start: datetime,
    data_end: datetime,
) -> Optional[tuple[int, datetime]]:
    for mid in machines:
        failures = _load_failures(mid)
        as_of = _normal_as_of(mid, failures, rng, data_start, data_end)
        if as_of is not None:
            return (mid, as_of)
    return None


def _pick_failure_scenarios(
    machines: list[int],
    rng: random.Random,
    seed: int,
) -> list[Scenario]:
    scenarios: list[Scenario] = []
    pre_24_48_done = False
    pre_0_24_done = False

    for mid in machines:
        if pre_24_48_done and pre_0_24_done:
            break
        failures = _load_failures(mid)
        if not failures:
            continue
        telemetry = _load_telemetry(mid)

        for failure_dt, failure_comp in failures:
            if pre_24_48_done and pre_0_24_done:
                break

            ts = _infer_timestamps(telemetry, failure_dt)
            if ts is None:
                continue

            # pre_failure_24_48h: as_of at failure_dt - 36h (middle of window)
            if not pre_24_48_done:
                as_of_36h = failure_dt - timedelta(hours=36)
                if as_of_36h > ts.anomaly_onset:
                    scenarios.append(Scenario(
                        schema_version="1.0",
                        scenario_id=f"dev-easy-pre48h-{seed:04d}-{mid:04d}",
                        scenario_type="pre_failure_24_48h",
                        machine_id=mid,
                        split="dev",
                        as_of=as_of_36h,
                        seed=seed,
                        ground_truth=GroundTruth(
                            action="SCHEDULE_PM",
                            rationale=(
                                f"Vibration drift detected; failure of {failure_comp} "
                                f"expected within 48h"
                            ),
                        ),
                        failure_datetime=failure_dt,
                        failure_component=failure_comp,
                        timestamps=ts,
                        perturbation=None,
                        pair_id=None,
                        pair_role=None,
                    ))
                    pre_24_48_done = True

            # pre_failure_0_24h: as_of at failure_dt - 12h
            if not pre_0_24_done:
                as_of_12h = failure_dt - timedelta(hours=12)
                if as_of_12h >= ts.anomaly_onset:
                    scenarios.append(Scenario(
                        schema_version="1.0",
                        scenario_id=f"dev-easy-pre24h-{seed:04d}-{mid:04d}",
                        scenario_type="pre_failure_0_24h",
                        machine_id=mid,
                        split="dev",
                        as_of=as_of_12h,
                        seed=seed,
                        ground_truth=GroundTruth(
                            action="ESCALATE",
                            rationale=(
                                f"Imminent failure of {failure_comp}; "
                                "vibration at critical level, escalate immediately"
                            ),
                        ),
                        failure_datetime=failure_dt,
                        failure_component=failure_comp,
                        timestamps=ts,
                        perturbation=None,
                        pair_id=None,
                        pair_role=None,
                    ))
                    pre_0_24_done = True

    return scenarios


def _normal_window_as_of(
    telemetry_times: list[datetime],
    failures: list[tuple[datetime, str]],
    rng: random.Random,
    half_window_days: int = _F2_NORMAL_HALF_WINDOW_DAYS,
) -> Optional[datetime]:
    """Return an as_of inside a window with no failure within ±half_window_days.

    The as_of is sampled from telemetry timestamps so stats and injection points
    land on actual data rows.
    """
    if not telemetry_times:
        return None
    data_start = telemetry_times[0]
    data_end = telemetry_times[-1]
    failure_times = [dt for dt, _ in failures]

    gap = timedelta(days=half_window_days)
    low = data_start + gap + timedelta(days=_BASELINE_DAYS)
    high = data_end - gap
    candidates = [
        dt for dt in telemetry_times
        if low <= dt <= high
        and all(abs((dt - ft).total_seconds()) >= gap.total_seconds() for ft in failure_times)
    ]
    if not candidates:
        return None
    return rng.choice(candidates)


def _pick_f2_pair(
    machines: list[int],
    rng: random.Random,
    seed: int,
    data_start: datetime,
    data_end: datetime,
) -> Optional[list[Scenario]]:
    """Build the F2 act/abstain pair on a normal window of a dev machine.

    Both members share scenario_type=hardcase, same as_of, same machine. The
    only causal difference is the perturbation kind:
      - act:     precursor_rise on vibration with correlated rotate drift →
                 SCHEDULE_PM
      - abstain: sensor_stuck on vibration starting a few hours before as_of →
                 CHECK_SENSOR
    """
    for mid in machines:
        failures = _load_failures(mid)
        telemetry = _load_telemetry_full(mid)
        if not telemetry:
            continue
        times = [r["datetime"] for r in telemetry]
        as_of = _normal_window_as_of(times, failures, rng)
        if as_of is None:
            continue

        vib_mean, vib_std = _sensor_stats(telemetry, "vibration", as_of)
        rot_mean, rot_std = _sensor_stats(telemetry, "rotate", as_of)
        if vib_std <= 0 or rot_std <= 0:
            continue

        pair_id = f"f2-pair-{seed:04d}-{mid:04d}"

        # Precursor starts a short time before as_of (6-18h), so the agent at
        # as_of sees a partial, not fully-mature trajectory.
        start_at_offset = rng.randint(
            _F2_START_AT_OFFSET_MIN_HOURS, _F2_START_AT_OFFSET_MAX_HOURS
        )
        precursor_start = as_of - timedelta(hours=start_at_offset)
        # Lead = start_at → implied_failure, in the measured 35-47h band.
        # Also require implied_failure ≥ as_of + 24h so SCHEDULE_PM remains the
        # right answer (fail is near but not immediate).
        min_lead = max(
            _F2_PRECURSOR_LEAD_MIN_HOURS,
            start_at_offset + _F2_MIN_IMPLIED_FAILURE_GAP_HOURS,
        )
        lead_hours = rng.randint(min_lead, _F2_PRECURSOR_LEAD_MAX_HOURS)
        implied_failure = precursor_start + timedelta(hours=lead_hours)
        # Rise reaches +2σ at implied_failure. At as_of the signal has moved
        # (2σ · start_at_offset / lead_hours) — a partial precursor.
        rise_rate = round(_F2_PRECURSOR_RISE_SD * vib_std / lead_hours, 4)
        total_rise_at_as_of_sd = round(
            _F2_PRECURSOR_RISE_SD * start_at_offset / lead_hours, 3
        )
        correlated_delta = round(_F2_CORRELATED_DELTA_SD * rot_std, 2)
        point_of_no_return = implied_failure - timedelta(
            hours=_F2_PONR_HOURS_BEFORE_FAILURE
        )

        act = Scenario(
            schema_version="1.0",
            scenario_id=f"dev-f2-act-{seed:04d}-{mid:04d}",
            scenario_type="hardcase",
            machine_id=mid,
            split="dev",
            as_of=as_of,
            seed=seed,
            ground_truth=GroundTruth(
                action="SCHEDULE_PM",
                rationale=(
                    f"Vibration drifting +{total_rise_at_as_of_sd:.2f}σ since "
                    f"{start_at_offset}h ago with correlated rotate decline; "
                    f"implied failure ~{lead_hours - start_at_offset}h away — "
                    "schedule PM"
                ),
            ),
            failure_datetime=None,
            failure_component=None,
            # ADR 0006 timestamps on the injected timeline:
            #   anomaly_onset       = precursor injection start
            #   first_observable    = precursor injection start (visible from tick 0)
            #   point_of_no_return  = implied_failure - 12h  (strictly after as_of)
            timestamps=Timestamps(
                anomaly_onset=precursor_start,
                first_observable=precursor_start,
                point_of_no_return=point_of_no_return,
            ),
            perturbation=Perturbation(
                family="F2",
                kind="precursor_rise",
                sensor="vibration",
                start_at=precursor_start,
                description=(
                    f"vibration rises {rise_rate:.4f}/h for {lead_hours}h to "
                    f"+{_F2_PRECURSOR_RISE_SD:.1f}σ at implied failure; by as_of "
                    f"({start_at_offset}h in) the signal has moved "
                    f"+{total_rise_at_as_of_sd:.2f}σ. rotate drifts "
                    f"{correlated_delta:.2f} over the same [start_at, "
                    "implied_failure] window"
                ),
                rise_rate_per_hour=rise_rate,
                rise_duration_hours=lead_hours,
                correlated_sensor="rotate",
                correlated_delta=correlated_delta,
                implied_failure=implied_failure,
            ),
            pair_id=pair_id,
            pair_role="act",
        )

        stuck_start = as_of - timedelta(hours=_F2_STUCK_LEAD_HOURS)
        abstain = Scenario(
            schema_version="1.0",
            scenario_id=f"dev-f2-abstain-{seed:04d}-{mid:04d}",
            scenario_type="hardcase",
            machine_id=mid,
            split="dev",
            as_of=as_of,
            seed=seed,
            ground_truth=GroundTruth(
                action="CHECK_SENSOR",
                rationale=(
                    f"Vibration sensor frozen since "
                    f"{stuck_start.strftime(_DT_FMT)} while rotate/pressure "
                    "stay nominal — verify sensor before scheduling PM"
                ),
            ),
            failure_datetime=None,
            failure_component=None,
            timestamps=None,
            perturbation=Perturbation(
                family="F2",
                kind="sensor_stuck",
                sensor="vibration",
                start_at=stuck_start,
                description=(
                    f"vibration sensor stuck at baseline mean "
                    f"({vib_mean:.2f}); other signals unchanged"
                ),
                stuck_at=round(vib_mean, 2),
            ),
            pair_id=pair_id,
            pair_role="abstain",
        )
        return [act, abstain]

    return None
