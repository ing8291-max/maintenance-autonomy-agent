"""Apply a scenario's perturbation to a telemetry slice.

Three kinds (schema.PerturbationKind):

* ``precursor_rise`` — adds ``(hours_since_start) * rise_rate_per_hour`` to
  the injected sensor for every row with ``datetime >= start_at``. If a
  ``correlated_sensor`` is set, it drifts linearly from 0 at ``start_at`` to
  ``correlated_delta`` at ``start_at + rise_duration_hours``.
* ``sensor_stuck`` — overwrites the injected sensor's value with
  ``stuck_at`` for every row from ``start_at`` onward.
* ``sensor_spike`` — adds ``spike_delta`` to the injected sensor on the
  single row at ``start_at``.

Rows before ``start_at`` pass through unchanged. The function copies rows
rather than mutating in place so the raw read-tool cache stays intact.
"""
from __future__ import annotations

from typing import Any

from mautonomy.scenarios.schema import Perturbation


def apply(
    rows: list[dict[str, Any]], perturbation: Perturbation
) -> list[dict[str, Any]]:
    """Return a new row list with ``perturbation`` applied to telemetry.

    Input rows must already be sliced to ``as_of``. The caller (environment)
    handles that cut; this function only transforms sensor values.
    """
    kind = perturbation.kind
    sensor = perturbation.sensor
    start_at = perturbation.start_at

    out: list[dict[str, Any]] = []
    for raw in rows:
        if raw["datetime"] < start_at:
            out.append(dict(raw))
            continue

        row = dict(raw)
        if kind == "precursor_rise":
            hours_in = (row["datetime"] - start_at).total_seconds() / 3600
            row[sensor] = row[sensor] + hours_in * perturbation.rise_rate_per_hour
            if (
                perturbation.correlated_sensor is not None
                and perturbation.correlated_delta is not None
                and perturbation.rise_duration_hours
            ):
                frac = hours_in / perturbation.rise_duration_hours
                row[perturbation.correlated_sensor] = (
                    row[perturbation.correlated_sensor]
                    + perturbation.correlated_delta * frac
                )
        elif kind == "sensor_stuck":
            row[sensor] = perturbation.stuck_at
        elif kind == "sensor_spike":
            if row["datetime"] == start_at:
                row[sensor] = row[sensor] + perturbation.spike_delta
            # Non-matching rows post-start_at still pass through unchanged
            # for spike (single-tick kind).
        out.append(row)

    return out
