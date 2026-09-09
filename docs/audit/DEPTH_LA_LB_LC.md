# DEPTH 감사 — LA/LB/LC(+DC) done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2723, qa-1)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/LA-|LB-|LC-|L0-/`에 맞는 리프 전수는 84건 — 60건을 넘어 명세에 따라 **최근 60건**(updated_at 기준)을 대조했다. 제외된 24건(가장 오래된 것들, 대부분 L0-1~L0-5와 LC-1~LC-10 초기 리프)은 이번 감사 범위 밖이다.
- 축 하한: ADR-2026-09-09-C 안전축(R, L4, LA/LB/LC, FA, CM, EO, DC) 목록에 LA/LB/LC와 DC가 포함되므로, 제목이 `LA-N`/`LB-N`/`LC-N`/`DC-N` 형식으로 **시작하는** 55건은 **D3 하한**. 제목이 그 형식으로 시작하지 않고 본문 중 다른 리프 번호를 괄호·중간 문장으로만 언급하는 5건(420, 618, 626, 627, 1569 — 커밋 메시지/제목이 실질적으로 다른 작업이고 정규식이 우연히 매치)은 축 코드 목록에 없는 것으로 취급해 **D2 하한**. LB-19/LA-24의 프론트엔드 후속 배선 커밋(1524, 1525)은 제목이 leaf 번호로 시작하므로 D3 하한을 유지했다.
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명 중 최소 1. 4개 요건 중 하나라도 빠지면 D2로도 인정하지 않고 D1에 머문다(엄격 적용).
- 방법: 5개 병렬 감사 에이전트(worktree 내 read-only, 일부는 하위 에이전트로 3건씩 추가 분할)가 각 리프의 커밋(`git show <commit> --stat`/diff)과 **현재** 테스트 파일을 직접 열어 negative 테스트 이름·실패 주입·성능 단언·게이트 적색 재현·적대적/리플레이/동시성 증거를 실측했다. 코드 수정 없음.

## 요약

- 전체 60건 중 **59건이 자기 축의 D 하한 미달**(floor met=NO), 1건만 하한 충족.
- 등급 분포: D0 0건 · D1 59건 · D2 0건 · D3 1건.
- 하한 충족 1건: **task-614(LC-17)** — 원장 적대적 4파일이 asyncio.gather 5-way 동시경합(정확히 1건 성공), 리플레이 공격 거부+감사기록, WORM 트리거 권한우회 시도 실패(InsufficientPrivilegeError), p95<30ms 성능 단언(CI 적색사고 task-920/1029 문서화)까지 D2 4요건과 D3 적대적 증명을 모두 갖췄다. 60건 중 유일하게 "전부 충족"을 실제로 증명한 리프.
- 가장 흔한 단일 누락 항목: **수치 성능/지연/round-trip 단언 부재**(59건 중 대다수). 두 번째는 **게이트 적색 재현(실제 CI 적색→수정 기록) 부재** — 예외적으로 626/627/701/842/951/1321은 커밋 자체가 문서화된 실결함(task-614/920/1029/951/1004/1302)을 재현·수정하는 결함수정 리프라 이 요건을 갖췄지만, 그마저도 나머지 3요건 중 최소 하나(주로 negative<3 또는 성능단언 부재)가 비어 D1~D2 사이에서 막혔다.
- 순수 도메인 함수형 리프(LA-2/3/4/5/6/7, LB-5 등)는 negative 커버리지 자체는 탄탄하지만 구조적으로 I/O가 없어 failure-injection·성능단언·게이트 재현이 성립하기 어렵다(CM/R 축 감사에서도 반복 관찰된 패턴).
- **실질적으로 D3 요건에 근접했으나 D2 4요건 중 1개만 비어 아쉽게 D1에 머문 리프**가 다수 있다: 375/489/613/951(동시성 증명은 강하나 negative<3 또는 성능단언 부재), 614는 예외적으로 전부 충족. 626/627(LC-17 결함 수정 2건)은 게이트 적색 재현은 최상급이지만 파일 자체의 negative가 0~3건에 그쳐 D2 문턱을 넘지 못했다.
- 진짜 negative 테스트가 0건인 리프 4건 발견: task-376(LB-10, 조용한 빈 결과만 확인·raise 없음), task-453(LC-14 refund, Σ보존 단언뿐 거부 시나리오 없음), task-654(LB-14, 경계값 확인뿐 거부 없음), task-627(LC-17 결함 B, 성능 테스트 단독 파일).

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2/D3 미달 근거) |
|---|---|---|---|---|---|---|---|
| 349 | LC-11 ledger backfill (기존 wallet_transactions → 원장 소급 적재) | fc08429 | D3 | D1 | **NO** | test_backfill_rolls_back_everything_on_balance_mismatch(BackfillMismatchError+전체 tx rollback); test_backfill_rejects_incomplete_purchase_group(UnrecognizedTxGroupError); test_backfill_rerun_is_idempotent_replay_without_duplicate_entries(replay/결정론 증명) | failure-injection 테스트 없음; 수치 성능/round-trip 단언 없음; 게이트 적색 재현 없음; negative 1건 더 필요(현재 2) |
| 374 | LB-5 journal_rules + snapshot_builder (분개 규칙·스냅샷 폴드) | cd50c84 | D3 | D1 | **NO** | test_verify_chain_detects_single_bit_tamper_in_qty_delta(탬퍼 탐지, ChainIntegrityError); test_replaying_same_fill_inputs_produces_identical_digest(replay/결정론); test_fold_equals_manual_reduce_over_200_random_entries(200-case 속성 증명); test_apply_one_rejects_oversell(NegativeQuantityError) | failure-injection 테스트 없음(전부 순수함수 ValueError); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 375 | LB-9 positions Postgres 어댑터 3종(journal/snapshot/nav) | e491f04 | D3 | D1 | **NO** | test_concurrent_appends_produce_contiguous_hash_chained_sequence(asyncio.gather 20-way 동시경합, no-double/연속시퀀스 증명); test_insert_violating_closing_nav_check_is_rejected(실DB CHECK 위반=failure injection); test_upsert_with_stale_expected_seq_raises_and_does_not_overwrite(ConcurrencyConflictError); test_append_same_key_different_content_raises_digest_mismatch | 수치 성능/round-trip 단언 없음(perf는 별도 리프의 test_perf_journal_append.py 소관); 게이트 적색 재현 없음 |
| 379 | LA-2 timeframe (타임프레임 enum·길이·정렬) | a5d5599 | D3 | D1 | **NO** | test_align_open_rejects_naive_datetime; test_expected_opens_unknown_timeframe_raises; test_align_open_boundary(7-case 경계); test_expected_opens_skips_gap_between_sessions | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 380 | LA-3 calendar session_rules + known_venues (venue 세션 창) | 0168905 | D3 | D1 | **NO** | test_next_open_raises_when_calendar_exhausted(CalendarExhaustedError); test_holiday_is_open_false(fail-closed); test_us_close_est_before_dst/edt_after_dst(DST 경계쌍) | negative 1건 더 필요(현재 2); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 381 | LA-4 quality ohlc_sanity + dedupe (캔들 정합성·중복 처리) | bdb09a0 | D3 | D1 | **NO** | test_infinite_price_rejected/test_non_finite_value_rejected(비유한값 REJECT); test_naive_open_time_rejected; test_conflicting_duplicate_isolates_both(DUPLICATE_CONFLICT REJECT, 양쪽 격리) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 376 | LB-10 legacy_positions_projection (기존 positions 조회 호환 투영) | 9e26cbf | D3 | D1 | **NO** | test_partial_close_still_matches_legacy_query(부분청산 후 legacy 대비 동등); test_no_linked_legacy_row_returns_empty_not_exception(FK 미연결 시 빈 결과, 예외 아님); test_closed_position_reports_closed_at_and_matches_legacy | 진짜 negative 테스트 0건(현재는 조용한 빈 결과 확인뿐, raise/DENY 없음); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 390 | LA-6 quality outlier_detector + verdict (스파이크 탐지·배치 판정) | 5616181 | D3 | D1 | **NO** | test_decide_zero_total_fails_closed_to_reject; test_decide_negative_total_fails_closed_to_reject; test_detect_spikes_no_false_positive_on_volatile_normal_segment(고정시드 300캔들, 위양성 0) | negative 1건 더 필요(현재 2); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 391 | LA-7 reference symbol_normalizer + lifecycle (심볼 정규화·상태기계) | 584c94c | D3 | D1 | **NO** | test_unknown_quote_raw_raises/test_krx_invalid_format_raises/test_us_invalid_format_raises(SymbolNormalizationError); test_transition_matches_table(20-combo 파라미터, 14/20 REJECT); test_delisted_rejects_every_event(5종 전부 거부) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 410 | LA-5 quality gap_detector + stale_detector (캔들 갭·정체 탐지) | e8c355a | D3 | D1 | **NO** | test_detect_gaps_unknown_timeframe_raises; test_naive_last_ts_rejected/test_naive_now_rejected; test_krx_no_lunch_break_and_after_close_missing_is_not_gap(세션경계 오탐 방지) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 363 | LC-12 legacy_wallet_bridge + wallet_service 위임 + application/topup | 411a00a | D3 | D1 | **NO** | test_bridge_debit_insufficient_balance_rolls_back_without_projecting(BridgeInsufficientBalanceError, wallet/ledger/wallet_transactions 전부 미변경); test_bridge_credit_matches_ledger_and_projects_wallet_transactions(이중기록 정합) | negative 2건 더 필요(현재 1); failure-injection(실 DB/어댑터 결함 시뮬레이션) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 411 | LA-8 corporate_actions adjustment + lineage (분할·배당 소급조정, 계보 해시) | aff50ae | D3 | D1 | **NO** | test_batch_hash_streaming_matches_reference_property(200 랜덤배치, 신규 스트리밍 구현이 구 참조구현과 바이트동일=replay/동등성 증명, ADR-2026-09-04-A #2); test_batch_hash_golden_vector_is_pinned(골든 다이제스트 핀, CI게이트류); test_batch_hash_is_order_independent; test_zero_ratio_raises/test_negative_ratio_raises | negative 1건 더 필요(현재 2); failure-injection 없음; 수치 성능 단언 없음(대용량 테스트가 elapsed 측정하되 의도적으로 한계 미단언, CI-red 회피 정책 명시) |
| 417 | LA-9 market_data ports 5파일 | 17ce0e9 | D3 | D1 | **NO** | test_incomplete_implementation_fails_port_check(메서드 누락 → isinstance() False, fail-closed); test_dict_returning_fake_satisfies_isinstance_but_not_the_dto(구조는 통과하나 DTO 검증 실패); test_full_implementations_satisfy_their_ports(happy path) | negative 1건 더 필요(현재 2); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 412 | LB-11 application/record_fill (체결 → 포지션 저널 기록, 감사 1:1) | 09d8163 | D3 | D1 | **NO** | test_audit_failure_rolls_back_journal_and_snapshot(_BoomAuditAppender RuntimeError 주입, rollback 확인=failure injection); test_reverse_direction_oversell_rejected_and_not_persisted(NegativeQuantityError); test_digest_mismatch_same_key_different_content_rejected; test_replay_same_fill_returns_existing_without_duplicate(replay/결정론) | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 420 | ledger 통합테스트 격리 결함: LC-10 tamper 테스트가 공유 DB에 손상 해시체인·write_frozen 잔류 | b120c35 | D2 | D1 | **NO** | test_verify_ledger_integrity_freezes_and_blocks_posting_on_tamper(WORM 트리거 비활성화로 lines_digest 손상, 체인단절 탐지+write_frozen+post_entry 거부 확인=적대적 결함주입); test_verify_ledger_integrity_reports_ok_when_chain_intact(happy path) | negative 2건 더 필요(현재 1, D2 floor 기준); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 452 | LB-13 record_funding_fee + rebuild_snapshot (펀딩피 기록·스냅샷 재빌드) | 2c9bf78 | D3 | D1 | **NO** | test_audit_failure_rolls_back_journal_and_snapshot(두 파일 공통, _BoomAuditAppender RuntimeError 주입=failure injection); test_unknown_position_rejected(2개 파일 각각); test_replay_same_funding_id_returns_existing_without_duplicate(replay 증명); test_apply_fixes_drift_without_touching_journal(드리프트 보정, journal WORM 확인) | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 425 | LB-12 order_service/position_ledger 위임(Phase 1 가정 제거·부분청산 정확) | 5424d67 | D3 | D1 | **NO** | test_submission_failure_does_not_roll_back_fsm_state(failing_place_order가 RetryableExchangeError 발생=어댑터 결함주입); test_paused_execution_still_checks_pending_order_fill(docstring '레드팀 #23-c 회귀 테스트'=게이트 적색 재현); test_live_mode_is_hard_blocked_before_any_order_is_placed/test_risk_not_approved_raises_before_any_order_is_placed(하드가드 negative) | 수치 성능 단언(지연/round-trip 한계) 없음 |
| 424 | LC-13 postgres_hold_repository + application/purchase_flow + purchase_service 위임 | ef6627b | D3 | D1 | **NO** | test_place_hold_concurrent_same_reference_only_one_succeeds(asyncio.gather 5-way 동시 place_hold, 정확히 1건 성공/4건 HoldConflictError=동시성 증명+DB오류 failure injection); test_capture_expired_hold_is_rejected(HoldExpiredError) | negative 1건 더 필요(현재 2); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 486 | LC-15a postgres_payout_repository + application/payouts.py (홀드 창 경과 정산 배치) | 2c42cbf | D3 | D1 | **NO** | test_mark_payout_paid_unknown_batch_is_rejected(UnknownPayoutBatchError); test_mark_payout_paid_moves_available_to_payout_clearing(재처리 시 ConcurrencyConflictError 내장=DB오류 failure injection); test_schedule_payouts_releases_only_after_window_elapsed(경계 거부 내장); test_schedule_payouts_same_batch_key_is_idempotent(replay/멱등 증명) | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 453 | LC-14 refund (분쟁 환불 R1/R2/R3) | ceccb89 | D3 | D1 | **NO** | test_refund_case_r1/r2/r3_...(시나리오별 Σdebit=Σcredit·총잔액보존 단언); test_refund_duplicate_purchase_id_is_not_double_posted(replay/멱등 증명, 2차 호출 시 신규 분개 없음) | negative/거부 테스트 0건(3건 필요); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 488 | LA-19 거래소 심볼 변환을 symbol_normalizer 위임으로 교체(감사 §7 해소) | 0edadcb | D3 | D1 | **NO** | test_to_bitget_symbol_unknown_quote_raises/test_to_canonical_symbol_unknown_quote_raises(미상 quote 거부); test_kis_get_ticker/orderbook/ohlcv_malformed_symbol_rejected_before_request(HTTP 호출 전 fail-closed, 3건) | failure-injection(입력검증과 별개인 실 DB/어댑터 결함) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 418 | LA-10 마이그레이션 md_reference_registry | 10abb56 | D3 | D1 | **NO** | test_md_instrument_invalid_status_rejected(CheckViolationError); test_md_symbol_alias_overlapping_period_rejected(ExclusionViolationError=DB오류 failure injection); test_md_corporate_action_non_positive_ratio_rejected; test_md_venue_calendar_day_trading_flag_mismatch_rejected | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 487 | LC-15b chargeback + RECEIVABLE 상계 + 관리자 라우터 | 0efd6c2 | D3 | D1 | **NO** | test_chargeback_digest_mismatch_when_balance_changed_between_calls(IdempotencyDigestMismatchError); test_chargeback_duplicate_topup_id_is_not_double_posted(replay/멱등 증명); test_chargeback_shortfall_splits_available_and_receivable(RECEIVABLE 이연 경계) | negative 2건 더 필요(현재 1); failure-injection(DB/어댑터 결함) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 450 | LA-11 마이그레이션 md_candles (파티션 캔들·틱·인제스트 배치·품질이슈) | c7ff06f | D3 | D1 | **NO** | test_md_candle_ohlcv_check_violations_rejected(파라미터 6종 CHECK 위반=DB오류 failure injection); test_aios_app_cannot_update_md_candle(WORM 위반 거부); test_aios_app_cannot_delete_md_ingest_batch/test_aios_app_cannot_update_md_quality_issue(WORM 위반 거부) | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 451 | LA-12 참조·캘린더 어댑터(postgres reference/calendar + yaml 캘린더 소스) | 532f4f0 | D3 | D1 | **NO** | test_record_action_is_idempotent_on_replay(replay/멱등 증명); test_record_action_different_content_same_key_raises(탬퍼/다이제스트불일치 탐지); test_add_alias_overlapping_period_raises_exclusion_violation(DB exclusion 제약 경계); test_upsert_days_rejects_mismatched_venue | failure-injection(monkeypatch 등 시뮬레이션) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 615 | LA-13 market_data 저장 어댑터(postgres_candle_store·postgres_batch_repository) + 통합테스트 | 22cec23 | D3 | D1 | **NO** | test_upsert_batch_is_idempotent_on_reingest(replay/멱등 증명); test_query_as_of_snapshot_isolation_ignores_later_insert(시점조회 결정론); test_upsert_batch_rejects_ohlc_check_violation(DB CHECK 위반); test_quarantine_writes_only_quarantine_table(격리) | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 614 | LC-17 원장 적대적 4파일 + perf(race_purchase·negative_and_zero·replay_attack·role_bypass) | 6f912c5 | D3 | D3 | **YES** | test_five_concurrent_purchases_against_9000_hold_exactly_one_succeeds(asyncio.gather 5-way 동시경합, 정확히 1건 성공=동시성 증명); test_same_event_ref_different_amount_is_denied_with_audit_trail(리플레이공격 거부+감사기록=탬퍼 탐지); test_aios_app_cannot_disable_worm_trigger_on_journal_entry(권한우회 시도→asyncpg.InsufficientPrivilegeError=적대적+failure injection); test_journal_append_p95_under_30ms(수치 p95/round-trip 단언, docstring이 실제 CI 적색사고 task-920/1029/esc-ci-d5723ce4366d 문서화=게이트 적색 재현) | 없음 — D3 하한 충족 |
| 613 | LC-16 wallet 조회 통합(application/queries.py) | 8ffa4b9 | D3 | D1 | **NO** | test_get_balance_no_false_positive_drift_under_concurrent_commits(asyncio.gather 90-way 동시경합, 위양성 없음=동시성 증명); test_get_balance_raises_explicit_drift_error_instead_of_generic_failure(명시적 드리프트 오류); test_get_balance_reports_pending_payout_after_capture | negative 2건 더 필요(현재 1); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 489 | LB-18 positions 적대적 2파일(race_fills·cross_tenant) + 저널 append 성능 단언 | fb24f04 | D3 | D1 | **NO** | test_twenty_concurrent_fills_produce_gapless_unique_sequence(asyncio.gather 20-way 동시경합, gapless/unique 증명); test_cross_tenant_position_key_rejected(교차테넌트 공격 거부, journal 미변경=적대적); test_record_fill_journal_append_p95_under_30ms(수치 p95 단언, CI 적색사고 task-822/1059/esc-ci-ec4672faa3e4 서술=게이트 적색 재현) | negative 1건 더 필요(현재 2); failure-injection(실 DB/어댑터 결함 시뮬레이션) 없음 |
| 616 | LA-14 market_data application 3종(register_instrument·record_corporate_action·sync_calendar) + 감사 이벤트 | 589078b | D3 | D1 | **NO** | test_register_instrument_rolls_back_with_audit_failure(RuntimeError 주입, rollback=failure injection); test_record_corporate_action_replay_writes_no_extra_audit(replay/멱등 증명); test_delisted_instrument_rejects_relisting_with_denied_audit; test_sync_calendar_rejects_venue_mismatch_without_writing | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 624 | LA-17 캔들 조회·리플레이 — 해시 결정론·strict 갭 | 7ad6d15 | D3 | D1 | **NO** | test_replay_series_hash_is_deterministic_across_calls(replay/결정론 증명, 동일입력→동일해시); test_replay_strict_gap_raises_incomplete; test_get_candles_quarantined_view_unsupported_raises; test_get_candles_adjustment_applies_action_before_as_of | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 618 | 지갑 잔액 3분할 표시(available/held/pending_payout) — LC-16 응답 소비 | ea7bafe | D2 | D1 | **NO** | 'balance != available+held+pendingPayout 불일치는...경고'(walletBalance.test.ts, SUM_MISMATCH negative); '음수 금액 응답은...경고로 표기'(WalletBalanceCard.test.tsx); '금액이 숫자 타입이 아니면 invalid'(타입안전 거부); '필드 누락 등 판독불가 응답은 예외 없이 오류상태'(graceful-degrade) | failure-injection(API/백엔드 결함 시뮬레이션) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 626 | 원장 event.extra 안전성 검증(LC-17 결함 A) | 1ca3f64 | D2 | D1 | **NO** | test_extra_with_api_key_shaped_key_is_rejected_by_post_entry(negative이자 게이트 적색 재현 — docstring이 이 시나리오를 task-614 '결함 A'로 명시, '아래 테스트는 이제 통과한다'); test_ledger_event_rejects_amount_not_positive(파라미터 3종 negative); test_listing_service_rejects_negative_price(0행 생성 확인) | failure-injection(DB/어댑터 결함 시뮬레이션) 없음; 수치 성능 단언 없음 |
| 627 | ledger journal.append DB 왕복 축소(LC-17 결함 B) | 37a5375 | D2 | D1 | **NO** | test_journal_append_p95_under_30ms(수치 성능 단언, docstring이 실제 CI 적색-수정 사고 task-920 p95=15.97ms 통과/task-1029 재발 p95=172.7ms 실패/esc-ci-d5723ce4366d 종결을 상세 문서화=게이트 적색 재현으로 인정) | negative 3건 필요(현재 파일 내 0건, 성능 테스트 단독); failure-injection 없음 |
| 623 | LA-15 캔들 인제스트(application/ingest_candles + bitget_ingest_source) | fe9ea8f | D3 | D1 | **NO** | test_ingest_rolls_back_everything_on_audit_failure(RuntimeError 주입, candles+batch+quarantine 원자적 rollback=failure injection); test_ingest_quarantines_ohlc_violation_candle_and_stores_rest; test_ingest_quarantines_duplicate_conflict_candles; test_ingest_reingest_is_idempotent_and_creates_new_batch_row(replay/멱등 증명) | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 654 | LB-14 mark price·fx 소스 + mark_positions(스테일 → None) | 2df4ed7 | D3 | D1 | **NO** | test_stale_candle_clears_previous_mark_instead_of_keeping_it(경계, None으로 명시 초기화); test_missing_fx_keeps_mark_but_clears_unrealized(경계); test_fx_median_of_two_reference_legs(정확성); test_unknown_instrument_yields_none_mark(경계) | negative/거부 테스트(pytest.raises/DENY) 3건 필요(현재 0); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 701 | LC-9 결함 수정: post_entry가 journal.append의 replayed를 무시해 동시 재시도 시 잔액 이중적용 | e894792 | D3 | D1 | **NO** | test_post_entry_audit_failure_rolls_back_journal_lines_and_balance(_BoomAuditAppender RuntimeError=failure injection, journal/lines/balance 전체 rollback); test_post_entry_race_after_precheck_applies_balance_once(이 커밋이 고친 이중적용 버그를 racy wrapper로 재현=게이트 적색 재현, 단 순차호출이라 진짜 동시성은 아님); test_post_entry_digest_mismatch_denies_and_emits_denied_audit; test_post_entry_rejects_when_ledger_frozen | 수치 성능 단언 없음; race 테스트가 순차호출이라 진짜 다중 동시호출(asyncio.gather) 증명 아님; 적대적/위조시도 테스트 없음 |
| 713 | LA-20 kis_ingest_source(MockTransport 테스트, KRX 세션 갭 판정 통합) | e52a183 | D3 | D1 | **NO** | test_fetch_candles_rejects_unsupported_venue(UnsupportedVenueError); test_fetch_candles_rejects_unsupported_timeframe(UnsupportedTimeframeError); test_fetch_candles_rejects_naive_datetime(ValueError); test_krx_holiday_has_no_session_so_no_gap(경계) | failure-injection 없음(MockTransport는 정상응답만 시뮬레이션); 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 714 | LB-15 application/compute_daily_nav(멱등·체인 등식 위반 거부) | fc73e26 | D3 | D1 | **NO** | test_chain_break_when_rollforward_does_not_reconcile_is_rejected(NavChainBrokenError, 미저장 확인); test_stale_mark_on_open_position_rejects_nav(NavMarkUnavailableError); test_missing_cash_source_value_rejects_nav(NavCashUnavailableError); test_rerun_same_day_is_idempotent_no_duplicate_row(멱등) | 진짜 failure-injection 없음(누락데이터 페이크는 경계 검증이지 시뮬레이션 예외/DB오류가 아님); 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성(asyncio.gather) 증명 없음 |
| 712 | LA-18 quality_metrics + scheduler + main.py 배선 | 5f0b61e | D3 | D1 | **NO** | test_export_quality_metrics_isolates_one_symbol_failure(_FlakyCandleStore RuntimeError 주입=failure injection, 실패 심볼만 배제); test_scheduler_run_once_isolates_ingest_failure_and_exports_metrics; test_export_quality_metrics_skips_series_with_no_stored_candles | 수치 성능/지연/round-trip 단언 없음; 게이트 적색 재현(문서화된 실사고 연계) 없음; 적대적/replay/동시성 증명 없음 |
| 726 | LB-16 exchange_balance_source + reconcile_provider(거래소 잔고 대사) | f425070 | D3 | D1 | **NO** | test_adapter_exception_propagates(FakeAdapter(error=ConnectionError)+pytest.raises=failure injection); test_one_account_failure_does_not_block_another_accounts_reconciliation(한 계좌 ConnectionError 주입해도 다른 계좌 정상 대사) | negative 1건 더 필요(현재 2); 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 727 | LB-17 positions application/queries + scheduler + main.py 배선 | 8407701 | D3 | D1 | **NO** | test_one_account_mark_failure_does_not_block_another(FailingVenueMarkSource, 실패계좌 report.failed 반영 + 정상계좌는 계속 갱신); test_run_mark_forever_updates_snapshot_and_gauge_deterministically(유일한 happy path, asyncio.Event 기반 결정론) | negative 2건 더 필요(현재 1); 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 655 | LA-21 market_data 적대적 2파일 + perf 리플레이(§8.3/§8.4) | 3844e83 | D3 | D1 | **NO** | test_tamper_changes_replay_series_hash/test_tamper_breaks_batch_hash_reverification(WORM우회 탬퍼 시도, 다만 해시변화만 단언해 negative로 미집계); test_cross_tenant_batch_get_does_not_leak_existence(공격자뷰=missing뷰=None, fail-closed); test_round_trip_gate_detects_extra_query(_ChattyCandleStore 추가 round-trip 주입=failure injection) | negative 1건 더 필요(현재 2, cross_tenant+round_trip_gate뿐); 이 파일 자체엔 수치 시간 단언 없음(별도 리프 test_perf_replay_nightly.py 소관); 코드상 명시적 red-before/green-after 아티팩트 없음(커밋 메시지에만 서술) |
| 825 | LA-22 BatchRepository 교차 tenant 열람 차단(get에 tenant_id 필터) | 190dfea | D3 | D1 | **NO** | test_cross_tenant_batch_get_does_not_leak_existence(tests/adversarial/market_data/test_cross_tenant.py 유일 테스트, tenant_id 필터로 공격자/미상배치 모두 None) | negative 2건 더 필요(현재 1); failure-injection 없음; 수치 성능 단언 없음; 이 리프 파일 내 게이트 적색 재현 없음; D3용 적대적/replay/동시성 테스트 추가 필요 |
| 826 | LA-23 캔들 리플레이 성능(§8.4 5s 목표, 실측 66.3s) 진단·수정 | 87094a1 | D3 | D1 | **NO** | test_round_trip_gate_detects_extra_query(_ChattyCandleStore 추가 round-trip 주입=failure injection, round_trip_count 상한 초과 단언); test_replay_1day_1440_candles_under_0_5s/test_replay_43200_candles_under_5s(test_perf_replay_nightly.py, elapsed_seconds<0.5s/<20.0s 실단언); CI파일(test_perf_replay.py)은 happy-path 시간출력뿐, 비차단 | negative 2건 더 필요(현재 1); 명시적 red→green 회귀 증거 없음(docstring이 task-1405 사고를 서술만 함, 코드상 증명 아님); 적대적/replay/다중인스턴스-동시성 테스트 없음 |
| 656 | LA-16a 틱 배치 저장 계층(마이그레이션+TickIngestBatchResult+BatchRepository) | 130a1e1 | D3 | D1 | **NO** | test_create_tick_batch_duplicate_batch_id_raises(PK위반, DuplicateBatchError); test_get_tick_batch_cross_tenant_lookup_returns_none(교차테넌트/미상 모두 None='404 동형'); test_create_tick_batch_then_get_roundtrips_verdict_and_issues(happy path) | negative 1건 더 필요(현재 2); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 적대적/replay/동시성 증명 없음 |
| 842 | LA-16b application/ingest_ticks(trade_id 역행 배치 전량 REJECT) | 1be181e | D3 | D1 | **NO** | test_ingest_rolls_back_md_tick_on_audit_failure(_BoomAuditAppender RuntimeError=failure injection, md_tick+md_ingest_batch_tick 전체 rollback); test_ingest_rejects_regression_hidden_by_same_timestamp_tie(게이트 적색 재현, docstring이 task-1004 버그 인용); test_ingest_rejects_regression_when_same_trade_id_reused_at_earlier_time(게이트 적색 재현, task-1302 리뷰-REJECT 인용); test_ingest_rejects_whole_batch_on_trade_id_regression | 수치 성능/지연/round-trip 단언 없음(D2 'ALL of' 요건 미충족); 적대적/다중인스턴스 동시성 테스트 없음(재인제스트 테스트는 순차) |
| 951 | LC-16 결함 수정: get_balance 4개 조회를 단일 트랜잭션 스냅샷으로 묶어 위양성 409 DRIFT 제거 | 72a7700 | D3 | D1 | **NO** | test_get_balance_no_false_positive_drift_under_concurrent_commits(asyncio.gather 90-way 동시 writer/reader, D3급 동시성 증명, docstring이 task-951 결함과 직결=게이트 적색 재현); test_get_balance_raises_explicit_drift_error_instead_of_generic_failure(유일한 negative, WalletLedgerDriftError) | negative 2건 더 필요(현재 1); failure-injection(드리프트가 실 SQL 데이터 손상으로 생성되어 모의/시뮬레이션 예외 아님) 없음; 수치 성능 단언 없음; D2 하한부터 먼저 충족해야 D3 동시성 증거가 유효 |
| 1081 | LA-23b 리플레이 성능: 컬럼지향 내부 읽기 경로 + 규모별 성능 계약(ADR-2026-09-04-A) | 151ac74 | D3 | D1 | **NO** | test_round_trip_gate_detects_extra_query(_ChattyCandleStore 추가 round-trip 주입=failure injection+CI게이트); test_replay_43200_candles_under_5s(round_trip_count<=2 수치단언 + elapsed_seconds 출력); nightly 파일 test_replay_1day_1440_candles_under_0_5s(elapsed_seconds<0.5s 수치단언) | negative 2건 더 필요(현재 1, 나머지는 성능측정/게이트뿐) |
| 1178 | DC-7 domain/coverage/gaps.py (커버리지 갭 fail-closed 판정, LA-5 재사용) | b1dbedc | D3 | D1 | **NO** | test_mismatched_timeframe_span_raises_instead_of_empty_gap_list/test_span_venue_mismatched_with_calendar_raises(IndeterminateCoverageError fail-closed); test_naive_datetime_range_raises/test_reversed_range_raises; test_gap_list_is_sorted_and_order_independent_of_input(5가지 셔플 입력, 결정론) | failure-injection(모의 DB/어댑터 예외) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1321 | LA-16b 결함 수정: _known_trade_ids 멱등키를 복합키로 정렬(역행 REJECT 우회 차단) | a15a171 | D3 | D1 | **NO** | test_ingest_rolls_back_md_tick_on_audit_failure(_BoomAuditAppender RuntimeError=failure injection, rollback 증명); test_ingest_rejects_regression_when_same_trade_id_reused_at_earlier_time(이 커밋이 고친 task-1302 버그를 재현+거부=게이트 적색 재현); test_ingest_rejects_regression_hidden_by_same_timestamp_tie(task-1004 엣지케이스) | 수치 성능 단언(지연/round-trip 한계) 없음 |
| 1377 | LB-19 positions HTTP 읽기 API(positions·journal·nav) | d83d79c | D3 | D1 | **NO** | test_list_positions_other_tenant_account_is_404_isomorphic(공격자 테넌트가 타 계정 프로빙, 미상 리소스와 바이트동일 404=적대적); test_list_positions_portfolio_id_rejects_cross_tenant_scope_fail_closed(타 portfolio_id fail-closed 거부); test_nav_cross_tenant_is_404_and_bad_range_is_rejected; test_journal_rejects_malformed_cursor | failure-injection(모의 DB/어댑터 예외) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1376 | LA-24 market_data HTTP 읽기 API(candles get/replay·instruments list/aliases) + entitlement 포트 | 19f91aa | D3 | D1 | **NO** | test_cross_tenant_candles_is_404_isomorphic_with_unknown_symbol(교차테넌트 프로빙, 동형 404=적대적); test_internal_scope_source_denies_candles_display/replay(INTERNAL 재배포범위 소스 거부, 양쪽 read path); test_span_outside_coverage_is_409_data_coverage_missing; test_negative_missing_identifier_and_future_as_of_are_400 | failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1524 | LB-19 실라우트 프론트 배선: api-client positions 클라이언트 + PortfolioPage 실데이터 연결 | 607f832 | D3 | D1 | **NO** | positions.test.ts 404/403/malformed-envelope negative 3건; PortfolioPage.test.tsx '503 EXCHANGE_UNAVAILABLE은 ErrorMessage+재시도 버튼, 재시도가 refetch 호출'(어댑터/거래소 결함 시뮬레이션+배선 증명); PortfolioPage.test.tsx '400 VALIDATION_INVALID_FIELD+빈 details.fields는 서버 message 배너로 폴백(P0 무응답 회귀 방지)'(게이트 적색 재현, 실제 선행 회귀 문서화) | 프론트 스위트 전체에 수치 성능 단언 없음 |
| 1525 | LA-24 실라우트 프론트 정합: marketData 4경로 envelope=true 전환 | 561a9e6 | D3 | D1 | **NO** | '봉투 없는 응답은 LA-24 계약 위반이라 조용히 파싱하지 않고 throw'(계약위반 거부); '409 DATA_COVERAGE_MISSING 에러 봉투는 ApiError로 그대로 던진다'(조용한 빈채움 방지); '미지 timeframe 거부'(get/replay 양쪽) | failure-injection(모의 HTTP 오류 바디 외의 실 어댑터/네트워크 결함 시뮬레이션) 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1569 | CH-10 chart StrategyMarkers.tsx — PAPER 실행의 신호·체결 마커를 ChartPage에 표시 | 6f526b6 | D2 | D1 | **NO** | '404 RESOURCE_NOT_FOUND는 NotFoundState로 그리고 재시도 버튼을 두지 않는다'; '403 AUTH_TENANT_MISMATCH는 ForbiddenNotice로 그린다'(둘 다 mockRejectedValue(ApiError)) | negative 1건 더 필요(현재 2, D2 하한 기준); 단순 rejected-promise 모킹 이상의 failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1753 | LA-25b 현금배당의 원장 전기 | 4411dc8 | D3 | D1 | **NO** | test_amount_changed_between_calls_is_digest_mismatch(동일 idempotency key·다른 금액=탬퍼된 리플레이 거부, IdempotencyDigestMismatchError); test_duplicate_call_keeps_single_journal_entry(replay/멱등 증명, replayed=True+entry_id 1개); test_zero_amount_does_not_post/test_non_cash_dividend_action_type_is_rejected | failure-injection(모의 DB/어댑터 예외) 없음; 수치 성능 단언 없음; 게이트 적색 재현(문서화된 실사고 연계) 없음 |
| 1757 | LB-20 선물환·FX 헤지 손익 | 8651460 | D3 | D1 | **NO** | test_hedge_unrealized_pnl_rejects_mismatched_settlement_date/test_hedge_unrealized_pnl_rejects_mismatched_currency_pair(ForwardRateMismatchError); test_decompose_fx_pnl_rejects_mixed_quote_currencies(HedgeQuoteCurrencyMismatchError); test_unhedged_exposure_matches_hand_calc_to_four_decimals(정확성 검증이지 성능단언 아님) | failure-injection 없음; 수치 성능/지연 단언 없음(기존 수치검증은 정확성뿐); 게이트 적색 재현 없음 |
| 1752 | LA-25 공매도 차입·마진 원시타입 (locate 부재 해소) | d077492 | D3 | D1 | **NO** | test_pos_borrow_position_insert_with_nonexistent_tenant_id_raises_fk_violation(FK 거부); test_pos_borrow_position_cross_tenant_select_returns_zero_rows/test_pos_borrow_position_select_bound_to_other_tenant_excludes_all(RLS 테넌트격리, 2테넌트 교차증명) | 실 FK/RLS 제약 집행 외의 failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 2195 | DC-18a 커버리지 조회 API GET /v1/foundation/market-data/coverage (LA-24 read_api 확장) | ddacfca6 | D3 | D1 | **NO** | test_get_coverage_returns_empty_list_for_tenant_without_entitlement('negative (c)' 명시, 미인가 테넌트는 fail-closed 빈 목록); test_get_coverage_ignores_spans_from_other_venues(venue-scope 필터가 타venue 스팬에 우회되지 않음 증명) | negative 1건 더 필요(현재 2); failure-injection 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |

## 발행된 DEEPEN task

D3(축)/D2(비축) 미달 59건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 1, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 349 | 2943 |
| 374 | 2944 |
| 375 | 2945 |
| 379 | 2946 |
| 380 | 2947 |
| 381 | 2948 |
| 376 | 2949 |
| 390 | 2950 |
| 391 | 2951 |
| 410 | 2952 |
| 363 | 2953 |
| 411 | 2954 |
| 417 | 2955 |
| 412 | 2956 |
| 420 | 2957 |
| 452 | 2958 |
| 425 | 2959 |
| 424 | 2960 |
| 486 | 2961 |
| 453 | 2962 |
| 488 | 2963 |
| 418 | 2964 |
| 487 | 2965 |
| 450 | 2966 |
| 451 | 2967 |
| 615 | 2968 |
| 613 | 2969 |
| 489 | 2970 |
| 616 | 2971 |
| 624 | 2972 |
| 618 | 2973 |
| 626 | 2974 |
| 627 | 2975 |
| 623 | 2976 |
| 654 | 2977 |
| 701 | 2978 |
| 713 | 2979 |
| 714 | 2980 |
| 712 | 2981 |
| 726 | 2982 |
| 727 | 2983 |
| 655 | 2984 |
| 825 | 2985 |
| 826 | 2986 |
| 656 | 2987 |
| 842 | 2988 |
| 951 | 2989 |
| 1081 | 2990 |
| 1178 | 2991 |
| 1321 | 2992 |
| 1377 | 2993 |
| 1376 | 2994 |
| 1524 | 2995 |
| 1525 | 2996 |
| 1569 | 2997 |
| 1753 | 2998 |
| 1757 | 2999 |
| 1752 | 3000 |
| 2195 | 3001 |
