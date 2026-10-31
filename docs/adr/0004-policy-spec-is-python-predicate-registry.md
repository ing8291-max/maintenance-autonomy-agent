# 정책 스펙은 OPA/Rego 나 상태기계가 아니라 Python predicate 레지스트리로 쓴다

정책 레이어("금지·승인·에스컬레이션 규칙")는 코드와 분리된 선언적 사양으로 써야 한다. 후보는 OPA/Rego, YAML 규칙 + 상태기계, Python predicate 모듈이었다. 선택은 **Python predicate 레지스트리 (`src/mautonomy/policy/rules.py`)**. 이유: (1) OPA/Rego 는 솔로가 1주일에 쓰기엔 러닝커브가 과하고 M3 8GB 환경 세팅 비용이 든다. (2) 상태기계는 우리 규칙이 전부 "액션 제안 1개에 대한 평가"라 상태가 없다. (3) Python 모듈은 Hypothesis 로 바로 퍼즈할 수 있고, 리뷰어도 읽을 수 있다.

## Considered Options

- **OPA/Rego**: 산업 표준이지만 의존성·학습 비용이 솔로 1주일에 안 맞고, Hypothesis 와 통합이 어렵다. 기각.
- **YAML + FSM**: 상태 없는 규칙에 FSM 은 과잉이다. 기각.

## Consequences

- 각 규칙은 `{id, severity, description, applies_to(action) -> bool, violates(context, action) -> bool}` 꼴. 게이트는 모든 규칙을 돌며 하나라도 violates → 차단.
- 코드와 사양이 "분리"됐다는 주장은 "다른 모듈에 있다 + Hypothesis 로 사양만 퍼즈한다"로 지지한다. OPA 수준의 외부 사양 언어는 아니다. 리포트에서 이 한계를 명시한다.
- 리뷰어가 Rego 를 기대할 수 있으므로, README 와 리포트에 이 선택 이유를 1문단 적는다.
