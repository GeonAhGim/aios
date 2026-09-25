# DEPTH 감사 — R/EO done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2721, qa-3)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/\bR-\d+|\bEO-\d+/`에 맞는 리프 전수 (53건, 60건 이하이므로 전수 대조)
- 축 하한: R(리스크)·EO(실행 소유권)는 ADR-2026-09-09-C 안전축(R, L4, LA/LB/LC, FA, CM, EO, DC) 목록에 모두 포함 → 53건 전부 **D3 하한**.
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명 중 최소 1.
- 방법: 병렬 감사 에이전트(worktree 내 read-only, 일부는 하위 에이전트로 추가 분할)가 각 리프의 커밋(`git show <commit> --stat`/diff)과 현재 테스트 파일을 직접 열어 negative 테스트 이름·실패 주입·성능 단언·게이트 적색 재현·적대적/리플레이/동시성 증거를 실측했다. 코드 수정 없음.

## 요약

- 전체 53건 중 **35건이 D3 하한 미달**, 18건이 D3 충족.
- 등급 분포: D0 1건 · D1 17건 · D2 17건 · D3 18건.
- 가장 흔한 단일 누락 항목: **성능/latency 수치 단언 부재**(D3 미달 리프 대부분). 다음으로 적대적/다중 인스턴스 증명 부재, negative<3, 실패 주입 부재 순. 순수 판정 함수(rules/*.py)들은 negative 커버리지는 두터운 편이나 구조적으로 동시성·적대적 시나리오가 성립하지 않아 D2에 머문다.
- **중대 배선 결함 2건 발견(단순 깊이 미달이 아니라 실기능 결함)**:
  - task-2133(R-49): `src/watchdog_process.py:192`가 `decide()`를 호출할 때 `market_wide_correlated=None` 고정·`failure_domain` 미전달 — 이 리프가 구현한 시장급변 감지·LIQUIDATE→HALT 강등 로직이 실제 실행 경로에서 전혀 도달 불가. task-1806(P0-B)이 고친 것과 동일 클래스의 '옵션 인자 항상 None' 결함이 한 계층 위에서 재발.
  - task-2358(R-52): 명세가 명시한 `SELECT...FOR UPDATE SKIP LOCKED` 동시 워커 중복실행 방지가 실제 2-워커 동시경합 테스트로 전혀 증명되지 않음 — 강제청산 이중실행 가능성이 미검증 상태.
- task-1521(R-57)은 명세 DoD가 요구하는 'p99≤50ms 단언'을 실제로 구현하지 않았다(퍼센타일은 출력만 하고 CI 게이트는 DB 왕복횟수로 대체 — 문서화된 flakiness 회피 결정). 이 성능 리프 자체가 D1에 머무는 것이 여러 하위 리프(1361/1316/1210 등)의 '성능 단언'을 실질적으로 무력화한다.
- task-1568(R-58)은 문서 재검증 커밋(테스트 0건)이라 D0 — 다만 실드리프트(RTF-04 2→3 지점)를 찾아낸 것은 유효한 QA 활동이다.

## 전수 판정표

| id | title | commit | depth | negative | evidence(발췌) | 부족한 증빙 |
|---|---|---|---|---|---|---|
| 103 | R-48 DataDistrust 풀스코프(참조시세 2소스·DEGRADED 규칙·게이트) | f05b2bf | D2 | 6 | tests/unit/core/test_data_distrust_monitor.py:test_no_reference_sources_with_normal_price_returns_degraded_single_source; tests/unit/core/test_data_distrust_monitor.py:test_majority_deviation_triggers_distrust | 참조시세 소실 시나리오·DISTRUSTED/SUSPICIOUS 차단 실DB 재현은 있으나 성능 단언·적대적 테스트·다중 인스턴스 증명 없음 — D3 미달. |
| 1115 | EO-06 I-01 정적 검사: 안전 게이트 파라미터 Optional/None 기본값 0건 CI 단언 | 1f08638 | D1 | 4 | tests/unit/test_gate_params_required.py:test_regression_flags_p0b_bypass_shape_before_fix; tests/unit/test_gate_params_required.py:test_regression_clears_after_p0b_required_gate_fix | AST 스캐너 자체 negative는 있고 실제 P0-B 전후 회귀쌍은 게이트 적색 재현이지만, 실패주입(DB/timeout)·성능단언·동시성/적대적 증거 전무 — D2 문턱도 못 넘음. |
| 1116 | EO-01 실행 소유권 순수 도메인(ExecutionLease + is_lease_available) 단위  | 73fea47 | D2 | 4 | tests/foundation/unit/execution_ownership/test_rules.py:test_is_lease_available_rejects_naive_now; tests/foundation/unit/execution_ownership/test_rules.py:test_execution_lease_rejects_naive_heartbeat_at | naive datetime 3종 거부 + 경계값 테스트는 탄탄하나 순수 함수 자체엔 성능단언·동시성 증명 없음(그 증명은 EO-02/04가 담당) — 이 리프 자체 근거로는 D3 미달. |
| 1117 | EO-02 execution_leases 마이그레이션 + repository/adapter + 동시획득 통합 | 60daf54 | D3 | 3 | tests/integration/foundation/execution_ownership/test_postgres_lease_repository.py:test_concurrent_acquire_exactly_one_winner; tests/integration/foundation/execution_ownership/test_postgres_lease_repository.py:test_unknown_execution_id_raises_fk_violation_for_whole_batch | D3. 실DB 2-owner asyncio.gather 경합 증명 + FK 실패주입 + 단일왕복 성능. |
| 1118 | EO-03 ExecutionLoopScheduler 시그니처 필수화 + list_candidates() 재작 | 5165b8a | D3 | 6 | tests/adversarial/execution_ownership/test_no_double_tick.py:test_two_schedulers_ticking_same_execution_place_order_exactly_once; tests/integration/test_execution_scheduler.py:test_one_failing_execution_does_not_block_the_others | D3. EO-04 커밋의 적대적 2-스케줄러 경합 테스트가 이 리프의 scheduler.py/list_candidates()를 직접 행사. 성능단언만 없음. |
| 1119 | EO-04 background_loops.py 조립부 주입 + stop() release_all 연결 + 적 | 5857fc9 | D3 | 0 | tests/adversarial/execution_ownership/test_no_double_tick.py:test_two_schedulers_ticking_same_execution_place_order_exactly_once; tests/adversarial/execution_ownership/test_no_double_tick.py:test_background_loops_stop_releases_lease_for_immediate_reacquisition | D3(교과서적 증거: 2 스케줄러 인스턴스 실DB 경합 + 정확히 1회 주문). 다만 이 커밋 자체는 별도 negative/실패주입/성능 테스트를 추가하지 않음. |
| 1120 | EO-05 execution_deps/ExecutionService 동등 게이트 배선 + kill switc | cbb8b9c | D3 | 3 | tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py:test_active_kill_switch_blocks_execution_loop_new_order_submission; tests/integration/test_execution_service_risk_gate.py:test_active_kill_switch_denies_unmandated_start | D3. 실제 게이트 배선 경로로 kill switch가 0건 주문을 증명하는 적대적 테스트. 성능단언 없음. |
| 1151 | R-04 risk rules/base.py (Rule Protocol·missing() fail-closed | 8a11adc | D1 | 2 | tests/unit/core/risk/test_rules_base.py:test_missing_denies_and_fills_missing_fields; tests/unit/core/risk/test_rules_base.py:test_rule_error_denies_with_risk_rule_error_reason_code | negative 2건뿐(<3), 성능단언·동시성·적대적 증거 없음 — D2 문턱 미달. |
| 1152 | R-15 risk policy_bundle.py (RiskRuleBundle 모델·상태·compute_rul | 9a1737a | D2 | 6 | tests/unit/core/risk/test_policy_bundle.py:test_skipping_or_reversing_states_is_rejected; tests/unit/core/risk/test_policy_bundle.py:test_invalid_rule_hash_length_rejected | 6개 fail-closed negative는 탄탄하나 성능단언·적대적/다중 인스턴스 증명 없음 — D3 미달. |
| 1176 | R-05~R-09 risk rules 5종(daily_loss·max_drawdown·leverage·con | e8ae0c7 | D2 | 13 | tests/unit/core/risk/test_daily_loss.py:test_missing_daily_pnl_denies; tests/unit/core/risk/test_concentration.py:test_missing_total_equity_denies | 5개 파일 전부 경계값+누락필드 DENY로 13 negative, 실질적 게이트 DENY 재현이나 성능단언·적대적/동시성 없음 — D3 미달. |
| 1177 | R-10~R-13 risk rules 4종(var_es·correlation·trade_frequency·s | 900704b | D2 | 22 | tests/unit/core/risk/test_safety_state.py:test_denies_on_active_control_scope; tests/unit/core/risk/test_correlation.py:test_denies_unconditionally_when_missing_pairs_present_even_if_metrics_look_safe | 배치 중 최다 negative(22)지만 순수함수라 성능단언·적대적/동시성 증거 전무 — D3 미달. |
| 1186 | R-14 risk limits check_exposure_limits (scope 매칭·hard/soft,  | c2c7be0 | D2 | 14 | tests/unit/core/risk/test_limits.py:test_hard_breach_denies; tests/unit/core/risk/test_limits.py:test_missing_gross_leverage_denies | scope/경계 커버리지 강함, DENY 게이트 재현 있으나 성능·동시성·적대적 증거 0 — D3 미달. |
| 1193 | R-16 risk evaluator.py (규칙 순서 고정·DENY 단락·outcome 합성·latency_ | 1658ddc | D3 | 5 | tests/unit/core/risk/test_evaluator.py:test_latency_us_is_positive; tests/unit/core/risk/test_evaluator.py:test_decision_id_is_deterministic_for_same_inputs | D3. latency_us>0 성능단언 + rule 예외 실패주입 + 결정론적 decision_id 재현(replay) 모두 충족. |
| 1194 | R-21 risk_policy.yaml §3.3 확장 + verify_policy_against_bundle | c29b779 | D2 | 12 | tests/unit/core/loader/test_risk_policy_loader.py:test_verify_policy_against_bundle_raises_on_hash_mismatch; tests/unit/core/loader/test_risk_policy_loader.py:test_unknown_top_level_key_is_rejected | I6 해시불일치 게이트 재현 + 12개 검증오류 negative, 성능단언·동시성·적대적 없음 — D3 미달. |
| 1196 | R-22 마이그레이션 risk_rule_bundle + postgres_bundle_repository (p | 7e50436 | D2 | 5 | tests/integration/risk/test_rule_bundle_activation.py:test_concurrent_activate_of_two_bundles_same_scope_only_one_succeeds; tests/integration/risk/test_rule_bundle_activation.py:test_worm_trigger_blocks_table_owner_too | 실DB 2-트랜잭션 동시활성화 경합(D3급 동시성 증명) + WORM 테이블오너 우회시도 실패주입까지 있으나 성능/latency 단언이 전무해 D2 체크리스트 자체가 미완성 — D3 불인정. |
| 1209 | R-17 engine.py facade 축약(215→≤120줄) + models.decision_id 옵션  | 35ec47a | D1 | 19 | tests/unit/core/test_risk_engine.py:test_daily_loss_rejects_and_short_circuits; tests/unit/core/test_risk_engine.py:test_safety_state_checked_last_even_if_it_would_reject | 커밋 자체는 신규 테스트 0줄 — 기존 test_risk_engine.py(legacy check() 경로)만 재사용. 신규 공개계약 check_decision()·decision_id 자체를 검증하는 테스트가 전무 — D2 문턱 미달. |
| 1210 | R-24 마이그레이션 risk_decision WORM + postgres_decision_repositor | 17527b7 | D3 | 7 | tests/adversarial/risk/test_worm_tables.py:test_aios_app_cannot_update_risk_decision; tests/adversarial/risk/test_worm_tables.py:test_worm_trigger_blocks_table_owner_update | D3. 테이블오너까지 포함한 UPDATE/DELETE 우회 시도 적대적 테스트 + 교차테넌트 격리 + replay 위변조탐지 + p50/p95/p99 성능측정. |
| 1219 | R-28 execution_loop/candle_history.py (심볼별 일봉 lookback 캐시, T | 561868e | D1 | 2 | tests/unit/services/test_candle_history.py:test_get_returns_none_on_adapter_failure_without_prior_cache; tests/unit/services/test_candle_history.py:test_get_returns_none_instead_of_stale_cache_when_refetch_fails | TTL 경계+실패주입 2건은 있으나 negative<3, 성능·동시성·적대적 없음 — D2 미달. |
| 1220 | R-30 equity_tracker UTC 일경계 + save_equity_baseline 단조 조건부 UP | d304b26 | D1 | 1 | tests/integration/services/test_equity_tracker.py:test_save_equity_baseline_peak_never_regresses; tests/integration/services/test_equity_tracker.py:test_save_equity_baseline_missing_execution_fails_closed | negative 1건뿐, 실동시경합(다중 트랜잭션) 테스트 없이 순차 쓰기만 검증, UTC 경계도 로컬시간 분기 실증 없음 — D2 미달. |
| 1221 | R-33 risk_gate domain/fence.py + application/read_fence.py + | 99505ac(원커밋 bfd0a4c5) | D3 | 5 | tests/adversarial/risk/test_fence_race.py:test_staged_gather_kill_switch_vs_submits_post_fence_zero; tests/adversarial/risk/test_fence_race.py:test_unstaged_gather_every_post_fence_effect_is_detected_and_reversed | D3. kill-switch 활성화 vs N개 동시제출 asyncio.gather 경합 + post-fence 0건·역전 증명, 단일왕복 성능단언. |
| 1222 | R-38 services/safety/legacy_execution_pauser.py (5범위 매핑, 행별  | 4293ec1 | D1 | 3 | tests/unit/services/test_legacy_execution_pauser.py:test_unmapped_scope_raises_instead_of_silently_matching_zero_rows; tests/unit/services/test_legacy_execution_pauser.py:test_tenant_scope_rejects_non_uuid_scope_ref | 실DB 격리·검증 테스트는 있으나 성능단언·게이트 적색 재현·동시성/kill-switch 경합 테스트 없음(kill switch는 본질적으로 동시성 민감) — D2 미달. |
| 1315 | R-23 application/activate_rule_bundle.py + 라우터(/foundation/r | de3a7ca | D3 | 11 | tests/integration/risk/test_rule_bundle_activation.py:test_approve_rule_bundle_rejects_self_approval; tests/integration/risk/test_rule_bundle_activation.py:test_worm_trigger_blocks_table_owner_too | D3. 4-eyes 자가승인 거부(게이트 재현) + WORM 테이블오너 우회시도(적대적) + 2-인스턴스 동시활성화 경합(파샬유니크 인덱스로 강제). 성능단언만 없음. |
| 1316 | R-25 services/risk_decision_recorder.py (WORM insert + audit | b54dfee | D2 | 3 | tests/integration/risk/test_risk_decision_recorder.py:test_clock_skew_beyond_tolerance_forces_deny_and_logs; tests/performance/test_pre_trade_latency.py:test_pre_trade_phase_p99_measured_and_round_trips_exact | negative 3·실패주입(UniqueViolation)·성능단언·게이트 DENY 재현까지 D2 완비되나 이 recorder 자체를 표적으로 한 적대적/replay/동시성 테스트 없음(다른 리프의 셋업으로만 재사용됨) — D3 미달. |
| 1317 | R-26 마이그레이션 c7e6a3b2d4f5_risk_limit + postgres_limit_reposit | ab501dd | D3 | 9 | tests/integration/risk/test_risk_limits_db.py:test_upsert_with_stale_expected_updated_at_raises_and_leaves_row_unchanged; tests/adversarial/risk/test_cross_tenant_limits.py:test_rsk_i8_risk_officer_of_a_cannot_plant_limit_in_b | D3. 낙관적잠금 실패주입 + 교차테넌트 한도 위조 시도 적대적 테스트. 성능단언만 없음. |
| 1318 | R-29 var_estimator 교체 + correlation_service.py 신규 + correlat | e8d4160 | D1 | 6 | tests/unit/services/test_correlation_service.py:test_missing_history_for_other_symbol_returns_none_not_zero; tests/unit/services/test_var_estimator.py:test_insufficient_bars_returns_none_not_zero | fail-closed(None이 아닌 0으로 위장 방지) 6건은 강하나 None 결과가 실제 게이트 DENY로 이어짐을 증명하는 체인 테스트가 없어 게이트 적색 재현 기준 미충족, 성능·적대적 없음 — D2 미달. |
| 1330 | R-42 core/safety/data_freshness.py 신규 + InstrumentedAdapter  | a0652c9 | D1 | 3 | tests/unit/core/safety/test_data_freshness.py:test_record_rejects_naive_close_time; tests/unit/core/safety/test_data_freshness.py:test_no_observations_returns_none | 입력형태 검증뿐, 성능단언·게이트 DENY 재현(관측 트래커라 자체 게이트 아님)·적대적/동시성 없음 — D2 미달. |
| 1331 | R-44 core/safety/recovery_gate.py 순수 규칙(재가동 4가지 거부) | ad2f0d7 | D2 | 9 | tests/unit/core/safety/test_recovery_gate.py:test_missing_evidence_denies; tests/unit/core/safety/test_recovery_gate.py:test_cooldown_not_met_by_unknown_data_delay_denies | 모든 DENY 테스트가 실제 can_reactivate() 게이트 재현 + unknown_data_delay 실패주입, 9 negative — 그러나 성능단언·적대적·동시성(순수함수) 없음 — D3 미달. |
| 1335 | R-39 services/safety/open_order_sweeper.py (범위내 취소, 멱등 sweep | 9d5bc6c | D3 | 4 | tests/unit/services/test_open_order_sweeper.py:test_toctou_race_exposes_locked_order_in_raced; tests/unit/services/test_open_order_sweeper.py:test_concurrent_sweeps_do_not_double_cancel_same_order | D3. 어댑터 예외 실패주입 + 2코루틴 실경합 증명 + 커밋 메시지가 문서화한 고의 결함주입→적색 확인→복구(게이트 적색 재현 교과서적 사례). 성능단언만 없음. |
| 1336 | R-27 execution_loop/exposure_snapshot.py (노출 스냅샷 단일쿼리 + asse | c29f159 | D1 | 2 | tests/integration/services/test_exposure_snapshot.py:test_single_round_trip; tests/integration/services/test_exposure_snapshot.py:test_other_users_positions_are_excluded | 단일왕복 성능단언은 있으나 negative 2건(<3)뿐, 실패주입·게이트 재현·적대적 없음 — D2 미달. |
| 1348 | R-34 마이그레이션 f4b9d6e5a7c8(gate_kind 6종·trace_id·멱등 digest·pau | 66bb7c4 | D2 | 4 | tests/foundation/integration/risk_gate/test_risk_gate_lifecycle.py:test_gate_kind_check_rejects_value_outside_the_six; tests/foundation/integration/risk_gate/test_risk_gate_lifecycle.py:test_idempotency_digest_unique_rejects_duplicate | 실DB 제약 실패주입(CHECK/UNIQUE/FK) + evaluate_risk_gate() DENY 재현 있으나 신규 필드 전용 성능·적대적·동시성 테스트 없음(파일 내 동시성 테스트는 다른 리프 대상) — D3 미달. |
| 1350 | R-43 metrics_collector data_delay 실측 + circuit_breaker _set_ | bb513af | D3 | 3 | tests/integration/test_circuit_breaker.py:test_unknown_data_delay_does_not_read_as_normal; tests/integration/test_circuit_breaker.py:test_concurrent_set_level_only_one_writer_wins | D3. R-43이 고치려는 실결함(fail-open)을 정확히 재현·수정확인 + 2코루틴 실경합에서 패자가 ConcurrencyConflictError(실패주입+동시성 동시충족). 성능단언만 없음. |
| 1353 | R-40 KillSwitchService(5범위 단일 권위 진입점) + activate_safety_cont | 2ffd22a | D3 | 5 | tests/integration/risk/test_kill_switch_service.py:test_insert_into_safety_control_has_exactly_one_call_site; tests/foundation/integration/risk_gate/test_risk_gate_lifecycle.py:test_concurrent_activations_never_lose_a_fence_token | D3. 5-way 동시활성화가 펜스토큰을 잃지 않음을 증명 + kill switch가 실제 실행루프를 차단함을 증명하는 정통 적대적 테스트. 성능단언만 없음. |
| 1360 | R-31 execution_loop/risk_inputs_assembler.py 신규(단일 CTE 2왕복)  | d6f48be | D2 | 3 | tests/integration/risk/test_risk_inputs_assembler.py:test_two_select_round_trips; tests/integration/risk/test_risk_inputs_assembler.py:test_missing_distrust_level_denies_via_safety_state_rule | negative≥3 + 실패주입/게이트DENY 결합 + 쿼리왕복횟수 성능단언까지 D2 완비. 그러나 적대적·다중인스턴스 증거 없음 — D3 미달. |
| 1361 | R-32 execution_loop/tick_risk_phase.py 신규 + tick.py 분할 | bc2d7da | D3 | 7 | tests/integration/test_execution_tick.py:test_concurrent_tick_race_only_submits_one_order; tests/integration/test_execution_tick.py:test_equity_baseline_persists_and_survives_simulated_restart | D3. 2-tick 실경합에서 정확히 1건만 제출되는 동시성 증명 + 시뮬레이션 재시작 복구 증명. 단 p99≤50ms는 실제로는 단언이 아니라 출력만 함(R-57 관련 결함). |
| 1362 | R-35 risk_gate/application/evaluate_pre_submit.py(PRE_SUBMIT | d103d07 | D2 | 7 | tests/integration/risk/test_pre_submit_gate.py:test_missing_circuit_breaker_level_is_fail_closed_deny; tests/integration/risk/test_pre_submit_gate.py:test_other_tenants_control_does_not_leak_into_this_tenants_decision | fail-closed DENY 다수 + WORM기록 + DB왕복횟수 게이트(성능 근사)는 있으나, evaluate_pre_submit 자체 로직을 표적으로 한 적대적 테스트나 동시성 증명은 없음 — D3 미달. |
| 1363 | R-45 services/safety/circuit_breaker_loop.py(10s 수집→evaluate | 58b3c16 | D2 | 6 | tests/integration/risk/test_circuit_breaker_loop.py:test_halted_never_auto_downgrades_only_creates_request; tests/integration/risk/test_circuit_breaker_loop.py:test_four_rejections_delegate_to_can_reactivate_and_block_normal | I5 상태기계 4가지 거부사유·재악화 취소까지 강한 커버리지지만 성능단언·동시성/적대적 증거 없음 — D3 미달. |
| 1370 | R-41 risk_guard_service를 KillSwitchService 단일 경로로 전환 | 807d748 | D1 | 3 | tests/integration/risk/test_risk_guard_service.py:test_duplicate_trigger_does_not_create_second_control; tests/integration/risk/test_risk_guard_service.py:test_risk_guard_service_has_no_direct_execution_stop_paths | 게이트 DENY 재현·격리 negative 3건은 있으나 진짜 실패주입(DB오류/timeout)이 아님, 성능·동시성·적대적 없음 — D2 미달. |
| 1403 | R-36 order_service gate/foundation_gate/pre_submit_check 수정( | a2e2646 | D2 | 5 | tests/integration/test_order_service_risk_gate.py:test_unmandated_submit_denied; tests/integration/test_order_service_risk_gate.py:test_stale_fence_denies_even_when_no_control_is_currently_active | mandate 누락 fail-closed·stale fence 거부(RISK_MANDATE_REQUIRED/RISK_FENCE_STALE 실사유코드) 확보. 단, 프로덕션 배선에 require_mandate=False 잔존 지점 존재(RTF-04) — 적대적/동시성 증거도 없음 — D3 미달. |
| 1404 | R-37 마이그레이션 orders_risk_decision_fk + order_service/fenced_s | 62aa2a9 | D3 | 9 | tests/adversarial/risk/test_fence_race.py:test_staged_gather_kill_switch_vs_submits_post_fence_zero; tests/integration/risk/test_fenced_submit.py:test_forged_allow_with_non_actionable_decision_rejected_by_trigger | D3. asyncio.gather 실경합(105 동시성 표준) + 어댑터 오류 실패주입 + DB 트리거 게이트 재현. |
| 1520 | R-56 risk 적대적 테스트 3파일(no_llm_in_risk_path·agent_cannot_forge | 51be3c7 | D3 | 20 | tests/adversarial/risk/test_agent_cannot_forge_allow.py:test_rsk006_forged_fence_snapshot_cannot_revive_denied_decision; tests/adversarial/risk/test_cross_tenant_limits.py:test_rsk_i8_tenant_a_cannot_aim_kill_switch_at_tenant_b | D3(적대적 리프 자체). 위조 decision-id/fence-snapshot/테넌트월경 공격 시뮬레이션 + AST LLM주입 탐지. |
| 1521 | R-57 tests/performance/test_pre_trade_latency.py(p99) + R-58 | 1d48636 | D1 | 2 | tests/performance/test_pre_trade_latency.py:test_pre_trade_phase_p99_measured_and_round_trips_exact; tests/performance/test_pre_trade_latency.py:test_pre_trade_round_trip_gate_detects_extra_query | 명세 DoD가 요구하는 'p99≤50ms 단언'이 실제로는 존재하지 않음(퍼센타일은 출력만, CI 게이트는 DB왕복횟수로 대체 — CI flakiness 회피 의도 기록됨). DoD 미이행 상태로 성능 리프 자체가 D1. |
| 1532 | P0 R-56 실결함 수정 1/2: fenced_submit이 WORM risk_decision 행에서 F0 | ae2a452 | D3 | 8 | tests/adversarial/risk/test_decision_subject_reuse.py:test_i10_allow_for_execution_x_cannot_be_transferred_to_execution_y; tests/adversarial/risk/test_decision_subject_reuse.py:test_i4_forged_f0_cannot_bypass_fence_after_kill_switch | D3. 레드팀 재현(774a0a0)→고정 전 실패 확인→수정 후 통과 문서화, 위조 F0/전이 decision-id 적대적 테스트로 자체 방어. |
| 1537 | P0 R-56 실결함 수정 2/2: orders_require_risk_decision 트리거 확장(exec | 636b628 | D3 | 9 | tests/adversarial/risk/test_trigger_execution_ref.py:test_i10_db_layer_rejects_allow_transferred_to_other_execution_symbol_quantity; tests/adversarial/risk/test_trigger_execution_ref.py:test_update_of_binding_columns_after_valid_insert_is_rejected | D3. 앱 계층을 건너뛰고 직접 INSERT로 트리거를 공격하는 적대적 테스트로 동일 I10 공격을 DB층에서 재현. |
| 1568 | R-58 docs/RED_TEAM_FINDINGS.md 등재 7건 — 번호 부여·해소 커밋 대조 | 5cadc59 | D0 | 0 | (없음) | 커밋이 문서 13줄만 수정 — 테스트 파일 추가/수정 0건. 실제 코드 재검증(grep)으로 실드리프트(RTF-04 2→3 지점)를 찾아낸 것은 유효하나, 이 리프 자체엔 실행 가능한 테스트 증거가 없어 D0. |
| 1806 | P0-B 분리 2/2: pre_submit_check가 mandate_revision_id·observed_ | 65e3f17(실제 기능 커밋 98e18c31) | D1 | 2 | tests/adversarial/risk/test_tick_mandate_fence_staleness.py:test_stale_observed_fence_denies_tick_submission; tests/adversarial/risk/test_tick_mandate_fence_staleness.py:test_superseded_mandate_revision_denies_tick_submission | tests/adversarial/ 안에 있고 두 DENY 모두 실결함(RISK_FENCE_STALE/RISK_MANDATE_REVISION_STALE)의 진짜 게이트 재현이지만 negative 2건(<3), 실패주입·성능단언·동시성 없음 — D2 미달. |
| 2133 | R-49 core/safety/market_correlation.py + watchdog.decide 확장( | e89217e7 | D1 | 4 | tests/unit/core/safety/test_watchdog_decide.py:test_db_isolated_failure_downgrades_liquidate_to_halt; tests/unit/core/safety/test_market_correlation.py:test_basket_below_min_symbols_is_undeterminable | [중대] 프로덕션 호출부 src/watchdog_process.py:192가 decide()에 market_wide_correlated=None 고정·failure_domain 미전달 — 이 리프가 만든 로직이 실제 실행루프에서 전혀 도달 불가(1806과 동일 클래스의 '파라미터 항상 None' 결함이 상위 레이어에서 재발). 단위테스트만으로는 D1이며, 배선 결함은 DEEPEN에 필수 명시. |
| 2134 | R-50 core/safety/liquidation_planner.py (분할·지터·참여율 상한, seed  | 9d72b7d7 | D1 | 1 | tests/unit/core/safety/test_liquidation_planner.py:test_same_inputs_produce_identical_canonical_hash; tests/unit/core/safety/test_liquidation_planner.py:test_unknown_volume_is_fail_closed_not_unlimited | 결정론(replay) 증명은 있으나 D2 기본 체크리스트(negative≥3·실패주입·게이트재현) 미충족. 프로덕션 배선(liquidation_planning.py)은 확인됐으나 volume_5m={}으로 항상 호출돼 참여율상한 실질경로 미검증. |
| 2135 | R-53 risk_gate application/recovery_gate.py + 라우터 (RECOVERY  | 6501a063 | D2 | 4 | tests/foundation/integration/risk_gate/test_recovery_gate.py:test_one_decision_row_per_evaluation_and_worm_blocks_update; tests/foundation/integration/risk_gate/test_recovery_gate.py:test_router_denied_maps_to_403_risk_denied_with_rsk007 | WORM 불변성 실패주입 + DENY→403 매핑 게이트 재현 확보. 성능단언·적대적/다중인스턴스 없음 — D3 미달. |
| 2136 | R-46 마이그레이션 risk_signal + postgres_signal_repository + appli | 42a9d8da | D2 | 6 | tests/integration/risk/test_intraday_monitor.py:test_insert_with_nonexistent_tenant_id_raises_fk_violation; tests/integration/risk/test_intraday_monitor.py:test_dedupe_key_boundary_05_00_00_and_05_04_59_are_same_key | FK 실패주입 + 마이그레이션 up/down/up 복구 + dedupe 경계 확보. 그러나 모듈의 핵심 안전장치인 ON CONFLICT DO NOTHING 중복방지가 실동시호출(병렬)로는 전혀 검증되지 않음(순차호출만 테스트) — D3 미달. |
| 2138 | R-54 application/replay_decision.py + src/tools/risk_replay. | 59f6e5d5 | D3 | 6 | tests/integration/risk/test_replay_decision.py:test_replay_matches_untampered_decision; tests/integration/risk/test_replay_decision.py:test_replay_detects_tampered_reason_codes | D3. 모듈 핵심 자체가 replay/결정론 증명이고, 후속 수정커밋 2건이 실제 CI 적색 사고(task-2174, task-2395/77871f678ce2)를 영구 회귀테스트로 등재. 성능단언만 없음. |
| 2139 | R-55 services/risk_alerting.py (한도 위반 알림 — 5분 중복 억제, hard=CR | c8bd6cd7 | D1 | 2 | tests/unit/services/test_risk_alerting.py:test_suppression_boundary_holds_at_299s_and_lifts_at_301s; tests/unit/services/test_risk_alerting.py:test_gateway_failure_does_not_propagate_to_caller | 억제 경계 테스트·게이트웨이 실패 fail-closed 2건은 있으나 negative<3, 성능단언·게이트DENY재현·적대적/동시성 없음 — D2 미달. |
| 2357 | R-51 마이그레이션 e5a8c5d4f6b7 liquidation_request/slice + watchdo | 1504fd95 | D1 | 3 | tests/integration/risk/test_watchdog_liquidation_request.py:test_slice_unique_request_seq_rejects_duplicate; tests/integration/risk/test_watchdog_liquidation_request.py:test_run_one_cycle_does_not_reapply_same_decision_twice | DB 제약(CHECK/UNIQUE/FK) 실패주입 3건은 있으나 성능단언·게이트 적색 재현·다중 프로세스 동시성 테스트 없음 — D2 경계이나 체크리스트 미완성으로 D1. |
| 2358 | R-52 services/safety/liquidation_executor.py + background_lo | 77550e36(원커밋 9782a290) | D1 | 2 | tests/integration/risk/test_liquidation_worker.py:test_seed_key_missing_fails_closed; tests/integration/risk/test_liquidation_worker.py:test_fence_change_aborts_request_and_skips_slices | [중대] 스펙이 명시적으로 요구하는 'SELECT...FOR UPDATE SKIP LOCKED로 동시 워커 간 중복실행 방지'를 실제 2-워커 동시경합으로 증명하는 테스트가 전혀 없음(로직만 구현, 증명 없음) — 강제청산 이중실행 방지가 미검증 상태로 D1. |

## 발행된 DEEPEN task

D3 미달 35건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 1, role은 원 task와 동일(backend), tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 103 | 2810 |
| 1115 | 2811 |
| 1116 | 2812 |
| 1151 | 2813 |
| 1152 | 2814 |
| 1176 | 2815 |
| 1177 | 2816 |
| 1186 | 2817 |
| 1194 | 2818 |
| 1196 | 2819 |
| 1209 | 2820 |
| 1219 | 2821 |
| 1220 | 2822 |
| 1222 | 2823 |
| 1316 | 2824 |
| 1318 | 2825 |
| 1330 | 2826 |
| 1331 | 2827 |
| 1336 | 2828 |
| 1348 | 2829 |
| 1360 | 2830 |
| 1362 | 2831 |
| 1363 | 2832 |
| 1370 | 2833 |
| 1403 | 2834 |
| 1521 | 2835 |
| 1568 | 2836 |
| 1806 | 2837 |
| 2133 | 2838 |
| 2134 | 2839 |
| 2135 | 2840 |
| 2136 | 2841 |
| 2139 | 2842 |
| 2357 | 2843 |
| 2358 | 2844 |

