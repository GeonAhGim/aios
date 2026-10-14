# 파일 범위 분할 제안 — task-12051 (작성: task-12056)

## 배경
- task-12051 (`QA: FND-09 performance L45~L49`)은 `files: ["src/foundation/performance/*"]`로
  배정됐고, `hold=true` / `hold_reason=local_file_scope` / `needs_split=true` 상태로 멈춰 있다.
  (task-12051 자체는 이미 status=done, verdict=pass — QA 수행은 끝났고, 범위 표현만 glob이라
  local-pm 분할 규칙에 걸린 것.)
- `src/foundation/performance/*`는 디렉터리 전체를 가리키는 glob이라 "한 리프 = 한 범위" 원칙에
  맞지 않는다. 실제 저장소에는 아래 22개 `.py` 파일(`__init__.py` 6개 제외, 총 1,932 LOC)이
  존재한다.

## 실제 파일 목록 (src/foundation/performance/)
```
api.py                                   21
domain/rules.py                          65
domain/twr.py                            66
domain/risk_metrics.py                   72
domain/mwr.py                            80
domain/identity.py                       83
domain/methodology.py                    91
ports/repository.py                      94
application/correct_statement.py         99
application/get_statement.py            103
application/statement_projection.py     108
adapters/postgres_statement_repository.py 123
contracts/v1.py                         128
domain/models.py                        149
adapters/paper_input_adapter.py         192
adapters/postgres_repository.py         207
application/compute_statement.py       251
```
(+ `__init__.py` × 6, 0 LOC)

## 분할안 — 스펙 리프(L45~L49) 경계를 그대로 따른다
`docs/specs/L4_strategy_portfolio_backtest_v1.0.md` §9(줄 660~664, 735~739)가 이 디렉터리를
이미 L45~L49 5개 리프로 나눠 정의하고 전부 `done` 처리했다. glob 범위를 이 경계와 동일하게
쪼개는 것이 "실제 파일별 분할"의 최소 변경안이다:

| 분할 조각 | files | 근거 |
|---|---|---|
| perf-contracts (L45) | `contracts/v1.py`, `domain/models.py`, `domain/methodology.py` | 스펙 L45, 커밋 0d31e4d |
| perf-formulas (L46) | `domain/twr.py`, `domain/mwr.py`, `domain/identity.py`, `domain/risk_metrics.py`, `domain/rules.py`, `api.py` | 스펙 L46, 커밋 c65f3971. `api.py`는 `domain/risk_metrics.calmar`만 재노출하는 교차-애그리게잇 경계 파사드(파일 docstring 참조)라 L46 도메인 묶음에 귀속 |
| perf-storage (L47) | `ports/repository.py`, `adapters/postgres_repository.py` | 스펙 L47, 마이그레이션 M5 포함 |
| perf-paper-input (L48) | `adapters/paper_input_adapter.py` | 스펙 L48 |
| perf-application (L49) | `application/compute_statement.py`, `application/correct_statement.py`, `application/get_statement.py`, `application/statement_projection.py`, `adapters/postgres_statement_repository.py` | 스펙 L49(라우터 포함 범위이나 라우터는 `src/api/`에 별도 위치 — 여기서는 statement 저장소 포함) |

## 제안 요약
- `src/foundation/performance/*` 1개 glob → 위 5개 조각으로 분할. 각 조각은 이미 완료된 스펙
  리프(L45~L49) 경계와 1:1로 대응하므로 추가 설계 없이 기존 완료 커밋을 근거로 쓸 수 있다.
- 조각 경계는 기존 애그리게잇 하위 디렉터리(`domain/`, `application/`, `adapters/`, `ports/`,
  `contracts/`) 구조를 넘어서지 않는다.

## 범위 밖 (이 task가 하지 않은 것)
- task-12051의 hold 해제, 재큐, status 변경 — 수행하지 않음.
- 소스 코드 수정 — 수행하지 않음(읽기/라인수 확인만).
