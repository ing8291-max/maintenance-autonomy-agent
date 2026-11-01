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
| act | `precursor_rise` | vibration (+ rotate 동반) | `as_of − 24h` | `SCHEDULE_PM` |
| abstain | `sensor_stuck` | vibration | `as_of − 18h` | `CHECK_SENSOR` |

두 멤버의 `scenario_type`은 `hardcase`, `failure_datetime=null`, `timestamps=null`이다. 주입 외의 모든 인과 필드(머신, 시드, as_of 등)는 동일하다.

### precursor_rise (실제 전조)
- vibration이 `start_at`부터 `rise_duration_hours`(기본 24h) 동안 선형으로 상승. 기울기 `rise_rate_per_hour`는 베이스라인 `2·std / 24h`.
- 동반 신호: `correlated_sensor=rotate`가 같은 창에서 `−1.5·σ_rotate`만큼 선형으로 떠내려감. "진동만 혼자 뛰는지 아니면 다른 신호도 같이 변하는지"가 센서 고장과 구분되는 핵심 단서다.
- `start_at`은 반드시 `(as_of − 48h, as_of)` 안에 있어야 한다(테스트 `test_f2_pair_start_at_close_to_as_of`로 강제).

### sensor_stuck (센서 고장)
- vibration이 `start_at`(기본 `as_of − 18h`)부터 `stuck_at`(베이스라인 평균) 값에 **그대로 굳는다**. 분산이 사라지는 모양.
- 다른 신호(rotate, pressure, volt)는 그대로 둔다. "한 신호만 조용히 얼어붙었는지"가 핵심 단서.
- (대안형: `sensor_spike` — 단일 틱이 베이스라인 평균에서 `spike_delta`만큼 혼자 튐. 지금 기본 쌍에는 쓰지 않지만 스키마는 지원.)

### 베이스라인은 어디서 재나
`as_of` 바로 앞 30일 창(`[as_of − 30d, as_of)`)에서 각 센서의 평균·표준편차를 계산한다. 이 값이 `stuck_at`과 `rise_rate_per_hour`, `correlated_delta`의 근거다. 생성기에선 `_sensor_stats`가 담당한다.

### 개발용 설비에서 측정한 값 (머신 20, seed=42)

seed=42로 돌리면 F2 쌍이 머신 20의 `as_of = 2015-04-27 14:00:00`에 놓인다. 그 시점 바로 앞 30일(=720시간)의 베이스라인은:

| 신호 | 평균 | 표준편차 |
|---|---|---|
| vibration | 40.68 | 5.39 |
| rotate | 450.85 | 50.56 |
| pressure | 102.82 | 12.33 |
| volt | 172.00 | 15.44 |

생성기는 이 값으로 다음을 뽑는다:
- `rise_rate_per_hour = round(2·5.39/24, 3) = 0.449` (vibration)
- `correlated_delta = round(−1.5·50.56, 2) = −75.84` (rotate)
- `stuck_at = round(40.68, 2) = 40.68`

### 이상 시작 시점의 참고값 (dev 머신 실제 고장)

같은 창 규칙(베이스라인 평균 + 2·std)으로 dev 머신 몇 대의 실제 고장을 재면, 센서가 처음 분명히 뜨는 시각이 고장보다 **35~47시간 앞**이다. 참고치:

| 머신 | 고장 시각 | 베이스라인 (vib 평균/sd) | 처음 vib>μ+2σ | 고장과의 간격 |
|---|---|---|---|---|
| 20 | 2015-01-19 06:00 | 41.54 / 6.46 | 2015-01-17 07:00 | 47h |
| 2  | 2015-03-19 06:00 | 40.23 / 4.87 | 2015-03-17 19:00 | 35h |
| 4  | 2015-01-17 06:00 | 40.38 / 4.57 | 2015-01-15 15:00 | 39h |

이 분포가 F2 `rise_duration_hours = 24`의 근거다. 실제 전조가 ‘뜨는 데서 고장까지’ 보통 하루~이틀이니, 합성 전조도 그 범위 안에 머문다.
