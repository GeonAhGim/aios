# L4 — 실행 OMS·거래소 어댑터 구현 명세 v1.0

> 템플릿: `docs/specs/_TEMPLATE.md`. 한 세션이 리프 하나(파일 하나, ≤300줄)씩
> 바로 구현할 수 있는 단위로 쪼갠다. "기초 수준"이 아니라 자산운용사가 실제
> 운용에 쓰는 OMS 요구를 기준으로 쓴다.

## 0. 문서 메타

| 항목 | 값 |
|---|---|
| status | DRAFT → 리프 L4-01 착수 시 ACTIVE |
| owner role | Execution/OMS 엔지니어(PM: agent-platform-12 승인, FROZEN_PAPER_ONLY 변경은 PM 사전 승인) |
| supersedes | `src/services/order_service/submit.py`의 claim-then-send-else-delete 흐름(§5.3), `Executor.execute()`의 타임스탬프 기반 `client_order_id`(§5.2), `_BitgetHTTPClient._request()`의 "미지 코드=Retryable" 분류(§3.4) |
| depends on | `02_exchange_adapter_v1.3.md` §2.0-A/§2.1 · `02b_bitget_api_v2_full_spec_v1.md` §3.2/§6/§7 · `02e_nh_api_spec_v1.md` §2~§4 · `ADR-2026-08-10-C`(execution_id) · `docs/ADR-2026-08-29-E`(LIVE 하드 가드) · `80_reconciliation_resilience_l3` §1~§4 · `105_concurrency_and_atomicity` §2~§5 · `107_contract_versioning` §3 · `108_structured_logging` §2~§4 · `기능설계문서_v1.21.md` FD-4.1~4.5, FD-8.4 · `docs/FULL_AUDIT_2026-09-02.md` §2, §3, §5, §7, §11 |
| implemented by | §2 표의 파일 경로 전체(기존 11개 수정 + 신규 41개) |
| verification evidence | `tests/unit/oms/**`, `tests/unit/exchanges/common/**`, `tests/integration/oms/**`, `tests/adversarial/oms/**`, `tests/contract/oms/**`, `tests/perf/oms/**`, `tests/e2e/bitget_demo/**`(키 확보 후) |
| Zone | 신규 `src/services/oms/**`, `src/exchanges/**` = SCAFFOLD. `src/core/executor/**` = FROZEN_PAPER_ONLY(리프 L4-10 한 곳만 수정). `tests/**`, `docs/**` = OPEN |

---

## 1. 기관급 요구 (왜 기초 수준으로는 부족한가)

자산운용사 OMS가 실행 계층에 요구하는 것과, 감사(`FULL_AUDIT_2026-09-02.md`)가
확인한 현재 코드의 격차를 항목별로 적는다.

| # | 기관 요구 | 근거(규제·감사·운용) | 현재 코드(감사 인용) | 격차 |
|---|---|---|---|---|
| R1 | **주문 유실·중복 0** — 재시도·크래시·중복 전달 어떤 조합에서도 거래소에 나간 주문 수 = DB가 아는 주문 수 | 8.3 Order State Machine, FD-4.2-a, 105번 §5(in-flight) | `submit.py`: claim→send→실패 시 `repository.delete()` — 전송이 실제로 나갔는데 응답만 유실되면 DB에 흔적이 사라진다(고아 주문). `Executor.execute()`: `client_order_id`에 `datetime.now()`가 들어가 재시도마다 새 키(§5 "재시도마다 새 키 생성") | 안정적 client id + outbox + UNKNOWN 상태 보존 |
| R2 | **모든 상태 변화가 감사 가능** — 누가/무엇이/언제/어떤 근거로 상태를 바꿨는지 append-only | 79번 감사 체인, 108번 §4 | `orders.status`만 덮어쓰고 이력 없음. `append_audit_event` 호출 0(§3) | `order_events` 테이블 + 감사 체인 연결 |
| R3 | **상태 전이가 DB 차원에서 강제** — 코드 버그·다른 세션의 손 UPDATE로도 FILLED→SUBMITTED 같은 역전이 불가 | 105번 §2, 04번 | `conditional_update`로 코드 레벨만. DB 트리거·CHECK 없음 | 트리거 `oms_enforce_order_transition` |
| R4 | **멱등성 스코프가 전역이 아님** — tenant/account/provider/strategy/version/time-window 단위 | 감사 §2 "구매 멱등키가 전역"과 같은 클래스 | `client_order_id` UNIQUE 하나뿐, 스코프 없음. KIS/NH는 client id 개념 자체가 없어 멱등 키를 전달할 수 없다(`kis/adapter.py` docstring) | `order_idempotency` 테이블 + venue별 id 정책 |
| R5 | **거래소 어댑터 내구성** — HTTP 상태코드, 429/5xx 백오프+지터, `Retry-After`, 서버시간 오프셋, 타임아웃 예산, 회로차단 | 감사 §7, §11 7단계 | "HTTP 상태코드를 한 번도 읽지 않는다", 미지 오류코드 일괄 Retryable(잔고 부족까지 재시도), `get_server_time` 미사용, 429·타임아웃 테스트 0건 | §2-D 8개 모듈 |
| R6 | **WebSocket 지속성** — 하트비트, ack 검증, 시퀀스 갭, 재구독, 스냅샷 재동기화 | 감사 §7 | ping/pong 없음(Bitget 30초 요구), ack 코드 미검사, 재연결 후 REST 재동기화 없음 | `ws_session.py` |
| R7 | **체결 정규화 정밀도** — Decimal, tick/lot 라운딩, 부분체결 누적 평균가·수수료 | 11번 §11.1 Money | `priceAvg` 파싱은 최근 복구(`67f23d0`)됐으나 fills 원시 dict 그대로, 수수료 미기록, lot/tick 라운딩 없음 | `rounding.py`, `fill_normalizer.py`, `fills` 테이블 |
| R8 | **심볼 정규화 단일 원천** | 감사 §7 "주문 시 BTC/USDT, 조회 시 BTCUSDT" | 각 믹스인이 `replace("/", "")`를 손으로 | `symbol_registry.py` |
| R9 | **3자 대사** — 내부 주문 vs 거래소 주문 vs 거래소 체결·잔고, T+0 N분 이내, 중대 불일치 → 정지 | 80번 §1/§2, REC-001~010 | `foundation/reconciliation`은 입력을 호출자가 조립해야 하고 호출자 0(§3). 내부/외부 원장을 실제로 읽는 코드 없음 | `three_way_reconciler.py` |
| R10 | **UNKNOWN 해소·재시작 복구** — 응답 유실 주문을 실패로 단정하지 않고 clientOid 역조회 → 상한 초과 시 스코프 정지 | FD-4.5, 8.3 | `reconcile.py` 3회 재조회 후 CRITICAL 로그만. 정지 연동 없음. `recovery_wiring.py`는 outbox 개념이 없어 "전송 중 크래시" 케이스를 복구 못 함 | `unknown_resolver.py`, `restart_recovery.py` |
| R11 | **PAPER 격리를 헤더 하나에 의존하지 않음** | 감사 §2 P0 "PAPER 격리가 미검증 헤더 하나" | `paptrading: 1`이 스팟에서 유효한지 미확정. 시뮬레이터 부재 | `src/exchanges/paper/**` 시뮬레이터 어댑터 |
| R12 | **LIVE 하드 가드 유지** | ADR-2026-08-29-E | `Executor` 이중 가드 존재(감사 §5 "완전") | 유지. 이 명세의 어떤 리프도 가드를 약화하지 않는다(§4.3) |
| R13 | **실행 알고리즘** — TWAP(Phase 1), VWAP/POV/iceberg는 정의만 — 참여율 상한, 슬라이스 무작위화(anti-front-running) | 06번 §6.1(TWAP 포함, VWAP/Iceberg 제외), 8.3-A, 8.6-A-2 | 없음(감사: Iceberg 구현은 스콥 이탈 — 이 명세는 순수 계획기만 두고 실행은 TWAP만 배선) | `algo_slicer.py`(순수) + `algo_executor.py`(TWAP만 활성) |
| R14 | **다거래소 capability gating** — KIS/NH/Bitget이 지원하지 않는 주문 유형·정정·client id를 명시적으로 거부 | 02번 §2.0-A | `ExchangeCapability`에 주문 유형/TIF/정정 가능 여부/id 정책 없음 | `VenueCapabilityProfile` |
| R15 | **성능·복구 목표 수치화** — p99 제출 경로, RPO/RTO | 108번 §3 | 관측성 0(감사 §5) | §7 |

---

## 2. 모듈 분해 (최소단위)

표기: **[기존]** 수정, **[신규]** 생성. 줄수 상한은 모두 300. 도메인(`domain/`)은
외부 I/O 없음. Zone은 §0 참조(별도 표기 없으면 SCAFFOLD).

### 2-A. OMS 도메인 (순수, I/O 없음) — `src/services/oms/domain/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| [신규] `src/services/oms/domain/state_machine.py` | 주문 상태 전이표 + 전이 판정 | `class OrderEvent(str, Enum)`; `ALLOWED: Mapping[OrderStatus, frozenset[OrderStatus]]`; `def next_status(current: OrderStatus, event: OrderEvent, *, filled_qty: Decimal, qty: Decimal) -> OrderStatus`; `def is_terminal(s) -> bool`; `class InvalidOrderTransitionError(MihwaError)` | 없음 | 160 | SCAFFOLD |
| [신규] `src/services/oms/domain/idempotency.py` | 멱등 스코프·digest·안정적 client id 생성 | `def build_scope(*, tenant_id: UUID, account_ref: str, provider: str, strategy_id: str, strategy_version: str, execution_id: int, intent_seq: int, window_start: datetime) -> IdempotencyScope`; `def scope_hash(scope) -> str`(sha256 hex 64); `def command_digest(cmd: SubmitOrderCommand) -> str`; `def client_order_id(scope, *, max_len: int, charset: str) -> str`(결정론, 같은 입력→같은 출력) | 없음 | 120 | SCAFFOLD |
| [신규] `src/services/oms/domain/rounding.py` | tick/lot/min-notional 라운딩 | `def round_price(price: Decimal, tick: Decimal, side: OrderSide) -> Decimal`(BUY=ROUND_DOWN, SELL=ROUND_UP — 불리한 방향 금지); `def round_qty(qty: Decimal, lot: Decimal) -> Decimal`(항상 ROUND_DOWN); `def check_notional(price, qty, min_notional) -> None`(위반 시 `OrderValidationError("MIN_NOTIONAL")`) | 없음 | 90 | SCAFFOLD |
| [신규] `src/services/oms/domain/fill_normalizer.py` | 거래소 원시 체결 → `FillEvent`, 누적 집계 | `def normalize_fill(raw: dict, *, venue: str, profile: VenueCapabilityProfile, registry: SymbolRegistry) -> FillEvent`; `def aggregate(fills: Sequence[FillEvent]) -> FillAggregate`(filled_qty, avg_price=Σ(p·q)/Σq quantize to tick, fee_total by currency) | `venue_profile`, `symbol_registry` | 180 | SCAFFOLD |
| [신규] `src/services/oms/domain/symbol_registry.py` | 정규 심볼 ↔ 거래소 심볼 단일 원천 | `class SymbolRegistry`; `def to_venue(self, canonical: str, venue: str) -> str`; `def to_canonical(self, venue_symbol: str, venue: str) -> str`; `def register(self, canonical, venue, venue_symbol, *, tick, lot, min_notional, quote_ccy)`; 미등록 → `UnknownSymbolError`(fail-closed) | 없음 | 150 | SCAFFOLD |
| [신규] `src/services/oms/domain/venue_profile.py` | 거래소 capability 프로파일 모델 | `class VenueCapabilityProfile(BaseModel)`(§3.2); `def assert_supported(profile, cmd: SubmitOrderCommand) -> None`(미지원 → `UnsupportedVenueFeatureError(code)`) | 없음 | 120 | SCAFFOLD |
| [신규] `src/services/oms/domain/algo_slicer.py` | TWAP/VWAP/POV/iceberg 슬라이스 계획(순수) | `def plan_slices(req: AlgoRequest, *, now: datetime, volume_profile: Sequence[Decimal] \| None, rng: random.Random) -> list[SlicePlan]`; 참여율 상한 `req.max_participation_pct`, 크기 지터 `±req.size_jitter_pct`, 시간 지터 `±req.time_jitter_pct`, 마지막 슬라이스에 잔량 흡수(Σ = parent qty 정확히) | `rounding` | 220 | SCAFFOLD |
| [신규] `src/services/oms/domain/reconcile_rules.py` | 3자 대사 비교 규칙 | `def compare_triple(internal: Sequence[OrderView], provider_orders: Sequence[OrderView], provider_fills: Sequence[FillEvent], balances: Mapping[str, Decimal], ledger_balances: Mapping[str, Decimal], policy: MaterialityPolicy) -> list[Discrepancy]`; `def classify(d: Discrepancy) -> Classification`(80번 7분류 재사용) | `foundation.reconciliation.domain` | 200 | SCAFFOLD |
| [신규] `src/services/oms/domain/errors.py` | OMS 에러 taxonomy(§3.4) | `class OmsError(MihwaError)`, 코드 상수 `OMS_*` | 없음 | 80 | SCAFFOLD |

### 2-B. 계약 — `src/services/oms/contracts/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| [신규] `src/services/oms/contracts/v1_commands.py` | 명령 DTO | `SubmitOrderCommand`, `CancelOrderCommand`, `ModifyOrderCommand`, `AlgoRequest`, `IdempotencyScope` (§3.1) | pydantic | 200 | SCAFFOLD |
| [신규] `src/services/oms/contracts/v1_events.py` | 이벤트 DTO | `ProviderOrderEvent`, `FillEvent`, `FillAggregate`, `OrderTransitionEvent`, `Discrepancy` (§3.1) | pydantic | 200 | SCAFFOLD |
| [신규] `src/services/oms/contracts/v1_views.py` | 읽기 모델 | `OrderView`, `OrderTimelineView`, `AlgoRunView`, `ReconcileSummaryView` | pydantic | 140 | SCAFFOLD |

### 2-C. OMS 어댑터·애플리케이션 — `src/services/oms/adapters/`, `application/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 | Zone |
|---|---|---|---|---|---|
| [신규] `src/services/oms/ports/repository.py` | Protocol 5개(OrderRepo, OrderEventRepo, FillRepo, OutboxRepo, InboxRepo, IdempotencyRepo) | 아래 어댑터의 시그니처를 Protocol로 | 없음 | 180 | SCAFFOLD |
| [신규] `src/services/oms/adapters/order_repository.py` | orders 행 전이(조건부 UPDATE + version 증가) | `async def transition(conn, *, order_id: UUID, expected_status: OrderStatus, expected_version: int, new_status: OrderStatus, patch: dict, event: OrderTransitionEvent) -> OrderView`(같은 tx에서 `order_events` INSERT) ; `async def get_for_update(conn, order_id) -> OrderView`; `async def find_by_scope_hash(conn, scope_hash) -> OrderView \| None` | `conditional_write` | 260 | SCAFFOLD |
| [기존] `src/services/order_service/repository.py` | `_row_to_order`에 신규 컬럼(`version`, `venue_symbol`, `time_in_force`, `parent_order_id`, `algo_run_id`, `fee_total`, `fee_currency`, `unknown_since`, `provider_order_date`) 반영; `delete()` 제거(§5.3) | 기존 시그니처 유지 | — | 220 | SCAFFOLD |
| [신규] `src/services/oms/adapters/order_events_repository.py` | append-only 이벤트 | `async def append(conn, ev: OrderTransitionEvent) -> int`(seq 반환); `async def timeline(conn, order_id) -> list[OrderTransitionEvent]` | — | 100 | SCAFFOLD |
| [신규] `src/services/oms/adapters/fills_repository.py` | 체결 원장(중복 방지) | `async def insert_if_absent(conn, fill: FillEvent) -> bool`(UNIQUE(venue, provider_fill_id) 충돌 시 False); `async def list_for_order(conn, order_id) -> list[FillEvent]` | — | 120 | SCAFFOLD |
| [신규] `src/services/oms/adapters/outbox_repository.py` | 주문 명령 outbox | `async def enqueue(conn, *, order_id, command_type: Literal["SUBMIT","CANCEL","MODIFY"], payload: dict, not_before: datetime) -> UUID`; `async def claim_batch(conn, *, worker_id: str, limit: int, lease_sec: int) -> list[OutboxRow]`(`FOR UPDATE SKIP LOCKED`, `lease_until` 설정); `async def mark_done(conn, id, *, expected_worker) -> None`; `async def mark_retry(conn, id, *, attempt, not_before, last_error) -> None`; `async def mark_dead(conn, id, reason) -> None` | — | 220 | SCAFFOLD |
| [신규] `src/services/oms/adapters/inbox_repository.py` | 거래소 이벤트 inbox(중복 전달 흡수) | `async def insert_if_absent(conn, ev: ProviderOrderEvent) -> bool`(UNIQUE(venue, provider_event_id)); `async def claim_unprocessed(conn, *, limit) -> list[ProviderOrderEvent]`; `async def mark_processed(conn, id, *, expected_state="NEW")` | — | 160 | SCAFFOLD |
| [신규] `src/services/oms/adapters/idempotency_repository.py` | 스코프 해시 선점 | `async def claim(conn, *, scope_hash: str, digest: str, order_id: UUID, ttl: timedelta) -> ClaimResult`(NEW / EXISTING(order_id) / DIGEST_MISMATCH) | — | 120 | SCAFFOLD |
| [신규] `src/services/oms/application/submit_order.py` | 제출 명령 핸들러(tx 1개: 멱등 선점→orders INSERT(CREATED)→VALIDATED 전이→outbox enqueue→commit) | `async def submit_order(cmd: SubmitOrderCommand, *, pool, profile: VenueCapabilityProfile, registry: SymbolRegistry, pre_submit_gate: PreSubmitGate \| None, clock: Clock) -> OrderView` | repo 포트, `order_service.gate` | 240 | SCAFFOLD |
| [기존] `src/services/order_service/submit.py` | 얇은 호환 래퍼로 축소 — `submit_order(order, ...)`는 `oms.application.submit_order` 호출 후 outbox 디스패치를 **동기로 1회 즉시 시도**(기존 호출부 계약 유지: 반환 시점에 SUBMITTED/REJECTED/UNKNOWN 중 하나) | 기존 시그니처 유지, `apply_fill`은 `inbox_processor`로 위임 | — | 160 | SCAFFOLD |
| [신규] `src/services/oms/application/outbox_dispatcher.py` | outbox → 어댑터 호출 → 전이 | `class OutboxDispatcher`; `async def dispatch_once(self, *, limit=50) -> DispatchReport`; `async def run_forever(self)`; 내부: `_send_submit`, `_send_cancel`, `_send_modify` | `ExchangeAdapter`, `ResilientTransport` 예외 taxonomy, repo 포트 | 280 | SCAFFOLD |
| [신규] `src/services/oms/application/inbox_processor.py` | inbox 이벤트 → fills/전이 | `class InboxProcessor`; `async def process_once(self, limit=100) -> int`; `async def ingest(self, ev: ProviderOrderEvent) -> bool`(WS/폴링 양쪽이 호출) | repo 포트, `fill_normalizer`, `position_ledger` | 260 | SCAFFOLD |
| [신규] `src/services/oms/application/cancel_order.py` | 취소 명령(전이 CANCEL_REQUESTED + outbox) | `async def cancel_order(cmd: CancelOrderCommand, *, pool, clock) -> OrderView` | repo 포트 | 140 | SCAFFOLD |
| [기존] `src/services/order_service/cancel.py` | 호환 래퍼(위 호출 + 동기 1회 디스패치) | 기존 시그니처 | — | 80 | SCAFFOLD |
| [신규] `src/services/oms/application/modify_order.py` | 정정(LIMIT만, `profile.supports_modify` 없으면 cancel+replace 자동 분해, 아니면 거부) | `async def modify_order(cmd: ModifyOrderCommand, *, pool, profile, clock) -> OrderView` | repo 포트 | 180 | SCAFFOLD |
| [기존] `src/services/order_service/modify.py` | 호환 래퍼 | 기존 시그니처 | — | 60 | SCAFFOLD |
| [신규] `src/services/oms/application/unknown_resolver.py` | UNKNOWN 해소(§6 F5) | `async def resolve_unknown(order_id, *, adapter, pool, clock, sleep, max_attempts=5, backoff=(1,2,4,8,16)) -> OrderView`; 상한 초과 → `activate_safety_control(scope=ACCOUNT)` + `RECONCILING` 이벤트 | `ExchangeAdapter.get_order`, `get_open_orders`, `find_order_by_client_id`(신규 ABC 메서드), risk_gate | 240 | SCAFFOLD |
| [기존] `src/services/order_service/reconcile.py` | `resolve_unknown` → 위 모듈로 위임(시그니처 유지) | — | 40 | SCAFFOLD |
| [신규] `src/services/oms/application/restart_recovery.py` | 재시작 복구(§6 F6): outbox 미완료·UNKNOWN·in-flight 재개 | `async def recover_on_startup(pool, *, resolve_adapter, publish, clock) -> RecoveryReport` | outbox/inbox repo, `unknown_resolver` | 220 | SCAFFOLD |
| [기존] `src/services/execution_loop/recovery_wiring.py` | `recover_orders_on_startup`가 위를 먼저 호출한 뒤 기존 로직 수행(FILLED는 tick 위임 규칙 유지) | 기존 시그니처 | — | 130 | SCAFFOLD |
| [신규] `src/services/oms/application/three_way_reconciler.py` | 3자 대사 오케스트레이션(§6 F7) | `async def reconcile_account(*, pool, adapter, tenant_id, connection_id, account_ref, window: timedelta, policy) -> ReconcileSummaryView`; 내부에서 `foundation.reconciliation.application.run_reconciliation` 호출(EntitySnapshot 조립) | `ExchangeAdapter.get_open_orders/get_order_history/get_fills/get_balance`, reconciliation repo | 280 | SCAFFOLD |
| [신규] `src/services/oms/application/reconcile_scheduler.py` | 주기 실행(주문 5분, 잔고·포지션 15분) + 리스 | `class ReconcileScheduler(run_forever)`; 대상별 advisory lock `pg_try_advisory_xact_lock(hashtext('recon:'||account_ref))` | 위 | 140 | SCAFFOLD |
| [신규] `src/services/oms/application/algo_executor.py` | 슬라이스 계획 실행(Phase 1: TWAP만 활성, VWAP/POV/ICEBERG는 `UnsupportedVenueFeatureError("ALGO_NOT_ENABLED")`) | `async def start_algo(req: AlgoRequest, *, pool, profile, clock) -> AlgoRunView`; `async def tick_algo(run_id, *, pool, submit, clock)`(due 슬라이스 1개 제출, kill switch 시 PAUSED) | `submit_order`, `algo_slicer`, risk_gate | 260 | SCAFFOLD |
| [신규] `src/services/oms/application/order_query.py` | 읽기 전용 조회 | `async def get_order(pool, order_id, *, tenant_id) -> OrderView`; `async def timeline(pool, order_id, *, tenant_id) -> OrderTimelineView`; `async def list_open(pool, *, tenant_id, execution_id)` | — | 120 | SCAFFOLD |
| [신규] `src/services/oms/application/audit_bridge.py` | 전이 이벤트 → `record_audit_log` + evidence `append_event` | `async def emit(conn, ev: OrderTransitionEvent, *, trace_id: UUID) -> None` | `core.logging.audit_log`, `foundation.evidence` | 100 | SCAFFOLD |
| [신규] `src/services/oms/wiring.py` | 조립: 워커 3개(outbox/inbox/reconcile) 생성, `main.py`에 태스크 등록 헬퍼 | `def build_oms_workers(pool, *, resolve_adapter, publish, policy) -> list[Coroutine]` | 전부 | 120 | SCAFFOLD |
| [기존] `src/main.py` | lifespan에 `build_oms_workers` 태스크 등록(PM 직렬화 대상) | — | +15 | SCAFFOLD |
| [기존] `src/core/executor/executor.py` | `client_order_id` 생성을 `oms.domain.idempotency.client_order_id(scope)`로 교체(§5.2). LIVE 가드·시그니처·판단 로직 **불변** | 기존 시그니처 | — | 135 | **FROZEN_PAPER_ONLY** — PM 승인 후 |

### 2-D. 거래소 내구성 공통 — `src/exchanges/common/`

| 파일 경로 | 단일 책임 | 공개 계약 | 의존 | 상한 | Zone |
|---|---|---|---|---|---|
| [신규] `src/exchanges/common/error_taxonomy.py` | 거래소 오류 분류 enum + 분류 함수 | `class ExchangeErrorKind(str, Enum)`: `TRANSIENT_NETWORK`, `RATE_LIMITED`, `SERVER_ERROR`, `AUTH`, `CLOCK_SKEW`, `INSUFFICIENT_FUNDS`, `INVALID_ORDER`, `ORDER_NOT_FOUND`, `DUPLICATE_CLIENT_ID`, `MARKET_CLOSED`, `UNKNOWN_RESPONSE`; `def classify_http(status: int, retry_after: str \| None) -> ExchangeErrorKind \| None`; `class ExchangeError(ExchangeAPIError)`(kind, retryable, venue, http_status, venue_code, retry_after_sec) | `core.exceptions` | 140 | SCAFFOLD |
| [신규] `src/exchanges/common/http_policy.py` | 재시도·백오프·타임아웃 예산 | `@dataclass(frozen=True) RetryPolicy(max_attempts=4, base=0.25, cap=8.0, jitter="full")`; `def backoff_delay(policy, attempt, retry_after: float \| None, rng) -> float`; `@dataclass(frozen=True) TimeoutBudget(connect=2.0, read=5.0, total=8.0)`; 주문 제출은 `max_attempts=1`(§5.4 — 재시도는 outbox가 한다) | 없음 | 110 | SCAFFOLD |
| [신규] `src/exchanges/common/rate_limiter.py` | venue·엔드포인트 그룹별 토큰 버킷 | `class TokenBucket(rate_per_sec, burst)`; `async def acquire(self, n=1, *, timeout: float) -> None`(초과 시 `ExchangeError(RATE_LIMITED, retryable=True)`) | 없음 | 90 | SCAFFOLD |
| [신규] `src/exchanges/common/circuit_breaker.py` | venue 단위 회로 | `class VenueCircuit(failure_threshold=5, window_sec=30, open_sec=20, half_open_max=2)`; `def allow(self) -> bool`; `def record(self, ok: bool)`; 상태 `CLOSED/OPEN/HALF_OPEN`; OPEN이면 `ExchangeError(SERVER_ERROR, retryable=True, circuit_open=True)` | 없음 | 120 | SCAFFOLD |
| [신규] `src/exchanges/common/clock_sync.py` | 서버시간 오프셋 | `class ServerClock`; `async def sync(self, fetch_server_ms: Callable[[], Awaitable[int]]) -> None`(왕복 절반 보정); `def now_ms(self) -> int`; `offset_ms`, `last_sync_at`; `max_skew_ms=1000` 초과 시 `CLOCK_SKEW` 오류로 서명 전 차단 | 없음 | 100 | SCAFFOLD |
| [신규] `src/exchanges/common/transport.py` | 위 5개를 합성한 요청 파이프라인 | `class ResilientTransport(client: httpx.AsyncClient, *, venue, policy, budget, limiter, circuit, clock, classify_body: Callable[[int, dict], ExchangeErrorKind \| None])`; `async def request(self, method, path, *, params, content, headers_fn, idempotent: bool) -> dict`; 비-JSON 본문 → `UNKNOWN_RESPONSE`; `idempotent=False`(주문 제출)는 전송 후 오류 시 **재시도하지 않고** `SentUnknownError`로 승격 | 위 5개 | 260 | SCAFFOLD |
| [신규] `src/exchanges/common/ws_session.py` | WebSocket 수명주기 | `class WsSession(url, *, connect_fn, heartbeat: HeartbeatSpec, ack_validator: Callable[[dict], AckResult], seq_extractor: Callable[[dict], int \| None], on_resync: Callable[[], Awaitable[None]], on_distrust: Callable[[bool], Awaitable[None]])`; `async def run(self, subscriptions: list[dict], handler)`; 규칙: ping 주기 내 pong 없으면 재연결, subscribe ack 실패 코드면 예외, seq 갭(≥1 누락) 감지 시 `on_resync()`(REST 스냅샷) 후 계속, 재연결 시 재로그인·재구독·`on_resync()` | `websockets` | 280 | SCAFFOLD |
| [기존] `src/exchanges/common/adapter.py` | ABC 확장(하위호환 기본 구현 포함): `async def get_open_orders(symbol=None) -> list[Order]`, `async def get_fills(symbol=None, *, order_id=None, since=None) -> list[dict]`, `async def find_order_by_client_id(client_order_id) -> Order \| None`(기본: `NotImplementedError` — profile이 `supports_client_order_id=False`면 호출 금지), `def venue_profile(self) -> VenueCapabilityProfile`, `async def subscribe_order_stream(callback) -> None`(기본 NotImplementedError) | — | 150 | SCAFFOLD |
| [기존] `src/exchanges/common/types.py` | `ExchangeCapability`에 `profile: VenueCapabilityProfile \| None` 추가(MINOR, optional) | — | 60 | SCAFFOLD |
| [기존] `src/exchanges/factory.py` | `"nh"`, `"paper_sim"` 추가; `demo_mode=False`는 `AIOS_ALLOW_LIVE_ADAPTER=1` 없이는 `FrozenZonePaperAdapterBlockedError` | 기존 시그니처 | — | 90 | SCAFFOLD |

### 2-E. 거래소별 — `src/exchanges/{bitget,kis,nh}/`

| 파일 경로 | 단일 책임 | 공개 계약 | 상한 | Zone |
|---|---|---|---|---|
| [기존] `src/exchanges/bitget/adapter.py` | `_BitgetHTTPClient._request`를 `ResilientTransport.request`로 위임. 서명 타임스탬프를 `ServerClock.now_ms()`로. `_FATAL_ERROR_CODES` 상수를 `bitget/error_codes.py`로 이동 | 기존 시그니처 | 190 | SCAFFOLD |
| [신규] `src/exchanges/bitget/error_codes.py` | Bitget 코드→`ExchangeErrorKind` 표(문서 확인분만; 나머지는 `UNKNOWN_RESPONSE`, **retryable=False**) | `def classify_bitget(http: int, body: dict) -> ExchangeErrorKind` | 80 | SCAFFOLD |
| [신규] `src/exchanges/bitget/venue_profile.py` | Bitget 스팟 프로파일 상수 | `BITGET_SPOT_PROFILE: VenueCapabilityProfile` | 60 | SCAFFOLD |
| [신규] `src/exchanges/bitget/private_ws_mixin.py` | private `orders`/`fill` 채널(login op) → `ProviderOrderEvent` | `async def subscribe_order_stream(self, callback)`; `def parse_private_order_event(msg) -> list[ProviderOrderEvent]` | 240 | SCAFFOLD |
| [기존] `src/exchanges/bitget/trading_mixin.py` | `find_order_by_client_id`(`orderInfo?clientOid=`), `get_fills(since=)` 추가; 심볼 변환을 `SymbolRegistry`로; `_STATUS_MAP`에 `"new"`, `"init"`→ACKNOWLEDGED(미확인 표기) | 기존 유지 | 300 | SCAFFOLD |
| [기존] `src/exchanges/bitget/market_data_mixin.py` | `_run_ws_subscription` → `WsSession`으로 교체(하트비트·ack·resync) — 620줄이므로 `bitget/ws_parsers.py`(파서 6개)로 분할 | — | 300+220 | SCAFFOLD |
| [신규] `src/exchanges/kis/venue_profile.py` | KIS 프로파일(`supports_client_order_id=False`, `id_policy="DAILY_SEQUENCE"`, `supports_modify=True`, market hours) | `KIS_KR_EQUITY_PROFILE` | 60 | SCAFFOLD |
| [기존] `src/exchanges/kis/adapter.py` | `_request` → `ResilientTransport`; 토큰 발급 `asyncio.Lock` + 401 시 1회 무효화·재발급; HTTP 상태 분류 | 기존 시그니처 | 220 | SCAFFOLD |
| [기존] `src/exchanges/kis/trading_mixin.py` | `find_order_by_client_id`는 미지원(`None` 반환 금지 → `UnsupportedVenueFeatureError`); 대신 `get_open_orders`+`get_order_history(date)`로 UNKNOWN 역조회(§6 F5-b) | — | 280 | SCAFFOLD |
| [신규] `src/exchanges/nh/venue_profile.py` | NH 프로파일(정정/취소 엔드포인트 **추정** → `supports_modify=False`, `supports_cancel="UNVERIFIED"`) | `NH_KR_EQUITY_PROFILE` | 60 | SCAFFOLD |
| [기존] `src/exchanges/nh/adapter.py` | `_request` → `ResilientTransport` | — | 200 | SCAFFOLD |

### 2-F. PAPER 시뮬레이터 — `src/exchanges/paper/`

| 파일 경로 | 단일 책임 | 공개 계약 | 상한 | Zone |
|---|---|---|---|---|
| [신규] `src/exchanges/paper/simulator_adapter.py` | `ExchangeAdapter` 구현체. `is_paper_trading=is_sandboxed=True` 상수. 시세는 read-only 실어댑터(`ReadonlyAccountProvider`/`get_orderbook`)에서, 체결은 모델로 | `class PaperSimulatorAdapter(reference: ExchangeAdapter, ledger: PaperLedgerRepo, fill_model, fee_model, latency_model, clock, rng)` | 280 | SCAFFOLD |
| [신규] `src/exchanges/paper/fill_model.py` | 슬리피지·부분체결 | `class FillModel(spread_bps, impact_bps_per_pct_adv, partial_fill_prob, partial_min_pct)`; `def simulate(order, book: OrderBook, adv: Decimal, rng) -> list[SimFill]`(LIMIT은 호가 교차 시만) | 180 | SCAFFOLD |
| [신규] `src/exchanges/paper/fee_model.py` | 수수료 | `class FeeModel(maker_bps, taker_bps, fee_currency)`; `def fee(fill) -> Money` | 60 | SCAFFOLD |
| [신규] `src/exchanges/paper/latency_model.py` | 지연·응답 유실 주입 | `class LatencyModel(ack_ms_p50, ack_ms_p99, drop_response_prob)`; `async def apply(rng) -> LatencyOutcome`(DROP이면 어댑터는 `SentUnknownError` — UNKNOWN 경로 테스트용) | 70 | SCAFFOLD |
| [신규] `src/exchanges/paper/ledger_repository.py` | 시뮬 잔고·주문 영속(재시작 생존) | `paper_sim_accounts`, `paper_sim_orders` 테이블 CRUD(조건부 UPDATE) | 200 | SCAFFOLD |
| [신규] `src/exchanges/paper/venue_profile.py` | 시뮬 프로파일(참조 거래소 프로파일 복제 + `venue="paper_sim"`) | `def profile_for(reference: VenueCapabilityProfile)` | 40 | SCAFFOLD |

### 2-G. 마이그레이션 — `src/db/migrations/versions/`

| 파일 경로 | 내용 | 상한 |
|---|---|---|
| [신규] `<rev1>_oms_order_events_outbox_inbox.py` | `orders` 컬럼 추가 + 트리거 + `order_events`/`order_command_outbox`/`provider_event_inbox`/`fills`/`order_idempotency` (§4.2, §5.1) | 280 |
| [신규] `<rev2>_oms_algo_runs.py` | `algo_runs`, `algo_slices` | 120 |
| [신규] `<rev3>_paper_sim_ledger.py` | `paper_sim_accounts`, `paper_sim_orders` | 100 |

`down_revision`은 착수 시 `alembic heads`가 **단일**임을 확인한 값(감사 §2-B의
직렬화 규칙: `b3f7e0c1a4d5` 이후 PM이 지정). 다중 head면 먼저 merge revision.

---

## 3. 계약 (Contract)

### 3.1 DTO (`schema_version="v1"`, 모든 datetime tz-aware UTC, 금액·수량 Decimal)

```python
# src/services/oms/contracts/v1_commands.py
class IdempotencyScope(BaseModel):
    tenant_id: UUID; account_ref: str; provider: str          # provider: "bitget"|"kis"|"nh"|"paper_sim"
    strategy_id: str; strategy_version: str; execution_id: int
    intent_seq: int                                            # 실행 내 단조 증가(FSM 전이 카운터)
    window_start: datetime                                     # 의도 시각을 window(기본 60s)로 내림
    schema_version: Literal["v1"] = "v1"

class SubmitOrderCommand(BaseModel):
    command_id: UUID; trace_id: UUID; scope: IdempotencyScope
    symbol: str                                                # 정규 "BTC/USDT" / "005930"
    side: OrderSide; order_type: OrderType                     # MARKET|LIMIT (TWAP 등은 AlgoRequest)
    quantity: Decimal = Field(gt=0); price: Decimal | None = None
    time_in_force: Literal["GTC","IOC","FOK","DAY"] = "GTC"
    asset_class: AssetClass; mode: Literal["PAPER"] = "PAPER"  # LIVE 값 자체를 계약에서 배제
    parent_order_id: UUID | None = None; algo_run_id: UUID | None = None
    is_liquidation: bool = False; actor_subject_id: UUID | Literal["system"]
    issued_at: datetime

class CancelOrderCommand(BaseModel):
    command_id: UUID; trace_id: UUID; order_id: UUID; tenant_id: UUID
    reason: str; actor_subject_id: UUID | Literal["system"]; issued_at: datetime

class ModifyOrderCommand(CancelOrderCommand):
    new_price: Decimal | None = None; new_quantity: Decimal | None = None   # 둘 중 하나 필수

class AlgoRequest(BaseModel):
    algo_run_id: UUID; trace_id: UUID; scope: IdempotencyScope
    algo: Literal["TWAP","VWAP","POV","ICEBERG"]; symbol: str; side: OrderSide
    total_quantity: Decimal; start_at: datetime; end_at: datetime
    slice_count: int = Field(ge=1, le=500)
    max_participation_pct: Decimal = Decimal("10")            # 슬라이스 qty ≤ 구간 예상 거래량 × pct
    size_jitter_pct: Decimal = Decimal("20"); time_jitter_pct: Decimal = Decimal("30")
    display_quantity: Decimal | None = None                   # ICEBERG만
    limit_price: Decimal | None = None; seed: int             # 재현 가능한 무작위화
```

```python
# src/services/oms/contracts/v1_events.py
class ProviderOrderEvent(BaseModel):
    provider_event_id: str            # venue 고유(WS seq|fill id|"poll:{order}:{uTime}")
    venue: str; venue_symbol: str; exchange_order_id: str | None
    client_order_id: str | None; venue_status: str
    filled_quantity: Decimal; average_price: Decimal | None
    last_fill: FillEvent | None; venue_ts: datetime; received_at: datetime
    source: Literal["WS","POLL","RESYNC","SUBMIT_RESPONSE"]; raw_hash: str  # raw 본문은 저장하지 않음(108번 §2.1)

class FillEvent(BaseModel):
    provider_fill_id: str; venue: str; order_id: UUID | None; exchange_order_id: str
    symbol: str; side: OrderSide; quantity: Decimal; price: Decimal
    fee: Decimal; fee_currency: str; liquidity: Literal["MAKER","TAKER","UNKNOWN"]
    venue_ts: datetime

class OrderTransitionEvent(BaseModel):
    order_id: UUID; seq: int | None = None; from_status: OrderStatus; to_status: OrderStatus
    event: str                         # OrderEvent.value
    reason_code: str | None; actor_subject_id: UUID | Literal["system"]
    trace_id: UUID; command_id: UUID | None; provider_event_id: str | None
    occurred_at: datetime; payload_hash: str

class Discrepancy(BaseModel):
    kind: Literal["ORDER_MISSING_AT_PROVIDER","ORDER_MISSING_INTERNAL","STATUS_MISMATCH",
                  "FILLED_QTY_MISMATCH","FILL_MISSING_INTERNAL","BALANCE_MISMATCH"]
    entity_key: str; internal_value: Decimal | str | None; provider_value: Decimal | str | None
    materiality: Classification
```

### 3.2 VenueCapabilityProfile

```python
class VenueCapabilityProfile(BaseModel):
    venue: str; asset_classes: list[AssetClass]
    order_types: set[OrderType]; time_in_force: set[str]
    supports_client_order_id: bool; client_order_id_max_len: int; client_order_id_charset: str
    id_policy: Literal["STABLE","DAILY_SEQUENCE"]        # KIS/NH = DAILY_SEQUENCE(자정 후 ODNO 재사용 가능)
    supports_modify: bool; supports_cancel: Literal["YES","UNVERIFIED"]
    supports_ws_orders: bool; supports_batch: bool
    price_tick: dict[str, Decimal]; qty_lot: dict[str, Decimal]; min_notional: dict[str, Decimal]
    rate_limits: dict[str, tuple[int, int]]              # group -> (per_sec, burst)
    submit_timeout: TimeoutBudget; query_timeout: TimeoutBudget
    market_hours: MarketHours | None; max_open_orders_per_symbol: int
    verified: Literal["LIVE_VERIFIED","DOC_ONLY","ESTIMATED"]   # §10 정직 표기
```

확정값(문서 근거만, 라이브 미검증): Bitget `client_order_id_max_len=40`(**미확인**),
`supports_ws_orders=True`(02b §6, login 방식 미확인), `supports_modify=True`
(cancel-replace). KIS `supports_client_order_id=False`, `id_policy="DAILY_SEQUENCE"`.
NH `supports_modify=False`, `supports_cancel="UNVERIFIED"`(02e §3 추정 엔드포인트).

### 3.3 버전·호환 규칙(107번 §3)

- 위 DTO는 L2 도메인 계약. optional 필드 추가 = MINOR(버전 불변). `OrderStatus`
  enum 값 추가는 소비자가 `UNKNOWN` 폴백을 갖는 것이 이미 계약(8.3)이므로 MINOR.
- `Order`(01번)·`OrderStatus` 자체는 공유접점 §2.3 동결 계약 — 이 명세는 **필드
  추가만**(ADR-C 선례) 하며 상태 집합·전이 의미를 바꾸지 않는다. 신규 이벤트
  `CANCEL_REQUESTED`/`MODIFY_REQUESTED`는 **상태가 아니라 이벤트**(`order_events`
  에만 기록, `orders.status`는 그대로)로 두어 동결을 지킨다.
- DB 컬럼 추가는 전부 NULL 허용 또는 DEFAULT — 구 코드 경로 회귀 없음.

### 3.4 에러 taxonomy

| 코드 | 클래스 | 재시도 | 호출자 조치 |
|---|---|---|---|
| `OMS_IDEMPOTENT_REPLAY` | 정상 반환(기존 OrderView) | — | 기존 주문 사용 |
| `OMS_IDEMPOTENCY_DIGEST_MISMATCH` | `OmsError` | 아니오 | 409. 같은 스코프·다른 내용 — 상위 버그 |
| `OMS_INVALID_TRANSITION` | `InvalidOrderTransitionError` | 아니오 | 500 + 알림(코드 결함) |
| `OMS_VALIDATION_*` (`TICK`,`LOT`,`MIN_NOTIONAL`,`UNSUPPORTED_TYPE`,`UNSUPPORTED_TIF`,`UNKNOWN_SYMBOL`,`MARKET_CLOSED`) | `OrderValidationError` | 아니오 | 400. FD-8.2 버그 신호(CRITICAL 로그) |
| `OMS_DENIED_BY_GATE` | `OrderDeniedByRiskGateError`(기존) | 아니오 | 403, reason_codes 전달 |
| `OMS_CONCURRENCY_CONFLICT` | `ConcurrencyConflictError`(105번) | 재조회 후 | 409 |
| `OMS_ALGO_NOT_ENABLED` | `UnsupportedVenueFeatureError` | 아니오 | 400 (Phase 1: TWAP 외) |
| `EXCH_TRANSIENT_NETWORK` / `EXCH_RATE_LIMITED` / `EXCH_SERVER_ERROR` | `ExchangeError(retryable=True)` | 예(백오프, 조회 계열만 transport 내부; 주문은 outbox) | outbox `mark_retry` |
| `EXCH_SENT_UNKNOWN` | `SentUnknownError(ExchangeError)` | **아니오** | 주문 → `UNKNOWN`, `unknown_resolver` 큐 |
| `EXCH_AUTH` / `EXCH_CLOCK_SKEW` | `ExchangeError(retryable=False)` | CLOCK_SKEW는 재동기화 후 1회 | 회로 OPEN + 운영 알림. 주문 → `FAILED(reason=AUTH)` |
| `EXCH_INSUFFICIENT_FUNDS` / `EXCH_INVALID_ORDER` / `EXCH_DUPLICATE_CLIENT_ID` / `EXCH_MARKET_CLOSED` | `ExchangeError(retryable=False)` | 아니오 | 주문 → `REJECTED(reason)`. `DUPLICATE_CLIENT_ID`는 → `find_order_by_client_id`로 기존 주문 채택(중복 방지 성공 케이스) |
| `EXCH_ORDER_NOT_FOUND` | `ExchangeError(retryable=False)` | 아니오 | UNKNOWN 해소 시 "거래소에 없음" 증거로 사용(단독으로 FAILED 확정 금지, §6 F5) |
| `EXCH_UNKNOWN_RESPONSE` | `ExchangeError(retryable=False)` | 아니오 | 조회: 오류 전파. 주문: `SentUnknownError`로 승격 |
| `EXCH_CIRCUIT_OPEN` | `ExchangeError(retryable=True, circuit_open=True)` | 회로 닫힌 뒤 | outbox `not_before = now + open_sec` |

기존 `RetryableExchangeError`/`FatalExchangeError`는 `ExchangeError`의 하위로 유지
(호환): `retryable=True` → Retryable, 아니면 Fatal. **미지 코드의 기본값은
`UNKNOWN_RESPONSE`(재시도 불가)** — 감사 §7의 "잔고 부족까지 재시도" 결함 종결.

---

## 4. 불변조건·상태기계

### 4.1 불변조건 (위반 시 전부 fail-closed)

| ID | 불변조건 | 강제 수단 |
|---|---|---|
| I1 | `orders.client_order_id` 전역 UNIQUE(기존) + `order_idempotency(scope_hash)` UNIQUE | DB |
| I2 | 한 주문의 `status` 전이는 §4.2 표의 (from,to)만 허용 | DB 트리거 `oms_enforce_order_transition` + 코드 `next_status` |
| I3 | `filled_quantity ≤ quantity`, `filled_quantity ≥ 0`, 단조 비감소 | DB CHECK + 트리거(OLD.filled ≤ NEW.filled) |
| I4 | 터미널 상태(FILLED/CANCELLED/REJECTED/EXPIRED/FAILED)에서 어떤 UPDATE도 `status`를 바꾸지 못함 | DB 트리거 |
| I5 | `orders.version`은 UPDATE마다 +1, 조건부 UPDATE는 `expected_version` 일치 필수 | 트리거(자동 증가) + 코드 |
| I6 | 모든 `status` 변경은 같은 트랜잭션 안에 `order_events` 1행 | 트리거가 `pg_trigger_depth()`와 세션 변수 `oms.event_written='1'` 확인, 없으면 RAISE(§5.1) |
| I7 | `order_events`, `fills`, `provider_event_inbox`는 append-only | `REVOKE UPDATE, DELETE ON ... FROM app_role`(마이그레이션) |
| I8 | 거래소 호출은 outbox 행 `SENDING` 상태에서만 발생 | `outbox_dispatcher` 단일 호출 지점 + `Executor` 경유(기존 가드) |
| I9 | `mode='LIVE'` 주문은 계약 레벨(`Literal["PAPER"]`)·Executor·factory 3중 차단 | 코드(ADR-E) |
| I10 | 제출 응답을 못 받은 주문은 삭제하지 않고 `UNKNOWN`으로 남긴다 | 코드(`repository.delete` 제거) |
| I11 | provider 값이 없을 때 대사는 0으로 해석하지 않는다(`PROVIDER_UNAVAILABLE`) | 코드(80번 §2) |
| I12 | `MATERIAL_MISMATCH`/`PROVIDER_UNAVAILABLE` 집계 시 해당 account 스코프 safety control ACTIVE 전까지 새 SUBMIT enqueue 거부 | 코드(`submit_order`가 `pre_submit_gate` + reconciliation_state 확인) |
| I13 | 심볼은 `SymbolRegistry` 등록분만 주문 가능 | 코드(`UnknownSymbolError`) |

### 4.2 주문 상태 전이표

`orders.status`(01번 `OrderStatus` 그대로). 이벤트는 `order_events.event`.

| from | event | guard | to | side-effect | 감사 이벤트(`event`) |
|---|---|---|---|---|---|
| — | `SUBMIT_ACCEPTED` | 멱등 선점 NEW, gate ALLOW | CREATED | orders INSERT | `order_created` |
| CREATED | `VALIDATED` | tick/lot/notional/profile 통과 | VALIDATED | outbox `SUBMIT` enqueue | `order_validated` |
| CREATED | `VALIDATION_FAILED` | — | FAILED | reason_code | `order_failed` |
| VALIDATED | `SENT` | outbox 행 SENDING 선점 후 어댑터 호출 직전 | SUBMITTED | `sent_at` | `order_submitted` |
| SUBMITTED | `ACK` | 응답에 `exchange_order_id` | ACKNOWLEDGED | `exchange_order_id` 저장 | `order_acknowledged` |
| SUBMITTED | `VENUE_REJECTED` | 응답 REJECTED/INSUFFICIENT_FUNDS/INVALID_ORDER | REJECTED | reason | `order_rejected` |
| SUBMITTED | `RESPONSE_LOST` | `SentUnknownError` | UNKNOWN | `unknown_since=now`, resolver enqueue | `order_unknown` |
| SUBMITTED/ACKNOWLEDGED | `FILL` | 0 < Σfills < qty | PARTIALLY_FILLED | fills INSERT, avg 갱신 | `order_partially_filled` |
| SUBMITTED/ACKNOWLEDGED/PARTIALLY_FILLED | `FILL` | Σfills == qty(lot 오차 ≤ 1 lot) | FILLED | position_ledger, FSM 전이 콜백 | `order_filled` |
| ACKNOWLEDGED/PARTIALLY_FILLED | `CANCEL_REQUESTED` | 터미널 아님 | (불변) | outbox `CANCEL` | `order_cancel_requested` |
| ACKNOWLEDGED/PARTIALLY_FILLED | `VENUE_CANCELLED` | 거래소 확인 | CANCELLED | 잔량 확정 | `order_cancelled` |
| ACKNOWLEDGED/PARTIALLY_FILLED | `VENUE_EXPIRED` | TIF 만료/장 마감 | EXPIRED | — | `order_expired` |
| ACKNOWLEDGED/PARTIALLY_FILLED | `MODIFY_REQUESTED` | LIMIT & profile.supports_modify | (불변) | outbox `MODIFY` | `order_modify_requested` |
| ACKNOWLEDGED | `MODIFIED` | 거래소 확인 | ACKNOWLEDGED | price/qty 갱신(version+1) | `order_modified` |
| UNKNOWN | `RESOLVED_AS(x)` | 역조회 결과 x ∈ {ACKNOWLEDGED, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED} | x | 해당 side-effect | `order_unknown_resolved` |
| UNKNOWN | `RESOLVED_ABSENT` | `ORDER_NOT_FOUND` **연속 2회** + 미체결 목록·당일 이력에 없음 + `unknown_since` 경과 ≥ profile 기준(기본 120s) | FAILED | reason=`NOT_AT_PROVIDER` | `order_failed` |
| UNKNOWN | `UNRESOLVED_LIMIT` | 시도 상한 초과 | (불변) | safety control ACCOUNT ACTIVE, CRITICAL 알림 | `order_unknown_escalated` |
| 임의 비터미널 | `RECONCILE_CORRECTION` | 3자 대사 증거 + 운영자 승인(recovery_case) | 증거가 가리키는 상태 | — | `order_reconciled` |
| 터미널 | 아무 이벤트 | — | **거부** | — | (트리거 RAISE) |

전이표 밖 → `InvalidOrderTransitionError`(코드) 또는 트리거 RAISE(DB). 둘 다
fail-closed. `SUBMITTED`→`FILLED` 직행(동기 체결)은 `FILL` 이벤트로 허용.

### 4.3 LIVE 가드(변경 없음)

`Executor.execute()`의 `mode != "PAPER"` 차단, `adapter.is_paper_trading and
is_sandboxed` 이중 확인, `@require_paper_sandbox`는 그대로. 이 명세가 추가하는
것: `SubmitOrderCommand.mode: Literal["PAPER"]`(계약 레벨), factory의
`demo_mode=False` 차단(환경변수 없이는 생성 불가). 가드 해제는 별도 ADR.

### 4.4 outbox / inbox 행 상태

- `order_command_outbox.state`: `PENDING → SENDING → DONE | RETRY(→PENDING) | DEAD`.
  `SENDING` 행은 `lease_until` 경과 시 복구 워커가 `UNKNOWN` 처리(§6 F6).
- `provider_event_inbox.state`: `NEW → PROCESSED | IGNORED(매칭 주문 없음, 대사 대상)`.

---

## 5. 동시성·멱등성·트랜잭션 경계 (105번)

### 5.1 쓰기별 잠금·조건

| 쓰기 | 방식 | 근거 |
|---|---|---|
| `order_idempotency` 선점 | `INSERT ... ON CONFLICT (scope_hash) DO NOTHING RETURNING`, 없으면 SELECT 후 digest 비교 | 105번 §2.2(단일 소유 UNIQUE) |
| `orders` INSERT(CREATED) | 같은 tx | — |
| `orders` 전이 | `conditional_update(expected_state_column="status")` **+** `AND version = $expected_version`(헬퍼에 `extra_conditions` 파라미터 추가 — `core/db/conditional_write.py` 소폭 확장, 기본값 없음=기존 동작) | 105번 §2.1 |
| `order_events` INSERT | 전이와 같은 tx, 전이 UPDATE **직전**에 `SET LOCAL oms.event_written = '1'` 후 INSERT → 트리거가 확인 | I6 |
| outbox enqueue | 전이 tx 안 | 원자성(주문 있는데 명령 없음/그 반대 방지) |
| outbox claim | `UPDATE ... SET state='SENDING', worker_id=$1, lease_until=now()+$lease WHERE id IN (SELECT id FROM order_command_outbox WHERE state='PENDING' AND not_before<=now() ORDER BY created_at LIMIT $n FOR UPDATE SKIP LOCKED) RETURNING *` | 다중 워커 안전 |
| outbox done/retry/dead | `WHERE id=$1 AND state='SENDING' AND worker_id=$2` RETURNING(리스 잃은 워커의 늦은 쓰기 차단) | 105번 §2 |
| inbox insert | `INSERT ... ON CONFLICT (venue, provider_event_id) DO NOTHING` — 중복 전달 흡수 | R1 |
| fills insert | `ON CONFLICT (venue, provider_fill_id) DO NOTHING`; 삽입된 경우에만 `filled_quantity` 재계산(fills 합산으로 — 누적값 신뢰 안 함) | 정밀도·중복 |
| `strategy_executions.fsm_state` | 기존 `_make_fsm_state_writer` 조건부 UPDATE 그대로 | 기존 |
| 대사 실행 | `pg_try_advisory_xact_lock(hashtext('recon:'||account_ref))` 실패 시 skip + `DEDUPED` | REC-004 |
| algo tick | `UPDATE algo_runs SET ... WHERE id=$1 AND state='RUNNING' AND next_slice_seq=$2 RETURNING` | 슬라이스 이중 제출 방지 |
| paper_sim 잔고 | `UPDATE paper_sim_accounts SET available=available-$q WHERE ... AND available>=$q RETURNING` | 음수 잔고 차단 |

### 5.2 멱등 키 스코프·digest·client_order_id

- `scope_hash = sha256(json(scope, sort_keys))`. `window_start = floor(issued_at, 60s)`.
  같은 실행·같은 intent_seq·같은 분 안의 재시도는 항상 같은 해시 → 같은 주문.
- `digest = sha256(symbol|side|type|qty|price|tif)` — 같은 스코프 다른 내용 → 409.
- `client_order_id = "a" + base32(scope_hash)[:N]`, N = `min(profile.client_order_id_max_len, 32) - 1`,
  charset은 profile 기준(Bitget 영숫자 **미확인**, 착수 시 문서 확인). **타임스탬프 금지**.
  `Executor`는 `intent_seq = strategy_executions.intent_counter`(신규 컬럼, 전이 시 +1)를
  사용 — 리프 L4-10.
- `supports_client_order_id=False`(KIS/NH): client id는 내부 전용. 거래소 매핑은
  `exchange_order_id`+`provider_order_date`로. UNKNOWN 역조회는 §6 F5-b.

### 5.3 트랜잭션 경계 변경(기존 대비)

- **폐기**: "전송 실패는 DB에 흔적 없음"(`repository.delete`). **대체**: 전송 전
  실패 = `FAILED(reason)`, 전송 후 응답 유실 = `UNKNOWN`. `tests/integration/
  test_order_service.py::test_submit_order_network_error_propagates`는 "행이 남고
  status=UNKNOWN 또는 FAILED"로 기대값 교체(리프 L4-12).
- 거래소 호출은 어떤 DB 트랜잭션도 열지 않은 상태에서(커넥션 반납 후) 수행.
- 이벤트 버스 발행은 commit 이후(기존 원칙 유지).

### 5.4 재시도 책임 분리

| 계층 | 재시도 | 이유 |
|---|---|---|
| `ResilientTransport` | 조회·취소(멱등) 계열만, `RetryPolicy` | 안전 |
| 주문 제출 | transport `max_attempts=1` | 응답 유실 시 재시도는 중복 주문 |
| outbox | `RETRY` 상태 재클레임(backoff = `backoff_delay`), `max_attempts=6` 후 `DEAD` + `FAILED` | 재시도 전 반드시 `find_order_by_client_id`/역조회(FD-4.2-b "재시도 전 FD-4.2-a") |

---

## 6. 실패 모드와 복구

| ID | 실패 | 감지 방법 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|---|
| F1 | 제출 전 크래시(CREATED/VALIDATED, outbox PENDING) | 재시작 시 `outbox.state='PENDING'` | 없음(거래소 미호출) | 디스패처가 정상 처리 | `order_submitted` 정상 경로 |
| F2 | 전송 중 크래시(outbox SENDING, lease 만료) | `restart_recovery`: `lease_until < now` | 주문 → `UNKNOWN`(`RESPONSE_LOST`) | `unknown_resolver` | `order_unknown`, `system.restart_recovery` |
| F3 | 응답 유실(타임아웃, 비JSON, 5xx after send) | `SentUnknownError` | `UNKNOWN`, outbox `DONE`(재전송 금지) | F5 | `order_unknown` |
| F4 | 429/5xx 조회 계열 | `classify_http` | 백오프(+`Retry-After`), 회로 카운트 | 회로 OPEN 시 outbox `not_before` 연기, 대사 `PROVIDER_UNAVAILABLE` | `venue_circuit_opened` |
| F5-a | UNKNOWN(Bitget, client id 지원) | resolver 큐 | `find_order_by_client_id` → 있으면 `RESOLVED_AS(x)`; `ORDER_NOT_FOUND` 2회 연속 + open/history 부재 + 120s 경과 → `RESOLVED_ABSENT` | 상한 5회(1,2,4,8,16s) 초과 → ACCOUNT safety control + CRITICAL | `order_unknown_resolved` / `order_unknown_escalated` |
| F5-b | UNKNOWN(KIS/NH, client id 없음) | resolver 큐 | `get_open_orders`+`get_order_history(today)`에서 (symbol, side, qty, price, 제출시각±30s) 매칭 후보 1개면 채택; 0개 & 조건 충족 → ABSENT; ≥2개 → 즉시 ESCALATE(자동 판단 금지) | 운영자 recovery_case | 동일 |
| F6 | 재시작 | lifespan | `restart_recovery` 순서: ① outbox lease 만료 → UNKNOWN ② UNKNOWN 전부 resolver ③ 비터미널 주문 `RESYNC`(REST 스냅샷 → inbox) ④ 기존 `recovery_wiring`(FILLED는 tick 위임 규칙 유지) ⑤ 완료 전 `submit_order` 거부(`OMS_RECOVERY_IN_PROGRESS`) | 60s 내 완료 목표(§7 RTO) | `system.restart_recovery`(건수) |
| F7 | 3자 대사 불일치 | `reconcile_account` 주기 | `MATERIAL_MISMATCH` → safety control ACCOUNT + 신규 SUBMIT enqueue 거부(I12); `MINOR_DIFFERENCE` → 기록만 | 운영자 recover(80번 §2: resume 자동 금지, resolve ≠ resume) | `reconciliation_material_mismatch` |
| F8 | 부분체결 후 취소 | inbox `VENUE_CANCELLED` with filled>0 | `CANCELLED`, `filled_quantity` 확정, position_ledger는 체결분만 | — | `order_cancelled` |
| F9 | 중복 전달(WS 재전송·폴링 중복) | inbox UNIQUE 충돌 | 무시(`False`) | — | 메트릭만 |
| F10 | WS 시퀀스 갭 | `seq_extractor` 불연속 | `on_resync()`(open orders + fills since last) → inbox `source=RESYNC` | 재구독 | `ws_sequence_gap` |
| F11 | WS 하트비트 실패 | ping 후 pong 없음(Bitget 30s, KIS PINGPONG 에코) | 재연결(백오프), `on_distrust(True)` → `market.distrust.entered` | 재연결 성공 → `on_distrust(False)` + resync | `ws_reconnected` |
| F12 | 시계 드리프트 | `ServerClock.offset` 절대값 > 1000ms 또는 서명 오류 코드 | 서명 차단, `sync()` 재시도 1회 | 지속 시 회로 OPEN + 운영 알림 | `venue_clock_skew` |
| F13 | 네트워크 분리(모든 venue 회로 OPEN) | 회로 상태 | 신규 SUBMIT enqueue는 허용하되 디스패치 보류; 대사 `PROVIDER_UNAVAILABLE` → I12 | 회로 HALF_OPEN 성공 → 재개 | `venue_circuit_closed` |
| F14 | 거래소가 `DUPLICATE_CLIENT_ID` | 오류 코드 | `find_order_by_client_id`로 기존 주문 채택 → `ACK` | — | `order_acknowledged(reason=DUPLICATE_ADOPTED)` |
| F15 | KIS 토큰 만료/분당 1회 발급 제한 | 401 / 발급 실패 | Lock 안에서 1회 재발급, 실패 시 `AUTH` | 회로 OPEN | `venue_auth_failed` |
| F16 | 장 마감 중 주문(KIS/NH) | `profile.market_hours` | `OMS_VALIDATION_MARKET_CLOSED`(거래소 미호출) | — | `order_failed` |
| F17 | 시뮬레이터 응답 유실 주입 | `LatencyModel.DROP` | F3와 동일 경로(테스트) | — | 동일 |
| F18 | algo 실행 중 kill switch | `tick_algo` gate DENY | `algo_runs.state=PAUSED`, 미제출 슬라이스 보류, 진행 중 child는 `CANCEL` enqueue | 운영자 resume(별도 승인) | `algo_paused` |

---

## 7. 성능·SLO·관측성 (108번)

### 7.1 목표

| 항목 | 목표 | 측정 지점 |
|---|---|---|
| 제출 내부 경로 p99(gate→멱등 선점→INSERT→VALIDATED→outbox commit) | ≤ 50 ms | `submit_order` 진입/커밋 |
| 제출 종단 p99(command 접수→거래소 ACK, venue RTT 포함) | Bitget ≤ 800 ms · KIS/NH ≤ 1500 ms(**미확인**, 실키 후 보정) · paper_sim ≤ 100 ms | outbox `claimed_at`→`ACK` 이벤트 |
| outbox 지연(PENDING→SENDING) p99 | ≤ 200 ms(디스패처 폴링 100ms 또는 LISTEN/NOTIFY) | outbox 타임스탬프 |
| inbox 처리 지연 p99(received_at→전이 commit) | ≤ 300 ms | inbox |
| 체결 확정 지연(거래소 체결 ts→FILLED commit) p99 | WS ≤ 1 s · 폴링 ≤ interval+1 s | fills.venue_ts vs order_events.occurred_at |
| 대사 T+0 | 미결 주문·체결: **5분** 주기, 잔고·포지션: 15분; 한 계정 대사 완료 ≤ 30 s | `reconcile_account` |
| UNKNOWN 해소 p95 | ≤ 60 s | `unknown_since`→resolved |
| RPO(주문 상태) | **0** — DB 동기 커밋이 진실, 거래소 호출 전후 각각 커밋 | — |
| RTO(주문 상태 가용) | DB 복구 후 ≤ 5 분, 그중 `restart_recovery` ≤ 60 s(미결 ≤ 500건 기준) | F6 |
| 처리량 | 실행 200개 동시 tick, outbox 100 cmd/s(단일 워커), inbox 500 ev/s | perf 테스트 |

### 7.2 메트릭(108번 §3 네이밍)

`aios.oms.order_submit.count_total{venue,outcome=accepted|denied|replay|error}` ·
`aios.oms.order_submit.duration_seconds{venue,stage=internal|end_to_end}` ·
`aios.oms.order_transition.count_total{from,to,event}` ·
`aios.oms.outbox.backlog{state}`(gauge) · `aios.oms.outbox.dispatch.duration_seconds{venue,command_type}` ·
`aios.oms.inbox.duplicate.count_total{venue,source}` · `aios.oms.inbox.lag_seconds`(gauge) ·
`aios.oms.unknown_orders{venue}`(gauge) · `aios.oms.unknown_resolution.duration_seconds{outcome}` ·
`aios.oms.reconcile.run.count_total{classification}` · `aios.oms.reconcile.lag_since_healthy_seconds{account}`(gauge) ·
`aios.oms.algo.slice_submit.count_total{algo,outcome}` ·
`aios.exchange.http_request.count_total{venue,group,kind}` · `aios.exchange.http_request.duration_seconds{venue,group}` ·
`aios.exchange.rate_limit.wait_seconds{venue,group}` · `aios.exchange.circuit.state{venue}`(gauge 0/1/2) ·
`aios.exchange.clock_offset_ms{venue}`(gauge) · `aios.exchange.ws.reconnect.count_total{venue,channel}` ·
`aios.exchange.ws.sequence_gap.count_total{venue,channel}` · `aios.exchange.ws.heartbeat_miss.count_total{venue}` ·
`aios.paper_sim.fill.count_total{kind=full|partial}` · `aios.paper_sim.slippage_bps`(histogram).

### 7.3 로그 필드(108번 §2 필수 + 도메인)

필수: `trace_id, tenant_id, actor_subject_id, command_id, component(oms.application|oms.adapters|exchanges.common|exchanges.<venue>), event, level, duration_ms`.
도메인: `order_id, client_order_id, exchange_order_id(있을 때), venue, venue_symbol, from_status, to_status, reason_code, outbox_id, attempt, provider_event_id, circuit_state, offset_ms`.
**금지**: raw payload, API 키, 서명, 계좌번호 원문(`account_ref`는 opaque ref).

### 7.4 알림 조건

| 조건 | 등급 |
|---|---|
| `unknown_orders > 0`가 120 s 지속 또는 `order_unknown_escalated` 1건 | CRITICAL |
| `reconcile.run{classification=MATERIAL_MISMATCH}` 1건 | CRITICAL |
| `reconcile.lag_since_healthy_seconds > 900` (계정당) | HIGH |
| `outbox.backlog{state=PENDING} > 100` 60 s 지속 또는 `DEAD` 1건 | HIGH |
| `circuit.state{venue}=OPEN` 60 s 지속 | HIGH |
| `clock_offset_ms > 1000` | HIGH |
| `ws.heartbeat_miss` 3회/5분 | MEDIUM |
| `inbox.duplicate` 비율 > 20%/5분 | MEDIUM(공급자 이상 징후) |
| `order_submit{outcome=error}` p99 > 목표 5분 지속 | MEDIUM |

---

## 8. 테스트 계획

각 리프에 negative test ≥ 1. 경로: `tests/unit/oms/`, `tests/unit/exchanges/common/`,
`tests/integration/oms/`, `tests/adversarial/oms/`, `tests/contract/oms/`, `tests/perf/oms/`.

### 8.1 단위(순수 규칙)

| 파일 | 케이스 |
|---|---|
| `tests/unit/oms/test_state_machine.py` | 전이표 전수(허용 25 / 거부 전수 — 11×11 조합 중 표 밖 전부 `InvalidOrderTransitionError`), 터미널 불변, 부분→전량 FILL 경계(lot 오차) |
| `tests/unit/oms/test_idempotency.py` | 같은 입력 → 같은 hash/client id 1000회; window 경계(59s vs 61s) 다른 키; `max_len` 준수; charset 위반 문자 없음; digest 불일치 |
| `tests/unit/oms/test_rounding.py` | BUY ROUND_DOWN/SELL ROUND_UP, lot ROUND_DOWN, min_notional 위반, tick=0 거부, Decimal 정밀도 유지(float 미개입) |
| `tests/unit/oms/test_fill_normalizer.py` | 부분체결 3건 평균가 = Σpq/Σq(정확 Decimal), 수수료 통화 분리, 미등록 심볼 fail-closed |
| `tests/unit/oms/test_symbol_registry.py` | Bitget `BTC/USDT↔BTCUSDT`, KIS `005930`, 미등록 → `UnknownSymbolError`, 역방향 충돌 |
| `tests/unit/oms/test_algo_slicer.py` | Σslices == total(정확), 참여율 상한 준수, 지터 범위, seed 재현성, 슬라이스 1개·500개 경계, ICEBERG display ≤ slice |
| `tests/unit/oms/test_reconcile_rules.py` | 6종 Discrepancy 각각, provider None → PROVIDER_UNAVAILABLE(0 해석 금지), 허용오차 경계 |
| `tests/unit/exchanges/common/test_error_taxonomy.py` | 429+Retry-After, 500/502/503, 401/403, 비JSON, 미지 코드 → `UNKNOWN_RESPONSE(retryable=False)` |
| `tests/unit/exchanges/common/test_http_policy.py` | full-jitter 범위, Retry-After 우선, cap, 주문 정책 `max_attempts=1` |
| `tests/unit/exchanges/common/test_circuit_breaker.py` | 임계 초과 OPEN, open_sec 후 HALF_OPEN, half-open 실패 재OPEN |
| `tests/unit/exchanges/common/test_clock_sync.py` | 왕복 보정, skew 초과 차단 |
| `tests/unit/exchanges/common/test_ws_session.py` | pong 미수신 재연결, ack 실패 코드 예외, seq 갭 → `on_resync` 1회, 재연결 후 재구독 순서, distrust 진입/해제 쌍 |
| `tests/unit/exchanges/paper/test_fill_model.py` | LIMIT 미교차 미체결, 시장가 슬리피지 부호(BUY↑ SELL↓), 부분체결 확률 0/1 극단 |

### 8.2 통합(실DB, `tests/integration/oms/`)

| 파일 | 케이스 |
|---|---|
| `test_submit_order_tx.py` | 정상: orders+order_events+outbox 1tx; gate DENY 시 어떤 행도 없음; 검증 실패 → FAILED 행 + 이벤트 |
| `test_db_transition_trigger.py` | 손 UPDATE `FILLED→SUBMITTED` RAISE; `order_events` 없이 status UPDATE RAISE; filled 감소 RAISE; version 자동 증가 |
| `test_outbox_dispatcher.py` | PENDING→DONE(SUBMITTED→ACK); `SentUnknownError` → UNKNOWN & 재전송 없음(어댑터 호출 1회 단언); 회로 OPEN → not_before 연기; DEAD 후 FAILED |
| `test_inbox_processor.py` | 부분→전량 체결; 중복 이벤트 무시(fills 1행); CANCELLED with filled>0; 매칭 없는 이벤트 IGNORED |
| `test_unknown_resolver.py` | 역조회 ACK 채택; NOT_FOUND 2회+120s → FAILED; 상한 초과 → safety control ACTIVE + 이후 submit DENY |
| `test_restart_recovery.py` | lease 만료 SENDING → UNKNOWN → 해소; 복구 중 submit 거부; 기존 recovery_wiring 규칙(FILLED는 tick 위임) 유지 |
| `test_three_way_reconciler.py` | REC-001 HEALTHY; REC-002 체결수량 불일치 → MATERIAL → submit 거부; REC-003 provider 타임아웃 → PROVIDER_UNAVAILABLE(잔고 0 가정 없음); REC-006 재실행 dedupe |
| `test_algo_executor_twap.py` | 5슬라이스 순차 제출·각각 별 client id·Σ=total; kill switch 중간 → PAUSED + child CANCEL enqueue; VWAP 요청 → `OMS_ALGO_NOT_ENABLED` |
| `test_paper_simulator_adapter.py` | 잔고 차감·환원, 부분체결 → inbox 2건, DROP 주입 → UNKNOWN 경로 종단, 재시작 후 잔고 보존 |
| `test_bitget_transport.py`(MockTransport) | 429+Retry-After 준수, 5xx 백오프 횟수, 비JSON, 서명 타임스탬프 = 서버시간 보정값, private WS login/ack/orders 파싱(**픽스처는 문서 추정 표기**) |
| `test_kis_durability.py` | 토큰 발급 동시 10회 → 발급 1회; 401 → 재발급 1회; 장 마감 → MARKET_CLOSED 거래소 미호출 |
| `test_executor_client_order_id.py` | 같은 (execution, intent_seq) 재호출 → 거래소 호출 1회(기존 `test_executor.py` 확장) |

### 8.3 적대적(`tests/adversarial/oms/`)

| 파일 | 케이스 |
|---|---|
| `test_concurrent_submit_same_scope.py` | `asyncio.gather` 50 동시 → orders 1행, 거래소 호출 1회(105번 §4.1 형태 A) |
| `test_concurrent_dispatchers.py` | 워커 3개 동시 claim → 각 outbox 행 정확히 1회 전송 |
| `test_stale_worker_late_write.py` | 리스 만료 후 늦은 `mark_done` → 0행(105번 형태 B) |
| `test_duplicate_delivery_storm.py` | 같은 체결 이벤트 1000회 → fills 1행, filled_quantity 정확 |
| `test_cross_tenant_isolation.py` | 타 tenant order_id 조회/취소 → 404, 대사 결과 교차 노출 없음(REC-008) |
| `test_tampered_provider_event.py` | `filled_quantity > quantity` 이벤트 → 거부 + `order_reconciled` 대상 표기, DB CHECK 위반 없음 |
| `test_live_mode_bypass_attempts.py` | `mode="LIVE"` 계약 거부, `demo_mode=False` factory 차단, `is_sandboxed=False` 어댑터 주입 → Executor 차단(3경로 100%) |
| `test_crash_between_send_and_commit.py` | 어댑터 응답 후 커밋 전 예외 주입 → 재시작 복구가 UNKNOWN→ACK 해소, 중복 주문 0 |

### 8.4 계약(`tests/contract/oms/`)

`v1_commands/events/views` JSON Schema 스냅샷 고정(107번 §3): optional 추가만 통과,
required 추가/타입 변경 시 실패. `VenueCapabilityProfile` 3개 상수의 `verified`
필드가 `LIVE_VERIFIED`가 아닌 항목 목록을 스냅샷으로 남겨 §10 미확인 항목과 일치.

### 8.5 성능(`tests/perf/oms/`)

`test_submit_internal_p99.py`(1000회 ≤ 50 ms p99, 단언 포함 — 감사 §9 "벤치마크
단언 없음" 재발 금지), `test_outbox_throughput.py`(100 cmd/s), `test_inbox_throughput.py`(500 ev/s),
`test_recovery_500_orders.py`(≤ 60 s).

### 8.6 e2e(키 확보 후, `tests/e2e/bitget_demo/`)

place/get/cancel 왕복 1회, `paptrading` 스팟 유효성 확정, private WS `orders`
login 확인 → 결과로 `BITGET_SPOT_PROFILE.verified="LIVE_VERIFIED"` 갱신.

---

## 9. 리프 목록 (구현 순서)

리프 하나 = 커밋 하나(`git commit -F - -- <경로>` + 즉시 push). 각 리프는
단독으로 ruff/mypy/pytest 통과. DoD의 검증 명령은 저장소 루트 기준.

| 리프 ID | 파일 | 선행 | DoD(검증 명령 · 기대 결과) | 예상 크기 |
|---|---|---|---|---|
| L4-01 | `src/services/oms/contracts/{v1_commands,v1_events,v1_views}.py`, `domain/errors.py`, `tests/contract/oms/test_schema_snapshot.py` | — | `pytest tests/contract/oms` 통과, 스냅샷 생성 | 600줄 |
| L4-02 | `domain/state_machine.py`, `tests/unit/oms/test_state_machine.py` | 01 | 전이표 전수 테스트 통과, 표 밖 조합 전부 예외 | 300 |
| L4-03 | `domain/idempotency.py`, `domain/rounding.py`, 테스트 2 | 01 | 결정론·라운딩 방향 테스트 통과 | 350 |
| L4-04 | `domain/symbol_registry.py`, `domain/venue_profile.py`, `exchanges/{bitget,kis,nh}/venue_profile.py`, 테스트 | 01 | 미등록 심볼 fail-closed, 프로파일 `verified` 스냅샷 | 450 |
| L4-05 | `domain/fill_normalizer.py`, `domain/reconcile_rules.py`, 테스트 | 03,04 | Decimal 평균가 정확, provider None → UNAVAILABLE | 450 |
| L4-06 | 마이그레이션 `<rev1>`(orders 컬럼·트리거·5개 테이블·REVOKE), `tests/integration/oms/test_db_transition_trigger.py` | 02 | `alembic upgrade head` 후 손 UPDATE 역전이 RAISE, downgrade 가능 | 350 |
| L4-07 | `core/db/conditional_write.py`에 `extra_conditions` 추가(기본값 없음, 기존 테스트 무변경), `ports/repository.py`, `adapters/order_repository.py`, `adapters/order_events_repository.py`, `application/audit_bridge.py` | 06 | 전이+이벤트 1tx, 이벤트 없는 전이 RAISE 재현 | 550 |
| L4-08 | `adapters/{outbox,inbox,fills,idempotency}_repository.py`, 테스트 | 06 | SKIP LOCKED 3워커 테스트, ON CONFLICT 중복 흡수 | 650 |
| L4-09 | `application/submit_order.py`, `order_service/submit.py` 축소, `order_service/repository.py` 갱신(`delete` 제거), `test_submit_order_tx.py`, 기존 `test_submit_order_network_error_propagates` 기대값 교체 | 07,08 | gate DENY 시 0행; 동시 50 submit → 1행(adversarial) | 500 |
| L4-10 | **[FROZEN_PAPER_ONLY, PM 승인]** `core/executor/executor.py` client id 교체 + `strategy_executions.intent_counter`(마이그레이션 `<rev1>`에 포함) + `test_executor_client_order_id.py` | 09 | 같은 intent 재호출 → 거래소 호출 1회; LIVE 가드 테스트 100% 유지 | 80 |
| L4-11 | `exchanges/common/{error_taxonomy,http_policy,rate_limiter,circuit_breaker,clock_sync}.py`, 단위 테스트 5 | — | 미지 코드 `retryable=False` | 700 |
| L4-12 | `exchanges/common/transport.py`, `bitget/error_codes.py`, `bitget/adapter.py` 위임, `test_bitget_transport.py` | 11 | 429/5xx/비JSON/서명 타임스탬프 보정 테스트; 기존 `test_bitget_adapter.py` 전부 통과 | 500 |
| L4-13 | `exchanges/common/adapter.py` ABC 확장(기본 구현), `bitget/trading_mixin.py`(`find_order_by_client_id`, `get_fills(since)`, registry), `exchanges/factory.py`(nh/paper_sim/LIVE 차단) | 04,12 | 기존 어댑터 테스트 통과, `demo_mode=False` 차단 테스트 | 350 |
| L4-14 | `application/outbox_dispatcher.py`, `wiring.py`(디스패처만), `test_outbox_dispatcher.py`, `test_concurrent_dispatchers.py`, `test_stale_worker_late_write.py` | 09,13 | SentUnknown → UNKNOWN 1회 호출; 3워커 정확히 1회 | 500 |
| L4-15 | `application/inbox_processor.py`, `order_service/submit.py::apply_fill` 위임, `test_inbox_processor.py`, `test_duplicate_delivery_storm.py` | 08,14 | 1000회 중복 → fills 1행; tick의 `_handle_pending_fill_check`가 inbox 경유해도 FSM 전이 동일 | 450 |
| L4-16 | `application/unknown_resolver.py`, `order_service/reconcile.py` 위임, `test_unknown_resolver.py` | 13,15 | NOT_FOUND 2회+120s → FAILED; 상한 → safety control | 400 |
| L4-17 | `application/{cancel_order,modify_order}.py`, `order_service/{cancel,modify}.py` 래퍼, 테스트 | 14 | 부분체결 후 취소 정합; NH `supports_modify=False` 거부 | 450 |
| L4-18 | `application/restart_recovery.py`, `execution_loop/recovery_wiring.py` 연결, `main.py`(PM), `test_restart_recovery.py`, `test_crash_between_send_and_commit.py` | 16 | lease 만료 복구; 복구 중 submit 거부; 500건 ≤ 60 s | 450 |
| L4-19 | `exchanges/common/ws_session.py`, `bitget/ws_parsers.py` 분할, `bitget/market_data_mixin.py` 교체, `test_ws_session.py` | 11 | 하트비트·ack·seq 갭·resync 테스트; 기존 `test_bitget_websocket.py` 통과 | 650 |
| L4-20 | `bitget/private_ws_mixin.py`(orders/fill → inbox), `wiring.py` 구독 등록 | 15,19 | MockTransport 픽스처(문서 추정 표기)로 이벤트→FILLED 종단 | 300 |
| L4-21 | `kis/adapter.py`(transport·토큰 Lock·401 재발급), `kis/trading_mixin.py`(역조회), `nh/adapter.py`(transport), `test_kis_durability.py` | 12,16 | 토큰 동시 10회 → 1회; F5-b 후보 2개 → ESCALATE | 500 |
| L4-22 | `exchanges/paper/{fill_model,fee_model,latency_model,venue_profile}.py`, 단위 테스트 | 04 | 슬리피지 부호·부분체결 극단 | 400 |
| L4-23 | 마이그레이션 `<rev3>`, `exchanges/paper/{ledger_repository,simulator_adapter}.py`, `test_paper_simulator_adapter.py` | 13,22 | DROP 주입 → UNKNOWN 종단; 재시작 후 잔고 보존; `is_sandboxed=True` 상수 | 550 |
| L4-24 | `application/three_way_reconciler.py`, `application/reconcile_scheduler.py`, `wiring.py`, `test_three_way_reconciler.py` | 05,13,15 | REC-001/002/003/006; MATERIAL → 이후 submit DENY | 550 |
| L4-25 | 마이그레이션 `<rev2>`, `domain/algo_slicer.py`, `application/algo_executor.py`(TWAP만), `test_algo_slicer.py`, `test_algo_executor_twap.py` | 09,24 | Σ=total 정확; kill switch → PAUSED; VWAP 거부 | 650 |
| L4-26 | `application/order_query.py`, 라우터는 별도 L4(API 명세) — 여기서는 서비스 함수까지 | 07 | tenant 격리 테스트(`test_cross_tenant_isolation.py`) | 250 |
| L4-27 | 관측성: 메트릭 헬퍼 호출 삽입(§7.2 전부), 로그 필드(§7.3), `tests/unit/oms/test_metrics_names.py`(네이밍 규칙 정규식) | 14~25 | 메트릭명 전부 `aios.<ctx>.<subject>.<verb>` 매칭 | 300 |
| L4-28 | `tests/perf/oms/*` 4개(단언 포함) | 27 | p99 ≤ 50 ms 등 §7.1 수치 단언 | 300 |
| L4-29 | `tests/adversarial/oms/test_live_mode_bypass_attempts.py`, `test_tampered_provider_event.py` | 23,15 | 3경로 100% 차단 | 200 |
| L4-30 | `tests/e2e/bitget_demo/*`(키 확보 후), 프로파일 `verified` 갱신, 이 문서 §10 갱신 | 20 | Demo 왕복 1회, `paptrading` 스팟 유효성 확정 | 200 |

리프 순서 근거: 감사 §11 "넓히지 말고 잇고, 이은 것을 증명하라" — 도메인·DB
강제(01~08) → 제출 경로 교체(09~10) → 어댑터 내구성(11~13) → 워커(14~18) →
WS(19~20) → 타 거래소·시뮬레이터(21~23) → 대사·알고(24~25) → 관측·증명(26~30).

---

## 10. 미확정·리스크

| # | 항목 | 상태 | 영향·처리 |
|---|---|---|---|
| U1 | Bitget `paptrading: 1`이 **스팟** 데모에 유효한지 | **미확인**(문서상 USDT-FUTURES만 확인, 감사 §2 P0) | L4-30 전까지 PAPER 실행의 기본 어댑터는 `paper_sim`(L4-23)으로 전환 — factory 기본값 변경은 PM 결정 |
| U2 | Bitget `clientOid` 최대 길이·허용 문자 | **미확인** | 프로파일 `max_len=40` 가정, `verified="DOC_ONLY"`. 초과 시 `DUPLICATE_CLIENT_ID`/`INVALID_ORDER`로 관측 가능 |
| U3 | Bitget private WS login 서명 방식·`orders` 채널 필드명 | **미확인**(02b §6 자인) | L4-20 픽스처는 커뮤니티 SDK 추정. 실패 시 폴링 경로(inbox `source=POLL`)가 정상 경로 |
| U4 | Bitget 오류 코드 표(잔고 부족·최소수량 등 코드값) | **미확인** — 확인된 것은 `40012`, `40037`뿐 | `error_codes.py`는 확인분만 매핑, 나머지 `UNKNOWN_RESPONSE(재시도 불가)`. 실키 후 확장 |
| U5 | Bitget WS 하트비트 정확한 규약(30초 `ping` 문자열) | 문서 근거만 | `HeartbeatSpec` 파라미터화 |
| U6 | KIS 주문번호(ODNO) 일자별 채번·자정 이후 미결 추적 | **미확인**(02번 §2.1 자인) | `id_policy=DAILY_SEQUENCE`, `provider_order_date` 컬럼. F5-b 매칭은 당일만 |
| U7 | KIS 토큰 발급 분당 1회 제한 수치 | 커뮤니티 근거 | Lock + 23h 캐시로 보수 대응 |
| U8 | NH 정정/취소/주문조회 엔드포인트 | **추정**(02e §3) | `supports_modify=False`, `supports_cancel="UNVERIFIED"` → 취소 실패 시 즉시 UNKNOWN 아님·`ORDER_NOT_FOUND`처럼 취급하지 않고 운영자 에스컬레이션 |
| U9 | NH WS 메시지 본문 포맷 | **미확인**(02e §4) | `supports_ws_orders=False` 유지, 폴링 |
| U10 | KIS/NH 레이트리밋 정확 수치 | 미확인(NH 초당 4~5회 SDK 기본값) | `rate_limits` 보수값, 429 관측으로 보정 |
| U11 | 제출 종단 p99 목표(KIS/NH 1500 ms) | 추정 | 실키 후 측정치로 §7.1 갱신 |
| U12 | `alembic heads` 단일 여부(감사 §2-B 직렬화 진행 중) | 착수 시 확인 | 다중이면 merge revision 선행 |
| U13 | `Order`/`OrderStatus` 동결 계약(공유접점 §2.3) — 필드 추가는 ADR-C 선례로 하위호환이나 **ADR 선기록** 필요 | 절차 | L4-06 착수 전 `ADR-2026-09-xx-oms-order-fields.md` 작성(DevEngine 측 전달 포함) |
| U14 | `core/db/conditional_write.py` 확장이 다른 세션 파일 영역과 충돌 | 조정 | PM 공지 후 착수(감사 §2-B 공통 규칙 1) |
| U15 | `apply_safety_control`이 PROVIDER/STRATEGY_DEPLOYMENT 범위를 처리하지 않음 | 기존 한계 | I12는 ACCOUNT 범위로 건다(paper_control이 처리하는 범위) |
| U16 | `tests/integration/test_order_service.py`의 "전송 실패 흔적 없음" 불변조건 폐기 | 의도된 변경(§5.3) | 기대값 교체는 L4-09 같은 커밋 |
| U17 | `08_test_plan`·`10_implementation_task_tree` 갱신 | 문서 후속 | L4-30 이후 |
| U18 | VWAP/POV/ICEBERG 실행 활성화 | 06번 §6.1 Phase 1 제외 | 계획기(순수)만 구현·테스트, 실행은 `OMS_ALGO_NOT_ENABLED`. 활성화는 06번 개정 후 |
