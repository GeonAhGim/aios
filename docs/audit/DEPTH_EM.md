# DEPTH 감사 — EM(execution/algo/TCA) done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2731, qa-2)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/EM-/`로 **시작하는** 리프 전수 15건(60건 상한 이내이므로 전수 대조). 제목 중간에 `EM-12`가 괄호로 언급될 뿐 실제로는 `CM-14`로 시작하는 task-2526(reporting/domain/best_execution.py)은 이미 `docs/audit/DEPTH_CM.md`에서 CM 축으로 감사·DEEPEN(task-2867) 발행이 끝난 리프라 이번 EM 전수 대조에서 제외했다(중복 발행 방지).
- 축 하한: ADR-2026-09-09-C의 안전축 목록(R, L4, LA/LB/LC, FA, CM, EO, DC)에 **EM은 포함되지 않는다** — Decision 4의 우선순위 문단(`R-47, L4, CM, EM, PLT`는 발행 우선순위 2를 매기는 목록일 뿐, D3 하한 목록과는 별개)과 혼동하지 않도록 명시한다. 따라서 EM 15건 전부 **D2가 하한**이다.
- 판정 기준(ADR-2026-09-09-C, 엄격 적용): D0=스텁, D1=happy path+단위 테스트, D2=negative≥3·실패 주입 1·성능 단언 1(p95/p99/처리량)·게이트 적색 재현 1을 **전부** 충족, D3=D2 + 적대적/리플레이/다중 인스턴스 증명 중 최소 1. 4개 요건 중 하나라도 비면 D2로 인정하지 않고 D1에 머문다.
- 방법: 4개 병렬 감사 에이전트(worktree 내 read-only)가 각 리프의 커밋(`git show <commit> --stat`/diff)과 **현재** 테스트 파일을 직접 열어 negative 테스트 이름·실패 주입·성능 단언·게이트 적색 재현·적대적/동시성 증거를 실측했다. `git log -- <test 파일>`로 원 커밋 이후 테스트가 추가 확장됐는지도 확인했다(확장 없음, 원 커밋 상태 그대로). 코드 수정 없음.

## 요약

- 전체 15건 **전부 D1**, D2 하한 미달(floor met=NO 15/15). D0 0건 · D1 15건 · D2 0건 · D3 0건.
- 가장 흔한 단일 누락 항목(15/15 공통): **수치 성능 단언 부재**(p95/p99/처리량 어디에도 없음)와 **게이트 적색 재현 부재**(어떤 테스트도 실제 CI 적색→수정 사고의 task/incident id를 인용하지 않음) — 두 항목이 동률 1위.
- negative 테스트 자체는 대부분 탄탄하다(EM-19/EM-1/EM-4/EM-9/EM-2/EM-7/EM-8/EM-10/EM-12/EM-11/EM-13/EM-3 모두 ≥3). 유일한 예외는 **EM-6(task-2120)** — negative 1건(`test_route_order_rejects_empty_candidates`)뿐으로 D2 요건(≥3) 자체도 미달.
- EM 축 대다수(11/15)는 설계상 순수 도메인 함수(I/O 없음)라 failure-injection이 구조적으로 성립하지 않는다(EM-1/2/4/5/7/8/9/10/11/12/13) — 각 리프의 docstring·모듈 임포트 정적 검사(예: `test_decomposition_module_imports_no_io_or_db`, `test_guard_module_never_reads_the_wall_clock`)가 이를 스스로 증명한다. 반면 **EM-6(route_order.py, DB 저장)**과 **EM-3(orders 컬럼, postgres 통합)**은 실제 I/O를 갖고 있음에도 실패 주입 테스트가 없다(EM-3은 `AsyncMock`으로 정상 응답만 시뮬레이션, DB 오류·연결 끊김 미시뮬레이션). **EM-17(fix_session.py 포트)**도 `FakeFixSession` 상태기계 예외뿐 실제 전송계층 결함(연결 끊김·하트비트 타임아웃·거부 메시지) 주입이 없다.
- D3급으로 근접한 동시성 증명이 이미 존재하는 리프 2건: **EM-19(task-1751)**의 `test_oco_group_atomic_under_1000_adversarial_concurrent_triggers`(1000-스레드 동시 트리거, 정확히 1승자 증명)와 **EM-6(task-2120)**의 `test_route_order_concurrent_calls_are_idempotent`(20-way `asyncio.gather` 멱등 증명) — 둘 다 D2의 나머지 요건(성능 단언·게이트 적색 재현, EM-6은 negative 개수도)이 비어 있어 D1에 머물렀다.

## 전수 판정표

| id | title | commit | axis floor | grade | floor met | evidence | missing |
|---|---|---|---|---|---|---|---|
| 1751 | EM-19 스톱·스톱리밋·OCO·트레일링 주문 유형 (T1 기본 주문 유형) | ea71aa1 | D2 | D1 | **NO** | test_rejects_non_positive_trigger_price; test_buy_stop_limit_rejects_limit_below_trigger; test_sell_stop_limit_rejects_limit_above_trigger; test_rejects_non_positive_trailing_offset; test_oco_group_atomic_under_1000_adversarial_concurrent_triggers(1000-스레드 동시 트리거, 정확히 1승자=D3급 동시성 증명) | failure-injection 없음(순수 트리거 판정 로직, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음(task-1751은 기능 티켓 인용일 뿐 사고 재현 아님) |
| 2067 | EM-1 ems contracts/v1.py (ParentOrder·ChildOrder·AlgoSpec·RouteDecision·TcaResult) + 스냅샷 | 3ed98daf | D2 | D1 | **NO** | test_algo_spec_rejects_kind_outside_table; test_algo_spec_rejects_naive_datetime; test_algo_spec_rejects_end_before_or_equal_start; test_route_decision_requires_reason_codes; test_error_taxonomy_http_status_snapshot(스키마 스냅샷) | failure-injection 없음(순수 pydantic 검증, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2070 | EM-2 ems domain/parent_child.py (자식 체결 → 부모 상태 집계, 취소·정정 전파, 초과 슬라이스 거부) | eaf66902 | D2 | D1 | **NO** | test_slice_one_unit_over_remaining_capacity_is_rejected(EM-A1 경계 +1 거부); test_terminal_parent_rejects_new_child[FILLED/CANCELLED/REJECTED](3-파라미터); test_aggregate_partial_fills_sum_to_partially_filled; test_cancellation_targets_only_open_children | failure-injection 없음(순수 집계 로직, 커밋 메시지 "Pure, no I/O"); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2106 | EM-4 ems domain/route/{fee_model,liquidity_model}.py (수수료 티어·유동성 깊이 점수, 순수) | 9c616648 | D2 | D1 | **NO** | test_empty_tier_schedule_is_rejected; test_unsorted_tier_schedule_is_rejected; test_duplicate_threshold_is_rejected; test_volume_exactly_at_threshold_selects_upper_tier(경계); test_asks_out_of_order_are_rejected_for_buy; test_bids_out_of_order_are_rejected_for_sell | failure-injection 없음(순수 함수, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2119 | EM-5 ems domain/route/venue_scoring.py (결정론 점수·타이브레이크, 순수) | f58ee605 | D2 | D1 | **NO** | test_negative_fee_bps_is_rejected; test_nan_fee_bps_is_rejected; test_liquidity_score_out_of_range_is_rejected; test_duplicate_venue_codes_are_rejected; test_ranking_is_invariant_under_random_input_shuffling(10회 랜덤 셔플, 결정론 증명) | failure-injection 없음(순수 함수, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2120 | EM-6 ems application/route_order.py + route_decisions 저장 + 통합(근거 저장·단일 벤처 폴백) | 3c5c65fe | D2 | D1 | **NO** | test_route_order_rejects_empty_candidates(NoRouteAvailableError, 유일한 negative); test_route_order_concurrent_calls_are_idempotent(20-way asyncio.gather, 1행/1 decision_id만 생성=D3급 동시성 증명); test_route_order_persists_reproducible_decision(저장된 스냅샷을 EM-5 rank_venues로 재계산해 일치 확인) | **negative 2건 더 필요(현재 1, D2 최소 요건 자체 미달)**; failure-injection 없음(DB 저장 경로가 있음에도 시뮬레이션된 DB 오류/커넥션 실패 테스트 없음 — 구조적으로 가능한데 부재); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2457 | EM-7 ems domain/algo/guard.py — 알고리즘 제약(최대 참여율·슬라이스 간격·잔여 마감) 순수 규칙 | 486dadb9 | D2 | D1 | **NO** | test_participation_one_unit_over_the_cap_is_rejected; test_zero_market_volume_is_rejected_without_raising_zero_division; test_slice_interval_one_second_short_is_rejected; test_naive_datetime_is_rejected; test_no_participation_cap_input_is_ever_read_as_unlimited(None/-5/101, 3-파라미터 적대적); test_guard_module_imports_no_nondeterministic_source(정적 순수성 검증) | failure-injection 없음(순수 규칙, I/O 없음, 정적으로 증명됨); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2461 | EM-8 ems domain/algo/twap.py — 기존 oms algo_slicer를 EMS 포트로 승격(재작성 금지) | 1968707d | D2 | D1 | **NO** | test_terminal_parent_is_rejected(전 종결상태 파라미터화); test_non_twap_kind_is_rejected; test_short_volume_profile_is_rejected/test_long_volume_profile_is_rejected; test_twap_module_calls_all_three_guard_functions(volume_profile=None 강제로 guard.py 위임 실증); test_same_inputs_produce_an_identical_plan(결정론) | failure-injection 없음(docstring "No DB -- pure function tests only"); 수치 성능 단언 없음; 게이트 적색 재현 없음(task/incident id 인용 없음, guard 위임 검증이 근접하지만 사고 재현은 아님) |
| 2499 | EM-10 ems domain/algo/pov.py — 실시간 참여율 추종(EM-7 guard 재사용, 상한 초과 0건) | 0688738d | D2 | D1 | **NO** | test_short_volume_profile_is_rejected/test_long_volume_profile_is_rejected; test_terminal_parent_is_rejected(전 종결상태 파라미터화); test_non_pov_kind_is_rejected; test_planned_qty_never_exceeds_the_participation_cap_of_market_volume(상한 불변식) | failure-injection 없음(순수 함수, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2500 | EM-12 ems domain/tca/benchmarks.py — 도착가·VWAP·종가 벤치마크(빈 체결 fail-closed) | f3569d93214f | D2 | D1 | **NO** | test_empty_fills_is_rejected_not_reported_as_zero_cost(제목의 fail-closed 핵심 주장 실증); test_non_positive_qty_is_rejected/test_non_positive_price_is_rejected(파라미터화); test_one_bad_fill_among_good_fills_still_rejects_the_whole_batch; test_close_price_rejects_empty_bars; test_benchmarks_module_reads_no_wall_clock(정적 순수성) | failure-injection 없음(순수 Decimal 연산, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2502 | EM-11 ems domain/algo/is_shortfall.py — 긴급도-비용 트레이드오프(단조성·합 보존) | 5831b823 | D2 | D1 | **NO** | test_non_is_kind_is_rejected; test_terminal_parent_is_rejected(파라미터화); test_short_volume_profile_is_rejected/test_long_volume_profile_is_rejected; test_first_slice_quantity_is_monotone_nondecreasing_in_urgency(단조성 증명); test_planned_qty_sums_exactly_to_parent_qty_for_every_urgency(합 보존, 파라미터화) | failure-injection 없음(순수 함수, guard.py 위임뿐 I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2511 | EM-13 ems domain/tca/decomposition.py — 비용 분해(분해 합 == 총비용) | bac4e446 | D2 | D1 | **NO** | test_empty_fills_is_rejected_not_reported_as_zero_cost(fail-closed); test_non_positive_qty_is_rejected/test_non_positive_price_is_rejected(파라미터화); test_one_bad_fill_among_good_fills_still_rejects_the_whole_batch; test_components_sum_to_total_cost_exactly; test_decomposition_module_imports_no_io_or_db(정적 순수성, asyncpg/sqlalchemy/adapters/psycopg 임포트 금지 검증) | failure-injection 없음(모듈 자체가 무-I/O임을 스스로 증명); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2501 | EM-9 ems domain/algo/vwap.py — 거래량 프로파일 가중(얕은 프로파일은 TWAP 강등·사유 기록) | c43e2c82 | D2 | D1 | **NO** | test_terminal_parent_is_rejected(파라미터화); test_short_volume_profile_is_rejected/test_long_volume_profile_is_rejected; test_fully_empty_profile_fails_closed_on_the_participation_check; test_sparse_profile_below_coverage_floor_is_demoted_to_equal_weight + test_demoted_participation_check_uses_average_volume_not_the_untrusted_shape(강등 경로·demotion_reason 실증) | failure-injection 없음(순수 함수, 입력검증 ValueError뿐 시뮬레이션된 어댑터 예외 아님); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2533 | EM-17 ems/ports/fix_session.py — FIX 세션 포트 + 재사용 가능한 계약 테스트(어댑터 없이 포트만) | 46f49303 | D2 | D1 | **NO** | test_requires_logon(로그아웃 상태·로그아웃 후 재시도 모두 RuntimeError); test_rejects_duplicate(중복 cl_ord_id, reset_sequence 후에도 기억); test_partial_implementation_is_rejected(Protocol 구조적 적합성, send_order 누락 시 isinstance 실패) | failure-injection 없음(FakeFixSession은 자체 상태기계 검증일 뿐 실제 전송계층 결함(연결 끊김·하트비트 타임아웃·거부 메시지) 미주입); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2121 | EM-3 orders.parent_order_id/algo_run_id 정본화 — 집계 컬럼 보강 + 자식 주문 submit_order 경유 정적 증명(EM-A2) | f553385a | D2 | D1 | **NO** | test_reserve_child_slice_rejects_over_commit(+실DB행 버전); test_reserve_child_slice_rejects_terminal_parent(+실DB행 버전); test_release_more_than_reserved_is_rejected; test_checker_catches_a_bypass_insert_outside_submit_order(정적 우회탐지 실증); test_repo_has_no_bypass_today(회귀 고정) | failure-injection 없음(AsyncMock/실DB 모두 정상 응답만 시뮬레이션, DB 오류·연결 실패 미주입); 수치 성능 단언 없음; 게이트 적색 재현 없음(task-2121 인용은 자기 스펙 참조일 뿐 선행 CI 적색 사고 재현이 아님) |

## 발행된 DEEPEN task

D2 하한 미달 15건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 2, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 1751 | 3112 |
| 2067 | 3113 |
| 2070 | 3114 |
| 2106 | 3115 |
| 2119 | 3116 |
| 2120 | 3117 |
| 2457 | 3118 |
| 2461 | 3119 |
| 2499 | 3120 |
| 2500 | 3121 |
| 2502 | 3122 |
| 2511 | 3123 |
| 2501 | 3124 |
| 2533 | 3125 |
| 2121 | 3126 |
