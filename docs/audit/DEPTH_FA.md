# DEPTH 감사 — FA done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2724, qa-2)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/FA-/`에 맞는 리프 전수(33건, 60건 이하이므로 전수 대조).
- 축 하한: 제목이 `FA-`로 시작하거나(또는 [health]/[ci] 태그 뒤에 명시적으로 "FA-0a 배치 N" 식으로 축 리프임을 밝히는 경우) FA(원장·펀드회계) 축 본체 구현/버그수정이면 ADR-2026-09-09-C 안전축(R, L4, LA/LB/LC, FA, CM, EO, DC) → **D3 하한**. 제목이 다른 축으로 시작하며 본문에 "FA-N"을 괄호 등으로 위임/언급만 하는 경우(2402 RD-2)나 FA 축 본체가 아닌 부수적 fixture/병합 정리(1989, 2099)는 **D2 하한**.
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명.
- 방법: 각 리프의 커밋(`git show <commit> --stat`)과 테스트 파일을 직접 열어 negative 테스트 개수·실패 주입·성능 단언·게이트 적색 재현·적대적/동시성/리플레이 증명 여부를 실측했다. 코드 수정 없음.

## 요약

- 전체 33건 중 **33건 전부 자기 축의 D 하한 미달**(meets_floor=false 33/33).
- 등급 분포: D0 9건 · D1 24건 · D2 0건 · D3 0건.
- D3 하한(FA 축 본체) 대상 30건 중 미달 30건. D2 하한(비축/부수) 대상 3건 중 미달 3건.
- 가장 흔한 단일 누락 항목: **수치 성능 단언 부재**(33/33 전부, 예외 없음). 다음으로 순수 도메인 리프(I/O 없음: 1701·1702·1708·2058)는 실패주입·게이트재현이 구조적으로 불가능해 D1이 상한이다. alembic head no-op 병합 3건(1814·1987·1988, 그리고 2099는 1988과 동일 커밋)은 테스트가 전혀 없는 완전 스텁(D0)이다.
- task.json commit 필드 정합성 결함 2건 발견(감사 범위 밖이지만 기록): task-2051은 commit 필드가 주석 영문 교정 커밋(9b12170b)을 가리켜 실질 FA-10 구현 diff를 이 커밋만으로 추적할 수 없고, task-2431은 commit 필드(6f9dd72d)가 순수 리팩터 커밋을 가리켜 실질 FA-2 결함 수정(TOCTOU negative 6건 포함)이 담긴 부모 커밋 7bb3ac62가 기록에서 누락되어 있다.
- task-2099는 CM 축 감사(DEPTH_CM.md)에서 이미 동일 커밋(a3096b99)이 D0로 판정되어 DEEPEN task-2860이 발행된 바 있다 — FA 축 명목으로도 규칙대로 별도 DEEPEN을 발행하되 중복 취지임을 명시한다.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (미달 근거) |
|---|---|---|---|---|---|---|---|
| 1701 | FA-1 entities contracts/v1 + domain/{hierarchy,defaults} (엔티티 계층 순수 도메인) | abeb361 | D3 | D1 | **NO** | test_contracts/defaults/hierarchy.py:25함수, negative=12(test_closed_at_rejects_naive_datetime, test_fund_rejects_unknown_currency, test_validate_new_fund_rejects_missing_parent 외 9); 순수 도메인, I/O 없음 | 실패주입/성능단언/게이트재현/D3증거 전무(순수 도메인, I/O 없음) — 수치 성능 단언 1개 추가 필요 |
| 1702 | FA-9 core/bitemporal.py (valid_time·transaction_time 4종 질의 순수 규칙) | e1fc8cf | D3 | D1 | **NO** | test_bitemporal.py:18함수, negative=9(test_check_no_overlap_rejects_overlapping_valid_and_tx, test_bitemporal_record_rejects_naive_datetime[x4] 외); 순수 함수 | 성능단언 없음, 실패주입/게이트재현/D3증거 없음(순수 함수) |
| 1703 | FA-13 core/eventstore contracts/v1 + append.py (해시체인·조건부 append) | 1a166a2 | D3 | D1 | **NO** | test_append.py:12함수, negative=4(test_append_rejects_reused_seq 등), 실패주입=有(DB UNIQUE 위반), 게이트재현=有, D3=有(test_concurrent_same_seq_appends_leave_exactly_one_winner, 20커넥션 동시경합) | 수치 성능 단언 없음(negative=4·실패주입·게이트재현·D3 동시성증거는 이미 충족) |
| 1704 | FA-2 마이그레이션 4테이블(entities) + adapters/postgres_repository.py + 통합 | 4b5cd18 | D3 | D1 | **NO** | test_postgres_entity_repository.py 등:19함수, negative=11(test_duplicate_primary_key_is_rejected_at_every_level 등), 실패주입=有(FK/PK/동시성충돌), 게이트재현=有 | 성능단언 없음, D3 증거(적대적/리플레이/다중워커) 없음 |
| 1708 | FA-7 allocation/domain/{policy,average_price}.py (3정책 배분·평균단가, 순수) | 2c0377e | D3 | D1 | **NO** | test_policy/average_price.py:17함수, negative=17(test_pro_rata_rejects_zero_total_quantity 등); 순수 함수 | 실패주입/게이트재현/성능단언/D3증거 전무(순수 함수) |
| 1747 | FA-2a legal_entity.tenant_id를 tenant(tenant_id)로 교정 | 09d9ed5 | D3 | D1 | **NO** | test_migration_static_no_users_fk.py+roundtrip:4함수, negative=0(재도입 방지 스캐너뿐), 게이트재현=有(test_no_migration_makes_legal_entity_tenant_id_reference_users) | negative test 0건(≥3 필요), 실패주입 없음, 성능단언 없음 |
| 1709 | FA-3 소급 마이그레이션 A: orders·fills에 fund_id/portfolio_id 컬럼+FK+백필 | 5a2ddf6 | D3 | D1 | **NO** | test_migration_fa3_orders_fills_columns.py:5함수, negative=1(test_negative_insert_with_nonexistent_fund_id_rejected_by_fk), 실패주입=有(FK위반), 게이트재현=有 | negative 1건뿐(≥3 필요), 성능단언 없음, D3증거 없음 |
| 1795 | FA-5 application/resolve_context.py + 주문·포지션·원장 쓰기 진입점 시그니처 확장 | 3fc5123 | D3 | D1 | **NO** | negative≥8(test_gate_none_is_fail_closed_type_error 등), 실패주입=有, 게이트재현=有(하드게이트), D3=有(test_submit_order_rejects_missing_entity_context_without_touching_adapter 우회시뮬, test_50_concurrent_same_intent_submits_produce_exactly_one_row 50동시워커) | 수치 성능 단언 없음(negative≥8·실패주입·게이트재현·D3 우회시뮬/50동시워커 증거는 이미 충족) |
| 1794 | FA-4 소급 마이그레이션 B: pos_account·pos_journal·pos_snapshot·ledger_journal_entry·ledger_posting | 9c9ff68 | D3 | D1 | **NO** | test_migration_fa4_columns.py:3함수, test_migration_fa4_worm_no_backfill.py:3함수, negative=2(FK위반, WORM 불변식 회귀), 실패주입=有, 게이트재현=有 | negative 2건뿐(≥3 필요), 성능단언 없음, D3증거(적대적/리플레이/동시경합) 없음 |
| 1906 | FA-2a 결함 수정(리뷰 task-1812 REJECT): legal_entity.tenant_id FK 거부 회귀 테스트 신설 | 2ea05de | D3 | D1 | **NO** | test_tenant_fk_enforced.py:4함수, negative=1(test_insert_with_nonexistent_tenant_id_raises_fk_violation), 실패주입=有, 게이트재현=有 | negative 1건뿐(≥3 필요), 성능단언 없음, D3 배선제거 증명이 커밋메시지 서술로만 존재하고 테스트 코드로 없음 |
| 1796 | FA-8 allocation/application/allocate_fills.py + 배분 마이그레이션 + 원장 연결 통합 | f158ebb | D3 | D0 | **NO** | 변경 파일은 allocate_fills.py 하나뿐(테스트 파일 0개, 모듈 독스트링 압축만, 313줄→299줄), 로직 변경 없음 | 테스트 전무(모듈 독스트링만 압축, 로직 0줄) — negative/실패주입/성능단언/게이트재현/D3증명 전부 부재 |
| 1925 | FA-5 결함 수정(리뷰 task-1912 REJECT): entity_context 실소유권 재검증 + orders.fund_id 단언 | 0765602 | D3 | D1 | **NO** | test_submit_order_entity_context_ownership.py:3함수+기존 다수, negative≥8, 실패주입=有, 게이트재현=有, D3=有(test_50_concurrent_same_intent_submits_produce_exactly_one_row, test_submit_order_existing_replay_returns_same_order_no_extra_row 리플레이) | 수치 성능 단언 없음(negative≥8·실패주입·게이트재현·D3 50동시워커/리플레이 증거는 이미 충족) |
| 1989 | [ci] CI 적색: ledger contracts/v1 스냅샷 fixture가 FA-8 신규 필드와 어긋남 | 8c11e66 | D2 | D0 | **NO** | 변경 파일은 ledger_contracts_v1.json fixture 하나(테스트 코드 0줄, JSON 데이터 26줄 추가) | 테스트 코드 0줄(fixture JSON만 갱신), FA-8 축 본체 재구현/보강 아님 — D2 하한 자체 미달 |
| 1814 | FA-0a tenant_id FK를 users에서 tenant로 전환 (배치 A) | f03d9ff | D3 | D0 | **NO** | alembic merge revision 1개, upgrade()/downgrade() 모두 pass(no-op), 테스트 파일 0개 | 완전 스텁(alembic no-op 병합) — negative/실패주입/성능단언/게이트재현/D3증명 전부 부재 |
| 1941 | FA-0b 소급 정정: 단일 포트폴리오 전제 제거 | 3b290ee | D3 | D0 | **NO** | postgres_policy_repository.py 신설(메서드 순수 이동)+postgres_repository.py에서 제거, 동작보존 검증 테스트 변경 0건 | 리팩터 커밋에 검증 테스트 0건(동작보존 증명 없음) — negative/실패주입/성능단언/게이트재현/D3증명 전부 부재 |
| 1987 | [health] FA-0a 배치 B: 원장·포지션·시장데이터 tenant_id FK를 users -> tenant | 117b40d5 | D3 | D0 | **NO** | alembic merge revision 1개, upgrade()/downgrade() 모두 pass(no-op, FA-0a 배치B+캘린더 head 병합), 테스트 파일 0개 | 완전 스텁(alembic no-op 병합) — negative/실패주입/성능단언/게이트재현/D3증명 전부 부재 |
| 2050 | FA-14 projections/{orders,positions,ledger}.py | 165dcdb0 | D3 | D1 | **NO** | test_projections.py:8함수(이 커밋 2함수 추가), negative=4(test_orders_projection_detects_dropped_event 등, 이벤트 1건 누락 시 재생값 divergence 유형) | negative=4는 있으나 실패주입/성능단언/게이트재현 전무(이벤트 누락 시뮬레이션만, 예외/DB오류 아님) |
| 2060 | FA-15 replay.py + scripts/replay_verify.py + 야간 CI 훅 | f2ff07d7 | D3 | D1 | **NO** | test_replay_verify.py:4함수, negative=1(test_replay_detects_ledger_balance_tampered_outside_the_event_trail), 게이트재현=有(replay_verify.py exit 0→1), D3=有(test_replay_is_deterministic_across_repeated_runs 리플레이 재현성) | negative 1건뿐(≥3 필요), 성능단언 없음(게이트재현·리플레이재현성은 이미 충족) |
| 2051 | FA-10 소급 마이그레이션 C: 투영 테이블 양시간축 + UPDATE 금지 | 9b12170b | D3 | D1 | **NO** | 이 커밋 자체는 주석/독스트링 영문 교정뿐(로직 변경 없음, ADR-2026-09-07-A). test_bitemporal_projections.py 최종 상태 6함수, negative=3(test_aios_app_cannot_update_{pos_snapshot,ledger_balance,positions}, 실 REVOKE+트리거 UPDATE 거부) | 이 task.json commit 필드(9b12170b)가 주석 교정 커밋이라 실질 FA-10 구현 증거를 이 커밋 자체 diff로 추적하기 어려움(레코드 정합성 문제); 최종 테스트 상태 기준으로도 성능단언 없음 |
| 2061 | FA-16 기존 쓰기 경로에 이벤트 append 삽입 + 적대적(무이벤트 상태 변경 탐지) | 5ea4fd49 | D3 | D1 | **NO** | tests/adversarial/eventstore/test_no_state_change_without_event.py 3파일 9함수, negative=6(test_oms_event_append_failure_blocks_order_state_change 등), 실패주입=有(monkeypatch RuntimeError) | 실패주입은 있으나 자동 재현 가능한 형태로 리포에 보존된 우회/게이트재현 테스트가 없음(커밋메시지는 수기 확인 서술뿐), 성능단언 없음 |
| 1988 | [health] FA-0a 배치 C: 리스크·포트폴리오·엔티티 tenant_id FK | a3096b99 | D3 | D0 | **NO** | alembic merge revision 1개(6877947783a6, 24줄), upgrade()/downgrade() 모두 pass(no-op, FA-0a 배치C+FA-10 head 병합), 테스트 파일 0개 | 완전 스텁(alembic no-op 병합) — negative/실패주입/성능단언/게이트재현/D3증명 전부 부재 |
| 2099 | P0 alembic head 병합 리비전 (FA-10·CM-4 언급) | a3096b99 | D2 | D0 | **NO** | 1988과 동일 커밋(a3096b99) — 순수 alembic head 병합, upgrade/downgrade 모두 pass, 테스트 파일 0개. CM 축 감사(DEPTH_CM.md)에서도 동일 커밋을 D0로 판정, DEEPEN task-2860 이미 발행됨 | 완전 스텁(alembic no-op 병합) — D2 하한 자체 미달, 테스트 0건. CM 축 DEEPEN(task-2860)과 중복이지만 FA 축 명목으로도 별도 기록 |
| 2058 | FA-11 ledger/domain/correction.py + positions/domain/restatement.py | aebf10d5 | D3 | D1 | **NO** | test_correction.py(8함수)+test_restatement.py(8함수), negative=8(test_reversal_lines_rejects_empty_input 등); 순수 도메인, I/O 없음 | 실패주입/게이트재현/성능단언/D3증거 전무(순수 도메인, I/O 없음) |
| 2059 | FA-12 application/{ibor_view,abor_snapshot}.py | 83868239 | D3 | D1 | **NO** | test_ibor_abor.py:6함수, negative=3(test_ibor_view_rejects_naive_cutoff, test_close_period_rejects_reclose_of_same_as_of_date 실DB UNIQUE위반, test_correction_pending_raises_when_period_never_closed), 실패주입=有, 게이트재현=有, D3=有(test_ibor_view_recomputes_deterministically_as_of_cutoff 리플레이재현성) | 수치 성능 단언 없음(negative=3·실패주입·게이트재현·D3 리플레이재현성 증거는 이미 충족) |
| 2122 | [ci] 리플레이 변조 감지 테스트가 FA-10 no-update 가드에 걸린다 | e1e44694 | D3 | D1 | **NO** | test_replay_verify.py:7함수, negative=3(test_replay_detects_ledger_balance_tampered_outside_the_event_trail 등), 실패주입=有(no-update 트리거 실위반), 게이트재현=有, D3=有(test_replay_is_deterministic_across_repeated_runs) | 수치 성능 단언 없음(negative=3·실패주입·게이트재현·D3 리플레이재현성/tamper우회 증거는 이미 충족) |
| 2126 | FA-15a 원장 테스트 픽스처를 분개 경유로 전환 | fe4613e0 | D3 | D1 | **NO** | test_check_audit_regressions.py:4함수, negative=1(test_flags_raw_balance_seed_injected_into_a_fixture), 실패주입=有(정적검사에 위반패턴 주입), 게이트재현=有(회귀 시 즉시 적색) | negative 1건뿐(≥3 필요), 성능단언 없음 |
| 2402 | RD-2 research_data contracts/v1 + domain/known_at.py (FA-9 bitemporal 위임) | 20e89594 | D2 | D1 | **NO** | test_contracts_v1.py+test_known_at.py, negative=5(test_research_item_naive_known_at_rejected 등), 실패주입=有(monkeypatch로 as_of 가짜구현 주입) | 게이트재현 없음(순수 unit test뿐, DB/CI 적색 재현 없음), 성능단언 없음 |
| 2406 | open_order_sweeper가 order_events 없이 주문 상태를 bulk UPDATE한다 — FA-16 무이벤트 상태변경 | 30ca7756 | D3 | D0 | **NO** | 변경 파일 1개(open_order_sweeper.py), diff 전체가 docstring/주석뿐, 테스트 파일 0개, 로직 변경 0. 커밋 메시지가 'DoD(e) 미해결, task-2432로 위임'이라 명시 | 이 커밋은 docstring/주석만 수정(로직·테스트 변경 0) — 실질 결함이 미해결로 후속 task-2432에 위임됨, negative/실패주입/성능단언/게이트재현/D3증명 전부 부재 |
| 2431 | FA-2 결함 수정(리뷰 task-2313 REJECT): 상위 폐쇄 TOCTOU 원자화 | 6f9dd72d | D3 | D0 | **NO** | task.json commit 필드(6f9dd72d)는 postgres_repository.py를 4개 믹스인으로 쪼갠 순수 코드이동 커밋(테스트 파일 0개). 실질 FA-2 결함 수정은 부모 커밋 7bb3ac62(negative=6: 교차테넌트 빈리스트 3+TOCTOU 재현 3, 실패주입=TOCTOU 경합 재현, 게이트재현=HierarchyViolationError 실DB)에 있음 | task.json commit 필드가 실질 수정 커밋(7bb3ac62)이 아니라 뒤이은 순수 리팩터 커밋(6f9dd72d)을 가리킴(레코드 불일치) — commit 필드를 7bb3ac62로 정정하고, 그 커밋 기준으로도 수치 성능 단언 없음 |
| 1942 | FA-0c 키 문법 정정 A: ledger_account 계층 | 4c925cf4 | D3 | D1 | **NO** | test_chart_of_accounts.py+test_fa0c_account_scope.py, negative=3(test_default_scope_rejects_unparseable_account_code 등), 실패주입=有(실 UniqueViolationError), 게이트재현=有 | negative 3건은 충족하나 D3 증거(적대적/리플레이/동시성) 없음, 성능단언 없음 |
| 1943 | FA-0d 키 문법 정정 B: pos_snapshot.position_key 중앙 생성자 | 278f6227 | D3 | D1 | **NO** | test_check_position_key_central.py(7함수,negative=4)+test_migration_fa0d(negative=1: test_backfill_fails_closed_and_rolls_back_whole_migration_when_portfolio_id_null), 실패주입=有(alembic subprocess 실패), 게이트재현=有(하드게이트) | D3 증거(적대적/리플레이/동시성) 없음, 성능단언 없음 |
| 1944 | FA-6 리스크·성과·API 응답에 펀드/포트폴리오 스코프 반영 | df84bd55 | D3 | D1 | **NO** | negative≥8(test_get_statement_rejects_other_portfolio_id_fail_closed 등), 무변경 회귀 2건 | negative≥8로 풍부하나 실패주입/게이트재현/D3증거/성능단언 전부 없음(전부 정상 입력검증 거부일 뿐 예외/DB오류 시뮬레이션 아님) |
| 2543 | [ci] FA-0d 마이그레이션 fail-closed가 CI DB 준비를 깨뜨린다 | 46f9d027 | D3 | D1 | **NO** | negative=3(test_cross_tenant_position_key_rejected 등), 실패주입=有(실 Postgres ObjectInUseError 근본원인 수정), 게이트재현=有([health:ci_red] 재현), D3=有(test_twenty_concurrent_fills_produce_gapless_unique_sequence 20동시워커) | 수치 성능 단언 없음(나머지 전부 충족했음에도 이 한 항목 때문에 D2/D3 미성립) |
## 발행된 DEEPEN task

D3(FA-)/D2(비축) 미달 33건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 1, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 1701 | 3002 |
| 1702 | 3003 |
| 1703 | 3004 |
| 1704 | 3005 |
| 1708 | 3006 |
| 1747 | 3007 |
| 1709 | 3008 |
| 1795 | 3009 |
| 1794 | 3010 |
| 1906 | 3011 |
| 1796 | 3012 |
| 1925 | 3013 |
| 1989 | 3014 |
| 1814 | 3015 |
| 1941 | 3016 |
| 1987 | 3017 |
| 2050 | 3018 |
| 2060 | 3019 |
| 2051 | 3020 |
| 2061 | 3021 |
| 1988 | 3022 |
| 2099 | 3023 |
| 2058 | 3024 |
| 2059 | 3025 |
| 2122 | 3026 |
| 2126 | 3027 |
| 2402 | 3028 |
| 2406 | 3029 |
| 2431 | 3030 |
| 1942 | 3031 |
| 1943 | 3032 |
| 1944 | 3033 |
| 2543 | 3034 |
