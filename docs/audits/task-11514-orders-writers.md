# task-11514 주문 직접 쓰기 전수 검색 (2026-10-15)

최종 재개 작업 기준: 19d358419 (2026-10-16); 아래 AST 줄은 이번 수정 후 기준. 조사 범위는 tests/**/*.py 전체.
`rg -n -i "(insert\s+into|update)\s+(public\.)?orders\b" tests --glob "*.py"`와
Python AST의 문자열 상수(인접 문자열 연결 포함, 문서 문자열 제외)를 교차 검색했다.
f-string의 고정 부분도 포함한다. 동적으로 구성되는 테이블명과 repository를 통한
간접 쓰기는 이 정적 검색만으로 완전성 증명이 되지 않으므로 감시기로 보완한다.
아래 줄은 SQL 문자열 시작 줄이며 grep의 SQL 본문 줄과 다를 수 있다.

## 기존 수정과 이번 복원

- 4d72f8c97(task-11767): tests/conftest.py가 OrdersResidueSentinel을 등록한다.
  워커 시작 전 및 모듈/세션 finalizer 이후 동일 워커 DB를 replay_verify로 검사한다.
  cross_tenant_isolation의 이벤트만 남기는 발생원은 격리되었고
  tests/adversarial/oms/test_orders_residue_source.py가 실제 오염을 재현한다.
- 이번 변경: test_order_service.py의 공용 풀을 테스트별 클론으로 전환한다.
  풀 연결/테스트 본문/풀 close 실패에서도 finally에서 DROP을 시도한다.
  보존 패치의 helper와 4가지 종료 경로 테스트를 복원했다.
- tests/conftest.py가 dotenv_values를 테스트 워커 URL로 치환한다.
  .env 호출 자체를 개발 DB 접근으로 분류하지 않았다.
- 기존 replay_verify의 pre-cutover 조건은 변경하지 않았다.

## 직접 쓰기 위치 및 풀 분류

공용 풀이라는 사실은 오염 확정을 뜻하지 않는다. 이벤트 유무 열도 파일 내 참조 여부이며
각 SQL과 원자적으로 동반된다는 뜻은 아니다. 클론과 공용 쓰기가 혼재하는 파일을
통째로 격리 완료로 표시하지 않는다.

| 파일 | AST 줄 | 풀 분류 | 파일 내 이벤트 참조 |
|---|---|---|---|
| `tests/adversarial/ems/test_child_order_bypass.py` | 46, 110 | 정적 검사 입력 문자열 (실제 SQL 실행 아님) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/ems/test_overcommit_and_terminal_races.py` | 81 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/eventstore/test_no_state_change_without_event.py` | 65, 154, 163, 173 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/adversarial/eventstore/test_oms_no_state_change_without_event.py` | 234 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/adversarial/oms/test_concurrent_submit.py` | 260, 277 | 정적 검사 입력 문자열 (실제 SQL 실행 아님) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/oms/test_duplicate_delivery_gate.py` | 86 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/oms/test_duplicate_delivery_storm.py` | 69 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/oms/test_tampered_provider_event.py` | 67 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/risk/test_orders_risk_decision_trigger.py` | 54, 109 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/adversarial/risk/test_trigger_execution_ref.py` | 39, 240 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/foundation/integration/performance/conftest.py` | 87 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/_execution_tick_helpers.py` | 143 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/core/db/test_rls_legacy_not_enabled.py` | 213 | 해당 UPDATE는 migration_pool 격리 클론 | 있음 |
| `tests/integration/ems/conftest.py` | 136 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/ems/test_committed_child_qty_replay.py` | 204 | 테스트별 격리 클론; finally DROP | 있음 |
| `tests/integration/ems/test_parent_aggregation.py` | 56 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/eventstore/test_replay_verify_order_chain.py` | 112, 195 | 격리 클론 | 있음 |
| `tests/integration/eventstore/test_replay_verify_tamper_detection.py` | 168, 251, 385, 439 | 격리 클론 | 있음 |
| `tests/integration/foundation/allocation/conftest.py` | 72 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/foundation/entities/test_migration_fa3_deepen.py` | 71, 126, 172 | 테스트별 격리 클론; finally DROP | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/foundation/entities/test_migration_fa3_orders_fills_columns.py` | 95, 131, 149 | 테스트별 격리 클론; finally DROP | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/foundation/entities/test_migration_fa4_worm_ledger_journal.py` | 291 | 테스트별 격리 클론; finally DROP | 있음 |
| `tests/integration/mandates/test_compliance_violation_release.py` | 89, 101 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/mandates/test_post_trade_batch.py` | 79 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/oms/_reconciler_test_support.py` | 62, 88 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/conftest.py` | 121 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_bitget_private_ws_to_inbox.py` | 118, 345 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/oms/test_cancel_modify_deepen.py` | 114 | 공용 OMS pool; 자식 pytest도 같은 워커 DB | 있음 |
| `tests/integration/oms/test_cancel_order.py` | 35 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_cancel_requested_replay.py` | 186 | 전용 replay 클론 | 있음 |
| `tests/integration/oms/test_db_transition_trigger.py` | 160, 164, 182, 195, 213, 231, 251, 260, 269, 295 | SQL 쓰기는 공용 pool/rollback; roundtrip만 클론 | 있음 |
| `tests/integration/oms/test_gate_perf_multiinstance.py` | 57, 85, 113 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/oms/test_inbox_processor.py` | 83 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/oms/test_migration_d4e8f1a29c37_child_qty_committed_backfill.py` | 112, 173 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_modify_order.py` | 79 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_order_query.py` | 108, 235, 240 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_order_repository.py` | 170 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_stale_worker_late_write.py` | 229, 299 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/oms/test_unknown_resolver.py` | 35, 187 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/oms/test_unknown_resolver_failure_injection.py` | 77 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 있음 |
| `tests/integration/risk/test_kill_switch_service.py` | 73 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/services/_exposure_snapshot_fixtures.py` | 113 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/test_metrics_collector.py` | 74 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/integration/test_order_service.py` | 273, 436, 455, 478, 502 | 격리 클론 | 있음 |
| `tests/integration/test_restart_recovery.py` | 240 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/performance/oms/_fixtures.py` | 101 | performance/oms 소비자는 테스트별 클론 | 없음 (동반 이벤트 보장 아님) |
| `tests/performance/test_pre_trade_latency.py` | 204 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |
| `tests/unit/services/conftest.py` | 66 | 공용 워커 DB; 모듈/상속 fixture (rollback 경로 포함) | 없음 (동반 이벤트 보장 아님) |

## 이전 시도 결과와 당시 미완료 범위 (2026-10-15)

- 2026-10-15: 보존 패치의 tracked diff를 수동 복원했고 helper/단위 테스트를 복구했다.
- 변경 관련 5개 파일(격리 helper 단위, sentinel 단위, order_service,
  orders_residue_source, ledger/resync_drift): `-n 4 --dist loadfile` 35 passed (44.87s).
- 전체 tests Python AST 문자열 상수 검색을 재실행했고 위 목록과 일치했다.
  직접 쓰기 test_*.py 및 피해자/발생원 재현 파일을 함께 실행:
  293 passed, 3 failed, 18 errors (120.80s). 전체 스위트 결과가 아니다.
- 18 errors: eventstore replay chain/tamper, EMS child quantity replay,
  FA4 migration fixture의 클론 이름이 tests/support/db.py의 이름 제한에 걸림.
  예: aios_test_codex_escalation_1_gw1_rv_tamper.
- 3 failed: test_post_trade_batch의 no_violation/market_abuse_warn_only,
  test_compliance_violation_release의 release_with_evidence_ref.
  이 두 모듈만 깨끗한 xdist 클론에서 재실행해도 3 failed, 11 passed (27.25s).
  이번 변경 파일을 실행하지 않아도 ALLOW 기대에 DENY가 반환된다.
  foundation_gate의 compliance bundle 결손 기본 DENY 변경과 기존 fixture의
  bundle 미지정 조합을 후속 확인해야 한다. 판정/정책은 변경하지 않았다.
- 확장 실행 직후 gw0/gw1/gw2/gw3 각각 실제 replay_verify.verify(hours=24):
  ok=True, mismatches=[] (4/4). 기존 pre-cutover 경고/제외 정책은 그대로다.
- ruff(src/tests/scripts), mypy(src 1569 files), zone manifest,
  code ratchets(--near 30), git diff --check 통과.
- 미완료: 공용 후보의 모든 간접 호출/fixture 소비자 동적 검증,
  실제 dummy pytest subprocess의 teardown 실패 증명, 전체 실행 DoD.
  후속 worker 지침의 전체 스위트 실행 금지에 따라 전체 pytest tests/는 실행하지 않았다.
  현재 결과를 전체 완료로 판정하지 않는다. 실패 검증이 남아 커밋/푸시는 수행하지 않았다.

## 2026-10-16 재개

- 기존 감시기를 유지하고, 실제 pytest subprocess의 module finalizer가 오염을
  남기는 경우 `1 passed, 1 error`와 원인 nodeid를 단언하는 재현을 복원했다.
  깨끗한 대조군도 함께 검사한다(I-07/I-10).
- FA3 마이그레이션 2개 모듈은 공유 DB의 downgrade/backfill로 기존 주문의
  재생 대상 컬럼을 바꾸므로 테스트별 clone/finally DROP으로 전환했다.
  기존 마지막 증명 테스트는 fixture가 바꾼 DATABASE_URL 대신 TEST_DATABASE_URL을
  검사해 실제 공용 워커를 확인한다.
- EMS는 원본 템플릿에서 짧은 cq 클론을 만들고 연결 실패에도 DROP한다.
  eventstore rvc/rvt 및 FA4 f4 접미사로 40자 제한을 준수한다.
- 이전 blocker인 compliance 3건은 task-12102의 fail-closed 기본값 변경 후
  활성 위임장을 만들지 않은 테스트 입력 문제였다. 실제 create/activate helper로
  해당 세 tenant의 위임장을 준비하며 정책 옵션·ALLOW 단언은 그대로 유지한다.
- 변경 관련 86 passed / 96.90s, ruff/mypy/zone/ratchets 통과.
- AST 전수 재검색: 직접 SQL 문자열을 포함한 48개 파일. 정적 검사 입력 2개도
  표시했으며, 실제 SQL·fixture 쓰기는 위 표에 전부 포함한다.

- 직접 SQL 모듈 확장: 수정 전 compliance 전제 실패 3건 외 314 passed
  (160.41s); 활성 위임장 보완 후 해당 두 모듈 14 passed (9.80s).
- 확장 실행 직후 gw0~gw3 각각 replay_verify(24h): 전 도메인 OK, mismatch=0.
  기존 pre-cutover 알림은 그대로 출력되며 검사 조건을 확대하지 않았다.
- SQL grep만으로 잡히지 않는 실제 사건 발생원은
  `tests/adversarial/oms/test_cross_tenant_isolation.py`의
  `test_list_order_events_cross_tenant_does_not_leak_existence`: 주문 상태 변경 없이
  이벤트만 추가하는 경로다. 기존 main의 isolated_event_pool로 격리되어 있으며
  이번 subprocess 증명은 바로 이 발생원을 finalizer에서 호출한다.

- 간접 helper/conftest 소비자 102개 모듈: 740 passed, 4 failed (147.03s).
  세 실패는 reconciliation ALLOW 대조군도 활성 위임장이 누락된 동일 전제 문제로
  실제 위임장을 준비했다. 나머지 성능 테스트는 공유 전역 outbox에서
  claimed=1/retried=1/acknowledged=0을 받아 정확 왕복 수 측정 전제가 깨졌다.
  drain은 미래 not_before 행을 제거하지 못하므로 performance/oms pool을
  원본 템플릿의 테스트별 클론(max_size=1 유지)으로 격리하고 finally DROP한다.
- 전체 tests/ 실행은 worker 지침의 명시적 금지에 따라 하지 않았다.
  직접 쓰기 및 fixture 소비자를 지정한 4 worker/loadfile 검증이며 전체 실행은 CI 몫이다.
  전체 스위트 DoD를 로컬에서 충족했다고 주장하지 않는다.
- D2/D3: 격리 helper 연결/본문/close 실패 3종, 실제 더미 pytest teardown
  적색 재현과 clean 대조군, 기존 FA3 backfill 수치 성능 및 OMS 왕복 수 단언,
  I-07/I-10 발생원 적대적 재현과 4 worker replay_verify로 증명한다.

- 최종 추가 수정 검증: reconciliation + performance/oms + 두 failure-injection 모듈
  35 passed (57.61s). 최종 gw0~gw3 replay_verify: 모두 OK, mismatch=0.
