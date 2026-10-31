"""Generator tests.

Completion criteria tested here:
  (1) as_of < failure_datetime for all non-normal scenarios
  (2) same seed → same scenarios
  (3) eval machines not used (guard test)
  (4) F2 pair one causal diff
  (5) all generated files pass loader
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

_REPO_ROOT = Path(__file__).parents[1]
_SPLITS = json.loads((_REPO_ROOT / "data" / "splits.json").read_text())
_EVAL_MACHINES: set[int] = set(_SPLITS["eval"])

_CAUSAL_FIELDS = frozenset(
    ["scenario_type", "machine_id", "split", "as_of", "seed",
     "failure_datetime", "failure_component", "timestamps", "perturbation"]
)


@pytest.fixture(scope="module")
def generated():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    return generate_easy_scenarios(seed=42)


@pytest.fixture(scope="module")
def generated_again():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    return generate_easy_scenarios(seed=42)


# ---------------------------------------------------------------------------
# Criterion (2): same seed → identical scenarios
# ---------------------------------------------------------------------------


def test_same_seed_same_count(generated, generated_again):
    assert len(generated) == len(generated_again)


def test_same_seed_same_ids(generated, generated_again):
    ids_a = [s.scenario_id for s in generated]
    ids_b = [s.scenario_id for s in generated_again]
    assert ids_a == ids_b


def test_same_seed_same_as_of(generated, generated_again):
    for a, b in zip(generated, generated_again):
        assert a.as_of == b.as_of


# ---------------------------------------------------------------------------
# Criterion (3): eval machines never used
# ---------------------------------------------------------------------------


def test_no_eval_machine_in_generated(generated):
    for sc in generated:
        assert sc.machine_id not in _EVAL_MACHINES, (
            f"scenario {sc.scenario_id} uses eval machine {sc.machine_id}"
        )


def test_eval_machine_guard_in_generator():
    from mautonomy.scenarios.generator import generate_easy_scenarios
    from mautonomy.scenarios.schema import SchemaError
    # generator must only draw from dev split; eval guard in loader catches slip-through
    scenarios = generate_easy_scenarios(seed=42)
    for sc in scenarios:
        assert sc.machine_id not in _EVAL_MACHINES


# ---------------------------------------------------------------------------
# Criterion (1): as_of < failure_datetime
# ---------------------------------------------------------------------------


def test_as_of_before_failure_all(generated):
    for sc in generated:
        if sc.failure_datetime is not None:
            assert sc.as_of < sc.failure_datetime, (
                f"{sc.scenario_id}: as_of {sc.as_of} >= failure {sc.failure_datetime}"
            )


# ---------------------------------------------------------------------------
# Scenario type coverage
# ---------------------------------------------------------------------------


def test_all_scenario_types_present(generated):
    types = {sc.scenario_type for sc in generated}
    assert "normal" in types
    assert "pre_failure_24_48h" in types
    assert "pre_failure_0_24h" in types


def test_scenario_count_by_type(generated):
    from collections import Counter
    counts = Counter(sc.scenario_type for sc in generated)
    print("\nScenario counts by type:")
    for t, n in sorted(counts.items()):
        print(f"  {t}: {n}")
    assert counts["normal"] >= 1
    assert counts["pre_failure_24_48h"] >= 1
    assert counts["pre_failure_0_24h"] >= 1


# ---------------------------------------------------------------------------
# Criterion (4): F2 pair present and one causal diff
# ---------------------------------------------------------------------------


def test_f2_pair_present(generated):
    f2_abstain = [sc for sc in generated
                  if sc.perturbation is not None and sc.perturbation.family == "F2"]
    assert len(f2_abstain) >= 1, "Expected at least one F2 abstain scenario"
    # Each abstain must have a corresponding act with the same pair_id
    pair_ids = {sc.pair_id for sc in f2_abstain}
    act_ids = {sc.pair_id for sc in generated if sc.pair_role == "act" and sc.pair_id in pair_ids}
    assert pair_ids == act_ids, f"F2 abstain pair_ids without matching act: {pair_ids - act_ids}"


def test_f2_pair_one_causal_diff(generated):
    from itertools import groupby

    pairs = {}
    for sc in generated:
        if sc.pair_id:
            pairs.setdefault(sc.pair_id, []).append(sc)

    f2_pairs = {pid: scs for pid, scs in pairs.items() if any(
        sc.perturbation is not None and sc.perturbation.family == "F2" for sc in scs
    )}
    assert f2_pairs, "No F2 pairs found"

    for pair_id, scs in f2_pairs.items():
        assert len(scs) == 2, f"pair {pair_id} has {len(scs)} scenarios, expected 2"
        act = next(s for s in scs if s.pair_role == "act")
        abstain = next(s for s in scs if s.pair_role == "abstain")
        diffs = [f for f in _CAUSAL_FIELDS if getattr(act, f) != getattr(abstain, f)]
        assert diffs == ["perturbation"], (
            f"F2 pair {pair_id}: expected only 'perturbation' diff, got {diffs}"
        )


# ---------------------------------------------------------------------------
# Criterion (5): all generated scenarios pass loader
# ---------------------------------------------------------------------------


def test_all_generated_scenarios_have_valid_ids(generated):
    ids = [sc.scenario_id for sc in generated]
    assert len(ids) == len(set(ids)), "Duplicate scenario IDs"


def test_generated_scenarios_timestamps_ordered(generated):
    for sc in generated:
        if sc.timestamps is not None:
            ts = sc.timestamps
            assert ts.anomaly_onset <= ts.first_observable <= ts.point_of_no_return, (
                f"{sc.scenario_id}: timestamps out of order"
            )


def test_generated_scenarios_written_and_loadable(tmp_path):
    from mautonomy.scenarios.generator import generate_easy_scenarios
    from mautonomy.scenarios.loader import load_scenario, write_scenario

    scenarios = generate_easy_scenarios(seed=42)
    for sc in scenarios:
        p = tmp_path / f"{sc.scenario_id}.yaml"
        write_scenario(sc, p)
        loaded = load_scenario(p)
        assert loaded.scenario_id == sc.scenario_id
        assert loaded.as_of == sc.as_of
        assert loaded.ground_truth.action == sc.ground_truth.action
