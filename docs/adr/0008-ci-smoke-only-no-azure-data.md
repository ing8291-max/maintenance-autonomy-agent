# CI 는 Azure 데이터 없는 완전 합성 스모크만 돌리고, VCR 캐시와 로그는 커밋하지 않는다

ADR 0001 에 따라 `data/` 전체가 gitignore 다. VCR 캐시(`cache/vcr/`)와 실행 로그(`runs/`)는 Azure PdM 데이터 조각을 포함하므로 **역시 커밋하지 않는다**. 그래서 GitHub Actions CI 는 전체 평가를 돌릴 수 없다. 전체 평가 재현은 **사용자가 Kaggle 에서 데이터를 받아 `make eval` 로 로컬에서** 한다.

CI(`.github/workflows/ci.yml`)가 돌리는 것:
1. `pytest tests/` (단위 테스트 + Hypothesis 퍼즈)
2. `tests/fixtures/scenarios/` 에 커밋된 **완전 합성 예시 시나리오** 몇 개로 end-to-end 스모크. 이 시나리오들은 Azure 데이터를 참조하지 않는 자립형이다.
3. 커밋된 `reports/fuzz-coverage.json` 과 재계산 결과가 일치하는지 비교.

API 키·시크릿은 CI 에 안 넣는다. GitHub Actions 는 Linux 러너이므로 **MLX(맥 전용)로 Llama 를 돌릴 수 없다**. 그래서 CI 스모크는 **정해진 답을 내는 가짜(stub) 모델** 어댑터로 돌린다 (`src/mautonomy/models/stub.py`). 스텁은 시나리오 ID 에 매핑된 고정 응답을 리턴해 하네스의 로지스틱만 검증한다. 실제 모델 비교는 로컬(`make eval`)에서만.

## Considered Options

- **CI 에서 전체 평가**: 데이터 재배포 금지라서 못 함. 기각.
- **VCR 캐시 커밋**: 캐시 안에 Azure CSV 조각(에러 코드, 머신 ID 등)이 들어가면 ADR 0001 위반. 기각.
- **데이터 접근 토큰을 CI 시크릿에 둠**: Kaggle 라이선스가 미표기라 2차 공유 위험. 기각.

## Consequences

- `tests/fixtures/scenarios/` 디렉토리에 완전 합성 YAML 3-5개 를 커밋해야 함. 합성기가 뽑는 평가용과 포맷은 같지만 Azure 참조 없음.
- `make eval` 로컬 재현 절차를 README 에 상세히. "데이터를 어디서 받아 어디에 두고, 어떤 명령을 쳐야 숫자가 리포트와 일치하는가".
- GitHub Actions 의 전체 평가 워크플로우는 제출 후, 데이터 라이선스가 정리되거나 완전 합성으로 전환했을 때 추가.

## Consequences for VCR 캐시 키

VCR 캐시 키는 `sha256(model_id + system_prompt + tools_schema + sampling_params + json(messages))`. 도구 정의와 샘플링 파라미터(온도 등)도 포함해, 그 중 하나라도 바뀌면 캐시 미스가 되게 한다.
