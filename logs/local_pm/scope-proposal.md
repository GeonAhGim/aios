# 파일 범위 분할 제안: task-16648 / task-18495

## 원본 작업 분석

- **task-16648**: QA(묶음 1건) — CI complexity 정정
- **task-18495**: ratchet 초과 파일 분할 — code_ratchets 게이트 `loc_over_500` 18개→37개 증가 대응
- **원본 files**: `["src/"]` — 전체 소스 디렉토리 (너무 광범위)
- **문제**: CI complexity 단계에서 `over_cap_count 9개 -> 10개 (증가, CAP=25)` 보고 + `loc_over_500` 18개→37개 증가
- **현재 상태**: 로컬 게이트 녹색 (complexity는 이전 커밋 `7740471bd`에서 이미 정정됨)

## 실제 게이트 대상 파일

### complexity 게이트 (task-16648)

`check_complexity.py`가 스캔하는 대상:
1. `src/` 내 모든 `.py` 파일 (1569개 파일, 10개 함수가 CAP=25 초과)
2. `scripts/` 내 모든 `.py` 파일

현재 CAP=25 초과 함수 9개:
| 파일 | 함수 | 복잡도 |
|------|------|--------|
| `src/core/script/import_/pine/lexer.py` | `tokenize()` | 74 |
| `src/core/script/grammar/lexer.py` | `tokenize()` | 64 |
| `src/foundation/backtest/application/quick_backtest.py` | `run_quick_backtest()` | 63 |
| `scripts/consistency/wiring.py` | `check_port_protocol_implementations()` | 46 |
| `scripts/check_zone_manifest.py` | `main()` | 30 |
| `src/core/indicators/engine/__init__.py` | `run_chain_incremental()` | 27 |
| `src/foundation/backtest/application/run_backtest.py` | `run_backtest()` | 27 |
| `src/foundation/mandates/application/activate_revision.py` | `activate_revision()` | 27 |
| `src/foundation/market_data/domain/instruments/roll.py` | `continuous_futures()` | 27 |

### loc_over_500 게이트 (task-18495) — 37개 파일

baseline: 18개 → 현재: 37개 (증가 19개)

#### tests/integration/foundation/ledger/ — 7개 파일 (가장 큰 군)

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_purchase_flow.py` | 757 | purchase/hold/capture/release (21개 테스트) |
| `test_refund.py` | 667 | refund case R1/R2/R3, duplicate (24개 테스트) |
| `test_chargeback.py` | 629 | chargeback full/shortfall/duplicate/digest/freeze (22개 테스트) |
| `test_backfill.py` | 591 | backfill operations |
| `test_post_entry.py` | 573 | post entry |
| `test_ibor_abor.py` | 519 | IBOR/ABOR |

**분할안**: `test_purchase_flow.py` → `test_purchase_hold.py`(purchase+hold), `test_capture_release.py`(capture+release), `test_purchase_budget.py`(budget). `test_refund.py` → `test_refund_cases.py`(R1-R3), `test_refund_dedup.py`(duplicate). `test_chargeback.py` → `test_chargeback_coverage.py`(full/shortfall), `test_chargeback_edge.py`(duplicate/digest/freeze/rollback).

#### tests/integration/foundation/market_data/ — 4개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_dc13_hot_postgres_deepen_2886.py` | 518 | DC13 hot postgres |
| `test_dc8_coverage_entitlements_deepen_2884.py` | 546 | DC8 coverage |
| `test_hot_postgres.py` | 529 | hot postgres |

#### tests/integration/foundation/entities/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_postgres_entity_repository.py` | 617 | entity repository CRUD |

#### tests/integration/oms/ — 3개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_bitget_private_ws_to_inbox.py` | 560 | Bitget WebSocket |
| `test_cancel_modify_deepen.py` | 589 | cancel/modify |
| `test_restart_recovery.py` | 516 | restart/recovery |

#### tests/integration/ — 3개 파일 (루트 테스트)

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_auth_router.py` | 594 | auth router |
| `test_execution_scheduler.py` | 522 | execution scheduler |
| `test_order_service.py` | 559 | order service |

#### tests/integration/risk/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_intraday_monitor.py` | 688 | intraday risk monitor (31개 테스트) |

#### tests/integration/exchange/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_kis_durability.py` | 552 | KIS durability |

#### tests/integration/db/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_migration_tenant_membership.py` | 561 | migration tenant |

#### tests/integration/db_schema/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_market_data.py` | 522 | market data schema |

#### tests/integration/platform/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_break_glass.py` | 538 | break glass |

#### tests/foundation/integration/risk_gate/ — 2개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_recovery_gate.py` | 678 | recovery gate (31개 테스트) |
| `test_risk_gate_lifecycle.py` | 638 | risk gate lifecycle |

#### tests/foundation/integration/market_data/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_instruments_schema_deepen_2879.py` | 595 | instruments schema |

#### tests/foundation/unit/ — 2개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_begin_connection_application.py` | 585 | connection application |
| `test_provider_spi_deepen_2885.py` | 556 | provider SPI |

#### tests/unit/core/portfolio/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_mandate_binding.py` | 511 | mandate binding |

#### tests/unit/foundation/ — 6개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_decomposition.py` | 502 | EMS decomposition |
| `test_market_abuse.py` | 522 | market abuse |
| `test_l2_ingest_session_deepen_2902.py` | 554 | L2 ingest session |
| `test_ingest_job.py` | 513 | research data ingest |
| `test_preview_order.py` | 544 | preview order |

#### tests/unit/oms/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_observability_deepen.py` | 591 | OMS observability |

#### tests/unit/scripts/ — 2개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_canary_bitget.py` | 555 | canary bitget |
| `test_check_audit_regressions.py` | 546 | audit regressions |

#### tests/adversarial/ — 1개 파일

| 파일 | LOC | 주요 테스트 |
|------|-----|------------|
| `test_rule_purity.py` | 580 | rule purity |

## 분할 우선순위

### 우선순위 1: ledger 테스트 (가장 큰 영향)
- `test_purchase_flow.py` (757줄) → 3개 파일로 분할
- `test_refund.py` (667줄) → 2개 파일로 분할
- `test_chargeback.py` (629줄) → 2개 파일로 분할

### 우선순위 2: risk_gate + risk 테스트
- `test_recovery_gate.py` (678줄) → gate lifecycle vs evidence 평가로 분할
- `test_risk_gate_lifecycle.py` (638줄) → gate lifecycle로 분할
- `test_intraday_monitor.py` (688줄) → monitor vs signal로 분할

### 우선순위 3: oms + auth 테스트
- `test_auth_router.py` (594줄) → auth lifecycle vs session으로 분할
- `test_cancel_modify_deepen.py` (589줄) → cancel vs modify로 분할
- `test_observability_deepen.py` (591줄) → observability metrics로 분할

### 우선순위 4: 나머지 파일 (각각 1개 리프)
- 나머지 20개 파일은 각각 1개 리프로 분할 제안

## 권장

**우선순위 1 → 2 → 3 → 4 순서**로 권장. ledger 테스트가 가장 많은 파일(7개)과 최대 LOC(757줄)를 차지하므로 먼저 처리해야 전체 37개 중 19개(51%)를 해소할 수 있음.

## 비고

- 현재 CI complexity는 녹색(9=9). 이전 회귀(`7740471bd`)가 이미 정정됨.
- task-16648의 실제 목적(회귀 정정)은 이미 완료되었으므로, 새로운 분할 작업은 **예방적 복잡도 개선**으로 재정의 필요.
- 원본 hold 해제·재큐·코드 수정은 금지 (task-16651 spec).
- `loc_over_500` 게이트는 관찰용 aids — baseline을 올리지 말고 파일 분할로 해소해야 함.

---

# 파일 범위 분할 제안: task-16649

## 원본 작업 분석

- **task-16649**: QA(묶음 1건) — CI guards 정정 (`P5.contract_field_removed`)
- **원본 files**: `["src/"]` — 전체 소스 디렉토리 (너무 광범위)
- **문제**: guards 단계가 `WhitelistEntryResponse.id/exchange/destination_address` 필드 삭제를 veto
- **원인 커밋**: `e4232f97f` (janitor가 부모와 중복되는 필드 재선언을 제거)
- **현재 상태**: `ff856c1cb`에서 세 필드 재선언 복구 완료 (`src/api/schemas/account.py` 44-55행)

## 실제 대상 파일

| 파일 | LOC | 설명 |
|------|-----|------|
| `src/api/schemas/account.py` | 73 | `WhitelistEntryResponse` 계약 필드 재선언 |
| `tests/unit/api/schemas/test_account.py` | - | DTO 필드 단위 테스트 (읽기 전용 확인) |
| `src/api/routers/users.py` | - | DTO 사용처 (읽기 전용 확인) |

## 분할안

단일 리프로 충분. `files`를 `["src/"]` → `["src/api/schemas/account.py", "tests/unit/api/schemas/test_account.py"]`로 좁힌다.

## 비고

- 코드 수정은 이미 완료되어 이 task에서 추가 변경 없음. 본 문서만 산출물.
- 기준선(baseline) 수치는 건드리지 않았다.
- guards 광범위 범위(`e4232f97~1..HEAD`) 재실행은 120s를 넘겨 이 task에서는 생략.

---

# task-18793: task-18421 파일 범위 triage_note

## 판정
원본은 assigned + hold=true(local_file_scope) 상태다. 이번 작업은 제안만 제출한다.
앱 저장소는 C:/aios/wt/codex-escalation-1, origin=https://github.com/GeonAhGim/aios.git 이다.
검사 HEAD: 331fa80f8ffa9cfd99da7ebb866a99ba6740548b. 원본의 tests/e2e/와 frontend/e2e/는 앱 저장소 경로이며 fleet 저장소 경로가 아니다.

부모 task-12313은 코드 변경 없이 fleet task-15102로 이관되어 done 처리됐다. 기록된 원인은 golden DB URL 이중 접미사다.
실제 C:/aios/pm/local_ci.py:run_golden_stage는 현재 DATABASE_URL/TEST_DATABASE_URL에 원본 url을 넣으며 PYTEST_XDIST_WORKER=golden을 유지한다.
C:/aios/pm/tests/test_ci_golden_stage.py에는 test_run_golden_stage_env_uses_unsuffixed_url_not_golden_url 회귀 검사가 있다.
task-15102 기록: commit c76a237e4862, 16 passed, 배포 완료. 이는 과거 증빙이며 현재 golden 성공으로 간주하지 않는다.

## 우선 권고: 기존 수정 QA를 먼저 분리
fleet 저장소 C:/aios/pm의 local_ci.py, tests/test_ci_golden_stage.py를 대상으로 기존 task-15102 증빙과 현재 golden 결과를 대조하는 QA를 제안한다.
라이브 fleet 편집은 금지하며 수정 필요 시 별도 ops worktree 리프로 넘긴다. 원본 앱 디렉터리 전체를 수정 리프로 재발행할 근거는 없다.
최종 검증은 같은 SHA의 backend_e2e 결과 및 orders sentinel을 확인해야 한다. 오류 원인이 달라진 것과 전체 성공을 구분한다.
이번에는 golden 실행, DB 생성, healthcheck 실행 및 원본 종결을 수행하지 않았다.

## 전체 files 범위를 유지해야 할 때의 대안
아래는 실제 추적 파일 30개를 중복 없이 15개 묶음으로 나눈 QA 제안이다. 각 묶음은 최대 3파일이며 발행·재큐 승인이 아니다.
B1~B5가 golden backend 고정 대상이고 F2~F3이 golden journey 대상이다. 나머지는 확대 QA로 분리한다.
공통 지원 파일은 지정 묶음이 소유하고 다른 묶음에서는 읽기 전용으로 참조한다. B1~B6은 B0, B8은 B7, F2~F5는 F0/F1 증빙 이후 검증한다.
실행 시간·컨텍스트 적합성은 아직 측정하지 않았으므로 파일 수만으로 로컬 레인 적합을 확정하지 않는다.

### B0 공통 DB 픽스처
- `tests/e2e/conftest.py` (36 lines)
- `tests/e2e/test_conftest_deepen_10090.py` (150 lines)
- `tests/e2e/__init__.py` (0 lines)

### B1 golden 주문
- `tests/e2e/test_e2e_1_paper_order_lifecycle.py` (380 lines)

### B2 golden watchdog
- `tests/e2e/test_e2e_2_watchdog_liquidate.py` (354 lines)
- `tests/e2e/test_e2e_2_watchdog_liquidate_deepen.py` (265 lines)
- `tests/e2e/_watchdog_liquidate_shared.py` (189 lines)

### B3 golden 시장데이터
- `tests/e2e/test_e2e_3_market_data_to_chart.py` (485 lines)
- `tests/e2e/market_data_helpers.py` (288 lines)

### B4 golden 백테스트
- `tests/e2e/test_e2e_4_script_to_backtest_report.py` (330 lines)

### B5 golden 컴플라이언스
- `tests/e2e/test_e2e_5_compliance_report_submit.py` (500 lines)

### B6 추가 안전·정산
- `tests/e2e/test_kill_switch_blocks_submission.py` (243 lines)
- `tests/e2e/test_order_execution_settlement.py` (284 lines)
- `tests/e2e/test_restart_recovery.py` (317 lines)

### B7 별도 demo 인증
- `tests/e2e/bitget_demo/__init__.py` (0 lines)
- `tests/e2e/bitget_demo/conftest.py` (56 lines)
- `tests/e2e/bitget_demo/test_bitget_demo_credential_guard.py` (141 lines)

### B8 별도 demo 파이프라인
- `tests/e2e/bitget_demo/test_bitget_demo_pipeline.py` (287 lines)

### F0 공통 브라우저 지원
- `frontend/e2e/support/fieldControl.ts` (12 lines)
- `frontend/e2e/support/mockBackend.ts` (283 lines)

### F1 체감 성능 지원
- `frontend/e2e/support/perceivedPerf.mjs` (118 lines)
- `frontend/e2e/support/perceivedPerf.test.mjs` (154 lines)

### F2 golden 온보딩·탐색
- `frontend/e2e/journey-j1-onboarding-to-dashboard.spec.ts` (363 lines)
- `frontend/e2e/journey-j2-discover-to-backtest.spec.ts` (272 lines)

### F3 golden 주문·긴급중지
- `frontend/e2e/journey-j3-paper-order-to-position.spec.ts` (398 lines)
- `frontend/e2e/journey-j9-emergency-stop.spec.ts` (129 lines)

### F4 기타 화면
- `frontend/e2e/backtest-run.spec.ts` (21 lines)
- `frontend/e2e/chart-indicator-overlay.spec.ts` (28 lines)

### F5 기타 주문·데모
- `frontend/e2e/demo-onboarding-flow.spec.ts` (24 lines)
- `frontend/e2e/order-submission.spec.ts` (66 lines)

## 검증 계획과 완료 범위
백엔드: 각 묶음 test_*.py만 지정하여 격리 DB로 pytest -q -p no:cacheprovider 실행. 지원 파일은 수집 대상이 아니다.
프론트: frontend에서 각 *.spec.ts를 Playwright 대상으로 명시하고 기존 playwright.config.ts를 사용한다. perceivedPerf.test.mjs는 Node test로 별도 검증한다.
Bitget demo는 외부 인증이 필요한 별도 범위이며 이번 조사에서 실행하지 않았다.
공유 읽기 의존성 tests/conftest.py, tests/support/db.py, frontend/playwright.config.ts는 원본 files 밖이므로 수정이 필요하면 별도 범위를 제안해야 한다.
이번 산출물은 문서뿐이다. 단위/DB/실패주입/성능/replay 검증 및 ruff/mypy/zone 검사: N/A(실행 코드 변경 없음).
파일 존재, 중복 없음, 원본 범위 전체 포함(30/30), 묶음당 최대 3파일을 프로그램으로 검증했다.
