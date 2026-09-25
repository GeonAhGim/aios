# DEPTH 감사 — L(strategy) done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2732, qa-3)
- 대상: `pm/tasks`의 done implement task 중 제목이 `\bL\d\d\b`(두 자리 L-번호, 예: L01~L50)에 맞는 리프 전수 23건(60건 상한 이내이므로 전수 대조). 이미 별도 감사가 끝난 `docs/audit/DEPTH_L4_BR.md`의 `L4-NN`(하이픈 단일 실행 축)과는 다른 축이다 — 이번 대상은 `docs/specs/L4_strategy_portfolio_backtest_v1.0.md` §9의 `L01`~`L50` 표(지표·전략·포트폴리오·백테스트·검증·성과 리프)다.
- task-102/113("FND-09 performance L45~L49")는 제목이 `L45`~`L49` 부분 문자열을 포함해 정규식에 걸렸다 — §9 표에서 L45~L49가 실제로 `src/foundation/performance/*` 행으로 존재하므로 정당한 매치다(오탐 아님).
- 축 하한: ADR-2026-09-09-C의 안전축 목록(R, L4, LA/LB/LC, FA, CM, EO, DC)에 **L(strategy)는 포함되지 않는다** — 지표/전략/포트폴리오/백테스트/검증/성과 리프 23건 전부 **D2가 하한**이다.
- 판정 기준(ADR-2026-09-09-C, 엄격 적용): D0=스텁, D1=happy path+단위 테스트, D2=negative≥3·실패 주입 1·성능 단언 1(p95/p99/처리량)·게이트 적색 재현 1을 **전부** 충족, D3=D2 + 적대적/리플레이/다중 인스턴스 증명 중 최소 1. 4개 요건 중 하나라도 비면 D2로 인정하지 않고 D1에 머문다.
- 방법: 4개 병렬 감사 에이전트(worktree 내 read-only)가 각 리프의 커밋(`git show <commit> --stat`/diff)과 **현재** 테스트 파일을 직접 열어 negative 테스트 이름·실패 주입·성능 단언·게이트 적색 재현·적대적/동시성 증거를 실측했다. 커밋 이후 테스트 확장 여부는 `git log -- <test 파일>`로 확인(대부분 확장 없음, 원 커밋 상태 그대로 — 예외: task-2410은 이후 hardening 커밋 3dddffb0/task-2604에서 논문 대조 테스트가 추가됨, task-2512의 `test_aggregation.py`는 `type: ignore` 정리만 있었고 테스트 케이스 추가는 없음). 코드 수정 없음.

## 요약

- 전체 23건 **전부 D1**, D2 하한 미달(floor met=NO 23/23). D0 0건 · D1 23건 · D2 0건 · D3 0건.
- 4개 D2 요건 중 negative 테스트(≥3)는 대부분 충분하다(19/23이 3건 이상, 다수는 5~13건). 예외 2건: **task-102(FND-09 L45~L49, task-2732 그룹A#1)**는 예외 기반 negative 테스트가 0건(경계/포지티브 테스트만 있음), **task-2520(L14 market_state 다중 tf)**은 negative 1건뿐(`test_build_market_state_raises_when_required_timeframe_entirely_missing`), **task-2521(L36 validation domain)**은 negative 2건뿐.
- **실패 주입(failure-injection)은 23건 전부 0건.** 대부분 리프가 순수 도메인/애플리케이션 함수(DB·네트워크 I/O 없음)로 설계되어 있어 구조적으로 시뮬레이션된 DB 오류·네트워크 단절·크래시 주입이 성립하지 않는다(EM 축 감사와 동일 패턴). `test_selector.py`(L19)의 `monkeypatch`는 변조된 디스패치 결과 탐지용이지 인프라 실패 주입이 아니다.
- **수치 성능 단언(p95/p99/처리량)도 23건 전부 0건.** task-2385(L28 series_cache, O(n²) 제거가 커밋의 핵심 주장)조차 동등성(정확성) 검증만 있고 타이밍/처리량 단언은 없다 — 리프 자신의 근거(rationale)에 비춰 가장 아쉬운 공백.
- **게이트 적색 재현(gate-red reproduction)도 23건 전부 0건.** 일부 리프가 "task-XXXX" 또는 ADR 조항(H-9, R-32 §5 등)을 docstring에 인용하지만, 이는 자기 스펙 참조이거나 사전예방적 검증(known-answer 테스트)이지 실제 선행 CI 적색→수정 사고의 재현이 아니다(예: task-2410의 DSR/PBO 논문 대조 테스트는 "공식이 원 논문과 한 번도 검증된 적 없다"는 하드닝 갭을 메운 것이지 CI 적색 재현이 아님).
- D3급으로 근접한 적대적 테스트가 존재하는 리프 1건: **task-2532(L20 mandate_binding)**의 `test_nan_total_equity_denies_instead_of_raising_invalid_operation` 등 `model_construct`로 pydantic 검증을 우회한 뒤 fail-closed(승인 대신 거부)를 증명하는 적대적 테스트군 + revision_hash 변조 탐지 테스트 — D2의 나머지 3요건(실패 주입·성능 단언·게이트 적색 재현)이 비어 있어 D1에 머물렀다.
- 리프 스코프 이상 1건: **task-102**의 제목은 "L45~L49" 전체를 주장하지만 실제 커밋(f6096a5)은 L47(repository)만 다룬다 — L45/L46은 별도 미대조 커밋(b1c7d869, 45c6d0f3)에서, L48/L49는 다음 감사 대상인 task-113(0492daa)에서 각각 수행됨. 해시·내용 오류는 아니고 제목 범위 표기 부정확.

## 전수 판정표

| id | title | commit | axis floor | grade | floor met | evidence | missing |
|---|---|---|---|---|---|---|---|
| 102 | FND-09 performance L45~L49 | f6096a5 | D2 | D1 | **NO** | test_insert_and_get_statement_round_trips_decimal_precision; test_list_statements_scoped_to_tenant; test_get_latest_statement_returns_highest_revision; test_public_role_has_no_update_or_delete_grant_on_statement(WORM 권한 검증); test_attribution_slices_persist_and_list_by_statement; test_insert_methodology_is_idempotent_on_conflict | negative(raises) 테스트 0건(요건 미달, ≥3 필요); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음. 제목 범위("L45~L49") vs 커밋 실제 범위(L47만) 불일치 |
| 113 | FND-09 L48(paper_input_adapter)·L49(compute_statement+라우터) | 0492daa | D2 | D1 | **NO** | test_load_reconciled_snapshots_raises_when_never_reconciled; test_load_reconciled_snapshots_raises_when_material_mismatch; test_compute_statement_raises_when_unreconciled; test_correct_statement_not_found; test_correct_statement_cross_tenant_denied; test_get_statement_cross_tenant_denied(6건) | failure-injection 없음(DB/네트워크 오류 시뮬레이션 0건, 저장소 전체 grep 결과 monkeypatch/side_effect/ConnectionError/OSError 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음(유일한 "regression" 이름 테스트도 인시던트 인용 없음) |
| 1232 | L01 지표 스펙 타입 + TA-Lib 11종 스펙(spec.py·specs_talib.py, REGISTRY_VERSION=ind-v1) | 0c639e9 | D2 | D1 | **NO** | test_specs_registry_version_is_ind_v1; test_specs_lookback_matches_talib_nan_count(11종 파라미터화, 실TA-Lib 대조); test_specs_defaults_are_within_param_range; test_specs_unknown_indicator_is_rejected; test_specs_out_of_range_param_is_rejected; test_param_spec_is_frozen | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(task-1729 인용은 순방향 호환 문서화일 뿐 사고 재현 아님) |
| 1233 | L02 IndicatorRegistry(조회·파라미터 검증·lookback·registry_hash 단일 진입점) | 88fbbbf | D2 | D1 | **NO** | test_registry_get_unknown_indicator_raises; test_registry_validate_params_out_of_range_raises(5-파라미터화); test_registry_validate_params_rejects_non_int_value; test_registry_validate_params_unknown_indicator_raises; test_registry_hash_is_stable_across_calls_and_instances; test_registry_hash_changes_when_a_spec_changes | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1367 | L03 talib_adapter registry 위임(_SPECS·period_param_name 제거) + IndicatorResult.registry_version | d7358d6 | D2 | D1 | **NO** | test_talib_adapter_has_no_leftover_specs_or_period_param_name(회귀); test_calculate_rejects_out_of_range_param_via_registry(4-파라미터화); test_calculate_rejects_unknown_indicator_via_registry; test_indicator_result_registry_version_matches_registry; test_calculate_min_required_bars_matches_registry_lookback(3-파라미터화, 경계) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2385 | L28 core/indicators/series_cache.py — 백테스트 전구간 1회 계산 + point-in-time 조회(O(n^2) 제거) | c84317ef | D2 | D1 | **NO** | test_value_at_matches_incremental_recomputation_sma/_rsi(인과성 증명); test_duplicate_keys_are_computed_once(1회 계산 증명); test_unknown_key_raises; test_out_of_range_bar_index_raises(3-파라미터화); test_missing_timeframe_raises; test_unknown_output_line_raises; test_noncausal_indicator_is_rejected_at_build(적대적 성격의 look-ahead 차단) | O(n²) 제거가 커밋의 핵심 주장임에도 **수치 성능/타이밍 단언 없음**(동등성 검증만 존재); failure-injection 없음; 게이트 적색 재현 없음 |
| 2386 | L33 backtest/domain/{splits,param_stability}.py — walk-forward 분할(purge·embargo)과 파라미터 고립도 | c7644cd0 | D2 | D1 | **NO** | test_assert_no_overlap_rejects_overlapping_indices(OosLeakageError); test_assert_no_overlap_rejects_insufficient_gap; test_min_train_violation_is_rejected_not_reduced(축소 대신 하드 실패); test_min_train_violation_rejected_even_with_zero_gap; test_unknown_mode_is_rejected; test_grid_smaller_than_four_hard_fails; test_missing_metric_point_is_rejected; test_non_ascending_axis_is_rejected(8건) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2410 | L34 backtest/domain/overfitting.py — Deflated Sharpe Ratio·PBO(CSCV) 순수 계산 | cd7205ee | D2 | D1 | **NO** | test_deflated_sharpe_rejects_n_trials_below_2/_T_below_2/_non_positive_sr_var; test_pbo_rejects_invalid_n_blocks(3-파라미터화); test_pbo_rejects_ragged_matrix; 이후 hardening 커밋(3dddffb0, task-2604, "ADR-2026-09-09-B H-9")에서 test_deflated_sharpe_matches_bailey_lopez_de_prado_2014_numerical_example 등 원논문 대조 테스트 추가 | failure-injection 없음; 수치 성능 단언 없음; **게이트 적색 재현 미충족** — H-9는 "공식이 원논문 대조 검증된 적 없다"는 사전예방적 하드닝 갭 메움이지 선행 CI 적색 재현이 아님 |
| 2411 | L35 backtest/application/{param_sweep,walk_forward,stress}.py — 격자 전수·워크포워드·스트레스(결정론) | 13d400f8 | D2 | D1 | **NO** | test_unknown_grid_axis_is_rejected(2개 파일 공통); test_too_many_blocks_for_equity_curve_is_rejected; test_empty_splits_is_rejected; test_overlapping_splits_are_rejected(OosLeakageError); test_worst_days_removed_shrinks_bar_count_effect; test_unknown_scenario_is_rejected(7건) | failure-injection 없음(fake indicator service가 런타임 오류를 시뮬레이션하지 않음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2415 | L26 backtest ports 2종(fill_simulator·bar_source) + adapters/list_bars.py (미래 인덱스 = LOOKAHEAD 위반) | a72cdca4 | D2 | D1 | **NO** | test_point_in_time_bars_protocol_rejects_missing_at; test_at_future_index_raises_lookahead_error; test_upto_future_index_raises_lookahead_error; test_at_negative_index_raises_lookahead_error_not_python_wraparound; test_upto_negative_index_raises_lookahead_error_not_python_wraparound(5건, error_code 단언) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(스펙 I2 원칙 인용일 뿐 실제 인시던트 아님) |
| 2416 | L29 backtest/domain/universe.py (survivorship 상폐 구간) + rules.py 확장 | d34569da | D2 | D1 | **NO** | test_member_rejects_naive_listed_from/_delisted_at; test_snapshot_rejects_naive_as_of; test_is_member_rejects_naive_at; test_is_member_unknown_symbol_raises_not_false(fail-closed); test_is_member_query_after_as_of_raises; test_assert_fill_after_signal_raises_on_same_bar_fill; test_require_cost_model_rejects_all_zero_when_disallowed(8건) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2451 | L06 ★ src/core/strategy/indicator_key.py — 지표 키 문법 단일 출처(parse_key/format_key) + _KEY_RE 사용처 교체 | 3b6bb9af | D2 | D1 | **NO** | test_parse_key_rejects_invalid_input(4-파라미터화); test_market_state_delegates_to_indicator_key(구 정규식이면 실패했을 것을 증명); test_format_key_round_trips; test_allowed_timeframes_match_market_data_timeframe_enum(5건 이상) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2452 | L17 ★ src/core/portfolio/{models,config,state_input}.py — PortfolioConfig·PortfolioStateInput 계약 + config_hash 안정성 | 433edcc4 | D2 | D1 | **NO** | test_from_dict_rejects_dict_missing_total_equity; test_min_trade_notional_rejects_negative; test_portfolio_config_rejects_float_for_decimal_field; test_portfolio_state_input_rejects_float_for_decimal_field; test_cost_model_ref_rejects_non_hex_hash(5건) | failure-injection 없음(순수 pydantic 값 객체, 주입할 I/O 자체가 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2456 | L08 ★ src/core/strategy/{state_memory,market_state}.py — 실행별 crossover 상태 계약 + 다중 tf MarketState(look-ahead 차단) | 7c1dbb57 | D2 | D1 | **NO** | test_assert_no_future_rejects_bar_close_time_one_second_ahead; test_market_state_rejects_malformed_indicator_key/_naive_as_of/_naive_bar_close_time/_float_values; test_state_memory_rejects_naive_last_bar_time/_float_prev_values(7건) | failure-injection 없음(시뮬레이션된 예외/크래시 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2462 | L25 backtest domain models/snapshot/events — 스냅샷·이벤트 모델과 cost_model_hash 안정성 | e17cac37 | D2 | D1 | **NO** | test_order_event_is_frozen(변경 거부); test_naive_datetime_is_rejected; test_append_event_rejects_non_increasing_sequence; test_bar_snapshot_ref_missing_snapshot_hash_is_rejected/_missing_bar_count_is_rejected(5건); AST 순수성 검사(test_domain_file_has_no_io_or_nondeterministic_imports)는 정적 위생 검사일 뿐 런타임 실패 주입 아님 | failure-injection 없음(도메인 계층 의도적 무-I/O, 상위 계층에서도 미검증); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2468 | L07 src/core/indicators/lookback.py — 타임프레임별 required_bars 정확값 + 미지 지표 오류 | e7f5d49d | D2 | D1 | **NO** | test_l07_unknown_indicator_rejected_fail_closed; test_l07_negative_period_and_malformed_key_rejected(2-raise); test_l07_key_without_timeframe_is_rejected_not_defaulted(3건, D2 하한 최소치 간신히 충족) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(80줄 상한·no-talib 검사는 위생 검사일 뿐) |
| 2469 | L27 backtest adapters/bar_fill_simulator.py + application/simulate_fill.py 래퍼 전환(수치 동일) | cf0a5475 | D2 | D1 | **NO** | test_look_ahead_violation_rejected_for_same_bar_index(I-05); test_rejects_non_positive_quantity; test_rejects_negative_slippage_bps(3건); test_deterministic_across_seeds + 수치 동등성 테스트 3건(레이턴시/처리량 단언 아님) | failure-injection 없음(모의 거래소/네트워크 오류 없음); 수치 성능 단언 없음(동등성 검증만); 게이트 적색 재현 없음 |
| 2512 | L18 ★ src/core/portfolio/aggregation.py — 다중 실행 노출 집계(합산 항등, as_of 필수) | 330ea6ac | D2 | D1 | **NO** | test_aggregate_rejects_as_of_none_instead_of_defaulting(MissingAsOfError); test_aggregate_rejects_zero_total_equity/_negative_total_equity(NonPositiveEquityError); test_execution_exposure_rejects_float_notional/_float_vol_pct(5건) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2520 | L14 execution_loop/market_state.py 다중 타임프레임 조립(MarketState·as_of·assert_no_future) | ed575aa9 | D2 | D1 | **NO** | test_build_market_state_raises_when_required_timeframe_entirely_missing(MarketStateAssemblyError, 유일한 negative); test_build_market_state_assembles_multiple_timeframes/_excludes_unclosed_trailing_bar/_skips_key_with_insufficient_warmup_without_raising(전부 happy-path) | **negative 1건뿐(≥3 미달, D2 최소 요건 자체 미충족)**; failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(docstring이 "R-32 §5" 스펙 규칙을 인용할 뿐 선행 인시던트 아님) |
| 2521 | L36 validation domain/{policy,check_result,artifact}.py — 정책 해시·아티팩트 변조 감지 | b137cfb0 | D2 | D1 | **NO** | test_check_result_rejects_hard_fail_code_outside_closed_set(ValidationError); test_verify_detects_tampered_fsm_definition(ARTIFACT_HASH_MISMATCH 반환, raise 아님) — 2건뿐 | **negative 2건뿐(≥3 미달)**; failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2522 | L21 ★ core/portfolio/{accounting,rebalance}.py — 현금 원장 불변식·리밸런스 회전율/비용 | f3faf8f3 | D2 | D1 | **NO** | test_reserve_rejects_when_available_would_go_negative(PortfolioInsufficientCashError); test_cash_ledger_rejects_float_cash/_float_reserved; test_missing_price_for_a_required_trade_raises(4건) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2525 | L19 ★ core/portfolio/sizing/*.py (5) — 4 산식 + selector 디스패치, None 입력 fail-closed | a41471a6 | D2 | D1 | **NO** | test_size_rejects_zero_price/_negative_price(fixed_fractional); test_size_rejects_none_win_rate/_none_avg_win_loss_ratio/_win_rate_out_of_range(kelly_capped); test_size_rejects_none_exposures/_none_realized_vol_pct(risk_parity); test_size_for_rejects_config_input_method_mismatch/_unknown_method/_malformed_inputs_hash/_result_method_mismatch(selector, 변조 탐지, 13건 이상) | failure-injection 없음(selector의 monkeypatch는 변조된 디스패치 결과 탐지용이지 DB/네트워크/크래시 주입 아님); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2532 | L20 ★ core/portfolio/mandate_binding.py — 순차 클램프 정확값·revision_hash 대조 fail-closed | 4614899c | D2 | D1 | **NO** | test_wiped_forbidden_assets_with_stale_hash_is_rejected/_tampered_numeric_limit_with_stale_hash_is_rejected(MandateRevisionHashMismatchError); test_nan_quantity/_nan_price/_nan_total_equity_denies_instead_of_raising_invalid_operation(model_construct로 pydantic 우회, **적대적 테스트=D3급**); test_negative_or_zero_quantity_denies_instead_of_slipping_through(11건 이상) | failure-injection 없음(순수 계산, I/O 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 — D3급 적대적 테스트가 있음에도 D2 자체가 미달이라 D3는 논외 |

## 발행된 DEEPEN task

D2 하한 미달 23건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 2, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 102 | 3196 |
| 113 | 3197 |
| 1232 | 3198 |
| 1233 | 3199 |
| 1367 | 3200 |
| 2385 | 3201 |
| 2386 | 3202 |
| 2410 | 3203 |
| 2411 | 3204 |
| 2415 | 3205 |
| 2416 | 3206 |
| 2451 | 3207 |
| 2452 | 3208 |
| 2456 | 3209 |
| 2462 | 3210 |
| 2468 | 3211 |
| 2469 | 3212 |
| 2512 | 3213 |
| 2520 | 3214 |
| 2521 | 3216 |
| 2522 | 3217 |
| 2525 | 3218 |
| 2532 | 3219 |
