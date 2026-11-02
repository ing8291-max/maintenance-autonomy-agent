"""Comparison baselines B1–B4 for the agent evaluation (ADR 0005).

Each baseline exposes:
  predict(machine_id: int, as_of: datetime, *, allow_eval: bool = False)
      -> BaselineOutput

All baselines use only as_of-aware read tools; none read scenario YAML
ground_truth or perturbation fields. Baselines without a calibrated
probability leave confidence=None — ECE calculation must skip null-conf rows.
"""
from mautonomy.baselines.base import BaselineOutput
from mautonomy.baselines.b1_always_escalate import predict as b1_predict
from mautonomy.baselines.b2_always_schedule_pm import predict as b2_predict
from mautonomy.baselines.b3_logistic import predict as b3_predict
from mautonomy.baselines.b4_sensor_rule import predict as b4_predict

__all__ = [
    "BaselineOutput",
    "b1_predict",
    "b2_predict",
    "b3_predict",
    "b4_predict",
]
