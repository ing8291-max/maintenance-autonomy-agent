# Interface change log

Per ADR 0011 §1.3: every change to the read-tool payload shape, the
agent prompt body, or the model-call parameters is recorded here
before the eval-split is first touched. Each entry carries a pre/post
example and the count of affected scenarios so the eval report can
cite the exact interface state the numbers were obtained under.

---

## 2026-11-03 — v2 interface bundle

Five linked changes, applied together. Scope: all dev-split synth
scenarios (20 of 20). Eval split: not yet touched.

### What changed

1. **`max_output_tokens`: 1024 → 4096** (`src/mautonomy/agent/config.py`).
   Diagnosed cause of 6 format errors in the previous dev run — all
   six were truncated JSON (the model's reply hit the 1024-token cap
   mid-emission; the closing brace never arrived). See cache files
   `bdfc5d10…` and `b1d2d2c8…` for the raw truncated bodies.
2. **Structured output enforced on the final-answer turn**
   (`src/mautonomy/agent/loop.py`, `DECISION_RESPONSE_FORMAT`). The
   adapter now sends an OpenAI-compatible `response_format` with the
   strict decision schema whenever the loop is in forced-final mode
   (step 8, or after a format error has locked in retries). Tools
   are suppressed on the same turn, so the only legal response is a
   valid decision JSON.
3. **Agent prompt v2** (`prompts/agent_v2.md`). Three edits versus v1:
   - Removed the line *"If a sensor stream is missing or stuck, prefer
     `CHECK_SENSOR`."* (over-indexed the agent on CHECK_SENSOR; the
     F2-abstain descriptor already carries that signal).
   - Rewrote `CHECK_SENSOR` as *"the sensor itself may be faulty rather
     than the machine"* so the action names the hypothesis, not the
     symptom.
   - Fixed the inaccurate *"strictly older than now"* claim; rows are
     filtered on `datetime <= as_of` (`src/mautonomy/data/read_tools.py:62`),
     so v2 says *"at or before now (never after)"*.
4. **Per-tool look-back cap** (`src/mautonomy/agent/tools.py:MAX_HOURS_BACK`).
   `read_telemetry` and `read_errors` keep the 72-hour cap. `read_maintenance_history`
   and `read_failure_history` extend to 8760 hours (365 days). The
   `as_of` filter upstream is unchanged, so rows after now are still
   never returned.
5. **`read_telemetry` returns a per-sensor summary block** alongside
   the raw rows. Fields per sensor: `baseline_mean_30d`,
   `baseline_std_30d`, `recent_mean_24h`, `zscore_24h`,
   `slope_per_hour_24h`, and the row counts the stats were built on.
   Baseline window is `[as_of - 30d, as_of - 24h)`, so the recent 24h
   window used for z-score / slope does not overlap with the baseline.
   No judgment rule or action hint is attached — descriptive statistics
   only.

### Before / after example (read_telemetry payload)

Before (v1):
```json
{"tool": "read_telemetry", "hours_back": 24, "row_count": 24,
 "rows": [{"datetime": "...", "volt": 170.3, "rotate": 440.1,
           "pressure": 100.0, "vibration": 40.2}, ...]}
```

After (v2) — `rows` identical; a new `summary` sibling is appended:
```json
{"tool": "read_telemetry", "hours_back": 24, "row_count": 24,
 "rows": [...same raw rows as before...],
 "summary": {
   "volt":      {"baseline_mean_30d": 170.6, "baseline_std_30d": 15.1,
                 "recent_mean_24h": 170.3, "zscore_24h": -0.02,
                 "slope_per_hour_24h": -0.011,
                 "baseline_row_count": 696, "recent_row_count": 24},
   "vibration": {"baseline_mean_30d": 40.1,  "baseline_std_30d": 1.9,
                 "recent_mean_24h": 48.0,   "zscore_24h": 4.16,
                 "slope_per_hour_24h": 0.21,
                 "baseline_row_count": 696, "recent_row_count": 24}
 }}
```

### Dev-run comparison (rep=1, 20 scenarios)

See `scripts/compare_dev_runs.py` for the raw table.

| Axis                | Before (v1) | After (v2 bundle) |
| ------------------- | ----------- | ----------------- |
| Format errors       | **6**       | **0**             |
| A1 accuracy         | 12 / 20     | 11 / 20           |
| F2 pair hits (both members correct) | 2 / 4 | 1 / 4 |
| Total cost (API)    | $0.3732     | $0.3212           |

**F2 pair detail**

| pair_id             | act truth    | act A1 (before → after) | abstain A1 before/after | pair hit b/a |
| ------------------- | ------------ | ----------------------- | ----------------------- | ------------ |
| f2-pair-0042-0020   | SCHEDULE_PM  | SCHEDULE_PM → CHECK_SENSOR | CHECK_SENSOR / CHECK_SENSOR | ✓ / ✗ |
| f2-pair-0043-0083   | SCHEDULE_PM  | SCHEDULE_PM → SCHEDULE_PM | CHECK_SENSOR / CHECK_SENSOR | ✓ / ✓ |
| f2-pair-0044-0003   | SCHEDULE_PM  | NO_ACTION → NO_ACTION   | CHECK_SENSOR / CHECK_SENSOR | ✗ / ✗ |
| f2-pair-0045-0095   | SCHEDULE_PM  | NO_ACTION → NO_ACTION   | CHECK_SENSOR / CHECK_SENSOR | ✗ / ✗ |

### Reading of the numbers

- **The format-error knob worked.** Six truncations → zero. Dropping
  the retries also pulled total cost down ~14% despite the token
  cap being bumped 4×.
- **The accuracy signal is mixed and noisy at n=20.** v2 lost one F2
  pair (42/0020 flipped from SCHEDULE_PM to CHECK_SENSOR on the act
  side) and lost one pre-24h case (44/0003 slid from SCHEDULE_PM to
  NO_ACTION). The three pre-24h and two F2-act losses that were
  already present in v1 did not recover.
- **Interpretation to carry into the next change**: the v2 prompt
  removed the explicit CHECK_SENSOR nudge, but on 42/0020 the agent
  reached for CHECK_SENSOR anyway — not from the deleted rule, since
  it was deleted. The telemetry summary now shows z-score / slope
  prominently, and this scenario's z-score may be high enough on a
  non-vibration sensor that the agent reads sensor fault instead of
  rising failure precursor. Worth a trace inspection before the next
  interface edit.

### Changes NOT made

- No change to how `machine_id` / `as_of` / `allow_eval` are
  threaded. They stay bound in `ScenarioEnvironment` per ADR 0011
  §1.4.
- No change to the policy gate (ADR 0004). A2 wiring is unchanged.
- No change to scoring (ADR 0012). The comparison table above is
  raw A1 accuracy and F2 pair hits, not the full four-axis
  cost / 적시 / Ask-F1 / 쌍 정확도 report.
