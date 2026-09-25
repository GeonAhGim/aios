# DEPTH 감사 — DC/RD done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2726, qa-3)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/DC-|RD-/`에 맞는 리프 전수(35건, 60건 이하이므로 전수 대조).
- 축 하한: 제목이 `DC-`로 시작하는 리프(27건)는 ADR-2026-09-09-C의 안전축(R, L4, LA/LB/LC, FA, CM, EO, DC) 중 DC(데이터)에 해당 → **D3 하한**. 제목이 `RD-`로 시작하는 리프(8건)는 축 코드 목록에 없음 → **D2 하한**.
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명.
- 방법: 병렬 감사 에이전트 5개(worktree 내 read-only)가 각 리프의 커밋(`git show <commit> --stat`)과 테스트 파일을 직접 열어 negative 테스트 개수·실패 주입·성능 단언·게이트 적색 재현·D3 요소 여부를 실측했다. 코드 수정 없음.

## 요약

- 전체 35건 중 **35건 전부 자기 축의 D 하한 미달**(meets_floor=false 35/35).
- 등급 분포: D0 2건(1766, 2401) · D1 33건 · D2 0건 · D3 0건.
- D3 하한(DC-) 대상 27건 중 미달 27건. D2 하한(RD-) 대상 8건 중 미달 8건.
- 가장 흔한 단일 누락 항목: **수치 성능 단언 부재**(35/35 전부, 예외 없음). 다음으로 **게이트 적색 재현 부재**(35/35 전부 — 1768이 과거 UNVERIFIED 불변식 반전으로 근접하나 결정적이지 않음). 실패 주입은 순수 도메인 함수 리프(예: 2071, 2131, 2554, 1127 등)에서는 원천적으로 성립하지 않고, I/O가 있는 리프(1187, 1195, 1765, 2158, 2349, 2350, 2427/2455의 위임 monkeypatch, 2463의 실 DB WORM 트리거)에서만 부분 충족된다.
- D0(유령/미착수) 2건: task-1766(RD-19, 암호화폐 L2 자체 수집기)은 변경 파일이 alembic dual-head merge 스텁 1개뿐이고 코드·테스트가 전혀 없다. task-2401(RD-1, 리서치 데이터 소스 평가)은 `docs/design/RESEARCH_DATA_SOURCE_EVAL.md` 문서 1건만 산출되어 코드/테스트 대상이 원천적으로 없다.
- DC-6(1127)는 negative 테스트 자체가 2건뿐으로 D2 최소 기준(≥3)에도 못 미쳐 27건 중 가장 얕다. DC-14(2349)·DC-15(2350)·DC-16(2158)·DC-23(2553)은 negative≥3과 failure-injection까지 갖춰 D2에 가장 근접했으나 성능 단언·게이트 적색 재현이 빠져 여전히 D2 미충족(D3 대상이라 격차는 더 크다).
- RD 축에서는 RD-3(2427)·RD-4(2463)·RD-20(1767)이 negative를 풍부하게 갖췄고 RD-4는 실 DB WORM 트리거 거부까지 실증했으나, 셋 다 성능 단언·게이트 적색 재현이 없어 D2에 못 미친다.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2/D3 미달 근거) |
|---|---|---|---|---|---|---|---|
| 1123 | DC-1 market_data contracts/v2 instruments (Instrument·VenueListing·InstrumentLifecycle) + 스키마 스냅샷 | 1e3d05a | D3 | D1 | **NO** | test_instruments_v2.py: negative 7+(naive datetime, 잘못된 ULID, 필수필드누락, invalid currency/country/mic), 스키마 스냅샷 회귀가드 1건 | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 적대적/리플레이/동시성 없음 |
| 1125 | DC-3 domain/instruments/lifecycle.py (§4.2 심볼 생애주기 전이표, 순수 상태기계) | dc87483 | D3 | D1 | **NO** | test_lifecycle.py: 전이표 전수 파라미터화(test_transition_matches_table), test_delisted_rejects_every_other_event 등 다수 negative | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1124 | DC-2 domain/instruments/symbol_master.py (instrument_id 발급·벤처 심볼 매핑·충돌 규칙, 순수) | 7ffefc0 | D3 | D1 | **NO** | test_symbol_master.py: negative 11개(InstrumentNotFoundError, SymbolMasterError×5, SymbolConflictError×3, RelistingReuseError) | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1126 | DC-5 market_data ports 3파일(provider SPI·instrument_repository·coverage_repository) | 554f078 | D3 | D1 | **NO** | test_ports_protocol.py: 구조체 vs DTO 불일치·미구현 포트 거부 등 negative 3건 | 실패주입 없음(DB/네트워크), 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1127 | DC-6 domain/coverage/registry.py (커버리지 선언 질의·병합, 순수) | 1920723 | D3 | D1 | **NO** | test_registry.py: negative 2개뿐(test_coverage_span_rejects_end_before_start, ...rejects_zero_length) | negative<3(27건 중 최저), 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1153 | DC-4 마이그레이션 instruments/venue_listings(EXCLUDE USING gist) + 통합 테스트 | f09026f | D3 | D1 | **NO** | test_instruments_schema.py(실 Postgres): negative 3개(ExclusionViolationError 2건, instrument_id 불변 CheckViolationError) — 실 DB 제약 위반 직접 유발 | 성능단언 없음, 게이트적색 재현 없음, D3 다중 인스턴스/워커 동시성 증명 없음(단일 pool, 순차) |
| 1154 | DC-10 domain/aggregation/timeframe_rollup.py (M1→파생 TF 결정론 집계·rollup_version) | a9f6509 | D3 | D1 | **NO** | test_timeframe_rollup.py: negative 3개(InvalidRollupTargetError, MismatchedColumnLengthError, UnsortedCandlesError) + 결정론 테스트 | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1179 | DC-9 domain/entitlement/policy.py (테넌트·사용자 데이터 이용권 판정, 거부 403) | 99c6c7b | D3 | D1 | **NO** | test_policy.py: negative 11개 이상(NO_GRANT, EXPIRED×2, OUT_OF_SCOPE×3, TENANT_MISMATCH 등) | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1178 | DC-7 domain/coverage/gaps.py (커버리지 갭 fail-closed 판정, LA-5 재사용) | b1dbedc | D3 | D1 | **NO** | test_gaps.py: negative 4건(IndeterminateCoverageError 계열) + 결정론 정렬 테스트 | 실패주입 없음(순수 함수), 성능단언 없음, 게이트적색 재현 없음 |
| 1187 | DC-11 adapters/providers/base_adapter.py (SPI 공통 토큰버킷·재시도·정규화 훅) | b0b8bed | D3 | D1 | **NO** | test_base_adapter.py: negative 6건, 실패주입 있음(fake op가 DataProviderError·ValueError 강제) | 수치 성능단언 없음(sleeper.calls 카운트뿐), 게이트적색 재현 없음 |
| 1195 | DC-8 마이그레이션 coverage_spans(EXCLUDE)/entitlements + Postgres 어댑터 2종 + 통합 | d332f07 | D3 | D1 | **NO** | 3개 통합테스트 파일: negative 14건 이상(EXCLUDE/Unique/Check Violation 등), 실패주입 있음(실 DB 제약 위반 강제) | 성능단언 없음, 게이트적색 재현 없음 |
| 1211 | DC-12 adapters/providers/bitget_provider.py·kis_provider.py (기존 거래소 어댑터 SPI 재배치) + 계약 테스트 | 5ce38ce | D3 | D1 | **NO** | test_bitget/kis_provider.py: negative 각 4~5건(DATA_COVERAGE_MISSING, 심볼 미해석 등) | 실패주입 없음(fake가 정상/빈 데이터만 반환), 성능단언 없음, 게이트적색 재현 없음 |
| 1212 | DC-13 adapters/storage/hot_postgres.py (hot 계층 읽기/쓰기, instrument_id 키) + 통합 테스트 | 0b17502 | D3 | D1 | **NO** | test_hot_postgres.py: p50/p95 실측하나 print만(비차단), round_trip_count 구조 가드; 경계 테스트 3건(빈 결과류) | pytest.raises 기반 negative 없음, 실패주입 없음, 수치 성능단언 의도적으로 비차단, 게이트적색 재현 없음 |
| 1764 | DC-27 source_contract — 소스 계약 등급·재배포 스코프 (기존 entitlements 확장) | 532c125 | D3 | D1 | **NO** | test_source_contract.py: negative 8건 이상(EXPIRED/NOT_YET_VALID/NOT_FOUND, naive datetime, credential_ref 유출 방지) | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 1765 | DC-28 재배포 스코프 강제 — 읽기·차트·내보내기 + USER_SCOPED 유출 차단 | a6f8e5b | D3 | D1 | **NO** | test_candle_history.py: 실패주입 2건(adapter.fail=True), 교차사용자 유출 차단 1건(단일 인스턴스) | 성능단언 없음, 게이트적색 재현 없음(owner_id가 신규 필수 인자라 사전코드 재현 불가) |
| 2068 | DC-19 market_data contracts/v2/microstructure.py (TradeTick·QuoteL1·BookL2, 나노초 정수) | 54689b71 | D3 | D1 | **NO** | test_microstructure_v2.py: negative 7건(초단위/naive ts, aggressor 표밖값, BookL2 정렬위반 등), 스키마 스냅샷 가드 | 실패주입 없음(pydantic 검증뿐), 성능단언 없음, 게이트적색 재현 없음 |
| 2071 | DC-20 contracts/v2/instruments.py 확장(파생 심볼: kind·underlying_id·expiry·strike·option_right·multiplier) | 24e25a66 | D3 | D1 | **NO** | test_instruments_v2.py: negative 3개(invalid currency/country/mic, ValidationError) | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 2131 | DC-22 domain/aggregation/tick_to_candle.py — 틱/호가 → 캔들 결정론 생성 + source_kind 계보 | f702898b | D3 | D1 | **NO** | test_tick_to_candle.py: negative 2개(UnsortedTicksError, MixedSeriesError) + 결정론 해시 테스트 | negative<3, 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2130 | DC-21 point_in_time 참조데이터(known_at 규칙) + instrument_attributes append-only 마이그레이션 | ae186957 | D3 | D1 | **NO** | test_point_in_time.py(위임 스파이) + test_instrument_attributes.py(실 DB): negative 3개, WORM UPDATE/DELETE 거부(asyncpg.RaiseError) | 성능단언 없음, 게이트적색 재현 없음, D3 다중 인스턴스/리플레이 없음 |
| 2195 | DC-18a 커버리지 조회 API GET /v1/foundation/market-data/coverage (LA-24 read_api 확장) | ddacfca6 | D3 | D1 | **NO** | test_get_coverage.py: 4개 테스트, 전부 "빈 리스트" 방어적 검증뿐 | negative(raises) 테스트 없음, 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2196 | DC-18b CoverageBadge — 차트·시장데이터 화면에 지연/미커버 구간 표기 | 370d6507 | D3 | D1 | **NO** | CoverageBadge.test.tsx: 렌더링 edge case 6개, 예외/reject 테스트 0건 | negative 테스트 없음, 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2330 | DC-17 application/realtime_fanout.py (실시간 팬아웃 — 권한 필터·backpressure drop 메트릭) | ff2b6fa1 | D3 | D1 | **NO** | test_realtime_fanout.py: 명시적 거부 1개, backpressure drop=500 정확 카운트(성능 단언 아님) | negative<3, 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2349 | DC-14 adapters/storage/warm_parquet.py (종목×연도 parquet 왕복 — CandleColumns 직접 로드) | aded6e7c | D3 | D1 | **NO** | test_warm_parquet.py: negative 4개, 실패주입 1개(parquet 파일 절반 절단 → 손상 시뮬레이션) | 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음(negative·실패주입은 충족) |
| 2350 | DC-15 adapters/storage/tiering.py (hot→warm 승격·아카이브 멱등 + 계보 기록) | bd9adb48 | D3 | D1 | **NO** | test_tiering.py: negative 2개, 실패주입 2개(_TamperingWarm 읽기조작, _CrashOnceWarm 쓰기중단) | negative<3, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 2158 | DC-16 application/backfill_job.py (갭 계획→백필→커버리지 갱신, 중단 후 재개) | e4d23ca4 | D3 | D1 | **NO** | negative 3개, 실패주입 1개(fake provider가 미등록 구간 요청 시 KeyError로 장애 흉내) | 성능단언 없음, 게이트적색 재현 없음, D3 적대적/리플레이/다중 워커 증명 없음 |
| 2553 | DC-23 adapters/storage/tick_parquet.py — 틱·호가 warm 저장(일자×종목 파티션, 컬럼지향 직접 로드) | 2899d6f3 | D3 | D1 | **NO** | negative≥4, 실패주입 1개(parquet 파일에 손상 바이트 강제 주입 → pa.ArrowInvalid); 메모리 상한 assert는 있으나 지연/처리량 아님 | 성능(p95/처리량) 단언 없음, 게이트적색 재현 없음, D3 다중 인스턴스/리플레이 없음 |
| 2554 | DC-25 domain/instruments/roll.py — 파생 만기 롤 규칙 + 연속선물 시리즈(비율·차분 조정) | 0c301917 | D3 | D1 | **NO** | negative 매우 풍부(invalid_contract 6종, expiry_order, bad_prices 4종, invalid_history 4종) | 순수 도메인 함수라 I/O 실패주입 부재, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음 |
| 1768 | RD-21 거래소 캘린더 1차 출처 수집 — source: UNVERIFIED 해소 | 0c08b34c | D2 | D1 | **NO** | negative 3개(missing collected_at/source 필드 거부 등); test_load_calendar_parses_krx_yaml_with_verified_source가 과거 UNVERIFIED 불변식 반전으로 게이트적색에 근접 | 실패주입(DB/네트워크) 없음, 성능단언 없음; 게이트적색 재현이 결정적이지 않음 |
| 1766 | RD-19 암호화폐 L2 자체 수집기 — 오늘부터 쌓는다 (벤더 불필요 영역) | 5406db6e | D2 | D0 | **NO** | 변경 파일이 alembic dual-head merge 스텁 1개뿐, 코드·테스트 전무 | negative/실패주입/성능/게이트 4요소 전부 증거 없음 — 실질 미착수 |
| 1767 | RD-20 전자공시 기업행위 파이프라인 — 벤더가 재판매하는 원본을 직접 쓴다 | bd64ef58 | D2 | D1 | **NO** | negative 8개 이상(split/dividend 필드 누락, naive known_at 등); PIT 정정 무결성 실DB 테스트 존재 | 실패주입(DB/네트워크) 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2401 | RD-1 리서치 데이터 소스 평가(계층 A 8종 — 약관 원문 인용·redistribution 판정·rate limit 실측) | e1c03575 | D2 | D0 | **NO** | 변경 파일이 docs/design/RESEARCH_DATA_SOURCE_EVAL.md 문서 1건뿐 | 코드/테스트 전무 — 문서 산출물이라 테스트 대상 자체가 없음 |
| 2402 | RD-2 research_data contracts/v1 + domain/known_at.py (FA-9 bitemporal 위임, known_at 없는 항목 거부) | 20e89594 | D2 | D1 | **NO** | negative≥5(naive known_at/published_at, missing known_at, 1us 경계 등); 위임 검증(monkeypatch)으로 FA-9 재구현 금지 실증 | monkeypatch는 계약 검증용이라 실패주입 불충족, 성능단언 없음, 게이트적색 재현 없음 |
| 2455 | RD-5 research_data domain/entity_link.py + application/link_entities.py — 확정 키만 매핑(이름 추측 금지) | 47b02706 | D2 | D1 | **NO** | negative 6개(assert 기반, pytest.raises 없음): 이름만으로 결정론적 키 불가·ISIN 체크섬 변조 거부·미매핑 시 예외 없이 unmapped 등 | 실패주입(DB/네트워크/크래시) 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2427 | RD-3 research_data domain/{redistribution,revision}.py — link_only 본문 저장 금지·정정 체인 | 66961cec | D2 | D1 | **NO** | negative 11개(pytest.raises: link_only 1자 본문 거부, 분기/순환 참조 거부, known_at 역전 거부 등) | 인프라 실패주입 없음(위임 monkeypatch는 계약 검증), 성능단언 없음, 게이트적색 재현 없음 |
| 2463 | RD-4 마이그레이션 research_items(WORM)·research_sources·매핑 + postgres_repository | 3b5a6e82 | D2 | D1 | **NO** | negative 5개; 실패주입 충족(실 Postgres WORM 트리거가 aios_app·테이블 소유자 양쪽 UPDATE/DELETE 모두 거부, asyncpg.RaiseError/InsufficientPrivilegeError) | 수치 성능(지연/처리량) 단언 없음, 게이트적색 재현 없음 |

## 발행된 DEEPEN task

D3(DC-)/D2(RD-) 미달 35건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 1, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 1123 | 2874 |
| 1125 | 2875 |
| 1124 | 2876 |
| 1126 | 2877 |
| 1127 | 2878 |
| 1153 | 2879 |
| 1154 | 2880 |
| 1179 | 2881 |
| 1178 | 2882 |
| 1187 | 2883 |
| 1195 | 2884 |
| 1211 | 2885 |
| 1212 | 2886 |
| 1764 | 2887 |
| 1765 | 2888 |
| 2068 | 2889 |
| 2071 | 2890 |
| 2131 | 2891 |
| 2130 | 2892 |
| 2195 | 2893 |
| 2196 | 2894 |
| 2330 | 2895 |
| 2349 | 2896 |
| 2350 | 2897 |
| 2158 | 2898 |
| 2553 | 2899 |
| 2554 | 2900 |
| 1768 | 2901 |
| 1766 | 2902 |
| 1767 | 2903 |
| 2401 | 2904 |
| 2402 | 2905 |
| 2455 | 2906 |
| 2427 | 2907 |
| 2463 | 2908 |
