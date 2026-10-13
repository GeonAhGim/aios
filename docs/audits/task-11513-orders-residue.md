# task-11513 주문 잔여물 전역 조사 (2026-10-13)

기준: `f88aa4be8`. 이 문서는 구현 근거가 아닌 조사 증거다.
규범: ADR-2026-09-26-D Decision 3, INVARIANTS I-07/I-10, FA-15/L4-26.

## 재개 상태 및 기존 정정

작업 트리는 clean이었다. 지정된 보존 패치는 task-11844의
`test_check_migration_chain_merge_1814_1987.py` 성능 측정 변경이며 HEAD에 이미 있다.
주문 감시기 패치로 재적용하지 않았다.

- `tests/integration/test_order_service.py:294,457,499`: 이벤트 없는 직접 상태 변경.
  `e6d53ff3b` 등에서 finally 상태 복원. 476/523은 복원 SQL이다.
- `tests/adversarial/oms/test_cross_tenant_isolation.py:107`: 반대로 이벤트만 INSERT.
  `4d72f8c97`에서 테스트별 클론과 finally DROP으로 격리했다.
- `tests/conftest.py:484`: 전역 OrdersResidueSentinel 등록.
  `tests/support/orders_residue.py`: 첫 테스트 전 baseline, 모듈/세션의 모든 fixture
  finalizer 이후 replay 검증. 메시지에 마지막 nodeid를 넣어 원인 모듈을 식별한다.
- `tests/adversarial/oms/test_orders_residue_source.py`: 실제 오염 시더 실행 후
  sentinel AssertionError 및 공유 DB 청결을 함께 검증한다. skip 없이 실행된다.
- `tests/unit/scripts/test_orders_residue_sentinel.py`: 오류 전파, 풀 종료,
  baseline 실패, finalizer 순서, 모듈 내부 추가 스캔 0회, 테스트별 진단 검증.

## 조사 방법 및 한계

`rg -n -i '(insert\s+into|update)\s+(orders|order_events)\b' tests`
이후 tests/**/*.py 전부를 ast.parse하고 docstring을 제외한 문자열 Constant를
같은 SQL 패턴으로 검사했다. AST는 인접 리터럴 및 여러 줄 SQL도 읽는다.
아래 줄은 SQL 문자열 시작 위치이며 한 문자열/테이블 조합을 1건으로 센다.
동적으로 계산하는 테이블 이름, 리포지토리 메서드 내부 SQL은 이 목록에 포함되지
않는다. 헬퍼의 DB는 호출자에게서 주입되므로 헬퍼 위치만으로 격리를 보증할 수 없다.

공유는 DATABASE_URL(직렬에서는 배정 DB, xdist에서는 워커 DB) 풀 사용을 뜻하며
그 자체가 오염 확정은 아니다. rollback/이벤트 동반 여부는 각 실행 경로에서
판정해야 한다. 과거 커밋의 '나머지 전부 rollback' 주장은 충분하지 않다:
`test_gate_perf_multiinstance.py:113`은 공유 DB에서 이벤트 없이 version을 변경한다.
현재 replay_verify는 order_events의 최근 키를 열거하므로 이벤트가 전혀 없는 행은
스캔 대상에 포함되지 않는다. 따라서 replay OK를 모든 raw orders 행의 청결 증명으로
해석하지 않는다. pre-cutover 예외를 확대하지 않았다.

## 전체 SQL 후보 목록

49파일, 91 문자열/테이블 조합. 정적 검사 입력 1파일도 누락 방지를 위해 표시한다.

| 파일 | 줄:테이블 | DB 경로 |
|---|---|---|
| `tests/adversarial/ems/test_child_order_bypass.py` | 46:orders, 110:orders | 정적 검사 입력 SQL 문자열; DB 쓰기 아님 |
| `tests/adversarial/ems/test_overcommit_and_terminal_races.py` | 81:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/eventstore/test_no_state_change_without_event.py` | 65:orders, 154:orders, 163:orders, 173:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/eventstore/test_oms_no_state_change_without_event.py` | 234:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/oms/test_concurrent_submit.py` | 260:orders, 277:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/oms/test_cross_tenant_isolation.py` | 107:order_events | 이벤트 시더: isolated_event_pool, finally DROP (4d72f8c97) |
| `tests/adversarial/oms/test_duplicate_delivery_gate.py` | 86:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/oms/test_duplicate_delivery_storm.py` | 69:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/oms/test_tampered_provider_event.py` | 67:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/risk/test_orders_risk_decision_trigger.py` | 54:orders, 109:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/adversarial/risk/test_trigger_execution_ref.py` | 39:orders, 240:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/foundation/integration/performance/conftest.py` | 87:orders | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/_execution_tick_helpers.py` | 143:orders | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/core/db/test_rls_legacy_not_enabled.py` | 213:orders, 216:order_events | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/ems/conftest.py` | 136:orders | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/ems/test_committed_child_qty_replay.py` | 194:orders | 전용 child_qty 클론 |
| `tests/integration/ems/test_parent_aggregation.py` | 56:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/eventstore/test_replay_verify_order_chain.py` | 112:orders, 195:orders | isolated_replay_pool, finally DROP |
| `tests/integration/eventstore/test_replay_verify_tamper_detection.py` | 168:orders, 251:orders, 385:orders, 439:orders | isolated_replay_pool, finally DROP |
| `tests/integration/foundation/allocation/conftest.py` | 72:orders | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/foundation/entities/test_migration_fa3_deepen.py` | 70:orders, 117:orders, 163:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/foundation/entities/test_migration_fa3_orders_fills_columns.py` | 83:orders, 118:orders, 136:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/foundation/entities/test_migration_fa4_worm_ledger_journal.py` | 291:orders, 294:order_events | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/mandates/test_compliance_violation_release.py` | 84:orders, 96:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/mandates/test_post_trade_batch.py` | 74:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/_reconciler_test_support.py` | 62:orders, 88:orders, 109:order_events | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/oms/conftest.py` | 105:orders, 127:order_events | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/oms/test_bitget_private_ws_to_inbox.py` | 118:orders, 345:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_cancel_modify_deepen.py` | 114:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_cancel_order.py` | 35:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_cancel_requested_replay.py` | 186:orders | 전용 replay 클론 |
| `tests/integration/oms/test_db_transition_trigger.py` | 160:orders, 164:orders, 182:orders, 195:orders, 213:orders, 231:orders, 251:orders, 260:orders, 269:orders, 295:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_gate_perf_multiinstance.py` | 57:orders, 85:orders, 113:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_inbox_processor.py` | 83:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_migration_d4e8f1a29c37_child_qty_committed_backfill.py` | 112:orders, 173:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_modify_order.py` | 79:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_order_query.py` | 108:orders, 235:orders, 240:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_order_repository.py` | 170:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_stale_worker_late_write.py` | 229:orders, 299:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_unknown_resolver.py` | 35:orders, 187:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/oms/test_unknown_resolver_failure_injection.py` | 77:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/risk/test_kill_switch_service.py` | 73:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/services/_exposure_snapshot_fixtures.py` | 113:orders | 호출자 풀 주입; 공유 경로 포함 |
| `tests/integration/test_metrics_collector.py` | 74:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/test_order_service.py` | 294:orders, 457:orders, 476:orders, 499:orders, 523:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/integration/test_restart_recovery.py` | 240:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/performance/oms/_fixtures.py` | 101:orders | 호출자 풀 주입; 공유 경로 포함 |
| `tests/performance/test_pre_trade_latency.py` | 204:orders | 공유 워커 풀; 개별 transaction/복원 여부 별도 |
| `tests/unit/services/conftest.py` | 66:orders | 호출자 풀 주입; 공유 경로 포함 |

## 검증

- sentinel 단위: 8 passed.
- sentinel 단위 + 실제 시더 재현 + 교차 tenant + order_service + resync_drift:
  `-n auto --dist loadfile` (`PYTEST_XDIST_AUTO_NUM_WORKERS=4`), 35 passed / 21.50s.
- 전역 grep 대상 파일 묶음 첫 실행: 64 passed, pool fixture 등록 오류 1건.
  adversarial 파일이 integration conftest를 먼저 import하는 수집 순서에서 관측.
  integration 파일을 먼저 지정하여 원인 테스트 검증을 이어갔다.
- 전체 스위트 DoD는 미검증: worker 지침이 전체 pytest tests/ 실행을 금지한다.
  이 문서의 부분 실행을 전체 통과로 보고하지 않는다.

- 배정 DB에서 두 번째 실행: 156 passed, 복제 이름이 40자 제한을 넘어 1 error.
  검증 도구 규칙을 바꾸지 않고 배정 DB에서 짧은 이름의 일회용 템플릿을 복제했다.
- 짧은 템플릿에서 직접 SQL 포함 비성능 테스트 39모듈:
  4워커 loadfile, 290 passed / 85.84s. 명령은 위 grep 결과 중 test_*.py,
  performance 경로 제외, integration 파일 먼저 정렬, `-m 'not perf'`.
- 종료 직후 동일 gw0~gw3 DB에 replay_verify.verify(hours=24) 재실행:
  모두 ok=True, mismatches=0, streams_checked는 각각 0/4/0/0.
  기존 pre-cutover EventChainBroken 예외 경로가 적용된 스트림이 있으므로
  이 결과는 모든 시더가 이벤트 계약을 충족한다는 증거가 아니다.
  실행에서 생성한 고유 이름의 템플릿·하위 클론은 finally에서 전부 삭제했다.
- ruff(src/tests/scripts), mypy(src 1567파일), zone_manifest, code_ratchets 통과.
- 미해결 완료조건: 전체 스위트 + 전체 실행 후 replay OK는 CI에서 확인해야 한다.
  helper 호출자 전부 및 perf 단계는 위 39모듈 실행 범위 밖이다.
  순수 orders-only 행을 잡지 못하는 감시 범위 한계도 남아 있어 전체 요구 완료로
  처리하지 않는다. 후속 실행은 이 문서의 공유 경로부터 이벤트 동반/정리 규율을
  확인하고, events 없는 변경까지 검출하는 별도 상태 스냅샷 검증을 보강해야 한다.

## 보존·커밋 범위

task-11844 보존 패치 커밋 f88aa4be8은 별도 로컬 참조에 보존하며 이번 push에서
제외한다. 이번 변경은 전수 목록·검증 증거 문서이며 기존 구현 완료로 위장하지 않는다.
D2/D3 새 제품 코드 변경에 대한 시험은 N/A(감사 문서만 변경). 기존 sentinel의
음성·실패주입·적대 테스트는 위 35개 회귀 묶음에서 실제 실행했다.
