# ADR-2026-09-26-D: 통합테스트 DB 격리 — 창(window) 스캔·전역 상태 테스트는 모듈별 템플릿 클론에서 돈다

## Status
Proposed (2026-09-26, 클라우드 세션 초안). 사용자/CTO 결정 대기. 승인 시 Accepted로 갱신.

## Context (실측 2026-09-25~26, GitHub Quality Gate)
- xdist(`-n auto --dist loadfile`) 워커는 **워커당 DB 1개**(`tests/support/db.ensure_worker_database`)를 세션 내내 공유한다. 파일 단위로 분산되므로 한 워커 DB에는 그 워커가 돈 모든 파일의 잔재(leftover)가 누적된다.
- 하루 동안 main/PR 적색 원인 중 **같은 뿌리의 순서 의존 실패 9종**을 근본 수리했다(PR #91·#122):
  1. `order_command_outbox` leftover 선점(e2e-1 cancel leg, stale_worker_late_write)
  2. `exec:1` kill switch 잔재(no_mandate_reject)
  3. 살아있는 워커 DB를 템플릿으로 `CREATE DATABASE ... TEMPLATE` → `ObjectInUseError`(마이그레이션 클론 5곳)
  4. `strategy_executions` RUNNING 잔재로 서킷브레이커 판정 오염(cbloop)
  5. `replay_verify(hours=1)` 창 스캔이 다른 모듈의 이벤트 트레일 밖 주문을 StreamDiff로 보고(order_chain·replay_verify·cancel_requested 3모듈)
  6. `risk_replay --since` 창 스캔이 다른 모듈의 raw INSERT(`inputs_snapshot='{}'`)에서 ValidationError로 배치 크래시(도구 결함도 함께 수리)
  7. `system_safety_state` 싱글톤 행 오염(users_router 재인증 테스트가 직접 리셋)
  8. 공유 워커 DB의 커넥션 합산이 서비스 컨테이너 `max_connections=100` 초과(→400)
  9. `ledger_control.write_frozen` 잔재(eventstore/ledger conftest autouse 리셋, 기존)
- 공통 구조: **"창 전체가 clean"을 단언하는 테스트**(replay·감사·재생 검증)와 **전역 싱글톤 행에 의존하는 테스트**는 같은 워커 DB의 다른 파일이 무엇을 남겼는지에 따라 결과가 달라진다. 개별 수리(픽스처 리셋, 고유 id)는 형제 파일이 하나 늘 때마다 재발한다(오늘만 fleet 커밋 5건이 새 잔재·가드 위반을 추가).
- 이미 갖춘 도구: `ensure_worker_database(template_url, worker_id)`(템플릿 복제 ~1초), `template_database_url()`(#91: 살아있는 워커 DB가 아닌 원본 템플릿), `drop_worker_database()`(#91), `tests/integration/eventstore/conftest.py`의 `isolated_replay_db_url`/`isolated_replay_pool`(테스트별 클론 → 잔재 0건 가드 → DROP), `_replay_verify_support.assert_no_replay_window_leftovers`.

## Decision (제안)
1. **격리 등급을 명시한다.** 통합테스트 모듈은 셋 중 하나를 택하고 픽스처로 드러낸다.
   - `shared`: 기본. 자기 행만 만들고 자기 행만 단언한다(고유 id·고유 email). 전역 행을 UPDATE하지 않는다.
   - `isolated`: 창 스캔(`hours=`/`since=`) 또는 전역 싱글톤 행(`system_safety_state`, `ledger_control`, `oms_order_transition_cutover`, `risk_rule_bundle` 활성 등)을 읽거나 바꾸는 모듈. **테스트별 템플릿 클론**(`ensure_worker_database(template_database_url(), f"{worker}_{suffix}")`)에서 돌고 teardown에서 `drop_worker_database`. 등록 접미사는 40자 규칙 안에서 모듈별 고정.
   - `subprocess`: 스크립트를 subprocess로 실행하는 테스트는 클론 URL을 `DATABASE_URL`로 넘긴다(tamper_detection 선례).
2. **공용 픽스처를 `tests/support/db_isolation.py`로 승격한다.** 지금 eventstore conftest에 있는 `isolated_*` 픽스처와 잔재 가드를 일반화해(`isolated_db_url(suffix)` 팩토리 + `assert_window_clean(tables...)`) risk/replay_decision·safety(cbloop)·oms/cancel_requested 등 이미 격리한 모듈들이 같은 코드를 쓴다. 가드 메시지는 추적 키(order_id/account_code/decision_id)를 남긴다 — DB는 어느 모듈이 썼는지 기록하지 않으므로 모듈명은 넣을 수 없다.
3. **raw INSERT/UPDATE로 계약을 우회하는 시더는 금지 또는 격리.** 리포지토리 경로를 우회해 행을 만드는 테스트 헬퍼(`_insert_minimal_risk_decision`의 `inputs_snapshot='{}'`, `insert_order(status=...)` 등)는 (a) 계약을 만족하는 값으로 쓰거나 (b) 그 모듈을 `isolated`로 올린다. 검사는 정적 가드(`scripts/check_test_db_isolation.py`, AST: `INSERT INTO <hot table>` 문자열이 있는 테스트 모듈이 `isolated_*` 픽스처를 쓰지 않으면 경고)로, CLAUDE.md §6-6대로 **warn + baseline**으로 먼저 들어간다.
4. **전 파일 클론은 하지 않는다.** 파일마다 클론하면 워커당 파일 수 × ~1초가 순증(수백 초)하고, 대부분의 모듈은 `shared` 규율로 충분하다. 격리는 창 스캔·전역 행 모듈에만 적용한다(오늘 기준 6모듈).
5. **도구 자체의 견고성은 별도 결함으로 다룬다.** 잔재를 만난 재생/감사 도구가 배치 전체를 죽이면(risk_replay ValidationError) 그것은 운영 결함이므로 테스트 격리로 숨기지 않고 도구를 고친다(#91 03d44f43 선례: 계약 위반 행은 CORRUPT로 보고하고 계속 점검).

## 관련: perf 직렬 단계 warn(#91 6번)과 러너 배수 ADR
- #91에서 GitHub 4 vCPU 러너의 perf 직렬 단계를 `continue-on-error`(warn)로 두었다(예산표 미변경, 로컬 게이트가 perf 정본). 이후 main이 task-7631·7741로 perf 예산을 wall-clock 절대치에서 **자기보정 비율**로 전환했으므로, 별도 "러너 배수" ADR은 필요성이 낮아졌다.
- 제안: perf 직렬 단계가 main에서 **3일 연속 녹색**(ADR-2026-09-26-C 종결 지표와 같은 창)이면 `continue-on-error`를 제거해 하드 게이트로 되돌린다. 그때까지 남는 절대치 예산 테스트는 비율 예산으로 전환하는 리프를 발행한다.

## Consequences
- 오늘 격리한 6모듈이 참조 구현이 된다. 새 통합테스트 리프의 DoD에 "격리 등급 명시" 한 줄이 추가된다(ADR-2026-09-10-C의 체크리스트 원칙대로 floor).
- CI 시간: 격리 모듈 6개 × 테스트별 클론 ~1초 → 분당 수십 초 이내 증가(실측 #91 verify 21~24분, 변화 없음).
- 되돌림: 이 ADR Superseded + 공용 픽스처 제거, 모듈별 픽스처 원복.

## Open questions (결정 필요)
- 정적 가드(3번)의 hot table 목록과 baseline 범위.
- `isolated` 등급의 클론 단위: 테스트별(현행, 가장 안전) vs 모듈별(pytest-asyncio loop scope 조정 필요, 더 빠름).
