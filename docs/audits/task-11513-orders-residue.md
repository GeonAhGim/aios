# task-11513 주문 잔여물 전역 조사 (2026-10-15)

기준 HEAD: `5bce813fc`. 규범: INVARIANTS I-07/I-10, FA-15/L4-26.
이 문서는 조사 증거이며 구현 규범을 대체하지 않는다.

## 복원과 원인

보존 패치 `task-11513-attempt2-20261014T145940Z.patch`는 줄바꿈 차이 때문에
기본 git apply가 실패했다. UTF-8 및 ignore-space-change로 복원하고 LF로 정규화했다.
PR 158 verify는 CANCELLED였으며 전체 검증 통과 증거가 없었다.

- FA-3 백필: 공유 주문의 version을 이벤트 없이 증가시킨다. 두 마이그레이션
  테스트를 테스트별 클론으로 전환했다. 기존 트리거 해제 후 광역 정리는 제거했다.
- raw 주문 시더: 의도적으로 불완전한 이벤트/주문을 만드는 모듈은 아래 목록처럼
  모듈 클론으로 격리했다. DATABASE_URL 및 dotenv_values 대역을 함께 복원한다.
- OMS 동시 UPDATE: 모든 writer를 gather(return_exceptions=True)로 회수한 뒤
  성공/단언 실패/writer 실패/gather 실패 모두 finally에서 해당 주문만 삭제한다.
- 추가 발견: test_execution_tick.py 및 test_execution_tick_distrust.py가
  tick-test 주문을 FILLED/SUBMITTED, version=1, 이벤트 0건으로 남겼다.
  헬퍼를 사용하는 execution_tick 모듈들을 동일하게 격리했다.
- replay/FA-4/child_qty 시험 클론 이름이 배정 DB 이름과 합쳐 40자를 넘었다.
  접미사만 줄였으며 이름 검증 한도와 테스트 단언은 유지했다.

## 감시와 적색 재현

기존 tests/conftest.py의 plugin 등록은 그대로 사용한다. 첫 fixture 전 baseline,
모듈/세션의 모든 finalizer 후 원래 워커 DB를 검사한다. replay_verify에 더해
order_events가 없는 orders도 동일 _order_pair 투영으로 비교한다. 초기 상태와
일치하는 행은 허용한다. pre-cutover 예외는 변경하지 않았다.

오류는 원인 모듈/마지막 nodeid와 주문 UUID를 포함한다. 같은 키가 이후에도
남아 있으면 다시 실패하므로 후속 오류 수를 별도 발생원 수로 세지 않는다.
실제 더미 pytest는 본문 1 passed / teardown 1 error를 내고 writer 이름과 키가
출력된다. pytest 경고 개수와 독립적으로 이 두 결과를 검증한다.

D2/D3: 세 필드 불일치 negative, 연결 실패주입/풀 close, 클론 예외시 DROP/환경 복원,
실제 FA-3 백필 오염 검출 및 공유 주문 보존, 동시 writer 실패 정리 회귀가 포함된다.
성능은 기존 p95/throughput 예산 시험을 그대로 실행하며 예산을 완화하지 않는다.
전체 로컬 스위트는 worker 지침으로 금지되어 CI 검증이 필요하다.

## grep + AST 전수 후보

`rg -n -i '(insert\s+into|update)\s+(orders|order_events)\b' tests --glob '*.py'`
결과 100줄. tests/**/*.py를 ast.parse하고 docstring을 제외한 문자열 Constant에서
같은 정규식을 검사했다. 인접/여러 줄 리터럴을 포함하고 같은 줄/테이블은 중복 제거한다.
동적 테이블명 및 repository 내부 SQL은 정적 SQL 목록의 한계이므로 헬퍼 호출자도
검증 대상에 포함했다. 공유라는 표시는 오염 확정이 아니라 비격리 DB 경로를 뜻한다.

51파일, 94 위치.

| 파일 | 줄:테이블 | DB 경로/정정 |
|---|---|---|
| `tests/adversarial/ems/test_child_order_bypass.py` | 46:orders, 110:orders | 정적 검사 입력 문자열; 실제 SQL 실행 아님 |
| `tests/adversarial/ems/test_overcommit_and_terminal_races.py` | 82:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/adversarial/eventstore/test_no_state_change_without_event.py` | 66:orders, 155:orders, 164:orders, 174:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/adversarial/eventstore/test_oms_no_state_change_without_event.py` | 235:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/adversarial/oms/test_concurrent_submit.py` | 260:orders, 277:orders | 정적 검사 입력 문자열; 실제 SQL 실행 아님 |
| `tests/adversarial/oms/test_cross_tenant_isolation.py` | 107:order_events | 기존/정정된 별도 클론 |
| `tests/adversarial/oms/test_duplicate_delivery_gate.py` | 86:orders | 공유 워커 풀; 모듈 종료 sentinel 검증 대상 |
| `tests/adversarial/oms/test_duplicate_delivery_storm.py` | 69:orders | 공유 워커 풀; 모듈 종료 sentinel 검증 대상 |
| `tests/adversarial/oms/test_tampered_provider_event.py` | 68:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/adversarial/risk/test_orders_risk_decision_trigger.py` | 54:orders, 109:orders | 공유 워커 풀; 모듈 종료 sentinel 검증 대상 |
| `tests/adversarial/risk/test_trigger_execution_ref.py` | 39:orders, 240:orders | 공유 워커 풀; 모듈 종료 sentinel 검증 대상 |
| `tests/foundation/integration/performance/conftest.py` | 87:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/_execution_tick_helpers.py` | 143:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/core/db/test_rls_legacy_not_enabled.py` | 213:orders, 216:order_events | 기존/정정된 별도 클론 |
| `tests/integration/ems/conftest.py` | 136:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/ems/test_committed_child_qty_replay.py` | 204:orders | 기존/정정된 별도 클론 |
| `tests/integration/ems/test_parent_aggregation.py` | 57:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/eventstore/test_replay_verify_order_chain.py` | 112:orders, 195:orders | 기존/정정된 별도 클론 |
| `tests/integration/eventstore/test_replay_verify_tamper_detection.py` | 168:orders, 251:orders, 385:orders, 439:orders | 기존/정정된 별도 클론 |
| `tests/integration/foundation/allocation/conftest.py` | 72:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/foundation/entities/test_fa3_shared_order_isolation.py` | 53:orders, 71:orders | FA-3 테스트별 클론; 공유 주문은 finally DELETE |
| `tests/integration/foundation/entities/test_migration_fa3_deepen.py` | 71:orders, 126:orders, 172:orders | 기존/정정된 별도 클론 |
| `tests/integration/foundation/entities/test_migration_fa3_orders_fills_columns.py` | 121:orders, 157:orders, 175:orders | 기존/정정된 별도 클론 |
| `tests/integration/foundation/entities/test_migration_fa4_worm_ledger_journal.py` | 291:orders, 294:order_events | 기존/정정된 별도 클론 |
| `tests/integration/mandates/test_compliance_violation_release.py` | 90:orders, 102:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/mandates/test_post_trade_batch.py` | 80:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/_reconciler_test_support.py` | 62:orders, 88:orders, 109:order_events | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/oms/conftest.py` | 121:orders, 143:order_events | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/oms/test_bitget_private_ws_to_inbox.py` | 119:orders, 346:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_cancel_modify_deepen.py` | 116:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_cancel_order.py` | 36:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_cancel_requested_replay.py` | 186:orders | 기존/정정된 별도 클론 |
| `tests/integration/oms/test_db_transition_trigger.py` | 161:orders, 165:orders, 183:orders, 196:orders, 214:orders, 232:orders, 252:orders, 261:orders, 270:orders, 296:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_gate_perf_multiinstance.py` | 58:orders, 89:orders, 117:orders | 공유 풀; transaction rollback + 동시성 주문 finally DELETE |
| `tests/integration/oms/test_inbox_processor.py` | 84:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_migration_d4e8f1a29c37_child_qty_committed_backfill.py` | 112:orders, 173:orders | 공유 워커 풀; 모듈 종료 sentinel 검증 대상 |
| `tests/integration/oms/test_modify_order.py` | 80:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_order_query.py` | 109:orders, 236:orders, 241:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_order_repository.py` | 171:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_orders_residue_hook.py` | 25:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_stale_worker_late_write.py` | 230:orders, 300:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_unknown_resolver.py` | 36:orders, 188:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/oms/test_unknown_resolver_failure_injection.py` | 79:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/risk/test_kill_switch_service.py` | 73:orders | 공유 워커 풀; 모듈 종료 sentinel 검증 대상 |
| `tests/integration/services/_exposure_snapshot_fixtures.py` | 113:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/integration/test_metrics_collector.py` | 75:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/integration/test_order_service.py` | 273:orders, 436:orders, 455:orders, 478:orders, 502:orders | 기존 테스트별 isolated_order_service_pool; finally DROP |
| `tests/integration/test_restart_recovery.py` | 241:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/performance/oms/_fixtures.py` | 101:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |
| `tests/performance/test_pre_trade_latency.py` | 205:orders | 모듈 클론; finally DROP, 환경 복원 |
| `tests/unit/services/conftest.py` | 66:orders | 호출자 풀 주입; 아래 헬퍼 호출자 검증 참조 |

## 모듈 클론으로 전환한 전체 목록

- `tests/adversarial/ems/test_overcommit_and_terminal_races.py`
- `tests/adversarial/eventstore/test_no_state_change_without_event.py`
- `tests/adversarial/eventstore/test_oms_no_state_change_without_event.py`
- `tests/adversarial/oms/test_crash_between_send_and_commit.py`
- `tests/adversarial/oms/test_live_mode_bypass_and_tampered_event_deepen.py`
- `tests/adversarial/oms/test_tampered_provider_event.py`
- `tests/foundation/integration/performance/test_paper_input_adapter.py`
- `tests/integration/ems/test_algo_cancel.py`
- `tests/integration/ems/test_algo_lifecycle.py`
- `tests/integration/ems/test_parent_aggregation.py`
- `tests/integration/foundation/allocation/test_allocate_fills.py`
- `tests/integration/foundation/allocation/test_conftest_deepen.py`
- `tests/integration/mandates/test_compliance_violation_release.py`
- `tests/integration/mandates/test_post_trade_batch.py`
- `tests/integration/oms/test_background_loops_wiring.py`
- `tests/integration/oms/test_bitget_private_ws_to_inbox.py`
- `tests/integration/oms/test_cancel_modify_deepen.py`
- `tests/integration/oms/test_cancel_order.py`
- `tests/integration/oms/test_concurrent_dispatchers.py`
- `tests/integration/oms/test_db_transition_trigger.py`
- `tests/integration/oms/test_inbox_fills_idempotency.py`
- `tests/integration/oms/test_inbox_processor.py`
- `tests/integration/oms/test_inbox_processor_fill_seq.py`
- `tests/integration/oms/test_modify_order.py`
- `tests/integration/oms/test_order_query.py`
- `tests/integration/oms/test_order_repository.py`
- `tests/integration/oms/test_orders_residue_hook.py`
- `tests/integration/oms/test_outbox_repository.py`
- `tests/integration/oms/test_restart_recovery.py`
- `tests/integration/oms/test_stale_worker_late_write.py`
- `tests/integration/oms/test_three_way_reconciler.py`
- `tests/integration/oms/test_three_way_reconciler_deepen.py`
- `tests/integration/oms/test_unknown_resolver.py`
- `tests/integration/oms/test_unknown_resolver_failure_injection.py`
- `tests/integration/test_execution_tick.py`
- `tests/integration/test_execution_tick_distrust.py`
- `tests/integration/test_execution_tick_equity_deepen.py`
- `tests/integration/test_execution_tick_pause_and_race.py`
- `tests/integration/test_execution_tick_risk_decisions.py`
- `tests/integration/test_execution_tick_stage_failures.py`
- `tests/integration/test_metrics_collector.py`
- `tests/integration/test_order_service.py`
- `tests/integration/test_restart_recovery.py`
- `tests/perf/oms/test_perf_inbox_processing.py`
- `tests/perf/oms/test_perf_throughput.py`
- `tests/performance/oms/test_inbox_lag.py`
- `tests/performance/oms/test_oms_throughput.py`
- `tests/performance/test_pre_trade_latency.py`
- `tests/unit/services/test_open_order_sweeper.py`
- `tests/unit/services/test_open_order_sweeper_concurrency.py`
- `tests/unit/services/test_open_order_sweeper_edge.py`

## 헬퍼 호출자와 검증 범위

OMS insert_order, EMS/할당 fixture, _reconciler_test_support, _execution_tick_helpers,
_exposure_snapshot_fixtures, performance/OMS fixture 및 unit/services fixture의
호출자 중 실제 DB 쓰기 모듈과 변경 모듈, ledger/resync 피해자 시험을 합쳐
80개 모듈을 -n auto --dist loadfile (PYTEST_XDIST_AUTO_NUM_WORKERS=4)로 실행했다.
전체 테스트 스위트 검증과 동일하다고 해석하지 않는다.

첫 확대 결과: 6 failed, 553 passed, 29 errors (198.36s).
잔여물 오류는 tick-test 두 발생원 및 동일 키의 후속 재검출이었다.
별도 오류 5건은 FA-4/child_qty 클론 이름 길이로 확인해 정정했다.

기존 HEAD의 세 모듈을 별도 DB에 복사해 동일 실패 6건을 재현했다:
three_way_reconciler 3건, post_trade_batch 2건, compliance_violation_release 1건.
정상 ALLOW를 기대하지만 활성 mandate가 없어 CM_MANDATE_MISSING으로 DENY된다.
이 재현에서는 변경 전 테스트 본문을 git show HEAD:<path>로 읽었고,
현재 강화된 sentinel은 유지하여 추가 teardown 오류 3건도 관측했다.
운영 게이트 완화/단언 변경/skip으로 숨기지 않았다.


## 최종 검증 및 재개 지점

- 동일 80모듈, 4워커 loadfile: **558 passed, 6 failed, teardown errors 0** (207.24s).
  실패는 위 변경 전 HEAD에서도 재현한 mandate 결손 6건과 동일하다.
- 그 실행 직후 동일 gw0~gw3에서 replay_verify(hours=24): **모두 ok=True,
  mismatches=0**. 별도 eventless sentinel도 네 DB 모두 통과했다.
  기존 pre-cutover 경고는 그대로 출력되며 예외를 확대하지 않았다.
- ruff 전체, mypy src(1568파일), zone_manifest, code_ratchets, diff --check 통과.
- 로컬 전체 스위트는 실행하지 않았다. 전체 CI/loadfile 후 replay DoD는 미충족이다.
- worker의 "게이트 전부 통과해야 커밋" 규칙에 따라 새 커밋/푸시는 하지 않았다.
  push 전용 Guard도 실행하지 않았다. 기존 PR 158은 변경하지 않았다.
- 수정물은 작업트리 및 `task-11513-resumed.patch`에 보존한다. 이 패치는 표준
  git diff 형식이며 신규 파일도 포함한다. 현재 수정 트리에서 reverse --check로
  적용 가능성을 확인한다. 로그: task-11513-final-validation.log,
  task-11513-baseline-validation.log, task-11513-scoped-validation.log.
- 다음: mandate fixture 결손 6건을 별도 리프에서 근본 수정한 뒤 이 패치의
  관련 검사 재실행, 경로 지정 커밋, fetch/rebase, 지정 Guard, push 및 전체 CI/replay.
  이 작업을 전체 TASK done으로 보고하지 않는다.

## 재개 검증 (attempt 4, 2026-10-15)

기준 HEAD `a1279298a`. 보존 패치에는 tracked diff가 두 번 포함되어 있었다.
동일 파일 diff 중복을 제거한 64파일 패치는 git apply --check 통과 후 복원했다.
AST 재확인: 51파일/94 SQL 위치, 기존 목록 외 누락 0.

- 감시기/격리/실제 적색 재현: 23 passed (39.82s).
- 변경 파일 + 감사 목록 + 헬퍼 호출자 + ledger resync: 76모듈,
  4워커 loadfile, 542 passed / 기존 mandate 6 failed / teardown errors 0 (208.51s).
  이전 80모듈 실행과 선택 범위가 달라 건수를 직접 비교하지 않는다.
- 동일 gw0~gw3에서 replay_verify ok=True, mismatches=0 및 eventless sentinel OK.
  기존 pre-cutover 경고/정책을 변경하지 않았다.
- ruff 전체, mypy src 1569파일, zone, ratchets, diff-check 통과.
  초기 mypy --cache-dir=NUL은 내부 오류였으며 정상 캐시로 재실행해 통과했다.
- PM decision에 따라 mandate 6건은 별도 리프 범위다. 필수 게이트 전체 통과 전
  커밋 금지에 따라 commit/push/Guard 미실행, 전체 CI DoD 미충족으로 blocked.
- 검증 로그: task-11513-validation.log. 표준 복원 패치:
  task-11513-resumed.patch (신규 파일 포함, 중복 없음, reverse --check 통과).
- 다음: 별도 mandate 수정 반영 후 관련 검증, 경로 지정 commit, fetch/rebase,
  Guard, push, 전체 CI/loadfile 및 replay 확인. 기존 실패를 제외하거나 완화하지 않는다.

## 재개 검증 (attempt 5, 2026-10-16 KST)

기준 HEAD `19d358419`. 보존 패치 중복 diff와 CRLF를 제거해 64파일 복원.
FA-3의 task-12155 후속 증명 테스트를 유지하고 실제 공유 워커 DB를 검사하도록 병합했다.
AST 전수 51파일/94 SQL 위치, 누락 0. 위 표의 줄 번호는 현재 코드로 갱신했다.

- 마지막 실패 재검증: 33 passed, 기존 mandate 결손 6 failed (20.65s).
- 감시기·격리·실패주입·FA-3·ledger resync: 4워커 loadfile 38 passed (48.45s).
- 실행 직후 gw0~3 replay_verify 모두 ok=True, mismatch=0; eventless 검사 통과.
- ruff, mypy(1569), zone, ratchets, diff-check 통과.
- 기존 mandate 6건이 미해결이므로 전체 게이트 통과 전 커밋 금지 규칙상 blocked.
  commit/push/Guard 미실행; 전체 CI/loadfile DoD 미충족.
- 다음: 별도 mandate fixture 수정 반영 후 관련 전체 범위 검증, commit, fetch/rebase,
  Guard, push, 전체 CI 및 replay 확인. 수정물은 task-11513-resumed.patch에 보존한다.

## 재개 검증 (attempt 6, 2026-10-16 KST)

기준 HEAD `d887e819b`. 중복 diff 58개 중 현행 코드와 호환되는 50개를 복원했다.
mandate 2파일은 새 fixture 변경을 유지하며 격리 import만 병합했다. FA-3/FA-4,
child_qty, eventstore 접미사와 order_service 격리는 이미 main에 반영되어 재적용하지 않았다.
AST 51파일/94 SQL 위치 및 grep 100줄 재확인, 목록 누락 0; 위 표의 줄 번호를 갱신했다.
마지막 mandate 실패 범위와 감시기 회귀: 39 passed (4워커 loadfile).

변경·발생원·ledger resync 74모듈: 538 passed (230.98s), -n 4 --dist loadfile.
직후 gw0~gw3 replay_verify 모두 ok=True/mismatches=0, eventless sentinel 모두 OK.
기존 pre-cutover 경고는 유지했으며 replay 예외·단언을 완화하지 않았다.
ruff 전체, mypy src 1569파일, zone_manifest, code_ratchets, diff-check 통과.
기준 HEAD의 Actions 38809056815는 60 failed/15289 passed로 기존 적색이다.
mandate 누락 E2E와 ledger integrity 등 이 리프 밖 실패가 남아 전체 DoD와 구분한다.

간접 헬퍼 호출자·기존 적대 테스트 추가 12모듈: 55 passed (38.05s).
추가 실행 직후 gw0~gw3 replay/eventless 모두 OK.
