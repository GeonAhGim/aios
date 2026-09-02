# L4 구현 명세 — 전략·포트폴리오·백테스트·검증·성과 (v1.0)

> 템플릿: `docs/specs/_TEMPLATE.md`. 기준 HEAD `67a7f92`, alembic head `5ed4921f9873`
> (작성 시점 — 마이그레이션 리프 착수 전 `alembic heads` 재확인 필수, §2-B 공유 DB 규칙).
> 이 문서는 "리프 하나 = 파일 하나(≤300줄) = 커밋 하나"로 바로 구현 가능한 단위까지 내린다.

## 0. 문서 메타

| 항목 | 값 |
|---|---|
| status | DRAFT → PM(agent-platform-12) 승인 후 ACTIVE |
| owner role | Strategy/Portfolio 도메인 오너(FROZEN_PAPER_ONLY 변경은 PM 승인, 감사 §2-B 규칙 6) |
| supersedes | 없음. `03_core_modules_v1.1.md` §3.5~3.6 시그니처·`기능설계문서_v1.21.md` FD-8.1/8.2·마이그레이션 `3b244535b311` docstring의 "Backtest 체크 하나만" 축소 결정을 **확장**한다(대체 아님) |
| depends on | ADR-2026-08-29-E(FROZEN_PAPER_ONLY), 75/76/81번 L3, 105/107/108 표준, FND-03 evidence(`record_command_event`), FND-02 mandates(`contracts/v1.MandateRevisionView`), FND-08 reconciliation(`EntitySnapshot`), 마이그레이션 `b3f7e0c1a4d5`(strategy_executions.mandate_revision_id, 44 세션) |
| implemented by | §2 표의 파일 경로 전부. 기존 파일은 `[기존]`, 신규는 `[신규]` |
| verification evidence | §8·§9의 테스트 경로. CI 게이트: `ruff` / `mypy --strict` / `scripts/check_zone_manifest.py` / `pytest --cov=src` |
| 대상 Zone | `src/core/strategy/**`, `src/core/portfolio/**` = **FROZEN_PAPER_ONLY**(`.aios-zone`). 이 문서의 모든 리프는 PAPER 판단 로직만 다루며 LIVE 경로는 `Executor`의 `FrozenZoneLiveModeBlockedError` 하드 가드를 그대로 둔다. 03번 §3.5/§3.6의 **위치 인자 시그니처는 변경하지 않는다**(키워드 전용 인자 추가만) |

---

## 1. 기관급 요구 (왜 기초 수준으로는 부족한가)

### 1.1 기관·자산운용사가 이 도메인에 요구하는 것

| # | 요구 | 근거 |
|---|---|---|
| R1 | **결정론적 재생(replay)**: 같은 전략 아티팩트 + 같은 입력 스냅샷 + 같은 정책 버전이면 신호·배분·백테스트·검증 결과 해시가 바이트 단위로 동일 | 76번 STR-001, 105번, 감사 §6 "reproducibility mismatch rate" |
| R2 | **모든 파라미터 버전화**: 지표 파라미터·조건 문법·사이징 방법·비용 모델·검증 정책·성과 방법론 각각에 버전과 해시. "어느 버전으로 이 숫자가 나왔는가"에 항상 답할 수 있어야 함 | 76번 §1 "compiler version, build environment hash", 81번 "methodology immutable/versioned" |
| R3 | **look-ahead·survivorship 무결성**: 신호 시점에 미래 bar·미래 상장 정보를 볼 수 없음이 코드로 강제되고 테스트로 증명됨. 위반은 경고가 아니라 hard fail | 76번 §3 point-in-time 행, 109번 §5 |
| R4 | **실제 비용·체결 모델**: 수수료(maker/taker)·슬리피지·스프레드·펀딩을 분리 계상. 백테스트와 PAPER가 같은 체결 시뮬레이터 계약을 쓴다 | 76번 "cost model absent = hard fail", 02번 "PAPER 시뮬레이터 최우선" |
| R5 | **과최적화 통제**: walk-forward/OOS 분할, 파라미터 안정성, Deflated Sharpe·PBO를 계산해 검증 결과에 기계판독 가능하게 남김 | 76번 OOS/Robustness 행 |
| R6 | **검증 FAIL 경로가 실재**: 6개 체크 각각 hard fail 조건이 코드로 있고, UI 필드 수정으로 hard fail을 낮출 수 없음 | 76번 §3 |
| R7 | **포트폴리오 실구축**: 목표 비중·사이징 방법(고정비율/변동성 목표/캡 Kelly/리스크패리티)·실행 간 집계(8.2-C)·리밸런싱 회전율·현금/증거금 회계가 전부 존재하고 mandate 제약이 결정론적으로 클램프/거부 | 75번 §3, FD-8.2, 03번 §3.6 docstring |
| R8 | **성과 보고 재현성**: gross/net 분리, TWR·MWR 병기, as-of·방법론·증거 참조 바인딩, PAPER/LIVE 물리 분리, 정정은 새 리비전 | 81번 전체 |
| R9 | **감사 가능성**: 모든 판정(신호·배분·검증·성과)이 evidence 체인(FND-03)에 기록되고 trace_id로 연결 | 108번, 79번 |
| R10 | **동시성**: 상태 저장은 전부 조건부 UPDATE/UNIQUE로, 멱등키는 tenant·아티팩트·입력 해시 범위 | 105번 |

### 1.2 현재 코드 수준과의 격차 (docs/FULL_AUDIT_2026-09-02.md 인용)

| 감사 인용 | 현재 코드 | 격차 |
|---|---|---|
| §5 "StrategyEngine.evaluate 부분 — `confidence=1.0`, `target_position=0`, `stop_loss=None` 상수. 조건식은 순수 AND 또는 순수 OR만" | `src/core/strategy/engine.py:85-88`, `condition_evaluator.py`가 `" AND "`/`" OR "` split만 | 중첩·NOT·괄호 불가, confidence 무의미, 손절/익절 부재, 단일 타임프레임(`tick.py:210` `"1m", limit=100` 하드코딩), crossover 캐시가 프로세스 메모리(`_prev_tick_cache`) → 재시작 시 첫 틱 crossover 항상 False |
| §5 "PortfolioEngine.allocate 불일치 — 8.2-C 집계 없음, BUY=전액·SELL=전량 두 경우뿐" | `src/core/portfolio/engine.py` 72줄 | 사이징 방법 없음, 목표 비중 없음, 실행 간 집계 없음, 리밸런싱·회전율·비용 없음, 현금/증거금 회계 없음, mandate 미참조(`mandates.contracts.v1`을 core가 소비하지 않음) |
| §6 "FND-04 축소 — 6개 체크 중 backtest 1개. `hard_fail_reasons` 항상 빈 튜플 → FAIL 판정이 구조적으로 불가능" | `validation/domain/rules.py:evaluate_validation_policy`, `start_validation.py` `hard_fail_reasons=()` | point-in-time·OOS·robustness·stress·failure-conditions 체크 부재, 아티팩트 해시 없음(`strategies.fsm_definition` 직접 읽음), 정책 버전 없음, evidence 바인딩 0건 |
| §6 "FND-09 performance 미구현 — 디렉터리 없음" | 없음 | 81번 전체 미구현 |
| `run_backtest.py` (감사 §6 FND-10 언급 외) | 매 bar `bars[:bar_index+1]`로 지표 전량 재계산(O(n²)), 비용모델 선형 1종, 분할·OOS·안정성·DSR/PBO 없음, 입력 스냅샷 미영속(해시만 저장) → "재생"이 아니라 "재실행" | R1·R3·R4·R5 전부 미충족 |
| `talib_adapter.py` `_SPECS` | 파라미터 범위 검증 없음(`timeperiod=0`·음수 통과), lookback 계산이 `period_param` 하나뿐(MACD는 slow+signal, BBANDS 등 실제 필요 bar 수와 불일치) | R2·R3 미충족 |
| §6 "응용 계층은 트랜잭션을 열지 않고 repo 호출마다 별도 커넥션" | `start_validation.py` | run 생성→실행→결과 저장 사이 부분 실패 시 RUNNING 고착 |

---

## 2. 모듈 분해 (최소단위)

표기: `[기존]` 수정, `[신규]` 생성. Zone은 `.aios-zone` 기준. 줄수 상한은 300(초과 예상 시 분할 지점 명기). 순수(pure) = 외부 I/O·시계·난수 없음.

### 2.1 전략 엔진 — `src/core/strategy/**` (FROZEN_PAPER_ONLY)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| `[신규] src/core/strategy/condition_ast.py` | 조건 트리 노드 정의(pydantic, 불변) | `AtomNode(key, op, threshold)`, `AndNode(children)`, `OrNode(children)`, `NotNode(child)`, `ConditionNode = Annotated[Union[...], Field(discriminator="kind")]`, `GRAMMAR_VERSION = "cond-v2"`, `leaf_keys(node) -> list[str]`, `node_hash(node) -> str` | 없음(pure) | 150 | FPO |
| `[신규] src/core/strategy/condition_parser.py` | v2 문법 문자열 → AST. 재귀하강, 우선순위 `NOT > AND > OR`, 괄호. **v1 평면 문자열(현 컴파일러 산출)은 v2의 부분집합**이라 그대로 파싱됨 | `parse(expression: str) -> ConditionNode`, `to_canonical(node) -> str`(정규화 직렬화, 왕복 동일성 보장), `ConditionSyntaxError(code="STRATEGY_CONDITION_SYNTAX")` | 없음(pure) | 250 | FPO |
| `[신규] src/core/strategy/indicator_key.py` | 키 문법 단일 출처: `{IND}{_param값}*[@{tf}]` 예 `RSI_timeperiod14@1h`, `MACD_slowperiod26.signal` | `parse_key(key) -> IndicatorKey(indicator, params, output, timeframe|None)`, `format_key(...)`. `market_state.py:_KEY_RE`·`condition_evaluator.extract_indicator_keys`를 이 모듈로 대체 | 없음 | 120 | FPO |
| `[신규] src/core/strategy/state_memory.py` | crossover·상태 메모리 계약(실행별 영속 대상) | `StrategyStateMemory(schema_version="ssm-v1", execution_id, last_bar_time: dict[tf, datetime], prev_values: dict[key, Decimal], state_version: int)`, `advance(memory, market_state, bar_times) -> StrategyStateMemory`(순수, 새 객체 반환) | 없음 | 120 | FPO |
| `[신규] src/core/strategy/tree_evaluator.py` | AST 평가. 누락 키는 `IndicatorDataMissingError`(판단 보류), crossover는 `prev_values`가 **같은 bar_time 기준으로 직전 bar**일 때만 유효 | `evaluate(node, market_state: MarketState, memory: StrategyStateMemory) -> EvalResult(matched: bool, satisfied_leaves: int, total_leaves: int, missing_keys: list[str])` | `condition_ast`, `state_memory` | 200 | FPO |
| `[기존] src/core/strategy/condition_evaluator.py` | 하위호환 파사드: 내부에서 `parse()`+`tree_evaluator` 호출. `extract_indicator_keys`는 `leaf_keys(parse(expr))` 위임. 시그니처 유지 | 기존 `ConditionEvaluator.evaluate(expression, market_state, prev_market_state)` 유지(deprecated 주석) | 위 3개 | 100 | FPO |
| `[신규] src/core/strategy/market_state.py` | 다중 타임프레임 시장상태 DTO. bar 닫힘 시각 포함 → look-ahead 검증 가능 | `MarketState(as_of: datetime, values: dict[str, Decimal], bar_close_time: dict[tf, datetime])`, `assert_no_future(state)`(모든 `bar_close_time <= as_of`, 위반 시 `INTEGRITY_FUTURE_DATA`) | 없음 | 80 | FPO |
| `[신규] src/core/strategy/risk_params.py` | 전략 수준 손절/익절 규칙(고정 % 또는 ATR 배수) — `fsm_definition.risk_params`(선택, JSONB) | `StrategyRiskParams(schema_version="srp-v1", stop_loss_pct: Decimal|None, take_profit_pct: Decimal|None, atr_stop_multiple: Decimal|None, atr_key: str|None)`, `derive_levels(params, entry_price, market_state) -> (stop, tp)` | `market_state` | 120 | FPO |
| `[신규] src/core/strategy/confidence.py` | confidence 결정론 산식: `satisfied/total` 리프 비율에 crossover 리프 가중 1.0·비교 리프 가중 0.5, NOT 하위는 부정 결과 기준. 범위 [0,1], Decimal 4자리 | `compute_confidence(result: EvalResult) -> Decimal` | `tree_evaluator` | 80 | FPO |
| `[기존] src/core/strategy/models.py` | `Signal` MINOR 확장(107번 §3.2, optional만): `signal_id: str|None`(= sha256(strategy_id, version, execution_id, to_state, bar_time)), `bar_time: datetime|None`, `satisfied_leaves/total_leaves: int|None`, `grammar_version: str = "cond-v2"`, `schema_version: str = "signal-v1"` | 기존 필수 필드 불변 | — | 80 | FPO |
| `[기존] src/core/strategy/engine.py` | 오케스트레이션만: 후보 전이 → `tree_evaluator` → `confidence` → `risk_params` → Signal. 프로세스 메모리 캐시 제거, `memory: StrategyStateMemory` 키워드 인자로 주입받고 새 메모리를 반환 | `evaluate(fsm_config, market_state, *, execution_id, fsm_state, memory, as_of) -> tuple[Signal|None, StrategyStateMemory]`(위치 인자 2개 불변). `market_state`는 `dict[str,float]`(v1) 또는 `MarketState`(v2) 둘 다 수용 — dict면 `MarketState.from_flat()` | 위 전부 | 200 | FPO |

### 2.2 지표 레지스트리 — `src/core/indicators/**` (SCAFFOLD)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `[신규] src/core/indicators/spec.py` | 지표 스펙 타입 | `ParamSpec(name, min, max, default)`, `IndicatorSpec(name, inputs: tuple[str,...], params: tuple[ParamSpec,...], outputs: tuple[str,...], lookback: Callable[[dict[str,int]], int], causal: bool = True)`, `REGISTRY_VERSION = "ind-v1"` | 없음 | 80 | SCAFFOLD |
| `[신규] src/core/indicators/specs_talib.py` | 현 `_SPECS` 11개를 `IndicatorSpec`으로 이전 + 정확한 lookback: SMA/EMA/RSI/ATR/CCI/WILLR/MFI = `timeperiod`, MACD = `slowperiod + signalperiod - 1`, BBANDS = `timeperiod`, STOCH = `fastk_period + slowk_period + slowd_period - 2`, OBV = 1. 파라미터 범위: 2 ≤ timeperiod ≤ 500 등 | `TALIB_SPECS: dict[str, IndicatorSpec]` | `spec` | 150 | SCAFFOLD |
| `[신규] src/core/indicators/registry.py` | 조회·파라미터 검증·lookback 산출 단일 진입점 | `IndicatorRegistry.get(name) -> IndicatorSpec`(미지 → `IndicatorError("STRATEGY_INDICATOR_UNKNOWN")`), `validate_params(name, params) -> dict[str,int]`(범위 밖 → `"STRATEGY_PARAM_OUT_OF_RANGE"`), `lookback(name, params) -> int`, `registry_hash() -> str` | `spec`, `specs_talib` | 120 | SCAFFOLD |
| `[신규] src/core/indicators/lookback.py` | 전략 전체 필요 bar 수(타임프레임별 최대 lookback + 여유 1) — `tick.py`의 `limit=100` 하드코딩 대체 | `required_bars(fsm_config, registry) -> dict[tf, int]` | `registry`, `strategy.indicator_key` | 80 | SCAFFOLD |
| `[기존] src/core/indicators/talib_adapter.py` | 계산만. `_SPECS`·`period_param_name` 제거 → registry 위임. `calculate()`가 `validate_params` 통과값만 사용, 반환에 `registry_version` 포함 | `IndicatorService.calculate(indicator, candles, **params) -> IndicatorResult(+registry_version)` | `registry` | 150 | SCAFFOLD |
| `[신규] src/core/indicators/series_cache.py` | 백테스트용 전구간 1회 계산 + point-in-time 조회(O(n²) 제거). 인과성(causal=True) 지표만 허용 | `IndicatorSeriesCache.build(bars_by_tf, keys, service) -> IndicatorSeriesCache`, `value_at(key, bar_index) -> Decimal|None`(`bar_index`보다 큰 인덱스 접근은 코드 경로 자체가 없음) | `talib_adapter` | 150 | SCAFFOLD |

### 2.3 포트폴리오 엔진 — `src/core/portfolio/**` (FROZEN_PAPER_ONLY)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `[기존] src/core/portfolio/models.py` | `AllocationDecision` MINOR 확장: `sizing_method: str|None`, `target_weight_pct: Decimal|None`, `pre_binding_quantity: Decimal|None`, `binding_reasons: list[str] = []`, `decision_hash: str|None`, `schema_version="alloc-v1"`. 신규 DTO: `PortfolioStateInput`, `SizingResult(quantity, weight_pct, method, inputs_hash)` | 기존 4필드 불변 | — | 120 | FPO |
| `[신규] src/core/portfolio/state_input.py` | `current_portfolio_state: dict` 대체 타입(dict도 `from_dict()`로 수용) | `PortfolioStateInput(allocated_capital, position_quantity, current_price, total_equity, cash_available, realized_vol_pct: Decimal|None, win_rate: Decimal|None, avg_win_loss_ratio: Decimal|None, exposures: PortfolioAggregate|None, mandate: MandateRevisionView|None, portfolio_config: PortfolioConfig)` | `mandates.contracts.v1` | 120 | FPO |
| `[신규] src/core/portfolio/config.py` | 실행별 포트폴리오 설정(버전화, `strategy_executions.portfolio_config` JSONB) | `SizingMethod(Enum: FIXED_FRACTIONAL, VOLATILITY_TARGET, KELLY_CAPPED, RISK_PARITY)`, `PortfolioConfig(schema_version="pcfg-v1", method, fraction_pct=100, target_vol_pct: Decimal|None, kelly_cap_pct: Decimal|None, rebalance_band_pct: Decimal = 5, min_trade_notional: Decimal, cost_model: CostModelRef)`, `config_hash()` | 없음 | 120 | FPO |
| `[신규] src/core/portfolio/sizing/fixed_fractional.py` | `qty = allocated_capital × fraction% / price` | `size(inp: PortfolioStateInput) -> SizingResult` | `models` | 60 | FPO |
| `[신규] src/core/portfolio/sizing/volatility_target.py` | `weight = target_vol / realized_vol`(상한 100%), `realized_vol_pct` None → `PortfolioSizingError("PORTFOLIO_SIZING_INPUT_MISSING")` (0으로 대체 금지) | `size(inp) -> SizingResult` | `models` | 80 | FPO |
| `[신규] src/core/portfolio/sizing/kelly_capped.py` | `f* = p − (1−p)/b`, `f = min(max(f*,0), kelly_cap)`; p/b None → 입력 누락 오류 | `size(inp) -> SizingResult` | `models` | 80 | FPO |
| `[신규] src/core/portfolio/sizing/risk_parity.py` | 실행 간 역변동성 가중(8.2-C 집계 입력 필요): `w_i ∝ 1/σ_i`, 합 = 1 | `size(inp) -> SizingResult`(exposures None → 입력 누락 오류) | `models`, `aggregation` | 100 | FPO |
| `[신규] src/core/portfolio/sizing/selector.py` | 방법 → 함수 디스패치, 결과 `inputs_hash` 검증 | `size_for(config, inp) -> SizingResult` | 4개 사이징 | 60 | FPO |
| `[신규] src/core/portfolio/aggregation.py` | 8.2-C: 여러 실행의 포지션·현금을 심볼/전략/총 노출로 집계(순수) | `ExecutionExposure(execution_id, strategy_id, symbol, notional, vol_pct|None)`, `PortfolioAggregate(total_equity, per_symbol_pct, per_strategy_pct, total_exposure_pct, cash_pct, as_of)`, `aggregate(exposures, cash, as_of) -> PortfolioAggregate` | 없음 | 120 | FPO |
| `[신규] src/core/portfolio/mandate_binding.py` | mandate 제약을 배분에 **결정론적으로** 적용: FORBIDDEN_ASSET → 거부, MAX_SINGLE_INSTRUMENT/MAX_TOTAL_EXPOSURE → 수량 클램프(clamp 후 0이면 거부), MIN_CASH_BUFFER → 클램프 | `bind(qty, price, symbol, agg, mandate) -> BindingResult(quantity, reasons: list[str], denied: bool)`. 사유 코드는 75번 `POLICY_*` 그대로 | `aggregation`, `mandates.contracts.v1` | 150 | FPO |
| `[신규] src/core/portfolio/rebalance.py` | 목표 비중 → 거래 목록. 밴드(`rebalance_band_pct`) 밖만 거래, `min_trade_notional` 미만 제외, 회전율 `Σ|Δw|/2`, 예상 비용 = 회전율 × 비용모델 | `RebalancePlan(trades: list[TradeLeg], turnover_pct, est_cost, skipped: list[str])`, `plan_rebalance(targets: dict[sym, Decimal], current: PortfolioAggregate, cfg, prices) -> RebalancePlan` | `config`, `aggregation` | 150 | FPO |
| `[신규] src/core/portfolio/accounting.py` | 현금/증거금 원장(순수 상태기계): `cash`, `reserved`(미체결 주문 예약), `margin_used`(현물 Phase 1 = 0, 필드는 존재), `available = cash − reserved − margin_used`. 음수 available은 `PORTFOLIO_INSUFFICIENT_CASH` | `CashLedger(...)`, `reserve(ledger, notional) -> CashLedger`, `settle_fill(ledger, fill) -> CashLedger`, `release(ledger, notional)` | `backtest.domain.models.SimulatedFill` | 150 | FPO |
| `[기존] src/core/portfolio/engine.py` | 오케스트레이션: BUY → `sizing.selector` → `mandate_binding` → `accounting.reserve` 검사 → `AllocationDecision`; SELL → 전량(Phase 1) + `decision_hash`. 무포지션 SELL/보유중 BUY는 기존 `PortfolioEngineError` 유지 | `allocate(signal, current_portfolio_state) -> AllocationDecision|None`(위치 인자 불변; dict 또는 `PortfolioStateInput`) | 위 전부 | 200 | FPO |

### 2.4 백테스트 엔진 — `src/foundation/backtest/**` (SCAFFOLD)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `[기존] src/foundation/backtest/domain/models.py` | `CostModel` v2(MINOR): `maker_fee_bps`, `taker_fee_bps`(기존 `fee_bps`는 taker 별칭), `spread_bps: Decimal = 0`, `slippage_model: Literal["LINEAR","SQRT_IMPACT"] = "LINEAR"`, `impact_coeff: Decimal|None`, `funding_bps_per_period: Decimal = 0`, `cost_model_hash()`. `BacktestConfig` v2: `seed: int`, `timeframe: str`, `data_snapshot_hash: str`, `fill_policy: Literal["NEXT_OPEN","NEXT_OPEN_WITH_GAP_CHECK"]`, `survivorship_policy: Literal["UNIVERSE_SNAPSHOT_REQUIRED"]`, `config_hash()`. `BacktestMetrics` v2: `gross_return_pct`, `net_return_pct`, `total_fees`, `total_slippage`, `total_funding`, `calmar_ratio|None`, `exposure_time_pct`, `annualization: int`, `basis: "PAPER_SIM"` | 기존 필드 불변 | — | 250 | SCAFFOLD |
| `[신규] src/foundation/backtest/domain/events.py` | 이벤트 구동 타입: `BarEvent(bar_index, tf, bar)`, `SignalEvent(bar_index, signal)`, `OrderEvent(bar_index, side, qty, decision_hash)`, `FillEvent(bar_index, fill)`. 순서 불변식: 같은 bar_index에서 `Fill(이전 Order) < Bar < Signal < Order` | 없음 | 80 | SCAFFOLD |
| `[신규] src/foundation/backtest/domain/snapshot.py` | 입력 스냅샷 해시(bar 시퀀스·심볼·tf·source·as_of). `validation.domain.rules._bar_fingerprint`를 여기로 이동, rules는 import | `BarSnapshotRef(snapshot_hash, symbol, exchange, timeframe, from_time, to_time, bar_count, source, as_of)`, `compute_bar_snapshot_hash(bars, source, as_of) -> str` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/backtest/domain/universe.py` | survivorship: 심볼별 상장/상폐 구간. as-of 시점 소속 아닌 심볼은 신호 평가 제외, 스냅샷 없으면 hard fail | `UniverseSnapshot(as_of, members: list[UniverseMember(symbol, listed_from, delisted_at|None)], snapshot_hash)`, `is_member(u, symbol, at) -> bool`, `SurvivorshipUnknownError("INTEGRITY_SURVIVORSHIP_UNKNOWN")` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/backtest/domain/splits.py` | walk-forward/OOS 분할 생성(anchored/rolling, purge·embargo bar) | `Split(train: range, test: range, purge: int, embargo: int)`, `make_splits(n_bars, n_splits, mode, purge, embargo, min_train) -> list[Split]`, `assert_no_overlap(splits)`(위반 → `VALIDATION_OOS_LEAKAGE`) | 없음 | 120 | SCAFFOLD |
| `[신규] src/foundation/backtest/domain/overfitting.py` | Deflated Sharpe Ratio·PBO(CSCV) 순수 계산 | `deflated_sharpe(sr_hat, n_trials, T, skew, kurt, sr_var) -> Decimal`, `pbo_cscv(perf_matrix: list[list[Decimal]], n_blocks: int) -> Decimal`. 수식은 §3.5 | 없음(`statistics`, `math`만) | 200 | SCAFFOLD |
| `[신규] src/foundation/backtest/domain/param_stability.py` | 파라미터 격자 인접점 성과 분산·최적점 고립도 | `ParamGrid(axes: dict[str, list[int]])`, `stability_score(grid, metric_by_point) -> ParameterStabilityReport(best, neighbor_mean, neighbor_std, isolated: bool)` | 없음 | 120 | SCAFFOLD |
| `[기존] src/foundation/backtest/domain/rules.py` | 기존 3함수 유지 + `assert_fill_after_signal(order_ev, fill_ev)`(`BACKTEST_LOOKAHEAD_VIOLATION`), `require_cost_model(cost, allow_zero) -> None|HardFail` | `models` | 80 | SCAFFOLD |
| `[신규] src/foundation/backtest/ports/fill_simulator.py` | **PAPER와 공유하는 체결 시뮬레이터 계약**. 미래 `src/exchanges/paper_sim/adapter.py`(별도 L4)는 이 Protocol을 구현해야 함 | `class FillSimulatorPort(Protocol): def simulate(self, *, bar: Candle, bar_index: int, side, quantity, cost_model, seed: int) -> SimulatedFill` | `models` | 40 | SCAFFOLD |
| `[신규] src/foundation/backtest/ports/bar_source.py` | point-in-time bar 접근 포트 | `class PointInTimeBars(Protocol): def upto(self, bar_index) -> Sequence[Candle]; def at(self, bar_index) -> Candle; def __len__` | 없음 | 40 | SCAFFOLD |
| `[신규] src/foundation/backtest/ports/snapshot_repository.py` | 스냅샷 저장/조회 포트 | `save(ref, bars) -> None`(중복 해시는 no-op), `load(snapshot_hash) -> tuple[BarSnapshotRef, list[Candle]]|None` | `snapshot` | 40 | SCAFFOLD |
| `[신규] src/foundation/backtest/adapters/bar_fill_simulator.py` | 기존 `application/simulate_fill.py` 이동+확장: NEXT_OPEN, 갭 체크(시가가 신호 bar 종가 대비 ±spread 밖이면 시가 기준), maker/taker, SQRT_IMPACT(`impact_coeff × sqrt(qty×price / bar.volume×price)`), 펀딩(보유 bar마다 `funding_bps_per_period`) | `BarFillSimulator(FillSimulatorPort)` | `ports.fill_simulator` | 150 | SCAFFOLD |
| `[기존] src/foundation/backtest/application/simulate_fill.py` | 하위호환 얇은 래퍼 → `BarFillSimulator` 호출 | 기존 `simulate_fill(...)` 시그니처 유지 | 어댑터 | 40 | SCAFFOLD |
| `[신규] src/foundation/backtest/adapters/list_bars.py` | `PointInTimeBars` 리스트 구현(범위 밖 인덱스는 `IndexError`가 아니라 `BACKTEST_LOOKAHEAD_VIOLATION`) | `ListBars(bars)` | `ports.bar_source` | 50 | SCAFFOLD |
| `[신규] src/foundation/backtest/adapters/postgres_snapshot_repository.py` | `market_bar_snapshot` 테이블(§3.7 M4) | `PostgresSnapshotRepository(pool)` | asyncpg | 120 | SCAFFOLD |
| `[신규] src/foundation/backtest/application/event_loop.py` | 이벤트 구동 재생 핵심. `IndicatorSeriesCache`로 1회 계산, bar마다 `MarketState(as_of=bar.close_time)` 조립(값은 `value_at(key, bar_index)`), `StrategyEngine`+`PortfolioEngine` 호출, `CashLedger` 회계, `FillSimulatorPort` 체결. `run_backtest.py` 기존 루프를 여기로 이동 | `replay(config, fsm_config, bars: PointInTimeBars, universe, fill_sim, registry) -> ReplayTrace(events, fills, equity_curve, final_ledger, warnings)` | 위 전부 | 300(초과 시 `event_loop_accounting.py`로 회계 분리) | SCAFFOLD |
| `[기존] src/foundation/backtest/application/run_backtest.py` | 파사드: `replay` + `compute_metrics` → `BacktestResult`. 시그니처 유지 | 기존 `run_backtest(config, fsm_config, bars, *, indicator_service=None)` + `universe: UniverseSnapshot|None`, `fill_sim: FillSimulatorPort|None` 키워드 추가 | `event_loop` | 100 | SCAFFOLD |
| `[기존] src/foundation/backtest/application/compute_metrics.py` | gross/net 분리, 비용 합계, calmar, exposure_time. 기존 None 정책 유지 | `compute_metrics(*, equity_curve, fills, initial_equity, periods_per_year, gross_equity_curve) -> BacktestMetrics` | `models` | 200 | SCAFFOLD |
| `[신규] src/foundation/backtest/application/walk_forward.py` | 분할별 IS 격자 탐색(선택 규칙 = IS net Sharpe 최대, 동률 시 파라미터 사전순 — 결정론) → OOS 재생 | `run_walk_forward(base_config, fsm_config, bars, grid, splits, ...) -> WalkForwardReport(windows: list[WindowOutcome(split, selected_params, is_metrics, oos_metrics)], oos_stitched_metrics, selection_rule="IS_NET_SHARPE_MAX")` | `run_backtest`, `splits`, `param_stability` | 200 | SCAFFOLD |
| `[신규] src/foundation/backtest/application/param_sweep.py` | 격자 전수 실행(결정론 순서) + `overfitting`·`param_stability` 입력 행렬 생성 | `sweep(base_config, fsm_config, bars, grid) -> SweepResult(points, perf_matrix)` | `run_backtest` | 120 | SCAFFOLD |
| `[신규] src/foundation/backtest/application/stress.py` | 스트레스: 비용 ×2/×3, 슬리피지 +50bps, 최악 N일 제거, 갭 시나리오. 필수 시나리오 집합 상수 `REQUIRED_SCENARIOS` | `run_stress(base_config, fsm_config, bars, scenarios) -> StressReport(per_scenario: dict[str, BacktestMetrics], missing: list[str])` | `run_backtest` | 150 | SCAFFOLD |

`event_loop.replay` 알고리즘(bar 하나당, 결정론 순서 고정):

```
0. 사전: keys = leaf_keys(모든 non-ORDER_FILLED 전이); cache = IndicatorSeriesCache.build(bars_by_tf, keys)
   ledger = CashLedger(cash=initial_equity); memory = StrategyStateMemory(execution_id=-1, state_version=0)
   pending: OrderEvent | None = None; fsm_state = initial_state
for i, bar in enumerate(bars):                       # bars: PointInTimeBars, i는 기준 tf 인덱스
  1. if pending: fill = fill_sim.simulate(bar=bars.at(i), bar_index=i, ...)   # I2: i > pending.bar_index
        assert_fill_after_signal(pending, fill); ledger = settle_fill(ledger, fill)
        fsm_state = ORDER_FILLED 대상 상태; events += FillEvent(i); pending = None
  2. if universe and not is_member(universe, symbol, bar.close_time): equity 기록만 하고 continue
  3. equity = ledger.cash + position × bar.close (gross_equity는 fee/slippage/funding 미차감 병행 기록)
     funding: 포지션 보유 bar마다 ledger.cash -= notional × funding_bps_per_period / 1e4
  4. if i < warmup_bars: continue
  5. state = MarketState(as_of=bar.close_time, values={k: cache.value_at(k, idx_tf(i)) ...}, bar_close_time=...)
     assert_no_future(state)                                                     # I1
  6. signal, memory = StrategyEngine.evaluate(fsm_config, state, execution_id=-1, fsm_state=fsm_state, memory=memory, as_of=bar.close_time)
  7. if signal: inp = PortfolioStateInput(..., current_price=bar.close, cash_available=ledger.available, exposures=단일실행 집계)
        decision = PortfolioEngine.allocate(signal, inp)  # None/PortfolioEngineError → warnings, continue
        ledger = reserve(ledger, qty × price)(BUY만); pending = OrderEvent(i, side, qty, decision_hash); fsm_state = signal.to_state
  8. events += BarEvent(i), SignalEvent?, OrderEvent?
end; ReplayTrace.trace_hash = sha256(모든 event의 canonical json)  # 같은 입력 → 같은 trace_hash (R1)
```
상위 tf 인덱스 `idx_tf(i)`: 기준 tf bar `i`의 `close_time` 이하로 닫힌 마지막 상위 tf bar(U10). 미체결 `pending`이 마지막 bar까지 남으면 `warnings`에 기록하고 체결시키지 않는다(미래 bar 없음).

### 2.5 검증 파이프라인 — `src/foundation/validation/**` (SCAFFOLD)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `[신규] src/foundation/validation/domain/policy.py` | 버전화된 검증 정책(임계치) | `ValidationPolicy(policy_version="vp-v1", min_oos_windows=3, max_pbo=Decimal("0.5"), min_dsr=Decimal("0.95"), allow_zero_cost=False, required_stress=REQUIRED_SCENARIOS, max_param_isolation=..., required_checks=6개)`, `policy_hash()` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/validation/domain/check_result.py` | 체크 공통 결과 | `CheckResult(check_type, outcome, metrics: dict, warnings, hard_fail_reasons, obligations, evidence_refs: list[str], result_hash)`, `HARD_FAIL_CODES` 상수 집합 | `models` | 80 | SCAFFOLD |
| `[신규] src/foundation/validation/domain/artifact.py` | 전략 아티팩트(내용 주소화): `sha256(canonical_json(fsm_definition) + GRAMMAR_VERSION + registry_hash + compiler_version)` | `StrategyArtifact(artifact_hash, strategy_id, version, compiler_version, grammar_version, registry_version, fsm_definition)`, `build_artifact(...)`, `verify(artifact) -> None|"INTEGRITY_ARTIFACT_HASH_MISMATCH"` | `strategy.condition_parser`, `indicators.registry` | 100 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/point_in_time.py` | 체크 1: bar 시각 단조증가·`close_time <= as_of`·source lineage 존재·gap 리포트. hard fail: 미래 bar / lineage 없음 | `run(ctx: CheckContext) -> CheckResult` | `backtest.domain.snapshot` | 120 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/backtest.py` | 체크 2: 비용모델 필수(`allow_zero_cost=False`면 0비용 hard fail `VALIDATION_COST_MODEL_REQUIRED`), 재생, 벤치마크(buy&hold) 병기 | `run(ctx) -> CheckResult` | `run_backtest` | 120 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/oos_walk_forward.py` | 체크 3: 분할 경계·선택규칙 기록, `assert_no_overlap`, 창 수 < `min_oos_windows` hard fail, OOS net Sharpe ≤ 0이면 FAIL | `run(ctx) -> CheckResult` | `walk_forward` | 120 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/robustness.py` | 체크 4: 파라미터 안정성 + DSR/PBO. `pbo > max_pbo` 또는 `dsr < min_dsr` → FAIL, 격자 크기 < 4 → hard fail(비재현 구성) | `run(ctx) -> CheckResult` | `param_sweep`, `overfitting` | 120 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/stress_capacity.py` | 체크 5: 필수 시나리오 누락 hard fail `VALIDATION_SCENARIO_MISSING`, 시나리오별 MDD·회전율·용량(평균 체결 notional / bar 거래대금) | `run(ctx) -> CheckResult` | `stress` | 100 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/failure_conditions.py` | 체크 6: 운영 무효화 기준 산출(OOS MDD×1.5 초과 시 pause, 30일 rolling Sharpe < 0 시 revalidate). 기준 자체를 만들 수 없으면(OOS 결과 없음) hard fail | `run(ctx) -> CheckResult(obligations=["PAUSE_IF_MDD_GT_x","REVALIDATE_IF_..."])` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/validation/checks/context.py` | 체크 입력 묶음 | `CheckContext(artifact, policy, bars: PointInTimeBars, snapshot_ref, universe, config, seed, trace_id, prior_results: dict[str, CheckResult])` | 위 도메인 | 60 | SCAFFOLD |
| `[기존] src/foundation/validation/domain/rules.py` | `evaluate_validation_policy(warnings)` → `evaluate_bundle(results: list[CheckResult], policy) -> (Outcome, hard_fail_reasons, obligations)`: hard_fail 하나라도 있으면 FAIL, obligation 있으면 PASS_WITH_OBLIGATIONS. `compute_input_snapshot_hash`는 `backtest.domain.snapshot` 재사용 | 기존 함수는 deprecated 유지 | — | 120 | SCAFFOLD |
| `[기존] src/foundation/validation/domain/models.py` | `ValidationRun`에 `artifact_hash`, `policy_version`, `seed`, `data_snapshot_hash`, `trace_id` 추가; `ValidationResult`에 `evidence_refs` 추가; 신규 `ValidationBundle(id, artifact_hash, policy_version, data_snapshot_hash, outcome, check_run_ids, bundle_hash)` | — | — | 120 | SCAFFOLD |
| `[기존] src/foundation/validation/contracts/v1.py` | `StartValidationCommand`에 `check_type: str = "backtest"`, `seed: int = 0`, `policy_version: str|None` 추가(optional, MINOR). 신규 `ValidationBundleView`, `CheckResultView` | `SCHEMA_VERSION="v1"` 유지 | — | 120 | SCAFFOLD |
| `[기존] src/foundation/validation/ports/repository.py` | `create_run`에 새 컬럼 인자, `save_bundle`, `get_bundle_by_artifact`, `list_results_for_artifact`, `run_in_transaction(fn)` 추가 | Protocol | — | 100 | SCAFFOLD |
| `[기존] src/foundation/validation/adapters/postgres_repository.py` | 위 포트 구현. `mark_running`/`mark_failed`/`complete_with_result`는 `conditional_update` 유지 | — | asyncpg | 300(초과 시 `postgres_bundle_repository.py` 분리) | SCAFFOLD |
| `[신규] src/foundation/validation/application/compile_artifact.py` | 76번 `CompileArtifact`: `strategies.fsm_definition` → `build_artifact` → `strategy_artifact` INSERT(내용 주소, 중복 해시 no-op) → evidence | `compile_artifact(repo, strategy_service, evidence_repo, *, owner_user_id, strategy_id, version) -> StrategyArtifact` | `artifact` | 100 | SCAFFOLD |
| `[신규] src/foundation/validation/application/run_check.py` | 체크 하나 실행의 공통 골격: 멱등 조회(UNIQUE) → run 생성 → RUNNING → 체크 실행 → 결과+evidence를 **한 트랜잭션**으로 저장(`run_in_transaction`). 예외 시 FAILED + evidence(outcome=ERROR) | `run_check(repo, evidence_repo, *, check_type, ctx) -> ValidationResultView` | `checks/*` | 200 | SCAFFOLD |
| `[기존] src/foundation/validation/application/start_validation.py` | `run_check(check_type=command.check_type)` 호출 파사드로 축소. 생애주기 전이: 체크 6개 중 `backtest` PASS → BACKTESTING→VALIDATING(기존 유지) | 시그니처 유지 | `run_check` | 120 | SCAFFOLD |
| `[신규] src/foundation/validation/application/build_bundle.py` | 76번 `BuildPackage`의 검증 부분: 같은 artifact·policy·snapshot의 6개 SUCCEEDED run 수집 → `evaluate_bundle` → `strategy_validation_bundle` INSERT → FAIL이면 전략 `FAILED` 전이, PASS면 VALIDATING→STRESS_TESTING→RISK_REVIEW(두 전이 모두 이 커맨드가 내부 호출, 사람은 RISK_REVIEW부터) | `build_bundle(repo, strategy_service, evidence_repo, *, artifact_hash, policy_version, data_snapshot_hash) -> ValidationBundleView` | `rules` | 150 | SCAFFOLD |
| `[신규] src/foundation/validation/projections.py` | 76번 `VerificationView`: as_of, policy_version, run 상태, 체크별 metrics/warnings/hard_fail, evidence 링크. "PAPER_ELIGIBLE = 배포 조건, 투자 추천 아님" 문구 상수 | `build_verification_view(repo, *, artifact_hash) -> VerificationView` | repo | 120 | SCAFFOLD |
| `[기존] src/api/routers/foundation/validation.py` | `POST /v1/foundation/strategy-artifacts:compile`, `POST /validation-runs`(check_type), `POST /validation-bundles`, `GET /strategy-artifacts/{hash}/verification`. 에러 매핑 §3.6 | — | — | 200 | SCAFFOLD |

### 2.6 성과 보고 — `src/foundation/performance/**` (SCAFFOLD, 신규 컨텍스트 FND-09)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `[신규] src/foundation/performance/contracts/v1.py` | 계약. §3.4 | `MoneyValue`, `ReturnValue`, `ComponentBreakdown`, `PerformanceMethodologyView`, `PerformanceStatementView`, `AttributionSliceView`, `ComputeStatementCommand`, `SCHEMA_VERSION="v1"` | pydantic | 200 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/models.py` | frozen dataclass 도메인 모델 | `ValuationSnapshot`, `Methodology`, `PerformanceStatement(state: ESTIMATED/FINAL/CORRECTED, revision_no, prior_statement_id)`, `AttributionSlice`, `Cashflow(at, amount, kind: DEPOSIT/WITHDRAWAL)` | 없음 | 150 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/methodology.py` | 방법론 버전·해시. 기본 `pm-v1`: TWR 기간연결(현금흐름 기초 반영), MWR = IRR(이분법 200회, tol 1e-10), 무위험 0, 연환산 `periods_per_year` 명시, 벤치마크 = 기간 시작 시 mandate 지정 | `DEFAULT_METHODOLOGY`, `methodology_hash(m) -> str` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/twr.py` | 시간가중수익률 | `twr(valuations: list[(at, value)], cashflows) -> Decimal`; 현금흐름 시점 평가액 없음 → `MissingInputError("INTEGRITY_STATEMENT_INPUT_UNRECONCILED")` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/mwr.py` | 금액가중수익률(IRR) 결정론 이분법 | `mwr(cashflows, start_value, end_value, start, end) -> Decimal|None`(수렴 실패 None + 사유) | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/identity.py` | 회계 항등식: `gross_pnl − fees − slippage − funding ± fx − est_tax = net_pnl`; `end = start + net_pnl + Σcashflow`. 입력 None → `PENDING`(0 대체 금지) | `check_identity(b: ComponentBreakdown, start, end, cashflows) -> IdentityResult(ok, residual, pending_fields)` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/risk_metrics.py` | 변동성·MDD·Sharpe(연환산·rf 명시)·Calmar — `backtest.compute_metrics`와 동일 정의를 공유하도록 순수 함수로 추출 | `period_returns`, `annualized_vol`, `max_drawdown`, `sharpe(rf, periods_per_year)` | 없음 | 120 | SCAFFOLD |
| `[신규] src/foundation/performance/domain/rules.py` | PAPER/LIVE 혼합 금지(`INTEGRITY_PAPER_LIVE_MIX`), 통화·정밀도 검사(`INTEGRITY_CURRENCY_PRECISION`), 벤치마크 고정 검사, 정정 규칙 | `assert_single_scope`, `assert_precision`, `assert_benchmark_pinned`, `next_revision(prev) -> int` | `models` | 120 | SCAFFOLD |
| `[신규] src/foundation/performance/ports/repository.py` | 저장소 포트 + 입력 포트 | `PerformanceRepository(Protocol)`: `get_methodology`, `insert_statement`(WORM), `get_statement`, `list_statements`, `insert_attribution`. `StatementInputPort(Protocol)`: `load_reconciled_snapshots(scope, period)`, `load_fills(scope, period)`, `load_cashflows(scope, period)` | 없음 | 100 | SCAFFOLD |
| `[신규] src/foundation/performance/adapters/postgres_repository.py` | §3.7 M5 테이블 | `PostgresPerformanceRepository(pool)` | asyncpg | 250 | SCAFFOLD |
| `[신규] src/foundation/performance/adapters/paper_input_adapter.py` | PAPER 스코프 입력: `orders`(FILLED, fee·average_fill_price)·`positions`·`strategy_executions.allocated_capital`(초기 입금으로 취급)·FND-08 reconciliation 최신 `RESOLVED` run 확인. 미리컨실 → `INTEGRITY_STATEMENT_INPUT_UNRECONCILED` | `PaperStatementInputAdapter(pool)` | asyncpg, `reconciliation.contracts.v1` | 200 | SCAFFOLD |
| `[신규] src/foundation/performance/application/compute_statement.py` | 81번 §2 파이프라인. 결과는 새 리비전 INSERT, evidence(`performance.statement_computed.v1`) 기록 | `compute_statement(repo, inputs, evidence_repo, *, tenant_id, cmd: ComputeStatementCommand, trace_id) -> PerformanceStatementView` | 도메인 전부 | 200 | SCAFFOLD |
| `[신규] src/foundation/performance/application/correct_statement.py` | `CORRECTED` 리비전(prior ref, delta, reason). 원본 불변 | `correct_statement(repo, evidence_repo, *, statement_id, reason, trace_id) -> PerformanceStatementView` | `rules` | 100 | SCAFFOLD |
| `[신규] src/foundation/performance/application/get_statement.py` | 조회 + 테넌트 스코프(`AUTH_PERFORMANCE_SCOPE_DENIED`), 안전 한계 문구 | `get_statement(repo, *, tenant_id, statement_id)`, `list_statements(...)` | repo | 80 | SCAFFOLD |
| `[신규] src/api/routers/foundation/performance.py` | `POST /v1/foundation/performance-statements:compute`(202), `GET /v1/foundation/performance-statements`, `GET /{id}`, `POST /{id}:correct` | — | — | 150 | SCAFFOLD |

### 2.7 서비스 배선 — `src/services/**` (SCAFFOLD)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `[기존] src/services/condition_compiler.py` | v2 문법 출력: 그룹 DTO `ConditionGroup(kind: AND/OR/NOT, items: list[PreviewCondition|ConditionGroup])` 수용, `to_canonical(parse(...))`로 왕복 검증 후 문자열 저장. 파라미터는 `IndicatorRegistry.validate_params` 통과 필수. `risk_params` 선택 인자 | `compile(..., entry: ConditionGroup|list[PreviewCondition], ...)` (list는 v1 호환) | `condition_parser`, `registry` | 200 | SCAFFOLD |
| `[기존] src/services/strategy_builder_service.py` | `save_strategy`가 `fsm_definition.transitions[].condition`을 `parse()`로 검증(문법 오류 400) + 키 파라미터 레지스트리 검증. 그 외 불변 | — | `condition_parser`, `registry` | 250 | SCAFFOLD |
| `[기존] src/services/preview_service.py` | `PreviewCalculator`가 `tree_evaluator` 사용(자체 AND/OR 루프 제거). 결과에 `grammar_version` | — | `tree_evaluator` | 100 | SCAFFOLD |
| `[기존] src/services/execution_loop/market_state.py` | 다중 tf: `required_bars()`로 tf별 limit 산출, tf별 `get_ohlcv`, `MarketState` 반환(`bar_close_time` 포함), `assert_no_future` | `build_market_state(fsm_config, candles_by_tf, *, as_of, indicator_service, registry) -> MarketState`(기존 `list[Candle]` 인자는 `{"1m": candles}`로 승격) | `indicator_key`, `lookback` | 150 | SCAFFOLD |
| `[신규] src/services/execution_loop/strategy_state_store.py` | `strategy_execution_state` 로드/저장(조건부 UPDATE by `state_version`) | `load(pool, execution_id) -> StrategyStateMemory`, `save(pool, memory) -> StrategyStateMemory`(`ConcurrencyConflictError` → 이 틱 폐기) | asyncpg, `conditional_write` | 100 | SCAFFOLD |
| `[신규] src/services/execution_loop/portfolio_state.py` | `PortfolioStateInput` 조립: `position.py` 수량, `account_state.py` 총자산, `var_estimator.py` 실현변동성, `aggregation.aggregate`(사용자 전체 RUNNING 실행), mandate 뷰(`evaluate_policy`가 아니라 `get_active_revision`), `portfolio_config` | `assemble_portfolio_state(pool, *, execution, price, mandate_repo) -> PortfolioStateInput` | 위 전부 | 150 | SCAFFOLD |
| `[기존] src/services/execution_loop/tick.py` | `strategy_state_store.load` → `evaluate(..., memory=)` → `save`; `assemble_portfolio_state`; `AllocationDecision.decision_hash`를 `client_order_id` 재료에 포함 | 시그니처 유지 | 위 | 300 | SCAFFOLD |

---

## 3. 계약 (Contract)

공통: 금액·수량·비율은 `Decimal`(수량 8자리, 금액 통화 정밀도, 비율 4자리), 시각은 tz-aware UTC(`01_data_models_v1.4.md` §1.7). 모든 DTO에 `schema_version`. 호환 규칙은 107번 §3(optional 추가 = MINOR, 필수 추가/타입 변경 = MAJOR → `v2.py` 신설). JSON 직렬화는 `DecimalSafeEncoder`.

### 3.0 해시·버전 레지스트리 (R1·R2의 실체)

모든 해시는 `sha256(canonical_json)` — `json.dumps(sort_keys=True, separators=(",", ":"), default=str)`, Decimal은 `str()`(지수 표기 금지, `Decimal.normalize()` 후), datetime은 ISO-8601 UTC `Z`. 어떤 해시도 시각·UUID·프로세스 정보를 입력에 넣지 않는다.

| 해시/버전 | 입력 | 소유 모듈 | 저장 위치 |
|---|---|---|---|
| `GRAMMAR_VERSION` `cond-v2` | 상수 | `condition_ast` | `strategy_artifact.grammar_version` |
| `REGISTRY_VERSION` `ind-v1`, `registry_hash` | 11개 `IndicatorSpec`(이름·params 범위·lookback 함수의 대표값 표) | `indicators.registry` | `strategy_artifact.registry_version` |
| `node_hash` | `to_canonical(node)` | `condition_ast` | 아티팩트 입력 |
| `artifact_hash` | `fsm_definition`(canonical) + grammar_version + registry_hash + `compiler_version`(`condition_compiler.COMPILER_VERSION="cc-v2"`) | `validation.domain.artifact` | `strategy_artifact.artifact_hash` PK |
| `config_hash` | `PortfolioConfig` 전 필드 | `portfolio.config` | `AllocationDecision.decision_hash` 입력 |
| `cost_model_hash` | `CostModel` 전 필드 | `backtest.domain.models` | `BacktestConfig.config_hash` 입력 |
| `snapshot_hash` | bar 시퀀스 fingerprint(symbol, exchange, tf, open_time, o/h/l/c/v) + source + as_of | `backtest.domain.snapshot` | `market_bar_snapshot` PK, `validation_run.data_snapshot_hash` |
| `BacktestConfig.config_hash` | strategy_id/version, initial_equity, cost_model_hash, warmup, periods_per_year, seed, timeframe, fill_policy, survivorship_policy, snapshot_hash | `backtest.domain.models` | run `metrics.config_hash` |
| `signal_id` | strategy_id, version, execution_id, to_state, bar_time(기준 tf) | `strategy.engine` | 로그·`client_order_id` 재료 |
| `decision_hash` | signal_id, sizing_method, `SizingResult.inputs_hash`, approved_quantity, binding_reasons, config_hash | `portfolio.engine` | 로그·주문 멱등 재료 |
| `trace_hash` | ReplayTrace 이벤트 전체 | `backtest.event_loop` | check `metrics.trace_hash` |
| `result_hash` | `CheckResult.metrics`(단위·기간·버전 포함) | `validation.domain.rules` | `strategy_validation_result.result_hash` |
| `policy_hash`, `policy_version` `vp-v1` | `ValidationPolicy` 전 필드 | `validation.domain.policy` | `validation_run.policy_version` |
| `bundle_hash` | 6개 result_hash(check_type 순 정렬) + policy_hash + artifact_hash | `validation.build_bundle` | `strategy_validation_bundle.bundle_hash` |
| `methodology_hash`, version `pm-v1` | 방법론 정의 JSON | `performance.domain.methodology` | `performance_methodology` |
| `ofit-v1` | DSR/PBO 산식 버전(상수, metrics에 기록) | `backtest.domain.overfitting` | `metrics.overfitting_version` |

재현성 정의: `artifact_hash`, `policy_version`, `snapshot_hash`, `seed`가 같으면 `result_hash`가 같아야 한다(I4). 이 넷 중 하나라도 다르면 **다른 run**이며 캐시 재사용은 없다.

### 3.1 조건 문법 v2 (`GRAMMAR_VERSION="cond-v2"`)

```
expr     := or_expr
or_expr  := and_expr ( "OR" and_expr )*
and_expr := not_expr ( "AND" not_expr )*
not_expr := "NOT" not_expr | "(" expr ")" | atom
atom     := KEY OP NUMBER
KEY      := IND ( "_" param DIGITS )* ( "." output )? ( "@" TF )?
OP       := ">=" | "<=" | "==" | ">" | "<" | "CROSSES_ABOVE" | "CROSSES_BELOW"
TF       := "1m"|"5m"|"15m"|"1h"|"4h"|"1d"
```
- v1 문자열 `"RSI_timeperiod14 < 30 AND SMA_timeperiod20 > 100"`은 그대로 유효(정규화 결과 동일).
- `to_canonical`: 괄호 최소화, 공백 단일화, 리프 순서 **보존**(정렬하지 않음 — confidence 가중 순서 보존).
- `node_hash = sha256(canonical)`. 아티팩트 해시 입력.
- 오류: `STRATEGY_CONDITION_SYNTAX`(재시도 불가, 호출자=컴파일러 버그 또는 사용자 입력 400).

### 3.2 전략 상태 메모리 (`ssm-v1`) — `strategy_execution_state.state` JSONB

```python
class StrategyStateMemory(BaseModel):
    schema_version: Literal["ssm-v1"] = "ssm-v1"
    execution_id: int
    state_version: int                      # 조건부 UPDATE 기대값(105번)
    last_bar_time: dict[str, datetime]      # tf → 마지막으로 반영한 bar close_time
    prev_values: dict[str, Decimal]         # key → 직전 bar 값
```
crossover 유효 조건: `prev_values[key]`가 존재하고 `last_bar_time[tf(key)]`가 현재 bar의 **직전 bar** close_time일 것. 그렇지 않으면(재시작·bar 누락) crossover 리프는 False이며 `EvalResult.missing_keys`가 아니라 `stale_keys`로 보고(신호 없음 + WARNING). 첫 틱 안전성(FD-8.1 완료조건) 유지.

```python
class MarketState(BaseModel):                       # src/core/strategy/market_state.py
    schema_version: Literal["ms-v1"] = "ms-v1"
    as_of: datetime                                  # 평가 기준 시각(tick: now, backtest: bar.close_time)
    values: dict[str, Decimal]                       # key → 최신 닫힌 bar의 지표값
    bar_close_time: dict[str, datetime]              # tf → 그 값이 나온 bar의 close_time (≤ as_of)
    @classmethod
    def from_flat(cls, d: dict[str, float], *, as_of: datetime, tf: str = "1m") -> "MarketState": ...

class EvalResult(BaseModel):                        # src/core/strategy/tree_evaluator.py
    matched: bool
    satisfied_leaves: int; total_leaves: int
    crossover_leaves_satisfied: int                  # confidence 가중 입력
    missing_keys: list[str] = []                     # 지표 데이터 부족 → 판단 보류
    stale_keys: list[str] = []                       # crossover 메모리 불연속 → 해당 리프 False
```
`tree_evaluator.evaluate`는 `missing_keys`가 비어있지 않으면 `IndicatorDataMissingError`를 던진다(부분 평가 금지 — 누락 리프를 False로 간주하면 `NOT` 아래에서 True로 뒤집혀 오신호가 난다). `stale_keys`는 예외가 아니라 결과에 담는다.

### 3.3 포트폴리오 계약

```python
class PortfolioConfig(BaseModel):            # strategy_executions.portfolio_config
    schema_version: Literal["pcfg-v1"] = "pcfg-v1"
    method: SizingMethod = SizingMethod.FIXED_FRACTIONAL
    fraction_pct: Decimal = Decimal("100")   # FIXED_FRACTIONAL: allocated_capital 대비
    target_vol_pct: Decimal | None = None    # VOLATILITY_TARGET 연환산 %
    kelly_cap_pct: Decimal | None = None     # KELLY_CAPPED 상한(예: 25)
    rebalance_band_pct: Decimal = Decimal("5")
    min_trade_notional: Decimal = Decimal("10")
    cost_model: CostModel                    # backtest.domain.models 재사용(단일 출처)

class AllocationDecision(BaseModel):         # 기존 4필드 + MINOR
    symbol: str; strategy_id: str; approved_quantity: Decimal; capital_pct: Decimal
    sizing_method: str | None = None
    target_weight_pct: Decimal | None = None
    pre_binding_quantity: Decimal | None = None
    binding_reasons: list[str] = []          # 75번 POLICY_* 코드
    decision_hash: str | None = None         # sha256(signal_id, method, inputs_hash, qty)
    schema_version: str = "alloc-v1"
```
사이징 산식(모두 Decimal, 최종 수량은 거래소 lot 정밀도로 내림 — 정밀도는 호출자 전달):
- FIXED_FRACTIONAL: `qty = allocated_capital × fraction_pct/100 ÷ price`
- VOLATILITY_TARGET: `w = min(1, target_vol/realized_vol)`, `qty = allocated_capital × w ÷ price`
- KELLY_CAPPED: `f* = p − (1−p)/b`, `f = clamp(f*, 0, cap)`, `qty = allocated_capital × f ÷ price`
- RISK_PARITY: `w_i = (1/σ_i) / Σ_j(1/σ_j)` (전략 i 대상 실행 전체), `qty = total_equity × w_i ÷ price`
- 입력 None(σ, p, b)은 **0 대체 금지** → `PORTFOLIO_SIZING_INPUT_MISSING`(이 틱 스킵, 재시도 가능).

mandate 바인딩 순서(결정론): FORBIDDEN_ASSET → MAX_SINGLE_INSTRUMENT 클램프 → MAX_TOTAL_EXPOSURE 클램프 → MIN_CASH_BUFFER 클램프 → `qty × price < min_trade_notional`이면 거부(`PORTFOLIO_MIN_NOTIONAL`). 클램프된 사유는 전부 `binding_reasons`에 기록.

### 3.3-A 백테스트 계약 v2 (`backtest/domain/models.py`, 기존 필드 불변 + optional 추가)

```python
class CostModel(BaseModel):
    fee_bps: Decimal                                 # [기존] = taker 별칭(하위호환); validator가 taker_fee_bps로 복사
    slippage_bps: Decimal                            # [기존] LINEAR 슬리피지
    maker_fee_bps: Decimal | None = None             # None이면 fee_bps 사용
    taker_fee_bps: Decimal | None = None
    spread_bps: Decimal = Decimal("0")               # 갭 체크·half-spread 비용
    slippage_model: Literal["LINEAR", "SQRT_IMPACT"] = "LINEAR"
    impact_coeff: Decimal | None = None              # SQRT_IMPACT 필수(없으면 ValidationError)
    funding_bps_per_period: Decimal = Decimal("0")   # 보유 bar당 (현물 0, 무기한선물 8h)
    schema_version: Literal["cost-v2"] = "cost-v2"
    def cost_model_hash(self) -> str: ...

class BacktestConfig(BaseModel):
    strategy_id: str; strategy_version: str; initial_equity: Decimal      # [기존]
    cost_model: CostModel; warmup_bars: int; periods_per_year: int         # [기존]
    seed: int = 0
    timeframe: str = "1m"                            # 기준 tf(상위 tf는 조건식 키에서 도출)
    data_snapshot_hash: str | None = None            # None 허용은 단위테스트용; 검증 run은 필수(check가 강제)
    fill_policy: Literal["NEXT_OPEN", "NEXT_OPEN_WITH_GAP_CHECK"] = "NEXT_OPEN_WITH_GAP_CHECK"
    survivorship_policy: Literal["UNIVERSE_SNAPSHOT_REQUIRED", "SINGLE_SYMBOL_ASSUMED_LISTED"] = "UNIVERSE_SNAPSHOT_REQUIRED"
    schema_version: Literal["bt-v2"] = "bt-v2"
    def config_hash(self) -> str: ...
```
`SINGLE_SYMBOL_ASSUMED_LISTED`는 Phase 1 단일 심볼 화이트리스트(06번 §6.2)에서만 허용되며, 검증 체크 1(point-in-time)이 이 값을 보면 obligation `SURVIVORSHIP_NOT_MODELED`를 남긴다(hard fail 아님 — 5개 심볼 전부 기간 내 상장 유지가 사실인 동안만; 상폐 발생 시 정책 v2에서 hard fail로 승격).

### 3.4 성과 계약 (`performance/contracts/v1.py`)

```python
class MoneyValue(BaseModel):
    amount: Decimal | None      # None = PENDING(미리컨실). 0으로 대체 금지
    currency: str; precision: int; as_of: datetime; state: Literal["ESTIMATED","FINAL"]
class ReturnValue(BaseModel):
    value_pct: Decimal | None; basis: Literal["GROSS","NET"]; method: Literal["TWR","MWR"]
    period_start: datetime; period_end: datetime; annualized: bool; periods_per_year: int | None
class ComponentBreakdown(BaseModel):
    gross_pnl: MoneyValue; fees: MoneyValue; slippage: MoneyValue; funding: MoneyValue
    fx: MoneyValue; cashflows_net: MoneyValue; estimated_tax: MoneyValue; net_pnl: MoneyValue
class PerformanceStatementView(BaseModel):
    id: UUID; tenant_id: UUID; scope: Literal["PAPER","LIVE"]; scope_ref: str
    period_start: datetime; period_end: datetime; as_of: datetime
    methodology_version: str; methodology_hash: str
    input_refs: list[str]               # snapshot id, reconciliation run id, fill ids hash
    components: ComponentBreakdown; returns: list[ReturnValue]
    risk: dict[str, Decimal | None]     # vol_pct, mdd_pct, sharpe, calmar (None 허용)
    benchmark: dict[str, Decimal | None] | None; benchmark_ref: str | None
    state: Literal["ESTIMATED","FINAL","CORRECTED"]; revision_no: int; prior_statement_id: UUID | None
    identity_ok: bool; identity_residual: Decimal | None; limitations: list[str]
    evidence_refs: list[str]; schema_version: str = "v1"
```

### 3.5 과최적화 지표 정의 (검증 결과 `metrics`에 기록되는 산식 버전 `ofit-v1`)

- **Deflated Sharpe Ratio**: `DSR = Φ( (SR̂ − SR₀)·√(T−1) / √(1 − γ₃·SR̂ + ((γ₄−1)/4)·SR̂²) )`, `SR₀ = √V[SRₙ]·( (1−γ)·Φ⁻¹(1−1/N) + γ·Φ⁻¹(1−1/(N·e)) )`, γ = 0.5772(오일러-마스케로니), N = 격자 시도 수, T = 표본 수, γ₃/γ₄ = 수익률 왜도/첨도, V[SRₙ] = 격자 Sharpe 분산. Φ⁻¹은 `statistics.NormalDist().inv_cdf`.
- **PBO(CSCV)**: 성과 행렬(T×N)을 S개 블록(짝수)으로 나눠 `C(S, S/2)` 조합마다 IS 최적 열의 OOS 순위 ω(0~1) → `λ = ln(ω/(1−ω))`; `PBO = #(λ<0)/#조합`. S 기본 8(→ 70조합), 결정론 순서(`itertools.combinations`).
- 둘 다 계산 불가(표본 부족·N<2)면 `None` + `warnings`(0 대체 금지). §10 미확인 항목 참조.

### 3.5-A 검증 정책·체크 결과 계약

```python
class ValidationPolicy(BaseModel):                  # validation/domain/policy.py
    policy_version: Literal["vp-v1"] = "vp-v1"
    required_checks: tuple[str, ...] = ("point_in_time", "backtest", "oos_walk_forward",
                                        "robustness", "stress_capacity", "failure_conditions")
    allow_zero_cost: bool = False                    # False → 0비용은 VALIDATION_COST_MODEL_REQUIRED
    min_oos_windows: int = 3; oos_mode: Literal["ANCHORED", "ROLLING"] = "ROLLING"
    purge_bars: int = 24; embargo_bars: int = 24
    min_grid_points: int = 4; max_pbo: Decimal = Decimal("0.5"); min_dsr: Decimal = Decimal("0.95")
    max_param_isolation: Decimal = Decimal("0.5")    # (best − neighbor_mean)/|best| 상한
    required_stress: tuple[str, ...] = ("COST_X2", "COST_X3", "SLIPPAGE_PLUS_50BPS", "WORST_5_DAYS_REMOVED", "GAP_2PCT")
    run_timeout_seconds: int = 1800
    def policy_hash(self) -> str: ...

class CheckResult(BaseModel):                       # validation/domain/check_result.py
    check_type: str; outcome: Outcome
    metrics: dict[str, Any]                          # 단위·기간·annualization·version 키 포함 필수(76번 "bare float 금지")
    warnings: list[str] = []; hard_fail_reasons: list[str] = []; obligations: list[str] = []
    evidence_refs: list[str] = []                    # "snapshot:<hash>", "artifact:<hash>", "trace:<hash>", "audit:<event_id>"
    result_hash: str; policy_version: str; overfitting_version: str | None = None
    schema_version: Literal["chk-v1"] = "chk-v1"
```
`metrics` 필수 키 규약: 모든 수치 키는 `{name}_{unit}` 형식(`net_return_pct`, `total_fees_quote`, `sharpe_annualized`), 그리고 `period_start`, `period_end`, `periods_per_year`, `basis: "PAPER_SIM"`, `config_hash`. 규약 위반은 `run_check`가 저장 전에 거부한다(`VALIDATION_METRIC_UNIT_MISSING`, 400).

hard fail 코드 ↔ 체크 매핑(정책 편집으로 낮출 수 없는 것 — I6):

| 체크 | hard fail 코드 | 조건 |
|---|---|---|
| point_in_time | `INTEGRITY_FUTURE_DATA` / `INTEGRITY_LINEAGE_MISSING` / `INTEGRITY_BAR_ORDER` | `close_time > as_of` 존재 / `source` 빈값 / open_time 비단조 |
| backtest | `VALIDATION_COST_MODEL_REQUIRED` / `BACKTEST_LOOKAHEAD_VIOLATION` | 0비용 & `allow_zero_cost=False` / 재생 중 I2 위반 |
| oos_walk_forward | `VALIDATION_OOS_LEAKAGE` / `VALIDATION_OOS_INSUFFICIENT` | 분할 겹침 또는 purge 미적용 / 창 수 < `min_oos_windows` |
| robustness | `VALIDATION_NONREPRODUCIBLE_CONFIG` | 격자 < `min_grid_points`, seed 미고정, 레지스트리 버전 불일치 |
| stress_capacity | `VALIDATION_SCENARIO_MISSING` | `required_stress` 중 결과 없는 시나리오 |
| failure_conditions | `VALIDATION_NO_INVALIDATION_CRITERIA` | OOS 결과 부재로 pause/revalidate 기준 산출 불가 |

FAIL(hard fail 아님)로 내려가는 조건은 정책 값에 좌우된다: OOS net Sharpe ≤ 0, `pbo > max_pbo`, `dsr < min_dsr`, 고립도 > `max_param_isolation`, 스트레스 시나리오 중 하나라도 MDD > 2×base MDD.

### 3.6 에러 taxonomy

| 코드 | HTTP | 재시도 | 호출자 조치 |
|---|---|---|---|
| `STRATEGY_CONDITION_SYNTAX` | 400 | 불가 | 조건식 수정 |
| `STRATEGY_INDICATOR_UNKNOWN` / `STRATEGY_PARAM_OUT_OF_RANGE` | 400 | 불가 | 레지스트리 범위 내로 수정 |
| `STRATEGY_LOOKBACK_INSUFFICIENT` | (내부) | 다음 틱 | 신호 없음 처리, WARNING |
| `STRATEGY_STATE_VERSION_CONFLICT` | (내부) | 다음 틱 | 이 틱 결과 폐기(105번) |
| `PORTFOLIO_SIZING_INPUT_MISSING` / `PORTFOLIO_INSUFFICIENT_CASH` / `PORTFOLIO_MIN_NOTIONAL` | (내부) | 다음 틱 / 불가 / 불가 | 스킵 + audit |
| `POLICY_FORBIDDEN_ASSET` 등 `POLICY_*` | (내부) | 불가 | 거부 + audit(`allocation_denied`) |
| `BACKTEST_LOOKAHEAD_VIOLATION` / `BACKTEST_SNAPSHOT_MISSING` / `INTEGRITY_SURVIVORSHIP_UNKNOWN` | 422 | 불가 | 엔진 버그 또는 입력 보강 |
| `VALIDATION_COST_MODEL_REQUIRED` / `INTEGRITY_FUTURE_DATA` / `VALIDATION_OOS_LEAKAGE` / `VALIDATION_SCENARIO_MISSING` | 200(FAIL 결과) | 불가 | hard fail 결과로 영속 |
| `INTEGRITY_ARTIFACT_HASH_MISMATCH` | 409 | 불가 | 재컴파일 |
| `STATE_PACKAGE_NOT_ELIGIBLE` / `STATE_STALE_REVISION` | 409 | 재조회 후 | — |
| `INTEGRITY_STATEMENT_INPUT_UNRECONCILED` | 409 | 리컨실 후 | FND-08 실행 |
| `VALIDATION_METHODOLOGY_REQUIRED` / `INTEGRITY_CURRENCY_PRECISION` / `INTEGRITY_PAPER_LIVE_MIX` | 400 | 불가 | — |
| `STATE_STATEMENT_NOT_FINAL` / `AUTH_PERFORMANCE_SCOPE_DENIED` | 409 / 404(존재 비노출) | — | — |
| `DEPENDENCY_COMPUTE_UNAVAILABLE` / `RATE_VALIDATION_QUOTA` | 503 / 429 | 가능(backoff) | — |

### 3.7 마이그레이션 (전부 신규, 체인은 `5ed4921f9873` 이후 — 착수 시 `alembic heads` 재확인)

| ID | 내용 |
|---|---|
| M1 `strategy_execution_state` | `execution_id BIGINT PK REFERENCES strategy_executions(id)`, `state JSONB NOT NULL`, `state_version INT NOT NULL DEFAULT 0`, `schema_version VARCHAR(10) NOT NULL DEFAULT 'ssm-v1'`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` |
| M2 `strategy_executions.portfolio_config` | `JSONB NOT NULL DEFAULT '{"schema_version":"pcfg-v1","method":"FIXED_FRACTIONAL","fraction_pct":"100","rebalance_band_pct":"5","min_trade_notional":"10","cost_model":{"maker_fee_bps":"10","taker_fee_bps":"10","slippage_bps":"5"}}'` + `CHECK (portfolio_config ? 'schema_version')` |
| M3 `strategy_artifact` + validation 확장 | `strategy_artifact(artifact_hash VARCHAR(64) PK, strategy_id, version, compiler_version, grammar_version, registry_version, fsm_definition JSONB, created_at; FK strategies)`, `REVOKE UPDATE, DELETE`. `strategy_validation_run ADD artifact_hash VARCHAR(64) REFERENCES strategy_artifact, policy_version VARCHAR(20) NOT NULL DEFAULT 'vp-v1', seed INT NOT NULL DEFAULT 0, data_snapshot_hash VARCHAR(64), trace_id UUID`; UNIQUE를 `(artifact_hash, check_type, policy_version, data_snapshot_hash, seed)`로 교체(기존 UNIQUE는 유지하되 신규 행은 artifact 기반). `strategy_validation_result ADD evidence_refs JSONB NOT NULL DEFAULT '[]'`. `strategy_validation_bundle(id UUID PK, artifact_hash FK, policy_version, data_snapshot_hash, outcome CHECK, check_run_ids UUID[] NOT NULL, hard_fail_reasons TEXT[], obligations TEXT[], bundle_hash VARCHAR(64), created_at; UNIQUE(artifact_hash, policy_version, data_snapshot_hash))`, `REVOKE UPDATE, DELETE` |
| M4 `market_bar_snapshot` | `snapshot_hash VARCHAR(64) PK, symbol, exchange, timeframe, from_time, to_time, bar_count INT, source VARCHAR(50), as_of TIMESTAMPTZ, bars JSONB NOT NULL, created_at`. `REVOKE UPDATE, DELETE`. 크기: 1m×90일 ≈ 130k bar ≈ 20MB JSONB — 90일 초과는 §10 |
| M5 performance | `performance_methodology(version PK, methodology_hash, definition JSONB, created_at)`, `valuation_snapshot(id UUID PK, tenant_id, scope, scope_ref, as_of, positions JSONB, cash JSONB, price_evidence JSONB, reconciliation_run_id UUID, state CHECK('ESTIMATED','RECONCILED'))`, `performance_statement(id UUID PK, tenant_id, scope CHECK('PAPER','LIVE'), scope_ref, period_start, period_end, as_of, methodology_version FK, input_refs JSONB, components JSONB, returns JSONB, risk JSONB, benchmark JSONB, benchmark_ref, state CHECK('ESTIMATED','FINAL','CORRECTED'), revision_no INT, prior_statement_id UUID, identity_ok BOOL, identity_residual NUMERIC(20,8), limitations TEXT[], evidence_refs JSONB, created_at; UNIQUE(tenant_id, scope, scope_ref, period_start, period_end, methodology_version, revision_no))`, `performance_attribution_slice(id, statement_id FK, dimension, key, contribution NUMERIC, confidence, limitation)`. `performance_statement` `REVOKE UPDATE, DELETE`. 인덱스 `(tenant_id, scope, period_end DESC)` |

---

## 4. 불변조건·상태기계

### 4.1 불변조건 (위반 시 정책)

| # | 불변조건 | 강제 지점 | 위반 시 |
|---|---|---|---|
| I1 | 신호 평가 시 `MarketState.bar_close_time[tf] <= as_of` (look-ahead 0) | 코드: `assert_no_future`(engine 진입), `ListBars.upto` | fail-closed(신호 없음 + `INTEGRITY_FUTURE_DATA` audit) |
| I2 | 백테스트 체결 bar_index > 주문 bar_index | 코드: `assert_fill_after_signal` | fail-closed(`BACKTEST_LOOKAHEAD_VIOLATION`, run FAILED) |
| I3 | crossover는 직전 bar 메모리 없이는 항상 False | 코드: `tree_evaluator` | fail-closed(신호 없음) |
| I4 | 같은 artifact·snapshot·policy·seed → 같은 result_hash | DB: `strategy_validation_run` UNIQUE; 테스트 STR-001 | 재실행 없음(멱등 반환) |
| I5 | 아티팩트 해시 = 내용 해시 | DB: PK + REVOKE; 코드: `verify()` | `INTEGRITY_ARTIFACT_HASH_MISMATCH` |
| I6 | hard_fail_reasons 비어있지 않으면 outcome은 반드시 FAIL | 코드: `evaluate_bundle`; DB: `CHECK (outcome <> 'FAIL' OR cardinality(hard_fail_reasons) > 0)` 역방향은 코드만 | fail-closed |
| I7 | `AllocationDecision.approved_quantity × price ≤ mandate 한도 후 잔여` | 코드: `mandate_binding` | 클램프/거부 + audit |
| I8 | `CashLedger.available ≥ 0` | 코드 | `PORTFOLIO_INSUFFICIENT_CASH` 거부 |
| I9 | 성과 statement PAPER/LIVE 단일 스코프, 정정은 새 행 | DB: CHECK + REVOKE + UNIQUE(revision_no); 코드: `assert_single_scope` | fail-closed |
| I10 | 성과 항등식 잔차 = 0(정밀도 내) 아니면 `FINAL` 불가 | 코드: `check_identity`; 상태기계 | `ESTIMATED` 유지 + limitations |
| I11 | 상태 메모리는 `state_version` 조건부 UPDATE로만 갱신 | 코드: `conditional_update` | `ConcurrencyConflictError` → 틱 폐기 |
| I12 | 사이징 입력 None은 0 대체 금지 | 코드 | 스킵(재시도 가능) |
| I13 | LIVE 실행은 이 문서의 어떤 경로도 주문에 도달 불가 | 코드: `Executor` 하드 가드(불변, 이 문서 범위 밖) | `FrozenZoneLiveModeBlockedError` |

### 4.2 상태 전이표

**검증 run** (`strategy_validation_run.state`)

| from | event | guard | to | side-effect | 감사 이벤트 |
|---|---|---|---|---|---|
| — | StartValidation | UNIQUE 미충돌, 전략 상태 = BACKTESTING(backtest 체크) 또는 ≥ VALIDATING(그 외 체크), artifact 존재 | QUEUED | run INSERT | `validation.run_queued.v1` |
| QUEUED | worker pick | conditional_update | RUNNING | — | — |
| RUNNING | check 완료 | 결과 해시 계산 | SUCCEEDED | result+evidence 같은 트랜잭션 | `validation.check_completed.v1`(payload: outcome, result_hash, hard_fail_reasons) |
| RUNNING | 예외/타임아웃 | — | FAILED | evidence(outcome=ERROR) | `validation.run_failed.v1` |
| QUEUED/RUNNING | CancelValidation | 소유자 | CANCELLED | — | `validation.run_cancelled.v1` |

**검증 번들 → 전략 생애주기**

| from(strategies.lifecycle_status) | event | guard | to | 감사 |
|---|---|---|---|---|
| BACKTESTING | backtest 체크 PASS/PASS_WITH_OBLIGATIONS | — | VALIDATING | `strategy.lifecycle_transitioned.v1` |
| VALIDATING | build_bundle outcome ≠ FAIL | 6/6 SUCCEEDED, 같은 artifact/policy/snapshot | STRESS_TESTING → RISK_REVIEW(내부 연속 전이, 각각 conditional_update) | 동일 |
| VALIDATING | build_bundle outcome = FAIL | — | FAILED | `strategy.validation_failed.v1`(hard_fail_reasons) |
| 어느 단계든 | artifact 변경(재컴파일로 다른 해시) | — | 번들 무효(새 artifact는 새 번들 필요; 생애주기 자체는 PM 정책 §10) | `strategy.artifact_compiled.v1` |

**성과 statement**

| from | event | guard | to | side-effect | 감사 |
|---|---|---|---|---|---|
| — | ComputeStatement | 입력 리컨실됨, 방법론 존재, 단일 스코프 | ESTIMATED 또는 FINAL | INSERT(revision_no = prior+1 또는 1) | `performance.statement_computed.v1` |
| ESTIMATED | 입력 FINAL화 재계산 | identity_ok | FINAL(새 리비전) | INSERT | 동일 |
| FINAL/ESTIMATED | CorrectStatement | 사유 필수 | CORRECTED(새 리비전, prior ref) | INSERT + 사용자 알림 정책 | `performance.statement_corrected.v1` |

**전략 상태 메모리**: 상태 없음 → 첫 틱 `INSERT ... ON CONFLICT DO NOTHING`(state_version 0) → 매 틱 `UPDATE ... WHERE state_version = $expected` → 실행 RETIRED 시 행 유지(감사).

---

## 5. 동시성·멱등성·트랜잭션 경계 (105번)

| 쓰기 | 패턴 | 멱등키 스코프 / digest | outbox |
|---|---|---|---|
| `strategy_execution_state` 갱신 | `conditional_update(expected_state_column="state_version")`; 충돌 시 이 틱의 신호·배분 결과 폐기(주문 없음), 다음 틱 재계산 | execution_id(단일 소유자: 스케줄러 tick, 그러나 재시작 중복 프로세스 대비) | 아니오 |
| `strategy_artifact` INSERT | `INSERT ... ON CONFLICT (artifact_hash) DO NOTHING` + SELECT | 내용 해시 자체가 멱등키 | 아니오 |
| `strategy_validation_run` 생성 | UNIQUE(artifact_hash, check_type, policy_version, data_snapshot_hash, seed) → `ConcurrencyConflictError` 캐치 후 승자 조회(기존 `start_validation` 패턴 유지). 같은 키 + 다른 본문(예: 다른 `initial_equity`)은 스냅샷 해시가 달라져 별도 run — 76번 STR-007 "changed same key conflicts"는 `StartValidationCommand` 전체 digest를 `command_digest` 컬럼에 저장해 UNIQUE 충돌 시 digest 불일치면 409 | tenant(소유자) × artifact × check × policy × snapshot × seed | 아니오 |
| run RUNNING→SUCCEEDED + result + evidence | `run_in_transaction`: `conditional_update`(state=RUNNING) + result INSERT + `append_audit_event`(같은 conn). 감사 §6 "repo 호출마다 별도 커넥션" 결함을 이 컨텍스트에서 제거 | — | 아니오(evidence는 같은 트랜잭션) |
| `strategy_validation_bundle` INSERT | UNIQUE(artifact_hash, policy_version, data_snapshot_hash) + 생애주기 두 전이는 같은 트랜잭션 안 `conditional_update` ×2 | artifact × policy × snapshot | 아니오 |
| `market_bar_snapshot` INSERT | PK 해시 `ON CONFLICT DO NOTHING` | 해시 | 아니오 |
| `performance_statement` INSERT | UNIQUE(... revision_no) — 동시 compute는 하나만 성공, 패자는 승자 반환(멱등) | tenant × scope × scope_ref × period × methodology × revision | 아니오 |
| `strategy_executions.portfolio_config` 변경 | 실행 `status IN ('PENDING_APPROVAL','PAUSED')`일 때만 `conditional_update`(RUNNING 중 변경 금지 — 사이징 방법이 틱 중간에 바뀌면 재현 불가) | execution_id | 아니오 |
| 백테스트·검증 워커 | 순수 계산(DB 없음). 시계 주입(`as_of`), 난수는 `seed`로 `random.Random(seed)`만(SQRT_IMPACT 노이즈 없음 — v1은 결정론 고정) | — | — |

advisory lock 사용 없음(모두 UNIQUE/조건부 UPDATE로 충분 — 105번 §2.2 단일 소유자 스키마 보장).

---

## 6. 실패 모드와 복구

| 실패 | 감지 방법 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|
| 재시작 후 상태 메모리 없음/오래됨 | `last_bar_time[tf]` ≠ 직전 bar | crossover 리프 False(신호 없음), 비교 리프는 정상 | 다음 bar에서 자동 회복(1 bar 지연) | 로그 `strategy_state_stale`(WARNING) |
| 상태 메모리 버전 충돌(중복 스케줄러) | `ConcurrencyConflictError` | 이 틱 폐기, 주문 없음 | 다음 틱 | `strategy_state_concurrency_conflict` + 108번 공용 알림 1 |
| 지표 lookback 부족(신규 상장·거래소 응답 절단) | `required_bars` > 수신 bar 수 | 신호 없음 | 다음 틱; 3틱 연속이면 WARNING | `strategy_lookback_insufficient` |
| 거래소 캔들에 미래 시각(시계 드리프트) | `assert_no_future` | 틱 폐기 | 서버시간 오프셋 보정(어댑터, 감사 §11-7) 후 재시도 | `INTEGRITY_FUTURE_DATA` audit(outcome=DENIED) |
| 다중 tf 중 하나만 갱신 실패(네트워크 분리) | tf별 `get_ohlcv` 예외 | 전체 틱 폐기(부분 시장상태로 판단 금지) | 다음 틱 | `market_state_partial` |
| 사이징 입력 부재(변동성 추정 불가) | None | 스킵 | 실현변동성 표본 확보 시 자동 | `allocation_skipped`(reason) |
| mandate 없음/일시정지 | `get_active_revision` None/PAUSED | 배분 거부 | mandate 활성화 | `allocation_denied`(STATE_NO_ACTIVE_MANDATE) |
| 부분체결로 원장 예약 ≠ 체결 | `settle_fill` 잔여 예약 | 잔여 `release` | 취소 확인(FD-4.3) 후 | `cash_reservation_released` |
| 검증 워커 타임아웃/프로세스 사망 | RUNNING이 `created_at + 30분` 초과 | 스케줄러가 FAILED 전이(conditional_update) | 재요청 시 새 run(멱등키에 seed 포함) | `validation.run_failed.v1`(reason=TIMEOUT) |
| 스냅샷 없이 재생 요청 | `load(snapshot_hash)` None | `BACKTEST_SNAPSHOT_MISSING` | 스냅샷 재수집(같은 해시 검증) | — |
| 결과 해시 불일치(재현성 위반) | 같은 run 키로 재계산한 result_hash ≠ 저장값(주간 재현성 잡) | 알림, 아티팩트 격리 | 레지스트리/문법 버전 변경 여부 조사 | `validation.reproducibility_mismatch.v1` |
| 성과 입력 미리컨실 | FND-08 최신 run 상태 | 409 | reconciliation 실행 | — |
| 성과 항등식 잔차 ≠ 0 | `check_identity` | `ESTIMATED` + limitations | 누락 필드(fee 등) 채운 뒤 재계산 → FINAL 리비전 | `performance.identity_failed.v1` |
| PAPER/LIVE 혼합 조회 시도 | `assert_single_scope` | 400 | — | 108번 공용 알림 3 계열(`INTEGRITY_PAPER_LIVE_MIX` 카운터) |

---

## 7. 성능·SLO·관측성 (108번)

| 측정 지점 | 목표 | 메트릭 이름 |
|---|---|---|
| `StrategyEngine.evaluate`(순수, 리프 ≤ 20) | p95 < 2ms | `aios.core_strategy.evaluate.duration_seconds` |
| `build_market_state`(tf 3개, 캔들 캐시 적중) | p95 < 50ms | `aios.execution_loop.market_state.duration_seconds` |
| `PortfolioEngine.allocate` + `assemble_portfolio_state` | p95 < 30ms | `aios.core_portfolio.allocate.duration_seconds` |
| 상태 메모리 저장 | p95 < 10ms | `aios.execution_loop.state_store.duration_seconds` |
| 백테스트 재생(단일 심볼, 지표 5개, 100k bar) | < 10s(지표 1회 계산 + O(n) 루프) | `aios.foundation_backtest.replay.bars_per_second` |
| walk-forward(격자 16점 × 창 5) | < 5분 | `aios.foundation_backtest.walk_forward.duration_seconds` |
| 검증 큐 대기 | p95 < 2분 | `aios.foundation_validation.queue_age.seconds` |
| 재현성 불일치율 | 0 | `aios.foundation_validation.reproducibility_mismatch.count_total` |
| 성과 계산(월 1스코프) | p95 < 3s | `aios.foundation_performance.compute.duration_seconds` |
| 카운터 | — | `aios.core_strategy.signal.count_total{to_state}`, `aios.core_portfolio.allocation.count_total{outcome=approved\|clamped\|denied\|skipped}`, `aios.foundation_validation.check.count_total{check_type,outcome}`, `aios.foundation_performance.identity.count_total{ok}` |

로그 필드(108번 §2 필수): `trace_id`, `tenant_id`, `actor_subject_id`("system" for tick/worker), `component`(`core.strategy`, `core.portfolio`, `foundation.backtest.application`, `foundation.validation.application`, `foundation.performance.application`), `event`(`signal_generated`, `signal_withheld`, `allocation_approved`, `allocation_denied`, `allocation_clamped`, `strategy_state_concurrency_conflict`, `validation_check_completed`, `validation_check_failed`, `statement_computed`, `statement_corrected`), `duration_ms`, 추가: `execution_id`, `artifact_hash`, `result_hash`, `decision_hash`, `signal_id`. 절대 금지: bar 원본 배열 덤프, 사용자 원문 조건식 이외의 payload 전체.

알림: (1) `reproducibility_mismatch > 0`, (2) 검증 큐 age > 10분, (3) `allocation_denied` 비율 5분 이동평균 급증, (4) 컴파일러/레지스트리/정책 버전 skew(활성 아티팩트의 `registry_version` ≠ 현재), (5) `identity.count_total{ok=false}` > 0, (6) 108번 공용 4종.

---

## 8. 테스트 계획

각 리프는 **최소 negative test 1개**를 포함한다(§9 DoD에 명시).

| 종류 | 경로 | 내용 |
|---|---|---|
| 단위(순수) | `tests/unit/core/strategy/test_condition_parser.py` | v1 문자열 왕복 동일성, 중첩 `(A AND NOT (B OR C))`, 우선순위, 괄호 불일치·미지 연산자 → `STRATEGY_CONDITION_SYNTAX`, canonical 안정성(같은 AST → 같은 해시) |
| 단위 | `tests/unit/core/strategy/test_tree_evaluator.py` | AND/OR/NOT 진리표, 누락 키 → `IndicatorDataMissingError`, crossover 직전 bar 없음 → False, stale(bar 건너뜀) → False, 다중 tf 키 혼합 |
| 단위 | `tests/unit/core/strategy/test_state_memory.py`, `test_confidence.py`, `test_risk_params.py` | advance 불변성, confidence 범위·가중, ATR 배수 손절 산출, 음수 pct 거부 |
| 단위 | `tests/unit/core/strategy/test_engine_v2.py` | FD-8.1 완료조건 6전이 + stop_loss 우선 회귀 유지, memory 반환, `as_of` 미래 bar → 신호 없음(I1) |
| 단위 | `tests/unit/core/indicators/test_registry.py`, `test_lookback.py`, `test_series_cache.py` | 범위 밖 파라미터 거부, MACD lookback = slow+signal−1 실측(TA-Lib NaN 개수와 일치), `value_at`이 전량 계산 결과와 bar별 재계산 결과 동일(인과성 증명), 미래 인덱스 접근 경로 부재 |
| 단위 | `tests/unit/core/portfolio/sizing/test_*.py` (4) | 산식 정확값(Decimal), None 입력 → `PORTFOLIO_SIZING_INPUT_MISSING`, Kelly 음수 → 0, 리스크패리티 합 = 1 |
| 단위 | `tests/unit/core/portfolio/test_mandate_binding.py` | FORBIDDEN 거부, 단일종목 클램프 정확값, 총노출·현금버퍼 순차 클램프, 클램프 후 0 → 거부, 사유 순서 결정론 |
| 단위 | `tests/unit/core/portfolio/test_aggregation.py`, `test_rebalance.py`, `test_accounting.py` | 8.2-C 합산 = 개별 합, 밴드 내 무거래, 회전율 산식, 최소 notional 제외, 예약 > 현금 → 오류, 부분체결 release |
| 단위 | `tests/unit/core/portfolio/test_engine_v2.py` | 기존 FD-8.2 케이스 회귀 + dict 입력 호환 + `decision_hash` 결정론 |
| 단위 | `tests/foundation/unit/backtest/test_event_loop.py` | 이벤트 순서 불변식, 체결 bar > 신호 bar, 상폐 심볼 신호 제외, universe 없음 → hard fail, 같은 seed 두 번 → 동일 trace 해시, 기존 `test_run_backtest.py` 결과와 수치 동일(회귀) |
| 단위 | `tests/foundation/unit/backtest/test_fill_simulator.py` | maker/taker, 갭, SQRT_IMPACT 단조성, 펀딩 누적, 0비용 경고 |
| 단위 | `tests/foundation/unit/backtest/test_splits.py`, `test_walk_forward.py`, `test_overfitting.py`, `test_param_stability.py`, `test_stress.py` | purge/embargo 겹침 0, 선택 규칙 동률 사전순, DSR 알려진 값(§10 수식 대조 픽스처), PBO 무작위 행렬 ≈ 0.5, 고립 최적점 검출, 필수 시나리오 누락 검출 |
| 단위 | `tests/foundation/unit/validation/checks/test_*.py` (6) | 각 체크의 PASS 1건 + **hard fail 1건 이상**(미래 bar, 0비용, OOS 겹침, 격자 < 4, 시나리오 누락, OOS 결과 없음) |
| 단위 | `tests/foundation/unit/validation/test_artifact.py`, `test_bundle_rules.py` | 내용 변경 → 해시 변경, 변조 감지, hard fail → FAIL 강제(I6), obligation 승격 |
| 단위 | `tests/foundation/unit/performance/test_twr.py`, `test_mwr.py`, `test_identity.py`, `test_rules.py` | 현금흐름 있는 TWR 기간연결 정확값, IRR 알려진 값·수렴 실패 None, 잔차 ≠ 0 → PENDING, PAPER/LIVE 혼합 거부, 정밀도 위반 |
| 통합(실DB) | `tests/foundation/integration/validation/test_run_check.py`, `test_build_bundle.py`, `test_compile_artifact.py` | run→result→evidence 단일 트랜잭션(중간 예외 시 run FAILED, result 없음, evidence ERROR), 번들 FAIL → 전략 FAILED, PASS → RISK_REVIEW, 멱등 재요청 동일 뷰, 스냅샷 저장/재로드 후 result_hash 동일(**R1 증명**) |
| 통합 | `tests/integration/execution_loop/test_strategy_state_store.py`, `test_portfolio_state.py`, `test_tick_v2.py` | 재시작 시뮬레이션(메모리 저장 후 새 store로 로드 → crossover 1 bar 지연 후 발화), mandate 클램프가 실제 주문 수량에 반영, `portfolio_config` RUNNING 중 변경 거부 |
| 통합 | `tests/foundation/integration/performance/test_compute_statement.py`, `test_correct_statement.py` | PRF-001·002·004·009, 미리컨실 409, 방법론 변경 → 새 statement |
| 적대적 | `tests/foundation/adversarial/validation/test_tamper.py` | `strategy_artifact.fsm_definition` 직접 UPDATE 시도 → REVOKE로 실패(권한 분리 role에서), result 행 UPDATE 실패, 다른 테넌트 artifact로 run 생성 → 404 |
| 적대적 | `tests/unit/core/portfolio/test_adversarial_binding.py` | 극단값(음수 가격, 0 equity, NaN 문자열)에서 예외가 아닌 거부 코드, mandate 뷰 조작(forbidden 빈 리스트로 재구성) 무효 — 바인딩은 뷰의 `revision_hash` 재계산 대조 |
| 적대적(경합) | `tests/integration/execution_loop/test_state_store_race.py`, `tests/foundation/integration/validation/test_concurrent_start.py` | `asyncio.gather` 2-way: 상태 저장 1승 1 `ConcurrencyConflictError`; 같은 run 키 동시 시작 1 run |
| 계약(107번) | `tests/contract/test_strategy_contracts.py`, `test_portfolio_contracts.py`, `test_performance_contracts.py` | 기존 v1 fixture(`Signal`, `AllocationDecision`, `ValidationResultView`)가 확장 후에도 유효, 필수 필드 집합 불변, `schema_version` 존재, JSON 왕복 Decimal 정밀도 |
| 성능 | `tests/benchmarks/test_backtest_throughput.py` | 100k bar 재생 < 10s **단언**(감사 §9 "단언 없는 벤치마크" 재발 금지), `docs/benchmarks` 자동 덮어쓰기 금지 |
| 재현성 | `tests/foundation/integration/validation/test_reproducibility.py` | 고정 픽스처(`tests/fixtures/backtest/btcusdt_1h_2026q1.json`, `universe.json`)로 result_hash 상수 대조 — 해시가 바뀌면 산식·버전 변경이 의도된 것인지 리뷰 강제 |

고정 픽스처(전부 `tests/fixtures/`, 생성 스크립트 `scripts/make_backtest_fixtures.py`로 결정론 생성 — seed 고정, 실거래소 데이터 아님):

| 파일 | 내용 | 용도 |
|---|---|---|
| `backtest/btcusdt_1h_2026q1.json` | 2,160 bar(90일×24), 기하브라운운동 seed=20260101, 갭 3회 삽입 | 재생·재현성·walk-forward |
| `backtest/btcusdt_4h_2026q1.json` | 위와 정합하는 4h 집계(1h 4개 → 1개, close_time 일치) | 다중 tf·U10 |
| `backtest/universe.json` | 5심볼 상장일, `DOGE/USDT` 상폐일 2026-02-15 삽입 | survivorship |
| `backtest/future_bar.json` | 마지막 bar `close_time > as_of` | point_in_time hard fail |
| `strategies/rsi_sma_v2.json` | 중첩 조건 `(RSI_timeperiod14 < 30 AND NOT (SMA_timeperiod20@4h < SMA_timeperiod50@4h)) OR ...` + `risk_params` | 파서·엔진·컴파일러 |
| `strategies/rsi_v1_legacy.json` | 현 컴파일러 산출 v1 평면 문자열 | 하위호환 회귀 |
| `overfitting/dsr_reference.json`, `pbo_reference.json` | 논문 예제 입력·기대값(U3 확인 후 채움) | §3.5 산식 |
| `performance/paper_month_2026_02.json` | 체결 12건, 입금 1건, 출금 1건, 리컨실 스냅샷 3개 | TWR/MWR/항등식 |

`test_reproducibility.py`의 기대 해시 상수는 L44에서 첫 실행값을 박고, 이후 변경은 커밋 메시지에 `reproducibility-hash-change:` 접두어와 사유(산식/버전/픽스처)를 요구한다.

---

## 9. 리프 목록 (구현 순서)

DoD 공통: `ruff` · `mypy --strict` · `scripts/check_zone_manifest.py` 통과, 해당 테스트 파일 `pytest -q` 통과, negative test ≥ 1, 커밋은 `git commit -F - -- <경로>`(§2-B). FPO 리프(★)는 PM 사전 승인.

| 리프 ID | 파일 | 선행 | DoD(검증 명령·기대 결과) | 크기 |
|---|---|---|---|---|
| L01 | `src/core/indicators/spec.py`, `specs_talib.py` | — | `pytest tests/unit/core/indicators/test_registry.py -k specs` — 11개 스펙 lookback이 TA-Lib NaN 수와 일치 | 230 |
| L02 | `src/core/indicators/registry.py` | L01 | `-k registry` — 범위 밖 파라미터 거부, `registry_hash` 안정 | 120 |
| L03 | `src/core/indicators/talib_adapter.py` | L02 | 기존 `tests/unit/test_indicator_service.py` 전부 통과 + `registry_version` 필드 | 150 |
| L04 ★ | `src/core/strategy/condition_ast.py` | — | AST 직렬화 왕복, discriminator 검증 실패 케이스 | 150 |
| L05 ★ | `src/core/strategy/condition_parser.py` | L04 | `test_condition_parser.py` — v1 전 케이스 왕복 동일, 문법 오류 코드 | 250 |
| L06 ★ | `src/core/strategy/indicator_key.py` | — | tf 유무·다중출력 파싱, 잘못된 tf 거부; `market_state.py:_KEY_RE` 사용처 교체 | 120 |
| L07 | `src/core/indicators/lookback.py` | L02, L06 | tf별 required_bars 정확값, 미지 지표 오류 | 80 |
| L08 ★ | `src/core/strategy/state_memory.py`, `market_state.py` | L06 | advance 불변성, `assert_no_future` 위반 검출 | 200 |
| L09 ★ | `src/core/strategy/tree_evaluator.py` | L04, L08 | 진리표·누락·stale crossover 케이스 | 200 |
| L10 ★ | `src/core/strategy/confidence.py`, `risk_params.py` | L09 | 산식 정확값, 음수/범위 거부 | 200 |
| L11 ★ | `src/core/strategy/models.py`, `condition_evaluator.py`(파사드) | L05, L09 | 기존 `tests/unit/core/strategy/*` 회귀 전부 통과 | 180 |
| L12 ★ | `src/core/strategy/engine.py` | L08–L11 | `test_engine_v2.py` + 기존 FD-8.1 테스트 회귀, 프로세스 캐시 제거 확인(`_prev_tick_cache` grep 0) | 200 |
| L13 | 마이그레이션 M1 + `src/services/execution_loop/strategy_state_store.py` | L08 | `alembic upgrade head` 공유 DB 적용 + PM 공지; 경합 테스트 1승 1충돌 | 100+60 |
| L14 | `src/services/execution_loop/market_state.py` | L07, L08 | 다중 tf 조립, 부분 실패 시 예외 전파 | 150 |
| L15 | `src/services/condition_compiler.py`, `preview_service.py` | L05, L02 | 기존 `test_preview_service.py`·컴파일러 테스트 회귀 + 중첩 그룹 컴파일 왕복 | 300 |
| L16 | `src/services/strategy_builder_service.py` | L05 | 문법 오류 fsm_definition 저장 400 | 250 |
| L17 ★ | `src/core/portfolio/models.py`, `config.py`, `state_input.py` | — | 계약 테스트, `config_hash` 안정 | 360(3파일) |
| L18 ★ | `src/core/portfolio/aggregation.py` | L17 | 합산 항등, as_of 필수 | 120 |
| L19 ★ | `src/core/portfolio/sizing/*.py` (5) | L17, L18 | 4 산식 + selector 디스패치, None 입력 거부 | 380(5파일) |
| L20 ★ | `src/core/portfolio/mandate_binding.py` | L18 | 순차 클램프 정확값, `revision_hash` 대조 | 150 |
| L21 ★ | `src/core/portfolio/accounting.py`, `rebalance.py` | L17 | 원장 불변식, 회전율·비용 | 300 |
| L22 ★ | `src/core/portfolio/engine.py` | L19–L21 | `test_engine_v2.py` + 기존 FD-8.2 회귀 | 200 |
| L23 | 마이그레이션 M2 + `src/services/execution_loop/portfolio_state.py` | L22, `b3f7e0c1a4d5` | 조립 통합테스트, RUNNING 중 config 변경 거부 | 60+150 |
| L24 | `src/services/execution_loop/tick.py` | L13, L14, L23 | `test_tick_v2.py` 재시작 crossover 지연·mandate 클램프 반영 | 300 |
| L25 | `src/foundation/backtest/domain/models.py`, `snapshot.py`, `events.py` | — | 기존 backtest 단위테스트 회귀 + `cost_model_hash`·스냅샷 해시 안정 | 430(3파일) |
| L26 | `src/foundation/backtest/ports/*.py` (3), `adapters/list_bars.py` | L25 | 미래 인덱스 → `BACKTEST_LOOKAHEAD_VIOLATION` | 170 |
| L27 | `src/foundation/backtest/adapters/bar_fill_simulator.py`, `application/simulate_fill.py`(래퍼) | L26 | `test_fill_simulator.py`, 기존 `test_simulate_fill.py` 회귀 | 190 |
| L28 | `src/core/indicators/series_cache.py` | L03 | 인과성 증명 테스트(전량 vs 점진 동일) | 150 |
| L29 | `src/foundation/backtest/domain/universe.py`, `rules.py` | L25 | 상폐 구간 판정, 스냅샷 없음 hard fail | 180 |
| L30 | `src/foundation/backtest/application/event_loop.py` | L12, L22, L27–L29 | `test_event_loop.py`; 기존 `test_run_backtest.py` 수치 동일 | 300 |
| L31 | `src/foundation/backtest/application/run_backtest.py`, `compute_metrics.py` | L30 | gross/net 분리, 기존 metrics 회귀 | 300 |
| L32 | 마이그레이션 M4 + `adapters/postgres_snapshot_repository.py` | L25 | 저장→로드→해시 동일 | 120 |
| L33 | `src/foundation/backtest/domain/splits.py`, `param_stability.py` | — | 겹침 0, 고립 검출 | 240 |
| L34 | `src/foundation/backtest/domain/overfitting.py` | — | DSR/PBO 픽스처 값(§10 대조 후 확정) | 200 |
| L35 | `src/foundation/backtest/application/param_sweep.py`, `walk_forward.py`, `stress.py` | L31, L33 | 결정론 순서, 선택 규칙, 필수 시나리오 | 470(3파일) |
| L36 | `src/foundation/validation/domain/policy.py`, `check_result.py`, `artifact.py` | L05, L02 | 정책 해시, 변조 감지 | 280 |
| L37 | 마이그레이션 M3 + `domain/models.py`, `contracts/v1.py`, `ports/repository.py`, `adapters/postgres_repository.py` | L36 | 기존 `test_start_validation.py` 회귀, 계약 테스트 | 640(5파일, 어댑터 300 초과 시 분리) |
| L38 | `checks/context.py`, `checks/point_in_time.py`, `checks/backtest.py` | L31, L36 | PASS 1 + hard fail 1 each | 300 |
| L39 | `checks/oos_walk_forward.py`, `checks/robustness.py` | L34, L35 | 동일 | 240 |
| L40 | `checks/stress_capacity.py`, `checks/failure_conditions.py` | L35 | 동일 | 200 |
| L41 | `application/compile_artifact.py`, `application/run_check.py` | L37, L38 | 단일 트랜잭션 통합테스트, evidence 행 존재 | 300 |
| L42 | `application/start_validation.py`(축소), `domain/rules.py`(evaluate_bundle) | L41 | 회귀 + I6 | 240 |
| L43 | `application/build_bundle.py`, `projections.py`, 라우터 | L39–L42 | FAIL → 전략 FAILED, PASS → RISK_REVIEW, VerificationView 문구 | 470 |
| L44 | `tests/foundation/integration/validation/test_reproducibility.py` + 픽스처 | L43 | result_hash 상수 대조 통과 | 150 |
| L45 | `src/foundation/performance/contracts/v1.py`, `domain/models.py`, `methodology.py` | — | 계약 테스트, 방법론 해시 | 450 |
| L46 | `domain/twr.py`, `mwr.py`, `identity.py`, `risk_metrics.py`, `rules.py` | L45 | 각 산식 정확값 + negative | 540(5파일) |
| L47 | 마이그레이션 M5 + `ports/repository.py`, `adapters/postgres_repository.py` | L45 | 저장/조회, REVOKE 검증 | 350 |
| L48 | `adapters/paper_input_adapter.py` | L47, FND-08 | 미리컨실 409, 입력 조립 | 200 |
| L49 | `application/compute_statement.py`, `correct_statement.py`, `get_statement.py`, 라우터 | L46–L48 | PRF-001/002/004/009 통합 | 530 |
| L50 | 관측성 배선(각 컨텍스트 로그 필드·메트릭 카운터) + `tests/benchmarks/test_backtest_throughput.py` | L31, L43, L49 | 벤치마크 단언 통과, 로그 필드 스냅샷 테스트 | 200 |

순서 근거: 지표 레지스트리(L01–L03) → 전략(L04–L16) → 포트폴리오(L17–L24) → 백테스트(L25–L35) → 검증(L36–L44) → 성과(L45–L49) → 관측성(L50). 백테스트가 전략·포트폴리오 엔진을 재사용하므로 앞선다. 각 그룹 내 리프는 독립 CI 통과 가능(파사드·래퍼로 하위호환 유지).

---

## 10. 미확정·리스크

| # | 항목 | 상태 | 조치 |
|---|---|---|---|
| U1 | FROZEN_PAPER_ONLY 파일(★ 리프 17개) 수정 승인 | 미승인 | PM(agent-platform-12) 사전 승인. ADR-E "인터페이스 자체 변경 없음" 준수 근거: 위치 인자 불변, 키워드 추가만 |
| U2 | `FSMStrategyConfig`(공유접점문서 §2 동결 계약) | 변경 없음 | `FSMTransition.condition: str` 유지 — v2 문법이 문자열 안에서 표현되므로 계약 무변경. `fsm_definition.risk_params`는 JSONB 추가 키(계약 필드 아님) |
| U3 | DSR·PBO 수식 | **미확인** — Bailey & López de Prado(2014) "The Deflated Sharpe Ratio", Bailey et al.(2015) "The Probability of Backtest Overfitting" 원문 대조 필요. §3.5는 구현 세션의 기억 기반 | L34 착수 전 원문 확인, 픽스처 값 확정. 불일치 시 `ofit-v1` 버전 그대로 두고 v2로 수정(107번) |
| U4 | Bitget 캔들 `close_time`·펀딩 주기·maker/taker 실수수료 | **미확인**(감사 §7 "실캡처 픽스처 1개") | 비용모델 기본값은 보수적 상수(10/10bps, 5bps), 실측 후 `PortfolioConfig.cost_model` 갱신. 실측 전 검증 결과는 obligation `COST_MODEL_UNVERIFIED` |
| U5 | PAPER 시뮬레이터 어댑터(`src/exchanges/paper_sim/`) | 별도 L4 필요(감사 §7 "PAPER 시뮬레이터 부재") | 이 문서는 `FillSimulatorPort` 계약만 고정. Bitget Demo 실왕복(§11-4) 결과에 따라 시뮬레이터 vs Demo 결정 |
| U6 | 스냅샷 JSONB 크기(1m × 90일 초과) | 리스크 | 90일 초과 시 `bytea` 압축 또는 객체 저장소 ref로 M4 v2. 지금은 1h/4h/1d 우선 |
| U7 | 8.2-C 집계의 "사용자 전체 RUNNING 실행" 범위가 tenant 경계와 일치하는지 | 미확정 | `strategy_executions.user_id` = tenant로 가정. FND-01 membership 도입 시 재검토 |
| U8 | 번들 PASS 후 VALIDATING→STRESS_TESTING→RISK_REVIEW 내부 연속 전이가 9.9 "건너뛸 수 없음"과 양립하는가 | 해석 필요 | 두 전이 모두 실제로 수행(스킵 아님)하고 각 전이가 audit 이벤트를 남기므로 원칙 준수로 본다 — PM 확인 |
| U9 | 성과 statement의 LIVE 스코프 | 구현하되 데이터 없음 | LIVE 실행이 존재하지 않으므로 `INTEGRITY_PAPER_LIVE_MIX`·스코프 분리 테스트는 픽스처로만 |
| U10 | 다중 타임프레임 상위 tf bar "닫힘" 판정 | 리스크 | `close_time <= as_of`만 사용, 진행 중 bar 값은 절대 사용하지 않음(보수적). 거래소가 진행 중 bar를 마지막 원소로 반환하면 `market_state.py`가 제외 |
| U11 | 세션별 DB·마이그레이션 직렬화(M1–M5, 5개) | 규칙 존재(§2-B) | 각 마이그레이션 리프 착수 직전 `alembic heads` 확인 후 parent 지정, 적용 후 PM 공지 |
| U12 | 벤치마크 목표 수치(100k bar < 10s 등) | 추정 | 첫 실측 후 조정. 단언은 유지(감사 §9 재발 방지) |
