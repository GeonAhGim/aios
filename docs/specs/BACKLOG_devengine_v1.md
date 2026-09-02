# DevEngine 백로그 v1 — L4 §9 미배정 리프

**작성**: 2026-09-03, DevEngine 세션(agent-platform-6a, PM task-109)이 5개 L4
명세서(`docs/specs/L4_*_v1.0.md`)의 §9 리프 목록을 `C:\aios\pm\tasks\*.json`의
실시간 배정 현황과 대조해, 아직 아무도 진행 중이 아닌 SCAFFOLD/OPEN/★리프만
추출했다. FROZEN(★ 없는 순수 FROZEN)은 전부 제외했다 — DevEngine의
Capability Token이 애초에 발급을 거부한다.

**형식**: 각 항목은 DevEngine `POST /devengine/v1/issues`가 받는
`leaf_spec`(자유문)/`target_repository`/`paths`/`assigned_agent` 스키마에
맞춰 정리했다. Anthropic 크레딧 확보 시 이 파일을 그대로 사람이 복사해
등록하거나, 향후 파일 일괄등록 기능이 생기면 기계적으로 파싱 가능하도록
일관된 구조를 유지했다.

**주의**: 여기 나열된 의존관계(Dep)는 §9 원문 기준이다 — 실제 착수 전에
`C:\aios\pm\tasks\`의 최신 상태로 선행 리프 완료 여부를 다시 확인해야
한다(이 파일은 2026-09-03 스냅샷).

**target_repository**: 전부 `GeonAhGim/mihwa-aios` (별도 표기 없으면 동일)

---

## 1. L4_execution_oms_and_exchange_v1.0.md (L4-01~09 제외 — OMS domain, agent-platform-44 진행 중)

### L4-10 ★FROZEN_PAPER_ONLY — PM 승인 필요 (아래 §A 참조)

### L4-11. Exchange 내구성 기반 5모듈
- **paths**: `src/exchanges/common/{error_taxonomy,http_policy,rate_limiter,circuit_breaker,clock_sync}.py`
- **leaf_spec**: 에러 분류(error_taxonomy), 전지터 재시도/백오프(http_policy), 토큰버킷 rate limiter, 서킷브레이커, 서버-클럭 오프셋 동기화(clock_sync) 5개 모듈을 신설한다.
- **dod**: 알 수 없는 HTTP/거래소 에러 코드는 `retryable=False`로 분류돼야 한다.
- **negative_test**: 미지 에러코드 입력 시 retryable=True로 잘못 분류되면 실패.
- **dep**: 없음(기반 모듈).

### L4-12. Bitget ResilientTransport 통합
- **paths**: `src/exchanges/common/transport.py`, `src/exchanges/bitget/error_codes.py`, `src/exchanges/bitget/adapter.py`
- **leaf_spec**: L4-11의 5개 모듈을 `ResilientTransport`로 조립하고, Bitget adapter의 `_request`가 이걸 거치도록 하며 서버-보정 클럭으로 서명한다.
- **dod**: 429/5xx/비-JSON/서명-타임스탬프 케이스 테스트 통과, 기존 Bitget adapter 테스트 무손상.
- **negative_test**: 429 응답 시 재시도 없이 즉시 실패하면 실패.
- **dep**: L4-11.

### L4-13. Adapter ABC 확장 + factory 가드
- **paths**: `src/exchanges/common/adapter.py`, `src/exchanges/bitget/trading_mixin.py`, `src/exchanges/factory.py`
- **leaf_spec**: adapter ABC에 `get_open_orders/get_fills/find_order_by_client_id/venue_profile/subscribe_order_stream` 기본 구현 추가, Bitget에 client-id 조회+`get_fills(since=)` 추가, factory에 nh/paper_sim 추가 + `demo_mode=False`는 환경변수 없이 차단.
- **dod**: 기존 adapter 테스트 통과 + `demo_mode=False` 차단 테스트.
- **negative_test**: 환경변수 없이 `demo_mode=False` 요청 시 통과하면 실패.
- **dep**: L4-04(진행 중, 완료 후 확인), L4-12.

### L4-14. Outbox dispatcher
- **paths**: `src/application/outbox_dispatcher.py`, `src/wiring.py`(dispatcher 부분만)
- **leaf_spec**: `SKIP LOCKED`로 outbox 행을 선점해 adapter 호출, `SentUnknownError`는 재전송 없이 `UNKNOWN`으로.
- **dod**: SentUnknown→UNKNOWN 정확히 1회 호출, 동시 워커 3개가 각자 정확히 1번씩만 전송.
- **negative_test**: 동시 워커 3개가 같은 주문을 2번 이상 전송하면 실패.
- **dep**: L4-09(진행 중), L4-13.

### L4-15. Inbox processor
- **paths**: `src/application/inbox_processor.py`, `src/order_service/submit.py`(apply_fill 위임)
- **leaf_spec**: provider 이벤트를 fill/전이로 처리하며 inbox 레벨 중복제거.
- **dod**: 동일 이벤트 1000회 중복 투입 → fills 행 1개만 생성.
- **negative_test**: 중복 이벤트가 fills를 2개 이상 만들면 실패.
- **dep**: L4-08(진행 중), L4-14.

### L4-16. Unknown order resolver
- **paths**: `src/application/unknown_resolver.py`, `src/order_service/reconcile.py`(위임)
- **leaf_spec**: client-id 조회 또는 open/history 역매칭으로 UNKNOWN 주문 해소, 재시도 상한 초과 시 안전장치로 에스컬레이션.
- **dod**: NOT_FOUND×2 + 120초 → FAILED, 상한 초과 → 안전장치 ACTIVE.
- **negative_test**: 상한 초과했는데 안전장치가 발동 안 하면 실패.
- **dep**: L4-13, L4-15.

### L4-17. Cancel/Modify order
- **paths**: `src/application/{cancel_order,modify_order}.py`, `src/order_service/{cancel,modify}.py`(wrapper)
- **leaf_spec**: 취소는 CANCEL_REQUESTED+outbox 전이, 정정은 LIMIT 전용이며 거래소가 네이티브 정정 미지원 시 자동 cancel-replace.
- **dod**: 부분체결 후 취소 정확성, NH `supports_modify=False` 거부.
- **negative_test**: NH에서 modify 요청이 거부 안 되면 실패.
- **dep**: L4-14.

### L4-18. 재시작 복구 (PM 배선 필요)
- **paths**: `src/application/restart_recovery.py`, `src/execution_loop/recovery_wiring.py`, `src/main.py`(PM)
- **leaf_spec**: 시작 시 만료된 outbox 리스는 UNKNOWN 처리, UNKNOWN 해소, 비종결 주문 RESYNC, 복구 중 신규 제출 거부.
- **dod**: 리스만료 복구 동작, 복구 중 제출 거부, 주문 500건 ≤60초.
- **negative_test**: 복구 중 제출이 거부 안 되면 실패.
- **dep**: L4-16.

### L4-19. WS 세션 생명주기
- **paths**: `src/exchanges/common/ws_session.py`, `src/exchanges/bitget/ws_parsers.py`, `src/exchanges/bitget/market_data_mixin.py`
- **leaf_spec**: 하트비트, ack 검증, 시퀀스갭 재동기화, 재연결+재구독.
- **dod**: 하트비트/ack/시퀀스갭/재동기화 테스트 통과, 기존 Bitget WS 테스트 무손상.
- **negative_test**: 시퀀스갭 발생 시 재동기화 안 되면 실패.
- **dep**: L4-11.

### L4-20. Bitget private WS 주문/체결
- **paths**: `src/exchanges/bitget/private_ws_mixin.py`, `src/wiring.py`(구독 등록)
- **leaf_spec**: Bitget private 주문/체결 채널을 `ProviderOrderEvent`로 파싱해 inbox에 공급.
- **dod**: mock-transport fixture 이벤트→FILLED 종단 확인.
- **negative_test**: 파싱 실패 이벤트가 inbox에 잘못된 상태로 들어가면 실패.
- **dep**: L4-15, L4-19.

### L4-21. KIS/NH 어댑터 내구성
- **paths**: `src/exchanges/kis/adapter.py`, `src/exchanges/kis/trading_mixin.py`, `src/exchanges/nh/adapter.py`
- **leaf_spec**: KIS 토큰 발급을 `asyncio.Lock`으로 직렬화(401 시 1회 재발급), KIS UNKNOWN 역매칭, NH 전송 내구성.
- **dod**: 동시 발급 10회 → 실제 발급 1회, F5-b 2개 후보 → ESCALATE.
- **negative_test**: 동시 발급 10회가 실제로 10번 호출되면 실패.
- **dep**: L4-12, L4-16.

### L4-22. Paper 시뮬레이션 모델
- **paths**: `src/exchanges/paper/{fill_model,fee_model,latency_model,venue_profile}.py`
- **leaf_spec**: 슬리피지/부분체결 시뮬레이션, 메이커/테이커 수수료, 지연+응답손실 주입(UNKNOWN 경로 테스트용).
- **dod**: 슬리피지 부호 정확성, 부분체결 확률 극단값 검증.
- **negative_test**: 슬리피지 부호가 매수/매도 반대로 나오면 실패.
- **dep**: L4-04(진행 중).

### L4-23. Paper 거래소 전체 구현
- **paths**: migration(신규), `src/exchanges/paper/{ledger_repository,simulator_adapter}.py`
- **leaf_spec**: 영속화된 paper ledger 위에 `ExchangeAdapter` 전체 구현, `is_sandboxed=True`.
- **dod**: DROP 주입→UNKNOWN 경로 종단 확인, 재시작 후 잔고 유지.
- **negative_test**: 재시작 후 잔고가 사라지면 실패.
- **dep**: L4-13, L4-22.

### L4-24. 3-way 리컨실
- **paths**: `src/application/three_way_reconciler.py`, `src/application/reconcile_scheduler.py`, `src/wiring.py`
- **leaf_spec**: 내부-vs-provider 주문/체결/잔고 비교를 `foundation.reconciliation`으로 오케스트레이션, advisory lock 주기 실행.
- **dod**: REC-001/002/003/006 통과, MATERIAL_MISMATCH → 후속 제출 DENY.
- **negative_test**: MATERIAL_MISMATCH 이후에도 제출이 허용되면 실패.
- **dep**: L4-05(진행 중), L4-13, L4-15.

### L4-25. TWAP 슬라이서
- **paths**: migration(신규), `src/domain/algo_slicer.py`, `src/application/algo_executor.py`
- **leaf_spec**: 참여율 상한+지터 슬라이스 계획, TWAP만 실행 배선(VWAP/POV/ICEBERG는 거부).
- **dod**: Σ슬라이스=총량 정확 일치, 킬스위치→PAUSED, VWAP 요청 거부.
- **negative_test**: VWAP 요청이 거부 안 되면 실패.
- **dep**: L4-09(진행 중), L4-24.

### L4-26. 주문 조회 API
- **paths**: `src/application/order_query.py`
- **leaf_spec**: 테넌트 스코프 읽기전용 주문/타임라인/미체결 조회.
- **dod**: 교차 테넌트 격리 테스트.
- **negative_test**: 다른 테넌트 주문이 조회되면 실패.
- **dep**: L4-07(진행 중).

### L4-27. OMS 메트릭 계측
- **paths**: §7.2/§7.3 전 구간, `tests/test_metrics_names.py`
- **leaf_spec**: 명세된 모든 OMS/거래소 메트릭·로그 필드 삽입.
- **dod**: 모든 메트릭명이 `aios.<ctx>.<subject>.<verb>` 정규식과 일치.
- **negative_test**: 정규식 불일치 메트릭명이 있으면 실패.
- **dep**: L4-14~25.

### L4-28. OMS 성능 벤치마크
- **paths**: `tests/perf/oms/*`(4개, assertion 포함)
- **leaf_spec**: §7.1 수치 목표(p99 제출 ≤50ms 등) 어설션화.
- **dod**: CI에서 수치 assertion 통과.
- **negative_test**: p99 초과 시 assertion 실패해야 함(실패하지 않으면 이 리프 자체가 실패).
- **dep**: L4-27.

### L4-29. LIVE 우회/변조 방어 테스트
- **paths**: `tests/test_live_mode_bypass_attempts.py`, `tests/test_tampered_provider_event.py`
- **leaf_spec**: LIVE 모드 우회 3경로 전부 차단 검증, `filled_quantity>quantity` 변조 거부(DB CHECK 위반 없이).
- **dod**: 명시된 대로.
- **negative_test**: 우회 경로 중 하나라도 성공하면 실패.
- **dep**: L4-23, L4-15.

### L4-30. Bitget 데모 E2E
- **paths**: `tests/e2e/bitget_demo/*`, profile `verified` 갱신, §10 갱신
- **leaf_spec**: 실 데모키로 주문생성/조회/취소 왕복, spot `paptrading` 유효성 확인, private-WS 주문 로그인 확인.
- **dod**: 왕복 1회 성공, spot 유효성 확인.
- **negative_test**: 왕복 실패 시 이 리프 자체가 실패(실 API 키 필요).
- **dep**: L4-20.

---

## 2. L4_market_data_positions_ledger_v1.0.md (아무것도 배정 안 됨 — 61개 전부)

### 공통 기반

#### L0-1. 관측성 레지스트리
- **paths**: `src/core/observability/metrics_registry.py` + test
- **leaf_spec**: 프로세스 내 counter/gauge/histogram 레지스트리 + Prometheus 텍스트 렌더.
- **dod**: `render_text()` 파싱 가능, 라벨 불일치 시 `ValueError`.
- **negative_test**: 라벨 불일치가 예외 없이 통과하면 실패.

#### L0-2. Zone 순수성 강제
- **paths**: `tests/unit/test_zone_purity.py`, ruff `banned-api` 설정
- **leaf_spec**: `domain/**`가 `asyncpg|httpx`를 임포트하면 AST 레벨에서 항상 실패하도록 강제.
- **dod**: domain에서 I/O 임포트 시 재현 가능한 실패.
- **negative_test**: domain에 httpx import를 넣었는데 통과하면 실패.

#### L0-3. WORM 롤/트리거 생성기
- **paths**: `src/core/db/{append_only,roles}.py` + test
- **leaf_spec**: WORM(REVOKE+RAISE 트리거) SQL과 앱/마이그레이터 롤 권한 SQL을 생성.
- **dod**: SQL 스냅샷 일치, 테이블명에 `;` 포함 시 거부.
- **negative_test**: SQL 인젝션성 테이블명이 거부 안 되면 실패.
- **dep**: 없음.

#### L0-4. 트랜잭션 내부 감사이벤트 append
- **paths**: `src/foundation/evidence/adapters/postgres_repository.py`(`append_event_in(conn,...)` 추가) + test
- **leaf_spec**: 호출자의 기존 트랜잭션 안에서 감사이벤트를 추가할 수 있게(별도 커넥션 안 씀).
- **dod**: 외부 트랜잭션 롤백 시 이벤트도 함께 롤백.
- **negative_test**: 외부 롤백 후에도 이벤트가 남아있으면 실패.

#### L0-5. Migrator/App 롤 분리 + WORM 소급 적용
- **paths**: migration `4a1d0c0de001`, `src/api/routers/metrics.py` + test
- **leaf_spec**: `aios_migrator`/`aios_app` 롤 생성, `audit_log`/`foundation_audit_event`/`wallet_transactions`에 WORM 트리거 소급 적용, `/metrics` 노출.
- **dod**: 롤 존재, `aios_app`의 `audit_log` UPDATE 실패, `/metrics` 200.
- **negative_test**: `aios_app`으로 `audit_log` UPDATE가 성공하면 실패.
- **dep**: L0-1, L0-3.

### (A) 시장 데이터

#### LA-1. 시장데이터 계약 스키마
- **paths**: `src/market_data/contracts/v1.py` + schema test
- **leaf_spec**: Timeframe/Venue/CandleRecord/TickRecord/QualityIssue/Verdict/Ingest커맨드/CandleQuery/CandleSeries 등 Pydantic DTO.
- **dod**: 스키마 스냅샷, naive datetime → ValidationError.
- **negative_test**: naive datetime이 통과하면 실패.

#### LA-2. Timeframe 도메인
- **paths**: `src/domain/timeframe.py` + test
- **leaf_spec**: enum, duration, `align_open`, 세션 윈도 대비 `expected_opens`.
- **dep**: LA-1. **dod/negative_test**: §8.1 케이스 전부.

#### LA-3. 거래소 캘린더
- **paths**: `src/domain/calendar/{session_rules,known_venues}.py` + test
- **leaf_spec**: KRX/US/crypto 세션 윈도(조기마감/휴장/24×7 포함).
- **dep**: LA-1. **dod/negative_test**: DST, 조기마감, 휴장 케이스.

#### LA-4. OHLC 정합성/중복제거
- **paths**: `src/domain/quality/{ohlc_sanity,dedupe}.py` + tests
- **leaf_spec**: 단일 캔들 OHLC/거래량/시간 일관성, 중복 탐지(동일 vs 충돌).
- **dep**: LA-1. **dod/negative_test**: §8.1 케이스.

#### LA-5. 갭/정체 탐지
- **paths**: `src/domain/quality/{gap_detector,stale_detector}.py` + tests
- **leaf_spec**: 세션 인지 갭 탐지(expected_opens 대비), `k×duration` 대비 정체 판정.
- **dep**: LA-2, LA-3. **dod/negative_test**: §8.1 케이스.

#### LA-6. 이상치 탐지
- **paths**: `src/domain/quality/{outlier_detector,verdict}.py` + tests
- **leaf_spec**: 롤링 median/MAD 스파이크 탐지, 배치 verdict 종합.
- **dep**: LA-1. **dod**: 고정시드 false positive 0건, 20% 거부율 경계. **negative_test**: 고정시드에서 false positive 발생 시 실패.

#### LA-7. 심볼 정규화/생명주기
- **paths**: `src/domain/reference/{symbol_normalizer,lifecycle}.py` + tests
- **leaf_spec**: 정규↔거래소 심볼 매핑, 심볼 생명주기 FSM.
- **dep**: LA-1. **dod/negative_test**: 전이표(허용 5개, 나머지 거부).

#### LA-8. 기업행위 조정/리니지
- **paths**: `src/domain/corporate_actions/adjustment.py`, `src/domain/lineage.py` + tests
- **leaf_spec**: 액면분할/배당 누적조정계수 체인, 배치 콘텐츠해시 리니지.
- **dep**: LA-1. **dod/negative_test**: 누적분할 산술, 해시 순서독립성.

#### LA-9. 저장/참조/캘린더/수집/배치 포트
- **paths**: `src/ports/{candle_store,reference_repository,calendar_repository,ingest_source,batch_repository}.py`
- **leaf_spec**: 5개 Protocol 정의.
- **dep**: LA-1. **dod/negative_test**: mypy Protocol 준수.

#### LA-10. 참조 레지스트리 스키마
- **paths**: migration `4a1d0c0de002`(`md_instrument`,`md_symbol_alias` EXCLUDE gist,`md_corporate_action`,`md_venue_calendar_day`) + test
- **dep**: L0-5. **dod/negative_test**: 라운드트립, 제약조건 위반 거부.

#### LA-11. 캔들/틱 저장 스키마
- **paths**: migration `4a1d0c0de003`(`md_candle` 파티션+CHECK×6, `md_quarantine_candle`, `md_tick` 파티션, `md_ingest_batch`, `md_quality_issue`, WORM, 파티션 생성 함수)
- **dep**: LA-10. **dod/negative_test**: 라운드트립, CHECK 위반 실패, 파티션 자동생성.

#### LA-12. 참조/캘린더 어댑터
- **paths**: `src/adapters/{postgres_reference_repository,postgres_calendar_repository,yaml_calendar_source}.py`, `config/market_calendars/KRX_2026.yaml`(미검증) + tests
- **dep**: LA-9, LA-10. **dod/negative_test**: RENAME 별칭 처리, 캘린더 로드.

#### LA-13. 캔들/배치 저장 어댑터
- **paths**: `src/adapters/{postgres_candle_store,postgres_batch_repository}.py` + test
- **leaf_spec**: `ON CONFLICT DO NOTHING` upsert, 격리보관, as_of 쿼리.
- **dep**: LA-11. **dod/negative_test**: 중복제거/격리보관/as_of 검증.

#### LA-14. 참조데이터 변경 커맨드
- **paths**: `src/application/{register_instrument,record_corporate_action,sync_calendar}.py` + tests
- **dep**: LA-12, L0-4. **dod/negative_test**: 감사이벤트 1:1, 미결제 포지션 있는 종목 상장폐지 거부.

#### LA-15. 캔들 수집 파이프라인
- **paths**: `src/application/ingest_candles.py`, `src/adapters/bitget_ingest_source.py` + integration test
- **leaf_spec**: fetch→parse→sanity→dedupe→gap/stale/spike→verdict→저장/격리→배치→감사, fail-closed 전 구간.
- **dep**: LA-4~8, LA-13, LA-14. **dod/negative_test**: §8.2 케이스, 감사 실패 시 저장도 롤백.

#### LA-16. 틱 수집 파이프라인
- **paths**: `src/application/ingest_ticks.py` + test
- **dep**: LA-15. **dod/negative_test**: trade_id 역전 시 REJECT.

#### LA-17. 캔들 조회/리플레이
- **paths**: `src/application/{get_candles,replay_candles}.py` + tests
- **leaf_spec**: as_of 스코프 읽기, 결정적 백테스트 리플레이(시리즈 해시).
- **dep**: LA-13, LA-8. **dod/negative_test**: 해시 결정성, strict 모드 갭 탐지.

#### LA-18. 수집 스케줄러
- **paths**: `src/application/quality_metrics.py`, `src/application/scheduler.py`, `src/main.py`(PM 배선)
- **dep**: LA-15, L0-1. **dod/negative_test**: 1주기 후 게이지 존재, 심볼 1개 실패가 나머지 안 막음.

#### LA-19. 거래소 심볼 정규화 위임
- **paths**: `src/exchanges/{bitget,kis}/market_data_mixin.py` + 기존 테스트
- **dep**: LA-7. **dod/negative_test**: `get_positions` 심볼이 정규형이어야 함.

#### LA-20. KIS 캔들 수집
- **paths**: `src/adapters/kis_ingest_source.py` + test(MockTransport)
- **dep**: LA-15. **dod/negative_test**: KRX 세션 갭 판정 통합.

#### LA-21. A파트 적대적/성능 테스트
- **paths**: 적대적 테스트 2개 + 리플레이 성능 테스트
- **dep**: LA-17. **dod/negative_test**: 교차테넌트 배치 격리, 변조→해시불일치 탐지, 1년치 1분봉 리플레이 성능.

### (B) 포지션 & PnL

#### LB-1. 포지션 계약 스키마
- **paths**: `src/positions/contracts/v1.py` + schema test
- **leaf_spec**: RecordFillCommand, PositionSnapshotView, PnLBreakdown, NAVSnapshot 등.

#### LB-2. FIFO 원가기준
- **paths**: `src/domain/position_key.py`, `src/domain/cost_basis/fifo.py` + tests
- **dep**: LB-1. **dod/negative_test**: §8.1 FIFO 케이스.

#### LB-3. 가중평균 원가기준
- **paths**: `src/domain/cost_basis/{weighted,selector}.py` + tests
- **leaf_spec**: 가중평균 방식, 파생상품은 강제 WEIGHTED.
- **dep**: LB-2. **negative_test**: 파생상품에 다른 방식이 적용되면 실패.

#### LB-4. FX/PnL/펀딩수수료
- **paths**: `src/domain/{fx,pnl,funding_fees}.py` + tests
- **leaf_spec**: 기준통화 환산(환율 없으면 예외, 삼각환산 금지), 미실현PnL, 펀딩/수수료 분개.
- **dep**: LB-1. **negative_test**: 환율 없을 때 0으로 처리되면 실패(예외여야 함).

#### LB-5. 저널/스냅샷 빌더
- **paths**: `src/domain/journal_rules.py`, `src/domain/snapshot_builder.py` + property tests
- **dep**: LB-2~4. **dod/negative_test**: `fold==reduce(apply_one)` 200회 무작위 체결열.

#### LB-6. NAV/리컨실 규칙
- **paths**: `src/domain/{nav,reconciliation_rules}.py` + tests
- **dep**: LB-4. **dod/negative_test**: 일일 NAV 체인 등식.

#### LB-7. 포지션 포트
- **paths**: `src/ports/*.py`(journal/snapshot/mark_price/fx_rate/provider_balance/nav, 6개)
- **dep**: LB-1.

#### LB-8. 포지션/원장 스키마
- **paths**: migration `4a1d0c0de004`(`pos_account`, `pos_journal` WORM, `pos_snapshot`, `pos_nav_daily` CHECK closing=cash+mv)
- **dep**: L0-5.

#### LB-9. 저널/스냅샷/NAV 어댑터
- **paths**: `src/adapters/postgres_{journal,snapshot,nav}_repository.py` + integration tests
- **dep**: LB-7, LB-8. **dod/negative_test**: 동시 체결 20건 → 연속 시퀀스.

#### LB-10. 레거시 포지션 프로젝션
- **paths**: `src/adapters/legacy_positions_projection.py`, `tests/test_legacy_compat.py`
- **dep**: LB-9. **negative_test**: 기존 3개 서비스 쿼리 결과가 바뀌면 실패.

#### LB-11. record_fill 단일 트랜잭션
- **paths**: `src/application/record_fill.py` + integration test
- **dep**: LB-5, LB-9, LB-10, L0-4. **dod/negative_test**: §8.2 record_fill 전체 케이스.

#### LB-12. position_ledger 위임
- **paths**: `src/services/order_service/position_ledger.py`(수정) + 부분청산 신규 테스트
- **dep**: LB-11. **negative_test**: BUY×2 후 부분 SELL 계산이 틀리면 실패.

#### LB-13. 펀딩수수료/스냅샷 재구성
- **paths**: `src/application/{record_funding_fee,rebuild_snapshot}.py` + tests
- **dep**: LB-11. **dod/negative_test**: 재구성 drift=∅.

#### LB-14. 포지션 마킹
- **paths**: `src/adapters/{candle_mark_price_source,fx_rate_source}.py`, `src/application/mark_positions.py` + tests
- **dep**: LA-17, LB-11. **negative_test**: 정체된 시세인데 미실현이 None이 아니면 실패.

#### LB-15. 일일 NAV 계산
- **paths**: `src/application/compute_daily_nav.py` + test
- **dep**: LB-6, LB-14, LA-3. **negative_test**: 체인 위반인데 덮어쓰면 실패.

#### LB-16. Provider 잔고 리컨실
- **paths**: `src/adapters/exchange_balance_source.py`, `src/application/reconcile_provider.py` + test(FakeAdapter)
- **dep**: LB-14, "FND-08"(기존 리컨실 프레임워크). **negative_test**: provider 예외를 빈 리스트로 폴백하면 실패.

#### LB-17. 포지션 조회 스케줄러
- **paths**: `src/application/queries.py`, `src/application/scheduler.py`, `src/main.py`(PM 배선)
- **dep**: LB-14~16.

#### LB-18. B파트 적대적/성능 테스트
- **dep**: LB-11. **dod/negative_test**: 동시 동일-포지션키 체결 경쟁(20×gather→연속시퀀스), 교차테넌트 record_fill 거부.

### (C) 자금 원장

#### LC-1. 원장 계약 스키마
- **paths**: `src/ledger/contracts/v1.py` + schema test
- **negative_test**: `amount≤0` → ValidationError 안 나면 실패.

#### LC-2. 계정과목/반올림
- **paths**: `src/domain/{chart_of_accounts,rounding}.py` + tests, `src/services/commission.py`(위임) + test
- **dep**: LC-1. **negative_test**: HALF_EVEN 분할 합계가 안 맞으면 실패.

#### LC-3. 잔액규칙/해시체인/멱등성
- **paths**: `src/domain/{balance_rules,hash_chain,idempotency}.py` + tests
- **dep**: LC-1. **negative_test**: 변조가 탐지 안 되거나 음수잔액이 거부 안 되면 실패.

#### LC-4. 전기규칙(9종 이벤트)
- **paths**: `src/domain/posting_rules.py` + test
- **dep**: LC-2, LC-3. **negative_test**: 9종 이벤트 중 하나라도 Σ차변≠Σ대변이면 실패.

#### LC-5. 홀드/지급/시산표
- **paths**: `src/domain/{hold_state,payout_schedule,trial_balance}.py` + tests(1000건 무결성 증명)
- **dep**: LC-4. **negative_test**: 1000회 무작위 이벤트열 중 Σ≠0인 경우가 있으면 실패.

#### LC-6. 원장 핵심 스키마
- **paths**: migration `4a1d0c0de005`(`ledger_account`,`ledger_journal_entry`,`ledger_posting_line`, 지연 잔액체크 트리거, `ledger_balance`,`ledger_control`, WORM, 플랫폼 계정 시드)
- **dep**: L0-5. **negative_test**: 불균형 커밋이 성공하면 실패.

#### LC-7. 홀드/지급 스키마
- **paths**: migration `4a1d0c0de006`(`ledger_hold`,`ledger_payout_batch`,`ledger_payout_item`,`ledger_integrity_check` WORM)
- **dep**: LC-6.

#### LC-8. 원장 저장 어댑터
- **paths**: `src/ports/*.py`(4개), `src/adapters/postgres_{journal,balance}_repository.py` + integration tests
- **dep**: LC-6. **negative_test**: 동시 작성자 50명이 연속 글로벌 시퀀스를 못 만들면 실패.

#### LC-9. 단일 전기 경로
- **paths**: `src/application/post_entry.py` + test
- **dep**: LC-4, LC-8, L0-4. **negative_test**: 동결된 원장에 전기가 성공하거나, DIGEST_MISMATCH가 감사 없이 통과하면 실패.

#### LC-10. 무결성 검증
- **paths**: `src/application/verify_integrity.py`, `src/scheduler.py`(무결성 부분) + test
- **dep**: LC-9. **negative_test**: 변조 후 원장이 동결 안 되면 실패.

#### LC-11. 원장 백필
- **paths**: `scripts/ledger_backfill.py`, `src/application/backfill.py` + test
- **dep**: LC-9. **negative_test**: 픽스처 Σ≠0인데 롤백 안 되면 실패.

#### LC-12. 레거시 지갑 브리지
- **paths**: `src/adapters/legacy_wallet_bridge.py`, `src/services/wallet_service.py`(수정), `src/application/topup.py` + 기존 지갑 테스트
- **dep**: LC-11. **negative_test**: 잔고가 원장과 불일치하면 실패.

#### LC-13. 홀드 기반 구매 플로우
- **paths**: `src/adapters/postgres_hold_repository.py`, `src/application/purchase_flow.py`, `src/services/purchase_service.py`(수정) + tests
- **dep**: LC-12. **negative_test**: 동시 구매 5건 중 2건 이상 성공하면 실패.

#### LC-14. 환불(펀드생성 버그 수정)
- **paths**: `src/application/refund.py`, `src/services/dispute_resolution_service.py`(수정) + tests
- **leaf_spec**: R1/R2/R3 환불 재원 로직(판매자 clawback 포함) — 환불이 돈을 새로 만들던 버그(레드팀 #41) 수정.
- **dep**: LC-13. **negative_test**: 이중환불이 거부 안 되면 실패.

#### LC-15. 지급/차지백
- **paths**: `src/adapters/postgres_payout_repository.py`, `src/application/{payouts,chargeback}.py` + RECEIVABLE 오프셋 + tests + admin route
- **dep**: LC-13.

#### LC-16. 원장 조회 API
- **paths**: `src/application/queries.py`, `src/api/routers/wallet.py`(수정), `src/scheduler.py`(지급 부분), `src/main.py`(배선)
- **dep**: LC-15. **negative_test**: 기존 프론트 `balance` 필드가 깨지면 실패.

#### LC-17. C파트 적대적/성능 테스트
- **dep**: LC-14. **dod/negative_test**: 동시구매 경쟁, 음수/0금액+시크릿키 인젝션 거부, 리플레이공격 409, 롤 우회 DDL 거부.

---

## 3. L4_platform_observability_tenancy_api_v1.0.md (§9-C API계약 그룹·PLT-05 제외 — agent-platform-f0 진행 중/완료)

### PLT-01. RequestContext
- **paths**: `src/core/observability/context.py` + test
- **leaf_spec**: 8필드 frozen `RequestContext` + `bind()`/`bind_system()`. 타 세션의 request_id 커밋 이후 `get_current_request_id()` shim.
- **dep**: 타 세션 request_id 커밋(완료 여부 확인 필요).

### PLT-02. 로그 마스킹/필드
- **paths**: `src/core/logging/{redaction,fields}.py` + tests
- **dep**: PLT-01. **negative_test**: hex/JWT형 값이 마스킹 안 되면 실패.

### PLT-03. 로그 포매터 통합
- **paths**: `src/core/logging/schema.py`(수정)
- **dep**: PLT-02. **negative_test**: 기존 로깅 테스트가 깨지면 실패.

### PLT-04. 메트릭 포트/네이밍
- **paths**: `src/core/observability/{metric_names,metrics}.py`, `prometheus-client` 의존성 추가
- **negative_test**: 메트릭명이 정규식과 안 맞으면 실패.

### PLT-06. 이벤트버스 컨텍스트 전파
- **paths**: `src/core/event_bus/envelope.py`, `in_process.py`(수정)
- **dep**: PLT-01. **negative_test**: 핸들러가 발행시점 trace_id를 못 보면 실패.

### PLT-07. audit_log trace_id 통일
- **paths**: migration M1(`audit_log_trace_id`), `audit_log.py`, `record_command_event.py`(수정)
- **dep**: PLT-01. **negative_test**: 감사호출마다 새 uuid4가 생성되면(현재 버그) 실패.

### PLT-08. 백그라운드 루프 헬스
- **paths**: `src/core/observability/loop_health.py`, `src/core/safety/base_loop.py`(수정), `src/main.py`(PM 배선)
- **dep**: PLT-04(및 완료된 PLT-05).

### PLT-09. 헬스체크 엔드포인트
- **paths**: `src/api/routers/health.py`, `src/main.py`(PM 등록)
- **dep**: PLT-08. **negative_test**: `/metrics`가 토큰 없이 열리면 실패.

### PLT-10. 도메인 지점 메트릭 계측
- **paths**: `src/order_service/{submit,gate,position_ledger,reconcile}.py`, `submit_paper_intent.py`, adapter `_request` 3곳
- **dep**: PLT-04. **negative_test**: FROZEN 경로 diff가 0이 아니면 실패(로직 안 건드려야 함).

### PLT-11. 알림규칙/runbook
- **paths**: `config/observability/alert_rules.yaml`, `docs/runbooks/RB-01..08.md` + test
- **dep**: PLT-04. **negative_test**: yaml에서 참조하는 메트릭이 metric_names.py에 없으면 실패.

### PLT-22. 로그인 잠금 원자화
- **paths**: `src/services/auth/lockout.py`, `auth_service.py`(수정) + test
- **dep**: PLT-13(타 세션 진행 여부 확인). **negative_test**: 동시 오답 10회 gather가 정확한 카운트+423을 안 내면 실패.

### PLT-23. JWT 세션
- **paths**: migration M3(`auth_session`), `session_repository.py`, `tokens.py`
- **leaf_spec**: HS256+kid 헤더 JWT 발급, 세션 CRUD, 리프레시 로테이션(재사용 탐지 포함).
- **dep**: PLT-22.

### PLT-24. 로그인/리프레시/로그아웃
- **paths**: `src/auth/{login,refresh,logout}.py`, `routers/auth.py`(수정), `deps.py`(수정)
- **dep**: PLT-23. **negative_test**: 기존 auth 테스트가 토큰쌍 응답으로 갱신 안 되면 실패.

### PLT-25. Rate limit 미들웨어
- **paths**: `src/security/rate_limit/{policy,limiter}.py`, `middleware/rate_limit.py`, `src/main.py`(PM)
- **dep**: PLT-12(타 세션 확인). **negative_test**: 121번째 읽기요청이 429가 아니면 실패.

### PLT-26. 멀티테넌트 스키마
- **paths**: migration M4(`tenant`,`tenant_membership`, 개인테넌트 백필, partial UNIQUE), `trust/domain/models.py`(수정), `rules/membership.py`

### PLT-27. 멤버십 저장소
- **paths**: `trust/ports/membership_repository.py`, `adapters/postgres_membership_repository.py`
- **dep**: PLT-26.

### PLT-28. 테넌트 컨텍스트 해석
- **paths**: `src/application/resolve_tenant_context.py`, `trust/contracts/v1.py`(MINOR), `foundation_deps.py`(수정)
- **dep**: PLT-27, PLT-05(완료). **negative_test**: 비멤버가 403이 아니면 실패.

### PLT-29. 멤버십 부여/정지/철회
- **paths**: `grant/suspend/revoke_membership.py`, `trust_memberships.py` router + 적대적 테스트
- **dep**: PLT-28, PLT-24. **negative_test**: 마지막 owner 제거가 거부 안 되면 실패.

### PLT-30. RLS 정책
- **paths**: `db/roles.sql`, migration M5(`rls_policies_foundation`), `core/db/tenant_scope.py` + RLS 테스트 3개
- **dep**: PLT-26. **negative_test**: WHERE절 없는 SELECT가 0행이 아니면 실패.

### PLT-31. 키 링
- **paths**: `security/key_ring.py`, `encryption.py`(수정) + tests
- **negative_test**: LIVE 키가 PAPER 런타임에서 부팅을 막지 않으면 실패.

### PLT-32. 봉투 암호화
- **paths**: `security/envelope.py`, `secret_ref.py`, `secret_handle.py`
- **dep**: PLT-31. **negative_test**: 핸들 종료 후 bytearray가 0으로 안 지워지면 실패.

### PLT-33. 자격증명 스코프
- **paths**: migration M6, `exchange_credential_service.py`(수정), `credential_resolver.py`(수정)
- **dep**: PLT-32. **negative_test**: 스코프 격리/로그유출 적대적 테스트.

### PLT-34. 키 로테이션 스크립트
- **paths**: `scripts/rotate_credential_keys.py` + test + RB-05
- **dep**: PLT-33. **negative_test**: 중단 후 재개가 안 되면 실패.

### PLT-35. Break-glass 워크플로
- **paths**: migration M7(`break_glass_grant`), `security/break_glass.py`, `admin_deps.py`(수정, MFA 필수)
- **dep**: PLT-24. **negative_test**: 자기승인/만료/이중소비 중 하나라도 거부 안 되면 실패.

### PLT-36. 테스트 DB 격리
- **paths**: `tests/support/db.py`, `conftest.py`, `setup_test_db.py`(수정), pytest-xdist
- **negative_test**: `pytest -n 4`에서 격리 오류가 있으면 실패.

### PLT-37. 커버리지 래칫
- **paths**: `scripts/coverage_ratchet.py`, `coverage-baseline.txt` + CI
- **dep**: PLT-36. **negative_test**: 커버리지 하락인데 CI가 통과하면 실패.

### PLT-38. 마이그레이션 체인/Zone diff 체크
- **paths**: `scripts/check_migration_chain.py`, `check_zone_diff.py` + CI + test
- **negative_test**: 듀얼헤드나 FROZEN diff가 CI를 통과하면 실패.

### PLT-39. 릴리스 게이트
- **paths**: `.gitleaks.toml`, `config/release_gates.yaml`, `check_release_gate.py`

### PLT-40. type: ignore 제거
- **paths**: `exchanges/common/http_client.py`(Protocol) + mixin 31개(bitget 18/kis 9/nh 4), `warn_unused_ignores`, `check_type_ignore_budget.py`
- **negative_test**: ignore 개수가 226에서 안 줄면 실패.

### PLT-41. ruff 엄격화
- **paths**: `pyproject.toml`(ruff S,BLE,ARG,PGH,T20) + 수정
- **dep**: PLT-40.

### PLT-42. 문서화
- **paths**: `docs/TESTING.md`, README, `.env.example`
- **dep**: 위 전부.

---

## 4. L4_risk_and_safety_v1.0.md (R-48·risk_stats 그룹(R-18~20) 제외 — agent-platform-9f 진행 중)

### R-01~R-17, R-21~R-58: **아래 §A(FROZEN_PAPER_ONLY, PM 승인 필요)와 본 섹션에 분리 표기**

#### R-21. 리스크 정책 확장 로더
- **paths**: `config/risk_policy.yaml`(§3.3 확장), `risk_policy_loader.py` + test
- **negative_test**: 범위제약 위반이나 번들 불일치가 통과하면 실패.

#### R-22. 정책번들 저장소
- **paths**: migration `a9c4e1f7b2d3`, `adapters/postgres_bundle_repository.py` + test
- **dep**: R-15(★, 아래 참조).

#### R-23. 룰번들 활성화
- **paths**: `application/activate_rule_bundle.py` + router + test
- **leaf_spec**: DRAFT→APPROVED(승인자≠작성자)→ACTIVE(이전 것 은퇴).
- **dep**: R-22. **negative_test**: 승인자=작성자가 거부 안 되면 실패.

#### R-24. 결정 저장소(WORM)
- **paths**: migration `b8d5f2a1c3e4`, `adapters/postgres_decision_repository.py` + test
- **dep**: R-02(★). **negative_test**: UPDATE/DELETE가 RAISE 안 하면 실패.

#### R-25. 리스크 결정 기록기
- **paths**: `services/risk_decision_recorder.py` + test
- **dep**: R-24. **negative_test**: 클럭 드리프트인데 DENY가 아니면 실패.

#### R-26. 노출한도 저장소
- **paths**: migration `c7e6a3b2d4f5`, `postgres_limit_repository.py`, `upsert_risk_limit.py` + test
- **dep**: R-14(★). **negative_test**: 교차테넌트가 0행이 아니면 실패.

#### R-27. 노출 스냅샷
- **paths**: `execution_loop/exposure_snapshot.py` + integration test
- **dep**: R-26. **negative_test**: 다중쿼리로 나뉘거나 교차유저 유출 시 실패.

#### R-28. 캔들 히스토리 캐시
- **paths**: `execution_loop/candle_history.py` + test
- **negative_test**: 실패 시 stale 값을 반환하면 실패(None이어야 함).

#### R-29. VaR/상관 execution_loop 이관
- **paths**: `execution_loop/var_estimator.py`(교체), `correlation_service.py`(신규), `correlation.py`(삭제) + test
- **dep**: R-19/R-20(진행 중, 완료 확인), R-28. **negative_test**: 삭제된 하드코딩 상관테이블 grep 히트가 있으면 실패.

#### R-30. 자산피크 추적기
- **paths**: `execution_loop/equity_tracker.py` + test
- **leaf_spec**: UTC 일자 경계, 단조 조건부 UPDATE(GREATEST+CASE)로 lost-update 방지.
- **negative_test**: 동시쓰기 주입 시 피크가 역행하면 실패.

#### R-31. RiskInputs 조립기
- **paths**: `execution_loop/risk_inputs_assembler.py`, `account_state.py`(축소) + integration test
- **dep**: R-03(★), R-27, R-28, R-29, R-30. **negative_test**: DB 왕복이 2회 초과하면 실패.

#### R-32. tick 리스크 단계 배선
- **paths**: `execution_loop/tick_risk_phase.py`, `tick.py`(수정) + test
- **dep**: R-17(★), R-25, R-31. **negative_test**: DENY가 기록 안 되면 실패.

#### R-33. Fence 도메인
- **paths**: `foundation/risk_gate/domain/fence.py`, `application/read_fence.py`, repo `read_fences()` + test
- **negative_test**: 5쌍이 쿼리 1번에 안 나오면 실패.

#### R-34. Risk gate 모델 확장
- **paths**: migration `f4b9d6e5a7c8`, `contracts/v1.py`, `domain/models.py`, `evaluate_risk_gate.py`(trace_id)
- **dep**: R-33. **negative_test**: 기존 `test_risk_gate_lifecycle.py`가 깨지면 실패.

#### R-35. PRE_SUBMIT 게이트
- **paths**: `application/evaluate_pre_submit.py` + test
- **dep**: R-33, R-34, R-25. **negative_test**: 각 입력이 독립적으로 DENY/PAUSE를 못 내면 실패.

#### R-36. foundation_gate 배선
- **paths**: `order_service/{gate,foundation_gate,pre_submit_check}.py`(수정) + test
- **dep**: R-35. **negative_test**: mandate 기본값이 강제 안 되면 실패.

#### R-37. Fence-checked submit
- **paths**: migration `93c0e7f6b8d9`, `order_service/fenced_submit.py` + test + fence-race 적대적 테스트
- **dep**: R-36. **negative_test**: gather 경쟁에서 fence 이후 부수효과가 발생하면 실패.

#### R-38. 킬스위치 레거시 매핑
- **paths**: `services/safety/legacy_execution_pauser.py` + test
- **negative_test**: 5개 스코프 매핑 중 하나라도 틀리면 실패.

#### R-39. 미체결주문 소탕
- **paths**: `services/safety/open_order_sweeper.py` + test
- **dep**: R-38. **negative_test**: control_id 재실행 시 중복 취소가 발생하면 실패.

#### R-40. KillSwitchService 단일 진입점
- **paths**: `services/safety/kill_switch_service.py`, `activate_safety_control.py` hook + test
- **dep**: R-38, R-39. **negative_test**: `safety_control` INSERT 호출지점이 2곳 이상이면 실패.

#### R-41. RiskGuard 킬스위치 경유
- **paths**: `services/risk_guard_service.py`(수정) + test
- **dep**: R-40. **negative_test**: 직접 `pause()` 호출이 남아있으면 실패.

#### R-42. 데이터 신선도 추적
- **paths**: `core/safety/data_freshness.py`, `exchanges/common/instrumented_adapter.py`(옵션 인자) + test
- **negative_test**: 관측 0건인데 None이 아니면 실패.

#### R-43. data_delay 실측 반영
- **paths**: `metrics_collector.py`(수정), `circuit_breaker.py`(_set_level 조건부) + test
- **dep**: R-42. **negative_test**: 상수 0이 남아있으면 실패.

#### R-44. 재가동 게이트
- **paths**: `core/safety/recovery_gate.py` + test
- **negative_test**: 4가지 거부 케이스 중 하나라도 통과하면 실패.

#### R-45. 서킷브레이커 루프
- **paths**: `services/safety/circuit_breaker_loop.py`, `main.py`(PM 배선) + test
- **dep**: R-43, R-44. **negative_test**: halted가 자동으로 하향되면 실패.

#### R-46. 인트라데이 모니터
- **paths**: migration `d6f7b4c3e5a6`, `postgres_signal_repository.py`, `application/intraday_monitor.py` + test
- **dep**: R-40. **negative_test**: 정체 신호인데 PAUSE 안 걸면 실패.

#### R-47. 참조시세 포트
- **paths**: `services/safety/reference_quotes.py` + 어댑터 2개(미검증) + test(fake)
- **negative_test**: 타임아웃인데 None이 아니면 실패.

#### R-49. 시장 상관 감지
- **paths**: `core/safety/market_correlation.py`, `watchdog.py`(확장) + test
- **negative_test**: 바스켓<3인데 None이 아니거나, DB 격리장애 시 LIQUIDATE가 HALT로 안 내려가면 실패.

#### R-50. 청산 슬라이서
- **paths**: `core/safety/liquidation_planner.py` + test
- **dep**: R-21. **negative_test**: 결정성/합계보존/참여율상한/최소3슬라이스 중 하나라도 위반되면 실패.

#### R-51. Watchdog 배선
- **paths**: migration `e5a8c5d4f6b7`, `watchdog_process.py`(수정) + test
- **dep**: R-40, R-49, R-50. **negative_test**: 무조건 UPDATE가 남아있으면 실패.

#### R-52. 청산 실행기
- **paths**: `services/safety/liquidation_executor.py`, `main.py`(PM 워커 배선) + test
- **dep**: R-51. **negative_test**: fence 변경 시 ABORTED가 안 되면 실패.

#### R-53. 재가동 게이트 API
- **paths**: `application/recovery_gate.py` + router + test
- **dep**: R-44, R-35(진행 중 확인). **negative_test**: 증거 누락인데 거부(RSK-007) 안 되면 실패.

#### R-54. 결정 재현 검증
- **paths**: `application/replay_decision.py`, `tools/risk_replay.py` + test + nightly CI
- **dep**: R-24, R-22. **negative_test**: 변조된 결정이 exit 2가 아니면 실패.

#### R-55. 리스크 알림
- **paths**: `services/risk_alerting.py` + test
- **dep**: R-25. **negative_test**: 5분 dedupe 윈도 안에 중복 알림이 가면 실패.

#### R-56. 리스크 적대적 테스트 3종
- **paths**: `test_no_llm_in_risk_path`, `test_agent_cannot_forge_allow`, `test_cross_tenant_limits`
- **dep**: R-37, R-40.

#### R-57. 사전거래 지연 성능
- **paths**: `tests/performance/test_pre_trade_latency.py` + CI assertion
- **dep**: R-32. **dod**: p99≤50ms 종단, `evaluate()` p99≤5ms.

#### R-58. 레드팀 발견사항 문서화
- **paths**: `docs/RED_TEAM_FINDINGS.md`
- **leaf_spec**: 상관계수 0.0 fail-open, data_delay 상수 0, watchdog None-lock, foundation_gate mandate-bypass 플래그, 무조건 UPDATE들, strategy_allocation 분모오류를 번호 매겨 등록.

---

## 5. L4_strategy_portfolio_backtest_v1.0.md (L45~L49 제외 — agent-platform-c2 진행 중)

### L01. 지표 스펙
- **paths**: `core/indicators/{spec,specs_talib}.py`
- **leaf_spec**: `ParamSpec`/`IndicatorSpec` + TA-Lib 11종 스펙(정확한 lookback 공식+파라미터 범위검증).
- **negative_test**: lookback이 TA-Lib NaN 개수와 안 맞으면 실패.

### L02. 지표 레지스트리
- **paths**: `core/indicators/registry.py`
- **dep**: L01. **negative_test**: 범위 밖 파라미터가 거부 안 되면 실패.

### L03. TA-Lib 어댑터 갱신
- **paths**: `core/indicators/talib_adapter.py`
- **dep**: L02. **negative_test**: 기존 indicator_service 테스트가 깨지면 실패.

### L07. Lookback 계산
- **paths**: `core/indicators/lookback.py`
- **leaf_spec**: 전략 키 기준 tf별 필요 봉수 계산, 하드코딩 `limit=100` 제거.
- **dep**: L02, L06(★). **negative_test**: 미지 지표 에러가 안 나면 실패.

### L13. 전략 상태 저장
- **paths**: migration M1(`strategy_execution_state`), `execution_loop/strategy_state_store.py`
- **dep**: L08(★). **negative_test**: 동시쓰기 충돌 케이스가 처리 안 되면 실패.

### L14. execution_loop 시장상태 조립
- **paths**: `execution_loop/market_state.py`
- **dep**: L07, L08(★). **negative_test**: 부분실패가 전파 안 되면 실패.

### L15. 조건 컴파일러 갱신
- **paths**: `services/condition_compiler.py`, `preview_service.py`
- **dep**: L05(★), L02. **negative_test**: 중첩 ConditionGroup 왕복이 깨지면 실패.

### L16. 전략빌더 검증 강화
- **paths**: `services/strategy_builder_service.py`
- **dep**: L05(★). **negative_test**: 문법오류가 400이 아니면 실패.

### L23. 포트폴리오 상태 조립(execution_loop)
- **paths**: migration M2(`portfolio_config` 컬럼), `execution_loop/portfolio_state.py`
- **dep**: L22(★), `b3f7e0c1a4d5`. **negative_test**: RUNNING 실행 중 설정변경이 거부 안 되면 실패.

### L24. tick 배선(상태+포트폴리오+decision_hash)
- **paths**: `execution_loop/tick.py`
- **dep**: L13, L14, L23. **negative_test**: 재시작 후 크로스오버 지연이나 mandate clamp가 주문에 반영 안 되면 실패.

### L25. 백테스트 도메인 v2
- **paths**: `foundation/backtest/domain/{models,snapshot,events}.py`
- **leaf_spec**: CostModel/BacktestConfig/BacktestMetrics v2, 이벤트드리븐 타입, 봉 스냅샷 해싱.
- **negative_test**: 해시가 결정적이지 않으면 실패.

### L26. 백테스트 포트
- **paths**: `foundation/backtest/ports/*.py`(3개), `adapters/list_bars.py`
- **dep**: L25. **negative_test**: 미래 인덱스 접근이 `BACKTEST_LOOKAHEAD_VIOLATION`을 안 내면 실패.

### L27. 체결 시뮬레이터
- **paths**: `adapters/bar_fill_simulator.py`, `application/simulate_fill.py`(wrapper)
- **dep**: L26. **negative_test**: NEXT_OPEN/갭체크/메이커테이커/SQRT_IMPACT 중 하나라도 틀리면 실패.

### L28. 지표 시리즈 캐시(O(n²) 제거)
- **paths**: `core/indicators/series_cache.py`
- **dep**: L03. **negative_test**: 전체계산과 점진계산 결과가 다르면 실패(인과성 증명).

### L29. 유니버스/규칙
- **paths**: `foundation/backtest/domain/{universe,rules}.py`
- **dep**: L25. **negative_test**: 상장폐지 구간 판정이나 스냅샷 누락 하드실패가 안 되면 실패.

### L30. 이벤트 루프(백테스트 핵심)
- **paths**: `foundation/backtest/application/event_loop.py`
- **dep**: L12(★), L22(★), L27~L29. **negative_test**: 기존 `run_backtest` 수치결과 회귀 테스트가 깨지면 실패.

### L31. 백테스트 파사드/지표계산
- **paths**: `foundation/backtest/application/{run_backtest,compute_metrics}.py`
- **dep**: L30. **negative_test**: gross/net 분리나 기존 지표 회귀가 깨지면 실패.

### L32. 봉 스냅샷 저장
- **paths**: migration M4(`market_bar_snapshot`), `adapters/postgres_snapshot_repository.py`
- **dep**: L25. **negative_test**: 저장→로드→해시가 안 맞으면 실패.

### L33. OOS 분할/파라미터 안정성
- **paths**: `foundation/backtest/domain/{splits,param_stability}.py`
- **negative_test**: purge/embargo 구간이 겹치거나 고립최적점 탐지가 안 되면 실패.

### L34. DSR/PBO
- **paths**: `foundation/backtest/domain/overfitting.py`(공식 출처 미검증 U3)
- **negative_test**: 픽스처 기준값과 안 맞으면 실패.

### L35. 파라미터 스윕/워크포워드/스트레스
- **paths**: `foundation/backtest/application/{param_sweep,walk_forward,stress}.py`
- **dep**: L31, L33. **negative_test**: 결정적 순서·선택규칙·필수 시나리오 커버리지 중 하나라도 빠지면 실패.

### L36. 검증 정책/아티팩트
- **paths**: `foundation/validation/domain/{policy,check_result,artifact}.py`
- **dep**: L05(★), L02. **negative_test**: 변조된 아티팩트 해시가 탐지 안 되면 실패.

### L37. 검증 실행 스키마 확장
- **paths**: migration M3, `domain/models.py`, `contracts/v1.py`, `ports/repository.py`, `adapters/postgres_repository.py`
- **dep**: L36. **negative_test**: 기존 `test_start_validation.py`가 깨지면 실패.

### L38. 검증 체크 1·2
- **paths**: `checks/{context,point_in_time,backtest}.py`
- **dep**: L31, L36. **negative_test**: PASS 케이스 1개 + hard-fail 케이스가 없으면 실패.

### L39. 검증 체크 3·4
- **paths**: `checks/{oos_walk_forward,robustness}.py`
- **dep**: L34, L35.

### L40. 검증 체크 5·6
- **paths**: `checks/{stress_capacity,failure_conditions}.py`
- **dep**: L35.

### L41. 검증 실행 스켈레톤
- **paths**: `application/{compile_artifact,run_check}.py`
- **dep**: L37, L38. **negative_test**: 증거행 없이 결과가 커밋되면 실패.

### L42. 검증 파사드
- **paths**: `application/start_validation.py`(축소), `domain/rules.py`
- **dep**: L41. **negative_test**: hard_fail_reasons가 비어있는데 FAIL이 아니면 실패(현재 알려진 버그).

### L43. 검증 번들/전략 생명주기 연동
- **paths**: `application/build_bundle.py`, `projections.py`, API router
- **dep**: L39, L40, L41, L42. **negative_test**: FAIL인데 전략이 FAILED로 안 가거나, PASS인데 RISK_REVIEW로 안 가면 실패.

### L44. 재현성 회귀 테스트
- **paths**: `tests/foundation/integration/validation/test_reproducibility.py` + fixtures
- **dep**: L43. **negative_test**: result_hash가 고정값과 안 맞으면 실패.

### L50. 관측성 배선 + 성능 벤치마크
- **paths**: §7 메트릭/로그필드 전체, `tests/benchmarks/test_backtest_throughput.py`
- **dep**: L31, L43, L49(진행 중, 완료 확인). **negative_test**: 10만봉 리플레이가 10초 이상 걸리면 실패.

---

## §A. ★ FROZEN_PAPER_ONLY — PM 승인 필요

`src/core/{strategy,portfolio,risk,executor}/**`를 건드리는 리프. DevEngine의
Capability Token은 이 경로들을 FROZEN으로 분류해 토큰 발급 자체를 거부한다
— 아래 항목은 PM이 명시적으로 승인(Zone-Approval 트레일러 등)하고 DevEngine
쪽 FROZEN 예외 처리가 별도로 준비되기 전까지는 착수 대상에서 제외한다.

| ID | 파일 | 요지 |
|---|---|---|
| L4-10 | `core/executor/executor.py` | client_order_id를 idempotency 모듈로 교체(LIVE 가드 바이트동일 유지 필수) |
| R-01 | `core/risk/hashing.py` | 결정 해시 정규화 |
| R-02 | `core/risk/decision.py` | RiskOutcome/RiskDecision 모델 |
| R-03 | `core/risk/inputs.py` | RiskInputs 스냅샷 |
| R-04 | `core/risk/rules/base.py` | Rule Protocol |
| R-05~R-13 | `core/risk/rules/*.py`(9개) | 개별 리스크 규칙 구현(concentration/strategy_allocation 등) |
| R-14 | `core/risk/limits.py` | 노출한도 모델 |
| R-15 | `core/risk/policy_bundle.py` | 정책번들+해시 |
| R-16 | `core/risk/evaluator.py` | 규칙 평가 오케스트레이션 |
| R-17 | `core/risk/engine.py` | 레거시 엔진 축소+래핑 |
| L04 | `core/strategy/condition_ast.py` | 조건 AST |
| L05 | `core/strategy/condition_parser.py` | 조건 파서 |
| L06 | `core/strategy/indicator_key.py` | 지표 키 문법 |
| L08 | `core/strategy/state_memory.py`, `market_state.py` | 상태메모리 영속화 |
| L09 | `core/strategy/tree_evaluator.py` | AST 평가 |
| L10 | `core/strategy/{confidence,risk_params}.py` | 신뢰도/손절익절 |
| L11 | `core/strategy/models.py`, `condition_evaluator.py` | Signal 확장+파사드화 |
| L12 | `core/strategy/engine.py` | 전략엔진 오케스트레이션 |
| L17 | `core/portfolio/{models,config,state_input}.py` | 포트폴리오 모델 |
| L18 | `core/portfolio/aggregation.py` | 노출 집계 |
| L19 | `core/portfolio/sizing/*.py`(5개) | 사이징 공식 |
| L20 | `core/portfolio/mandate_binding.py` | 위임범위 클램프 |
| L21 | `core/portfolio/{accounting,rebalance}.py` | 현금원장/리밸런스 |
| L22 | `core/portfolio/engine.py` | 포트폴리오엔진 오케스트레이션 |

**착수 전 필요 조건**: PM이 각 항목(또는 그룹)에 대해 Zone-Approval 승인 +
DevEngine의 `zones.py::FROZEN_PATTERNS`에 임시 예외를 추가하거나, 검토된
PR을 별도 경로(FROZEN 전용 승인 플로우)로 처리하는 방식 확정 필요. 지금은
목록화만 하고 착수하지 않는다.
