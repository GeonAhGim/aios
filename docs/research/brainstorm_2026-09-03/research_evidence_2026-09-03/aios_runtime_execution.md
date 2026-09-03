# AIOS 런타임/실행 아키텍처 현황 감사 (2026-09-03)

대상: `C:/aios/aios` (Python 3.11 / FastAPI / asyncpg / Alembic, 504 files, 279 test files)

---

## 1. 프로세스·워커 토폴로지

### 구현됨
- **단일 HTTP 프로세스가 모든 장기 실행 루프를 소유** — `src/main.py:43-107` (lifespan)이 `start_background_loops()`를 호출하고, `src/services/background_loops.py:68-183`이 5개 asyncio 태스크를 띄운다:
  - heartbeat 2초 (`background_loops.py:76-84`)
  - alert 평가 60초 (`background_loops.py:94-109`)
  - risk_guard 30초 (`background_loops.py:119-131`)
  - safety/circuit-breaker 재가동 10초 (`background_loops.py:167-177`)
  - execution_loop (`background_loops.py:146-158`, 주기는 `config/risk_policy.yaml:75-76` `interval_sec: 1.0`)
- **각 루프는 try/except로 주기 실패를 흡수**해 코루틴이 죽지 않는다 (`background_loops.py:102-107, 124-129, 174-175`).
- **Feature flag로 루프 on/off** — `AIOS_EXECUTION_LOOP_ENABLED`, `AIOS_STARTUP_RECOVERY_ENABLED` (`background_loops.py:46-50`).
- **실행 루프 스케줄러** — `src/services/execution_loop/scheduler.py:58-143`. 엔진 4종(Strategy/Portfolio/Risk/Executor)을 1회만 생성해 공유, 세마포어 4로 동시 tick 제한(`scheduler.py:48, 75`), 실행 1건 실패는 격리(`scheduler.py:128-133`).
- **별도 OS 프로세스 watchdog** — `src/watchdog_process.py:196-252`, `python -m src.watchdog_process`. heartbeat 파일 타임스탬프 + Postgres로만 통신(`watchdog_process.py:1-61`). 5초 폴링(`:85`). HALT/LIQUIDATE 시 `strategy_executions.paused_by='SAFETY_LAYER'` 일괄 UPDATE(`:92-112`). Split-Brain DB 단독장애 진단 시 강제조치 보류(`:162-170`).

### 부분 구현
- **CI** — `.github/workflows/quality.yml` 단 1개 파일, 3 job:
  - `verify`: gitleaks 시크릿 스캔(`:44-47`) → ruff(`:60`) → mypy strict(`:63`) → zone manifest 검증(`:67-68`) → pytest+coverage(`:74`). **커버리지 임계치 게이트 없음**(`:70-73` 주석에 명시).
  - `guards`: 외부 저장소 `GeonAhGim/aios-meta`의 Architecture/Security Guard를 pinned SHA로 실행(`:79-107`).
  - `frontend`: lint/build, vitest는 조건부(`:109-140`).
  - API 호환성 검사 job 없음.

### 없음
- **Celery / RQ / arq / Redis 일절 없음** — 전체 grep 결과 유일한 히트가 `src/core/event_bus/bus.py:6`의 "향후 RedisEventBus로 교체 가능" 주석뿐. `pyproject.toml:5-23` 의존성에도 없음.
- **DB 리스/하트비트/소유권 레코드 없음** — 배포 인스턴스 단위의 `lease`/`worker_id`/`advisory lock`/`FOR UPDATE SKIP LOCKED`가 실행 루프 경로에 전무. `scheduler.list_runnable()`(`scheduler.py:86-92`)은 조건 없이 `SELECT ... WHERE status='RUNNING' AND mode='PAPER'`만 한다 → **HTTP 프로세스를 2개 이상 띄우면 같은 실행을 동시에 tick한다**. 방어선은 `fsm_state` 조건부 UPDATE(`tick.py:87-107`) 하나뿐 — 중복 주문은 좁은 창에서 여전히 가능.
  - (참고) fence token은 `safety_fence`(`c7d4e1a9f052_foundation_risk_gate.py:54-58`), `paper_deployment.fence_token`(`e91a4c2b7d63:58`)에 존재하나 **레거시 실행 루프와 무관한 별도 컨텍스트**.
- **Dockerfile 없음** (repo 전체 검색 0건). `docker-compose.dev.yml`은 14줄, **postgres 컨테이너 하나뿐** — 앱 서비스 정의 없음, non-root/read-only fs/resource limit/healthcheck 전부 없음.
- **헬스 엔드포인트 없음** — `/health`, `/healthz`, `/readyz`, `/metrics` 전부 0건.

---

## 2. 주문 생명주기

### 구현됨
- **상태 전이표(순수 함수)** — `src/services/oms/domain/state_machine.py:68-113` `ALLOWED` 그래프 + `:117-134` `_SIMPLE_TRANSITIONS` + `:145-188` `next_status()`.
  - 상태: `CREATED / VALIDATED / SUBMITTED / ACKNOWLEDGED / PARTIALLY_FILLED / FILLED / REJECTED / CANCELLED / EXPIRED / FAILED / UNKNOWN`.
  - 터미널 5종 정의(`:58-66`), 터미널에서 어떤 이벤트도 거부(`:152-156`).
  - **부분체결 자기전이 허용** — `PARTIALLY_FILLED → PARTIALLY_FILLED`(`:89-96`, 30%→60%→100%).
  - **FILL 목적지 분기** — `filled_qty >= qty ? FILLED : PARTIALLY_FILLED`(`:170-181`).
- **client_order_id 원자적 선점(TOCTOU 수정)** — `src/services/order_service/submit.py:78-85`: 거래소 호출 **전에** `INSERT`로 claim, `UniqueViolationError`면 기존 행 반환. 전송 실패 시 claim 행 삭제(`:93-98`).
  - 스키마 근거: `orders.client_order_id VARCHAR(100) NOT NULL UNIQUE`(`210cc26533c7_orders.py:32`).
  - client_order_id 생성 규칙: `f"{execution_id}:{fsm_state}:{utc_isoformat}"`(`src/core/executor/executor.py:87-89`).
- **낙관적 동시성(compare-and-set)** — 모든 orders 갱신이 `expected_status`를 WHERE에 건다(`order_service/repository.py:100-131, 133-155`), 공용 헬퍼 `src/core/db/conditional_write.py:29-70` (`IS NOT DISTINCT FROM`, 실패 시 `ConcurrencyConflictError`).
- **UNKNOWN 처리** — `src/services/order_service/reconcile.py:29-79`: 최대 3회·2초 간격 `get_order()` 재조회, 실패 시 CRITICAL 로그로 human 개입 신호(`:73-78`).
- **체결가 캡처** — `apply_fill()`이 `filled_quantity`/`average_fill_price`를 거래소 재조회 결과로 반영(`submit.py:128-166`), 동기 체결 경로는 `place_order` 응답 그대로(`submit.py:100-105`).
- **재시작 복구 실배선** — `src/services/execution_loop/recovery_wiring.py:51-110` + 순수 오케스트레이터 `src/core/event_bus/recovery.py:29-64`. 비종결 3상태 조회(`:43, 57-65`) → 거래소 재조회 → **FILLED는 DB에 쓰지 않고**(tick의 `_handle_pending_fill_check`가 유일한 체결 반영 경로, `:14-18`) CANCELLED/REJECTED/EXPIRED/FAILED만 영속화(`:75-81`) → 이벤트 재발행 → audit_log 기록(`:94-103`).
- **취소** — `src/services/order_service/cancel.py:22-58`. 이미 체결된 주문 취소 실패는 오류가 아닌 no-op 반환(`:39-42`).
- **PENDING 갇힘 방지** — 주문이 REJECTED/CANCELLED/EXPIRED/FAILED로 끝나면 `fsm_state`를 이전 상태로 복귀(`tick.py:141-156`).
- **LIVE 하드 차단(2중)** — `executor.py:71-85`: `mode != "PAPER"` → `FrozenZoneLiveModeBlockedError`, `adapter.is_paper_trading/is_sandboxed` 미증명 → `FrozenZonePaperAdapterBlockedError`. 확장 어댑터 메서드용 데코레이터 `src/exchanges/common/live_guard.py:33-46`.

### 설계만 존재
- **`src/services/oms/*` 전체가 미배선 병렬 섬** — `contracts/v1_commands|v1_events|v1_views`, `domain/{state_machine, idempotency, fill_normalizer, algo_slicer, reconcile_rules, rounding, symbol_registry, venue_profile}`, `ports/repository.py`. grep 결과 **OMS 패키지 밖에서 import하는 프로덕션 코드가 0건**. 실제 실행 경로는 `order_service/*`가 담당하며 `state_machine.next_status()`를 부르지 않는다.
  - `ports/repository.py:29-45` `OutboxRow`(PENDING/SENDING/DONE/RETRY/DEAD, `lease_until`, `worker_id`, `attempt`, `not_before`) — **Protocol만 정의, 어댑터도 마이그레이션도 없음**(`:1-11` docstring이 "L4-06 마이그레이션 미착수"라고 명시).
  - `domain/idempotency.py:19-50` tenant/account/provider/strategy/version/window 스코프 해시 — 미사용. 실제로는 전역 `client_order_id` UNIQUE 하나.
- **`resolve_unknown()` 호출자가 테스트뿐** — `src/services/order_service/reconcile.py`를 부르는 프로덕션 코드 0건 (`__init__.py:11` re-export + `tests/integration/test_order_service.py`만). tick은 UNKNOWN을 별도로 다루지 않는다.

### 없음
- **DB 레벨 전이 강제 없음** — `orders.status`에 CHECK 제약도 트리거도 없다(`210cc26533c7_orders.py:26-50`은 `side`에만 CHECK). 전이 규칙은 순수 Python(그것도 미배선 모듈)에만 있다.
- **outbox / inbox / order_events / command 테이블 없음** — 54개 마이그레이션 중 해당 DDL 0건.
- **부분체결 누적 처리 경로 없음** — tick은 `reconfirmed.status != "FILLED"`면 즉시 return(`tick.py:161-163`). PARTIALLY_FILLED가 DB에 기록되거나 포지션에 반영되는 실행 경로가 없다.
- **modify 경로 미배선** — `order_service/modify.py` 존재하나 호출자 없음.

---

## 3. 거래소/브로커리지 추상화

### 구현됨
- **ExchangeAdapter ABC** — `src/exchanges/common/adapter.py:22-95`. `is_paper_trading`/`is_sandboxed` 2개 독립 프로퍼티(`:23-43`), `get_capabilities()`, market data 4종, account 4종. **출금 메서드 의도적 부재**(`:1-9`, "Trading Permission ≠ Withdrawal Permission").
- **Capability 모델** — `src/exchanges/common/types.py:28-50`: `supported_asset_classes`, `supports_*`, `max_leverage`, `reference_feed_coverage`, `has_official_sandbox`, `market_hours`, `min_order_size`/`tick_size` dict.
- **정규화된 마켓데이터 계약** — `src/data/models/market_data.py:13-80`: `Ticker`(+`source_type: primary|reference`, `:22`), `Candle`, `OrderBook`, `SpotSymbolInfo`, `PublicTrade`. 전 어댑터가 이 pydantic 모델로 반환 → 정본 계약 존재.
- **어댑터 팩토리** — `src/exchanges/factory.py:24-59`, 거래소별 extra 필드(bitget passphrase / kis cano+acnt_prdt_cd / nh act_no) 해체. `demo_mode=True` 기본값.
- **InstrumentedAdapter** — `src/exchanges/common/instrumented_adapter.py:47-70`: `__getattr__` 위임 프록시로 코루틴 메서드만 성공/실패 계측(`ApiCallTracker`). 프로퍼티는 그대로 통과(live_guard가 읽어야 하므로, `:19-23`). `main.py:80-84`에서 `CredentialResolver`에 주입 — 시세/잔고/주문 호출이 전부 자동 계측.
- **WS 세션 관리(어댑터 내부)** — Bitget: 공용 재연결 루프 + 지수 백오프 + `on_reconnecting`/`on_reconnected` 훅(`src/exchanges/bitget/market_data_mixin.py:244-281`), 구독 메시지를 팩토리로 매 재연결마다 재생성(`:255-262`). KIS: PINGPONG pong 응답, 파이프/캐럿 파싱, 체결통보 AES-256-CBC 복호화(`src/exchanges/kis/websocket_mixin.py:1-40, 327+`).

### 부분 구현
- **NH 어댑터** — `subscribe_ticker_stream`이 "연결/구독은 구현됨, 데이터 프레임 스키마 미확인"으로 남아 있음(`src/exchanges/nh/market_data_mixin.py:112-125`, `src/exchanges/nh/adapter.py:160, 194`).

### 없음
- **WS 스트림 소비자 없음** — `subscribe_ticker_stream` 구현체는 3개지만 프로덕션 호출부 0건. 실행 루프는 전적으로 REST 폴링(`tick.py:217` `adapter.get_ohlcv(symbol,"1m",limit=100)`, `:259` `get_balance()`) → **1초 tick마다 거래소 REST 왕복**.
- **백테스트/라이브 패리티 장치 없음** — `src/foundation/backtest/*`는 별도 컨텍스트로, 실행 루프와 동일 엔진을 공유하지 않는다.
- **주문 유형 다양성 없음** — Executor가 항상 `OrderType.MARKET` 하드코딩(`executor.py:98`).

---

## 4. 대사(Reconciliation) / 포지션 원장

### 구현됨
- **포지션 원장 쓰기 경로** — `src/services/order_service/position_ledger.py:36-116`. 호출 2곳: `submit.py:111`(동기 체결), `submit.py:154`(`apply_fill`). BUY=신규 오픈(`:57-84`), SELL=전량 청산 + realized_pnl 계산(`:87-116`).
- **포지션 저널 보존** — `positions`는 청산 시 삭제하지 않고 `quantity=0, closed_at=now()`(`c8ead41fd624_positions.py:47-50` 주석 + `position_ledger.py:107-114`).
- **Foundation 3-way 대사 도메인** — `src/foundation/reconciliation/domain/rules.py:26-66`: `classify_item()`(HEALTHY / MINOR_DIFFERENCE / MATERIAL_MISMATCH / PROVIDER_UNAVAILABLE), 심각도 순 집계(`:16-23, 46-53`), 입력 해시 기반 중복 실행 dedupe(`:56-66`).
- **대사 → kill switch 연동** — `application/run_reconciliation.py:80-120+`: MATERIAL_MISMATCH/PROVIDER_UNAVAILABLE 시 `activate_safety_control`을 `STRATEGY_DEPLOYMENT` 범위로 호출. connection이 unhealthy면 항목 분류 전에 전체 PROVIDER_UNAVAILABLE(**0으로 가정하지 않음**, `:110-116`).
- **테이블** — `reconciliation_run`/`reconciliation_item`/`reconciliation_state`(`f2b8e5d1a734:56-109`), 레거시 `reconciliation_events`(`0ff10faffd25:24-47`).

### 부분 구현
- **레거시 에스컬레이션 서비스** — `src/core/safety/reconciliation.py:46+` `record_and_escalate()`: 1시간 3회→RESTRICTED, 24시간 5회 또는 단일 10% 초과→HALTED(`:35-38`). **프로덕션 호출자 0건** (테스트 `tests/integration/test_reconciliation.py:42,47`만).

### 설계만 존재
- **`run_reconciliation`의 입력 조립부 없음** — 내부/provider 값을 전부 호출자가 `EntitySnapshot`으로 넘겨야 하며(`run_reconciliation.py:5-13` docstring이 명시), 유일한 호출자는 HTTP 라우터 `src/api/routers/foundation/reconciliation.py:48-60`. **주기적으로 도는 대사 워커가 없다.**
- **`src/services/oms/domain/reconcile_rules.py`** — "주문 vs 거래소 주문 vs 체결·잔고" 3자 비교 규칙(`:1-19`), 미배선.

### 없음
- **mark-to-market 갱신 없음** — `positions.unrealized_pnl`은 항상 0 (`watchdog_process.py:25-29`가 명시적으로 인정).
- **분할 청산 미지원** — Phase 1 가정: 실행당 종목 1개, 전량 청산만(`position_ledger.py:20-24`).

---

## 5. 이벤트 원장 / 내구성

### 구현됨
- **InProcessEventBus** — `src/core/event_bus/in_process.py:47-140`. topic별 `asyncio.Queue(maxsize=1000)`, topic당 워커 코루틴, criticality별 재시도(최대 5회, 초기 1초, `:35-37`), 백프레셔 지속 시 `event_bus.queue.backpressure_sustained` 발행(`:39-40`), 워커 루프 예외 흡수(`:121-129`).
- **핸들러 예외 → audit_log** — `main.py:52-68`이 `_event_bus_audit_sink`를 주입해 `audit_log` 테이블에 실기록.
- **WORM 테이블 2종**:
  - `audit_log` — `9ec8a1ee28d7:32-52`. `REVOKE UPDATE, DELETE ... FROM PUBLIC`(`:47`). **해시 체인 없음**. 마이그레이션 docstring(`:14-18`)이 "소유자에게는 REVOKE가 안 먹으므로 별도 DB 역할 분리 필요, 아직 없음"을 정직하게 명시.
  - `foundation_audit_event` — `4453afe74725:41-88`. `sequence_no` + `previous_hash` + `event_hash` + `payload_hash` **해시 체인**, tenant별/system별 partial unique index(`:71-79`), `trace_id UUID NOT NULL`, `classification` 5단계, WORM REVOKE(`:87`).
- **correlation id** — HTTP 경로: `RequestIdMiddleware`(`src/api/middleware/request_id.py:29-41`, `X-Request-ID` 수용/생성/응답 반영) → contextvar → `JSONLinesFormatter`가 로그마다 자동 주입(`src/core/logging/schema.py:43-58`).
- **Postgres가 유일한 권위 상태** — Redis/캐시 계층 없음. "DB 쓰기가 이벤트 발행보다 먼저"(`recovery.py:38-44`, `submit.py:100-106`) 원칙이 코드에 반영됨.

### 부분 구현
- **replay 능력** — 재시작 시 `order.status.changed` 재발행만 가능(`recovery_wiring.py:91-92`). 임의 시점 replay를 위한 이벤트 저장소는 `foundation_audit_event`뿐이며 레거시 실행 경로는 여기 쓰지 않는다(`4453afe74725:19-25`가 명시).
- **causation id 없음** — `trace_id`(foundation)와 `correlation_id`(로깅)만 존재. `causation_id` 필드 0건.

### 없음
- **트랜잭셔널 outbox 없음** — 이벤트 발행이 DB 커밋과 별도(`submit.py:102-123`). 프로세스가 커밋 직후 죽으면 그 이벤트는 재시작 복구가 커버하는 범위(orders 비종결 상태) 밖에서는 소실.
- **프로세스 경계를 넘는 이벤트 전달 없음** — `in_process.py:48-49`("단일 프로세스 내에서만"). watchdog_process가 알림을 직접 발행하지 못하고 `paused_by` DB 변경 + audit_log로만 사실을 남긴다(`watchdog_process.py:55-60` — 아웃박스 폴러가 후속 과제라고 명시).

---

## 6. 리스크 / 안전 런타임

### 구현됨
- **RiskEngine 9개 지표 순차 검사** — `src/core/risk/engine.py:33-215`, `checked.append` 기준: `daily_loss`(:58), `max_drawdown`(:73), `leverage`(:94), `position_concentration`(:109), `strategy_allocation`(:127), `var`(:151), `correlation_risk`(:164), `trade_frequency`(:180), `safety_state`(:199). **입력값 하나라도 없으면 즉시 거부**(판단 불가 ≠ 승인, `:56-58` docstring 및 각 지표의 `_data_unavailable` 분기).
- **Circuit Breaker 5단계** — `src/core/safety/circuit_breaker.py:41-58`. warning/restricted만 자동 하향, **halted/emergency는 절대 자동 하향 없음**(`:57`), 인간 승인 워크플로(ApprovalService, PLATFORM scope, 180초 하한) 경유(`:10-16`). 상태는 `system_safety_state` 단일 행 테이블에 영속(`:5-8`).
- **지표 수집 실배선** — `src/core/safety/metrics_collector.py:32-80+`: `ApiCallTracker`(100회 롤링 윈도우, `error_rate_pct`, `seconds_since_last_success`), `_order_reject_rate_pct`(orders 60분 창 SQL). `main.py:80` 생성 → `background_loops.py:171-173`이 `evaluate(metrics)` + `check_reactivation()`을 같은 10초 주기에 순차 실행.
- **equity_tracker 영속화** — `src/services/execution_loop/equity_tracker.py:26-60+`. `seed()`로 DB 기준점 주입, `strategy_executions.equity_day_start_date/value/peak_value`에 write-through(`:8-16`, 마이그레이션 `4747bb11f733_execution_equity_baseline.py`). 재시작해도 "오늘 손익" 기준점이 유실되지 않음.
- **DataDistrust 4단계 판정** — `src/core/safety/data_distrust.py:41-46`: NORMAL / SUSPICIOUS / DISTRUSTED / DEGRADED_SINGLE_SOURCE. 3소스 쿼럼 중앙값 편차 히스테리시스(1.5%/0.75%/60초) + 실현변동성 5배 통계 검사(`:11-17`). DB 영속화·복원 `src/services/safety/distrust_wiring.py:33-45+`(`(exchange,symbol)` UPSERT, 레벨 변경 시에만 `since` 갱신).
- **tick 내 다중 안전 재확인** — `paused_by` 재확인(`tick.py:309-319`, 신호 평가 도중 watchdog 개입 감지), FSM 전이 전 게이트 검사(`:326-333`), FSM 조건부 전이 충돌 시 포기(`:336-346`).
- **fence token 인프라** — `safety_fence(scope, scope_ref, token)` PK(`c7d4e1a9f052:54-58`), `paper_deployment.fence_token`(`e91a4c2b7d63:58`), `paper_order_intent.fence_token_at_submit`(`:89`). `increment_fence()`가 상태 전이+토큰 증가를 단일 UPDATE로(`paper_control/adapters/postgres_repository.py:207-215`). 소비자: `apply_safety_control.py:68`, `pause_deployment.py:89,127,145`, 검증부 `submit_paper_intent.py:58-59`.

### 부분 구현
- **`FrozenZone` 이중 가드**는 견고하나, 확장 어댑터 메서드(convert/grid/margin/futures/loan/subaccount)는 `require_paper_sandbox` 데코레이터 1중 방어만(`live_guard.py:9-18`이 한계를 명시).

### 없음 — **가장 심각한 배선 누락**
- **kill switch가 레거시 실행 경로를 막지 못한다.** `src/services/order_service/foundation_gate.py:43-94` `make_foundation_pre_submit_gate()`가 GLOBAL/TENANT/ACCOUNT/PROVIDER 범위 활성 safety control을 DENY하도록 완성돼 있으나, **프로덕션 호출자가 0건**이다:
  - `background_loops.py:146-151`이 `ExecutionLoopScheduler(...)`를 `pre_submit_gate` 없이 생성 → `scheduler.py:74`에 `None` → `tick.py:326-333` `is_submission_allowed(None, ...)` → `pre_submit_check.py:36-37`에서 무조건 통과.
  - `ExecutionService`도 두 생성 지점 모두 `pre_start_gate` 미주입(`src/api/execution_deps.py:21`, `background_loops.py:115`).
  - 결과: **`safety_control`을 ACTIVE로 올려도 실행 루프의 신규 주문이 그대로 나간다.**
- **DataDistrust도 실행 루프에서 꺼져 있다.** `tick.py:196-197`의 `distrust_monitor`가 scheduler에서 전달되지 않아 항상 `None` → `tick.py:224-235` 블록 스킵 → `distrust_level`이 영구히 NORMAL. `DataDistrustMonitor(...)` 인스턴스화가 프로덕션 코드에 0건.
- **fence token을 소비하는 실행 경로 없음** — paper_control 컨텍스트 내부(HTTP 라우터 트리거)에서만 순환하며, `execution_loop`/`order_service`는 fence를 읽지도 검증하지도 않는다.

---

## 7. 관측성

### 구현됨
- **구조화 로깅(JSON Lines)** — `src/core/logging/schema.py:32-70`. `LogEntry{timestamp, level, module, event_type, correlation_id, message, extra}`. `main.py:47`에서 실제 활성화(파일 docstring `:45-46`이 "이전엔 호출자가 없어 운영에서 한 번도 활성화되지 않았다"고 기록).
- **로그 레벨 사용 기준 문서화** — `schema.py:9-18` (CRITICAL = Watchdog 발동/CB 거래중지/Kill Switch, audit_log 동시 기록 원칙).
- **audit_log 실기록 경로** — `record_audit_log()` 소비자: event bus sink(`main.py:56-63`), watchdog 조치(`watchdog_process.py:98-109`), 재시작 복구(`recovery_wiring.py:96-103`), 무권한 제출(`foundation_gate.py:69-78`).

### 없음
- **Prometheus / OpenTelemetry / metrics 엔드포인트 전무** — `prometheus_client`, `Counter(`, `Histogram(`, `/metrics` 전부 0건. `pyproject.toml` 의존성에도 없음.
- **알림 실발송기 없음** — `NotificationGateway`가 senders 없이 등록되어 "발송 실패"로 정직하게 기록만 됨(`main.py:65-69`).
- **`data_delay_sec` 관측 지점 없음** — `metrics_collector.py:14-18`이 항상 0을 반환한다고 명시. CB 5개 지표 중 3개(`api_error_rate_pct`, `api_disconnect_sec`는 tracker로 수집되나 `data_delay_sec`)가 미측정.
- **로그 수집기 미연결** — `schema.py:7-8`("Datadog/Loki 등은 팀 확정 후").

---

## Gap Summary (비교 기준별)

### vs QuantDinger (API/worker/scheduler 분리, DB 리스, 프로덕션 하드닝)
| 항목 | AIOS 현황 |
|---|---|
| 프로세스 분리 | **없음.** uvicorn 1 프로세스가 HTTP + 5개 트레이딩 루프를 겸함. 유일한 분리는 watchdog(감시 전용). |
| 큐/브로커 | **없음.** Celery/RQ/Redis 0건. `asyncio.Task` 직접 생성. |
| 내구성 리스/하트비트 | **없음.** heartbeat는 파일 타임스탬프 1개(`DEFAULT_HEARTBEAT_PATH`), DB 소유권 레코드 없음. → **다중 인스턴스 배포 시 중복 tick·중복 주문 위험.** |
| 커맨드/이벤트 테이블 | outbox/inbox는 `oms/ports/repository.py`에 Protocol만. DDL 없음. |
| 컨테이너 하드닝 | **Dockerfile 자체가 없음.** compose는 postgres 단독, non-root/read-only/limits 전무. |
| CI | 1 workflow / 3 job. gitleaks·ruff·mypy strict·pytest·외부 guard는 있으나 커버리지 게이트·API 호환성 검사 없음. |

### vs LEAN (주문 생명주기, 브로커리지 인터페이스, transaction handler, 백테스트/라이브 패리티)
| 항목 | AIOS 현황 |
|---|---|
| 상태 머신 | 11상태 전이표 완성(`state_machine.py`)이나 **실행 경로가 이를 호출하지 않음.** DB CHECK/트리거도 없어 전이 규칙이 런타임에 강제되지 않는다. |
| 브로커리지 ABC | 견고(`common/adapter.py`, capability model, 출금 배제, live_guard). |
| transaction handler | 부재. `submit.py`가 claim→전송→갱신을 직접 수행. 재시도·큐잉 없음(`:90-92`가 "자체 재시도 안 함" 명시). |
| 부분 체결 | 상태표는 지원, **실행 경로는 FILLED만 처리**(`tick.py:161-163`). |
| 주문 유형 | MARKET 하드코딩(`executor.py:98`). |
| 백테스트/라이브 패리티 | `foundation/backtest`는 실행 루프와 엔진 비공유. |

### vs Freqtrade (영속성/복구, dry-run 스위치)
| 항목 | AIOS 현황 |
|---|---|
| 재시작 복구 | **양호.** 비종결 주문 재조회 + FILLED 이중처리 회피 + 이벤트 재발행 + audit 기록(`recovery_wiring.py`). equity 기준점도 DB write-through. |
| dry-run 스위치 | **더 강함.** `mode` + `is_paper_trading` + `is_sandboxed` 3중 fail-closed. LIVE는 코드 레벨 하드 차단. |
| 영속 상태 권위 | Postgres 단일 권위, 캐시 계층 없음 — 명확. |
| 미실현 손익 | **없음** (mark-to-market 경로 부재 → 손실 감시가 청산 시점에만 반응). |

### 최우선 리스크 3건
1. **kill switch 미배선** — `make_foundation_pre_submit_gate()` 호출자 0건. 안전 통제가 ACTIVE여도 실행 루프 주문이 통과(`background_loops.py:146-151`).
2. **DataDistrust 미배선** — `distrust_monitor=None` 고정으로 이상 시세 방어가 런타임에 비활성(`tick.py:224-235`).
3. **단일 프로세스 + 리스 부재** — 수평 확장 불가. 스케일아웃 시 `scheduler.list_runnable()`이 같은 실행을 중복 tick한다.
