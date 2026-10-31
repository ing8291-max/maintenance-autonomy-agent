# 정책 스펙은 OPA/Rego 나 상태기계가 아니라 Python predicate 레지스트리로 쓴다

정책 레이어("금지·승인·에스컬레이션 규칙")는 코드와 분리된 선언적 사양으로 써야 한다. 후보는 OPA/Rego, YAML 규칙 + 상태기계, Python predicate 모듈이었다. 선택은 **Python predicate 레지스트리 (`src/mautonomy/policy/rules.py`)**. 이유: (1) OPA/Rego 는 솔로가 1주일에 쓰기엔 러닝커브가 과하고 M3 8GB 환경 세팅 비용이 든다. (2) 상태기계는 우리 규칙이 전부 "액션 제안 1개에 대한 평가"라 상태가 없다. (3) Python 모듈은 Hypothesis 로 바로 퍼즈할 수 있고, 리뷰어도 읽을 수 있다.

## Considered Options

- **OPA/Rego**: 산업 표준이지만 의존성·학습 비용이 솔로 1주일에 안 맞고, Hypothesis 와 통합이 어렵다. 기각.
- **YAML + FSM**: 상태 없는 규칙에 FSM 은 과잉이다. 기각.

## 액션 타입별 판정 등급표

| 액션 타입 | 판정 | 실행 주체 | 근거 |
|---|---|---|---|
| `NO_ACTION` | `ALLOW` | 에이전트 단독 | 아무것도 안 함 |
| `CHECK_SENSOR` | `ALLOW` | 에이전트 단독 | 점검 티켓 생성, 되돌릴 수 있음 |
| `ESCALATE` | `ALLOW` | 에이전트 단독 | 사람 호출 자체가 에스컬레이션 |
| `SCHEDULE_PM` | `NEEDS_HUMAN` | 사람 승인 필요 | 설비 일정 변경, 비가역 영향 |
| `REQUEST_STOP` | `NEEDS_HUMAN` | 항상 사람 결정 | ADR 0010 지키는 선 1 |
| 목록에 없는 값 | `BLOCKED` | 차단 | 모르는 행동은 막는다 |

## Consequences

- 각 규칙은 `{id, severity, description, applies_to(action_type) -> bool, violates(action) -> bool, verdict_on_violation}` 꼴. 게이트는 `action_type` 문자열을 먼저 `ActionType` 열거형으로 변환하고, 실패하면 즉시 `BLOCKED`를 반환한다. 그 뒤 모든 규칙을 순회해 처음으로 `violates` 가 참인 규칙의 `verdict_on_violation`을 돌려준다.
- 게이트는 `reason` 같은 자유 문장 필드를 읽지 않는다. `ProposedAction.reason`은 경계(boundary)에서 받되, 어떤 규칙도 참조하지 않는다.
- 코드와 사양이 "분리"됐다는 주장은 "다른 모듈에 있다 + Hypothesis 로 사양만 퍼즈한다"로 지지한다. OPA 수준의 외부 사양 언어는 아니다. 리포트에서 이 한계를 명시한다.
- 리뷰어가 Rego 를 기대할 수 있으므로, README 와 리포트에 이 선택 이유를 1문단 적는다.
