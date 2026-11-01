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
# Lead-time bounds for the precursor: matches measured 35-47h window of
# real dev-machine failures (docs/synthesis-protocol.md § F2 patterns).
_F2_PRECURSOR_LEAD_MIN_HOURS = 35
_F2_PRECURSOR_LEAD_MAX_HOURS = 47
# Total rise of the vibration signal from start_at to as_of, in σ units.
_F2_PRECURSOR_RISE_SD = 2.0
# Correlated drift of rotate across the same window, in σ units.
_F2_CORRELATED_DELTA_SD = -1.5
_F2_STUCK_LEAD_HOURS = 18

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

        # Precursor lead time: sampled from the measured 35-47h band so that
        # the synthetic trace matches real dev-machine failure precursors.
        lead_hours = rng.randint(
            _F2_PRECURSOR_LEAD_MIN_HOURS, _F2_PRECURSOR_LEAD_MAX_HOURS
        )
        precursor_start = as_of - timedelta(hours=lead_hours)
        # Rise spans [start_at, as_of] exactly: total rise = _F2_PRECURSOR_RISE_SD · σ
        rise_rate = round(_F2_PRECURSOR_RISE_SD * vib_std / lead_hours, 4)
        correlated_delta = round(_F2_CORRELATED_DELTA_SD * rot_std, 2)

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
                    f"Vibration drifted +{_F2_PRECURSOR_RISE_SD:.1f}σ over "
                    f"{lead_hours}h with correlated rotate decline — real "
                    "precursor, schedule PM"
                ),
            ),
            failure_datetime=None,
            failure_component=None,
            # ADR 0006 timestamps on the injected timeline:
            #   anomaly_onset       = precursor injection start
            #   first_observable    = precursor injection start (visible from tick 0)
            #   point_of_no_return  = as_of (synthetic failure = as_of + 12h ⇒ PONR − 12h = as_of)
            timestamps=Timestamps(
                anomaly_onset=precursor_start,
                first_observable=precursor_start,
                point_of_no_return=as_of,
            ),
            perturbation=Perturbation(
                family="F2",
                kind="precursor_rise",
                sensor="vibration",
                start_at=precursor_start,
                description=(
                    f"vibration rises {rise_rate:.4f}/h for "
                    f"{lead_hours}h ({_F2_PRECURSOR_RISE_SD:.1f}σ total) with "
                    f"rotate drifting by {correlated_delta:.2f} over the same window"
                ),
                rise_rate_per_hour=rise_rate,
                rise_duration_hours=lead_hours,
                correlated_sensor="rotate",
                correlated_delta=correlated_delta,
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
