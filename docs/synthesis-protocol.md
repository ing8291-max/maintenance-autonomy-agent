# Synthesis Protocol

Rules for generating scenario YAMLs that apply to both dev and eval hard-case scenarios.

## as_of placement

**시나리오의 `as_of`는 항상 고장 시각보다 앞선다.**

`as_of`가 고장 시각과 같거나 이후이면, 그 시각의 교체 기록(`PdM_maint`)이 `read_maintenance_history`에서 보인다. 이는 읽기 도구의 버그가 아니라 `as_of` 선택의 문제다. 합성기는 반드시 `as_of < failure_datetime`을 보장해야 한다.

실용 규칙: 시나리오의 관측 시각은 `되돌릴 수 없는 시점` 이전 어느 시점이고, 고장 자체(`PdM_failures` 기록)와 같은 tick이어서는 안 된다.

## 개발용 vs 평가용 머신

합성기는 `data/splits.json`의 `dev` 집합에서만 머신 프로파일을 참조한다. 평가용 하드케이스 합성도 `eval` 집합에서만 참조한다. 읽기 도구에 `allow_eval=True`를 넘기는 쪽은 평가 파이프라인뿐이다.
