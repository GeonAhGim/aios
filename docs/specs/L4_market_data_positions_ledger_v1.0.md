# L4 구현 명세 — 시장데이터 플랫폼 · 포지션/PnL 원장 · 머니 원장 v1.0

> 템플릿: `docs/specs/_TEMPLATE.md`. 한 리프 = 파일 하나(≤300줄) = 커밋 하나.
> 세 도메인(A 시장데이터, B 포지션/PnL, C 머니 원장)을 한 문서에 두는 이유:
> 셋이 같은 불변조건(append-only 저널 → 파생 스냅샷, 해시 체인, 멱등 포스팅,
> 증거 바인딩)을 공유하고, B는 A(마크가격·FX)와 C(수수료·펀딩)의 소비자다.

## 0. 문서 메타

| 항목 | 값 |
|---|---|
| status | DRAFT v1.0 (2026-09-03) — 리프 착수 가능 |
| owner role | Platform Data & Ledger Engineering (SCAFFOLD Zone, 인간 리뷰어 1인) |
| supersedes | `src/services/wallet_service.py`의 단식 원장(`wallet_transactions`) 의미론, `src/services/order_service/position_ledger.py`의 Phase 1 가정(실행당 1포지션·전량청산) |
| depends on | `docs/FULL_AUDIT_2026-09-02.md` §2/§3/§7/§8/§11 · `01_data_models_v1.4.md` §1.3/§1.4/§1.7 · `04_db_schema_v1.7.md` positions/audit_log/reconciliation_events · 기능설계문서 v1.21 FD-2/FD-3/FD-5/FD-13.7/FD-14 · `14_marketplace_detailed_v1.1.md` §14.1/§14.5 · ADR-2026-08-29 §1 · 79번(감사·증거 L3) · FND-03(`src/foundation/evidence/**`) · FND-08(`src/foundation/reconciliation/**`) · 105번(동시성) · 107번(계약 버전) · 108번(관측성) |
| implemented by | (A) `src/foundation/market_data/**` (B) `src/foundation/positions/**` (C) `src/foundation/ledger/**` — 전부 신규. 기존 파일 중 수정 대상은 §2 표에 `기존-수정`으로 표시 |
| verification evidence | `tests/foundation/{unit,integration,adversarial}/{market_data,positions,ledger}/**` (§8), 기존 `tests/integration/test_dispute_resolution_service.py`·`test_marketplace_router.py`는 브리지 전환 후에도 그대로 통과해야 한다 |
| migration head 기준 | `5ed4921f9873_connections_live_provider` (2026-09-03 확인, 단일 head) |

---

## 1. 기관급 요구 (왜 기초 수준으로는 부족한가)

### 1.1 도메인별 기관 요구

| # | 요구 | 근거·규제 성격 | 현재 코드(감사 인용) | 격차 |
|---|---|---|---|---|
| A1 | 캔들/틱은 **품질 게이트를 통과한 것만** 저장·소비. 갭·스테일·스파이크·중복은 판정 근거와 함께 격리(quarantine) | 백테스트 재현성, 8.1-A 오라클 왜곡 방어 | `candle_parser.py`는 파싱만 한다. 갭·중복·OHLC 정합성(`low<=open<=high`) 검사 0건. 저장 테이블 없음(캔들은 메모리에서 소비되고 버려짐) | 품질 판정·격리·저장 계층 전체 부재 |
| A2 | 거래소별 **거래 캘린더·세션**(KRX 09:00–15:30 KST, US 09:30–16:00 ET·조기폐장, 크립토 24×7)이 갭 판정과 NAV 마감의 기준 | 감사 §7 "`market_hours`는 선언만 되고 읽는 코드 0" | `src/exchanges/kis/adapter.py:192` `MarketHours` 선언만. 휴장일 데이터 없음 | 캘린더 저장소·세션 규칙·휴장일 부재 |
| A3 | **기업행위·심볼 생애주기**(분할, 상장폐지, 티커 변경)를 조정계수 체인으로 표현, RAW/ADJUSTED 조회 분리 | KRX/US 주식 확장(ADR-2026-08-28 다자산군) 시 필수 | 없음. 심볼 정규화가 어댑터마다 달라 `"BTC/USDT"`↔`"BTCUSDT"` 불일치(감사 §7) | 참조데이터 레지스트리·별칭·조정 부재 |
| A4 | **계보(lineage)**: 모든 저장 배치는 source·요청 파라미터·응답 해시·판정으로 추적, 79번 `evidence_object` 형식으로 감사 이벤트에 바인딩 | 79번 §1, 감사 §3 "감사 이벤트 호출자 0" | `append_audit_event` 호출 0 | 배치 단위 계보 없음 |
| A5 | **백테스트 리플레이는 결정론적**: 같은 `as_of`+같은 범위 → 같은 바이트 | FD-14.4/9.3 백테스트 재현 | `foundation/backtest`는 호출자가 캔들을 주입 | as_of 스냅샷·정렬·해시 검증 부재 |
| A6 | 데이터 품질 지표(staleness, gap ratio, reject 비율)를 **메트릭으로 export** | 108번, 감사 §0 "관측성 0" | 메트릭 레지스트리 없음 | 신설 |
| B1 | 포지션은 **append-only 저널**(체결·펀딩·수수료·조정)과 그로부터 **재구축 가능한 스냅샷**. 스냅샷은 언제든 삭제 후 재빌드해 같은 값 | 8.10 감사 원칙, 09번 §9.1 #9 | `position_ledger.py`가 `positions` 행을 in-place UPDATE(`quantity = 0, realized_pnl = realized_pnl + $2`). 부분청산·분할매수 불가(docstring 자인) | 저널/스냅샷 분리 부재, 이력 손실 |
| B2 | 원가법 **FIFO/가중평균**을 계좌·자산군별로 선택, 실현/미실현 PnL은 **기준통화로 FX 환산**(환율 출처·시각 기록) | 다계좌·다통화(Bitget USDT, KIS KRW) | `portfolio_service.py` docstring: "FX 변환 서비스 없음, 호출부가 단일 통화로 정리" | 원가법·FX 계층 부재 |
| B3 | **펀딩·수수료**가 PnL에 귀속, 일별 **NAV 체인**(전일 NAV + 손익 + 자금흐름 = 당일 NAV)이 대수적으로 검증 | 운용보고(FD-20) 정확성 | `report_service.py`는 `closed_at` 포지션 realized만 합산, 수수료·펀딩 0 | NAV·수수료·펀딩 부재 |
| B4 | 공급자 잔고/포지션과 **분 단위 대사**, 브레이크는 발생 후 수 분 내 표면화·에스컬레이션 | 8.4, FND-08 REC-00x | FND-08은 입력을 호출자가 주입, 스케줄러·내부 원장 없음(마이그레이션 `f2b8e5d1a734` docstring). `get_positions` 항상 빈 리스트(감사 §7) | 대사 입력 조립·주기 실행 부재 |
| C1 | 머니 원장은 **복식부기**(모든 분개 Σ차변 = Σ대변, DB 제약으로 강제), 저널 **불변**(UPDATE/DELETE 불가, 역할 분리로 소유자 우회 차단), **해시 체인** | 회계·감사, 16.3 P0-3 | `wallet_transactions`는 단식(잔액 after만). WORM REVOKE 없음(감사 §2). `audit_log` REVOKE는 소유자 role 접속으로 무력(감사 §8) | 복식·WORM·체인 전부 부재 |
| C2 | **합계 보존이 테스트로 증명**: 어떤 사건 열에서도 Σ(사용자 부채) + Σ(플랫폼 계정) = 0 | 자금 창출/소멸 방지 | **현재 환불이 자금을 창출한다**: `DELISTED_AND_REFUND` 시 구매자에 `price_paid` 전액 적립하나 판매자 `SALE_CREDIT`·하우스 `COMMISSION_CREDIT`은 환수하지 않음(`dispute_resolution_service.py:125-142`). 환불 1건당 `price_paid`만큼 시스템 총잔액 증가 | 합계 보존 위반(P0급, §10 R1) |
| C3 | 모든 포스팅은 **비즈니스 사건(event_type, event_ref)에 추적**되고 **멱등**(같은 사건 재전송 → 같은 분개, 다른 내용 → 거부) | 105번 | `debit/credit`에 멱등키 없음; `related_purchase_id`만 | 사건 추적성 부분 |
| C4 | 구매는 **홀드/에스크로** → 캡처, 판매대금은 차지백 창(Draft T+7) 동안 보류 후 **정산 스케줄**로 지급, 플랫폼 커미션은 수익계정에 회계 | 14번 §14.5 분쟁·환불 | 즉시 정산(ADR §1). 환불 재원 없음 | 홀드·정산 스케줄 부재 |
| C5 | 분개 1건 = 감사 이벤트 1건(FND-03 체인)에 **증거 바인딩** | 79번 §2 | `record_audit_log`(legacy)만 5파일에서 호출 | FND-03 미연결 |

### 1.2 이 명세가 바꾸지 않는 것

- FROZEN_PAPER_ONLY Zone(`src/core/{strategy,portfolio,risk,executor}`)은 손대지 않는다. B의 스냅샷을 legacy `positions` 테이블로 **투영**해 RiskGuard·portfolio·report가 읽던 컬럼을 그대로 채운다.
- ADR-2026-08-29 §1의 "1크레딧 = 1KRW, 자동 PG 미도입, 충전은 관리자 수동 확인"은 유지. 바뀌는 것은 **회계 표현**(단식→복식)과 **정산 시점**(즉시→홀드 창 후)이다. 후자는 ADR 개정 필요(§10 R2).

---

## 2. 모듈 분해 (최소단위)

표기: **신규** / **기존-유지** / **기존-수정**. Zone은 `.aios-zone` 기준(전부 SCAFFOLD, 테스트·config는 OPEN). 도메인 규칙 파일은 asyncpg/httpx import 금지(ruff `banned-api`로 CI 강제 — 리프 L0-2).

### 2.1 공통 기반 (신규)

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| `src/core/observability/metrics_registry.py` **신규** | 프로세스 내 counter/gauge/histogram 레지스트리 + Prometheus 텍스트 노출 형식 직렬화 | `class MetricsRegistry: counter(name, labels) -> Counter; gauge(...); histogram(name, buckets, labels); render_text() -> str` · `get_registry() -> MetricsRegistry` | 없음 | 200 | SCAFFOLD |
| `src/api/routers/metrics.py` **신규** | `GET /metrics` (관리자 토큰 또는 내부 네트워크 한정) | `router: APIRouter` | metrics_registry | 60 | SCAFFOLD |
| `src/core/db/append_only.py` **신규** | append-only 테이블 공통 DDL 헬퍼(REVOKE + `RAISE EXCEPTION` 트리거 생성 SQL 문자열 생성) — 마이그레이션에서 호출 | `def worm_sql(table: str) -> list[str]` · `def worm_drop_sql(table) -> list[str]` | 없음 | 80 | SCAFFOLD |
| `src/core/db/roles.py` **신규** | 역할 분리 SQL: `aios_migrator`(소유자)·`aios_app`(DML만, append-only 테이블은 INSERT/SELECT만) 생성·권한 부여 | `def ensure_roles_sql(app_role: str, migrator_role: str) -> list[str]` | 없음 | 120 | SCAFFOLD |
| `src/db/migrations/versions/4a1d0c0de001_db_roles_and_worm_helper.py` **신규** | 역할 생성(존재 시 skip) + legacy `audit_log`·`foundation_audit_event`·`wallet_transactions`에 WORM 트리거 소급 | `upgrade()/downgrade()` | roles, append_only | 120 | SCAFFOLD |

### 2.2 (A) 시장데이터 플랫폼 — `src/foundation/market_data/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| `contracts/v1.py` **신규** | 외부 노출 DTO(§3.1) | §3.1 전체 | pydantic | 280 | SCAFFOLD |
| `domain/timeframe.py` **신규** | 타임프레임 enum·길이·정렬 | `class Timeframe(str, Enum)` · `def duration(tf) -> timedelta` · `def align_open(ts, tf) -> datetime` · `def expected_opens(start, end, tf, sessions: list[SessionWindow]) -> list[datetime]` | 없음 | 120 | SCAFFOLD |
| `domain/quality/ohlc_sanity.py` **신규** | 단일 캔들 정합성(`low<=min(open,close)`, `high>=max(open,close)`, `volume>=0`, `close_time==open_time+duration`, tz-aware UTC) | `def check_candle(c: CandleRecord) -> list[QualityIssue]` | 없음 | 90 | SCAFFOLD |
| `domain/quality/gap_detector.py` **신규** | 세션 기준 기대 open_time 집합과 실제 집합의 차 → GAP 이슈(세션 밖 결측은 갭 아님) | `def detect_gaps(candles, tf, sessions) -> list[QualityIssue]` | timeframe | 110 | SCAFFOLD |
| `domain/quality/stale_detector.py` **신규** | 마지막 캔들/틱 시각과 `now`의 차가 세션 내에서 `k × duration` 초과 시 STALE | `def detect_stale(last_ts, now, tf, session_open: bool, k: int = 3) -> QualityIssue \| None` | timeframe | 70 | SCAFFOLD |
| `domain/quality/outlier_detector.py` **신규** | 로그수익률 rolling median/MAD 기반 스파이크(Draft: `\|r\| > 8·MAD`, window 60) + 인접 캔들 대비 `high/low` 비율 상한 | `def detect_spikes(candles, window=60, k_mad=Decimal("8")) -> list[QualityIssue]` | 없음 | 130 | SCAFFOLD |
| `domain/quality/dedupe.py` **신규** | 같은 (venue, instrument, tf, open_time) 중복: 내용 동일 → 1건 유지(DUPLICATE_IDENTICAL, info), 내용 상이 → CONFLICT(둘 다 격리) | `def dedupe(candles) -> DedupeResult` | 없음 | 90 | SCAFFOLD |
| `domain/quality/verdict.py` **신규** | 이슈 집합 → 배치 판정(ACCEPT/PARTIAL/QUARANTINE/REJECT), fail-closed 규칙표(§4.1) | `def decide(issues, total: int) -> QualityVerdict` | 없음 | 80 | SCAFFOLD |
| `domain/calendar/session_rules.py` **신규** | venue별 세션 창 계산(정규장·조기폐장·24×7). 휴장일 목록은 입력으로 받는다(순수) | `class VenueCalendar(venue, tz, regular: SessionSpec, holidays: frozenset[date], early_closes: dict[date, time])` · `def sessions_for(day: date) -> list[SessionWindow]` · `def is_open(at: datetime) -> bool` · `def next_open(at) -> datetime` · `def trading_day_of(at) -> date` | 없음 | 180 | SCAFFOLD |
| `domain/calendar/known_venues.py` **신규** | KRX/US(NYSE·NASDAQ 동일 취급)/BITGET/KIS 세션 스펙 상수 + 크립토 24×7 | `KNOWN_SESSIONS: dict[str, SessionSpec]` | session_rules | 80 | SCAFFOLD |
| `domain/reference/symbol_normalizer.py` **신규** | venue 원시 심볼 ↔ canonical(`BASE/QUOTE`, KRX 6자리 코드, US 티커) 단일 규칙 — 어댑터의 중복 로직을 이 파일로 수렴 | `def to_canonical(venue, raw) -> str` · `def to_venue(venue, canonical) -> str` | 없음 | 120 | SCAFFOLD |
| `domain/reference/lifecycle.py` **신규** | 심볼 상태기계(§4.2) | `def transition(state: SymbolStatus, event: LifecycleEvent) -> SymbolStatus` (불가 시 `LifecycleTransitionError`) | 없음 | 80 | SCAFFOLD |
| `domain/corporate_actions/adjustment.py` **신규** | 조정계수 체인(누적 곱), 분할·배당(가격만)·병합. RAW→ADJUSTED 변환은 순수 함수 | `def factor_chain(actions: list[CorporateAction], as_of: datetime) -> list[AdjustmentFactor]` · `def adjust(candles, factors) -> list[CandleRecord]` | 없음 | 140 | SCAFFOLD |
| `domain/lineage.py` **신규** | 배치 해시(정렬된 레코드의 canonical JSON sha256), 요청 지문 | `def batch_hash(records) -> str` · `def request_fingerprint(source, params) -> str` | 없음 | 60 | SCAFFOLD |
| `ports/candle_store.py` **신규** | 캔들/틱/격리 저장·조회 Protocol | `class CandleStore(Protocol): upsert_batch(conn, batch_id, candles) -> int; quarantine(conn, batch_id, candles, issues); query(conn, key, start, end, as_of) -> list[CandleRecord]; last_open_time(conn, key) -> datetime \| None` | 없음 | 90 | SCAFFOLD |
| `ports/reference_repository.py` **신규** | 인스트루먼트·별칭·기업행위 | `class ReferenceRepository(Protocol): get_instrument(venue, canonical, at) ; register(...) ; add_alias(...) ; list_actions(instrument_id) ; record_action(...)` | 없음 | 80 | SCAFFOLD |
| `ports/calendar_repository.py` **신규** | venue 캘린더 일자 | `class CalendarRepository(Protocol): load(venue, year) -> VenueCalendar; upsert_days(venue, days)` | 없음 | 50 | SCAFFOLD |
| `ports/ingest_source.py` **신규** | 원시 데이터 공급자 | `class IngestSource(Protocol): fetch_candles(venue, raw_symbol, tf, start, end) -> RawFetch` | 없음 | 50 | SCAFFOLD |
| `ports/batch_repository.py` **신규** | 계보 배치·이슈 기록 | `class BatchRepository(Protocol): create(conn, IngestBatchRecord) ; add_issues(conn, batch_id, issues) ; get(batch_id)` | 없음 | 60 | SCAFFOLD |
| `application/ingest_candles.py` **신규** | 커맨드: fetch → parse(기존 parser) → sanity → dedupe → gap/stale/spike → verdict → 저장/격리 → 배치 기록 → 감사 이벤트(§4.1 fail-closed) | `async def ingest_candles(cmd: IngestCandlesCommand, *, source, store, refs, cal, batches, audit, pool, clock) -> IngestBatchResult` | 위 5 포트 + `AuditEventRepository` | 220 | SCAFFOLD |
| `application/ingest_ticks.py` **신규** | 틱 동일 파이프라인(갭 검사 대신 trade_id 단조성·시각 역행 검사) | `async def ingest_ticks(cmd: IngestTicksCommand, ...) -> IngestBatchResult` | 동일 | 180 | SCAFFOLD |
| `application/get_candles.py` **신규** | 조회: `as_of` 이전에 저장된 배치만, `adjustment=RAW\|ADJUSTED` | `async def get_candles(q: CandleQuery, *, store, refs, pool) -> CandleSeries` | store, refs | 120 | SCAFFOLD |
| `application/replay_candles.py` **신규** | 백테스트용 결정론 리플레이: 정렬·해시·`series_hash` 반환, 격리 캔들 제외, 갭은 명시(채우지 않음) | `async def replay(q: ReplayRequest, ...) -> ReplaySeries` | store | 120 | SCAFFOLD |
| `application/register_instrument.py` **신규** | 참조데이터 등록·별칭·생애주기 전이 + 감사 | `async def register_instrument(cmd, *, refs, audit, pool)` · `async def apply_lifecycle_event(cmd, ...)` | refs, audit | 140 | SCAFFOLD |
| `application/record_corporate_action.py` **신규** | 기업행위 기록(멱등: `(instrument, type, ex_date)`) + 감사 | `async def record_corporate_action(cmd, *, refs, audit, pool) -> CorporateActionView` | refs, audit | 100 | SCAFFOLD |
| `application/sync_calendar.py` **신규** | 캘린더 연도 단위 적재(yaml/공급자) | `async def sync_calendar(venue, year, days, *, cal, audit, pool)` | cal, audit | 90 | SCAFFOLD |
| `application/quality_metrics.py` **신규** | 최근 배치·스테일 상태 → 메트릭 게이지 갱신 | `async def export_quality_metrics(*, batches, store, pool, registry, clock)` | batches, store | 120 | SCAFFOLD |
| `application/scheduler.py` **신규** | 심볼×tf별 주기 ingest + 지표 export(실행별 실패 격리, `execution_loop/scheduler.py`와 같은 패턴) | `async def run_market_data_scheduler(app_state, *, interval_s, stop: asyncio.Event)` | ingest_candles, quality_metrics | 160 | SCAFFOLD |
| `adapters/postgres_candle_store.py` **신규** | `md_candle`/`md_tick`/`md_quarantine_candle` asyncpg | CandleStore 구현 | asyncpg | 260 (초과 시 tick 분리 → `postgres_tick_store.py`) | SCAFFOLD |
| `adapters/postgres_reference_repository.py` **신규** | `md_instrument`/`md_symbol_alias`/`md_corporate_action` | ReferenceRepository 구현 | asyncpg | 220 | SCAFFOLD |
| `adapters/postgres_calendar_repository.py` **신규** | `md_venue_calendar_day` | CalendarRepository 구현 | asyncpg | 120 | SCAFFOLD |
| `adapters/postgres_batch_repository.py` **신규** | `md_ingest_batch`/`md_quality_issue` | BatchRepository 구현 | asyncpg | 140 | SCAFFOLD |
| `adapters/bitget_ingest_source.py` **신규** | `ExchangeAdapter.get_ohlcv` 호출 + 원시 응답 바이트 해시 보존 | IngestSource 구현 | `src/exchanges/common/adapter.py` | 100 | SCAFFOLD |
| `adapters/kis_ingest_source.py` **신규** | KIS 일봉/분봉 | IngestSource 구현 | KIS adapter | 100 | SCAFFOLD |
| `adapters/yaml_calendar_source.py` **신규** | `config/market_calendars/{KRX,US}_{year}.yaml` 로더(휴장일·조기폐장) | `def load_calendar_yaml(path) -> list[CalendarDay]` | pyyaml | 80 | SCAFFOLD |
| `src/core/parser/candle_parser.py` **기존-유지** | Bitget 배열 → `Candle` | 변경 없음. `ingest_candles`가 `Candle`→`CandleRecord`로 승격 | — | 67 | SCAFFOLD |
| `src/core/parser/data_trust_checker.py`, `src/core/safety/data_distrust.py` **기존-유지** | 피드 간 괴리(오라클) — 이 명세의 품질 게이트와 직교(전자는 소스 간, 후자는 시계열 내) | 변경 없음 | — | — | SCAFFOLD |
| `src/core/scanner/market_scanner.py`, `src/core/indicators/talib_adapter.py` **기존-유지** | 스캔·지표 | 입력을 `get_candles`로 바꾸는 배선은 후속(§10) | — | — | SCAFFOLD |
| `src/exchanges/bitget/market_data_mixin.py`, `src/exchanges/kis/market_data_mixin.py` **기존-수정** | 심볼 변환을 `symbol_normalizer`로 위임(감사 §7 불일치 해소) | 시그니처 불변 | normalizer | — | SCAFFOLD |

### 2.3 (B) 포지션 & PnL — `src/foundation/positions/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| `contracts/v1.py` **신규** | DTO(§3.2) | §3.2 | pydantic | 260 | SCAFFOLD |
| `domain/position_key.py` **신규** | 포지션 식별자 `venue:instrument_id:strategy_id:execution_id` 직렬화·파싱 | `class PositionKey(frozen dataclass)` · `parse/str` | 없음 | 60 | SCAFFOLD |
| `domain/cost_basis/fifo.py` **신규** | FIFO 로트 큐: 매수는 로트 push, 매도는 head부터 소진, 실현손익 = Σ(체결가−로트원가)×수량 | `class FifoLots: apply(fill: FillEvent) -> CostBasisResult` · `lots -> tuple[Lot, ...]` · `to_json/from_json` | 없음 | 150 | SCAFFOLD |
| `domain/cost_basis/weighted.py` **신규** | 가중평균: 매수 시 평단 재계산, 매도 시 평단 유지 | `class WeightedAverage: apply(fill) -> CostBasisResult` | 없음 | 90 | SCAFFOLD |
| `domain/cost_basis/selector.py` **신규** | 계좌 `cost_method`·자산군으로 구현 선택(파생상품은 가중평균 강제, 현물 기본 FIFO — Draft) | `def cost_basis_for(method: CostMethod, asset_class) -> CostBasis` | fifo, weighted | 50 | SCAFFOLD |
| `domain/fx.py` **신규** | `Money`를 기준통화로 환산. 환율 없음 → `FxRateMissingError`(0으로 대체 금지), 삼각환산 금지, 환율 시각·출처 결과에 동봉 | `def convert(m: Money, to: Currency, rate: FXRate \| None) -> Converted` | `src/data/models/base.py` | 80 | SCAFFOLD |
| `domain/pnl.py` **신규** | 미실현 = (mark − avg_cost) × qty × multiplier, 실현은 cost_basis 결과 사용, 전부 Decimal quantize(§3.5) | `def unrealized(snapshot, mark: Money, rate) -> PnLBreakdown` | fx | 90 | SCAFFOLD |
| `domain/funding_fees.py` **신규** | 펀딩(무기한): 포지션 부호×notional×rate, 수수료 귀속(체결 수수료는 체결 저널행에, 펀딩은 별도 행) | `def funding_amount(qty, mark, rate) -> Money` · `def fee_entry(fill) -> JournalLine` | 없음 | 80 | SCAFFOLD |
| `domain/journal_rules.py` **신규** | 저널 불변조건(§4.3): 시퀀스 연속, 수량 비음수(현물), 멱등키 형식, 행 해시·체인 | `def validate_append(prev: JournalEntry \| None, new: JournalEntry) -> None` · `def entry_hash(prev_hash, e) -> str` · `def verify_chain(entries) -> None` | 없음 | 140 | SCAFFOLD |
| `domain/snapshot_builder.py` **신규** | 저널 fold → 스냅샷(결정론). 재빌드와 증분 적용이 같은 결과여야 함(테스트로 증명) | `def fold(entries, cost_basis) -> PositionSnapshotState` · `def apply_one(state, entry, cost_basis) -> PositionSnapshotState` | cost_basis | 160 | SCAFFOLD |
| `domain/nav.py` **신규** | 일별 NAV: `closing = cash + Σ position_mv`, 체인 검증 `closing = opening + realized + Δunrealized + funding − fees + flows` (허용오차 0, Decimal) | `def compute_daily_nav(inputs: NavInputs) -> NAVSnapshot` · `def verify_chain(prev: NAVSnapshot, cur: NAVSnapshot) -> None` | 없음 | 120 | SCAFFOLD |
| `domain/reconciliation_rules.py` **신규** | 내부 스냅샷 vs 공급자 값 → FND-08 `EntitySnapshot` 조립 + 브레이크 나이 계산 | `def build_entity_snapshots(internal, provider) -> list[EntitySnapshot]` · `def break_age(detected_at, now) -> timedelta` | FND-08 contracts | 90 | SCAFFOLD |
| `ports/journal_repository.py` **신규** | 저널 append/조회 | `class PositionJournalRepository(Protocol): append(conn, entry) -> JournalEntry ; list_for(conn, key, from_seq=0) ; last(conn, key)` | 없음 | 60 | SCAFFOLD |
| `ports/snapshot_repository.py` **신규** | 스냅샷 upsert(조건부, `last_journal_seq` 기대값) | `class SnapshotRepository(Protocol): get(conn, key) ; upsert(conn, snap, expected_seq) ; list_open(conn, tenant_id, account_id)` | 없음 | 60 | SCAFFOLD |
| `ports/mark_price_source.py`, `ports/fx_rate_source.py`, `ports/provider_balance_source.py` **신규** (3파일) | 마크가격/FX/공급자 잔고 공급 Protocol | `async def mark(key, at) -> Money \| None` · `async def rate(base, quote, at) -> FXRate \| None` · `async def balances(connection_id) -> list[AccountBalance]` | 없음 | 각 40 | SCAFFOLD |
| `ports/nav_repository.py` **신규** | `pos_nav_daily` | `insert(conn, nav)` · `get(conn, account_id, day)` | 없음 | 40 | SCAFFOLD |
| `application/record_fill.py` **신규** | 체결 → 저널 append(멱등키 `fill:{order_id}:{fill_seq}`) → 스냅샷 증분 → legacy 투영 → 감사. 하나의 트랜잭션 | `async def record_fill(cmd: RecordFillCommand, *, journal, snapshots, projection, audit, pool) -> PositionSnapshotView` | journal, snapshots, projection, audit | 180 | SCAFFOLD |
| `application/record_funding_fee.py` **신규** | 펀딩/수수료 저널 append + 스냅샷 | `async def record_funding(cmd) ; async def record_fee(cmd)` | 동일 | 120 | SCAFFOLD |
| `application/rebuild_snapshot.py` **신규** | 저널 전체 fold로 스냅샷 재구축, 기존 스냅샷과 diff 보고(운영 도구) | `async def rebuild_snapshot(key, *, journal, snapshots, pool, dry_run=True) -> RebuildReport` | journal, snapshots | 110 | SCAFFOLD |
| `application/mark_positions.py` **신규** | 열린 스냅샷에 마크가격·FX 적용해 미실현 갱신(MARK 저널행은 남기지 않음 — 파생값) | `async def mark_positions(tenant_id, account_id, *, snapshots, marks, fx, pool, clock)` | snapshots, marks, fx | 120 | SCAFFOLD |
| `application/compute_daily_nav.py` **신규** | 세션 마감(A 캘린더) 기준 일별 NAV 산출·체인 검증·저장(멱등: `(account, day)`) | `async def compute_daily_nav(cmd, *, snapshots, cash: CashSource, nav_repo, calendar, fx, pool) -> NAVSnapshot` | 다수 | 160 | SCAFFOLD |
| `application/reconcile_provider.py` **신규** | 공급자 잔고·포지션 조회 → `EntitySnapshot` → FND-08 `run_reconciliation` 호출 → 브레이크 메트릭·알림 | `async def reconcile_account(account_id, *, snapshots, provider, recon: RunReconciliation, pool, registry)` | provider, FND-08 | 150 | SCAFFOLD |
| `application/scheduler.py` **신규** | 마크(Draft 10s)·대사(Draft 60s)·NAV(세션 마감 +5m) 주기 실행 | `async def run_positions_scheduler(app_state, *, stop)` | 위 3 | 140 | SCAFFOLD |
| `application/queries.py` **신규** | 조회: 열린 포지션, PnL 분해, NAV 시계열 | `async def get_positions(...)` · `async def get_pnl_report(...)` | snapshots, nav_repo | 140 | SCAFFOLD |
| `adapters/postgres_journal_repository.py` **신규** | `pos_journal` (advisory lock per position_key) | 구현 | asyncpg | 180 | SCAFFOLD |
| `adapters/postgres_snapshot_repository.py` **신규** | `pos_snapshot` 조건부 upsert(`conditional_update`) | 구현 | `src/core/db/conditional_write.py` | 140 | SCAFFOLD |
| `adapters/postgres_nav_repository.py` **신규** | `pos_nav_daily` | 구현 | asyncpg | 80 | SCAFFOLD |
| `adapters/legacy_positions_projection.py` **신규** | 스냅샷 → legacy `positions` 행 upsert(RiskGuard·portfolio·report 호환) | `class LegacyPositionsProjection: project(conn, snap)` | asyncpg | 120 | SCAFFOLD |
| `adapters/candle_mark_price_source.py` **신규** | A의 최신 1m close → 마크(스테일이면 None) | MarkPriceSource 구현 | A `get_candles` | 80 | SCAFFOLD |
| `adapters/fx_rate_source.py` **신규** | USDT/KRW: Bitget·KIS 참조 시세 중앙값(Draft), 출처·시각 기록. 없으면 None | FxRateSource 구현 | A | 100 | SCAFFOLD |
| `adapters/exchange_balance_source.py` **신규** | `ExchangeAdapter.get_balance/get_positions` 래핑(실패 시 예외 전파 — 빈 리스트로 대체 금지, FD-3.3) | ProviderBalanceSource 구현 | exchanges | 80 | SCAFFOLD |
| `src/services/order_service/position_ledger.py` **기존-수정** | `record_fill_in_position_ledger`가 B의 `record_fill`로 위임(서명 유지, 내부 교체). Phase 1 가정 제거 | `async def record_fill_in_position_ledger(pool, order) -> None` 유지 | B | ≤116 | SCAFFOLD |
| `src/services/report_service.py`, `src/services/portfolio_service.py` **기존-유지** | legacy 투영을 계속 읽음. NAV 기반 보고서 전환은 후속 | — | — | — | SCAFFOLD |

### 2.4 (C) 머니 원장 — `src/foundation/ledger/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| `contracts/v1.py` **신규** | DTO(§3.3) | §3.3 | pydantic | 280 | SCAFFOLD |
| `domain/chart_of_accounts.py` **신규** | 계정코드 체계·유형·부호·음수허용(§3.3 `AccountCode`) | `def user_account(user_id, sub: UserSub) -> AccountCode` · `PLATFORM_CASH_CLEARING`, `PLATFORM_COMMISSION_REVENUE`, `PLATFORM_REFUND_RESERVE`, `PLATFORM_PAYOUT_CLEARING` 상수 · `def account_type(code) -> AccountType` · `def allows_negative(code) -> bool` | 없음 | 120 | SCAFFOLD |
| `domain/posting_rules.py` **신규** | 비즈니스 사건 → 분개행 목록(§4.4 표). 여기만이 "누가 차변·누가 대변"을 안다 | `def lines_for(event: LedgerEvent) -> list[PostingLine]` | chart_of_accounts, rounding | 220 | SCAFFOLD |
| `domain/rounding.py` **신규** | KRW 2dp `ROUND_HALF_EVEN`, 커미션 = round(price×rate), 정산 = price − 커미션(합 정확히 price) | `def split_commission(price: Decimal, rate: Decimal) -> tuple[Decimal, Decimal]` | `src/services/commission.py` 대체(기존은 곱만 하고 반올림 없음) | 50 | SCAFFOLD |
| `domain/balance_rules.py` **신규** | 분개 적용 후 잔액 규칙: `available = balance − held ≥ 0`(음수허용 계정 제외), 통화 일치 | `def check_balanced(lines) -> None` (Σ차=Σ대 아니면 `UnbalancedEntryError`) · `def apply(bal: Balance, line) -> Balance` | chart_of_accounts | 100 | SCAFFOLD |
| `domain/hash_chain.py` **신규** | 분개 해시(정렬된 행 canonical JSON + prev_hash + seq + event) — FND-03 `rules.py`와 같은 방식, 플랫폼 단일 체인 | `def lines_digest(lines) -> str` · `def entry_hash(prev, seq, event_type, event_ref, digest, posted_at) -> str` · `def verify_chain(entries) -> None` | 없음 | 90 | SCAFFOLD |
| `domain/idempotency.py` **신규** | 멱등키 형식 `{event_type}:{event_ref}`; 재전송 시 digest 비교 | `def idempotency_key(event) -> str` · `def assert_same_digest(existing, new) -> None` | hash_chain | 60 | SCAFFOLD |
| `domain/hold_state.py` **신규** | 홀드 FSM(§4.5) | `def transition(state: HoldState, event: HoldEvent) -> HoldState` | 없음 | 60 | SCAFFOLD |
| `domain/payout_schedule.py` **신규** | 캡처 목록 + 홀드 창(Draft 7일) + 마감 시각 → 판매자별 정산 배치(순수) | `def schedule(captures: list[CaptureRecord], *, hold_days: int, cutoff: datetime) -> list[PayoutBatchPlan]` | 없음 | 100 | SCAFFOLD |
| `domain/trial_balance.py` **신규** | 전 분개 fold → 계정별 잔액, Σ(전체) = 0 및 스냅샷 대조 | `def fold(lines) -> dict[AccountCode, Decimal]` · `def assert_zero_sum(balances) -> None` · `def diff(folded, snapshot) -> list[BalanceDrift]` | 없음 | 90 | SCAFFOLD |
| `ports/journal_repository.py` **신규** | 분개 append(advisory lock, 체인)·조회·멱등 lookup | `class LedgerJournalRepository(Protocol): append(conn, entry, lines) -> JournalEntryView ; find_by_idempotency_key(conn, key) ; list_since(conn, seq) ; last(conn)` | 없음 | 70 | SCAFFOLD |
| `ports/balance_repository.py` **신규** | 잔액 스냅샷 조건부 갱신 | `class BalanceRepository(Protocol): get_for_update(conn, account_ids) ; apply(conn, account_id, delta_balance, delta_held, expected_seq)` | 없음 | 60 | SCAFFOLD |
| `ports/hold_repository.py`, `ports/payout_repository.py` **신규** | 홀드/정산 배치 | `create/transition(conn, hold_id, expected_state, new_state, entry_id)` · `create_batch/list_due/mark_paid` | 없음 | 각 60 | SCAFFOLD |
| `application/post_entry.py` **신규** | 모든 포스팅의 단일 경로: 멱등 lookup → `lines_for` → `check_balanced` → 잔액 FOR UPDATE → `apply` → journal append → 잔액 갱신 → 감사 이벤트(같은 트랜잭션 안에서 FND-03 append; 실패 시 전체 롤백 = fail-closed) | `async def post_entry(conn, event: LedgerEvent, *, journal, balances, audit, clock) -> JournalEntryView` | 4포트 | 200 | SCAFFOLD |
| `application/topup.py` **신규** | 충전 확인 → `TOPUP_CONFIRMED` 사건 포스팅 | `async def post_topup(conn, topup_id, user_id, amount, admin_id, ...)` | post_entry | 70 | SCAFFOLD |
| `application/purchase_flow.py` **신규** | 구매: `place_hold`(HOLD) → `capture_hold`(CAPTURE: 판매자 PENDING_PAYOUT + 커미션 수익) — 같은 트랜잭션, 홀드 레코드는 감사용으로 남김 | `async def place_hold(conn, ...) -> HoldView` · `async def capture_hold(conn, hold_id, ...) -> CaptureView` · `async def release_hold(conn, hold_id, reason)` | post_entry, holds | 180 | SCAFFOLD |
| `application/refund.py` **신규** | 환불 재원 결정(§4.4 R1~R3) + `REFUND` 포스팅. 판매자 잔액 부족 시 `PLATFORM_REFUND_RESERVE` 선지급 + 판매자 `RECEIVABLE` 음수 | `async def post_refund(conn, purchase_id, amount, admin_id, reason, ...) -> RefundView` | post_entry, balances | 150 | SCAFFOLD |
| `application/chargeback.py` **신규** | 은행 입금 취소 → 사용자 AVAILABLE 차감(부족 시 RECEIVABLE) | `async def post_chargeback(conn, topup_id, amount, admin_id, ...)` | post_entry | 90 | SCAFFOLD |
| `application/payouts.py` **신규** | 정산 배치 생성(PENDING_PAYOUT → AVAILABLE, `PAYOUT_RELEASE`)·오프플랫폼 지급 확정(`PAYOUT_PAID`: AVAILABLE → CASH_CLEARING) | `async def schedule_payouts(*, clock, hold_days, ...) -> list[PayoutBatchView]` · `async def mark_payout_paid(batch_id, admin_id, external_ref)` | post_entry, payouts | 160 | SCAFFOLD |
| `application/queries.py` **신규** | 잔액(available/held/pending_payout), 분개 타임라인(커서), 시산표 | `async def get_balance(user_id)` · `async def list_entries(account, cursor, limit)` · `async def trial_balance()` | journal, balances | 140 | SCAFFOLD |
| `application/verify_integrity.py` **신규** | 체인 검증 + 시산표 Σ=0 + 저널 fold vs `ledger_balance` 드리프트 → 메트릭·감사·알림. 드리프트 발견 시 결과를 `ledger_integrity_check`에 기록(원장은 건드리지 않음) | `async def verify_ledger_integrity(*, journal, balances, audit, pool, registry) -> IntegrityReport` | journal, balances | 140 | SCAFFOLD |
| `application/scheduler.py` **신규** | 무결성 검증(Draft 5분)·정산 스케줄(일 1회 00:10 KST) | `async def run_ledger_scheduler(app_state, *, stop)` | 위 2 | 100 | SCAFFOLD |
| `adapters/postgres_journal_repository.py` **신규** | `ledger_journal_entry`/`ledger_posting_line` (advisory lock `hashtext('ledger_journal')`) | 구현 | asyncpg | 220 | SCAFFOLD |
| `adapters/postgres_balance_repository.py` **신규** | `ledger_balance` FOR UPDATE + `conditional_update(expected last_entry_seq)` | 구현 | conditional_write | 140 | SCAFFOLD |
| `adapters/postgres_hold_repository.py`, `adapters/postgres_payout_repository.py` **신규** | `ledger_hold`/`ledger_payout_*` | 구현 | asyncpg | 각 120 | SCAFFOLD |
| `adapters/legacy_wallet_bridge.py` **신규** | 전환기 브리지: `wallet_service.debit/credit` 호출을 C 포스팅으로 **대체**하고 `user_wallets.balance`·`wallet_transactions`를 투영으로 유지(읽기 호환). 전환 단계 §5.4 | `async def bridge_debit(conn, user_id, amount, tx_type, related_purchase_id) -> Decimal` · `bridge_credit(...)` | post_entry | 140 | SCAFFOLD |
| `src/services/wallet_service.py` **기존-수정** | `debit/credit` 본문을 브리지 위임으로 교체(시그니처·예외 `InsufficientBalanceError` 유지). `confirm_topup`은 `post_topup` 호출 | 공개 서명 불변 | bridge | ≤244 | SCAFFOLD |
| `src/services/purchase_service.py` **기존-수정** | 3회의 `debit/credit`를 `place_hold`+`capture_hold` 1회로 교체. `PurchaseResult` 필드 유지 | 공개 서명 불변 | purchase_flow | ≤240 | SCAFFOLD |
| `src/services/dispute_resolution_service.py` **기존-수정** | `credit(REFUND)`를 `post_refund`로 교체(환수 포함) | 공개 서명 불변 | refund | ≤200 | SCAFFOLD |
| `src/services/commission.py` **기존-수정** | `calculate_commission`이 `rounding.split_commission`에 위임(반올림 도입, 합 보존) | 서명 불변 | rounding | ≤40 | SCAFFOLD |

### 2.5 분할 규칙(300줄 초과 예상 지점)

- `postgres_candle_store.py`: 캔들 upsert/조회와 틱을 분리(`postgres_tick_store.py`). 격리 테이블은 캔들 쪽에 둔다.
- `posting_rules.py`: 사건이 12종을 넘으면 `posting_rules_marketplace.py`(HOLD/CAPTURE/RELEASE/REFUND)와 `posting_rules_cash.py`(TOPUP/CHARGEBACK/PAYOUT)로 분할, `lines_for`는 디스패치만 남긴다.
- `post_entry.py`: 감사 바인딩 부분이 커지면 `adapters/audit_binding.py`로 분리.

---

## 3. 계약 (Contract)

공통: 모든 `datetime`은 tz-aware UTC(01번 §1.7, 검증기로 강제), 금액·수량은 `Decimal`(float 금지), 각 DTO에 `schema_version: Literal["v1"]`. 107번: 필드 **추가**는 minor(기본값 필수), 제거·의미 변경은 `v2` 모듈 신설. 계약 모듈은 `domain/`을 import하지 않는다(FND-03과 동일).

### 3.1 (A) `src/foundation/market_data/contracts/v1.py`

```python
class Timeframe(str, Enum): M1="1m"; M5="5m"; M15="15m"; M30="30m"; H1="1h"; H4="4h"; D1="1d"
class Venue(str, Enum): BITGET="BITGET"; KIS_KRX="KIS_KRX"; KIS_US="KIS_US"   # 세션 규칙 키
class Adjustment(str, Enum): RAW="RAW"; ADJUSTED="ADJUSTED"
class SymbolStatus(str, Enum): PENDING="PENDING"; LISTED="LISTED"; SUSPENDED="SUSPENDED"; DELISTED="DELISTED"
class QualityIssueType(str, Enum):
    OHLC_INCONSISTENT="OHLC_INCONSISTENT"; NEGATIVE_VOLUME="NEGATIVE_VOLUME"; TIME_MISALIGNED="TIME_MISALIGNED"
    NAIVE_DATETIME="NAIVE_DATETIME"; GAP="GAP"; STALE="STALE"; SPIKE="SPIKE"
    DUPLICATE_IDENTICAL="DUPLICATE_IDENTICAL"; DUPLICATE_CONFLICT="DUPLICATE_CONFLICT"; OUT_OF_SESSION="OUT_OF_SESSION"
class Severity(str, Enum): INFO="INFO"; WARN="WARN"; REJECT="REJECT"
class Verdict(str, Enum): ACCEPT="ACCEPT"; PARTIAL="PARTIAL"; QUARANTINE="QUARANTINE"; REJECT="REJECT"

class SeriesKey(BaseModel):
    venue: Venue; instrument_id: UUID; timeframe: Timeframe
class CandleRecord(BaseModel):
    key: SeriesKey; open_time: datetime; close_time: datetime
    open: Decimal; high: Decimal; low: Decimal; close: Decimal
    volume: Decimal; quote_volume: Decimal | None = None
    # 정밀도: 가격 NUMERIC(30,10), 수량 NUMERIC(30,10) — DB와 동일 quantize
class TickRecord(BaseModel):
    venue: Venue; instrument_id: UUID; trade_id: str; price: Decimal; quantity: Decimal
    side: Literal["buy","sell"]; traded_at: datetime
class QualityIssue(BaseModel):
    type: QualityIssueType; severity: Severity; open_time: datetime | None; detail: dict[str, str]
class QualityVerdict(BaseModel):
    verdict: Verdict; accepted: int; quarantined: int; rejected: int; issues: list[QualityIssue]
class IngestCandlesCommand(BaseModel):
    tenant_id: UUID | None      # None = 플랫폼 공용 데이터
    venue: Venue; canonical_symbol: str; timeframe: Timeframe
    range_start: datetime; range_end: datetime; trace_id: UUID
class IngestBatchResult(BaseModel):
    batch_id: UUID; verdict: QualityVerdict; batch_hash: str; audit_event_id: UUID | None
    stored_range: tuple[datetime, datetime] | None
class CandleQuery(BaseModel):
    key: SeriesKey; start: datetime; end: datetime; as_of: datetime | None = None
    adjustment: Adjustment = Adjustment.RAW; include_quarantined: bool = False
class CandleSeries(BaseModel):
    key: SeriesKey; candles: list[CandleRecord]; gaps: list[tuple[datetime, datetime]]
    adjustment: Adjustment; as_of: datetime; series_hash: str
class ReplayRequest(CandleQuery): pass          # as_of 필수(검증기), include_quarantined 항상 False
class ReplaySeries(CandleSeries): expected_count: int; missing_count: int

class SessionWindow(BaseModel): open_at: datetime; close_at: datetime; kind: Literal["REGULAR","EARLY_CLOSE","CONTINUOUS"]
class CalendarDay(BaseModel):
    venue: Venue; trade_date: date; is_trading_day: bool
    open_at: datetime | None; close_at: datetime | None; early_close: bool = False; source: str
class InstrumentRef(BaseModel):
    instrument_id: UUID; venue: Venue; canonical_symbol: str; venue_symbol: str
    asset_class: AssetClass; base: str | None; quote: str | None
    tick_size: Decimal; lot_size: Decimal; status: SymbolStatus; listed_at: datetime; delisted_at: datetime | None
class RegisterInstrumentCommand(BaseModel):
    venue: Venue; venue_symbol: str; asset_class: AssetClass; tick_size: Decimal; lot_size: Decimal
    listed_at: datetime; actor_subject_id: UUID; trace_id: UUID
class LifecycleEventCommand(BaseModel):
    instrument_id: UUID; event: Literal["LIST","SUSPEND","RESUME","DELIST","RENAME"]
    effective_at: datetime; new_venue_symbol: str | None = None; source_ref: str; actor_subject_id: UUID; trace_id: UUID
class CorporateAction(BaseModel):
    action_type: Literal["SPLIT","REVERSE_SPLIT","CASH_DIVIDEND","MERGER"]
    instrument_id: UUID; ex_date: date; ratio: Decimal      # SPLIT 2:1 → ratio=2 ; 배당은 ratio=1, amount 별도
    cash_amount: Decimal | None = None; source_ref: str
class DataQualityMetrics(BaseModel):
    key: SeriesKey; staleness_s: int; gap_ratio_24h: Decimal; reject_ratio_24h: Decimal; last_batch_id: UUID | None
```

에러 taxonomy (A):

| 코드 | 재시도 | 호출자 조치 |
|---|---|---|
| `MD_SYMBOL_UNKNOWN` | 불가 | 참조데이터 등록 후 재시도 |
| `MD_SYMBOL_NOT_TRADABLE` (SUSPENDED/DELISTED) | 불가 | 사용자에 상태 노출 |
| `MD_CALENDAR_MISSING` (venue·연도 캘린더 없음) | 불가 | `sync_calendar` 후 — **크립토는 캘린더 불필요** |
| `MD_QUALITY_REJECTED` | 불가(같은 입력) | 격리 조회, 소스 점검 |
| `MD_SOURCE_UNAVAILABLE` (RetryableExchangeError 소진) | 가능(백오프) | 스케줄러 재시도, STALE 게이지 상승 |
| `MD_AS_OF_IN_FUTURE` | 불가 | 요청 수정 |
| `MD_REPLAY_INCOMPLETE` (expected≠stored, strict 모드) | 불가 | 갭 채운 뒤 재실행 |

### 3.2 (B) `src/foundation/positions/contracts/v1.py`

```python
class CostMethod(str, Enum): FIFO="FIFO"; WEIGHTED="WEIGHTED"
class JournalEntryType(str, Enum): FILL="FILL"; FUNDING="FUNDING"; FEE="FEE"; ADJUSTMENT="ADJUSTMENT"; CORP_ACTION="CORP_ACTION"
class RecordFillCommand(BaseModel):
    tenant_id: UUID; account_id: UUID; position_key: str
    order_id: UUID; fill_seq: int                       # 멱등키 = f"fill:{order_id}:{fill_seq}"
    side: OrderSide; quantity: Decimal; price: Money; fee: Money | None
    contract_multiplier: Decimal = Decimal("1"); occurred_at: datetime; trace_id: UUID
class RecordFundingCommand(BaseModel):
    tenant_id: UUID; account_id: UUID; position_key: str; funding_id: str   # 멱등키 f"funding:{funding_id}"
    amount: Money; rate: Decimal; occurred_at: datetime; trace_id: UUID
class PositionJournalEntryView(BaseModel):
    id: int; position_key: str; sequence_no: int; entry_type: JournalEntryType
    qty_delta: Decimal; price: Money | None; fee: Money | None
    realized_pnl_base: Decimal; fx_rate: Decimal | None; fx_source: str | None
    source_event_type: str; source_event_id: str; idempotency_key: str
    prev_hash: str | None; entry_hash: str; occurred_at: datetime; recorded_at: datetime
class Lot(BaseModel): quantity: Decimal; unit_cost: Decimal; opened_at: datetime
class PositionSnapshotView(BaseModel):
    position_key: str; tenant_id: UUID; account_id: UUID; instrument_id: UUID
    quantity: Decimal; avg_cost: Money; cost_method: CostMethod; lots: list[Lot]
    realized_pnl_base: Decimal; unrealized_pnl_base: Decimal | None; fees_base: Decimal; funding_base: Decimal
    mark_price: Money | None; mark_at: datetime | None; base_currency: Currency
    last_journal_seq: int; updated_at: datetime
class PnLBreakdown(BaseModel):
    realized: Decimal; unrealized: Decimal; fees: Decimal; funding: Decimal; total: Decimal
    base_currency: Currency; fx_rates_used: list[FXRate]
class NAVSnapshot(BaseModel):
    account_id: UUID; nav_date: date; base_currency: Currency
    opening_nav: Decimal; cash: Decimal; positions_mv: Decimal
    realized: Decimal; unrealized_delta: Decimal; funding: Decimal; fees: Decimal; flows: Decimal
    closing_nav: Decimal; fx_rates: list[FXRate]; source_hash: str
class RebuildReport(BaseModel): position_key: str; entries: int; drift: dict[str, tuple[Decimal, Decimal]]; applied: bool
```

에러 taxonomy (B): `POS_IDEMPOTENT_REPLAY`(오류 아님, 기존 뷰 반환) · `POS_IDEMPOTENCY_DIGEST_MISMATCH`(불가, 호출자 버그) · `POS_SEQUENCE_CONFLICT`(가능, 재조회 후 재시도 — `ConcurrencyConflictError` 매핑) · `POS_NEGATIVE_QUANTITY`(불가, 현물 공매도 금지 — 주문 경로 버그) · `POS_FX_RATE_MISSING`(가능, 환율 도착 후 — **0으로 대체 금지**) · `POS_MARK_STALE`(가능, 미실현은 `None` 유지) · `POS_NAV_CHAIN_BROKEN`(불가, 운영 개입) · `POS_ACCOUNT_UNKNOWN`(불가).

### 3.3 (C) `src/foundation/ledger/contracts/v1.py`

```python
class AccountType(str, Enum): ASSET="ASSET"; LIABILITY="LIABILITY"; REVENUE="REVENUE"; EXPENSE="EXPENSE"; CLEARING="CLEARING"
class UserSub(str, Enum): AVAILABLE="AVAILABLE"; HELD="HELD"; PENDING_PAYOUT="PENDING_PAYOUT"; RECEIVABLE="RECEIVABLE"
# AccountCode 문자열 형식: "USER:{uuid}:{UserSub}" | "PLATFORM:{NAME}"
class Side(str, Enum): DEBIT="DEBIT"; CREDIT="CREDIT"
class LedgerEventType(str, Enum):
    TOPUP_CONFIRMED="TOPUP_CONFIRMED"; HOLD_PLACED="HOLD_PLACED"; HOLD_CAPTURED="HOLD_CAPTURED"; HOLD_RELEASED="HOLD_RELEASED"
    REFUND="REFUND"; CHARGEBACK="CHARGEBACK"; PAYOUT_RELEASE="PAYOUT_RELEASE"; PAYOUT_PAID="PAYOUT_PAID"; MANUAL_ADJUSTMENT="MANUAL_ADJUSTMENT"
class LedgerEvent(BaseModel):
    event_type: LedgerEventType; event_ref: str          # 예: "purchase:123", "topup:45", "refund:dispute:7"
    tenant_id: UUID | None; actor_subject_id: UUID | None; trace_id: UUID
    amount: Decimal = Field(gt=0); currency: Currency = Currency.KRW
    parties: dict[str, UUID]                              # buyer/seller/user 등 규칙별 필수 키(§4.4)
    extra: dict[str, Decimal | str] = {}                  # commission_rate 등. secret류 키 금지(FND-03 규칙 재사용)
class PostingLine(BaseModel):
    line_no: int; account_code: str; side: Side; amount: Decimal = Field(gt=0, decimal_places=2); currency: Currency
class JournalEntryView(BaseModel):
    entry_id: UUID; sequence_no: int; event_type: LedgerEventType; event_ref: str; idempotency_key: str
    lines: list[PostingLine]; lines_digest: str; prev_hash: str | None; entry_hash: str
    audit_event_id: UUID; posted_at: datetime; replayed: bool = False
class BalanceView(BaseModel):
    account_code: str; balance: Decimal; held: Decimal; available: Decimal; pending_payout: Decimal
    currency: Currency; last_entry_seq: int; as_of: datetime
class HoldState(str, Enum): PENDING="PENDING"; CAPTURED="CAPTURED"; RELEASED="RELEASED"; EXPIRED="EXPIRED"
class HoldView(BaseModel): hold_id: UUID; account_code: str; amount: Decimal; purpose: str; reference: str; state: HoldState; expires_at: datetime; entry_id: UUID
class PayoutBatchView(BaseModel):
    batch_id: UUID; seller_user_id: UUID; period_start: date; period_end: date; amount: Decimal
    state: Literal["SCHEDULED","RELEASED","PAID","FAILED"]; capture_entry_ids: list[UUID]; release_entry_id: UUID | None; paid_entry_id: UUID | None
class TrialBalanceView(BaseModel): as_of: datetime; last_entry_seq: int; balances: dict[str, Decimal]; total: Decimal  # total은 항상 0
class IntegrityReport(BaseModel):
    checked_at: datetime; entries_verified: int; chain_ok: bool; zero_sum_ok: bool
    drifts: list[tuple[str, Decimal, Decimal]]; first_broken_seq: int | None
```

에러 taxonomy (C):

| 코드 | 재시도 | 호출자 조치 |
|---|---|---|
| `LEDGER_UNBALANCED_ENTRY` | 불가 | 규칙 버그 — 500, 알림 |
| `LEDGER_INSUFFICIENT_AVAILABLE` | 불가(잔액 변동 전) | 402로 노출(기존 `InsufficientBalanceError` 유지) |
| `LEDGER_IDEMPOTENT_REPLAY` | — | 오류 아님. `replayed=True` 뷰 반환 |
| `LEDGER_IDEMPOTENCY_DIGEST_MISMATCH` | 불가 | 같은 사건에 다른 금액 — 409, 감사 DENIED 이벤트 |
| `LEDGER_CURRENCY_MISMATCH` | 불가 | 호출자 버그 |
| `LEDGER_HOLD_STATE_INVALID` | 불가 | 409 |
| `LEDGER_ACCOUNT_FROZEN` | 불가 | 관리자 개입 |
| `INTEGRITY_LEDGER_CHAIN_BROKEN` / `INTEGRITY_TRIAL_BALANCE_NONZERO` / `INTEGRITY_BALANCE_DRIFT` | 불가 | **원장 쓰기 전면 차단**(fail-closed, §4.4) + CRITICAL |
| `LEDGER_AUDIT_APPEND_FAILED` | 가능 | 포스팅 전체 롤백(AUD-010) |

### 3.4 정밀도

| 값 | 타입 | quantize |
|---|---|---|
| 가격·수량(A, B) | `NUMERIC(30,10)` | `Decimal("1e-10")`, `ROUND_HALF_EVEN` |
| PnL 기준통화 금액(B) | `NUMERIC(30,10)` 저장, 보고 시 통화 소수자리(KRW 0, USDT 2)로 표시 quantize | 저장은 절대 반올림하지 않음 |
| FX rate | `NUMERIC(20,10)` | — |
| 원장 금액(C) | `NUMERIC(20,2)` KRW | `Decimal("0.01")`, `ROUND_HALF_EVEN`. 1크레딧=1원이므로 소수 발생은 커미션 계산뿐 |
| 조정계수 | `NUMERIC(20,10)` | — |

---

## 4. 불변조건·상태기계

### 4.1 (A) 품질 게이트 판정표 — fail-closed

| 이슈 | 심각도 | 처리 | 강제 위치 |
|---|---|---|---|
| OHLC 불일치·음수 거래량·정렬 오류·naive datetime | REJECT | 해당 캔들 격리(`md_quarantine_candle`) | 코드 + DB CHECK(`md_candle`에 동일 CHECK → 우회 삽입도 실패) |
| DUPLICATE_CONFLICT | REJECT | 양쪽 격리, 기존 저장분은 유지(덮어쓰지 않음) | 코드 + PK |
| SPIKE | WARN | 저장하되 `quality_flags` 비트 설정, 배치 판정 PARTIAL | 코드 |
| GAP | WARN | 저장, 갭 구간 `md_quality_issue`에 기록, 채우지 않음 | 코드 |
| STALE | WARN(게이지) | 저장 여부와 무관, 메트릭·알림 | 스케줄러 |
| 배치의 REJECT 비율 > 20%(Draft) | — | 배치 전체 QUARANTINE(부분 저장 금지), 감사 이벤트 `outcome=DENIED` | 코드 |
| 캘린더 없음(비크립토) | — | ingest 거부 `MD_CALENDAR_MISSING` — 갭 판정 불가 상태로 저장하지 않음 | 코드 |

불변: `md_candle` 행은 **INSERT ON CONFLICT DO NOTHING**만(내용 상이 시 격리) — 저장된 캔들은 수정되지 않는다. 정정은 새 배치가 `superseded_by` 없이 격리 테이블에 남고 운영자가 `md_candle_correction`(후속, §10) 절차로만 처리.

### 4.2 (A) 심볼 생애주기

| from | event | guard | to | side-effect | 감사 이벤트 |
|---|---|---|---|---|---|
| PENDING | LIST | `listed_at ≤ now` | LISTED | 별칭 활성 | `instrument.listed` |
| LISTED | SUSPEND | — | SUSPENDED | ingest 스케줄 제외, B 마크 중단(미실현 `None`) | `instrument.suspended` |
| SUSPENDED | RESUME | — | LISTED | 스케줄 복귀, 재개 후 첫 배치는 GAP 이슈 기대(세션 밖 취급) | `instrument.resumed` |
| LISTED/SUSPENDED | DELIST | 열린 포지션 0 **또는** 강제 플래그 | DELISTED | 열린 포지션 있으면 B에 `CORP_ACTION(DELIST)` 조정 저널 요구(마지막 마크로 청산 처리 — Draft) | `instrument.delisted` |
| LISTED | RENAME | `new_venue_symbol` 미사용 중 | LISTED | 이전 별칭 `valid_to=effective_at`, 새 별칭 insert; `instrument_id` 불변 | `instrument.renamed` |
| DELISTED | * | — | 거부 | — | `outcome=DENIED` |

### 4.3 (B) 포지션 저널 불변조건

| 불변 | 강제 | 위반 시 |
|---|---|---|
| `(position_key, sequence_no)` 유일·연속(1부터) | DB UNIQUE + 코드(`validate_append`: `new.seq == prev.seq+1`) | fail-closed(`POS_SEQUENCE_CONFLICT`) |
| 저널 UPDATE/DELETE 불가 | REVOKE + 트리거 + 역할 분리 | DB 예외 |
| `idempotency_key` 유일, 재전송은 digest 동일해야 | DB UNIQUE + 코드 | REPLAY 반환 / DIGEST_MISMATCH |
| 현물(`asset_class ∈ {CRYPTO, *_EQUITY, *_ETF, *_ETN}`) 수량 ≥ 0 | 코드(`snapshot_builder.apply_one`) | `POS_NEGATIVE_QUANTITY`, 저널 append 거부 |
| 스냅샷 = fold(저널) | 테스트(재빌드 diff = ∅) + `rebuild_snapshot` 운영 도구 + 스케줄 검증(일 1회) | 드리프트 메트릭·CRITICAL, 스냅샷 재빌드(저널 불변) |
| `pos_snapshot.last_journal_seq`는 단조 증가 | `conditional_update(expected last_journal_seq)` | 재조회 후 재시도 |
| 미실현은 마크 없으면 `None`(0 아님) | 코드 + DB nullable | — |
| NAV 체인 등식 | 코드(`nav.verify_chain`) + DB CHECK `closing_nav = cash + positions_mv` | `POS_NAV_CHAIN_BROKEN`, 저장 거부 |
| legacy `positions` 투영은 스냅샷에서만 씀 | `position_ledger.py` 수정 후 다른 쓰기 경로 0(grep 테스트) | — |

### 4.4 (C) 분개 규칙표 (`posting_rules.lines_for`)

계정 성격: `USER:*:AVAILABLE/HELD/PENDING_PAYOUT`은 플랫폼의 **부채**(사용자에게 갚을 크레딧), `USER:*:RECEIVABLE`은 **자산**(음수 허용 유일 계정), `PLATFORM:CASH_CLEARING`은 은행 입금 확인분 **자산**, `PLATFORM:COMMISSION_REVENUE` **수익**, `PLATFORM:REFUND_RESERVE` **비용/충당**, `PLATFORM:PAYOUT_CLEARING` 오프플랫폼 송금 **자산 감소** 경유. 부호 규약: 자산·비용은 차변 증가, 부채·수익은 대변 증가. 시산표 Σ(차변 − 대변) = 0.

| 사건 | 차변 | 대변 | 필수 parties/extra |
|---|---|---|---|
| TOPUP_CONFIRMED | `PLATFORM:CASH_CLEARING` amount | `USER:u:AVAILABLE` amount | user |
| HOLD_PLACED | `USER:b:AVAILABLE` amount | `USER:b:HELD` amount | buyer. guard: available ≥ amount |
| HOLD_CAPTURED | `USER:b:HELD` price | `USER:s:PENDING_PAYOUT` payout · `PLATFORM:COMMISSION_REVENUE` commission | buyer, seller, `commission_rate`. `payout+commission == price` |
| HOLD_RELEASED | `USER:b:HELD` amount | `USER:b:AVAILABLE` amount | buyer |
| PAYOUT_RELEASE(홀드 창 경과) | `USER:s:PENDING_PAYOUT` amount | `USER:s:AVAILABLE` amount | seller |
| PAYOUT_PAID(오프플랫폼 송금 확정) | `USER:s:AVAILABLE` amount | `PLATFORM:PAYOUT_CLEARING` amount | seller, `external_ref` |
| REFUND — R1 창 내(판매대금 아직 PENDING_PAYOUT) | `USER:s:PENDING_PAYOUT` payout · `PLATFORM:COMMISSION_REVENUE` commission | `USER:b:AVAILABLE` price | buyer, seller, purchase |
| REFUND — R2 창 후, 판매자 AVAILABLE ≥ payout | `USER:s:AVAILABLE` payout · `PLATFORM:COMMISSION_REVENUE` commission | `USER:b:AVAILABLE` price | 동일 |
| REFUND — R3 창 후, 판매자 부족 | `USER:s:AVAILABLE` (가용분) · `USER:s:RECEIVABLE` (부족분, 음수 허용) · `PLATFORM:COMMISSION_REVENUE` commission | `USER:b:AVAILABLE` price | 동일. `PLATFORM:REFUND_RESERVE`는 RECEIVABLE 대손 확정 시 별도 MANUAL_ADJUSTMENT |
| CHARGEBACK(입금 취소) | `USER:u:AVAILABLE`(가용분) · `USER:u:RECEIVABLE`(부족분) | `PLATFORM:CASH_CLEARING` amount | user, topup |
| MANUAL_ADJUSTMENT | 명시된 행 | 명시된 행 | 관리자 2인 승인(`approval_requests` 재사용) — 그래도 Σ차=Σ대 강제 |

불변(전부 DB 제약으로도 강제):

| 불변 | DB | 코드 |
|---|---|---|
| 분개마다 Σ차변 = Σ대변, 통화 단일 | `CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`(커밋 시 검사) | `check_balanced` |
| 행 금액 > 0, 2dp | `CHECK (amount > 0)`, `NUMERIC(20,2)` | Field |
| 저널·행 불변 | REVOKE + 트리거 + `aios_app`은 INSERT/SELECT만 | — |
| `available = balance − held ≥ 0` (RECEIVABLE 제외) | `CHECK (allow_negative OR balance - held >= 0)` | `apply` |
| `sequence_no` 전역 연속, 체인 | UNIQUE + advisory lock | `entry_hash`, `verify_chain` |
| 멱등키 유일 | UNIQUE | digest 비교 |
| 홀드 `(purpose, reference)` 유일 | UNIQUE | — |
| 무결성 위반 감지 시 쓰기 차단 | `ledger_control(single row) write_frozen BOOLEAN` — `post_entry`가 트랜잭션 첫 문장에서 `SELECT ... FOR SHARE` 후 true면 거부 | fail-closed |
| 감사 이벤트 없는 분개 없음 | `audit_event_id NOT NULL REFERENCES foundation_audit_event(id)` | 같은 트랜잭션 |

### 4.5 (C) 홀드 상태기계

| from | event | guard | to | side-effect | 감사 |
|---|---|---|---|---|---|
| — | place | available ≥ amount, 계정 미동결 | PENDING | HOLD_PLACED 분개 | `ledger.hold.placed` |
| PENDING | capture | 미만료 | CAPTURED | HOLD_CAPTURED 분개, `strategy_purchases` 확정 | `ledger.hold.captured` |
| PENDING | release | — | RELEASED | HOLD_RELEASED 분개 | `ledger.hold.released` |
| PENDING | expire(스케줄) | `now > expires_at` | EXPIRED | HOLD_RELEASED 분개(같은 규칙) | `ledger.hold.expired` |
| CAPTURED/RELEASED/EXPIRED | * | — | 거부 | — | `outcome=DENIED` |

Phase 1 구매(FIXED_ONE_TIME)는 place→capture를 **같은 트랜잭션**에서 수행한다(ADR §1 "즉시 확정" 유지). 홀드 레코드를 남기는 이유는 향후 승인형 구매·구독의 동일 경로 재사용과 감사 추적.

---

## 5. 동시성·멱등성·트랜잭션 경계 (105번)

| 쓰기 | 메커니즘 | 멱등키 스코프·digest | outbox |
|---|---|---|---|
| A `md_candle` upsert | `INSERT ... ON CONFLICT (venue, instrument_id, timeframe, open_time) DO NOTHING` + 충돌 행 사후 비교(격리) | 배치: `request_fingerprint(source, venue, symbol, tf, start, end)`; 같은 지문 재실행은 새 배치 행을 만들되 저장 0건이 정상 | 없음(감사 이벤트만) |
| A `md_ingest_batch` | INSERT only | — | — |
| A 참조데이터 생애주기 | `conditional_update(expected status)` | `(instrument_id, event, effective_at)` UNIQUE | — |
| A 기업행위 | `INSERT ON CONFLICT (instrument_id, action_type, ex_date) DO NOTHING RETURNING` → 없으면 기존 조회·digest 비교 | 동일 | — |
| B 저널 append | `pg_advisory_xact_lock(hashtext('pos_journal'), hashtext(position_key))` → last seq 읽기 → INSERT | `idempotency_key` UNIQUE; digest = sha256(qty_delta, price, fee, occurred_at) | — |
| B 스냅샷 | `conditional_update(pos_snapshot, id=position_key, expected last_journal_seq)` | — | — |
| B legacy 투영 | 같은 트랜잭션, `UPDATE positions ... WHERE id = $1` (투영 전용 행, `pos_snapshot.legacy_position_id`) | — | — |
| B NAV | `INSERT ... ON CONFLICT (account_id, nav_date) DO NOTHING RETURNING`; 기존 행과 `source_hash` 다르면 `POS_NAV_CHAIN_BROKEN`(덮어쓰기 금지) | `(account, day)` | — |
| C 분개 | 트랜잭션: `ledger_control FOR SHARE` → `pg_advisory_xact_lock(hashtext('ledger_journal'))`(전역 단일 체인) → 멱등 lookup → 관련 `ledger_balance` `FOR UPDATE`(account_id 정렬로 교착 방지) → INSERT entry/lines → `conditional_update(balance, expected last_entry_seq)` → FND-03 append(같은 conn) → COMMIT(deferred 트리거 검사) | `{event_type}:{event_ref}` 전역 UNIQUE; digest = `lines_digest` | 없음 — 이벤트 버스 발행(`ledger.entry.posted`)은 커밋 후 best-effort, 구독자는 저널을 진실로 재조회 |
| C 홀드 전이 | `conditional_update(ledger_hold, expected state)` | `(purpose, reference)` | — |
| C 정산 배치 | `INSERT ON CONFLICT (seller_user_id, period_end) DO NOTHING` | — | — |
| C 무결성 동결 | `UPDATE ledger_control SET write_frozen = true WHERE write_frozen = false` (관리자 해제는 승인 2인) | — | — |

### 5.1 트랜잭션 경계

- **한 커넥션, 한 트랜잭션**: `post_entry`·`record_fill`은 `conn`을 인자로 받는다(`wallet_service.debit`와 같은 계약). 호출자(purchase_service 등)가 `async with pool.acquire() as conn, conn.transaction()`을 연다. 감사 §2 P1 "커넥션 쥔 채 두 번째 커넥션 획득" 패턴 금지 — `record_command_event`(별도 conn)를 쓰지 않고 `AuditEventRepository.append_event_in(conn, ...)`를 FND-03 어댑터에 **추가**(리프 L0-4, 기존 `append_event` 유지).
- 이벤트 버스 발행·메트릭 갱신은 커밋 후.

### 5.2 클록

모든 `now`는 주입(`Clock = Callable[[], datetime]`). 스케줄러·테스트가 고정 클록을 준다. 감사 §9의 "31초 sleep" 패턴 금지.

### 5.3 스케줄러 상호 간섭

A(ingest) → B(mark) → B(recon) → B(NAV) 순서 의존이 있으나 결합하지 않는다: 각 스케줄러는 독립 태스크, B는 A의 `staleness_s > 3×duration`이면 마크를 건너뛰고 `POS_MARK_STALE` 게이지만 올린다.

### 5.4 C 전환 단계 (금전 데이터 마이그레이션)

1. **L-C1~C6**: 새 테이블·도메인·`post_entry`. 프로덕션 쓰기 없음.
2. **L-C9 백필**: `wallet_transactions`를 시간순으로 읽어 `MANUAL_ADJUSTMENT(event_ref="backfill:wallet_tx:{id}")`로 분개 생성 — 기존 환불의 창출분은 `PLATFORM:REFUND_RESERVE` 차변으로 흡수(자금 출처를 명시). 백필 후 `trial_balance` Σ=0 및 `USER:u:AVAILABLE == user_wallets.balance` 전 사용자 일치 검증(불일치 시 백필 실패, 원장 비움).
3. **L-C10 브리지 절체**: `wallet_service.debit/credit`가 브리지로 위임. `user_wallets`·`wallet_transactions`는 투영으로 계속 갱신(기존 라우터·프론트 무변경). 이 단계부터 `wallet_transactions`에도 WORM 트리거 활성.
4. **L-C11**: purchase/dispute 서비스가 홀드·환불 경로로 전환.
5. 후속(§10): `user_wallets.balance` 읽기를 `ledger_balance`로 교체하고 `wallet_transactions` 쓰기 중단.

---

## 6. 실패 모드와 복구

| 실패 | 감지 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|
| A 소스 5xx/429/타임아웃 | `RetryableExchangeError` 소진 | 배치 실패 기록(`verdict=REJECT, issues=[SOURCE_UNAVAILABLE]`), STALE 게이지 | 다음 주기 재시도; 스테일 > 10×duration → 알림 | `market_data.ingest` outcome=ERROR |
| A 소스가 조용히 스키마 변경(필드 누락) | `FatalExchangeError`(기존 parser) | 해당 심볼 ingest 중단, CRITICAL | 파서 수정 배포 | outcome=ERROR |
| A 스파이크 다발(플래시 크래시 vs 오류) | SPIKE 비율 > 5%/배치 | 배치 QUARANTINE, B 마크 스킵 | 운영자 격리 검토 → 정당하면 `md_candle_correction`(후속)로 승격 | `market_data.batch.quarantined` |
| A 캘린더 오류(휴장일 누락) | 세션 내 전 심볼 동시 GAP | 알림(“venue-wide gap”) | `sync_calendar` 정정, 과거 GAP 이슈는 `resolved_reason='CALENDAR_FIX'` | `market_data.calendar.synced` |
| A 시계 드리프트(서버 vs 거래소) | 캔들 `open_time > now + 60s` | TIME_MISALIGNED REJECT | NTP 점검; 거래소 서버시간 오프셋(감사 §7 `get_server_time`) 적용 | — |
| B 체결 재전송(폴링 + 동기 두 경로) | 멱등키 hit | REPLAY 반환(중복 저널 없음) | — | 없음(정상) |
| B 같은 order_id에 다른 체결 내용 | digest mismatch | 거부, CRITICAL | 주문 경로 조사; 정정은 `ADJUSTMENT` 저널로만 | `positions.fill.denied` |
| B 부분체결 누적 | 정상 경로 | fill_seq 증가로 각 부분체결이 별도 저널행 | — | — |
| B 프로세스 재시작 | — | 스냅샷은 DB에 있음. 마크는 다음 주기 | `rebuild_snapshot --all --dry-run` 드리프트 0 확인 | — |
| B 공급자 잔고 불일치 | FND-08 `MATERIAL_MISMATCH` | `pos_recon_break_open` 증가, 60s 내 알림; FND-08 state가 safety_control 연동 | 운영자 `resolve`(FND-08 REC-007: resolve만으로 재개 불가) | FND-08 기존 |
| B 공급자 불명(타임아웃) | `PROVIDER_UNAVAILABLE` | 브레이크로 취급하지 않음, 나이 카운트 시작 | 3회 연속 → 알림 | — |
| B FX 없음 | `POS_FX_RATE_MISSING` | 해당 계좌 미실현·NAV `None`/스킵(0 금지) | FX 소스 복구 후 자동 | — |
| C 감사 append 실패 | 예외 | 포스팅 전체 롤백(AUD-010) | FND-03 복구 | — |
| C 체인 단절/시산표 ≠ 0/드리프트 | `verify_ledger_integrity` | `ledger_control.write_frozen = true`, CRITICAL, 모든 구매 503 | 원인 규명 → 보정 분개(MANUAL_ADJUSTMENT, 2인 승인) → 재검증 통과 → 해동 | `ledger.integrity.failed` / `ledger.control.frozen/unfrozen` |
| C 판매자 잔액 부족한 환불 | R3 경로 | RECEIVABLE 음수 | 다음 판매 캡처 시 `PAYOUT_RELEASE` 전에 RECEIVABLE 상계(리프 L-C13) | `ledger.refund.receivable_created` |
| C 정산 배치 생성 후 송금 실패 | 관리자 `mark_payout_failed` | 배치 FAILED, AVAILABLE 유지(분개 없음) | 재시도 배치 | `ledger.payout.failed` |
| C 두 관리자 동시 충전 확인 | 기존 조건부 UPDATE + 멱등키 `topup:{id}` | 한쪽 REPLAY | — | — |
| C 역할 오설정(앱이 소유자로 접속) | 부팅 시 `SELECT current_user`≠`aios_app` | **프로덕션 모드 기동 거부**(dev는 WARNING) | 접속 문자열 수정 | `system.boot.denied` |
| 네트워크 분리 중 커밋 여부 불명 | asyncpg 예외 | 멱등키로 재시도 안전 | — | — |

---

## 7. 성능·SLO·관측성 (108번)

| 지표 | 목표 | 측정 지점 | 메트릭 |
|---|---|---|---|
| A ingest 지연(소스 응답 → 저장 커밋) | p95 < 2s / 배치 500캔들 | `ingest_candles` | `md_ingest_latency_ms{venue,timeframe}` histogram |
| A 스테일 | 1m: < 180s 세션 중 | 스케줄러 | `md_staleness_seconds{venue,symbol,timeframe}` gauge |
| A 갭 비율(24h) | < 0.5% 세션 내 | `quality_metrics` | `md_gap_ratio_24h{...}` gauge |
| A 품질 이슈 | — | verdict | `md_quality_issues_total{type,severity}` counter, `md_ingest_batches_total{verdict}` |
| A 리플레이 | 1년 1m(≈525k) < 5s, 메모리 < 512MB(스트리밍 커서) | `replay` | `md_replay_seconds` |
| B 저널 append(락 포함) | p95 < 30ms | `record_fill` | `pos_journal_append_ms` |
| B 재빌드 | 10k 저널행 < 1s | `rebuild_snapshot` | `pos_snapshot_rebuild_seconds` |
| B 브레이크 표면화 | 감지 → 알림 < 2분 (대사 주기 60s + 처리) | `reconcile_provider` | `pos_recon_break_open_count`, `pos_recon_break_age_seconds_max`, `pos_recon_runs_total{classification}` |
| B NAV | 세션 마감 +5분 내 | `compute_daily_nav` | `pos_nav_compute_total{outcome}`, `pos_nav_lag_seconds` |
| B 드리프트 | 0 | 일일 검증 | `pos_snapshot_drift_count` |
| C 포스팅 | p95 < 50ms(단일 전역 락이므로 처리량 목표 200 entry/s — Phase 1 규모 10인에는 충분; 초과 시 §10 R5) | `post_entry` | `ledger_post_latency_ms{event_type}`, `ledger_entries_total{event_type}` |
| C 무결성 | 5분 주기 100% 성공 | `verify_integrity` | `ledger_integrity_checks_total{result}`, `ledger_trial_balance_total`(항상 0), `ledger_chain_verified_seq` |
| C 거부 | — | — | `ledger_rejections_total{code}` |
| C 홀드/정산 | — | — | `ledger_holds_open`, `ledger_pending_payout_amount`, `ledger_receivable_amount`, `ledger_payout_backlog_amount` |

로그 필드(JSON Lines, `src/core/logging/schema.py` 확장 — `LogEntry.extra`에 강제): `trace_id`, `tenant_id`, `component`(`market_data|positions|ledger`), `event`, `duration_ms`, 도메인 키(`batch_id`/`position_key`/`entry_id`). secret류 키는 FND-03 `assert_safe_payload` 재사용.

알림 조건: `md_staleness_seconds > 10×duration`(WARN) · 배치 QUARANTINE(WARN) · venue-wide GAP(CRITICAL) · `pos_recon_break_age_seconds_max > 300`(CRITICAL) · `pos_snapshot_drift_count > 0`(CRITICAL) · `ledger_integrity_checks_total{result="fail"}` 증가(CRITICAL + 동결) · `ledger_receivable_amount > 0`(WARN, 일일) · `ledger_post_latency_ms p95 > 200`(WARN).

---

## 8. 테스트 계획

경로: `tests/foundation/unit/<ctx>/`, `tests/foundation/integration/<ctx>/`(실 Postgres, CI 동일), `tests/foundation/adversarial/<ctx>/`. 각 리프는 negative test ≥ 1(§9 DoD에 명시).

### 8.1 단위(순수 규칙)

| 테스트 파일 | 핵심 케이스 |
|---|---|
| `unit/market_data/test_timeframe.py` | `align_open` 경계(정각·D1 UTC), `expected_opens`가 세션 밖 시간을 만들지 않음, 알 수 없는 tf → 예외 |
| `unit/market_data/test_ohlc_sanity.py` | 6개 위반 각각 REJECT, naive datetime REJECT, 정상 0이슈 |
| `unit/market_data/test_gap_detector.py` | KRX 점심 없음·장 마감 후 결측은 갭 아님, 세션 중 결측 2개 → GAP 2, 크립토 24×7 |
| `unit/market_data/test_stale_detector.py` | 세션 밖 스테일 아님, 세션 내 `3×duration+1s` STALE |
| `unit/market_data/test_outlier_detector.py` | 합성 시계열에 +30% 스파이크 1개 → 정확히 그 캔들, 변동성 높은 정상 구간 오탐 0(고정 시드) |
| `unit/market_data/test_dedupe.py` | 동일 중복 → 1건 유지, 상이 중복 → CONFLICT 양쪽 |
| `unit/market_data/test_verdict.py` | REJECT 비율 20% 경계(20.0% ACCEPT/PARTIAL, 20.1% QUARANTINE) |
| `unit/market_data/test_session_rules.py` | KRX 15:30 KST 마감 = 06:30 UTC, DST 전후 US 마감(EST/EDT), 조기폐장, 휴장일 `is_open=False`, 크립토 `next_open == at` |
| `unit/market_data/test_symbol_normalizer.py` | `BTCUSDT↔BTC/USDT`, KRX `005930`, 미지 quote → 예외 |
| `unit/market_data/test_lifecycle.py` | 전이표 전수(허용 5, 거부 나머지 전부) |
| `unit/market_data/test_adjustment.py` | 2:1 분할 ex_date 이전 가격 ÷2·거래량 ×2, 두 분할 누적 곱, ex_date 당일 포함/제외 경계 |
| `unit/market_data/test_lineage.py` | 순서 다른 같은 레코드 → 같은 해시, 한 값 변경 → 다른 해시 |
| `unit/positions/test_fifo.py` | 매수 10@100, 5@110, 매도 12 → 실현 (12: 10@100+2@110), 로트 잔량 3@110; 초과 매도 → `POS_NEGATIVE_QUANTITY`; JSON 왕복 |
| `unit/positions/test_weighted.py` | 평단 재계산, 매도 시 평단 불변 |
| `unit/positions/test_fx.py` | 환율 없음 → 예외(0 아님), 같은 통화 → rate 1 출처 `identity`, 삼각 거부 |
| `unit/positions/test_pnl.py` | multiplier·FX 결합, quantize |
| `unit/positions/test_snapshot_builder.py` | **property**: 임의 체결열(Hypothesis 없이 시드 랜덤 200열)에서 `fold(all) == reduce(apply_one)`; 재빌드 결정론 |
| `unit/positions/test_journal_rules.py` | seq 건너뜀 거부, 체인 재계산 불일치 감지, 변조 1비트 감지 |
| `unit/positions/test_nav.py` | 체인 등식 성립/불성립, `closing = cash + mv` |
| `unit/ledger/test_chart_of_accounts.py` | 코드 파싱·유형·음수허용(RECEIVABLE만 true) |
| `unit/ledger/test_posting_rules.py` | 사건 9종 × Σ차=Σ대, 필수 parties 누락 → 예외, R1/R2/R3 분기 |
| `unit/ledger/test_rounding.py` | `split_commission(10001, 0.15)` 합 = 10001 정확, HALF_EVEN(0.005→0.00, 0.015→0.02) |
| `unit/ledger/test_balance_rules.py` | available 음수 거부(RECEIVABLE 허용), 통화 불일치 거부 |
| `unit/ledger/test_hash_chain.py` | 행 순서 무관 digest, 변조 감지 |
| `unit/ledger/test_hold_state.py` | 전이표 전수 |
| `unit/ledger/test_payout_schedule.py` | 창 미경과 제외, 판매자별 합산, cutoff 경계 |
| `unit/ledger/test_trial_balance.py` | **합계 보존 증명**: 무작위 사건열 1,000건(topup/hold/capture/refund R1~R3/chargeback/payout, 시드 고정) fold → Σ=0 **항상**; 각 사건 후에도 Σ=0 |

### 8.2 통합(실 DB)

| 테스트 파일 | 핵심 케이스 |
|---|---|
| `integration/market_data/test_ingest_candles.py` | 정상 배치 저장·배치행·감사 이벤트 1건; 재실행 저장 0·새 배치행; 충돌 캔들 격리·기존 불변; CHECK 위반 직접 INSERT 실패 |
| `integration/market_data/test_get_candles_as_of.py` | `as_of` 이전 배치만 반환, ADJUSTED 변환, 격리 제외 |
| `integration/market_data/test_replay.py` | 두 번 리플레이 `series_hash` 동일, strict 모드 갭 → `MD_REPLAY_INCOMPLETE` |
| `integration/market_data/test_reference.py` | RENAME 별칭 valid_to, DELIST 후 ingest 거부, 기업행위 멱등 |
| `integration/market_data/test_calendar.py` | yaml 적재, 캘린더 없는 KRX ingest → `MD_CALENDAR_MISSING` |
| `integration/positions/test_record_fill.py` | 부분체결 3건 → seq 1..3, 스냅샷·legacy `positions` 투영 일치; 재전송 REPLAY; digest mismatch 거부; REVOKE·트리거로 UPDATE 실패 |
| `integration/positions/test_rebuild_snapshot.py` | 스냅샷 삭제 후 재빌드 = 원본(drift ∅) |
| `integration/positions/test_mark_and_nav.py` | 마크 스테일 → unrealized None; NAV 멱등, 체인 위반 저장 거부 |
| `integration/positions/test_reconcile_provider.py` | FakeAdapter 잔고 불일치 → FND-08 MATERIAL, 메트릭 증가; 공급자 예외 → PROVIDER_UNAVAILABLE(빈 리스트 아님) |
| `integration/positions/test_legacy_compat.py` | 기존 `report_service`·`portfolio_service`·`risk_guard` 쿼리가 투영 행으로 이전과 같은 결과 |
| `integration/ledger/test_post_entry.py` | 분개+행+잔액+감사 원자성(감사 실패 주입 → 전부 롤백); deferred 트리거로 불균형 직접 INSERT 커밋 실패; `aios_app`으로 UPDATE/DELETE 실패 |
| `integration/ledger/test_purchase_flow.py` | 구매 → HOLD+CAPTURE 2분개, 구매자 available↓ price, 판매자 pending_payout↑ payout, 커미션 수익↑; 잔액 부족 402; 동시 구매 2건 중 1건만 |
| `integration/ledger/test_refund.py` | R1/R2/R3 각각 Σ=0 유지, 이중 환불 거부(`refunded_at`), R3 RECEIVABLE 음수 |
| `integration/ledger/test_payouts.py` | 창 경과 후 RELEASE, PAID 후 CASH_CLEARING 감소, 배치 멱등 |
| `integration/ledger/test_verify_integrity.py` | 정상 리포트; 행 변조(superuser로) → 체인 실패 → `write_frozen=true` → `post_entry` 거부 |
| `integration/ledger/test_backfill.py` | 픽스처 `wallet_transactions`(환불 포함) 백필 → Σ=0, 잔액 전원 일치, 환불 창출분 REFUND_RESERVE |
| `integration/ledger/test_legacy_bridge.py` | 기존 `tests/integration/test_dispute_resolution_service.py`·`test_marketplace_router.py`·`test_wallet_*` 전부 통과(회귀) + `wallet_transactions` 투영 행 계속 생성 |

### 8.3 적대적

| 테스트 파일 | 케이스 |
|---|---|
| `adversarial/market_data/test_cross_tenant.py` | tenant A의 배치 조회를 B가 시도 → 존재 자체 비노출(404 동형) |
| `adversarial/market_data/test_tamper.py` | 저장 캔들 superuser UPDATE 후 `replay.series_hash` 변화 + 배치 해시 재검증 실패 감지 |
| `adversarial/positions/test_race_fills.py` | 같은 position_key에 동시 체결 20건(asyncio.gather) → seq 1..20 빈틈·중복 없음 |
| `adversarial/positions/test_cross_tenant.py` | 다른 tenant의 position_key로 record_fill → 거부 |
| `adversarial/ledger/test_race_purchase.py` | 잔액 10,000에 9,000짜리 구매 5건 동시 → 정확히 1건 성공, Σ=0 |
| `adversarial/ledger/test_negative_and_zero.py` | amount ≤ 0, 음수 price 리스팅, extra에 `api_key` 키 → 전부 거부 |
| `adversarial/ledger/test_replay_attack.py` | 같은 event_ref 다른 금액 → 409 + 감사 DENIED 이벤트 존재 |
| `adversarial/ledger/test_role_bypass.py` | `aios_app`으로 `ALTER TABLE DISABLE TRIGGER` 시도 → 권한 오류 |

### 8.4 계약·성능

- `tests/foundation/unit/{market_data,positions,ledger}/test_contracts_schema.py`: `model_json_schema()` 스냅샷을 `tests/contracts/snapshots/*.json`과 비교(107번 — 필드 제거 시 실패).
- `tests/foundation/integration/**/test_perf_*.py`(`pytest-benchmark`, 단언 포함 — 감사 §9 "벤치마크에 단언 없음" 반복 금지): 리플레이 525k행 < 5s, 저널 append p95 < 30ms(100회), 포스팅 p95 < 50ms(200회), 무결성 검증 10k 분개 < 3s.
- `tests/unit/test_zone_purity.py`: `src/foundation/*/domain/**`가 `asyncpg|httpx|sqlalchemy`를 import하지 않음(AST 검사).

---

## 9. 리프 목록 (구현 순서)

DoD 공통: `ruff check . && mypy --strict <paths>` 통과, 명시된 pytest 명령 통과, 커버리지(`pytest --cov=<pkg>`) 리프 파일 ≥ 90%. 마이그레이션 리프는 `alembic upgrade head && alembic downgrade -1 && alembic upgrade head` 왕복.

### 9.1 공통 기반

| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| L0-1 | `src/core/observability/metrics_registry.py` + `tests/unit/core/test_metrics_registry.py` | — | counter/gauge/histogram, `render_text()` Prometheus 형식 파싱 가능; negative: 라벨 키 불일치 → `ValueError` | 200 |
| L0-2 | `tests/unit/test_zone_purity.py` + ruff `banned-api` 설정(pyproject) | — | domain에 I/O import 시 실패 재현 | 60 |
| L0-3 | `src/core/db/append_only.py`, `src/core/db/roles.py` + `tests/unit/core/test_worm_sql.py` | — | 생성 SQL 스냅샷; negative: 테이블명에 `;` → 거부 | 200 |
| L0-4 | `src/foundation/evidence/adapters/postgres_repository.py` **기존-수정**: `append_event_in(conn, ...)` 추가(기존 `append_event`는 이를 호출) + `tests/foundation/integration/evidence/test_append_in_conn.py` | — | 외부 트랜잭션 롤백 시 이벤트도 사라짐 | +40 |
| L0-5 | `src/db/migrations/versions/4a1d0c0de001_db_roles_and_worm_helper.py` + `src/api/routers/metrics.py` + `tests/integration/test_db_roles.py` | L0-1, L0-3 | 역할 존재, `aios_app`으로 `audit_log` UPDATE 실패; `/metrics` 200 | 180 |

### 9.2 (A) 시장데이터

| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| LA-1 | `market_data/contracts/v1.py` + `test_contracts_schema.py` | — | 스냅샷 생성, naive datetime → ValidationError | 280 |
| LA-2 | `domain/timeframe.py` + test | LA-1 | §8.1 | 120 |
| LA-3 | `domain/calendar/session_rules.py`, `domain/calendar/known_venues.py` + test | LA-1 | DST·조기폐장·휴장 케이스 | 260 |
| LA-4 | `domain/quality/ohlc_sanity.py`, `dedupe.py` + tests | LA-1 | §8.1 | 180 |
| LA-5 | `domain/quality/gap_detector.py`, `stale_detector.py` + tests | LA-2, LA-3 | §8.1 | 180 |
| LA-6 | `domain/quality/outlier_detector.py`, `verdict.py` + tests | LA-1 | 오탐 0 시드 고정, 20% 경계 | 210 |
| LA-7 | `domain/reference/symbol_normalizer.py`, `lifecycle.py` + tests | LA-1 | 전이표 전수 | 200 |
| LA-8 | `domain/corporate_actions/adjustment.py`, `domain/lineage.py` + tests | LA-1 | 누적 분할, 해시 순서 무관 | 200 |
| LA-9 | `ports/*.py` 5파일 | LA-1 | mypy 통과(Protocol) | 330 |
| LA-10 | 마이그레이션 `4a1d0c0de002_md_reference_registry.py`: `md_instrument`(UNIQUE(venue, canonical_symbol, listed_at), status CHECK), `md_symbol_alias`(UNIQUE(venue, alias_symbol, valid_from), `EXCLUDE USING gist` 기간 중복 금지 — btree_gist 확장), `md_corporate_action`(UNIQUE(instrument_id, action_type, ex_date), ratio > 0), `md_venue_calendar_day`(UNIQUE(venue, trade_date), CHECK(is_trading_day = (open_at IS NOT NULL))) + `tests/integration/test_db_schema.py` 확장 | L0-5 | 왕복, 제약 각각 위반 INSERT 실패 | 160 |
| LA-11 | 마이그레이션 `4a1d0c0de003_md_candles.py`: `md_candle` **RANGE 파티션(open_time, 월)** PK(venue, instrument_id, timeframe, open_time), CHECK 6종(§4.1), `quality_flags SMALLINT`, `batch_id UUID NOT NULL`; `md_quarantine_candle`(동일 컬럼 + `issue_type`, PK에 batch_id 포함); `md_tick` 파티션(traded_at, 월) UNIQUE(venue, instrument_id, trade_id); `md_ingest_batch`(id, tenant_id NULL, source, venue, instrument_id, timeframe, range_start, range_end, request_fingerprint, batch_hash, record_count, verdict CHECK, audit_event_id FK, created_at); `md_quality_issue`(batch_id FK, type, severity, open_time, detail JSONB); WORM: `md_candle`·`md_ingest_batch`·`md_quality_issue`에 `worm_sql`; 파티션 생성 함수 `md_ensure_partitions(months_ahead int)` | LA-10 | 왕복, CHECK 위반 실패, 파티션 자동 생성 | 260 |
| LA-12 | `adapters/postgres_reference_repository.py`, `adapters/postgres_calendar_repository.py`, `adapters/yaml_calendar_source.py` + `config/market_calendars/KRX_2026.yaml`(**미확인 표기**, §10) + integration tests | LA-9, LA-10 | RENAME 별칭, 캘린더 적재 | 320 |
| LA-13 | `adapters/postgres_candle_store.py`, `adapters/postgres_batch_repository.py` + integration test | LA-11 | ON CONFLICT DO NOTHING, 격리, as_of 조회 | 400 |
| LA-14 | `application/register_instrument.py`, `record_corporate_action.py`, `sync_calendar.py` + tests | LA-12, L0-4 | 감사 이벤트 1:1, DELIST 거부 케이스 | 330 |
| LA-15 | `application/ingest_candles.py`, `adapters/bitget_ingest_source.py` + `tests/foundation/integration/market_data/test_ingest_candles.py` | LA-4~8, LA-13, LA-14 | §8.2; 감사 실패 주입 → 저장 롤백 | 320 |
| LA-16 | `application/ingest_ticks.py` + test | LA-15 | trade_id 역행 REJECT | 200 |
| LA-17 | `application/get_candles.py`, `replay_candles.py` + tests | LA-13, LA-8 | 해시 결정론, strict 갭 | 240 |
| LA-18 | `application/quality_metrics.py` + `scheduler.py` + `main.py` 배선(백그라운드 태스크, `execution_loop/scheduler.py` 패턴) + test | LA-15, L0-1 | 스케줄러 1주기 후 게이지 존재, 심볼 1개 실패가 나머지 차단 안 함 | 300 |
| LA-19 | `src/exchanges/{bitget,kis}/market_data_mixin.py` 심볼 변환 → normalizer 위임 + 기존 테스트 통과 | LA-7 | `get_positions` 심볼이 canonical(감사 §7 해소) | +30 |
| LA-20 | `adapters/kis_ingest_source.py` + test(MockTransport) | LA-15 | KRX 세션 갭 판정 통합 | 120 |
| LA-21 | adversarial 2파일 + perf 리플레이 | LA-17 | §8.3/8.4 | 200 |

### 9.3 (B) 포지션 & PnL

| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| LB-1 | `positions/contracts/v1.py` + schema test | — | 스냅샷 | 260 |
| LB-2 | `domain/position_key.py`, `domain/cost_basis/fifo.py` + tests | LB-1 | §8.1 FIFO | 210 |
| LB-3 | `domain/cost_basis/weighted.py`, `selector.py` + tests | LB-2 | 파생상품 → WEIGHTED 강제 | 140 |
| LB-4 | `domain/fx.py`, `domain/pnl.py`, `domain/funding_fees.py` + tests | LB-1 | 환율 없음 → 예외 | 250 |
| LB-5 | `domain/journal_rules.py`, `domain/snapshot_builder.py` + tests(property) | LB-2~4 | fold == reduce(apply_one) 200열 | 300 |
| LB-6 | `domain/nav.py`, `domain/reconciliation_rules.py` + tests | LB-4 | 체인 등식 | 210 |
| LB-7 | `ports/*.py` 6파일 | LB-1 | mypy | 300 |
| LB-8 | 마이그레이션 `4a1d0c0de004_positions_journal.py`: `pos_account`(PK, tenant_id FK users, venue, connection_id FK account_connection NULL, base_currency, cost_method CHECK, UNIQUE(tenant_id, venue, connection_id)); `pos_journal`(BIGSERIAL, tenant_id, account_id FK, position_key VARCHAR(200), sequence_no, entry_type CHECK, qty_delta, price, price_ccy, fee, fee_ccy, realized_pnl_base, fx_rate, fx_source, source_event_type, source_event_id, idempotency_key UNIQUE, digest, prev_hash, entry_hash, occurred_at, recorded_at DEFAULT now(); UNIQUE(position_key, sequence_no); CHECK(sequence_no ≥ 1); WORM); `pos_snapshot`(position_key PK, tenant_id, account_id, instrument_id, quantity CHECK(≥0 OR asset_class 파생), avg_cost, cost_method, lots JSONB, realized_pnl_base, unrealized_pnl_base NULL, fees_base, funding_base, mark_price NULL, mark_at NULL, last_journal_seq, legacy_position_id BIGINT FK positions(id) NULL, updated_at); `pos_nav_daily`(UNIQUE(account_id, nav_date), CHECK(closing_nav = cash + positions_mv), WORM) | L0-5 | 왕복, 제약 위반 실패 | 260 |
| LB-9 | `adapters/postgres_journal_repository.py`, `postgres_snapshot_repository.py`, `postgres_nav_repository.py` + integration tests | LB-7, LB-8 | advisory lock 동시 20건 seq 연속; 조건부 upsert 충돌 | 400 |
| LB-10 | `adapters/legacy_positions_projection.py` + `test_legacy_compat.py` | LB-9 | 기존 3개 서비스 쿼리 결과 동일 | 200 |
| LB-11 | `application/record_fill.py` + integration test | LB-5, LB-9, LB-10, L0-4 | §8.2 record_fill 전 케이스 | 260 |
| LB-12 | `src/services/order_service/position_ledger.py` **수정**(위임) + 기존 `tests/integration/test_execution_tick.py`·`test_executor.py` 통과 + 새 테스트 부분청산 | LB-11 | Phase 1 가정 제거: BUY 2회 후 SELL 1회 부분청산 정확 | ≤116 |
| LB-13 | `application/record_funding_fee.py`, `rebuild_snapshot.py` + tests | LB-11 | 재빌드 drift ∅ | 230 |
| LB-14 | `adapters/candle_mark_price_source.py`, `fx_rate_source.py`, `application/mark_positions.py` + tests | LA-17, LB-11 | 스테일 → None | 300 |
| LB-15 | `application/compute_daily_nav.py` + test | LB-6, LB-14, LA-3 | 멱등, 체인 위반 거부 | 220 |
| LB-16 | `adapters/exchange_balance_source.py`, `application/reconcile_provider.py` + test(FakeAdapter, FND-08 실호출) | LB-14, FND-08 | MATERIAL → 메트릭, 예외 전파 | 230 |
| LB-17 | `application/queries.py`, `scheduler.py` + `main.py` 배선 + test | LB-14~16 | 스케줄 1주기 | 280 |
| LB-18 | adversarial 2파일 + perf | LB-11 | §8.3 | 180 |

### 9.4 (C) 머니 원장

| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| LC-1 | `ledger/contracts/v1.py` + schema test | — | 스냅샷; amount ≤ 0 ValidationError | 280 |
| LC-2 | `domain/chart_of_accounts.py`, `domain/rounding.py` + tests; `src/services/commission.py` 위임 + `tests/unit/test_commission.py` 갱신 | LC-1 | 합 보존 반올림 | 210 |
| LC-3 | `domain/balance_rules.py`, `domain/hash_chain.py`, `domain/idempotency.py` + tests | LC-1 | 변조 감지, 음수 거부 | 250 |
| LC-4 | `domain/posting_rules.py` + `test_posting_rules.py` | LC-2, LC-3 | 사건 9종 Σ=0 | 300 |
| LC-5 | `domain/hold_state.py`, `domain/payout_schedule.py`, `domain/trial_balance.py` + tests(**합계 보존 1,000 사건**) | LC-4 | Σ=0 항상 | 330 |
| LC-6 | 마이그레이션 `4a1d0c0de005_ledger_core.py`: `ledger_account`(account_id UUID PK, tenant_id NULL, account_code UNIQUE, account_type CHECK, currency, allow_negative BOOL); `ledger_journal_entry`(entry_id UUID PK, sequence_no BIGINT UNIQUE CHECK(≥1), event_type CHECK, event_ref, idempotency_key UNIQUE, lines_digest, prev_hash, entry_hash, audit_event_id UUID NOT NULL FK foundation_audit_event, posted_by, posted_at); `ledger_posting_line`(line_id BIGSERIAL, entry_id FK, line_no, account_id FK, side CHECK, amount NUMERIC(20,2) CHECK(>0), currency, UNIQUE(entry_id, line_no)); **deferred constraint trigger** `ledger_entry_balanced()`(entry별 Σ차=Σ대, 통화 단일 아니면 RAISE); `ledger_balance`(account_id PK FK, balance, held, pending_payout, allow_negative, last_entry_seq, updated_at, CHECK(held ≥ 0), CHECK(pending_payout ≥ 0), CHECK(allow_negative OR balance - held ≥ 0)); `ledger_control`(id=1 CHECK, write_frozen BOOL, frozen_reason, frozen_at, unfrozen_by); WORM(entry, line); 플랫폼 계정 4개 + house 사용자 계정 시드 | L0-5 | 왕복; 불균형 커밋 실패; `aios_app` UPDATE 실패 | 280 |
| LC-7 | 마이그레이션 `4a1d0c0de006_ledger_holds_payouts.py`: `ledger_hold`(UNIQUE(purpose, reference), state CHECK, expires_at, entry_id FK, settled_entry_id FK NULL), `ledger_payout_batch`(UNIQUE(seller_user_id, period_end), state CHECK), `ledger_payout_item`(batch_id FK, capture_entry_id FK UNIQUE), `ledger_integrity_check`(checked_at, result, report JSONB, WORM) | LC-6 | 왕복 | 160 |
| LC-8 | `ports/*.py` 4파일 + `adapters/postgres_journal_repository.py`, `postgres_balance_repository.py` + integration tests | LC-6 | 전역 락 동시 50건 seq 연속 | 480 (2커밋: ports / adapters) |
| LC-9 | `application/post_entry.py` + `test_post_entry.py` | LC-4, LC-8, L0-4 | 원자성·동결·REPLAY·DIGEST_MISMATCH(DENIED 감사) | 300 |
| LC-10 | `application/verify_integrity.py` + `scheduler.py`(무결성 부분) + test | LC-9 | 변조 → 동결 → 포스팅 거부 | 260 |
| LC-11 | `scripts/ledger_backfill.py` + `application/backfill.py` + `test_backfill.py` | LC-9 | 픽스처 Σ=0·잔액 일치, 불일치 시 롤백 | 260 |
| LC-12 | `adapters/legacy_wallet_bridge.py` + `src/services/wallet_service.py` **수정** + `application/topup.py` + 기존 지갑 테스트 전부 통과 | LC-11 | 브리지 후 `wallet_transactions` 투영 계속, 잔액 = ledger | 320 |
| LC-13 | `adapters/postgres_hold_repository.py`, `application/purchase_flow.py` + `src/services/purchase_service.py` **수정** + `test_purchase_flow.py` + 기존 `test_marketplace_router.py` 통과 | LC-12 | 동시 5건 중 1건, 무료 리스팅 분개 0 | 400 |
| LC-14 | `application/refund.py` + `src/services/dispute_resolution_service.py` **수정** + `test_refund.py` + 기존 분쟁 테스트 통과 | LC-13 | R1/R2/R3 Σ=0, 이중 환불 거부 | 300 |
| LC-15 | `adapters/postgres_payout_repository.py`, `application/payouts.py`, `application/chargeback.py` + RECEIVABLE 상계 + tests + 관리자 라우터 `POST /admin/ledger/payouts/{id}/paid` | LC-13 | §8.2 payouts | 420 (2커밋) |
| LC-16 | `application/queries.py` + `src/api/routers/wallet.py` **수정**(`GET /wallet`이 available/held/pending_payout 반환, 기존 `balance` 필드 유지) + `scheduler.py` 정산 부분 + `main.py` 배선 | LC-15 | 프론트 무변경으로 통과 | 300 |
| LC-17 | adversarial 4파일 + perf | LC-14 | §8.3 | 260 |

총 리프 61개. 임계 경로: L0-* → LC-1..10(원장 무결성) → LB-8..12(positions 쓰기 경로 교체) → LA-13..18(데이터 공급). C를 먼저 두는 이유: 감사 §2 "돈이 새는 결함"의 남은 하나(환불 창출, §1.1 C2)가 지금 프로덕션 경로에 있기 때문이다.

---

## 10. 미확정·리스크

| # | 항목 | 상태 | 조치 |
|---|---|---|---|
| R1 | **현재 환불이 자금을 창출**(§1.1 C2) — 지금 코드로 `DELISTED_AND_REFUND` 1건마다 시스템 총잔액이 `price_paid`만큼 증가 | 코드로 확인(`dispute_resolution_service.py:125-142`, `purchase_service.py:143-159`) | LC-14 전까지 임시: 분쟁 환불 시 판매자 `SALE_CREDIT` 환수 부재를 운영 공지. FULL_AUDIT §2-A에 항목 추가 요청(이 문서는 감사 문서를 수정하지 않는다) |
| R2 | 판매대금 즉시 정산(ADR-2026-08-29 §1) → 홀드 창 후 정산으로 변경 | **ADR 개정 필요** | `ADR-2026-09-xx-ledger-escrow-payout-window.md` 초안: 창 길이 Draft 7일, 무료 리스팅 무관, 기존 판매자 잔액은 백필 시 AVAILABLE 유지(소급 없음) |
| R3 | 커미션 반올림 도입(현재 `price × 0.15` 무반올림 → 2dp 저장 시 DB가 반올림하고 합이 어긋날 수 있음) | 코드 확인(`commission.py` NUMERIC(20,2) 컬럼) | LC-2에서 HALF_EVEN, 백필 시 과거 분개는 `wallet_transactions`의 실제 값 그대로 |
| R4 | KRX·US 휴장일·조기폐장 목록 | **미확인** — 공식 소스(KRX 휴장일 공시, NYSE holiday calendar) 대조 전 yaml은 placeholder | LA-12 yaml에 `source: UNVERIFIED` 표기, 검증 전 KRX ingest는 dev 전용 |
| R5 | C 전역 단일 체인 락 처리량(≈200 entry/s) | Phase 1 10인 규모에 충분, 마켓플레이스 확장 시 병목 | 필요 시 tenant별 체인 + 플랫폼 계정 별도 체인으로 분할(107번 v2) |
| R6 | 파생상품 공매도·숏 포지션(수량 음수) | 이 명세는 현물만 음수 금지. `KR_FUTURES`/`OVERSEAS_FUTURES`는 `pos_snapshot` CHECK에서 예외 | Bitget 선물 실체결 확인 전까지 숏 저널은 테스트만 |
| R7 | FX 소스(USDT/KRW) — Bitget·KIS 참조 시세 중앙값은 Draft | 기관 기준(예: 서울외국환중개 매매기준율) 미채택 | 보고서용 FX는 출처를 NAV 행에 기록하므로 후속 교체 가능 |
| R8 | Bitget 캔들 API `limit` 최대치·페이지네이션·서버시간 오프셋 | **미확인**(감사 §7) | LA-15 소스 어댑터에 최대 200으로 보수 설정, 실측 후 조정 |
| R9 | 역할 분리 인프라(접속 문자열 2종, CI Postgres 롤 생성) | CI 워크플로 수정 필요(`.github/**` OPEN Zone) | L0-5에서 CI에 `aios_app` 접속 테스트 추가 |
| R10 | `positions` legacy 테이블 UNIQUE(symbol, exchange, strategy_id, entry_time)가 투영과 충돌 가능(같은 초 재진입) | 코드 확인 | 투영은 `legacy_position_id`로 1:1 고정, 재진입은 새 스냅샷=새 행 |
| R11 | `md_candle_correction`(운영자 정정 절차)·`SUBSCRIPTION` 가격모델·NAV 기반 보고서 전환·`user_wallets` 읽기 경로 교체 | 스콥 밖(후속 L4) | 별도 명세 |
| R12 | 캔들 보존: 1m 2년·≥1h 무기한·틱 90일, 파티션 DROP은 운영자 승인 커밋 | Draft | `scripts/md_retention.py`(후속), 감사 이벤트 필수 |
