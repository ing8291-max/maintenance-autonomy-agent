# Eval v1 — 채점 보고서

생성: 2026-11-03 14:37 UTC  
평가 실행 커밋: `b006f6d` (eval-freeze-v1)  
채점 커밋: `d78002b`  

## 0. 사전 점검

**git diff eval-freeze-v1 -- src prompts config**: 비어 있음 ✓

**시나리오 유형별 개수**

- `normal`: 14 ✓
- `pre48h`: 13 ✓
- `pre24h`: 13 ✓
- `f2-act`: 20 ✓
- `f2-abstain`: 20 ✓

**A1/A2 실행 수**: 400 (80 × 5 = 400 ✓)
**베이스라인 실행 수**: 320 (80 × 4 = 320 ✓)

## 1. 6줄 성적표

비용은 default 가중치(100:10:1). A1·A2는 5회 평균, 95% CI (부트스트랩 2000회, 쌍 단위 샘플링).
쌍 정확도·Ask-F1은 A1 기준(ADR 0012). 정답률 = executed_category 일치율.

시스템        | 비용/문제 | 95% CI         | 쌍 정확도 | Ask-F1 | 적시 비율  | 정답률  
---------- | ----- | -------------- | ----- | ------ | ------ | -----
B1         | 1.00  | —              | 0.0%  | 0.280  | 100.0% | 16.2%
B2         | 5.88  | —              | 0.0%  | 0.000  | 100.0% | 41.2%
B3         | 22.88 | —              | 0.0%  | 0.000  | 35.0%  | 40.0%
B4         | 30.38 | —              | 0.0%  | 0.000  | 20.0%  | 32.5%
A1 (raw)   | 38.73 | [32.80, 44.90] | 15.0% | 0.029  | 15.0%  | 52.5%
A2 (gated) | 37.95 | [31.99, 44.23] | 15.0% | 0.029  | 15.0%  | 52.5%

**Ask-F1 상세 (A1 raw)** precision=0.200 recall=0.015 F1=0.029

## 2. A2 − A1 비용 차이

A2 게이트 적용 전(A1 raw)과 적용 후(A2 gated)의 문제당 평균 비용 차이.  
음수 = 게이트가 비용을 낮춤.

가중치 세트    | A1 비용  | A2 비용  | 차이 (A2−A1) | 95% CI          
--------- | ------ | ------ | ---------- | ----------------
low_miss  | 19.852 | 19.072 | -0.780     | [-1.238, -0.347]
default   | 38.727 | 37.948 | -0.780     | [-1.239, -0.347]
high_miss | 76.477 | 75.698 | -0.780     | [-1.238, -0.347]

## 3. 비용 민감도 — 3개 가중치 세트

miss:overstop:overescalation (correct=0 공통).

시스템 | low_miss (50:10:1) | default (100:10:1) | high_miss (200:10:1) | 순위 변동
--- | ------------------ | ------------------ | -------------------- | -----
B1  | 1.00               | 1.00               | 1.00                 | 없음   
B2  | 5.88               | 5.88               | 5.88                 | 없음   
B3  | 13.50              | 22.88              | 41.62                | 없음   
B4  | 17.25              | 30.38              | 56.62                | 없음   
A1  | 19.85              | 38.73              | 76.48                | 없음   
A2  | 19.07              | 37.95              | 75.70                | 없음   

## 4. 유형별 A1 정답률 및 놓친 고장 비율

정답률 = modal(5회) == ground_truth. 놓친 고장 = gt∈{SCHEDULE_PM,ESCALATE}, executed=NOTHING or CHECK_SENSOR.

유형         | n  | 정답률    | 놓친 고장 비율    | CHECK_SENSOR | NO_ACTION | SCHEDULE_PM
---------- | -- | ------ | ----------- | ------------ | --------- | -----------
normal     | 14 | 100.0% | —           | 0            | 14        | 0          
pre48h     | 13 | 38.5%  | 8/13 (62%)  | 2            | 6         | 5          
pre24h     | 13 | 0.0%   | 5/13 (38%)  | 1            | 4         | 8          
f2-act     | 20 | 15.0%  | 17/20 (85%) | 0            | 17        | 3          
f2-abstain | 20 | 100.0% | —           | 20           | 0         | 0          

*유형별 A1 예측 분포 (modal 기준): 열 = 예측 action.*

## 5. 사전 등록 분석

### 5a. 실패 원인 분류 (ADR 0011 §2.1)

자동 분류 기준:
- **harness**: INVALID 또는 format_errors>0 또는 reached_step_cap=True
- **tool**: trace에 도구 오류 필드 없음 → 자동 감지 불가
- **model**: 그 외 모든 오답
- **시험 탓 후보**: 5회 모두 같은 오답 → 아래 목록

| 분류 | 건수 | 비율 |
|------|------|------|
| correct | 210 | 52.5% |
| harness | 0 | 0.0% |
| tool | 0 | 0.0% |
| model | 190 | 47.5% |
| **총 실패** | **190** | **47.5%** |

**시험 탓 후보** (5회 모두 같은 오답): 34건

| scenario_id | 유형 | 정답 | 예측 |
|-------------|------|------|------|
| `eval-f2-act-0042-0006` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0025` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0028` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0031` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0035` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0038` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0040` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0041` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0043` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0046` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0055` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0064` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0069` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0071` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0086` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0093` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-f2-act-0042-0099` | f2-act | SCHEDULE_PM | NO_ACTION |
| `eval-easy-pre24h-0042-0025` | pre24h | ESCALATE | NO_ACTION |
| `eval-easy-pre24h-0042-0028` | pre24h | ESCALATE | CHECK_SENSOR |
| `eval-easy-pre24h-0042-0031` | pre24h | ESCALATE | NO_ACTION |
| `eval-easy-pre24h-0042-0035` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre24h-0042-0038` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre24h-0042-0041` | pre24h | ESCALATE | NO_ACTION |
| `eval-easy-pre24h-0042-0043` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre24h-0042-0064` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre24h-0042-0069` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre24h-0042-0071` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre24h-0042-0099` | pre24h | ESCALATE | SCHEDULE_PM |
| `eval-easy-pre48h-0042-0025` | pre48h | SCHEDULE_PM | NO_ACTION |
| `eval-easy-pre48h-0042-0041` | pre48h | SCHEDULE_PM | CHECK_SENSOR |
| `eval-easy-pre48h-0042-0064` | pre48h | SCHEDULE_PM | NO_ACTION |
| `eval-easy-pre48h-0042-0069` | pre48h | SCHEDULE_PM | NO_ACTION |
| `eval-easy-pre48h-0042-0071` | pre48h | SCHEDULE_PM | NO_ACTION |
| `eval-easy-pre48h-0042-0086` | pre48h | SCHEDULE_PM | NO_ACTION |

### 5b. B3 dev-τ (τ=0.186) 적용 결과

b3_lr.json threshold=0.186이 eval 실행에 이미 적용됨.

| 지표 | 값 |
|------|----|
| 비용/문제 (default) | 22.88 |
| Ask-F1 | 0.000 |
| Ask-precision | 0.000 |
| Ask-recall | 0.000 |

### 5c. AUROC / ECE / Brier

**AUROC 정의와 기준**

| 항목 | 내용 |
|------|------|
| AUROC | P(score_pos > score_neg): 양성·음성 쌍 전체에서 양성의 score가 높은 비율 (0.5=무작위, 1.0=완벽) |
| **기준 A — failure** | 양성 = gt ∈ {SCHEDULE_PM, ESCALATE} — 고장 경고가 필요한 문제 |
| **기준 B — action_needed** | 양성 = gt ≠ NO_ACTION — 어떤 행동이든 필요한 문제 |

**점수 변환**

| 시스템 | score | 비고 |
|--------|-------|------|
| B3 | 로지스틱 회귀 raw 확률 P(failure within 48h) | 기준 A에 자연스럽게 정렬. 기준 B에서는 CHECK_SENSOR 양성을 낮게 점수화할 수 있어 AUROC 하락 가능 |
| A1 | modal_action≠NO_ACTION이면 avg_conf, NO_ACTION이면 1−avg_conf | '어떤 행동이 필요하다'는 신호로 해석. B3와 다른 도메인(확률 vs 확신도) |

B3와 A1은 점수 도메인이 달라 직접 수치 비교는 주의 필요. 동일 기준 A/B 하에 같은 AUROC 정의로 산출.

양성 수: 기준 A = 46/80 (57.5%), 기준 B = 66/80 (82.5%)

시스템        | AUROC (기준 A: failure) | AUROC (기준 B: action_needed) | ECE   | Brier
---------- | --------------------- | --------------------------- | ----- | -----
B3         | 0.895                 | 0.823                       | 0.305 | 0.327
A1 (modal) | 0.352                 | 0.899                       | 0.339 | 0.343

### 5d. A1 확신도 임계값 τ 분석

**정책**: a1_confidence < τ → ESCALATE로 교체 (낮은 확신 = 에스컬레이션).
**τ 결정 근거**: dev v2 기록 20건에서 비용(default) 최소화.

dev v2 기록 τ 탐색:

| τ | dev 비용 | dev Ask-F1 |
|---|---------|------------|
| 0.00 | 40.50 | 0.000 |
| 0.60 | 40.50 | 0.000 |
| 0.65 | 40.50 | 0.000 |
| 0.68 | 40.50 | 0.000 |
| 0.70 | 35.15 | 0.286 |
| 0.72 | 35.15 | 0.286 |
| 0.74 | 30.30 | 0.400 |
| 0.75 | 30.35 | 0.364 |
| 0.80 | 30.35 | 0.364 |
| 0.82 | 30.35 | 0.364 |
| 0.85 | 25.40 | 0.500 |
| 0.88 | 25.40 | 0.500 |
| 0.90 | 20.45 | 0.615 |
| 0.92 | 20.45 | 0.615 |
| 0.93 | 10.70 | 0.444 |
| 0.95 | 0.85 | 0.381 | ← 선택
| 1.00 | 1.00 | 0.333 |

**선택된 τ = 0.95** (dev 비용 최솟값 = 0.85)

τ 적용 전후 eval 비교 (A1 raw vs τ 정책, default 가중치):

| | 비용/문제 | Ask-F1 |
|---|---------|--------|
| A1 raw | 38.73 | 0.029 |
| A1 (τ=0.95) | 1.61 | 0.318 |

*주의: dev v2 데이터 20건으로 τ를 정했으므로 신뢰 구간이 넓다.*

## 6. 5회 반복 답 일치율

| 지표 | 값 |
|------|----|
| 전체 시나리오 수 | 80 |
| 5회 모두 같은 답 (완전 일치) | 74 (92.5%) |
| 평균 최빈값 일치율 | 97.8% |

유형         | n  | 완전 일치     | 평균 최빈값 일치율
---------- | -- | --------- | ----------
normal     | 14 | 14 (100%) | 100.0%    
pre48h     | 13 | 9 (69%)   | 89.2%     
pre24h     | 13 | 11 (85%)  | 96.9%     
f2-act     | 20 | 20 (100%) | 100.0%    
f2-abstain | 20 | 20 (100%) | 100.0%    

---

*eval-freeze-v1 코드 동결 준수. src/prompts/config 변경 없음.*
*B1~B4 단일 결정론적 실행. A1·A2 5회 반복, 부트스트랩 2000회.*