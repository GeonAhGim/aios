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

---

# 파일 범위 분할 제안: task-12055 (task-12057 triage_note)

## 조사 결과

- task-12055는 `pools.yaml`의 `local-review: repo: backend` 레인에서 실행됐다. `fleet.yaml`의
  `app_repo`에 따라 실제 대상 저장소는 `C:\aios\aios` (worktree가 아닌 메인 clone) — 이 작업
  디렉터리(`C:\aios\wt\backend-2`)가 아니다.
- task-12055.files = `["src/api/*"]` — glob이 `src/api/` 트리 전체(30+ 라우터, 15개 스키마,
  11개 contracts 모듈, middleware, foundation 서브패키지, mcp, ws 등)를 가리켜 `local_file_split.expand()`
  한도(매치 12개)를 초과하고 `needs_split=true`/`hold=true`로 멈춰 있다.
- task-12055.title/spec은 "PLT API 계약 리프: 응답 봉투·에러 taxonomy·페이지네이션 (auth/users/admin까지)"
  — 즉 실제 책임 범위는 (a) 계약 인프라(응답 봉투/에러 taxonomy/페이지네이션)와 (b) 이를 적용받는
  auth/users/admin 라우터 3개로 한정된다. `src/api/*` 전체가 아니다.
- `C:\aios\aios`에서 확인한 실제 파일/라인 수:
  - `src/api/contracts/envelope.py` 59
  - `src/api/contracts/error_codes.py` 112
  - `src/api/contracts/exception_mapping.py` 97
  - `src/api/contracts/handlers.py` 122
  - `src/api/contracts/pagination.py` 30
  - `src/api/contracts/idempotency.py` 131
  - `src/api/contracts/exception_registry.py` 156
  - `src/api/routers/auth.py` 153, `src/api/schemas/auth.py` 86
  - `src/api/routers/users.py` 145 (schemas/users.py 없음 — users는 schemas/auth.py 또는 공용 스키마 재사용 추정, 분할 2에서 확인 필요)
  - `src/api/routers/admin.py` 260, `src/api/routers/admin_break_glass.py` 71,
    `src/api/schemas/admin.py` 38, `src/api/schemas/admin_break_glass.py` 13
  - `src/api/router_registry.py` 117, `src/api/deps.py` 165, `src/api/admin_deps.py` 107
    (라우터 배선 확인용, 변경 대상 여부는 분할 4에서 diff로 재확인)

## 제안: 4개 리프로 분할

1. **계약 인프라(응답 봉투/에러 taxonomy/페이지네이션)**
   `src/api/contracts/envelope.py`, `src/api/contracts/error_codes.py`,
   `src/api/contracts/exception_mapping.py`, `src/api/contracts/handlers.py`,
   `src/api/contracts/pagination.py`, `src/api/contracts/exception_registry.py`
   (idempotency.py는 응답 봉투/에러/페이지네이션과 축이 달라 제외 — 별도 확인 필요시 후속 리프)

2. **auth 라우터 적용**
   `src/api/routers/auth.py`, `src/api/schemas/auth.py`

3. **users 라우터 적용**
   `src/api/routers/users.py` (+ users 전용 스키마 파일이 diff에 있으면 포함, 현재 저장소에는
   `schemas/users.py` 없음 — 리뷰 대상 diff로 재확인 필요)

4. **admin 라우터 적용**
   `src/api/routers/admin.py`, `src/api/routers/admin_break_glass.py`,
   `src/api/schemas/admin.py`, `src/api/schemas/admin_break_glass.py`

`router_registry.py`/`deps.py`/`admin_deps.py`는 위 4개 리프의 실제 diff에 배선 변경이 포함된
경우에만 해당 리프에 추가 — 현재는 라인 수 확인 목적으로만 조사했고 diff 내용은 보지 않았다.

## 범위 외 (이번 조사에서 수행하지 않음)

- task-12055의 hold 해제, 재큐, 코드 수정 — 금지 사항이므로 수행하지 않았다.
- `local_file_split.py`/`local_triage.py` 등 `C:\aios\pm` 코드 수정 — fleet 코드는 직접 수정 금지
  대상이라 건드리지 않았다. 위 분할안을 자동화에 반영하려면 ops task로 별도 요청 필요.
