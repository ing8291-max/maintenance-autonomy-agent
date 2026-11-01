# Synthesis Protocol

Rules for generating scenario YAMLs that apply to both dev and eval hard-case scenarios.

## as_of placement

**시나리오의 `as_of`는 항상 고장 시각보다 앞선다.**

`as_of`가 고장 시각과 같거나 이후이면, 그 시각의 교체 기록(`PdM_maint`)이 `read_maintenance_history`에서 보인다. 이는 읽기 도구의 버그가 아니라 `as_of` 선택의 문제다. 합성기는 반드시 `as_of < failure_datetime`을 보장해야 한다.

실용 규칙: 시나리오의 관측 시각은 `되돌릴 수 없는 시점` 이전 어느 시점이고, 고장 자체(`PdM_failures` 기록)와 같은 tick이어서는 안 된다.

## 세 시점 산정 규칙 (easy 시나리오)

`src/mautonomy/scenarios/generator.py`의 `_infer_timestamps`가 구현한다.

| 시점 | 규칙 | 기준 |
|---|---|---|
| 이상 시작 | 4h 롤링 평균 진동이 `baseline_mean + 1·std`를 처음 초과하는 시각 | 진동 신호 |
| 처음 드러남 | 단일 진동 측정값이 `baseline_mean + 2·std`를 처음 초과하는 시각 | 진동 신호 |
| 되돌릴 수 없는 시점 | `failure_datetime − 12h` | 고장 시각 기준 고정 |

**기준값**: 베이스라인은 `failure_datetime − 72h` 이전 30일 진동 데이터의 평균·표준편차.  
데이터 시작 직후라 30일 기록이 없으면 폴백(mean=40.0, std=5.0)을 사용한다.

**검증 사례 — 머신 20 고장 2015-01-04 06:00 (comp2)**

베이스라인 구간이 데이터 시작 이전이라 폴백 적용:
- baseline_mean=40.0, std=5.0 → 임계값: +1sd=45.0, +2sd=50.0
- 이상 시작: `2015-01-01 16:00` (4h 롤링 평균 > 45.0)
- 처음 드러남: `2015-01-02 15:00` (진동 > 50.0)
- 되돌릴 수 없는 시점: `2015-01-03 18:00` (failure − 12h)

이 값들이 seed=42로 생성된 `dev-easy-pre48h-0042-0020.yaml`의 `timestamps` 필드에 그대로 기재된다.

## 개발용 vs 평가용 머신

합성기는 `data/splits.json`의 `dev` 집합에서만 머신 프로파일을 참조한다. 평가용 하드케이스 합성도 `eval` 집합에서만 참조한다. 읽기 도구에 `allow_eval=True`를 넘기는 쪽은 평가 파이프라인뿐이다.

## F2 쌍 — 주입 패턴 (normal window)

F2 쌍은 **고장이 없는 창**(as_of 앞뒤 7일 안에 고장 기록이 전혀 없음) 위에서 두 시나리오를 짝지어 돌린다. 베이스는 완전히 같고, 바뀌는 것은 '어떤 패턴을 주입했는가' 하나뿐이다.

| 역할 | `kind` | 센서 | 시작 시각 | 정답 행동 |
|---|---|---|---|---|
| act | `precursor_rise` | vibration (+ rotate 동반) | `as_of − 6h ~ as_of − 18h` (seed 기반) | `SCHEDULE_PM` |
| abstain | `sensor_stuck` | vibration | `as_of − 18h` | `CHECK_SENSOR` |

두 멤버의 `scenario_type`은 `hardcase`, `failure_datetime=null`, `timestamps=null`이다. 주입 외의 모든 인과 필드(머신, 시드, as_of 등)는 동일하다.

### precursor_rise (실제 전조)

**시간 구조** — 세 시각이 서로 다른 것을 지키는 게 핵심.

```
       start_at          as_of                       implied_failure
          |                |                                |
          |---- 6~18h -----|-------- lead − offset ---------|
          |---------------- lead (35~47h) -----------------|
                                            |------ 12h ----|
                                            PONR
```

- **선행시간(lead)**: `start_at → implied_failure`. dev 머신 실측(35~47h 분포, 아래 표)에서 뽑는다. 이 숫자는 "전조 시작부터 실제 고장까지"를 의미하지, "전조 시작부터 관찰 시각까지"가 아니다.
- **start_at 오프셋**: `as_of − 6h ~ as_of − 18h`. 에이전트가 보는 창에서 전조는 아직 **일부만 자란 상태**다.
- **implied_failure = start_at + lead**. SCHEDULE_PM 정답이 성립하려면 **implied_failure ≥ as_of + 24h**(테스트 `test_f2_act_implied_failure_at_least_24h_after_as_of`로 강제). 생성기는 `lead ≥ offset + 24` 를 보장해 뽑는다.
- **rise_rate_per_hour = 2·σ_vib / lead** (즉 implied_failure 에서 총 +2σ 상승). as_of 시점의 노출량은 `(2·offset / lead) σ` — offset=6, lead=47 이면 0.26σ, offset=18, lead=42 이면 0.86σ. 즉 **에이전트는 '서서히 뜨고 있는 중'인 신호를 본다**.
- **동반 신호**: `correlated_sensor=rotate` 가 **같은 `[start_at, implied_failure]` 창에서** `−1.5·σ_rotate` 만큼 선형으로 떠내려감. 30일 베이스라인 전체에 걸치는 변화가 아니다 — 베이스라인은 σ를 재는 데만 쓰고, 드리프트는 주입 창 안에서만 산다.
- **ADR 0006 세 시점**: `anomaly_onset=start_at`, `first_observable=start_at`(주입 즉시 보임), `point_of_no_return = implied_failure − 12h`. PONR 은 반드시 `as_of` 보다 뒤에 있어야 한다(테스트 `test_f2_act_has_timestamps_abstain_does_not`).

### sensor_stuck (센서 고장)
- vibration이 `start_at`(기본 `as_of − 18h`)부터 `stuck_at`(베이스라인 평균) 값에 **그대로 굳는다**. 분산이 사라지는 모양.
- 다른 신호(rotate, pressure, volt)는 그대로 둔다. "한 신호만 조용히 얼어붙었는지"가 핵심 단서.
- 행동할 실제 고장이 없으므로 **ADR 0006 세 시점은 저장하지 않는다**(`timestamps: null`). CHECK_SENSOR는 적시/너무 늦음 판정 대상이 아니다.
- (대안형: `sensor_spike` — 단일 틱이 베이스라인 평균에서 `spike_delta`만큼 혼자 튐. 지금 기본 쌍에는 쓰지 않지만 스키마는 지원.)

### 베이스라인은 어디서 재나
`as_of` 바로 앞 30일 창(`[as_of − 30d, as_of)`)에서 **쌍을 만드는 그 설비 자체**의 각 센서 평균·표준편차를 계산한다. μ·σ는 전역 상수가 아니라 매번 머신 ID 기준으로 다시 계산된다(생성기 `_sensor_stats`가 담당, 테스트 `test_f2_act_total_rise_is_two_sigma`가 쌍 머신 기준 2σ 상승을 확인). 이 값이 `stuck_at`·`rise_rate_per_hour`·`correlated_delta`의 근거다.

### 개발용 설비에서 측정한 값 (예시: 머신 20, seed=42)

아래 숫자는 **예시**다. 다른 머신·다른 seed 조합에서는 as_of 가 달라지고 μ·σ 도 달라진다. 중요한 것은 두 가지뿐: (a) 각 쌍은 짝지은 설비의 자기 μ·σ 를 기준으로 생성된다, (b) 상승량은 언제나 2σ 다.

seed=42로 돌리면 F2 쌍이 머신 20의 `as_of = 2015-04-27 14:00:00`에 놓인다. 그 시점 바로 앞 30일(=720시간)의 베이스라인은:

| 신호 | 평균 | 표준편차 |
|---|---|---|
| vibration | 40.68 | 5.39 |
| rotate | 450.85 | 50.56 |
| pressure | 102.82 | 12.33 |
| volt | 172.00 | 15.44 |

rng 가 뽑은 값: `start_at_offset = 12h`, `lead = 40h`. 결과:
- `start_at = 2015-04-27 02:00:00` (as_of − 12h)
- `implied_failure = 2015-04-28 18:00:00` (as_of + 28h, 24h 가드 통과)
- `rise_duration_hours = 40`, `rise_rate_per_hour = round(2·5.39/40, 4) = 0.2693`
- implied_failure 에서 총 상승 = 40 × 0.2693 ≈ **+10.77 (≈ 2σ)**
- **as_of 에서 지금까지 쌓인 상승 ≈ 0.60σ** (12/40 × 2σ = 3.23 ≈ 0.60σ)
- `correlated_delta = round(−1.5·50.56, 2) = −75.84` (rotate, `[start_at, implied_failure]` 창 내부에서만)
- `PONR = 2015-04-28 06:00:00` (implied − 12h, as_of 보다 16h 뒤)
- abstain: `stuck_at = round(40.68, 2) = 40.68`, `start_at = as_of − 18h = 2015-04-26 20:00:00`

### 이상 시작 시점의 참고값 (dev 머신 실제 고장)

같은 창 규칙(베이스라인 평균 + 2·std)으로 dev 머신 몇 대의 실제 고장을 재면, 센서가 처음 분명히 뜨는 시각이 고장보다 **35~47시간 앞**이다. 참고치:

| 머신 | 고장 시각 | 베이스라인 (vib 평균/sd) | 처음 vib>μ+2σ | 고장과의 간격 |
|---|---|---|---|---|
| 20 | 2015-01-19 06:00 | 41.54 / 6.46 | 2015-01-17 07:00 | 47h |
| 2  | 2015-03-19 06:00 | 40.23 / 4.87 | 2015-03-17 19:00 | 35h |
| 4  | 2015-01-17 06:00 | 40.38 / 4.57 | 2015-01-15 15:00 | 39h |

이 분포가 **F2 `lead ∈ [35, 47]` (= `start_at → implied_failure`) 의 근거**다. `start_at` 자체는 as_of 로부터 6~18h 뒤로 당겨 잡고, 남은 선행시간을 implied_failure 에 꽂는다. 그래서 as_of 시점의 에이전트는 "뜨고 있는 중"인 전조만 본다.

## 에이전트 단계 주의 — 보여선 안 되는 것

시나리오 YAML 은 **생성기와 평가기가 쓰는 라벨 파일**이지 에이전트의 입력이 아니다. 다음 필드는 평가가 끝날 때까지 에이전트에게 노출되면 안 된다:

| 필드 | 왜 숨기나 |
|---|---|
| `perturbation` 블록 전체 | 어떤 패턴을 주입했는지 알려 주면 F2 짝의 의미가 사라진다. "precursor_rise" 라고 쓰여 있으면 CHECK_SENSOR 를 고를 이유가 없다. |
| `ground_truth` (`action`, `rationale`) | 정답을 그대로 베끼게 된다. |
| `timestamps.anomaly_onset`, `timestamps.first_observable` | 전조가 언제 시작됐는지는 에이전트가 **읽기 도구 출력에서 스스로 추론해야 하는 것**이다. 라벨로 넘기면 평가 자체가 무의미하다. |
| `failure_datetime`, `failure_component`, `perturbation.implied_failure` | 모두 미래 라벨. 숨긴다. |
| `scenario_type`, `pair_id`, `pair_role` | F2 짝 중 어느 쪽인지 알면 "이건 함정이다"가 바로 보인다. |

에이전트가 받아도 되는 것은 `machine_id`, `as_of`, 그리고 **읽기 도구가 돌려주는 센서 값뿐**이다.

### 주입은 어디서 적용하나

주입 패턴(`precursor_rise`, `sensor_stuck`, `sensor_spike`)은 **읽기 도구 출력 단계에서만 반영한다**:

1. `read_telemetry(machine_id, as_of)` 가 원본 CSV 를 `as_of` 로 잘라 가져온다 (ADR 0009).
2. 호출된 시나리오가 F2 짝이면, 리턴 직전에 **그 쌍의 `perturbation` 을 센서 값 리스트에 덧씌운다**. `precursor_rise` 는 `start_at` 이후 시각의 vibration 에 `(dt − start_at) · rise_rate_per_hour` 를 더하고, `sensor_stuck` 은 `start_at` 이후 vibration 을 `stuck_at` 으로 덮어쓴다.
3. 다른 읽기 도구(`read_errors`, `read_maintenance_history`, `read_failure_history`)는 영향을 받지 않는다. 그 로그에는 합성 사건을 넣지 않는다.
4. 에이전트의 추론 로그·프롬프트에는 "perturbation" 이라는 단어 자체가 들어가지 않도록 한다.

이 규칙은 아직 코드로 강제되지 않았다(현재 `src/mautonomy/data/read_tools.py` 는 원본만 돌려준다). 하네스를 붙일 때 **읽기 도구 래퍼** 로 넣고, 그 래퍼가 `scenario.perturbation` 을 받아 센서 리스트를 변형한 뒤 돌려주도록 구현한다. 하네스 테스트에서 "YAML 전체"가 에이전트에게 흘러들지 않는지 보장하는 선택 테스트(allow-list)를 함께 둔다.
