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
