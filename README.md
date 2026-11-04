# Maintenance Autonomy Agent

**A test harness — not just an agent — that measures how safely an LLM can decide when to act, when to ask, and when to do nothing on predictive maintenance signals.**

The bottleneck for AI in industry is not capability but verification. Accuracy alone would have said deploy: the agent had the highest accuracy (52.5%). Verification showed the highest expected cost (A2 37.95 per problem vs 1.00 for always-escalate), because its errors are missed failures. The gate cut cost by only 0.78 (95% CI [−1.24, −0.35]): guardrails stop wrong actions, not wrong inaction.

---

## The question

> **How far can we trust an AI agent to act alone on industrial equipment signals?**

The bottleneck to deploying AI in industrial settings is not model quality — it is the absence of a credible method for measuring *where the autonomy boundary should sit*.  This project builds that measurement: a harness that adversarially tests whether an agent knows when to act, when to escalate, and when to stay quiet.  The primary result is not accuracy; it is **whether the policy gate changes the cost and safety picture in a meaningful and reproducible way**.

---

## Architecture

```
                        ┌─────────────────────────────────┐
   Scenario (as_of) ──► │            Agent (A1)            │
                        │  up to 8 turns, temperature=0    │
                        │                                  │
                        │   ┌────────────────────────────┐ │
                        │   │  read_telemetry             │ │
                        │   │  read_errors               │ │
                        │   │  read_maintenance_history  │ │
                        │   │  read_failure_history      │ │
                        │   └──── all sliced at as_of ───┘ │
                        └──────────────┬──────────────────┘
                                       │ JSON
                              {action, confidence, reason}
                                       │
                        ┌──────────────▼──────────────────┐
                        │   Policy Gate (A2)               │
                        │   pure-Python predicate registry │
                        │   ALLOW / NEEDS_HUMAN / BLOCKED  │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │   Scoring                        │
                        │   cost · pair-acc · Ask-F1       │
                        │   on-time ratio                  │
                        └─────────────────────────────────┘

A1 and A2 are produced in a single run.
Pair accuracy and Ask-F1 are scored on A1 (the agent's proposal).
Cost and on-time ratio are scored on A2 (what actually executes).
```

The four read tools are the agent's only tools.  No shell, no file access, no network calls (ADR 0011 §1.4).  Every tool silently truncates records after `as_of` in code — not in the prompt — so future information cannot leak regardless of what the agent requests (ADR 0009).

---

## Evaluation design

### Data

The underlying data is the **Microsoft Azure PdM sample dataset** (simulated, Microsoft blog 2016-04-21).  Its Kaggle distribution carries an unknown license.  We do not redistribute it.  Users must download it directly (see [Reproduction](#reproduction)).

### Dev / eval split

100 machines are stratified by model type and split 70/30 using a fixed seed (`data/splits.json`, ADR 0007).  The 70 development machines are used for all tuning — logistic regression training, prompt iteration, interface debugging, hard-case synthesis.  The 30 evaluation machines are never examined until the harness runs.

### Freeze (eval-freeze-v1)

Before the evaluation machines were first exposed, the following were committed and tagged `eval-freeze-v1` (commit `b006f6d`, ADR 0013):

| Category | Path |
|----------|------|
| Scenario generator | `src/mautonomy/scenarios/` |
| Prompt v2 | `prompts/agent_v2.md` |
| Read tools + summary formatter | `src/mautonomy/harness/` |
| Model config | `src/mautonomy/agent/config.py` (`muse-spark-1.1`, temp=0, reasoning_effort="medium") |
| Gate rules | `src/mautonomy/policy/rules.py` |
| Cost weights + matrix | `config/cost.yaml` |
| Scoring code | `src/mautonomy/scoring.py`, `src/mautonomy/metrics/` |
| Baseline checkpoints | `data/models/b3_lr.json`, `data/models/b4_thresholds.json` |

Nothing in this list was modified after the tag.  Any change requires a new tag and a separate report (ADR 0013 §2).

### 80 problems

Pre-registered counts (ADR 0013 §3.2):

| Type | n | Ground truth |
|------|---|-------------|
| Normal (no fault ±30 d) | 14 | NO_ACTION |
| Pre-failure 24–48 h | 13 | SCHEDULE_PM |
| Pre-failure 0–24 h | 13 | ESCALATE |
| F2-act (precursor-rise, act) | 20 | SCHEDULE_PM |
| F2-abstain (sensor-stuck, abstain) | 20 | CHECK_SENSOR |
| **Total** | **80** | — |

### F2 hard-case family

F2 scenarios come in matched pairs synthesised from the same machine and the same normal window.  In the act scenario a precursor is injected: vibration rises slowly and rotate falls together, calibrated to the median of 485 dev-set failures; the correct response is SCHEDULE_PM.  In the abstain scenario the vibration sensor is frozen at its baseline mean from 18 h before as_of; the correct response is CHECK_SENSOR.  The harness synthesises both by injecting controlled perturbations into real Azure telemetry intervals, keeping the base signal authentic (ADR 0003).  Bootstrap CI samples the act/abstain pair as a single unit to preserve within-pair correlation.

### Baselines

| System | Description |
|--------|-------------|
| B1 | Always ESCALATE |
| B2 | Always SCHEDULE_PM |
| B3 | Logistic regression on sensor features, threshold τ=0.186 (Youden's J on dev set) |
| B4 | Rule-based: sensor threshold lookup table |

Baselines are deterministic and run once.  A1/A2 run 5 times per problem (5 repeats at temperature 0; 92.5% fully consistent) to quantify sampling variance.

### Cost weights

The weights miss:overstop:overescalation = **100:10:1** are an **assumption**, not a field-validated number (ADR 0005, ADR 0012).  Three sensitivity sets are pre-registered:

| Set | missed_failure | overstop | overescalation |
|-----|---------------|---------|---------------|
| low_miss | 50 | 10 | 1 |
| **default** | **100** | **10** | **1** |
| high_miss | 200 | 10 | 1 |

The 25-cell cost matrix (5 ground truths × 5 executed categories) is specified in `config/cost.yaml`.

---

## Results

### Main scorecard

Costs are default weights (100:10:1).  A1/A2 are 5-rep averages with 95% bootstrap CI (2 000 iterations, pair-level sampling).  Pair accuracy and Ask-F1 are on A1; cost and on-time ratio are on A2.  Accuracy = modal executed category matches ground truth.

| System | Cost/problem | 95% CI | Pair acc | Ask-F1 | On-time | Accuracy |
|--------|-------------|--------|----------|--------|---------|----------|
| B1 | 1.00 | — | 0.0% | 0.280 | 100.0% | 16.2% |
| B2 | 5.88 | — | 0.0% | 0.000 | 100.0% | 41.2% |
| B3 | 22.88 | — | 0.0% | 0.000 | 35.0% | 40.0% |
| B4 | 30.38 | — | 0.0% | 0.000 | 20.0% | 32.5% |
| A1 (raw) | 38.73 | [32.80, 44.90] | 15.0% | 0.029 | 15.0% | 52.5% |
| A2 (gated) | 37.95 | [31.99, 44.23] | 15.0% | 0.029 | 15.0% | 52.5% |

Ask-F1 detail (A1): precision=0.200, recall=0.015, F1=0.029.

### Type-wise accuracy

| Type | n | Accuracy | Missed-failure rate | Predicted NO_ACTION | Predicted CHECK_SENSOR | Predicted SCHEDULE_PM |
|------|---|----------|--------------------|--------------------|----------------------|-----------------------|
| normal | 14 | 100.0% | — | 14 | 0 | 0 |
| pre48h | 13 | 38.5% | 8/13 (62%) | 6 | 2 | 5 |
| pre24h | 13 | 0.0% | 5/13 (38%) | 4 | 1 | 8 |
| f2-act | 20 | 15.0% | 17/20 (85%) | 17 | 0 | 3 |
| f2-abstain | 20 | 100.0% | — | 0 | 20 | 0 |

The agent handles normal and abstain cases perfectly.  Its failure mode on failure-warning and hard cases is predominantly **NO_ACTION** — it reads weak injected degradation (about 0.75σ at observation time) as normal.

### Gate effect

| Weight set | A1 cost | A2 cost | Δ (A2−A1) | 95% CI |
|-----------|---------|---------|-----------|--------|
| low_miss | 19.852 | 19.072 | −0.780 | [−1.238, −0.347] |
| default | 38.727 | 37.948 | −0.780 | [−1.239, −0.347] |
| high_miss | 76.477 | 75.698 | −0.780 | [−1.238, −0.347] |

The gate saves a consistent 0.78 cost units regardless of the miss/overstop ratio.  Rank order across all six systems is unchanged across all three weight sets.

### AUROC

AUROC measures how well a system's score separates positives from negatives; 0.5 = random, 1.0 = perfect.  Two criteria are reported because B3's score (P(failure)) and A1's score (confidence) address different questions.

| Score | Interpretation |
|-------|---------------|
| B3 | Logistic regression P(failure within 48 h) |
| A1 | avg_conf if modal action ≠ NO_ACTION, else 1 − avg_conf |

| System | AUROC — criterion A (failure: gt ∈ {SCHEDULE_PM, ESCALATE}) | AUROC — criterion B (action needed: gt ≠ NO_ACTION) | ECE | Brier |
|--------|------|------|------|------|
| B3 | 0.895 | 0.823 | 0.305 | 0.327 |
| A1 | 0.352 | 0.899 | 0.339 | 0.343 |

A1's confidence score is a poor discriminator for the specific failure criterion (0.352 ≈ anti-correlated) but a strong discriminator for whether *any* action is needed (0.899).  The two systems are not directly comparable because their scores are in different domains.

### Confidence threshold τ

A threshold policy was analysed: if `a1_confidence < τ`, replace the action with ESCALATE.  τ was swept on 20 dev-v2 runs; τ=0.95 minimises cost on that set (dev cost = 0.85 at τ=0.95 vs 1.00 at τ=1.00).

| | Cost/problem | Ask-F1 |
|---|-------------|--------|
| A1 raw | 38.73 | 0.029 |
| A1 (τ=0.95) | 1.61 | 0.318 |

The sharp improvement is explained by what τ=0.95 actually does: at that threshold the agent's confidence almost always falls below 0.95, so the policy escalates nearly every prediction to a human — it is functionally close to B1 (always-escalate, cost=1.00).  This is not a smart discriminator; it is a nearly unconditional fallback.  τ was fitted on only 20 dev runs, so its confidence interval is wide.

### Test-problem candidates

34 scenarios produced the same wrong prediction across all 5 repetitions — these are candidates for problem-level issues (ambiguous ground truth or underspecified scenarios) rather than model randomness, and should be manually reviewed before drawing conclusions from them (ADR 0011 §2.1):

| Type | Count | Ground truth | Predicted |
|------|-------|-------------|-----------|
| f2-act | 17 | SCHEDULE_PM | NO_ACTION (all 17) |
| pre24h | 11 | ESCALATE | mixed (NO_ACTION, SCHEDULE_PM, CHECK_SENSOR) |
| pre48h | 6 | SCHEDULE_PM | NO_ACTION or CHECK_SENSOR |

5-rep consistency overall: 74/80 (92.5%) full agreement; mean modal-match rate 97.8%.

---

## What this tells us — and what it does not

### Guardrails stop wrong actions, not wrong inaction

The policy gate catches cases where the agent proposes an *over-aggressive* action (e.g., scheduling a stop when none is needed) and routes them to a human.  What it cannot catch is the dominant failure mode in this evaluation: **the agent confidently does nothing when it should act**.  On f2-act scenarios, 85% of failures are missed faults predicted as NO_ACTION.  A gate that checks the action type cannot see the absence of an action.

### What you can rely on

- The gate consistently reduces cost (Δ=−0.78, CI excludes zero at 95%) regardless of how heavily you weight missed faults versus overstopping.
- The agent correctly handles fully normal machines (100%) and sensor-stuck abstain cases (100%), suggesting the basic telemetry-reading pipeline is sound.
- AUROC on the action-needed criterion (0.899) indicates the agent's confidence score is genuinely informative about whether *something* needs to happen.

### What you cannot rely on

- The agent misses 62% of pre-48h faults and 85% of F2-act faults.  Do not deploy it autonomously on failure-warning scenarios.
- Ask-F1 = 0.029 means the agent almost never escalates when it should.  The gate does not compensate for this; it only blocks wrong actions.
- The approver-accuracy assumption (`NEEDS_HUMAN` → human always picks the correct action) is an upper bound.  The true cost is higher whenever the human also errs.

---

## Deliberately out of scope — and why

| Item | Reason excluded |
|------|----------------|
| **F3 (post-replacement residual signal)** | A measurement across 2,296 replacement records (723 corrective + 1,573 preventive) on the 70 dev machines found no evidence of residual signal in the 0–48 h window after replacement (Δ|σ| median −0.03 to −0.09 for corrective; ≈0 for preventive; confirmed by a domain expert).  ADR 0003: hard cases must be rooted in the natural distribution.  An unobserved phenomenon cannot be synthesised honestly. |
| **F5 (prompt injection)** | Injection attacks require user-authored free text in the input stream.  The Azure PdM data contains no free-text fields — only numeric sensor values, categorical error codes, and maintenance type labels.  The injection surface does not exist in this input schema.  Reintroduce F5 if the scope expands to email or ticket ingestion (ADR 0003). |
| **F1 (signal overlap), F4 (spurious error codes)** | Out of scope for this evaluation due to hard-case design and validation cost; deferred to the next iteration (ADR 0013 §1). |
| **Multi-agent orchestration** | When all agents share the same machine, same `as_of`, and the same four read tools, splitting reasoning across agents does not improve decision quality — it only adds coordination overhead.  Multi-agent becomes relevant when inputs themselves are partitioned (e.g., separate inventory, production scheduling, and repair availability feeds) (ADR 0011 §3). |

---

## Limitations

1. **Simulated data.** The Azure PdM dataset is a simulation (Microsoft, 2016).  The fault seeding, pre-fault drift, and error–fault correlations are plausible but not derived from real plant telemetry.  Findings may not transfer to real sensor distributions.

2. **Approver accuracy = 1.** When the gate routes to a human (`NEEDS_HUMAN`), the scoring model assumes the human always selects the correct action.  This is an upper bound.  The sensitivity analysis with approver accuracy 0.9/0.8 has not been run on eval-v1 and is listed under future work.

3. **Cost weights are assumptions.** The 100:10:1 ratio (missed fault : unnecessary stop : unnecessary escalation) is not calibrated from field data.  Rank order is stable across the three pre-registered sensitivity sets, but a different domain may require different weights.

4. **80 problems.** The evaluation set covers 80 scenarios across five types.  Bootstrap CIs are correspondingly wide.  Results for sub-types (e.g., pre24h n=13) have limited statistical power.

5. **Single model.** All agent runs use `muse-spark-1.1` (temperature=0).  Whether these findings generalise to other model families or sizes is untested.

6. **τ selected from 20 dev runs.** The confidence threshold τ=0.95 was swept on only 20 dev-v2 runs (4 per type, 1 rep each).  The apparent improvement at τ=0.95 is largely explained by near-universal escalation, not selective escalation; the sample is too small to reliably separate the two effects.

---

## Reproduction

> CI runs unit tests and Hypothesis fuzz against a stub model only — no Azure data required (ADR 0008).  Full evaluation reproduction requires the data.

**1. Download the data**

```
https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance
```

Place the five CSV files (`PdM_telemetry.csv`, `PdM_errors.csv`, `PdM_maint.csv`, `PdM_failures.csv`, `PdM_machines.csv`) in `data/raw/`.  The directory is gitignored and will not be committed (ADR 0001).

**2. Configure credentials**

```bash
cp .env.example .env
# edit .env: set META_API_KEY to your LLM API key
```

**3. Install**

```bash
uv sync --extra dev
```

**4. Fit the baselines**

`data/models/` is not committed.  Run this once before `make eval`:

```bash
make baselines
```

This writes `data/models/b3_lr.json` and `data/models/b4_thresholds.json` from the dev-split machines.

**5. Run the full evaluation**

```bash
make eval
```

This calls `scripts/run_eval.py` and then `scripts/score_eval.py`, writing logs to `logs/` and the report to `reports/eval-v1.md`.  The evaluation runner resumes from cache on interruption.  To reproduce the exact eval-v1 numbers, check out the `eval-freeze-v1` tag first:

```bash
git checkout eval-freeze-v1
make baselines
make eval
```

Scenario YAMLs are not committed.  Running `generate_eval_scenarios(seed=42)` from the `eval-freeze-v1` codebase reproduces the same 80 scenarios deterministically (ADR 0013 §Consequences).

---

## Related work

All references below are arXiv preprints and have not been peer-reviewed.  They are cited as evidence pointing in the same direction as independent design decisions made in this project's ADRs, not as authoritative conclusions.

| Preprint | Relevance |
|----------|-----------|
| **PHMForge** (arXiv 2604.01532) | Benchmarks LLM agents on PHM algorithm use.  This project occupies the orthogonal axis: *when* to act and *when* to involve a human, not *how well* the agent solves the PHM task (ADR 0002). |
| **AgentAbstain** (arXiv 2607.10059) | Source of the paired-accuracy metric. |
| **HiL-Bench** (arXiv 2604.09408) | Source of the Ask-F1 metric. |
| **Failure-as-a-Process** (arXiv 2607.09510) | Inspiration for the three-timestamp ground truth (anomaly onset → first visible → point of no return) (ADR 0006). |
| **AutoHarness** (arXiv 2603.03329) | A code constraint layer that blocks illegal actions lets smaller models outperform larger ones; format quality should be reported separately from main results (ADR 0011 §1.1). |
| **On the Reliability Limits of LLM-Based Multi-Agent Planning** (arXiv 2603.26993) | Multi-agent with shared inputs does not improve decision quality (ADR 0011 §3). |
| **Adapting the Interface Not the Model** (arXiv 2605.22166) | A significant fraction of failures are model–environment interface mismatches that harness modifications fix; tuning the model/prompt distorts evaluation distributions (ADR 0011 §1.3). |
| **Metacognition** (arXiv 2607.11881) | Treats confidence calibration, self-verification, and knowing when to stop as a unified capability; survey of methods across all three (ADR 0011 §2.2). |
| **Harness Evolution Rethought** (arXiv 2607.12227) | Harness improvements should be compared against a simple search baseline at the same compute budget; step budget and repeat count are the main confounds (ADR 0011 §1.2). |
| **Model or Harness** (arXiv 2607.28802) | Proposes a taxonomy that attributes failures to model, harness, tools, or environment; pre-register the taxonomy before evaluation (ADR 0011 §2.1). |
| **Inside an Agent Team** (arXiv 2608.16801) | Designating a coordinator did not reliably improve success rate; agents proactively retrieved grading materials (ADR 0011 §1.4). |
| **JEV-as-a-Judge** (arXiv 2609.26550) | Errors cluster at low confidence, making confidence-based delegation effective; the delegation threshold should be calibrated on the agent's own data (ADR 0011 §2.2). |

---

## Future work

1. **Give B3 as a tool to the agent.** The agent currently cannot query the logistic regression model's probability estimate.  Providing `query_failure_probability()` as a fifth read tool and observing whether the agent uses it — and how — is the most direct way to test whether model uncertainty improves autonomy judgement.

2. **F1 (signal overlap) and F4 (spurious error codes).** These trap families were deferred from eval-v1 (ADR 0013 §1).  Both require new hard-case synthesis protocols and dev-set validation before they can be added to the evaluation without compromising ADR 0003's principle that hard cases must be rooted in natural data.

3. **Approver accuracy sensitivity.**  The eval-v1 cost figures assume the human reviewer always selects the correct action when the gate routes to `NEEDS_HUMAN`.  Running the full sensitivity sweep (approver accuracy 1.0 / 0.9 / 0.8) as pre-registered in ADR 0013 §4.2 would bound the range of cost estimates under realistic human error rates.
