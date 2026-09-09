# DEPTH 감사 — PLT done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2730, qa-1)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/PLT-/`에 맞는 리프 전수 60건(60건 이하이므로 전수 대조).
- 축 하한: PLT(플랫폼)는 ADR-2026-09-09-C의 안전축 목록(R, L4, LA/LB/LC, FA, CM, EO, DC)에 없다 → **D2 하한**(안전축 D3 적용 대상 아님).
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명. 4개 요건 중 하나라도 빠지면 D2로도 인정하지 않고 D1(또는 D0)에 머문다(엄격 적용).
- 방법: 5개 병렬 감사 에이전트(worktree 내 read-only, 각각 하위 에이전트로 3~4건씩 추가 분할)가 각 리프의 커밋(`git show <commit> --stat`/diff)과 **현재** 테스트 파일을 직접 열어 negative 테스트 이름·실패 주입(monkeypatch/MagicMock/실DB 결함)·수치 성능 단언·게이트 적색 재현·D3급 적대적/replay/동시성 증거를 실측했다. 코드 수정 없음, pytest 미실행.

## 요약

- 전체 60건 중 **60건 전부 D2 하한 미달**(floor met=NO 60/60).
- 등급 분포: D0 3건(1092, 1240, 2149) · D1 57건 · D2 0건 · D3 0건.
- 가장 흔한 단일 누락 항목: **수치 성능/지연/round-trip 단언 부재**(60건 중 사실상 전건 — 있는 곳도 정적 설정값 비교이거나 unasserted timing). 두 번째로 흔한 결여는 **monkeypatch/MagicMock류 실패 주입 부재**(대부분 실DB/실HTTP negative뿐이고 의존성에 인위적 결함을 주입한 테스트가 없다).
- **D2에 가장 근접한 리프(4요건 중 3개 충족, 성능 단언만 결여)**: 459(PLT-38), 846(PLT-39), 933(PLT-37), 1019(PLT-33), 1105(PLT-40c), 1104(PLT-34), 1216(PLT-16), 1544(PLT-34 결함수정), 933·1105·1216은 게이트 적색 재현이 공유 스크립트/일반 회귀 가드에 준하는 수준이라 다소 느슨하게 인정한 것이다.
- D3급 개별 증거(동시성/리플레이/적대적)는 여럿 관찰됐지만(852 로그인 잠금 동시경합, 853 레이트리밋 동시폭주, 940/1544/1104 replay-idempotency, 1091 RLS 적대 테스트, 1103 cross-tenant 403 등) D2 4요건을 전부 채우지 못해 D3로 승격된 리프는 0건이다.
- 순수 리팩터(믹스인 분할·type: ignore 제거) 리프 3건(1032, 1011, 1105 일부)은 런타임 동작 변화가 없어 신규 D2급 테스트를 작성할 대상 자체가 구조적으로 없다.
- 전용 테스트 파일 자체가 없는 리프 3건: 460(PLT-36 tests/support/db.py), 938(PLT-08 4루프·scheduler 배선), 1240(PLT-37 프론트 커버리지 래칫 .mjs) — D0/D1 경계.
- 문서/프로세스 전용이라 코드 증빙이 원천적으로 불가능한 리프 1건: 2149(OPS-2 Copilot 파일럿, PR조차 생성되지 못해 관찰 자체가 중단됨) — D0.
- 커밋 참조 불일치 2건 발견(감사 중 실제 구현 커밋으로 대체 판정): 939(PLT-09, ledger 커밋 5171b23은 main.py 300줄 캡 후속수정뿐 — 실제 기능 커밋 c0b7dea7로 판정), 1019(PLT-33, ledger 커밋 dace2f7는 라인캡 압축뿐 — 실제 기능 커밋 3fed8c6c로 판정), 1103(PLT-29, ledger 커밋 85789df는 main.py 배선 리버트뿐 — 실제 기능 커밋 efdce371로 판정), 2149(ledger 커밋 e6c9c34는 저장소에 존재하지 않음 — 실제 문서 커밋 9a8a0571로 판정).
- SecretHandle(932, PLT-32 "핸들 종료 후 0채움")은 후속 커밋 dcd2dda3(task-1722, "무consumer 정리")이 코드와 테스트를 통째로 삭제해 현재 코드베이스에 증빙 대상 자체가 존재하지 않는다.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2/D3 미달 근거) |
|---|---|---|---|---|---|---|---|
| 338 | PLT-15 잔여 금전 라우트 Idempotency 이관(충전 topup-requests·포트폴리오 rebalance·실행 convert) | e5684d4 | D2 | D1 | **NO** | clients/idempotency.test.ts 10 tests(키 전달 확인) + WalletPage.test.tsx·ExecutionControlPage.test.tsx·PortfolioPage.errors.test.tsx에 negative 다수(409 STATE_CONCURRENCY_CONFLICT 후 재제출 시 새 Idempotency-Key 재발급, 429 RATE_LIMIT_EXCEEDED 시 키 폐기, 400 VALIDATION_IDEMPOTENCY_KEY_REQUIRED); mockRejectedValue(ApiError)·fetchMock 409/429/503/502로 어댑터 실패 주입 다수 | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 457 | PLT-04 메트릭 이름 단일출처 + MetricsPort/NullMetrics(prometheus 어댑터) | 2e0488f | D2 | D1 | **NO** | test_metrics_port.py 7 tests + test_metric_names.py 5 tests; negative는 test_prometheus_metrics_rejects_relabeling_same_name 1건(pytest.raises(ValueError)) | negative<3(1건뿐); 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 459 | PLT-38 마이그레이션 체인·Zone diff 체크 스크립트(듀얼헤드/FROZEN diff 차단) | 733634c | D2 | D1 | **NO** | test_check_scripts.py 12+5 tests, negative 8건 모두 main() exit code==1 단언; 실패 주입(오타 parent·병합 리비전 제거로 fail-closed 확인); 게이트 적색 재현: test_removing_task_2099_merge_revision_reproduces_dual_head_gate_failure가 실제 task-2099 병합 리비전 파일 제거로 원 CI-red 재현 | 수치 성능 단언 없음(나머지 3요건은 충족, D2에 가장 근접) |
| 458 | PLT-31 KeyRing(키 버전·scope) + encryption 포맷 aios1$kid$... (LIVE 키 PAPER 기동 거부) | 4b2015f | D2 | D1 | **NO** | test_key_ring.py 17개+test_encryption_format.py 9개=26개; negative≥10(KeyRingConfigError 6건, FrozenZoneLiveModeBlockedError 4건, InvalidTag/UnknownKeyIdError 2건 등) | 실패 주입 없음(직접 입력검증뿐); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 493 | §3.7 PLT-15 잔여 라우트 멱등 클라이언트: /v1/foundation/paper-control 5개 + /v1/foundation/trust/consents | 5f7c00b | D2 | D1 | **NO** | foundation.test.ts 13개; negative≥3(키 없이 호출 시 런타임 거부, 빈 문자열 키 형식검증 거부, 같은 키·다른 body는 서버 왕복 전 차단) | 실패 주입 없음(stubFetch가 항상 정상 응답); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 460 | PLT-36 테스트 DB 격리(tests/support/db.py + xdist worker별 DB·시드 복원) | d0b948a | D2 | D1(사실상 근접 D0) | **NO** | tests/support/db.py를 직접 겨냥한 전용 테스트 파일 부재; 커밋 메시지의 수동 실행 기록만 존재 | negative 없음; asyncpg ObjectInUseError 재시도 monkeypatch 검증 없음; 클론 소요시간 단언 없음; 인시던트 고정 재현 게이트 없음 — 사실상 0/4 |
| 816 | PLT-01 관측성 컨텍스트(request_id/trace_id/tenant_id contextvar 전파) | 77bfdb4 | D2 | D1 | **NO** | test_context.py 12개; negative=2(test_request_context_is_frozen, test_bind_exception_still_restores_context) | negative<3(2건뿐); 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 818 | PLT-06 event_bus 봉투에 trace 컨텍스트 전파(envelope.py + in_process.py) | 2e8c8d4 | D2 | D1 | **NO** | test_event_bus_trace.py 4개; 정상 전파 1건 + 컨텍스트 비오염 확인 3건 | negative 0건; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 817 | PLT-02 로깅 레닥션·필드 집합(redaction.py + fields.py) | ff4b86e | D2 | D1 | **NO** | test_fields.py(8개)+test_redaction.py(15개); negative=1(test_structured_log_line_rejects_unknown_level) | negative<3(1건뿐); 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 819 | PLT-11 알림 규칙 yaml + 런북 RB-01~08 + 메트릭 참조 검증 테스트 | d2102c1 | D2 | D1 | **NO** | test_alert_rules_reference_known_metrics.py 9종(parametrize 포함 20개); negative=1(test_unknown_metric_token_is_rejected) | negative<3(1건뿐); 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(정적 검증뿐) |
| 845 | PLT-03 logging schema.py를 fields.py 단일출처로 위임 + QueueHandler | e8cb56e | D2 | D1 | **NO** | test_schema.py 12개; 전부 happy-path/필드값 단언 | negative 0건; 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 846 | PLT-39 릴리스 게이트 체크(config/release_gates.yaml + check_release_gate.py) + gitleaks 규칙 보강 | 0b0fc3c | D2 | D1 | **NO** | test_check_release_gate.py: test_internal_paper_lists_missing_evidence_against_real_repo(exit 1)/test_internal_development_passes_against_real_repo(exit 0)로 게이트 적색/녹색 재현; negative 5건 이상(exit_code==1) | 실패 주입 없음(순수 파일존재 검사); 수치 성능 단언 없음 — negative·게이트 적색 재현은 충족, D2에 근접 |
| 851 | PLT-10 도메인 계측 지점(order_service submit/gate/position_ledger/reconcile + submit_paper_intent) | 592311c | D2 | D1 | **NO** | test_instrumentation_points.py 8개; negative=1(test_submit_order_denied_records_gate_and_denied_metrics, OrderDeniedByRiskGateError); `_ExplodingPool`로 DB 접근 전 계측 완료를 증명하는 테스트=느슨한 실패 주입 1건(도메인 거부는 아님) | negative<3(도메인 거부 1건뿐); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 852 | PLT-22 로그인 실패 잠금 원자화(lockout.py + auth_service 수정, 423 retry_after) | 5f4f6da | D2 | D1 | **NO** | test_lockout_atomic.py 6개+test_auth_service.py 2건; negative=3(test_register_failed_attempt_for_unknown_user_raises 등); test_concurrent_failed_logins_lock_and_expose_retry_after=asyncio.gather 10-way 동시성 손실 0 증명(D3급); retry_after 상한 단언이 실제 CI 적색 esc-ci-401c16dd420e를 재현·고정(커밋 065bdab7 명시) | monkeypatch/MagicMock 기반 failure-injection 없음(전부 실DB); 수치 성능 단언 없음 |
| 850 | PLT-05 요청 컨텍스트 미들웨어(tenant_binding·instrument + main.py 등록 교체) | 40f8586 | D2 | D1 | **NO** | test_middleware_trace.py 6개, 전부 happy-path/행위 확인; instrument.py·tenant_binding.py는 "후속 리프가 배선할 신규 유틸"이라 전용 테스트 0건 | negative 0건; 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 — 3건 중 가장 얕음 |
| 904 | PLT-31 후속 경화 2건: KeyRing 예외 메시지 키 원문 노출 제거 + AIOS_RUNTIME_MODE fail-closed | 401c16d | D2 | D1 | **NO** | test_key_ring.py 19개, negative≈14(KeyRingConfigError 다수, RUNTIME_MODE 오타·오철자 거부 등); 키 원문 비노출 직접 검증 2건 | 실패 주입 없음(순수 파서, monkeypatch 대상 의존성 자체 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 905 | PLT-16 API 버저닝 + OpenAPI 스냅샷·호환성 체크(export_openapi/check_openapi_compat) | f800c1a | D2 | D1 | **NO** | test_openapi_compat.py 22개(6-case parametrize 포함), negative≥15; 게이트 적색 재현 2건(test_cli_exit_one_and_prints_removed_field_name, test_reviewer_password_str_to_int_regression_now_fails=task-1213 리뷰어 재현) | 실패 주입 없음(순수 JSON 비교/서브프로세스뿐); 수치 성능 단언 없음 |
| 932 | PLT-32 봉투 암호화 + SecretRef/SecretHandle(seal/open/rewrap, 핸들 종료 후 0채움) | 7546c50 | D2 | D1 | **NO** | test_envelope.py 8개, negative=5(UnknownKeyIdError/InvalidTag 계열); test_secret_ref.py(후속) 11개, negative=8 | 실패 주입 없음(암호 라이브러리 자체 동작이지 monkeypatch 기반 아님); 수치 성능 단언 없음; 게이트 적색 재현 없음; **SecretHandle(0채움) 부분은 후속 커밋 dcd2dda3(task-1722)이 "무consumer"로 코드·테스트를 통째로 삭제해 증빙 자체가 소실** |
| 853 | PLT-25 레이트 리밋(policy·limiter + api/middleware/rate_limit.py + main.py 등록) | d010555 | D2 | D1 | **NO** | test_rate_limit_storm.py 5개, negative=5(429 거부 전부); test_concurrent_storm_admits_exactly_limit_requests_no_partial_overrun=asyncio.gather 동시폭주(limit+5)에 정확히 limit개만 허용(D3급 근접) | 실패 주입 없음(InMemoryTokenBucket 직접 호출뿐); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 933 | PLT-37 커버리지 래칫(scripts/coverage_ratchet.py + coverage-baseline.txt + CI 스텝) | f8f2efb | D2 | D1 | **NO** | test_coverage_ratchet.py(후속 갱신), negative≥8(pytest.raises 4건+exit=1 다수); main() exit 1/0 게이트 재현; test_missing_coverage_xml_fails_with_not_generated_message 등이 실제 CI 적색 사건(ci:753a88c6aeb5)을 재현 | 실패 주입 없음(malformed 입력파일은 negative로만 카운트); 수치 성능 단언 없음 — negative·게이트 적색 재현은 충족, D2에 근접 |
| 938 | PLT-08 백그라운드 루프 헬스(loop_health.py + base_loop 계측 + 4루프·scheduler 배선) | fcd2d16 | D2 | D1 | **NO** | test_loop_health.py 12개, 전부 happy path(pytest.raises 0건) | negative 0건; 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음; 핵심인 background_loops.py 4루프·main.py 스케줄러 배선에는 전용 테스트가 전무(grep 확인) |
| 906 | PLT-07 audit_log.trace_id 마이그레이션 + audit_log·record_command_event trace 전파 | fa553a5 | D2 | D1 | **NO** | test_middleware_trace.py: audit_log·audit_event·응답헤더 trace_id 3자 일치 단언; "_negative" 명명 테스트 1건은 실제로는 값-차이 확인일 뿐 raise/거부가 아님 | negative 실질 0건; 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음 — 3건 중 가장 얕음 |
| 940 | PLT-14 멱등 키 scope·body digest(contracts/idempotency.py + core/idempotency 수정 + M2) | f1d1d35 | D2 | D1 | **NO** | test_idempotency_digest.py negative=3(409 INTEGRITY_IDEMPOTENCY_CONFLICT, 400 VALIDATION_IDEMPOTENCY_KEY_REQUIRED×2); test_first_call_executes_compute_and_replay_returns_same_response=리플레이 증명(D3 근접) | 실패 주입 없음(DB/adapter 장애 시뮬레이션 없음); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 939 | PLT-09 헬스체크 엔드포인트(routers/health.py: /readyz·/livez + /metrics 토큰 강제) | 5171b23(실제 기능 커밋 c0b7dea7) | D2 | D1 | **NO** | test_health_endpoints.py negative=4; test_readyz_returns_503_when_db_pool_broken이 `_BrokenPool`(ConnectionError)로 DB 풀 장애를 실제 주입=실패 주입 충족 | 수치 성능 단언 없음(threshold==30.0은 정적 설정값 비교뿐); 게이트 적색 재현 없음 — 2/4 충족, 가장 근접한 축 중 하나 |
| 1010 | PLT-26 M4 tenant_and_membership 마이그레이션 + trust 도메인 규칙(membership) | 3582714 | D2 | D1 | **NO** | test_migration_tenant_membership.py: happy-path 왕복+backfill; negative=1(test_active_membership_unique_per_tenant_subject, UniqueViolationError) | negative<3(1건뿐); DB unique violation은 실패 주입으로 불인정; 수치 성능 단언 없음; 게이트 적색 재현 없음; 핵심 순수함수(rules.py: is_membership_transition_allowed 등)는 단위테스트 자체가 없음 |
| 1002 | PLT-17 레거시 라우터 봉투·예외 이관 1/5(auth·users·exchange_credentials + 강제 테스트 2종) | d92ad68 | D2 | D1 | **NO** | test_envelope_everywhere.py negative(4xx) 다수(≥3); test_exception_mapping.py 단위테스트 5건; test_no_raw_http_exception.py 자기검증이 게이트 적색 재현에 준함 | 실패 주입 없음; 수치 성능 단언 없음 |
| 1032 | PLT-40a 선행: bitget 3개 믹스인 P6 분할(futures_trading 357 / market_data 735 / trading 314 → 파일당 ≤300줄) | 0b12638 | D2 | D1 | **NO** | 순수 이동 리팩터(공개 시그니처·동작 변경 0); 기존 test_bitget_ws_messages.py 등 3파일이 재-import 경로로 계속 커버 | 신규 negative/실패주입/perf/gate 테스트가 구조적으로 있을 수 없는 순수 리팩터; 새로 분리된 trading_plan_mixin.py/futures_plan_mixin.py는 여전히 전용 테스트가 없음 |
| 1018 | PLT-23 M3 auth_session 마이그레이션 + session_repository + tokens(JWT kid 회전·refresh 회전) | 13fad35 | D2 | D1 | **NO** | test_tokens.py negative 7건 이상(pytest.raises); test_rotate_refresh_reuse_of_old_hash_revokes_session이 실DB 조건부 UPDATE 충돌로 탈취 refresh 재사용을 재현·revoke(실패 주입에 준함); test_revoke_is_idempotent | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1001 | PLT-40a bitget 믹스인 type: ignore 제거(common/http_client.py Protocol + budget 체크) | 1cee5b5 | D2 | D1 | **NO** | 믹스인 자체는 순수 타입 리팩터(기존 bitget 280개 테스트 무수정 통과 주장); test_check_type_ignore_budget.py의 test_increase_beyond_budget_fails/test_equal_to_budget_passes_and_keeps_budget이 게이트 스크립트를 red/green 재현; negative 1건(test_read_budget_malformed_raises) | negative<3(1건뿐); 실패 주입 없음; 수치 성능 단언 없음 |
| 1009 | PLT-18 레거시 라우터 봉투·예외 이관 2/5(marketplace·strategy_builder·suitability) | 28b666c | D2 | D1 | **NO** | test_envelope_everywhere.py negative 3건(400/404); test_no_raw_http_exception.py MIGRATED_ROUTERS 확장+자기검증이 게이트 적색 재현에 해당 | 실패 주입 없음(monkeypatch/MagicMock 미사용 확인); 수치 성능 단언 없음 |
| 1017 | PLT-20 레거시 라우터 봉투·예외 이관 4/5(notifications·alerts·device_tokens·wallet) | f19d0d2 | D2 | D1 | **NO** | test_envelope_everywhere.py negative 4건(404/400); test_no_raw_http_exception.py MIGRATED_ROUTERS 확장+자기검증이 게이트 적색 재현에 해당 | 실패 주입 없음; 수치 성능 단언 없음 |
| 1011 | PLT-40b kis 믹스인 type: ignore 9건 제거(공용 HttpClient Protocol 적용) | 2f12f26 | D2 | D1 | **NO** | 순수 타입 리팩터(런타임 동작 무변경); 커밋 diff에 테스트 파일 변경 0건; 기존 kis 66개·bitget 197개 테스트가 무수정 통과 주장 | 신규 negative/실패주입/perf/gate 테스트 전무 — 순수 타입 전용 리팩터라 D2 요건 자체가 구조적으로 적용 불가 |
| 1016 | PLT-19 레거시 라우터 봉투·예외 이관 3/5(executions·portfolio·reports) | 568a60e | D2 | D1 | **NO** | test_envelope_everywhere.py negative 2건 + 기존 test_executions_router.py/test_portfolio_router.py negative 다수(401/400); test_no_raw_http_exception.py MIGRATED_ROUTERS 확장 | 실패 주입 없음(fake가 항상 성공만 반환); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1076 | PLT-27 멤버십 저장소(ports/membership_repository + postgres 어댑터) | 109514b | D2 | D1 | **NO** | test_membership_repository.py negative 3건, 전부 pytest.raises(ConcurrencyConflictError)(실UNIQUE 위반=DB오류 failure injection 포함, 교차 테넌트 차단도 확인) | 수치 성능 단언 없음; 게이트 적색 재현 없음; D3 동시성/적대적 증명 없음 |
| 1019 | PLT-33 M6 exchange_credentials key_version·scope(UNIQUE 교체) + credential_service/resolver 이관 | dace2f7(실제 기능 커밋 3fed8c6c) | D2 | D1 | **NO** | test_log_leak.py의 _FakeAdapter(fail=True) RuntimeError·kid 소실 후 복호 실패=실패 주입; negative 6건 이상; test_exchange_credentials_router.py가 docstring에 "docs/RED_TEAM_FINDINGS.md #02 회귀"를 명시해 게이트 적색 재현으로 인정 | 수치 성능 단언만 부재 — 4요건 중 3개 충족, 60건 중 D2에 가장 근접 |
| 1075 | PLT-24 로그인·리프레시·로그아웃 유스케이스(TokenPairResponse + 세션 회전·재사용 탐지 배선) | e0eb498 | D2 | D1 | **NO** | test_login_refresh_logout.py negative 5건; session_repository.rotate_refresh()의 실DB 조건부 UPDATE 충돌=failure injection; test_refresh_reuse_of_old_refresh_token_revokes_session=D3급 리플레이 증명(단 D2 미달로 미반영) | 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1090 | PLT-28 테넌트 컨텍스트 해석(resolve_tenant_context + foundation_deps 배선) | c05a58f | D2 | D1 | **NO** | test_resolve_tenant_context.py+test_foundation_deps.py+test_membership_admin.py; negative=3(TenantMismatchError, 400 malformed header, 403 cross-tenant) | 실패 주입 없음(멤버십 조회 mock/monkeypatch 없음, 전부 실비즈니스 negative); 수치 성능 단언 없음; 게이트 적색 재현 없음 |
| 1092 | PLT-41 ruff 규칙군 확장(S,BLE,ARG,PGH,T20) + 위반 정리 | f8b3110 | D2 | D0 | **NO** | 커밋이 pyproject.toml만 수정(코드/테스트 변경 0); 이 규칙 확장을 검증하는 테스트·게이트 스크립트가 저장소 어디에도 없음(grep 확인) | negative/실패주입/수치성능/게이트 적색 재현 4항목 전부 근거 없음(지목 가능한 테스트 함수 자체가 없음) |
| 1091 | PLT-30 테넌트 RLS(roles.sql + M5 rls_policies + core/db/tenant_scope.py) | 8c9fe01 | D2 | D1 | **NO** | test_rls_foundation.py+test_rls_bypass_denied.py("적대적" 명시)+test_rls_legacy_not_enabled.py 총 19개; negative=4(모두 asyncpg.InsufficientPrivilegeError); GUC 누수 방지 적대적 테스트 2건 | 실패 주입 없음(실제 RLS/권한 자체가 대상); 수치 성능 단언 없음; 게이트 적색 재현 없음(alembic 왕복은 게이트 재현 아님) |
| 1074 | PLT-21a 레거시 라우터 봉투·예외 이관 5/5 전반: admin.py raw HTTPException 제거 | ad74c3c | D2 | D1 | **NO** | test_envelope_everywhere.py+test_no_raw_http_exception.py(회귀 가드); 기존 test_admin_router.py negative 8건(401/403/400/409, 실DB+실HTTP) | 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(AST 스캔은 red/green 재현 아님) |
| 1103 | PLT-29 멤버십 grant/suspend/revoke 유스케이스 + trust_memberships 라우터 + 적대 테스트 | 85789df(실제 기능 커밋 efdce371) | D2 | D1 | **NO** | test_membership_admin.py negative 8건(MFA 미검증, 중복 grant, last-owner 강등/삭제 거부, cross-tenant 403 등, 실DB); test_foundation_trust_memberships_router.py 2건(401/403) | 실패 주입 없음(전부 실DB); 수치 성능 단언 없음; 게이트 적색 재현 없음; "적대 테스트"는 cross-tenant 403 1건뿐이라 D3 근거로는 얕음 |
| 1105 | PLT-40c nh 믹스인 type: ignore 4건 제거(common http_client Protocol 재사용) + budget 감소 | cda979f | D2 | D1 | **NO** | test_nh_http_client_protocol.py+test_nh_adapter.py negative 20건 이상; 실패 주입 2건(ConnectionClosed 재연결, httpx.ConnectError 재시도); test_check_type_ignore_budget.py가 budget 게이트 fail/pass 양쪽 단언(PLT-40 공유 스크립트) | 수치 성능 단언 없음(지연/처리량 단언 전무) — 3/4 충족, D2에 근접 |
| 1104 | PLT-34 자격증명 키 회전 스크립트(rotate_credential_keys.py) + 멱등 재실행 테스트 + RB-05 | 1690707 | D2 | D1 | **NO** | test_key_rotation.py(10)+test_key_rotation_negative.py(3, 후속 task-1544); negative=5; 실패 주입 3건(leaky_encrypt·failing_audit monkeypatch, orphan kid); 게이트 적색 재현 1건(평문 유출 버그 red→green); D3급 멱등 재실행 증명 2건 | 수치 성능 단언 전무 — 4요건 중 1개만 미충족, D2에 근접 |
| 1189 | P0 CI 적색 3건 진단·수정(PLT-30 RLS 도입 후 WORM 롤 negative 2건 + MFA 재설정 1건) | 0e77d6b | D2 | D1 | **NO** | test_db_roles.py+test_rls_legacy_not_enabled.py+test_auth_router.py negative≥5(WORM/RLS pytest.raises); 게이트 적색 재현 명확(esc-ci-cbb8b9c62497를 코드주석·커밋메시지에 3회 명시, RLS ENABLE 후 조용한 0행 필터링을 재현·수정) | 실패 주입 없음(monkeypatch 없이 전부 실DB/RLS 자연발생 경로); 수치 성능 단언 없음 |
| 1108 | PLT-21b-1 foundation 라우터 봉투·예외 이관 (connections·mandates·evidence) | 837f638 | D2 | D1 | **NO** | test_no_raw_http_exception.py(AST 스캔, 실질 게이트)+test_envelope_everywhere.py; negative≥6(401/404/403×2); test_start_on_already_running_deployment_is_409_not_500=실버그 회귀(게이트 적색 재현) | 실패 주입 없음(monkeypatch/MagicMock 없음); 수치 성능 단언 없음; connections.py는 라우터 레벨 HTTP 통합테스트 파일 자체가 없음 |
| 1216 | PLT-16 계약가드 사각지대 수정: check_openapi_compat이 anyOf(Optional) 내부 type·nullable 변경을 못 잡음 | e1af627 | D2 | D1 | **NO** | test_openapi_compat.py(444줄, 22개+6-case parametrize); negative≈18; test_reviewer_password_str_to_int_regression_now_fails=task-1213 리뷰어 재현(게이트 적색 재현); test_cli_fails_when_baseline_missing=약한 실패 주입 | 수치 성능 단언 전무(elapsed/perf_counter 전무, grep 확인) — 4요건 중 1개만 미충족, D2에 근접 |
| 1217 | PLT-21b-2 foundation 라우터 봉투·예외 이관 (paper_control·performance·reconciliation) | e4c9bd6 | D2 | D1 | **NO** | test_envelope_everywhere.py+test_no_raw_http_exception.py(정적 회귀 가드); test_start_on_already_running_deployment_is_409_not_500(실버그 재현, 게이트 적색 재현) | 실제 negative(에러코드) 테스트 1건뿐(≥3 미달); 실패 주입 없음; 수치 성능 단언 없음; performance.py·reconciliation.py는 HTTP 경계 테스트 자체가 없음 |
| 1240 | 프론트 커버리지 래칫(PLT-37 대응): vitest coverage + baseline + quality.yml 배선 | 03d2b78 | D2 | D0 | **NO** | scripts/frontend_coverage_ratchet.mjs(131줄)와 quality.yml 배선, coverage-baseline.txt(88.40) 등 config는 완비 | 이 스크립트를 겨냥한 테스트 파일이 전혀 없음(repo 전체 grep 0건) — happy path 단위테스트조차 없어 D1 미충족, negative/실패주입/성능/게이트 전부 없음 |
| 1218 | PLT-21b-3 foundation 라우터 봉투·예외 이관 (risk_gate·trust·validation) | b681258 | D2 | D1 | **NO** | test_foundation_trust_router.py+test_foundation_validation_router.py+test_foundation_paper_control_risk_gate_router.py negative 6건 이상(401/404/403/409); test_no_raw_http_exception.py/test_envelope_everywhere.py 회귀 가드 | 실패 주입 없음(monkeypatch/MagicMock으로 risk_gate·trust·validation 의존성 예외를 강제한 테스트 전무); 수치 성능 단언 없음; 게이트 적색 재현 없음(409 회귀는 리뷰 중 발견한 애플리케이션 버그일 뿐) |
| 1324 | §3.4 auth 클라이언트 ↔ 머지된 PLT-24 라우터 정합(TokenPairResponse·/auth/refresh·/auth/logout-all) | 9ed1b9f | D2 | D1 | **NO** | clients/auth.test.ts negative 6건 이상(TokenPairFormatError, 401 재시도없음 등); mockRejectedValue(TypeError)로 네트워크 실패 주입; sessionLifecycle.test.ts 동시 401 3건→refresh 1회(single-flight, D3급 동시성) | 수치 성능 단언 없음; 게이트 적색 재현 없음(스냅샷 드리프트 가드는 CI-red 재현 아님) — D2 미달로 D3 승격 불가 |
| 1309 | PLT-15 미소비 클라이언트 배선: 페이퍼 배포 제어 화면(list + request/start/pause/resume/stop) | 54cc6f5 | D2 | D1 | **NO** | PaperDeploymentsPage.test.tsx negative 3건(404/기타에러/403) + ECONNRESET 실패 주입; clients/foundation.test.ts negative 다수; "같은 키·같은 body 재전송" replay 증명(D3급) | 수치 성능 단언 없음; 게이트 적색 재현 없음(드리프트 가드는 CI-red 재현 아님) — D2 미달로 D3 승격 불가 |
| 1333 | §3.7 금전 라우트 멱등 부착 전수 회귀 가드(소스 스캔 + PLT-15 라우트 목록 양방향 대조) | 0e8283c | D2 | D1 | **NO** | negative fixture 2건(위반 주입 시 검출, 표식 누락 검출) + 정상 fixture 통과 확인; apiPaths.ts↔clients/*.ts 양방향 완전일치 스캔 | negative<3(2건뿐); 실패 주입 없음(순수 소스 스캔이라 외부 의존성 자체가 없음); 수치 성능 단언 없음; D3 증명 없음 |
| 1544 | PLT-34 결함 수정(리뷰 task-1542 REJECT): RB-05 실행 경로 배선(-m + subprocess CLI 테스트) + 평문 지역변수 제로화·예외 레닥션 + failure 메트릭 DB 오류 포함 + 명세 §2.2/§8 표기 정합 | 6db8ce8 | D2 | D1 | **NO** | negative 5건(pytest.raises); 실패 주입 2건(monkeypatch로 encrypt가 평문 포함 RuntimeError, record_audit_log을 존재하지 않는 테이블로 몽키패치); 게이트 적색 재현 2건(리뷰 task-1542 REJECT 사유 재현, RB-05 CLI ModuleNotFoundError 결함 재현); D3급 replay(test_second_run_is_idempotent) | 수치 성능 단언 없음 — 4요건 중 1개만 미충족, D2에 가장 근접한 리프 중 하나(D3 replay 증거 有이나 D2 미달로 미반영) |
| 1615 | P0 CI 적색(esc-ci-48db4810270d): tests/unit/services 5건 ERROR — DB 의존 단위테스트가 .env/DATABASE_URL 직접 참조(PLT-36 격리 우회) | a1718de | D2 | D1 | **NO** | negative 3건(pytest.raises, UnmappedSafetyScopeError·MalformedScopeRefError); tests/unit→tests/integration 이동으로 PLT-36 격리 우회 자체는 해소 | 실패 주입 없음(3건 negative는 입력검증이지 인프라 결함 주입 아님); 수치 성능 단언 없음; 게이트 적색 재현 없음(CI-red 근본원인인 .env 직접참조 fixture ERROR 자체를 재현하는 신규 테스트 없음) |
| 1815 | PLT-45 OMS IdempotencyScope를 OrderIdempotencyScope로 개명 (동명이의 제거) | f6986bb | D2 | D1 | **NO** | 순수 개명 커밋(src 2개·tests 7개 파일 식별자만 치환, assert 신설 없음); 사전 존재 테스트(test_idempotency.py negative 2건, test_concurrent_submit.py 50-way 동시성)가 개명에 맞춰 갱신됨 | 이번 리프에서 신규 negative·실패주입·성능·게이트 테스트가 전혀 없음(diff는 이름 치환뿐) |
| 1923 | P0 CI 적색: ruff 9건 — scripts/check_audit_regressions.py가 PLT-41 규칙군(E501/BLE001 등)에 걸린다 | a7b7951 | D2 | D1 | **NO** | E501 8건 변수추출, BLE001 1건 `_invoke()`/`_CheckerFailure` 분리; 커밋 메시지는 수동 red→green 확인을 서술하나 테스트 파일 변경은 0건 | 이번에 바뀐 run()/`_invoke`/`_CheckerFailure` 격리 로직이나 check_duplicate_type_names를 겨냥한 테스트가 전무; 신규 negative·실패주입(자동화)·성능·게이트 재현 테스트 없음 |
| 1759 | PLT-44 type: ignore 감축 219 -> 85 (하루에 두 번 늘었다) | b395748 | D2 | D1 | **NO** | ws_session.py `_decode` isinstance 가드, statement_projection.py 값검증 함수, identity.py `_require()` 헬퍼 추가; test_ws_session.py(20개)·test_identity.py(4개)는 이 커밋 이전부터 있던 테스트가 재사용됨(변경 0건) | statement_projection.py는 전용 테스트 파일이 전혀 없음; identity.py `_require()`의 raise 분기를 겨냥한 테스트 없음; ws_session.py 신규 isinstance 분기를 겨냥한 테스트 없음; 신규 실패주입·성능·게이트 재현 테스트 없음 |
| 1756 | PLT-43 직무분리 원시타입 (CM-5·브레이크글라스가 각자 재발명 중) | 203c1ca | D2 | D1 | **NO** | test_segregation_of_duty.py 4개, negative=2(SegregationOfDutyViolation×2); test_segregation_of_duty_static.py 정적 회귀 가드 2건(인라인 재발명 부재 스캔, import 확인) | negative<3(2건뿐); 실패 주입 없음; 수치 성능 단언 없음; 게이트 적색 재현 없음(정적 스캔은 회귀 가드일 뿐) |
| 1956 | PLT-16 후속: check_openapi_compat이 $ref 경유(ApiResponse 봉투·array items) 하위 스키마 변경을 전혀 비교하지 않는다 | 4333920b | D2 | D1 | **NO** | test_openapi_compat.py negative≥5($ref 경유 프로퍼티 삭제/타입변경/enum축소, 배열 items 경유); test_self_referential_ref_cycle_terminates_without_error; 게이트 적색 재현 2건(task-1213 리뷰어 재현 CLI, main() exit 0/1) | 실패 주입 테스트 없음(파일 전체 monkeypatch/MagicMock 0건, grep 확인); 수치 성능 단언 없음 |
| 2149 | OPS-2 Copilot 레인 파일럿 1건 — PLT-44 type: ignore 감축 배치를 Copilot에 보내 머지까지 검증 | 9a8a0571(제시된 e6c9c34는 저장소에 존재하지 않음) | D2 | D0 | **NO** | ADR-2026-09-08-B에 파일럿 결과 절 추가 — gh auth status·gh api user가 "not logged in"으로 실패해 PR 생성 이전 사전점검 단계에서 파일럿 자체가 시작되지 못했음을 문서화 | 코드 변경 없음(ADR 문서 전용); PLT-44 배치 자체가 생성되지 않아 실제 PR 생성·리뷰·머지·CI 그린 증거가 전무(process-verification 리프, D0) |

## 발행된 DEEPEN task

D2 미달 60건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 2, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id)은 커밋 시점에 아래 표로 채운다.

| 원 task id | DEEPEN task id |
|---|---|
| 338 | 3135 |
| 457 | 3136 |
| 459 | 3137 |
| 458 | 3138 |
| 493 | 3139 |
| 460 | 3140 |
| 816 | 3141 |
| 818 | 3142 |
| 817 | 3143 |
| 819 | 3144 |
| 845 | 3145 |
| 846 | 3146 |
| 851 | 3147 |
| 852 | 3148 |
| 850 | 3149 |
| 904 | 3150 |
| 905 | 3151 |
| 932 | 3152 |
| 853 | 3153 |
| 933 | 3154 |
| 938 | 3155 |
| 906 | 3156 |
| 940 | 3157 |
| 939 | 3158 |
| 1010 | 3159 |
| 1002 | 3160 |
| 1032 | 3161 |
| 1018 | 3162 |
| 1001 | 3163 |
| 1009 | 3164 |
| 1017 | 3165 |
| 1011 | 3166 |
| 1016 | 3167 |
| 1076 | 3168 |
| 1019 | 3169 |
| 1075 | 3170 |
| 1090 | 3171 |
| 1092 | 3172 |
| 1091 | 3173 |
| 1074 | 3174 |
| 1103 | 3175 |
| 1105 | 3176 |
| 1104 | 3177 |
| 1189 | 3178 |
| 1108 | 3179 |
| 1216 | 3180 |
| 1217 | 3181 |
| 1240 | 3182 |
| 1218 | 3183 |
| 1324 | 3184 |
| 1309 | 3185 |
| 1333 | 3186 |
| 1544 | 3187 |
| 1615 | 3188 |
| 1815 | 3189 |
| 1923 | 3190 |
| 1759 | 3191 |
| 1756 | 3192 |
| 1956 | 3193 |
| 2149 | 3194 |
