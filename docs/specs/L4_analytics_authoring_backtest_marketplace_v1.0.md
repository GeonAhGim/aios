# L4 분석·전략작성·백테스트·마켓플레이스·데이터 커버리지 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-04) — ADR-2026-09-04-B의 실행 명세
- owner role: Chief Architect(원칙·★ 승인), PM(리프 배정)
- supersedes: 없음. `L4_strategy_portfolio_backtest_v1.0.md` §2.2(지표 레지스트리)·§2.4(백테스트)·§3.1(cond-v2)을
  **확장**한다(대체 아님). `L4_market_data_positions_ledger_v1.0.md` (A) 시장데이터 위에 얹는다.
- depends on: LA-1~21(시장데이터 계약·품질·저장), ADR-2026-09-04-A(컬럼지향 읽기 경로), L0-1~5, PLT §3(에러 봉투·테넌시)
- implemented by: `src/foundation/market_data/coverage/**`, `src/foundation/market_data/providers/**`,
  `src/core/indicators/**`(확장), `src/core/script/**`(신규), `src/foundation/backtest/**`(확장),
  `src/foundation/marketplace/**`(신규 컨텍스트, 기존 `services/listing_service.py` 등은 파사드로 유지),
  `src/foundation/signals/**`(신규), `frontend/apps/web/src/chart/**`, `frontend/packages/chart-engine/**`
- verification evidence: 각 리프 DoD의 테스트 경로(§9)
- 리프 접두: **DC**(데이터 커버리지) **CH**(차트) **IND**(지표) **DSL**(AIOS Script) **BT**(백테스트) **MP**(마켓) **SIG**(신호 유입)

## 1. 기관급 요구 (왜 기초 수준으로는 부족한가)

### 1.1 도메인별 기관 요구
| 영역 | TradingView가 세운 기준(사용자 기대) | 기관이 추가로 요구하는 것 | 현재 코드 |
|---|---|---|---|
| 데이터 커버리지 | 전 세계 거래소·브로커, 수십 년 히스토리, 실시간 | 계보(출처·버전·조정 이력), 권한(라이선스), fail-closed 커버리지 선언, 심볼 변경·재상장 추적 | 거래소 2곳, 벤처 심볼 문자열 키 |
| 차트 | 캔버스 성능, 드로잉, 멀티페인, 리플레이, 레이아웃 저장, 알림 | 드로잉·레이아웃의 테넌트 스코프·감사, 리플레이=백테스트 동일 데이터 경로 | 기본 캔들 표 |
| 지표 | 수백 개 내장 + 커뮤니티 | 버전 고정·참조 벡터 검증·증분 계산(스트리밍)·해시 | TA-Lib 11종 레지스트리 |
| 전략 언어 | Pine: 시리즈 지향, inputs, plots, strategy.entry | 결정론, 미래참조 금지 정적 검출, 리소스 상한, 아티팩트 해시 재현 | cond-v2(비교식 AND/OR) |
| 백테스트 | 차트 위 즉시, Deep Backtesting, bar magnifier | 슬리피지·수수료·지연·부분체결 모델 계약, 재현 키, 워크포워드·과최적화 지표 | 봉 단위 단순 체결 |
| 마켓플레이스 | 공개/보호/초대, 평판, 인기순 | 보호 소스 서버 실행, 검증된 사용 기반 평판, 표절 탐지, 구독·정산·분쟁 회계 | 목록·구매·분쟁 골격 |
| 신호 유입 | 웹훅 알림 → 외부 봇 | 인증·멱등·재생 방지·리스크 게이트 필수 통과 | 없음 |

### 1.2 이 명세가 바꾸지 않는 것
- `contracts/v1` 계약(P5). 새 계약은 전부 `contracts/v2` 또는 새 컨텍스트에 추가.
- 실행 엔진 `src/core/strategy/**`·`src/core/execution/**`(FROZEN_PAPER_ONLY). 접점은 DSL-11 파사드 1파일(★, ADR-B로 승인).
- 리스크 게이트(Master Authority). 모든 신호·전략·마켓 실행은 기존 게이트를 통과한다.
- 원장·정산·분쟁 규칙(LC-*). 마켓 구독·수익배분은 새 분개 규칙을 **추가**한다(LC posting_rules 확장 리프 MP-8).

## 2. 모듈 분해 (최소단위)

### 2.1 (DC) 데이터 커버리지 — `src/foundation/market_data/`
| 파일 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| `contracts/v2/instruments.py` | 심볼 마스터 DTO(`Instrument`, `VenueListing`, `InstrumentLifecycle`) | pydantic v2, `schema_version="instruments-v2"` | — | 260 | OPEN |
| `domain/instruments/symbol_master.py` | `instrument_id` 발급·벤처 심볼 매핑·충돌 규칙(순수) | `resolve(venue, symbol) -> InstrumentRef`, `register(...)` | — | 240 | OPEN |
| `domain/instruments/lifecycle.py` | 상장·심볼변경·분할·상폐·재상장 전이(순수 상태기계) | `transition(state, event) -> state` | — | 200 | OPEN |
| `ports/provider.py` | `MarketDataProvider` SPI Protocol(capabilities·fetch·subscribe·entitlement) | §3.1 | — | 180 | OPEN |
| `ports/instrument_repository.py`, `ports/coverage_repository.py` | 저장 포트 | Protocol | — | 120 | OPEN |
| `domain/coverage/registry.py` | 커버리지 선언(벤처×자산군×TF×기간×품질등급) 질의·병합(순수) | `coverage_for(instrument, tf) -> CoverageSpan[]` | — | 220 | OPEN |
| `domain/coverage/gaps.py` | 요청 구간 대비 미커버 구간 계산·fail-closed 판정 | `plan_fetch(span, coverage) -> FetchPlan` | LA-5 gap_detector 재사용 | 160 | OPEN |
| `domain/entitlement/policy.py` | 테넌트/사용자 라이선스 → 허용 벤처·TF·지연(delayed/realtime) 판정 | `allowed(subject, feed) -> Entitlement` | — | 180 | OPEN |
| `domain/aggregation/timeframe_rollup.py` | M1 → 파생 TF 결정론 집계 + `rollup_version` | `rollup(columns, tf, calendar) -> CandleColumns` | ADR-A `CandleColumns`, LA-2/3 | 240 | OPEN |
| `adapters/providers/base_adapter.py` | SPI 공통(rate-limit 토큰버킷·재시도·정규화 훅) | | | 240 | OPEN |
| `adapters/providers/bitget_provider.py`, `kis_provider.py` | 기존 거래소 어댑터를 SPI로 재배치(호출 위임, 기존 파일 삭제 금지) | | exchanges/** | 200×2 | OPEN |
| `adapters/storage/hot_postgres.py` | hot 계층(파티션·최근 N일) 읽기/쓰기 | | LA-11 md_candles | 260 | OPEN |
| `adapters/storage/warm_parquet.py` | warm 계층(Parquet, 종목×연도 파일, 컬럼지향 직접 로드) | | pyarrow(미확인 §10) | 280 | OPEN |
| `adapters/storage/tiering.py` | hot→warm 승격·cold 아카이브 잡, 계보 기록 | | | 220 | OPEN |
| `application/backfill_job.py` | 커버리지 갭 기반 백필(멱등·재개 가능·진행률) | | | 260 | OPEN |
| `application/realtime_fanout.py` | 실시간 틱/캔들 pub-sub(테넌트 권한 필터, backpressure) | | PLT-06 event_bus | 260 | OPEN |
| `src/db/migrations/versions/…_instruments_coverage.py` | `instruments`, `venue_listings`, `coverage_spans`, `entitlements` | | | 200 | OPEN |

### 2.2 (CH) 차트 — `frontend/packages/chart-engine/`(자체 렌더링 코어, OSS 포크 기반), `frontend/apps/web/src/chart/`
| 파일 | 단일 책임 | 계약 | 상한 |
|---|---|---|---|
| `packages/chart-engine/vendor/**` | 포크한 OSS 엔진 원본(원 라이선스·NOTICE 유지, 점진적으로 src로 이전해 비운다) | — | 예외(§2.8) |
| `packages/chart-engine/src/core/{renderer,timeScale,priceScale,series}.ts` | AIOS 소유 렌더링 코어(캔버스·시간축·가격축·시리즈) — vendor를 감싸다가 재작성으로 대체 | `ChartEngine` 인터페이스 | 각 ≤300 |
| `packages/chart-engine/src/data/candleStream.ts` | 히스토리 페이지네이션 + 실시간 병합(중복·역순·갭 처리) | `CandleStream` | 240 |
| `packages/chart-engine/src/indicators/overlayRegistry.ts` | 서버 지표 결과를 페인/오버레이로 매핑 | | 200 |
| `packages/chart-engine/src/drawings/{model,tools,serialize}.ts` | 드로잉 도메인(추세선·수평선·피보나치·사각형·텍스트)·직렬화 v1 | `drawings-v1` | 260×3 |
| `packages/chart-engine/src/replay/replayController.ts` | 리플레이(재생·속도·스텝, 백테스트 동일 데이터 경로) | | 220 |
| `packages/chart-engine/src/layout/{layoutModel,persistence}.ts` | 멀티차트 레이아웃·워치리스트 저장/복원 | `layout-v1` | 220×2 |
| `apps/web/src/chart/ChartPage.tsx`, `ChartToolbar.tsx`, `IndicatorPicker.tsx`, `AlertFromChart.tsx`, `StrategyMarkers.tsx` | 화면 조립(각 ≤300줄) | | 300 |
| backend `src/api/routers/chart.py` + `src/foundation/charting/{contracts/v1.py, application/*.py, adapters/postgres_*.py}` | 드로잉·레이아웃·워치리스트 저장(테넌트·사용자 스코프, 감사) | `charting-v1` | 각 ≤260 |

### 2.3 (IND) 지표 라이브러리 — `src/core/indicators/`
| 파일 | 책임 | 상한 |
|---|---|---|
| `catalog/{trend,momentum,volatility,volume,price_levels}.py` | 카테고리별 지표 스펙(입력·파라미터 범위·출력·lookback) — 목표 100종(§10 목록) | 각 ≤280 |
| `engine/incremental.py` | 스트리밍 상태 기반 증분 계산(리플레이·실시간 동일 결과) | 260 |
| `engine/vectorized.py` | 컬럼지향 일괄 계산(백테스트 경로) + 증분과의 동일성 계약 | 260 |
| `reference/vectors/*.json` + `reference/verify.py` | 참조 벡터(공개 기준값) 대조 | 200 |
| `custom/dsl_indicator.py` | AIOS Script로 정의한 사용자 지표를 레지스트리에 등록(버전·해시) | 220 |

### 2.4 (DSL) AIOS Script — `src/core/script/` (신규)
| 파일 | 책임 | 상한 |
|---|---|---|
| `grammar/lexer.py`, `grammar/parser.py`, `grammar/ast.py` | 문법(§3.3)·AST(불변 dataclass, 직렬화 왕복) | 280×3 |
| `typing/types.py`, `typing/checker.py` | 정적 타입(series<float/int/bool>, simple, input, const)·타입 검사 | 260×2 |
| `analysis/lookahead.py` | 미래 참조·repaint 패턴 정적 검출(fail-closed) | 240 |
| `analysis/resources.py` | 루프·시리즈 길이·호출 깊이 상한 산정 | 160 |
| `ir/lower.py`, `ir/ops.py` | AST → IR(스택 기반, 결정론) | 280×2 |
| `runtime/interpreter.py`, `runtime/series.py`, `runtime/builtins_*.py` | IR 실행(순수, I/O 없음), 시리즈 연산, 내장 함수(ta.*, math.*, strategy.*) | 각 ≤280 |
| `compat/cond_v2_bridge.py` | cond-v2 → AIOS Script 변환(해시 보존 규칙 §3.3) | 200 |
| `artifact/hash.py` | 소스·IR·지표 레지스트리 버전·문법 버전 → `script_hash` | 120 |
| `src/core/strategy/script_facade.py` ★ | 실행 엔진이 `condition_evaluator` 대신 스크립트 신호를 받는 접점(1파일) | 200 |

### 2.5 (BT) 백테스트 확장 — `src/foundation/backtest/`
| 파일 | 책임 | 상한 |
|---|---|---|
| `domain/models_v2.py` | `BacktestConfigV2`(§3.4 현실성 모델 계약) | 260 |
| `domain/fill/{slippage,commission,latency,partial_fill,order_types}.py` | 체결 현실성 모델(순수, 각 1책임) | 각 ≤220 |
| `domain/magnifier.py` | bar magnifier(상위 TF 봉을 하위 TF로 확대해 체결 순서 결정) | 240 |
| `domain/costs/{funding,borrow}.py` | 펀딩·차입 비용 | 160×2 |
| `application/quick_backtest.py` | 차트 범위 즉시 백테스트(컬럼 경로, 상한 시간·봉수) | 240 |
| `application/deep_backtest_job.py` | 딥 백테스트 배치(체크포인트·재개·진행률) | 260 |
| `domain/reproducibility.py` | 재현 키 = script_hash + data_lineage_hash + rollup_version + config_hash | 140 |
| `application/tearsheet.py` | 성과 리포트(기존 performance 계약 재사용) | 200 |

### 2.6 (MP) 마켓플레이스 확장 — `src/foundation/marketplace/`
| 파일 | 책임 | 상한 |
|---|---|---|
| `contracts/v1.py` | `ScriptListing`(visibility 4단계·버전·호환 데이터 범위)·`Subscription`·`ReputationScore` | 280 |
| `domain/visibility.py` | public/protected/invite/private 규칙·소스 노출 판정(순수) | 160 |
| `domain/versioning.py` | 버전·체인지로그·호환성(스크립트 해시·문법 버전) | 200 |
| `domain/reputation.py` | 검증된 사용(PAPER 실행·재현 백테스트) 가중 평판 산식 | 220 |
| `domain/plagiarism.py` | AST 정규화 해시·유사도(임계·예외 규칙) | 220 |
| `domain/subscription_rules.py` | 구독 과금 주기·체험·해지·비례 환불(순수) | 240 |
| `application/{publish,subscribe,moderate}.py` | 유스케이스 | 각 ≤240 |
| `src/foundation/ledger/domain/posting_rules_marketplace.py` | 구독·수익배분 분개 규칙 추가(LC posting_rules 확장, 기존 규칙 불변) | 200 |
| `adapters/postgres_*.py` + 마이그레이션 | 저장 | 각 ≤260 |
| 프론트 `MarketplaceBrowse`·`ListingDetail`·`SellStrategy` 확장, `ScriptEditorPage.tsx`(Monaco 기반 편집기+컴파일 오류 표시) | 화면 | 각 ≤300 |

### 2.7 (SIG) 신호 유입 — `src/foundation/signals/`
| 파일 | 책임 | 상한 |
|---|---|---|
| `contracts/v1.py` | `SignalIntent`(source, instrument_ref, side, qty_mode, ttl, idempotency_key, meta) | 200 |
| `domain/auth.py` | 사용자별 HMAC 시크릿 검증·타임스탬프 창·재생 방지(nonce 캐시) | 200 |
| `domain/normalize_tradingview.py` | TradingView 웹훅 페이로드(`{{strategy.order.action}}` 등 플레이스홀더) → `SignalIntent` | 220 |
| `application/ingest.py` | rate limit·멱등·검증 → outbox → 기존 실행 경로(리스크 게이트) | 240 |
| `src/api/routers/signals.py` | `POST /signals/tradingview`, `POST /signals/generic`, 시크릿 회전 | 200 |

### 2.8 분할 규칙
`frontend/packages/chart-engine/vendor/**`는 포크 원본이라 300줄 규칙 예외다(Guard P6는 `src/**/*.py`만 검사). `chart-engine/src/**`는 규칙을 그대로 따르며, vendor 모듈을 재작성해 src로 옮길 때마다 vendor에서 삭제한다.
파서·인터프리터·어댑터가 300줄을 넘길 지점은 표에 미리 분할했다(lexer/parser/ast, builtins_ta/builtins_math/builtins_strategy).
넘기면 리프를 나누고 Guard P6이 막는다.

## 3. 계약 (Contract)

### 3.1 `MarketDataProvider` SPI (`ports/provider.py`)
```python
class ProviderCapabilities(BaseModel):
    provider_id: str; asset_classes: frozenset[AssetClass]; timeframes: frozenset[Timeframe]
    history_from: datetime | None; realtime: bool; delayed_seconds: int; max_symbols_per_request: int
    rate_limit: RateLimitSpec  # requests_per_second, burst
class MarketDataProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]: ...
    async def fetch_candles(self, listing: VenueListing, tf: Timeframe, span: TimeSpan) -> CandleColumns: ...
    async def subscribe(self, listings: Sequence[VenueListing]) -> AsyncIterator[TickOrCandle]: ...
```
- 모든 반환은 UTC tz-aware, Decimal 가격(LA-1 정밀도 규칙), `lineage`(provider_id, fetched_at, raw_digest) 필수.
- 에러: `DATA_PROVIDER_RATE_LIMITED`(재시도 가능, retry_after), `DATA_PROVIDER_UNAVAILABLE`(재시도), `DATA_ENTITLEMENT_DENIED`(불가, 403), `DATA_COVERAGE_MISSING`(불가, 409 — 조용한 0 채움 금지).

### 3.2 심볼 마스터 (`contracts/v2/instruments.py`)
- `Instrument{instrument_id: ULID, asset_class, base, quote, isin|None, figi|None, tick_size: Decimal, lot_size: Decimal, calendar_id, lifecycle_state, created_at}`
- `VenueListing{instrument_id, venue, venue_symbol, listed_at, delisted_at|None, is_primary}` — (venue, venue_symbol, listed_at) 유일.
- 심볼 변경은 새 `VenueListing`(구 listing delisted_at 설정), `instrument_id` 불변. 재상장은 새 instrument.

### 3.3 AIOS Script 문법 v1 (`GRAMMAR_VERSION="aios-script-1"`)
```
program   := decl*
decl      := "input" ident ":" type "=" literal | "let" ident "=" expr | "plot" "(" expr ("," style)? ")"
           | "signal" ident "=" expr | "order" "(" side "," qty_expr ("," opts)? ")" "when" expr
expr      := or_expr ; or_expr := and_expr ("or" and_expr)* ; and_expr := not_expr ("and" not_expr)*
not_expr  := "not" not_expr | cmp ; cmp := arith (("<"|"<="|"=="|">="|">"|"crosses_above"|"crosses_below") arith)?
arith     := term (("+"|"-") term)* ; term := unary (("*"|"/") unary)* ; unary := "-" unary | postfix
postfix   := primary ("[" INT "]")?          # 과거 참조만 허용(음수·변수 인덱스 금지)
primary   := NUMBER | ident | call | "(" expr ")" ; call := ns "." ident "(" args ")"   # ta.*, math.*, series.*
type      := "int" | "float" | "bool" | "series<float>" | "series<bool>"
```
- 결정론: 난수·시계·I/O·재귀 없음. `[n]`은 상수 n≥0. `ta.*`는 IND 레지스트리 버전에 고정.
- 미래참조: 컴파일 시 `lookahead.py`가 시리즈 오프셋 부호와 `security()`류 부재를 정적 검증(위반 = `SCRIPT_LOOKAHEAD`).
- 리소스: 최대 시리즈 길이·연산 수·호출 깊이는 컴파일 산정치로 거부(`SCRIPT_RESOURCE_LIMIT`).
- cond-v2 호환: `RSI_timeperiod14 < 30 AND SMA_timeperiod20 > 100` → `ta.rsi(close, 14) < 30 and ta.sma(close, 20) > 100`.
  변환 결과의 `script_hash`와 원 `node_hash`를 `compat_map`에 함께 저장해 기존 아티팩트 해시를 깨지 않는다.
- 에러 taxonomy: `SCRIPT_SYNTAX`·`SCRIPT_TYPE`·`SCRIPT_LOOKAHEAD`·`SCRIPT_RESOURCE_LIMIT`(모두 400, 재시도 불가, 위치 정보 포함).

### 3.4 백테스트 현실성 계약 (`BacktestConfigV2`, `schema_version="backtest-v2"`)
- `slippage: Fixed{bps} | Percent{pct} | VolumeImpact{k, participation_cap}`; `commission: VenueTier{venue, maker_bps, taker_bps, min_fee}`
- `latency_ms`, `partial_fill: {max_participation_pct}`, `order_types: {limit, stop, oco, trailing}`, `magnifier_tf: Timeframe|None`
- `costs: {funding: bool, borrow_apr: Decimal|None}`, `adjustments: {splits, dividends}`, `calendar: session|24x7`
- 재현 키 `reproducibility_key = sha256(script_hash ‖ data_lineage_hash ‖ rollup_version ‖ config_hash)`; 같은 키 = 같은 결과(바이트 동일한 체결 로그)여야 한다.

### 3.5 마켓플레이스 계약 (`marketplace/contracts/v1.py`)
- `Visibility = public|protected|invite|private`; protected는 소스 비공개·서버 실행만·재현 키 공개.
- `ScriptListing{listing_id, script_hash, version, changelog, visibility, price: OneTime|Subscription{period, trial_days}, compat: {asset_classes, timeframes, min_history}, reputation}`
- `ReputationScore{verified_runs, reproduced_backtests, dispute_rate, weighted_score}` — 산식 §4.3.
- 에러: `MP_PLAGIARISM_SUSPECT`(409, 심사 큐), `MP_SUBSCRIPTION_STATE`(409), `MP_VISIBILITY_DENIED`(403).

### 3.6 신호 계약 (`signals/contracts/v1.py`)
- `SignalIntent{source: "tradingview"|"generic", instrument_ref, side: buy|sell|flat, qty_mode: fixed|pct_equity|risk_pct, qty: Decimal, ttl_seconds, idempotency_key, received_at, signature_ok: bool}`
- 인증: `X-AIOS-Signature = HMAC-SHA256(secret, timestamp ‖ body)`, 타임스탬프 창 ±300s, nonce 24h 캐시. 시크릿은 PLT-33 KeyRing으로 암호화 저장·회전.
- 에러: `SIGNAL_AUTH_FAILED`(401), `SIGNAL_REPLAYED`(409), `SIGNAL_SCHEMA`(400), `SIGNAL_RATE_LIMITED`(429 retry_after).

## 4. 불변조건·상태기계
### 4.1 데이터 (fail-closed)
- 커버리지 밖 구간 요청 → `DATA_COVERAGE_MISSING`; 절대 0/NaN으로 채우지 않는다(DB 제약: `coverage_spans` 겹침 금지 EXCLUDE 제약).
- 파생 TF는 M1에서만 생성. `rollup_version` 없는 파생 캔들 저장 금지(CHECK).
- `instrument_id` 불변, `venue_listings` 기간 겹침 금지(EXCLUDE USING gist).
### 4.2 심볼 생애주기 전이표
| from | event | guard | to | 감사 |
|---|---|---|---|---|
| pending | listed | 벤처 확인 | active | instrument.listed |
| active | symbol_changed | 새 listing 등록 | active | listing.replaced |
| active | halted | 벤처 공지 | halted | instrument.halted |
| halted | resumed | | active | instrument.resumed |
| active/halted | delisted | | delisted | instrument.delisted |
| delisted | relisted | 새 instrument 생성(구 id 유지 금지) | — | instrument.relisted |
### 4.3 평판 산식(순수)
`weighted = 0.5·f(verified_runs) + 0.3·g(reproduced_backtests) + 0.2·(1 − dispute_rate)`; f,g는 포화 함수(log1p 정규화). 별점은 표시만, 순위에 사용하지 않는다.
### 4.4 스크립트·리스팅 상태
draft → compiled(해시 고정) → published(visibility) → deprecated; published 버전의 소스·IR은 불변(수정 = 새 버전).
### 4.5 신호
received → authenticated → normalized → gated(리스크) → accepted|rejected; 어느 단계도 건너뛸 수 없다(outbox 이벤트로 증명).

## 5. 동시성·멱등성·트랜잭션 경계 (105번)
- 백필: `(instrument_id, tf, span)` advisory lock, 진행 커서 조건부 UPDATE, 재실행 멱등.
- 실시간 fanout: 구독자별 bounded queue, 넘치면 최신 우선 drop + 메트릭(무한 버퍼 금지).
- 드로잉·레이아웃 저장: `version` 낙관적 잠금(`UPDATE … WHERE version = :v`), 충돌 = `STATE_CONCURRENCY_CONFLICT`.
- 딥 백테스트 잡: 체크포인트(봉 인덱스) 조건부 UPDATE, 워커 재시작 시 재개.
- 구독 과금: 주기 키 `(subscription_id, period_start)` 유일 + 원장 멱등키(LC 규칙).
- 신호 ingest: `idempotency_key` 스코프 = 사용자, 24h; outbox에 기록 후 실행 경로로.

## 6. 실패 모드와 복구
| 실패 | 감지 | 즉시 조치 | 복구 | 감사 |
|---|---|---|---|---|
| 공급자 rate limit/장애 | SPI 에러·메트릭 | 토큰버킷 backoff, 커버리지에 "지연" 표시 | 자동 재시도, 갭 백필 잡 | data.provider_degraded |
| 파생 TF 불일치(rollup 버전 변경) | 검증 잡 샘플 대조 | 구버전 격리(읽기 차단) | 재집계 잡 | data.rollup_reversioned |
| 심볼 변경 미반영 | listing 대조 잡 | 해당 종목 실시간 차단 | 매핑 갱신 | instrument.mapping_fixed |
| 스크립트 무한/과다 계산 | 컴파일 산정·런타임 카운터 | 거부/중단 | 사용자 수정 | script.resource_limit |
| 백테스트 재현 실패(같은 키, 다른 결과) | 재현 검증 잡 | 결과 무효화·리스팅 평판 동결 | 원인(데이터 계보/지표 버전) 추적 | backtest.nonreproducible |
| 웹훅 재생/위조 | HMAC·nonce | 401/409, 카운터 | 시크릿 회전 | signal.rejected |
| 표절 의심 | AST 유사도 | 심사 큐, 판매 보류 | 관리자 판정 | mp.plagiarism_review |

## 7. 성능·SLO·관측성 (108번)
| 지점 | 목표 | 메트릭 |
|---|---|---|
| 5k봉 조회(hot) | p95 200ms | `md_query_duration_ms{tier}` |
| 50k봉 조회(warm, 컬럼) | p95 800ms | 동일 |
| 실시간 fanout 지연 | p95 150ms(수신→클라이언트) | `md_fanout_lag_ms` |
| 지표 증분 계산 | 100 지표 × 1 봉 ≤ 5ms | `ind_incremental_ms` |
| 스크립트 컴파일 | ≤ 300ms | `script_compile_ms` |
| 즉시 백테스트(1개월 M1) | ≤ 5s(ADR-A) | `bt_quick_ms` |
| 딥 백테스트(10년 D1 + magnifier M1) | ≤ 10분 배치 | `bt_deep_ms` |
| 웹훅 ingest | p95 100ms(수신→outbox) | `signal_ingest_ms` |
로그 공통 필드: trace_id, tenant_id, component, event, duration_ms, instrument_id, script_hash.

## 8. 테스트 계획
- 단위: 문법·타입·lookahead·IR·인터프리터 property 테스트(hypothesis), 지표 참조 벡터, 심볼 마스터 규칙, 평판·표절·구독 산식, 슬리피지/수수료 모델.
- 통합(실DB): 커버리지·백필·hot/warm 왕복, 드로잉 낙관적 잠금, 구독 분개, 웹훅 → outbox → 리스크 게이트.
- 적대적: 교차 테넌트 드로잉/레이아웃/리스팅 조회 404 동형, 웹훅 재생·서명 위조, 미래참조 스크립트, 커버리지 밖 요청 0 채움 시도, 표절 우회(변수명 변경).
- 계약: v2 스키마 스냅샷, cond-v2 → script 변환 왕복 전 케이스.
- 성능: §7 표 항목별, 1개월까지 CI 강제, 그 이상 nightly.
- 프론트: chart-core 단위(vitest), 리플레이=백테스트 데이터 동일성, 드로잉 직렬화 왕복, 접근성.

## 9. 리프 목록 (구현 순서)
DoD 공통: `ruff` · `mypy --strict` · `scripts/check_zone_manifest.py` 통과, 명시 테스트 통과, negative test ≥1, 파일 ≤300줄. 프론트는 `npm run build --workspace=apps/web` + vitest.

### 9.1 (SIG) 신호 유입 — 선택 백로그(맨 마지막). 외부 신호 수신은 부가 기능이며 제품 독립성과 무관
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| SIG-1 | `signals/contracts/v1.py` + 스키마 테스트 | — | 스냅샷, naive datetime 거부 | 200 |
| SIG-2 | `signals/domain/auth.py` + test | SIG-1 | HMAC 정/오, 타임스탬프 창, nonce 재생 거부 | 200 |
| SIG-3 | `signals/domain/normalize_tradingview.py` + test | SIG-1 | TV 플레이스홀더 페이로드 5종 → SignalIntent, 미지 필드 거부 | 220 |
| SIG-4 | `signals/application/ingest.py` + outbox 통합 테스트 | SIG-2,3 | 멱등 재전송 1건, rate limit 429, 리스크 게이트 거부가 outbox에 남음 | 240 |
| SIG-5 | `api/routers/signals.py` + 시크릿 회전(PLT-33) + 통합 테스트 | SIG-4 | 401/409/400/429/202, 시크릿 회전 후 구서명 401 | 200 |
| SIG-6 | 프론트 `SignalSourcesPage.tsx`(시크릿 발급·회전·최근 수신 로그) | SIG-5 | 화면·negative | 280 |

### 9.2 (DC) 데이터 커버리지 — 구조 선행(R/L4 잔여보다 먼저)
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| DC-1 | `contracts/v2/instruments.py` + 스냅샷 | — | 스키마 스냅샷 | 260 |
| DC-2 | `domain/instruments/symbol_master.py` + test | DC-1 | 충돌·대소문자·재상장 규칙 | 240 |
| DC-3 | `domain/instruments/lifecycle.py` + test | DC-1 | §4.2 전이표 전 케이스, 불법 전이 거부 | 200 |
| DC-4 | 마이그레이션 instruments/venue_listings(EXCLUDE) + 통합 테스트 | DC-1 | 겹침 삽입 실패 | 200 |
| DC-5 | `ports/provider.py`, `ports/instrument_repository.py`, `ports/coverage_repository.py` | DC-1 | Protocol runtime_checkable 테스트 | 300 |
| DC-6 | `domain/coverage/registry.py` + test | DC-1 | 병합·질의, 겹침 병합 정확 | 220 |
| DC-7 | `domain/coverage/gaps.py` + test | DC-6 | fail-closed 판정, LA-5 재사용 | 160 |
| DC-8 | 마이그레이션 coverage_spans(EXCLUDE)/entitlements + 어댑터 + 통합 | DC-4,6 | | 260 |
| DC-9 | `domain/entitlement/policy.py` + test | DC-1 | 테넌트/사용자/지연 판정, 거부 403 | 180 |
| DC-10 | `domain/aggregation/timeframe_rollup.py` + test | ADR-A, LA-2/3 | M1→5m/1h/1d 결정론, 세션 경계·휴장, rollup_version | 240 |
| DC-11 | `adapters/providers/base_adapter.py` + test | DC-5 | 토큰버킷·재시도·정규화 훅 | 240 |
| DC-12 | `adapters/providers/bitget_provider.py`, `kis_provider.py` + 계약 테스트 | DC-11 | capabilities 정확, 기존 exchanges 경로 무변경 | 400 |
| DC-13 | `adapters/storage/hot_postgres.py` + 통합 | DC-8, LA-11 | instrument_id 키 조회 p95 200ms(5k봉) | 260 |
| DC-14 | `adapters/storage/warm_parquet.py` + 통합 | DC-13 | 종목×연도 파일 왕복, CandleColumns 직접 로드 | 280 |
| DC-15 | `adapters/storage/tiering.py` + 잡 테스트 | DC-14 | 승격·아카이브 멱등, 계보 기록 | 220 |
| DC-16 | `application/backfill_job.py` + 통합 | DC-7,11,13 | 갭 계획→백필→커버리지 갱신, 중단 후 재개 | 260 |
| DC-17 | `application/realtime_fanout.py` + 테스트 | DC-9, PLT-06 | 권한 필터, backpressure drop 메트릭 | 260 |
| DC-18 | 커버리지 API `GET /market-data/coverage` + 프론트 `CoverageBadge` | DC-8 | 화면에 "지연/미커버" 표기 | 200 |

### 9.3 (IND) 지표 라이브러리
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| IND-1 | `engine/incremental.py` + `engine/vectorized.py` 동일성 property 테스트 | L01~L03 | 증분=일괄 결과 동일(1e-9) | 520 |
| IND-2~6 | `catalog/{trend,momentum,volatility,volume,price_levels}.py` + 참조 벡터 | IND-1 | 카테고리당 ≥20종, 벡터 대조 통과 | 각 280 |
| IND-7 | `reference/verify.py` + 벡터 파일 | IND-2 | CI에서 벡터 검증 | 200 |
| IND-8 | `custom/dsl_indicator.py` + test | DSL-9 | 사용자 지표 등록·버전·해시 | 220 |

### 9.4 (DSL) AIOS Script
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| DSL-1 | `grammar/ast.py` + 직렬화 왕복 테스트 | — | 왕복 동일, 불변 | 280 |
| DSL-2 | `grammar/lexer.py` + test | — | 토큰 전 종류, 오류 위치 | 260 |
| DSL-3 | `grammar/parser.py` + test | DSL-1,2 | §3.3 문법 전 규칙, 오류 코드 | 280 |
| DSL-4 | `typing/types.py`, `typing/checker.py` + test | DSL-3 | 시리즈/스칼라 승격·거부 | 520 |
| DSL-5 | `analysis/lookahead.py` + test | DSL-4 | 음수 인덱스·변수 인덱스·미래 함수 거부 | 240 |
| DSL-6 | `analysis/resources.py` + test | DSL-4 | 산정치 상한 거부 | 160 |
| DSL-7 | `ir/ops.py`, `ir/lower.py` + test | DSL-4 | AST→IR 결정론(같은 AST=같은 IR 바이트) | 560 |
| DSL-8 | `runtime/series.py`, `runtime/interpreter.py` + property 테스트 | DSL-7 | 참조 구현 대비 동일, 재귀·I/O 없음 | 560 |
| DSL-9 | `runtime/builtins_ta.py`, `builtins_math.py`, `builtins_strategy.py` + test | DSL-8, IND-1 | ta.* 는 레지스트리 버전 고정 | 840 |
| DSL-10 | `compat/cond_v2_bridge.py` + 전 케이스 왕복 테스트 + `compat_map` 저장 | DSL-3 | 기존 cond-v2 테스트 전부 동일 신호 | 200 |
| DSL-11 ★ | `src/core/strategy/script_facade.py` + 회귀 테스트 | DSL-9,10 | 기존 `tests/unit/core/strategy/*` 전부 통과, 스크립트 경로 신호 동일 | 200 |
| DSL-12 | `artifact/hash.py` + `POST /scripts/compile` API + 오류 위치 응답 | DSL-7 | 컴파일 ≤300ms, 4종 오류 코드 | 260 |
| DSL-13 | 프론트 `ScriptEditorPage.tsx`(Monaco, 오류 마커, 컴파일 미리보기) | DSL-12 | 화면·negative | 300 |

### 9.5 (BT) 백테스트 현실성
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| BT-1 | `domain/models_v2.py` + 스냅샷 | — | backtest-v2 스키마 | 260 |
| BT-2~6 | `domain/fill/{slippage,commission,latency,partial_fill,order_types}.py` + test | BT-1 | 모델별 정확값·경계, 음수 거부 | 각 220 |
| BT-7 | `domain/magnifier.py` + test | BT-1, DC-10 | 하위 TF 체결 순서 결정론 | 240 |
| BT-8 | `domain/costs/{funding,borrow}.py` + test | BT-1 | 일할 계산 정확 | 320 |
| BT-9 | `domain/reproducibility.py` + test | DSL-12, DC-10 | 같은 키=바이트 동일 체결 로그 | 140 |
| BT-10 | `application/quick_backtest.py` + 통합(컬럼 경로) | BT-2~7, ADR-A | 1개월 M1 ≤5s | 240 |
| BT-11 | `application/deep_backtest_job.py` + 체크포인트 통합 | BT-10 | 중단·재개, 진행률 | 260 |
| BT-12 | `application/tearsheet.py` + performance 계약 재사용 | BT-10 | 리포트 스냅샷 | 200 |
| BT-13 | 프론트 차트 즉시 백테스트 패널(`BacktestPanel.tsx`) + 결과 마커 | BT-10, CH-6 | 화면·negative | 300 |

### 9.6 (CH) 차트
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| CH-0 | `docs/design/CHART_ENGINE_FORK_EVAL.md` + 벤치 스크립트 | — | 후보 4종(KLineChart·Lightweight Charts·NightVision·react-financial-charts) 라이선스 원문 확인·10만 봉 fps·드로잉 확장성·TS 모듈 경계 채점표, 포크 추천 1순위. CA 확정 후 CH-1 착수 | 240 |
| CH-1 | `packages/chart-engine` 골격 + `vendor/`에 포크 반입(NOTICE·LICENSE) + `src/core/*.ts` 래퍼 + 테스트 | CH-0 | 시리즈 생성·업데이트·리사이즈, 라이선스 고지 파일 존재, 빌드 통과 | 600 |
| CH-2 | `data/candleStream.ts` + 테스트 | CH-1, DC-18 | 페이지네이션+실시간 병합, 중복/역순/갭 | 240 |
| CH-3 | `indicators/overlayRegistry.ts` + 테스트 | CH-1 | 페인/오버레이 매핑 | 200 |
| CH-4 | `drawings/{model,tools,serialize}.ts` + 테스트 | CH-1 | 5종 도구, 직렬화 왕복 | 780 |
| CH-5 | backend charting 컨텍스트(계약·저장·API) + 통합·교차테넌트 테스트 | PLT-28 | 낙관적 잠금 409, 타 테넌트 404 | 780 |
| CH-6 | `ChartPage.tsx` + `ChartToolbar.tsx` + `IndicatorPicker.tsx` | CH-2~5 | 화면 조립·키보드 접근성 | 900 |
| CH-7 | `replay/replayController.ts` + 데이터 동일성 테스트 | CH-2, BT-10 | 리플레이=백테스트 봉 시퀀스 동일 | 220 |
| CH-8 | `layout/{layoutModel,persistence}.ts` + 서버 저장 | CH-5 | 멀티차트·워치리스트 복원 | 440 |
| CH-9 | `AlertFromChart.tsx` + 알림 규칙 API 연결 | CH-6, alerts | 차트에서 가격/지표 알림 생성 | 260 |
| CH-10 | `StrategyMarkers.tsx`(신호·체결 마커, 실행 상태 연동) | CH-6 | PAPER 실행 마커 표시 | 220 |

### 9.7 (MP) 마켓플레이스
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| MP-1 | `marketplace/contracts/v1.py` + 스냅샷 | DSL-12 | 스키마 | 280 |
| MP-2 | `domain/visibility.py` + test | MP-1 | 4단계 규칙, protected 소스 비노출 | 160 |
| MP-3 | `domain/versioning.py` + test | MP-1 | 불변 버전, 호환성 판정 | 200 |
| MP-4 | `domain/reputation.py` + test | MP-1 | §4.3 산식, 포화 | 220 |
| MP-5 | `domain/plagiarism.py` + test | DSL-1 | 변수명 변경 우회 탐지, 임계 | 220 |
| MP-6 | `domain/subscription_rules.py` + test | MP-1 | 주기·체험·비례 환불 정확값 | 240 |
| MP-7 | 마이그레이션 + `adapters/postgres_*.py` + 통합·교차테넌트 | MP-1 | | 520 |
| MP-8 | `ledger/domain/posting_rules_marketplace.py` + 분개 테스트 | LC-4, MP-6 | 구독·수익배분 분개 균형, 기존 규칙 불변 | 200 |
| MP-9 | `application/{publish,subscribe,moderate}.py` + 통합 | MP-2~8 | 유스케이스·심사 큐 | 720 |
| MP-10 | 프론트 `MarketplaceBrowse`·`ListingDetail`·`SellStrategy` 확장(가시성·구독·평판·재현 키) | MP-9 | 화면·negative | 900 |

### 9.8 우선순위와 병행 규칙
**원칙: AIOS는 TradingView에 연결해 쓰는 제품이 아니라 그 없이 독립적으로 같은 수준을 제공하는 제품이다.**
DC-1~18(R/L4 잔여보다 먼저, backend 4 중 2 고정) → CH-1~10 ∥ IND-1~8 → DSL-1~13 → BT-1~13 → MP-1~10 → SIG-1~6(선택 백로그, 외부 신호를 받고 싶은 사용자를 위한 부가 기능·맨 마지막).
프론트 리프(CH·DSL-13·BT-13·MP-10·SIG-6)는 frontend 풀, 나머지는 backend 풀. 각 영역 첫 리프는 QA에서 명세 §3 계약 스냅샷을 반드시 남긴다.
차트 렌더링 코어는 AIOS 소유 코드(`chart-engine`)다. OSS를 포크해 시작하되 점진 재작성으로 완전히 소유한다(ADR-B D2). 외부 서비스·계정·API 의존 없음.

## 10. 미확정·리스크
- 데이터 벤더 선택·라이선스(Polygon, Databento, EODHD, Kiwoom 등)는 **미확인·사람 결정**. 명세는 SPI 형태만 고정.
- 포크 후보의 라이선스·고지 의무(KLineChart, Lightweight Charts, NightVision, react-financial-charts)는 CH-0에서 원문 확인. 확인 전에는 어느 것도 반입하지 않는다.
- `pyarrow` 도입 여부(warm 계층)는 DC-14에서 벤치 후 결정; 대안은 numpy `.npz`.
- IND 100종 목록은 IND-2에서 카테고리별 확정(TA-Lib 전 종 + VWAP/anchored VWAP/Ichimoku/Volume Profile/Pivot/Supertrend 등).
- Pine Script 가져오기 변환기는 범위 밖(ADR-B Rejected). 요청이 많으면 별도 ADR.
- 딥 백테스트의 warm 계층 처리량 목표(10년 D1 + M1 magnifier ≤10분)는 DC-14 실측 후 조정 가능.
