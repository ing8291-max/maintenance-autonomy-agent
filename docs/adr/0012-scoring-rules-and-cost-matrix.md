# 채점은 두 레이어로 — 비용·적시는 '실제로 일어난 일'(A2), 쌍 정확도·Ask-F1은 '제안한 행동'(A1)

ADR 0005 가 네 축(비용 총합·쌍 정확도·Ask-F1·적시 비율)과 가중치 비(100:10:1)만 선언했고,
"실행 효과"·"비용 바구니"·"적시 조치의 정의"·"Ask-F1 의 질문/blocker 정의"는 미정이었다.
이 ADR 이 그 네 축을 코드로 돌릴 수 있게 못 박는다.

## 원칙: 어느 축이 어느 레이어를 보는가

- **쌍 정확도, Ask-F1**: 에이전트가 **제안한 행동 (A1)** 으로 채점한다. 게이트가 바꾼 결과를 보면
  에이전트의 능력이 게이트에 가려진다. (A1 = A2 로 간주.)
- **비용, 적시 비율**: **실제로 일어난 일 (A2)** 로 채점한다. 게이트가 사람에게 넘긴 결정과
  에이전트가 혼자 한 결정은 비용이 다르다.

## A2 실행 효과

| A2 판정 | 실제로 일어난 일 |
| --- | --- |
| `ALLOW` | A1 그대로 실행 |
| `NEEDS_HUMAN` | **사람에게 물음.** 사람이 정답 행동을 선택한다고 가정. |
| `BLOCKED` | **아무것도 안 함.** |
| `N/A` (A1=INVALID) | **아무것도 안 함.** |

이 매핑으로 집계 가능한 **실행 범주 (executed category)** 는 5개:
`NOTHING` / `CHECK_SENSOR` / `SCHEDULE_PM` / `REQUEST_STOP` / `ASK_HUMAN`.

변환 규칙:

| A1 | A2 | executed |
| --- | --- | --- |
| `NO_ACTION` | `ALLOW` | `NOTHING` |
| `CHECK_SENSOR` | `ALLOW` | `CHECK_SENSOR` |
| `SCHEDULE_PM` | `ALLOW` | `SCHEDULE_PM` |
| `REQUEST_STOP` | `ALLOW` | `REQUEST_STOP` |
| `ESCALATE` | `ALLOW` | `ASK_HUMAN` |
| 어느 것이든 | `NEEDS_HUMAN` | `ASK_HUMAN` |
| 어느 것이든 | `BLOCKED` 또는 `N/A` | `NOTHING` |

**한계로 명시**: `NEEDS_HUMAN` 뒤의 사람이 항상 정답을 고른다는 가정은 과한 쪽이다.
현실에서는 사람도 틀린다. "결재자 정확도 = 1" 은 분석의 상한선. 민감도 분석을 돌릴 때
결재자 정확도를 0.9 / 0.8 로 떨어뜨린 변형도 돌릴 수 있도록 코드에서 상수로 뽑아 둔다.

## 비용 바구니 (가정)

가중치 비 **100 : 10 : 1** 은 ADR 0005 의 가정값. `config/cost.yaml` 에 세 민감도 세트를 둔다:

- `default`: `missed_failure=100 / overstop=10 / overescalation=1 / correct=0`
- `low_miss`: `missed_failure=50 / overstop=10 / overescalation=1 / correct=0`
- `high_miss`: `missed_failure=200 / overstop=10 / overescalation=1 / correct=0`

### 25칸 전체 매트릭스 (정답 × 실행 범주 → 바구니)

| 정답 \ 실행 | NOTHING | CHECK_SENSOR | SCHEDULE_PM | REQUEST_STOP | ASK_HUMAN |
| --- | --- | --- | --- | --- | --- |
| **NO_ACTION** | correct ✓ | overescalation † | overstop ✓ | overstop ✓ | overescalation ✓ |
| **CHECK_SENSOR** | overstop ✓ | correct ✓ | overstop ✓ | overstop ✓ | overescalation ✓ |
| **SCHEDULE_PM** | missed ✓ | missed ✓ | correct ✓ | overstop † | overescalation ✓ |
| **ESCALATE** | missed ✓ | missed ✓ | overstop ✓ | overstop † | overescalation ✓ |
| **REQUEST_STOP** | missed ✓ | missed ✓ | overstop † | correct ✓ | overescalation ✓ |

- ✓ = 사용자가 명시한 규칙에서 그대로 도출.
- † = 사용자가 명시하지 않은 칸. 아래 추론 근거를 적어 둔다. 바꾸고 싶으면 `cost.yaml` 만 고치면 된다.

**추론 (†) 근거**

- `(정답=NO_ACTION, 실행=CHECK_SENSOR) = overescalation(1)`: 불필요한 센서 점검은
  운영 중단 없이 사람의 시간을 조금 쓰는 수준. "사람에게 물음(1)" 과 같은 급으로 둔다.
- `(정답=SCHEDULE_PM, 실행=REQUEST_STOP) = overstop(10)`: 예약 정비면 되는데 즉시 정지한 과잉 반응.
- `(정답=ESCALATE, 실행=REQUEST_STOP) = overstop(10)`: 사람 결정이 필요한 상황에서 혼자 멈춰 세움.
- `(정답=REQUEST_STOP, 실행=SCHEDULE_PM) = overstop(10)`: 사용자가 명시한 "정답 ESCALATE 인데
  SCHEDULE_PM = 10" 과 같은 논리 ("하루 안 예약 정비는 고장 전에 들어감"). REQUEST_STOP 정답에서도
  같은 24h 윈도 안이면 PM 이 들어가 고장을 막는다고 가정.

## 적시 비율

ADR 0006 이 `적시` 구간을 `[이상 시작, 되돌릴 수 없는 시점]` 으로 정의. 이 ADR 은 그 위에서
**조치의 정의**만 좁힌다:

- **조치로 인정**: 실행 범주가 `SCHEDULE_PM` / `REQUEST_STOP` / `ASK_HUMAN` 중 하나.
- **조치 제외**: `CHECK_SENSOR`, `NOTHING`.

적시 비율 = `(act 짝 중 조치 ∧ as_of ∈ [이상 시작, 되돌릴 수 없는 시점])` / `(act 짝 전체)`.

`act 짝` 은 `pair_role == "act"` 로 식별. 분모가 0 이면 적시 비율은 정의되지 않음 (None).

## Ask-F1

- **물어봐야 하는 상황** = `ground_truth.action == "ESCALATE"`.
- **물었다** = `A1 == "ESCALATE"` (게이트 결재는 세지 않는다 — 원칙 1).
- **정밀도** = `(물음 ∩ 물어봐야 함) / 물음`.
- **재현율** = `(물음 ∩ 물어봐야 함) / 물어봐야 함`.
- **F1** = 조화 평균. 분모 0 이면 해당 값은 0.

## 쌍 정확도

- `pair_id` 로 묶인 두 시나리오를 쌍으로 본다.
- 쌍 둘 다에서 `A1 == ground_truth.action` 이면 1점. 하나라도 틀리면 0.
- 쌍 정확도 = 맞춘 쌍 / 전체 쌍.

## B1 ~ B4 에도 같은 채점을 쓴다

베이스라인은 게이트가 없으므로 `A2 = A1` 로 간주해 같은 함수에 흘려 보낸다 (ADR 0005).
scoring 함수의 입력 계약은 `(scenario, a1_action, a2_verdict)` — 베이스라인은
`a2_verdict="ALLOW"` 로 고정해 넣는다. 그러면 비용·적시·쌍 정확도·Ask-F1 네 축이 다 공정하게 비교된다.

## Considered Options

- **단일 "맞음/틀림" 라벨**: 자율성 축을 못 잰다. 기각.
- **A1 하나로만 네 축 다 채점**: 게이트가 바꾼 결과를 전혀 못 본다. 비용 민감도 분석의 핵심이 사라진다. 기각.
- **A2 하나로만 네 축 다 채점**: 게이트가 다 바꿔 주면 에이전트의 Ask 품질과 쌍 정확도가 과장된다. 기각.
- **결재자 정확도 < 1**: 가정으로 깔끔하지 않다. 지금은 1 로 놓고 민감도 변형에서 흔든다.

## Consequences

- `config/cost.yaml` 이 생기고 세 민감도 세트가 들어간다.
- `src/mautonomy/scoring.py` 가 25 칸 매트릭스 + 네 축을 구현한다.
- 다음에 트레이스 집계기 (step 4) 가 이 모듈을 호출해 리포트 표의 각 행을 만든다.
- 추론 셀 (†) 네 개는 사용자가 바꾸고 싶으면 `cost.yaml` 매트릭스만 수정하면 되고, 코드 변경은 필요 없다.
