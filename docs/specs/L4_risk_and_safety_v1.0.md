# L4 구현 명세 — Risk & Safety v1.0 (기관·자산운용사급)

> 템플릿: `docs/specs/_TEMPLATE.md`. 대상 도메인: 사전 리스크 검사(8.2-B 8지표), 노출 한도,
> Kill switch 5범위 + fence, Circuit Breaker 지표수집·재가동 거버넌스, Watchdog 강제청산(분할),
> Data Distrust 쿼럼, RiskDecision 계약(48번), 결정 재생(replay), 정책 버전·인간 승인, 한도 위반 알림.
> 원칙 하나: **Risk는 추천하지 않는다. 결정론적 최종 거부권이며, 판단 불가는 거부다. 이 경로에 LLM은 없다.**

## 0. 문서 메타

| 항목 | 값 |
|---|---|
| status | DRAFT → PM(agent-platform-12) 승인 후 ACTIVE |
| owner role | Risk Engineering (구현 세션은 §9 리프 단위로 배정) |
| supersedes | `src/core/risk/engine.py` docstring의 "Draft 근사" 조항, `var_estimator.py`/`correlation.py`의 Draft 표 |
| depends on | 48번(게이트·kill switch), 78번(L3 risk/safety), 105번(동시성), 107번(계약 버전), 108번(관측성), 03번 §3.7, FD-8.3/FD-9, `config/risk_policy.yaml`, ADR-2026-08-29-E(FROZEN_PAPER_ONLY), ADR-2026-08-10-D(플랫폼 승인) |
| implemented by (기존) | `src/core/risk/{engine,models}.py` · `src/core/safety/{circuit_breaker,metrics_collector,data_distrust,watchdog,heartbeat,split_brain,base_loop}.py` · `src/foundation/risk_gate/**` · `src/services/{risk_guard_service}.py` · `src/services/execution_loop/{equity_tracker,account_state,var_estimator,correlation,pre_submit_check}.py` · `src/services/order_service/{gate,foundation_gate}.py` · `src/watchdog_process.py` · `src/main.py` |
| implemented by (신규) | §2 표의 "신규" 행 전부 — `src/core/risk/rules/**`, `src/core/risk_stats/**`, `src/core/safety/{data_freshness,market_correlation,liquidation_planner,recovery_gate}.py`, `src/foundation/risk_gate/application/{evaluate_pre_submit,recovery_gate,intraday_monitor,activate_rule_bundle,replay_decision,upsert_risk_limit,read_fence}.py`, `src/services/safety/**`, `src/services/risk_decision_recorder.py`, 마이그레이션 7개 |
| verification evidence | `tests/unit/core/risk/**`, `tests/unit/core/risk_stats/**`, `tests/unit/core/safety/**`, `tests/integration/risk/**`, `tests/adversarial/risk/**`, `tests/performance/test_pre_trade_latency.py` |
| zone 주의 | `src/core/risk/**`는 FROZEN_PAPER_ONLY(ADR-E). 이 문서의 `src/core/risk/**` 변경(신규 파일 포함)은 §2-B 규칙 6에 따라 **PM 승인 후** 착수. 통계 계산(`src/core/risk_stats/**`)과 안전장치(`src/core/safety/**`)는 SCAFFOLD — "판단"이 아니라 "계산·감시"이므로 FROZEN이 아니다(FD-9 재분류 근거와 동일). |

## 1. 기관급 요구 (왜 기초 수준으로는 부족한가)

| # | 기관 요구 | 현재 수준(FULL_AUDIT §2/§5/§6 + 코드 재확인) | 격차 |
|---|---|---|---|
| R1 | **결정 계약**: 5단계 outcome, rule_version·hash, TTL, trace_id, input refs를 가진 RiskDecision(48번 §2) | `RiskCheckResult{approved, rejection_reason, checked_rules}` 3필드(§5 "3필드 스텁"). foundation `RiskEvaluation`은 5단계지만 DEPLOYMENT/PRE_INTENT만, trace_id·inputs hash 없음 | 두 계약을 하나로. 모든 결정에 `decision_id, rule_hash, inputs_hash, trace_id, expires_at` |
| R2 | **결정론·재생**: 같은 pinned 입력·규칙이면 같은 결론(RSK-001), 감사관이 과거 결정을 재계산 가능(RSK-009) | 입력이 `dict[str, Any]`로 조립돼 사라짐. 거부는 `logger.info` 한 줄(§5 관측성 미구현). 재생 불가 | 입력 스냅샷 JSONB + hash를 WORM 테이블에 영속화, replay 도구·CI 야간 재생 |
| R3 | **fail-closed**: 입력 결손·stale은 DENY/PAUSE(78번 §1) | RiskEngine 9항목은 fail-closed. 그러나 `correlation_with()` 미지 페어=0.0(암묵 통과), `metrics_collector.data_delay_sec` 상수 0, watchdog `market_wide_correlated=None` 고정 → LIQUIDATE 영구 미발동, `foundation_gate`는 mandate 없으면 통과(env 플래그) | 결손은 전부 `None` → DENY. 상수 0/None 입력 경로 제거 |
| R4 | **통계적 정당성**: VaR/ES 방법론 선택(파라메트릭·역사적·Cornish-Fisher), 적정 lookback·horizon 스케일링, 실현 수익률 기반 상관 | VaR = 1분봉 21개 표준편차 × z × √days(단위 불일치 — 1분 σ에 일 단위 horizon 곱). 상관은 5심볼 하드코딩 표 | `risk_stats` 순수 모듈: 일봉 lookback ≥60/기본 250, log 수익률, 포트폴리오 VaR/ES, 회귀 테스트 known-value |
| R5 | **노출 한도**: tenant/account/strategy/symbol/asset class별 gross·net·건수·빈도 한도, DB에서 강제 | 실행별 `allocated_capital`·mandate 룰(총노출·단일종목)만. symbol/asset class/provider 한도 없음. 한도 위반 기록 테이블 없음 | `risk_limit` 테이블 + 단일 SQL 노출 스냅샷 + `risk_limit_breach` |
| R6 | **Kill switch 권위 저장소 + fence**: 5범위, 활성화 트랜잭션이 fence 증가, 워커는 모든 부작용 직전 fence 확인(78번 §3), post-fence 부작용 = 0 | 권위가 둘(`system_safety_state` id=1 단일 행, `safety_control`). fence는 단조증가 실구현·paper_control만 소비. legacy `strategy_executions`/`order_service` 경로는 gate에서 control 유무만 보고 **부작용 직전 fence 재확인 없음**. STRATEGY_DEPLOYMENT 범위는 어떤 평가에도 매치 안 됨 | `safety_control`+`safety_fence`를 단일 권위로. `FenceSnapshot`을 gate→submit→adapter 호출 직전까지 관통. legacy 실행도 5범위 매핑 |
| R7 | **Circuit Breaker**: 지표 실측(api error rate, data delay, reject rate, disconnect), 재가동은 인간 승인 + 근거 evidence + 냉각기간 + fresh 재평가(48번 §5-5, FD-9.4b) | `evaluate(metrics)`·`InstrumentedAdapter`(`d649004`)는 배선됨(api error rate·disconnect·reject rate 실측). `data_delay_sec`는 여전히 상수 0(관측점 없음). `check_reactivation`은 승인만 보고 NORMAL 전이 — 조건 완화 지속·evidence 검사 없음. `_set_level`은 무조건 UPDATE(105 위반) | 캔들 freshness 관측점, `recovery_gate` 순수 규칙, 조건부 UPDATE |
| R8 | **강제청산 시장충격 방지**: 분할·지터·참여율 상한(8.6-A-1) | 청산 실행 경로 자체가 없음. `_apply_decision`은 RUNNING 전부를 PAUSED로 바꿀 뿐 | `liquidation_planner`(순수, 결정론적 seed) + main 프로세스 `liquidation_executor` |
| R9 | **데이터 불신 쿼럼**: 3소스 중앙값, 쿼럼 불성립=판정불가(FD-9.5) | `DataDistrustMonitor`는 구현됐으나 호출자 0, 인메모리, 참조 소스 어댑터 없음 | 참조 시세 포트 + 영속 상태 + 사전 리스크 입력으로 연결 |
| R10 | **정책 버전·인간 승인**: 규칙 번들 불변 발행, rule hash, 승인자·ADR 기록(78번 §1 `risk_rule_bundle`) | `risk_policy.yaml version: "draft-1"`, "마지막 인간 승인: (미기재)". 로더는 범위검증만 | `risk_rule_bundle` 테이블, yaml hash ≠ ACTIVE 번들 hash면 엔진이 DENY |
| R11 | **지연 예산**: 사전 검사 p99 ≤ 50 ms | `assemble_account_state`가 tick마다 DB 왕복 5회 + `compute_user_positions` — 미측정(§9 벤치마크 단언 없음) | 단일 CTE 스냅샷 쿼리 + 캔들 캐시, 벤치마크 단언 |
| R12 | **테넌트 격리·권한**: 결정·한도·통제 모두 tenant 스코프, 에이전트/프론트가 ALLOW 위조 불가(RSK-006) | foundation은 tenant 격리 테스트 있음. legacy는 user_id 기준(= tenant). `orders`에 결정 참조 없음 → "결정 없이 주문" DB 레벨로는 가능 | `orders.risk_decision_id` FK + 트리거 |
| R13 | **관측성**: 108번 필드·메트릭, 거부 감사 | trace_id 0건, 메트릭 0건 | §7 메트릭·로그 필드 고정 |

## 2. 모듈 분해 (최소단위)

Zone 약어: FPO = FROZEN_PAPER_ONLY(PM 승인 필요), SC = SCAFFOLD, OP = OPEN. 상태: **기존** / 기존·수정 / 신규.

### 2.1 판단 코어 — `src/core/risk/` (FPO)

| 파일 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | 상태 |
|---|---|---|---|---|---|
| `models.py` | 레거시 `RiskCheckResult` 유지(executor 시그니처 보존) | `decision_id: UUID | None = None` 옵션 필드 추가(§3.9, 하위호환) | — | 30 | 기존·수정 |
| `decision.py` | `RiskDecision`·`RuleResult`·`RiskOutcome`·`GateKind` pydantic(§3.1) | 모델만 | — | 120 | 신규 |
| `inputs.py` | `RiskInputs` typed snapshot(§3.2), `inputs_hash()` | `RiskInputs.inputs_hash() -> str` | `hashing` | 180 | 신규 |
| `hashing.py` | canonical JSON(Decimal→str, UTC ISO, sorted keys) + sha256 | `canonical_json(obj) -> bytes`, `sha256_hex(b) -> str` | — | 60 | 신규 |
| `policy_bundle.py` | `RiskRuleBundle` 모델·상태·`compute_rule_hash(policy: RiskPolicy, engine_version) -> str` | 순수 | `hashing`, `risk_policy_loader` | 120 | 신규 |
| `limits.py` | `ExposureLimit`·`LimitScope`·`LimitMetric` + `check_exposure_limits(inputs, limits) -> RuleResult` | 순수 | `inputs` | 150 | 신규 |
| `rules/base.py` | `Rule` Protocol, `missing(rule_id, field) -> RuleResult`(fail-closed 헬퍼), `pct(Decimal)` 정밀도 규칙 | `Rule = Callable[[RiskInputs, RiskPolicy], RuleResult]` | — | 60 | 신규 |
| `rules/daily_loss.py` | 일손실 warning/halt | `check(inputs, policy) -> RuleResult` | base | 60 | 신규 |
| `rules/max_drawdown.py` | peak 대비 낙폭 warning/hard_stop | 동일 | base | 60 | 신규 |
| `rules/leverage.py` | gross_exposure/equity ≤ default_max × coverage_multiplier | 동일 | base | 70 | 신규 |
| `rules/concentration.py` | **체결 후 예측 비중**(기존 시가평가 + 주문 notional)/equity ≤ single_asset_max_pct, 감소 주문은 통과 | 동일 | base | 80 | 신규 |
| `rules/strategy_allocation.py` | allocated_capital/**total_equity** ≤ cap(certified) — 분모를 available_balance에서 교정 | 동일 | base, `services.capital_allocation.allocation_cap_pct`(순수) | 60 | 신규 |
| `rules/var_es.py` | 포스트트레이드 포트폴리오 VaR ≤ max_pct **and** ES ≤ es_max_pct, 방법·bars_used 검증(min_bars 미달=결손) | 동일 | base | 90 | 신규 |
| `rules/correlation.py` | ρ>threshold 심볼 합산 노출 ≤ aggregate_exposure_max_pct, ρ 결손 페어 있으면 DENY | 동일 | base | 70 | 신규 |
| `rules/trade_frequency.py` | 1h 건수 ≤ max(24h 평균×배수, 절대 상한 `max_trades_per_hour`) | 동일 | base | 60 | 신규 |
| `rules/safety_state.py` | CB level·활성 control·distrust level·paused_by·connection_fresh | 동일 | base | 90 | 신규 |
| `evaluator.py` | 규칙 순서 고정·단락 평가·outcome 합성(§4.2)·`RiskDecision` 생성, `latency_us` 측정 | `evaluate(inputs, bundle, *, gate_kind, trace_id, now, ttl) -> RiskDecision` | rules, limits, decision | 200 | 신규 |
| `engine.py` | 하위호환 facade — `check(allocation, account_state)`는 `RiskInputs.from_legacy_dict()`→`evaluate`→`RiskCheckResult`로 축약. `check_decision(inputs) -> RiskDecision` 추가 | 기존 시그니처 유지 | evaluator | 120 | 기존·수정(215→≤120) |

### 2.2 통계 계산 — `src/core/risk_stats/` (SC, 순수·numpy만)

| 파일 | 단일 책임 | 공개 계약 | 상한 | 상태 |
|---|---|---|---|---|
| `returns.py` | 캔들→log 수익률, timeframe→bars_per_day, horizon 스케일 | `log_returns(closes: Sequence[Decimal]) -> np.ndarray`, `bars_per_day(timeframe) -> int`, `scale_sigma(sigma, bars_per_day, horizon_days) -> float` | 80 | 신규 |
| `var_parametric.py` | 정규 VaR/ES: `VaR=zσ√h`, `ES=σφ(z)/(1−c)√h` | `parametric_var_es(r, *, confidence, horizon_days, bars_per_day) -> VarEs` | 60 | 신규 |
| `var_historical.py` | 경험 분위(선형보간), h>1은 겹침 합산 수익률 | `historical_var_es(r, ...) -> VarEs` | 80 | 신규 |
| `var_cornish_fisher.py` | `z_cf = z+(z²−1)S/6+(z³−3z)K/24−(2z³−5z)S²/36` (K=초과첨도), ES는 수치 적분 | `cornish_fisher_var_es(r, ...) -> VarEs` | 90 | 신규 |
| `portfolio.py` | 포스트트레이드 가중치 w, 파라메트릭 `√(wᵀΣw)`, 역사적은 포트폴리오 수익률 시계열 | `portfolio_returns(R, w)`, `portfolio_var_es(method, R, w, ...) -> VarEs` | 120 | 신규 |
| `correlation_matrix.py` | 정렬된 수익률 행렬의 Pearson(옵션 EWMA λ), 최소 겹침 미만 페어=`None` | `pearson_matrix(R, *, min_overlap) -> dict[tuple[str,str], float | None]` | 100 | 신규 |
| `models.py` | `VarEs(var_pct: Decimal, es_pct: Decimal, method, bars_used, lookback_bars)` | 모델 | 40 | 신규 |

### 2.3 안전장치 — `src/core/safety/` (SC)

| 파일 | 단일 책임 | 공개 계약 | 상한 | 상태 |
|---|---|---|---|---|
| `circuit_breaker.py` | 4단계 상태기계 | `_set_level`을 조건부 UPDATE로(§5) | 230 | 기존·수정 |
| `metrics_collector.py` | 5지표 수집 | `data_delay_sec`를 `DataFreshnessTracker`에서 읽도록(상수 0 제거) | 130 | 기존·수정 |
| `data_freshness.py` | (exchange, symbol)별 마지막 캔들 close_time 관측 | `DataFreshnessTracker.record(exchange, symbol, close_time: datetime)`, `.max_delay_sec(now) -> Decimal | None`(관측 0건이면 None) | 60 | 신규 |
| `recovery_gate.py` | 재가동 허용 순수 규칙(§4.3 CB 표) | `can_reactivate(*, current_level, metrics_history: Sequence[CircuitBreakerMetrics], cooldown_sec, evidence_ref, approval_status, fresh_risk_outcome) -> RecoveryDecision` | 100 | 신규 |
| `data_distrust.py` | 쿼럼·히스테리시스 판정 | 변경 없음 + `level_for(symbol)` 외부 주입용 `restore(symbol, level, since)` | 180 | 기존·수정 |
| `market_correlation.py` | 손실이 시장 전체 급변과 상관되는지 | `is_market_wide_move(basket_returns: dict[str, Decimal], *, account_loss_pct, min_symbols=3, move_threshold_pct) -> bool | None`(basket 부족=None) | 70 | 신규 |
| `watchdog.py` | 손실·응답성 판정 | `decide(snapshot, *, market_wide_correlated, failure_domain: FailureDomain | None, ...)` — DB_ISOLATED_FAILURE면 LIQUIDATE→HALT 강등 | 180 | 기존·수정 |
| `liquidation_planner.py` | 분할·지터·참여율 상한 계획(순수, seed 결정론) | `plan_liquidation(positions: Sequence[OpenPosition], *, seed: bytes, policy: LiquidationPolicy, volume_5m: Mapping[str, Decimal | None]) -> LiquidationPlan` | 200 | 신규 |
| `heartbeat.py`, `split_brain.py`, `base_loop.py` | 변경 없음 | — | — | **기존** |

### 2.4 Foundation risk_gate — `src/foundation/risk_gate/` (SC)

| 파일 | 단일 책임 | 공개 계약 | 상한 | 상태 |
|---|---|---|---|---|
| `contracts/v1.py` | 외부 소비 계약(107번 additive MINOR) | `GateKind` += PRE_SUBMIT/INTRADAY/RECOVERY; `RiskDecision` re-export; `RiskLimitView`, `RiskSignalView`, `RuleBundleView`, `FenceSnapshotView`, `ReplayResultView` | 200 | 기존·수정 |
| `domain/models.py` | 값 객체 | `RiskSignal`, `RiskLimit`, `RuleBundleRecord`, `FenceSnapshot(tokens: Mapping[tuple[SafetyScope,str], int])` 추가 | 200 | 기존·수정 |
| `domain/fence.py` | fence 비교 순수 규칙 | `fence_pairs_for(tenant_id, provider_code, execution_ref) -> tuple[tuple[SafetyScope,str],...]`, `is_stale(observed: FenceSnapshot, current: FenceSnapshot) -> bool` | 60 | 신규 |
| `domain/rules.py` | control 합성·기본 게이트 | 변경 없음 | 83 | **기존** |
| `ports/repository.py` | 저장소 포트 | `RiskGateRepository` += `read_fences(pairs) -> FenceSnapshot`, `list_active_controls_for(pairs)`; 신규 Protocol `RiskDecisionRepository`, `RiskLimitRepository`, `RuleBundleRepository`, `RiskSignalRepository` | 200 | 기존·수정 |
| `adapters/postgres_repository.py` | control/fence/evaluation | `read_fences` 1쿼리(`WHERE (scope,scope_ref) IN (...)`) | 260 | 기존·수정 |
| `adapters/postgres_decision_repository.py` | `risk_decision` WORM 쓰기·읽기 | `insert(decision, inputs_snapshot: dict) -> None`, `get(decision_id) -> (RiskDecision, dict)`, `list_recent(tenant_id, limit)` | 150 | 신규 |
| `adapters/postgres_limit_repository.py` | `risk_limit`/`risk_limit_breach` | `list_effective(tenant_id, *, provider_code, strategy_id, symbols) -> tuple[RiskLimit,...]`, `upsert(limit)`, `record_breach(...)` | 160 | 신규 |
| `adapters/postgres_bundle_repository.py` | `risk_rule_bundle` | `get_active(scope) -> RuleBundleRecord | None`, `insert_draft`, `transition(id, expected_state, new_state, **audit)` (conditional_update) | 150 | 신규 |
| `adapters/postgres_signal_repository.py` | `risk_signal` | `insert_if_new(dedupe_key, ...) -> bool`, `list_open(tenant_id)` | 100 | 신규 |
| `application/evaluate_risk_gate.py` | DEPLOYMENT/PRE_INTENT | trace_id 인자 추가, `risk_evaluation.trace_id` 저장 | 150 | 기존·수정 |
| `application/evaluate_pre_submit.py` | PRE_SUBMIT 게이트: control + fence + CB level + distrust + connection fresh → `RiskDecision`(TTL 2 s) + FenceSnapshot 반환 | `evaluate_pre_submit(repos..., *, tenant_id, execution_ref, provider_code, symbol, trace_id) -> tuple[RiskDecision, FenceSnapshot]` | 180 | 신규 |
| `application/read_fence.py` | fence 스냅샷 조회 | `read_fence_snapshot(repo, *, tenant_id, provider_code, execution_ref) -> FenceSnapshot` | 50 | 신규 |
| `application/intraday_monitor.py` | 드로다운·stale·provider·recon → `risk_signal` → 필요 시 PAUSE control(ACCOUNT/PROVIDER) | `run_intraday_monitor_once(repos, *, now) -> list[RiskSignal]` | 200 | 신규 |
| `application/recovery_gate.py` | RECOVERY 게이트: evidence + fresh trust/policy/risk + approval | `evaluate_recovery(repos, *, tenant_id, control_id, evidence_ref, approval_id, trace_id) -> RiskDecision` | 150 | 신규 |
| `application/activate_rule_bundle.py` | DRAFT→APPROVED(승인자·ADR)→ACTIVE(단일) | `approve_rule_bundle(repo, audit_repo, *, bundle_id, approver_subject_id, approval_ref, actor_is_risk_officer)`, `activate_rule_bundle(...)` | 160 | 신규 |
| `application/replay_decision.py` | 저장된 결정 재계산·비교 | `replay(decision_repo, bundle_repo, *, decision_id) -> ReplayResult(match: bool, diff: dict)` | 120 | 신규 |
| `application/upsert_risk_limit.py` | 한도 생성·변경(운영자·risk officer만, tenant 스코프 검증) | `upsert_risk_limit(repo, audit_repo, *, tenant_id, actor, limit)` | 100 | 신규 |
| `application/{activate,deactivate}_safety_control.py` | kill switch | `activate`: `trace_id` 인자 + `on_activated` 훅(services의 fan-out 호출) | 150/90 | 기존·수정 |

### 2.5 서비스·배선 — `src/services/` (SC)

| 파일 | 단일 책임 | 공개 계약 | 상한 | 상태 |
|---|---|---|---|---|
| `execution_loop/risk_inputs_assembler.py` | `RiskInputs` 조립(단일 CTE 쿼리 + 캐시) | `assemble_risk_inputs(pool, caches, *, execution_id, user_id, intent: OrderIntent, balances, candles, policy, now) -> RiskInputs` | 250 | 신규 (account_state.py 대체) |
| `execution_loop/account_state.py` | 레거시 dict 어댑터 — `RiskInputs.to_legacy_dict()` 호출만 | 기존 시그니처 유지, 2리프 뒤 삭제 | 40 | 기존·수정(111→≤40) |
| `execution_loop/exposure_snapshot.py` | 노출 스냅샷 SQL 1개 | `load_exposure_snapshot(conn, *, user_id, execution_id, symbol, prices) -> ExposureSnapshot` | 150 | 신규 |
| `execution_loop/candle_history.py` | 심볼별 일봉 lookback 캐시(TTL 60 s, `get_ohlcv('1d', limit)`) | `CandleHistoryCache.get(adapter, symbol, *, bars) -> list[Candle]` | 120 | 신규 |
| `execution_loop/var_estimator.py` | risk_stats 어댑터 | `estimate_portfolio_var_es(histories, weights, policy) -> VarEs | None` | 80 | 기존·수정 |
| `execution_loop/correlation.py` | 하드코딩 표 삭제 → `correlation_service.py`로 대체 | — | 0 | 삭제(리프 R-19) |
| `execution_loop/correlation_service.py` | 캐시된 히스토리로 상관행렬·상관노출 | `correlated_exposure(histories, positions, target, *, threshold, min_overlap) -> tuple[Decimal | None, float | None]` | 120 | 신규 |
| `execution_loop/equity_tracker.py` | 일손실·MDD 기준점 | UTC 일경계, `save_equity_baseline`을 단조 조건부 UPDATE로(§5) | 160 | 기존·수정 |
| `execution_loop/tick.py` | 실행 tick | `risk_engine.check_decision(inputs)` 사용, `recorder.record()` 호출, 거부 시 `return` 전 기록 | 320→분할: `tick_risk_phase.py` 신규(≤120) | 기존·수정 |
| `execution_loop/pre_submit_check.py` | tick 레벨 PRE_SUBMIT | `GateDecision.fence_snapshot` 반환 관통 | 80 | 기존·수정 |
| `order_service/gate.py` | 순수 타입 | `GateDecision` += `decision_id: UUID | None`, `fence_snapshot: Mapping[str,int]`(foundation 타입 미참조 유지) | 70 | 기존·수정 |
| `order_service/foundation_gate.py` | PRE_SUBMIT 구현체 | `evaluate_pre_submit` 호출로 교체; `AIOS_REQUIRE_MANDATE_FOR_SUBMIT` 기본값 `1` | 120 | 기존·수정 |
| `order_service/fenced_submit.py` | fence 확인 후에만 어댑터 호출 | `submit_with_fence(pool, adapter, order, *, gate_decision, read_fences) -> Order` — 직전 재조회, stale이면 `FenceStaleError` + 감사, 호출 후 재조회로 `post_fence_side_effect` 계수 | 120 | 신규 |
| `risk_decision_recorder.py` | 결정 영속화 + 감사 + 이벤트 | `RiskDecisionRecorder.record(decision, inputs, *, actor) -> None`(WORM insert, audit_log, `risk.decision.recorded`, 위반 시 `risk.limit.breached`) | 150 | 신규 |
| `risk_alerting.py` | 위반→알림(중복 억제 5분, 심각도) | `RiskAlertService.on_breach(event)`; 게이트웨이 `src/core/notifications/gateway.py` 사용 | 120 | 신규 |
| `risk_guard_service.py` | 실행별 손실 자동정지 | `pause()` 대신 `KillSwitchService.activate(STRATEGY_DEPLOYMENT, "exec:<id>")` 호출로 통일 | 90 | 기존·수정 |
| `safety/kill_switch_service.py` | **단일 권위 진입점** — control 생성(fence++) → legacy 실행 정지 → paper_control fan-out → 미체결 정리 enqueue → 감사·알림 | `KillSwitchService.activate(*, scope, scope_ref, reason, actor_subject_id, actor_is_admin, trace_id) -> SafetyControlView`, `.deactivate(control_id, *, evidence_ref, ...)` | 200 | 신규 |
| `safety/legacy_execution_pauser.py` | 범위→`strategy_executions` 매핑·행별 조건부 UPDATE | `pause_executions_for_scope(conn, scope, scope_ref, *, control_id) -> list[int]` | 100 | 신규 |
| `safety/open_order_sweeper.py` | 범위 내 취소가능 주문 취소(멱등: control_id) | `sweep_open_orders(pool, adapters, *, control_id, scope, scope_ref) -> SweepReport` | 150 | 신규 |
| `safety/circuit_breaker_loop.py` | 10 s: 수집→evaluate→recovery_gate→check_reactivation | `run_circuit_breaker_tick(pool, cb, tracker, freshness, policy, *, history)` | 120 | 신규 |
| `safety/distrust_wiring.py` | tick에서 primary+참조 시세 수집→monitor→영속 | `check_and_persist_distrust(pool, monitor, providers, *, exchange, symbol, primary, candles) -> DataDistrustLevel` | 120 | 신규 |
| `safety/reference_quotes.py` | 참조 시세 포트 + 어댑터 | `ReferenceQuoteProvider` Protocol `get_ticker(symbol) -> Ticker | None`(타임아웃 2 s, 실패=None) | 100 | 신규 |
| `safety/liquidation_executor.py` | 계획 슬라이스 실행(reduce-only), 진행 상태 영속 | `run_liquidation_worker_once(pool, adapters, *, now)` | 220 | 신규 |
| `exchanges/common/instrumented_adapter.py` | `ApiCallTracker` 계측 프록시(`d649004`) | `InstrumentedAdapter(wrapped, tracker, *, freshness: DataFreshnessTracker | None = None)` — `get_ohlcv` 반환 캔들의 마지막 `close_time`을 `freshness.record()`에 기록(옵션 인자, 하위호환) | 120 | 기존·수정(94→≤120) |
| `watchdog_process.py` | 별도 프로세스 | LIQUIDATE→`liquidation_request` INSERT + GLOBAL/ACCOUNT control(DB만, 이벤트버스 없음); HALT→control. `_apply_decision`의 무조건 UPDATE 제거 | 260 | 기존·수정 |
| `main.py` | 배선(PM 직렬화) | CB 루프 교체, liquidation worker, intraday monitor, instrumented adapter | — | 기존·수정 |
| `src/tools/risk_replay.py` | CLI `python -m src.tools.risk_replay --decision-id … | --since …` | exit 0=일치, 2=불일치 | 80 | 신규 |

### 2.6 마이그레이션 — `src/db/migrations/versions/` (SC). 현재 head `5ed4921f9873`; PM이 직렬화(§2-B 규칙 4).

| 파일 | 내용 |
|---|---|
| `a9c4e1f7b2d3_risk_rule_bundle.py` | `risk_rule_bundle(id UUID PK, scope VARCHAR(30) DEFAULT 'GLOBAL', version VARCHAR(40), rule_hash CHAR(64), engine_version VARCHAR(40), policy_snapshot JSONB NOT NULL, state CHECK IN ('DRAFT','APPROVED','ACTIVE','RETIRED'), effective_from TIMESTAMPTZ, effective_to TIMESTAMPTZ, created_by UUID, approved_by UUID, approval_ref TEXT, approved_at TIMESTAMPTZ, activated_at, retired_at)`; `UNIQUE(scope, version)`; **partial unique** `ux_bundle_active ON (scope) WHERE state='ACTIVE'`; CHECK `approved_by IS NOT NULL OR state IN ('DRAFT')`; UPDATE 트리거로 `rule_hash, policy_snapshot, version` 변경 금지 |
| `b8d5f2a1c3e4_risk_decision_worm.py` | `risk_decision(decision_id UUID PK, tenant_id UUID NOT NULL REFERENCES users(user_id), gate_kind CHECK IN 6종, execution_ref VARCHAR(60), subject_fingerprint CHAR(64), outcome CHECK 5종, reason_codes TEXT[], obligations TEXT[], rule_results JSONB, rule_version, rule_hash CHAR(64) NOT NULL, engine_version, inputs_hash CHAR(64) NOT NULL, inputs_snapshot JSONB NOT NULL, input_refs TEXT[], trace_id UUID NOT NULL, evaluated_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL, latency_us INT)`; 인덱스 `(tenant_id, evaluated_at DESC)`, `(trace_id)`, `(execution_ref, evaluated_at DESC)`; 트리거 `risk_decision_worm` — UPDATE/DELETE 시 RAISE; `REVOKE UPDATE, DELETE ON risk_decision FROM aios_app`(role 분리 미확정 → §10) |
| `c7e6a3b2d4f5_risk_limit.py` | `risk_limit(id UUID PK, tenant_id UUID NULL(=플랫폼 기본), scope CHECK IN ('TENANT','ACCOUNT','STRATEGY','SYMBOL','ASSET_CLASS','PROVIDER'), scope_ref VARCHAR(200) NOT NULL, metric CHECK IN ('GROSS_NOTIONAL_PCT','NET_NOTIONAL_PCT','MAX_ORDER_NOTIONAL','MAX_OPEN_POSITIONS','MAX_TRADES_PER_HOUR','MAX_LEVERAGE'), limit_value NUMERIC(30,10) CHECK (limit_value >= 0), hard BOOLEAN DEFAULT TRUE, effective_from, effective_to, created_by, approval_ref, updated_at)`; `UNIQUE(COALESCE(tenant_id,'00000000-…'), scope, scope_ref, metric)`(표현식 유니크); `risk_limit_breach(id BIGSERIAL, limit_id, decision_id REFERENCES risk_decision, observed NUMERIC, limit_value NUMERIC, severity, occurred_at)` |
| `d6f7b4c3e5a6_risk_signal_distrust_state.py` | `risk_signal(id UUID PK, tenant_id, type CHECK IN ('DRAWDOWN','STALE_DATA','PROVIDER_OUTAGE','RECON_MISMATCH','DISTRUST'), severity CHECK IN ('WARN','CRITICAL'), dedupe_key VARCHAR(200) UNIQUE, as_of, source, evidence_ref, state CHECK IN ('OPEN','ACKED','RESOLVED'), safety_control_id UUID NULL)`; `data_distrust_state(exchange VARCHAR(30), symbol VARCHAR(30), level CHECK IN ('NORMAL','SUSPICIOUS','DISTRUSTED'), since TIMESTAMPTZ, sources_available SMALLINT, updated_at, PRIMARY KEY(exchange, symbol))` |
| `e5a8c5d4f6b7_liquidation_request.py` | `liquidation_request(id UUID PK, safety_control_id UUID NOT NULL REFERENCES safety_control(id), scope, scope_ref, state CHECK IN ('REQUESTED','PLANNED','EXECUTING','DONE','PARTIAL','ABORTED'), plan JSONB, seed_ref VARCHAR(64), requested_by VARCHAR(40), requested_at, completed_at, fence_token BIGINT NOT NULL)`; `liquidation_slice(id BIGSERIAL, request_id, seq SMALLINT, symbol, quantity NUMERIC, not_before TIMESTAMPTZ, order_id UUID NULL REFERENCES orders(order_id), state CHECK IN ('PENDING','SENT','FILLED','FAILED','SKIPPED'), UNIQUE(request_id, seq))` |
| `f4b9d6e5a7c8_risk_evaluation_gate_kinds_trace.py` | `risk_evaluation.gate_kind` CHECK를 6종으로 확장, `trace_id UUID` 추가(NULL 허용, 신규 행은 코드가 항상 채움); `safety_control.idempotency_digest CHAR(64) NULL UNIQUE`(§5 멱등키); `strategy_executions.paused_by_control_id UUID NULL REFERENCES safety_control(id)`(§3.8 추적) |
| `93c0e7f6b8d9_orders_risk_decision_fk.py` | `orders.risk_decision_id UUID NULL REFERENCES risk_decision(decision_id)`, `orders.liquidation_request_id UUID NULL`; 트리거 `orders_require_risk_decision`: `NEW.is_liquidation = FALSE AND NEW.risk_decision_id IS NULL AND NEW.created_at >= <cutover_at>` 이면 RAISE; `is_liquidation = TRUE`면 `liquidation_request_id` 필수. cutover_at은 마이그레이션 적용 시각 |

### 2.7 테스트 — `tests/` (OP)

`tests/unit/core/risk/`(rule별 파일 + `test_evaluator.py`, `test_hashing.py`, `test_inputs_contract.py`, `test_policy_bundle.py`, `test_limits.py`), `tests/unit/core/risk_stats/`(`test_returns.py`, `test_var_parametric.py`, `test_var_historical.py`, `test_var_cornish_fisher.py`, `test_portfolio.py`, `test_correlation_matrix.py`), `tests/unit/core/safety/`(`test_liquidation_planner.py`, `test_market_correlation.py`, `test_recovery_gate.py`, `test_data_freshness.py`), `tests/integration/risk/`(`test_risk_decision_recorder.py`, `test_kill_switch_service.py`, `test_fenced_submit.py`, `test_circuit_breaker_loop.py`, `test_rule_bundle_activation.py`, `test_replay_decision.py`, `test_risk_limits_db.py`, `test_pre_submit_gate.py`, `test_intraday_monitor.py`, `test_liquidation_worker.py`, `test_watchdog_liquidation_request.py`, `test_distrust_wiring.py`), `tests/adversarial/risk/`(`test_cross_tenant_limits.py`, `test_no_llm_in_risk_path.py`, `test_agent_cannot_forge_allow.py`, `test_fence_race.py`, `test_worm_tables.py`), `tests/performance/test_pre_trade_latency.py`, `tests/unit/contracts/test_risk_decision_schema.py`.

## 3. 계약 (Contract)

### 3.1 `RiskDecision` (`src/core/risk/decision.py`, `schema_version="v1"`, 48번 §2 1:1)

```python
class RiskOutcome(str, Enum): ALLOW="ALLOW"; DENY="DENY"; REDUCE="REDUCE"; PAUSE="PAUSE"; ESCALATE="ESCALATE"
class GateKind(str, Enum): DEPLOYMENT; PRE_INTENT; PRE_TRADE; PRE_SUBMIT; INTRADAY; RECOVERY

class RuleResult(BaseModel, frozen=True):
    rule_id: str                       # "daily_loss" … "exposure_limit:SYMBOL:BTC/USDT:GROSS_NOTIONAL_PCT"
    outcome: RiskOutcome               # 통과면 ALLOW
    reason_code: str | None            # §3.4 taxonomy
    observed: Decimal | None           # 단위는 unit
    limit: Decimal | None
    unit: Literal["pct", "x", "count", "notional"]
    missing_fields: tuple[str, ...] = ()   # 비어있지 않으면 outcome은 반드시 DENY

class RiskDecision(BaseModel, frozen=True):
    schema_version: Literal["v1"] = "v1"
    decision_id: UUID
    gate_kind: GateKind
    tenant_id: UUID
    execution_ref: str | None          # "exec:<int>" | "dep:<uuid>"
    subject_fingerprint: str           # sha256(tenant|gate|rule_hash|inputs_hash)
    outcome: RiskOutcome
    reason_codes: tuple[str, ...]      # 심각도 내림차순, 결정론적 순서
    obligations: tuple[str, ...]       # "REDUCE_QUANTITY_TO:<Decimal>", "REQUIRE_FRESH_CONNECTION", "REQUIRE_RECOVERY_REVIEW"
    rule_results: tuple[RuleResult, ...]   # 실제 평가된 규칙만, 평가 순서 그대로(단락 이후는 없음)
    rule_version: str                  # bundle.version, 예 "2026.09.1"
    rule_hash: str                     # bundle.rule_hash
    engine_version: str                # ENGINE_VERSION = "risk-engine/2"
    inputs_hash: str
    input_refs: tuple[str, ...]        # "candles:bitget:BTC/USDT:1d:250:<sha256[:16]>", "exposure:<as_of>", "fence:<sha256[:16]>"
    evaluated_at: datetime             # tz-aware UTC 강제(validator)
    expires_at: datetime               # PRE_TRADE: evaluated_at+interval_sec, PRE_SUBMIT: +2 s, DEPLOYMENT/PRE_INTENT: +10 s
    trace_id: UUID
    evidence_ref: str | None           # audit_event id(기록 후 recorder가 채움 — 모델은 불변이므로 model_copy)
    latency_us: int

    def is_actionable(self, now: datetime) -> bool:  # outcome in (ALLOW, REDUCE) and now < expires_at
```

호환 규칙(107번): 필드 추가는 MINOR(기본값 필수). `outcome`/`GateKind` enum 값 추가는 MINOR, 제거·의미 변경은 MAJOR(`v2`). `RiskCheckResult`는 `RiskDecision.to_check_result()`로 파생만 하며 신규 소비자는 사용 금지.

### 3.2 `RiskInputs` (`src/core/risk/inputs.py`) — 모든 금액 `Decimal`, 비율은 `pct`(0–100, 소수 6자리 quantize), 시각은 UTC tz-aware

```python
class OrderIntent(BaseModel, frozen=True):
    symbol: str; asset_class: Literal["CRYPTO_SPOT","EQUITY","FUTURES"]; side: Literal["BUY","SELL"]
    quantity: Decimal; ref_price: Decimal; notional: Decimal; reduce_only: bool
    strategy_id: str; strategy_version: str; capital_pct: Decimal

class EquityInputs(BaseModel, frozen=True):
    total_equity: Decimal | None; available_balance: Decimal | None
    day_start_equity: Decimal | None; peak_equity: Decimal | None
    daily_pnl_pct: Decimal | None; drawdown_pct: Decimal | None
    account_daily_pnl_pct: Decimal | None; account_drawdown_pct: Decimal | None   # 계좌(테넌트) 스코프
    as_of: datetime

class ExposureSnapshot(BaseModel, frozen=True):
    gross_notional: Mapping[str, Decimal]     # key "TENANT:<id>" | "ACCOUNT:<id>" | "STRATEGY:<sid>" | "SYMBOL:<sym>" | "ASSET_CLASS:<cls>" | "PROVIDER:<code>"
    net_notional: Mapping[str, Decimal]
    open_positions_count: int; position_quantity: Decimal | None   # 이 심볼 기존 수량
    symbol_market_value: Decimal | None; gross_leverage: Decimal | None   # Σ|mv|/equity
    as_of: datetime

class StatsInputs(BaseModel, frozen=True):
    var_pct: Decimal | None; es_pct: Decimal | None; var_method: str | None
    lookback_bars: int | None; bars_used: int | None
    correlated_exposure_pct: Decimal | None; max_correlation: float | None
    missing_pairs: tuple[str, ...] = ()     # 비어있지 않으면 correlation 규칙은 DENY
    as_of: datetime

class ActivityInputs(BaseModel, frozen=True):
    trades_last_1h: int | None; trades_avg_per_hour_24h: Decimal | None

class SafetyInputs(BaseModel, frozen=True):
    circuit_breaker_level: str | None; active_control_scopes: tuple[str, ...] | None
    fence_snapshot: Mapping[str, int] | None      # "GLOBAL:" , "TENANT:<id>", …
    data_distrust_level: str | None; distrust_sources_available: int | None
    connection_fresh: bool | None; execution_paused_by_safety: bool | None
    rule_bundle_active: bool | None

class RiskInputs(BaseModel, frozen=True):
    schema_version: Literal["v1"] = "v1"
    tenant_id: UUID; execution_ref: str | None; certified_badge: bool | None; allocated_capital: Decimal | None
    intent: OrderIntent; equity: EquityInputs; exposure: ExposureSnapshot; stats: StatsInputs
    activity: ActivityInputs; safety: SafetyInputs; limits: tuple[ExposureLimit, ...]; as_of: datetime
    def inputs_hash(self) -> str          # sha256(canonical_json(self.model_dump(mode="json")))
    @classmethod
    def from_legacy_dict(cls, allocation, account_state: dict, *, tenant_id, execution_id, now) -> "RiskInputs"
```

`ExposureLimit(scope: LimitScope, scope_ref: str, metric: LimitMetric, limit_value: Decimal, hard: bool, limit_id: UUID)`.

### 3.3 정책 파일 확장 (`config/risk_policy.yaml`, OP zone — 수치는 Draft, 번들 승인 전 운영 적용 금지)

```yaml
version: "2026.09.1"
day_boundary: "UTC"
var: {confidence: 0.95, horizon_days: 1, max_pct: 5.0, es_max_pct: 7.0,
      method: "cornish_fisher", timeframe: "1d", lookback_bars: 250, min_bars: 60}
correlation_risk: {threshold: 0.7, aggregate_exposure_max_pct: 30.0, lookback_bars: 90, min_overlap: 30, ewma_lambda: null}
trade_frequency: {anomaly_multiplier: 3.0, max_trades_per_hour: 60}
decision_ttl: {pre_trade_sec: 1.0, pre_submit_sec: 2.0, deployment_sec: 10.0}
reactivation: {cooldown_sec: 300, approval_ttl_sec: 1800, evidence_required: true}
liquidation: {max_participation_pct: 10.0, slice_count_min: 3, slice_count_max: 20, size_jitter_pct: 30.0,
              interval_min_sec: 2, interval_max_sec: 15, max_slice_notional: 5000, limit_tolerance_bps: 15,
              slice_ttl_sec: 5, adverse_move_abort_pct: 1.0, total_deadline_sec: 300}
data_distrust: {enter_threshold_pct: 1.5, exit_threshold_pct: 0.75, exit_sustain_sec: 60, min_sources: 3, quote_timeout_sec: 2}
```

`risk_policy_loader.py`에 대응 pydantic 모델 추가(범위 제약 유지). `load_risk_policy()`는 그대로; 신규 `verify_policy_against_bundle(policy, bundle) -> None | BundleMismatchError`.

### 3.4 에러 taxonomy

| 코드 | 의미 | 재시도 | 호출자 조치 |
|---|---|---|---|
| `RISK_INPUT_MISSING:<field>` | 필수 입력 결손 → DENY | 다음 tick | 결손 필드 메트릭 관측, 반복 시 alert |
| `RISK_INPUT_STALE` | connection/캔들 freshness 초과 → PAUSE | fresh 후 | `REQUIRE_FRESH_CONNECTION` 의무 이행 |
| `RISK_DAILY_LOSS_WARN` / `RISK_DAILY_LOSS_HALT` | warning은 ESCALATE(알림), halt는 DENY | 아니오(당일) | 알림 |
| `RISK_MDD_WARN` / `RISK_MDD_HARD_STOP` | 동일 | 아니오 | halt는 STRATEGY_DEPLOYMENT control 생성(intraday) |
| `RISK_LEVERAGE_EXCEEDED` · `RISK_CONCENTRATION_EXCEEDED` · `RISK_STRATEGY_ALLOCATION_EXCEEDED` · `RISK_VAR_EXCEEDED` · `RISK_ES_EXCEEDED` · `RISK_CORRELATION_EXPOSURE_EXCEEDED` · `RISK_TRADE_FREQUENCY_ANOMALY` | 지표 한도 위반 → DENY(감소 주문이면 REDUCE 후보) | 조건 변경 시 | — |
| `RISK_LIMIT_BREACH:<scope>:<metric>` | `risk_limit` hard 위반 → DENY, soft → ESCALATE | 조건 변경 시 | `risk_limit_breach` 기록 |
| `RISK_KILL_SWITCH_ACTIVE_<SCOPE>` | 활성 control → DENY | 해제 후 | — |
| `RISK_CIRCUIT_BREAKER_<LEVEL>` | restricted 이상 → DENY, warning → ESCALATE | — | — |
| `RISK_DATA_DISTRUST_<LEVEL>` | SUSPICIOUS/DISTRUSTED → 신규 진입 DENY(reduce_only 허용) | 정상 복귀 후 | — |
| `RISK_RULE_BUNDLE_INACTIVE` | yaml hash ≠ ACTIVE 번들 → DENY 전체 | 아니오 | 운영 alert(§7) |
| `RISK_FENCE_STALE` | gate 이후 fence 증가 → 부작용 중단 | 새 결정 필요 | `FenceStaleError`(409) |
| `RISK_DECISION_EXPIRED` | TTL 경과 결정으로 제출 시도 | 새 결정 | 409 |
| `INTEGRITY_RISK_FINGERPRINT_MISMATCH` | 결정의 fingerprint ≠ 재계산 | 아니오 | 보안 alert, 제출 차단 |
| `INTEGRITY_RISK_REPLAY_MISMATCH` | replay 결과 ≠ 저장 결과 | 아니오 | 번들/엔진 변조 조사 |
| `STATE_RECOVERY_REVIEW_REQUIRED` | control 해제 뒤 RUNNING 시도 | RECOVERY 게이트 ALLOW 후 | 409 |

### 3.5 노출 스냅샷 SQL (`exposure_snapshot.py`, 단일 왕복 — R11 지연 예산의 핵심)

```sql
WITH open_pos AS (
  SELECT p.symbol, p.strategy_id, p.exchange, p.quantity, p.average_entry_price, p.leverage
  FROM positions p
  WHERE p.user_id = $1 AND p.closed_at IS NULL AND p.quantity <> 0
), priced AS (
  SELECT o.*, COALESCE(px.price, o.average_entry_price) AS mark,     -- $2 = jsonb {symbol: price}, 없으면 진입가 근사(input_refs에 'mark:entry_fallback' 기록)
         o.quantity * COALESCE(px.price, o.average_entry_price) AS mv
  FROM open_pos o LEFT JOIN jsonb_each_text($2::jsonb) px(symbol, price) ON px.symbol = o.symbol
), trades AS (
  SELECT COUNT(*) FILTER (WHERE created_at >= now() - interval '1 hour')  AS n_1h,
         COUNT(*) FILTER (WHERE created_at >= now() - interval '24 hours') AS n_24h
  FROM orders WHERE execution_id = $3
)
SELECT
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced)                                   AS gross_tenant,
  (SELECT COALESCE(SUM(mv),0)      FROM priced)                                   AS net_tenant,
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced WHERE strategy_id = $4)            AS gross_strategy,
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced WHERE symbol = $5)                 AS gross_symbol,
  (SELECT COALESCE(SUM(ABS(mv)),0) FROM priced WHERE exchange = $6)               AS gross_provider,
  (SELECT COALESCE(SUM(quantity),0) FROM priced WHERE symbol = $5)                AS position_quantity,
  (SELECT COUNT(*) FROM priced)                                                   AS open_positions_count,
  (SELECT MAX(leverage) FROM priced)                                              AS max_leverage,
  (SELECT n_1h FROM trades) AS trades_1h, (SELECT n_24h FROM trades) AS trades_24h,
  (SELECT circuit_breaker_level FROM system_safety_state WHERE id = 1)            AS cb_level,
  (SELECT paused_by FROM strategy_executions WHERE id = $3)                       AS paused_by,
  (SELECT level FROM data_distrust_state WHERE exchange = $6 AND symbol = $5)     AS distrust_level;
```

ASSET_CLASS 집계는 `symbol→asset_class` 매핑(정적 화이트리스트, `src/data/models/asset_class.py` 신규 40줄)으로 Python에서 합산. 활성 control·fence는 `read_fences`(1쿼리)로 별도 — 총 2왕복(R-31 DoD).

### 3.6 fence 관통 제출 시퀀스 (`fenced_submit.py`)

```text
1  gate := evaluate_pre_submit(...)            # RiskDecision(PRE_SUBMIT) + FenceSnapshot F0 (같은 트랜잭션에서 읽음)
2  assert gate.outcome in (ALLOW, REDUCE) and now < gate.expires_at             else RISK_DECISION_EXPIRED
3  assert pre_trade.decision_id 유효(만료 전) and pre_trade.tenant_id == ctx.user_id else INTEGRITY_RISK_FINGERPRINT_MISMATCH
4  INSERT orders(..., status='CREATED', risk_decision_id=pre_trade.decision_id)  # claim(기존 멱등)
5  F1 := read_fences(pairs)                                                      # 부작용 직전 재조회
6  if F1 != F0: UPDATE orders SET status='ABORTED_FENCE' WHERE order_id=$1 AND status='CREATED'; audit; raise FenceStaleError
7  resp := adapter.place_order(order)                                            # 유일한 부작용
8  F2 := read_fences(pairs)
9  if F2 != F1: metric post_fence_side_effect_total += 1; best-effort adapter.cancel_order; audit CRITICAL
10 기존 커밋·발행 경로
```

`pairs = fence_pairs_for(tenant_id, provider_code, execution_ref)` = `(GLOBAL,""),(PROVIDER,code),(TENANT,tid),(ACCOUNT,tid),(STRATEGY_DEPLOYMENT,execution_ref)` 5쌍 고정. F0/F1/F2 비교는 토큰 증가만 stale로 본다(감소는 DB 제약상 불가).

### 3.7 분할 청산 계획 (`liquidation_planner.py`)

```python
class LiquidationSlice(BaseModel, frozen=True): seq: int; symbol: str; quantity: Decimal; not_before_offset_sec: int; order_type: Literal["LIMIT","MARKET"]; limit_tolerance_bps: int
class LiquidationPlan(BaseModel, frozen=True): request_id: UUID; seed_hash: str; slices: tuple[LiquidationSlice, ...]; deadline_offset_sec: int; adverse_move_abort_pct: Decimal
```

알고리즘(심볼별, 결정론적 PRNG = `random.Random(int.from_bytes(seed[:8]))`): ① `n = clamp(ceil(notional / max_slice_notional), slice_count_min, slice_count_max)`; ② 기본 크기 `q/n`에 `±size_jitter_pct` 지터 → 합이 `q`가 되도록 마지막 슬라이스에서 lot 단위로 보정; ③ `volume_5m[symbol]`이 있으면 슬라이스 ≤ `max_participation_pct`% × volume_5m/n(초과 시 n 증가, 상한 도달 시 남은 수량은 마지막 MARKET 슬라이스); 없으면 최소 notional 슬라이스로 n=slice_count_max; ④ 간격 `U(interval_min_sec, interval_max_sec)` 누적 → `not_before_offset_sec`; ⑤ 마지막 슬라이스는 MARKET, 나머지는 LIMIT(mid ∓ tolerance, TTL `slice_ttl_sec`, 미체결 잔량은 다음 슬라이스에 합산); ⑥ `deadline_offset_sec = total_deadline_sec`. 검증: `Σ quantity == q`, 슬라이스 ≥ 3(수량이 3 lot 미만이면 1 MARKET), 같은 seed → 동일 plan(테스트).

### 3.8 Kill switch 범위 → 대상 매핑 (`legacy_execution_pauser.py` / paper_control)

| scope | scope_ref 형식 | legacy `strategy_executions` 조건 | paper_control | 권한 |
|---|---|---|---|---|
| GLOBAL | `""` | `status='RUNNING'` 전부 | `list_running_deployments()` | admin |
| PROVIDER | `provider_code`(예 `bitget`) | `exchange = ref` | provider 일치 배포 | admin |
| TENANT | `users.user_id` | `user_id = ref` | tenant 배포 | admin |
| ACCOUNT | `users.user_id`(Phase 1: tenant=account) | `user_id = ref` | tenant 배포 | admin 또는 본인 |
| STRATEGY_DEPLOYMENT | `exec:<int>` 또는 `dep:<uuid>` | `id = int` | `deployment_id = uuid` | admin, 실행 소유자, RiskGuard(system) |

정지 후 `strategy_executions.paused_by_control_id`(신규 컬럼, `f4b9d6e5a7c8`에 포함)로 어떤 통제가 멈췄는지 추적 — 해제 시 자동 재개는 없고, `start`/`resume`가 RECOVERY 게이트를 통과해야 한다.

### 3.9 tick 안의 사전 검사 시퀀스 (`tick_risk_phase.py`)

```text
t0  intent := OrderIntent.from_allocation(allocation, signal, ref_price=candles[-1].close, asset_class=lookup(symbol))
t1  histories := candle_cache.get_many(adapter, symbols=positions∪{symbol}, bars=policy.var.lookback_bars+1)   # 캐시 적중 시 I/O 없음
t2  inputs := assemble_risk_inputs(...)                      # 2 DB 왕복(§3.5) + risk_stats 순수 계산
t3  decision := risk_engine.check_decision(inputs, gate_kind=PRE_TRADE, trace_id, ttl=interval_sec)
t4  await recorder.record(decision, inputs)                  # 거부·허용 모두 WORM insert (p99 예산에 포함)
t5  if not decision.is_actionable(now): return               # 다음 tick — FSM 미접촉
t6  if decision.outcome == REDUCE: allocation = allocation.model_copy(update={"approved_quantity": parse_obligation(...)})
t7  → 기존 paused_by 재확인 → pre_submit(is_submission_allowed, fence_snapshot 관통) → FSM writer → executor.execute(..., risk_decision_id=decision.decision_id)
```

`executor.py`(FPO)는 시그니처를 바꾸지 않는다 — `risk_result: RiskCheckResult`에 `decision_id`를 옵션 필드로 추가(`models.py`, 하위호환)해 `submit_order`까지 전달한다.

## 4. 불변조건·상태기계

### 4.1 불변조건

| ID | 불변조건 | 강제 위치 | 위반 시 |
|---|---|---|---|
| I1 | **Master Authority**: `orders` 행은 유효(만료 전·fingerprint 일치)한 ALLOW/REDUCE `risk_decision`을 참조하거나 `liquidation_request`를 참조한다 | DB 트리거 `orders_require_risk_decision` + `fenced_submit` | fail-closed(INSERT 거부) |
| I2 | 어떤 입력이든 `None`이면 해당 규칙은 DENY; 규칙 예외(exception)도 DENY(`RISK_RULE_ERROR:<rule>`) | `rules/base.py`, `evaluator` try/except | fail-closed |
| I3 | Kill switch 권위는 `safety_control`+`safety_fence`뿐. `system_safety_state`는 CB level만 보유 | 코드(`KillSwitchService`가 유일한 writer), CI grep(`INSERT INTO safety_control` 호출부 1곳) | — |
| I4 | fence는 (scope, scope_ref)별 단조증가; 관측 스냅샷과 현재가 다르면 부작용 금지 | DB(`safety_fence` UPDATE는 `+1`만) + `fenced_submit` | fail-closed |
| I5 | `halted`/`emergency`는 자동 하향 불가; 하향은 `recovery_gate` ALLOW + APPROVED 승인 | 코드 + DB CHECK 불가(상태값만) → 통합 테스트 | fail-closed(유지) |
| I6 | 규칙 번들은 scope당 ACTIVE 1개; 결정은 항상 ACTIVE 번들의 `rule_hash`를 기록; yaml hash 불일치면 모든 결정 DENY | DB partial unique + 로더 검증 | fail-closed |
| I7 | `risk_decision`·`risk_rule_bundle.{rule_hash,policy_snapshot}`는 WORM | DB 트리거 | — |
| I8 | 모든 결정·한도·통제·신호 조회는 `tenant_id` 조건 포함(GLOBAL/PROVIDER 예외는 명시 상수) | 코드 + 적대 테스트 | — |
| I9 | `src/core/risk/**`, `src/core/risk_stats/**`, `src/core/safety/**`, `src/foundation/risk_gate/**`, `src/services/safety/**`, `risk_decision_recorder.py`는 LLM 클라이언트·프롬프트 서비스를 import하지 않는다 | `tests/adversarial/risk/test_no_llm_in_risk_path.py`(import graph 검사: `anthropic`, `openai`, `google.generativeai`, `src/services/strategy_prompt_service`, `src/core/llm`) | CI 실패 |
| I10 | ALLOW는 한 subject(intent) 전용, TTL 후 무효, 다른 intent에 이전 불가 | fingerprint에 `inputs_hash`(intent 포함) | 409 |
| I11 | 결정 지연 p99 ≤ 50 ms(PRE_TRADE, I/O 제외 순수 평가 ≤ 5 ms) | 벤치마크 단언 | CI 실패 |

### 4.2 outcome 합성 규칙 (`evaluator.py`)

평가 순서 고정: `safety_state → exposure_limits → daily_loss → max_drawdown → leverage → concentration → strategy_allocation → var_es → correlation → trade_frequency`. 첫 DENY에서 단락(그 이후 규칙은 `rule_results`에 없음). DENY가 없으면 심각도 `PAUSE > REDUCE > ESCALATE > ALLOW`로 합성. `REDUCE`는 `intent.reduce_only=False`이고 concentration/exposure 위반량이 수량 축소로 해소 가능할 때만(`obligations=["REDUCE_QUANTITY_TO:<q>"]`, q = 한도 도달 수량을 lot 단위로 내림); 축소 후 수량이 0이면 DENY. `ESCALATE`는 warning 임계치·soft limit·CB warning. 단락은 DENY에만 적용된다 — 감사가 "어디서 막혔는지"를 보려면 통과 규칙은 끝까지 기록돼야 한다.

### 4.3 상태 전이표

**safety_control** (kill switch)

| from | event | guard | to | side-effect | 감사 이벤트 |
|---|---|---|---|---|---|
| — | `activate(scope, ref)` | 권한(§2.4 activate), scope_ref 형식 | ACTIVE | fence(scope,ref)+1; 캐시 무효화; legacy 실행 `RUNNING→PAUSED(SAFETY_LAYER)` 행별 조건부; paper_control fan-out; `open_order_sweeper` enqueue; 알림 | `safety_control_activated` |
| ACTIVE | `deactivate(control_id, evidence_ref)` | admin 또는 self ACCOUNT; `evidence_ref` 필수 | INACTIVE | 캐시 무효화; **아무것도 재개하지 않음** | `safety_control_deactivated` |
| INACTIVE | `resume` 요청 | RECOVERY 게이트 ALLOW | (실행/배포 측 상태) | 실행 측 start 경로가 게이트 재평가 | `recovery_review_passed` |

**circuit breaker** (`system_safety_state`, 조건부 UPDATE)

| from | event | guard | to | side-effect | 감사 |
|---|---|---|---|---|---|
| L | `evaluate(m)` computed > L | — | computed | 대기 재가동 요청 취소; `restricted` 이상이면 PROVIDER control(reason `cb:<level>`) 생성 | `circuit_breaker_level_raised` |
| warning/restricted | computed < L | — | computed | — | `circuit_breaker_level_lowered` |
| halted/emergency | computed < L 지속 ≥ `cooldown_sec` | 재가동 요청 없음 | 동일(대기) | 승인 요청 생성(PLATFORM, 180 s 강제대기) | `circuit_breaker_reactivation_requested` |
| halted/emergency + 대기 | 승인 APPROVED | `can_reactivate()`: evidence_ref 존재 ∧ metrics 이력이 cooldown 동안 warning 미만 ∧ RECOVERY 결정 ALLOW ∧ 승인 TTL 이내 | normal | `cb:*` PROVIDER control INACTIVE(재개는 아님) | `circuit_breaker_reactivated` |
| halted/emergency + 대기 | computed ≥ L 재악화 | — | 유지 | 요청 취소 | `circuit_breaker_reactivation_cancelled` |

**liquidation_request**

| from | event | guard | to | side-effect | 감사 |
|---|---|---|---|---|---|
| — | watchdog LIQUIDATE | failure_domain ≠ DB_ISOLATED, control ACTIVE | REQUESTED | control 생성(GLOBAL 또는 ACCOUNT), row INSERT(fence_token 포함) | `liquidation_requested` |
| REQUESTED | worker 픽업 | fence 현재값 == row.fence_token | PLANNED | `plan_liquidation(seed=HMAC(secret, request_id))` 결과 JSONB, slices INSERT | `liquidation_planned` |
| PLANNED/EXECUTING | slice due | fence 동일 ∧ now ≥ not_before | EXECUTING | reduce-only 주문(`is_liquidation=TRUE`), slice SENT/FILLED/FAILED | `liquidation_slice_sent` |
| EXECUTING | 모든 slice 종료 | — | DONE/PARTIAL | 잔여 수량 보고, 알림 | `liquidation_completed` |
| 임의 | adverse move > `adverse_move_abort_pct` 또는 deadline | — | EXECUTING(시장가 잔량 1회) → DONE/PARTIAL | 8.6-A-1 "즉시 시장가" 폴백 | `liquidation_fallback_market` |
| 임의 | fence 변경(새 control) | — | ABORTED | 진행 중 슬라이스 취소 시도 | `liquidation_aborted` |

**risk_rule_bundle**: `DRAFT →(approve: risk officer ≠ created_by, approval_ref 필수)→ APPROVED →(activate: 기존 ACTIVE는 같은 트랜잭션에서 RETIRED, effective_from=now)→ ACTIVE →(retire)→ RETIRED`. 모든 전이는 `conditional_update(expected_state)`. **data_distrust_state**: `NORMAL ↔ DISTRUSTED`(히스테리시스), `* → SUSPICIOUS`(sources < min_sources), `SUSPICIOUS → NORMAL`(쿼럼 복구 + 편차 < exit 60 s 지속). SUSPICIOUS/DISTRUSTED는 사전 검사에서 신규 진입 DENY.

## 5. 동시성·멱등성·트랜잭션 경계 (105번)

| 쓰기 | 패턴 | 멱등키 / 스코프 | outbox |
|---|---|---|---|
| `safety_fence` 증가 + `safety_control` INSERT | 단일 트랜잭션, `INSERT … ON CONFLICT DO UPDATE SET current_token = current_token+1 RETURNING`(단조, 105 §2.2 예외 인정) | 요청 `Idempotency-Key`(라우터) → `safety_control.idempotency_digest`(신규 컬럼, UNIQUE, sha256(scope,ref,reason,actor)) 24 h | 아니오(동일 트랜잭션에 `audit_event`) |
| legacy 실행 정지 | 행별 `UPDATE strategy_executions SET status='PAUSED', paused_by='SAFETY_LAYER', paused_by_control_id=$c WHERE id=$1 AND status='RUNNING' RETURNING id` | control_id | — |
| paper_control fan-out | 기존 `increment_fence(expected_state=RUNNING)` | `risk-pause:<control_id>` | 기존 |
| `open_order_sweeper` | 주문별 `UPDATE orders SET status='CANCEL_REQUESTED' WHERE order_id=$1 AND status IN ('SUBMITTED','PARTIALLY_FILLED') RETURNING` 후 어댑터 cancel; 결과는 reconcile에 위임 | `sweep:<control_id>:<order_id>` | — |
| `system_safety_state` level | `UPDATE … WHERE id=1 AND circuit_breaker_level=$expected RETURNING`(현재 무조건 UPDATE → 교정) | — | — |
| `risk_decision` INSERT | append-only; PK 충돌은 버그로 간주(재시도 없음) | `decision_id`(UUID4, 호출자 생성) | 아니오 — 이벤트 발행은 커밋 후 in-process bus(유실 허용: 결정 자체가 원천, 알림은 `risk_signal`로 재파생) |
| `orders` INSERT with `risk_decision_id` | 기존 claim-then-send + `fenced_submit`: gate 스냅샷 → `INSERT orders`(claim) → **fence 재조회** → `place_order` → fence 재조회(post-fence 계수) | 기존 `client_order_id` | 기존 |
| `equity_tracker.save_equity_baseline` | `UPDATE … SET equity_peak_value=GREATEST(equity_peak_value,$4), equity_day_start_date=$2, equity_day_start_value=CASE WHEN equity_day_start_date IS DISTINCT FROM $2 THEN $3 ELSE equity_day_start_value END WHERE id=$1 RETURNING`(단조·CASE로 lost update 제거) | execution_id | — |
| `risk_limit` upsert | `INSERT … ON CONFLICT (…) DO UPDATE … WHERE risk_limit.updated_at = $expected_updated_at RETURNING` (낙관적) | — | — |
| `risk_rule_bundle` 전이 | `conditional_update(expected_state_column="state")`; activate는 `SELECT … FOR UPDATE` 로 ACTIVE 행 잠근 뒤 RETIRED→ACTIVE 두 UPDATE를 한 트랜잭션 | bundle_id | — |
| `risk_signal` | `INSERT … ON CONFLICT (dedupe_key) DO NOTHING RETURNING id`(반환 없음=중복, RSK-008) | `dedupe_key = f"{type}:{scope_ref}:{floor(as_of, 5min)}"` | — |
| `liquidation_request`/`slice` | 상태 전이 전부 `conditional_update`; worker는 `SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` | request_id, (request_id, seq) | — |
| `data_distrust_state` | `INSERT … ON CONFLICT (exchange,symbol) DO UPDATE … WHERE data_distrust_state.updated_at < EXCLUDED.updated_at` | — | — |
| 결정 캐시 | PRE_TRADE는 캐시 없음(매 tick 새 결정). DEPLOYMENT/PRE_INTENT 캐시(10 s)의 fingerprint에 `rule_hash`·`fence_snapshot` 포함 → control 변경 시 자연 무효 + 기존 `invalidate_evaluations` 유지 | — | — |

트랜잭션 경계: `KillSwitchService.activate`는 (control+fence+audit) 1개 트랜잭션 커밋 후 fan-out(각각 독립 트랜잭션, 실패는 `risk_signal` PROVIDER_OUTAGE로 기록하고 재시도 루프). 응용 계층은 커넥션을 쥔 채 두 번째 커넥션을 얻지 않는다(§2 P1 교착 패턴 금지).

## 6. 실패 모드와 복구

| 실패 | 감지 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|
| 캔들 lookback 부족(< min_bars) | `StatsInputs.bars_used` | VaR/상관 규칙 DENY(`RISK_INPUT_MISSING:stats.var_pct`) | 히스토리 캐시 백필(리프 R-18) | decision(DENY) |
| 참조 시세 2개 이상 실패 | `sources_available < min_sources` | SUSPICIOUS → 신규 진입 DENY, reduce-only 허용 | 소스 복구 + exit 지속 | `risk_signal DISTRUST`, decision |
| yaml 편집됐으나 번들 미승인 | 로더 hash 비교 | 전 결정 DENY(`RISK_RULE_BUNDLE_INACTIVE`) + CRITICAL alert | 번들 DRAFT→APPROVED→ACTIVE | `rule_bundle_mismatch_detected` |
| DB 단독 장애(메인) | tick 예외 | 결정 불가 → 제출 없음(I1) | 재연결 | 로그만(DB 없음) |
| DB 단독 장애(watchdog) | split_brain DB_ISOLATED | LIQUIDATE→HALT 강등, DB 쓰기 안 함 | DB 복구 후 다음 사이클 | 로그만 |
| 메인 프로세스 사망 | heartbeat age ≥ 30 s | watchdog HALT control(DB) | 재시작 시 `recovery_wiring` + RECOVERY 게이트; 자동 RUNNING 없음 | `watchdog_decision_applied` |
| kill switch와 submit 경합 | fence 재조회 | `FenceStaleError`, 주문 claim 행 `status='ABORTED_FENCE'` | — | `post_fence_side_effect_prevented` |
| 어댑터 호출 후 fence 변경(진짜 post-fence) | 호출 후 재조회 | 즉시 cancel 시도 + `post_fence_side_effect_total`+1 (SLO 0) | reconcile | CRITICAL alert |
| 부분체결 중 청산 abort | slice 상태 | 잔여 수량 PARTIAL 보고 | 운영자 수동 또는 새 request | `liquidation_completed(PARTIAL)` |
| 공급자 상태 불명(SENT_UNKNOWN) | order_service reconcile | 해당 심볼 신규 진입 DENY(`risk_signal RECON_MISMATCH`) | reconcile 확정 | signal |
| 시계 드리프트 | `evaluated_at`와 DB `now()` 차 > 2 s(recorder가 비교) | 결정 DENY(`RISK_INPUT_STALE`) + alert | NTP | `clock_skew_detected` |
| 재시작 후 기준점 유실 | `equity_tracker.seed` | DB 기준점 복원(기존) | — | — |
| 승인 대기 중 재악화 | CB evaluate | 요청 취소, 상태 유지 | 새 대기 | `circuit_breaker_reactivation_cancelled` |
| 재생 불일치 | 야간 replay | CRITICAL alert, 해당 rule_hash 결정 전부 재검토 목록 | 엔진/번들 변조 조사 | `risk_replay_mismatch` |
| 규칙 함수 예외 | evaluator except | 해당 규칙 DENY(`RISK_RULE_ERROR`) | 버그 수정 | decision + error log |

## 7. 성능·SLO·관측성 (108번)

| SLI | 목표 | 측정 지점 | 메트릭 |
|---|---|---|---|
| PRE_TRADE 결정 지연(조립+평가+기록, 거래소 I/O 제외) | p99 ≤ 50 ms, p50 ≤ 15 ms | `tick_risk_phase` | `aios.core_risk.pre_trade_decision.duration_seconds{outcome}` |
| 순수 평가(`evaluate`) | p99 ≤ 5 ms | evaluator | `aios.core_risk.evaluate.duration_seconds` |
| PRE_SUBMIT 게이트 | p99 ≤ 20 ms | `evaluate_pre_submit` | `aios.foundation_risk_gate.pre_submit.duration_seconds` |
| kill switch 활성화→모든 대상 정지 | p99 ≤ 500 ms(≤100 실행) | `KillSwitchService` | `aios.safety.kill_switch.fanout.duration_seconds{scope}` |
| signal→PAUSE | ≤ 2 s | intraday monitor | `aios.safety.signal_to_pause.duration_seconds` |
| post-fence 부작용 | **0** | fenced_submit | `aios.safety.post_fence_side_effect.count_total` |
| 결정 결손율 | < 1%/5 min | recorder | `aios.core_risk.decision.count_total{outcome,gate_kind,reason_code}` |
| 규칙별 거부율 | 대시보드 | recorder | `aios.core_risk.rule_result.count_total{rule_id,outcome}` |
| 입력 결손 | alert ≥ 5/5 min | rules | `aios.core_risk.input_missing.count_total{field}` |
| 재생 불일치 | 0 | replay | `aios.core_risk.replay_mismatch.count_total` |
| 번들 skew | 0 | loader | `aios.core_risk.rule_bundle_mismatch.count_total` |
| CB 지표 | gauge | collector | `aios.safety.circuit_breaker.{api_error_rate_pct,data_delay_seconds,order_reject_rate_pct,api_disconnect_seconds}` |
| 청산 진행 | gauge | worker | `aios.safety.liquidation.remaining_notional`, `.slice.count_total{state}` |
| 캔들 캐시 적중 | > 95% | candle_history | `aios.core_risk.candle_cache.hit_total`, `.miss_total` |

로그 필드(모든 결정·통제·청산 로그 라인): `trace_id, tenant_id, actor_subject_id("system" 허용), component("core.risk.evaluator" | "foundation.risk_gate.application" | "services.safety.kill_switch"), event(과거분사: risk_decision_recorded, safety_control_activated, fence_stale_detected, liquidation_slice_sent, rule_bundle_activated), duration_ms, decision_id, rule_hash, outcome, reason_codes`. 절대 포함 금지: 잔고 원값·자격증명·inputs_snapshot 전문(hash만).

알림(108 §5 공용 4개 + 도메인): (a) `post_fence_side_effect_total > 0` CRITICAL 즉시, (b) `rule_bundle_mismatch` CRITICAL, (c) GLOBAL/PROVIDER control ACTIVE 지속 > 15 min WARN, (d) 규칙 예외율 > 0, (e) `input_missing` ≥ 5/5 min, (f) replay mismatch > 0, (g) 한도 hard 위반 CRITICAL / soft WARN(5 min 중복 억제, `risk_alerting.py`), (h) daily_loss/MDD warning 임계치 ESCALATE → 사용자 알림 채널.

## 8. 테스트 계획

| 층 | 대상 | 필수 negative |
|---|---|---|
| 단위(순수) | 규칙 10개 각각: 통과·경계값(=한도는 통과, 초과는 거부)·결손=DENY·예외=DENY; evaluator 순서·단락·합성; hashing 결정론(키 순서·Decimal 표현 무관); `RiskInputs` UTC validator; policy_bundle hash 안정성; limits scope 매칭 | 각 규칙 `test_missing_<field>_denies`, evaluator `test_rule_exception_denies_not_allows` |
| 단위(통계) | 파라메트릭 VaR known-value(σ=1%, c=0.95 → 1.645%), 역사적 분위 보간, CF 왜도/첨도 보정 방향, ES ≥ VaR, 포트폴리오 VaR ≤ Σ개별 VaR(상관<1), 상관행렬 대칭·대각 1·min_overlap 미만=None, horizon 스케일링(1m vs 1d 동일 입력 다른 결과) | `test_insufficient_bars_returns_none`, `test_unknown_pair_is_none_not_zero` |
| 단위(안전) | planner: 결정론(seed 동일→plan 동일), 슬라이스 합=수량, 참여율 상한, 최소 3분할, volume None이면 최소 슬라이스; recovery_gate: evidence 없음/cooldown 미달/approval 만료/fresh DENY 각각 거부; market_correlation basket<3=None; freshness 관측 0건=None | 각 1개 이상 |
| 계약 | `RiskDecision`·`RiskInputs` JSON Schema 스냅샷(`tests/unit/contracts/fixtures/risk_decision_v1.json`), 필드 제거 시 실패; foundation contracts re-export 동일성 | `test_schema_removed_field_fails` |
| 통합(실DB) | recorder WORM(UPDATE/DELETE RAISE); kill switch 5범위 각각 → 대상 실행만 PAUSED, 타 테넌트 미영향, fence 증가, 감사 행; `orders` 트리거(결정 없음 INSERT 실패, 청산은 request 필요); 번들 DRAFT→ACTIVE, 두 번째 ACTIVE 실패, hash 변조 UPDATE 실패, yaml 불일치 → 전 결정 DENY; replay 일치·의도적 변조 불일치; risk_limit 표현식 UNIQUE·breach 기록; CB 루프 지표 실측→level; halted 자동하향 없음(FD-9.4 완료조건); intraday monitor stale→PAUSE control; distrust 3소스 중 1 왜곡=NORMAL, 2 왜곡=DISTRUSTED, 2 실패=SUSPICIOUS; watchdog LIQUIDATE→request+control; worker 슬라이스 실행·fence 변경 ABORTED | 각 파일 ≥1 |
| 적대적 | RSK-005 `asyncio.gather(activate_control, submit)` → 어댑터 호출 0 또는 cancel 1, post-fence 0; RSK-006 라우터/에이전트 입력으로 `risk_decision` 위조 시도 → fingerprint 불일치 409, 타 테넌트 decision_id 사용 409; 교차 테넌트 한도·결정·통제 조회 0건; LLM import 그래프 0건; WORM 우회(직접 SQL) 실패 | 전부 negative |
| 성능 | `tests/performance/test_pre_trade_latency.py`: 캐시 워밍 후 1,000회 PRE_TRADE p99 ≤ 50 ms **단언**(CI), 순수 evaluate p99 ≤ 5 ms; 결과를 `docs/benchmarks/`에 덮어쓰지 않음(§9 정정) | 회귀 시 CI 실패 |
| 78번 명명 테스트 | RSK-001~010 각각을 위 테스트 함수명에 접두어로 매핑(`test_rsk001_…`) | — |

## 9. 리프 목록 (구현 순서) — 리프 = 커밋 = 파일 하나(테스트 동반), `git commit -F - -- <경로>`, 즉시 push

DoD 공통: `.venv/Scripts/python.exe -m pytest -q <테스트 경로>` 통과, `ruff`·`mypy --strict` 통과, 줄수 ≤ 상한, `scripts/check_zone_manifest.py` 통과. FPO 리프는 PM 승인 커밋 해시를 커밋 메시지에 인용.

| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| R-01 | `src/core/risk/hashing.py` + `tests/unit/core/risk/test_hashing.py` | — (FPO) | 같은 dict 키 순서 달라도 hash 동일, Decimal("1.0")≠Decimal("1.00") 아님(정규화) 테스트 | S |
| R-02 | `src/core/risk/decision.py` + `tests/unit/contracts/test_risk_decision_schema.py` | R-01 | 스키마 스냅샷 생성·naive datetime 거부 | S |
| R-03 | `src/core/risk/inputs.py` + `test_inputs_contract.py` | R-02 | `from_legacy_dict` 왕복, `inputs_hash` 안정 | M |
| R-04 | `src/core/risk/rules/base.py` | R-03 | `missing()`이 DENY·missing_fields 채움 | S |
| R-05~R-13 | `rules/{daily_loss,max_drawdown,leverage,concentration,strategy_allocation,var_es,correlation,trade_frequency,safety_state}.py` 각 + 테스트 | R-04 | 규칙별 §8 단위 표 전부; concentration은 "체결 후 비중" 테스트(기존 30% + 신규 5% → 35% 거부) | S×9 |
| R-14 | `src/core/risk/limits.py` + `test_limits.py` | R-04 | scope 매칭(SYMBOL 한도가 다른 심볼에 미적용), hard/soft | M |
| R-15 | `src/core/risk/policy_bundle.py` + `test_policy_bundle.py` | R-01 | yaml 주석 변경은 hash 불변, 수치 변경은 변경 | S |
| R-16 | `src/core/risk/evaluator.py` + `test_evaluator.py` | R-05~15 | 순서·단락·REDUCE 산출·예외=DENY·latency_us>0 | M |
| R-17 | `src/core/risk/engine.py` 축약(facade) + `models.py` `decision_id` 옵션 필드 | R-16 | 기존 `tests/unit/core/test_risk_engine.py` 전부 무수정 통과 + `check_decision`; `RiskCheckResult()` 기본 생성 하위호환 | S |
| R-18 | `src/core/risk_stats/{models,returns}.py` + 테스트 | — (SC) | bars_per_day("1m")=1440, "1d"=1; log 수익률 길이 n−1 | S |
| R-19 | `risk_stats/var_parametric.py`, `var_historical.py`, `var_cornish_fisher.py` 각 + 테스트 | R-18 | known-value 3개, ES ≥ VaR | S×3 |
| R-20 | `risk_stats/correlation_matrix.py`, `portfolio.py` + 테스트 | R-18 | 대칭·None·포트폴리오 VaR ≤ Σ | M |
| R-21 | `config/risk_policy.yaml` §3.3 확장 + `risk_policy_loader.py` 모델 + `test_risk_policy_loader.py` | — (OP/SC) | 범위 제약, `verify_policy_against_bundle` | S |
| R-22 | 마이그레이션 `a9c4e1f7b2d3_risk_rule_bundle.py` + `adapters/postgres_bundle_repository.py` + `test_rule_bundle_activation.py` | R-15 | partial unique·WORM 컬럼·conditional 전이 | M |
| R-23 | `application/activate_rule_bundle.py` + 라우터(`/foundation/risk-gate/rule-bundles`) + 테스트 | R-22 | 승인자=작성자 거부, approval_ref 필수, 감사 이벤트 | M |
| R-24 | 마이그레이션 `b8d5f2a1c3e4_risk_decision_worm.py` + `postgres_decision_repository.py` + `test_worm_tables.py` | R-02 | UPDATE/DELETE RAISE | M |
| R-25 | `src/services/risk_decision_recorder.py` + `test_risk_decision_recorder.py` | R-24 | insert + audit_log + 이벤트, 시계 드리프트 DENY | M |
| R-26 | 마이그레이션 `c7e6a3b2d4f5_risk_limit.py` + `postgres_limit_repository.py` + `upsert_risk_limit.py` + `test_risk_limits_db.py` | R-14 | 표현식 UNIQUE, 교차 테넌트 0건 | M |
| R-27 | `execution_loop/exposure_snapshot.py` + 통합 테스트 | R-26 | 단일 쿼리, 6개 scope 키, 다른 사용자 포지션 미포함 | M |
| R-28 | `execution_loop/candle_history.py` + 테스트 | — | TTL·bars, 실패 시 stale 캐시 반환 안 함(None) | S |
| R-29 | `execution_loop/var_estimator.py` 교체 + `correlation_service.py` 신규 + `correlation.py` 삭제 + 테스트 | R-19,20,28 | 하드코딩 표 참조 0건(grep) | M |
| R-30 | `execution_loop/equity_tracker.py` UTC·단조 UPDATE + 테스트(105 §4.2 형태 B) | — | 선행 변경 주입 후 peak 역행 없음 | S |
| R-31 | `execution_loop/risk_inputs_assembler.py` + `account_state.py` 축약 + 통합 테스트 | R-03,27,28,29,30 | DB 왕복 ≤ 2회(쿼리 카운트 단언), 모든 필드 채움 또는 None | L |
| R-32 | `execution_loop/tick_risk_phase.py` + `tick.py` 수정 + `test_execution_tick.py` 보강 | R-17,25,31 | 거부도 결정 기록, ALLOW 결정 id가 executor로 전달 | M |
| R-33 | `domain/fence.py`, `application/read_fence.py`, repo `read_fences` + 테스트 | — | 5쌍 1쿼리, is_stale | S |
| R-34 | 마이그레이션 `f4b9d6e5a7c8` + `contracts/v1.py`·`domain/models.py` 확장 + `evaluate_risk_gate.py` trace_id | R-33 | 기존 `test_risk_gate_lifecycle.py` 통과 | S |
| R-35 | `application/evaluate_pre_submit.py` + `test_pre_submit_gate.py` | R-33,34,25 | control/CB/distrust/fresh 각각 단독 DENY·PAUSE, TTL 2 s | M |
| R-36 | `order_service/gate.py`·`foundation_gate.py`·`pre_submit_check.py` 수정 + `test_order_service_risk_gate.py` 보강 | R-35 | mandate 필수 기본값, fence_snapshot 관통 | M |
| R-37 | 마이그레이션 `93c0e7f6b8d9_orders_risk_decision_fk.py` + `order_service/fenced_submit.py` + `test_fenced_submit.py` + `tests/adversarial/risk/test_fence_race.py` | R-36 | gather 경합 post-fence 0, 트리거 거부 | L |
| R-38 | `services/safety/legacy_execution_pauser.py` + 테스트 | — | 5범위 매핑, 행별 RETURNING | S |
| R-39 | `services/safety/open_order_sweeper.py` + 테스트 | R-38 | 멱등, 취소 불가 주문 skip 보고 | M |
| R-40 | `services/safety/kill_switch_service.py` + `activate_safety_control.py` 훅 + `test_kill_switch_service.py` | R-38,39 | 5범위 통합, 타 테넌트 미영향, `INSERT INTO safety_control` 호출부 1곳 grep | L |
| R-41 | `risk_guard_service.py` → KillSwitchService 사용 + 테스트 | R-40 | pause 직접 호출 0건 | S |
| R-42 | `core/safety/data_freshness.py` 신규 + `exchanges/common/instrumented_adapter.py`에 `freshness` 옵션 인자 + 테스트 | — | 관측 0건=None, `get_ohlcv` 후 close_time 기록, 기존 `tests/unit/exchanges` 계측 테스트 무수정 통과 | S |
| R-43 | `metrics_collector.py` data_delay 실측 + `circuit_breaker.py` 조건부 UPDATE + 테스트 | R-42 | 상수 0 제거, 105 형태 B | S |
| R-44 | `core/safety/recovery_gate.py` + 테스트 | — | §8 4가지 거부 | S |
| R-45 | `services/safety/circuit_breaker_loop.py` + `main.py` 배선(PM) + `test_circuit_breaker_loop.py` | R-43,44 | halted 자동하향 없음, 승인+evidence+cooldown 후 normal | M |
| R-46 | 마이그레이션 `d6f7b4c3e5a6` + `postgres_signal_repository.py` + `application/intraday_monitor.py` + 테스트 | R-40 | dedupe, stale→PAUSE ACCOUNT control | M |
| R-47 | `services/safety/reference_quotes.py` + 어댑터 2종(§10 미확인 표기) + 테스트(fake) | — | 타임아웃=None | S |
| R-48 | `data_distrust.py` restore + `services/safety/distrust_wiring.py` + `data_distrust_state` 영속 + `test_distrust_wiring.py` | R-46,47 | 1 왜곡 NORMAL / 2 왜곡 DISTRUSTED / 2 실패 SUSPICIOUS, tick 배선 | M |
| R-49 | `core/safety/market_correlation.py` + `watchdog.py` decide 확장 + 테스트 | — | basket<3=None→HALT, DB_ISOLATED→LIQUIDATE 강등 | S |
| R-50 | `core/safety/liquidation_planner.py` + `test_liquidation_planner.py` | R-21 | 결정론·합계·참여율·최소 3분할 | M |
| R-51 | 마이그레이션 `e5a8c5d4f6b7` + `watchdog_process.py` 수정 + `test_watchdog_liquidation_request.py` | R-40,49,50 | LIQUIDATE→control+request, HALT→control, 무조건 UPDATE 0건 | M |
| R-52 | `services/safety/liquidation_executor.py` + `main.py` worker 배선(PM) + `test_liquidation_worker.py` | R-51 | fence 변경 ABORTED, deadline 시장가 폴백 1회, `is_liquidation` 주문은 request 참조 | L |
| R-53 | `application/recovery_gate.py` + 라우터 + `tests/foundation/integration/risk_gate/test_recovery_gate.py` | R-44,35 | evidence 없음 거부, RSK-007 | M |
| R-54 | `application/replay_decision.py` + `src/tools/risk_replay.py` + `test_replay_decision.py` + CI 야간 job | R-24,22 | 일치 exit 0, 변조 exit 2 | M |
| R-55 | `services/risk_alerting.py` + 테스트 | R-25 | 5분 중복 억제, hard=CRITICAL | S |
| R-56 | `tests/adversarial/risk/{test_no_llm_in_risk_path,test_agent_cannot_forge_allow,test_cross_tenant_limits}.py` | R-37,40 | 전부 negative 통과 | M |
| R-57 | `tests/performance/test_pre_trade_latency.py` + CI 단언 | R-32 | p99 ≤ 50 ms 단언 | S |
| R-58 | `docs/RED_TEAM_FINDINGS.md` 등재(correlation 0.0 fail-open, data_delay 상수 0, watchdog None 고정, foundation_gate mandate 우회 플래그, `_apply_decision` 무조건 UPDATE, `_set_level` 무조건 UPDATE, strategy_allocation 분모) | — | 번호 부여 | S |

병렬 가능 묶음: {R-01~17 FPO 코어} ∥ {R-18~20 통계} ∥ {R-22~26 저장소} ∥ {R-33~35 foundation} ∥ {R-42~44 안전장치 순수}. 직렬 필수: R-31→R-32→R-37, R-40→R-41/46/51→R-52. 마이그레이션 7개는 PM이 체인 순서를 확정한 뒤 각 리프에서 `down_revision`을 채운다.

## 10. 미확정·리스크

| 항목 | 상태 | 결정 필요자 |
|---|---|---|
| `src/core/risk/**` 신규 파일·engine.py 축약은 FROZEN_PAPER_ONLY 변경 | PM 승인 필요(§2-B 규칙 6). 대안: rules를 `src/core/risk_rules/`(SC)로 두고 engine.py만 1줄 위임 — 판단 로직이 FPO 밖으로 나가는 것이라 ADR-E 취지와 충돌, 권장하지 않음 | PM |
| 참조 시세 소스(쿼럼 3) | **미확인**: Binance `GET /api/v3/ticker/price`, OKX `GET /api/v5/market/ticker` 공개 엔드포인트의 무인증 가용성·레이트리밋·심볼 표기. KIS/NH는 KRW 시장이라 크립토 참조 불가 | 외부 문서 확인 후 R-47 |
| 분할 청산 seed 비밀키(HMAC) | 서버 비밀(`secret_loader`)에 `AIOS_LIQUIDATION_SEED_KEY` 추가 — 없으면 worker 기동 거부(fail-closed). 키 회전 시 진행 중 request는 기존 plan JSONB로 계속 | PM |
| `risk_decision` REVOKE UPDATE/DELETE는 DB role 분리 전제 | 현재 단일 role(`wallet_transactions` WORM 보류 사유와 동일) → 트리거로 먼저 강제, role 분리는 별도 결정 | PM/DBA |
| `orders` 트리거 cutover | 마이그레이션 적용 시각 이후 행만 강제. 적용 전 실행 중 tick이 결정 없이 INSERT하면 실패 → R-32 배포 **후** R-37 적용(순서 고정) | PM |
| Watchdog 독립 청산 | 메인 프로세스가 죽으면 청산 worker도 없다. watchdog에 자격증명·어댑터를 주는 것은 8.6-A 격리 원칙과 충돌 → 별도 ADR(현재는 HALT+request 기록까지) | ADR |
| 거래소 세션 일경계 | 크립토는 UTC 00:00 채택. KIS/NH 주식은 KST 장 기준 필요 → `day_boundary`를 provider별로 확장할 때 재검토 | Risk officer |
| VaR 방법론 기본값 | Cornish-Fisher(팻테일 보정)로 제안. 250일 lookback은 신규 상장 심볼에서 min_bars 60 미달 → 해당 심볼 DENY가 의도된 동작인지 확인 | Risk officer |
| 포트폴리오 VaR 분모 | `total_equity`를 USDT 잔고로 근사(11번 FX 계층 부재) — 다통화 계좌에서 과소평가 가능 | 11번 FX 리프 |
| 한도 기본값(플랫폼, tenant_id NULL) | 초기 세트 제안: SYMBOL GROSS 20%, ASSET_CLASS CRYPTO_SPOT 100%, MAX_OPEN_POSITIONS 10, MAX_TRADES_PER_HOUR 60, MAX_ORDER_NOTIONAL 10,000 USDT — 전부 Draft, 번들 승인 대상 | Risk officer |
| `system_safety_state` 폐기 | CB level만 남기고 kill switch 의미 제거. 완전 폐기(level을 `safety_control` reason으로 흡수)는 이 버전 범위 밖 | 후속 |
| 알림 채널 강제 등급 | FD-17 미착수 — `risk_alerting`은 gateway 인터페이스만 호출, 실제 강제 채널 매핑은 FD-17 | FD-17 |
