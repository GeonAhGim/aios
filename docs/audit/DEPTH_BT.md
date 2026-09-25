# DEPTH 감사 — BT done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2728, qa-3)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/BT-/`에 맞는 리프 전수(20건, 60건 이하이므로 전수 대조).
- 축 하한: BT(백테스트)는 ADR-2026-09-09-C의 안전축(R, L4, LA/LB/LC, FA, CM, EO, DC) 목록에 없다 → 전 리프 **D2 하한**(D3 요건 없음).
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명.
- 방법: 병렬 감사 에이전트 4개(worktree 내 read-only)가 각 리프의 커밋(`git show <commit> --stat`)과 테스트 파일을 직접 열어 negative 테스트 개수·실패 주입·성능 단언·게이트 적색 재현 여부를 실측했다. 코드 수정 없음.

## 요약

- 전체 20건 중 **20건 전부 D2 하한 미달**(floor met=NO 20/20).
- 등급 분포: D0 1건(2181, 문서 산출물만) · D1 19건 · D2 0건 · D3 0건.
- 가장 흔한 단일 누락 항목: **실패 주입 부재**(20/20 전부 — 대부분 순수 도메인 함수·pydantic/ValueError 입력 검증만 있고 I/O 실패·크래시·손상 시뮬레이션이 없다)와 **게이트 적색 재현 부재**(19/20, BT-19만 예외적으로 충족). **수치 성능 단언 부재**도 17/20에서 확인되며, BT-17(2372)·BT-16a(2371)·BT-16b(2426) 3건만 실제 `assert elapsed <= threshold` 형태의 성능 단언을 갖췄다.
- D0(유령/미착수) 1건: task-2181(BT-14, 벡터 백테스트 OSS 라이선스 평가)은 `docs/design/BACKTEST_VECTOR_EVAL.md` 문서 1건만 산출되어 코드·테스트 대상 자체가 없다(문서 본문도 "범위: 문서만. 코드 변경 0"이라고 명시).
- BT-19(1748, 백테스트=라이브 패리티 하네스, I-05 강제)이 20건 중 D2에 가장 근접했다 — negative/발산 6건, 인위적 1틱 가격 어긋남 주입(실패 주입 충족), "DSL-11·BT-9로는 I-05가 실제로 강제되지 않았다"던 과거 미탐지 상태를 재현해 이제 거부됨을 증명(게이트 적색 재현 충족)했으나, latency/throughput에 대한 수치 성능 단언이 전혀 없어 4개 중 1개가 빠져 D2 미달이다.
- negative 테스트 수 자체가 3건 미만인 리프도 3건 있다: BT-17(2372, 2건), BT-11(2152, 함수 기준 2건), BT-15b(2325, 함수 기준 1건).
- BT-18(2428, SweepResultsPage.tsx)은 백엔드 라우터(`/v1/backtests/sweep`) 자체가 미구현 상태(`backtests.ts` 주석에 명시)라 프론트 테스트가 전부 mock 기반 happy-path/문구 검증에 그친다 — 실행 경로 자체가 아직 없는 유령 리프에 가깝다.
- BT-10(1504)의 5s 성능 목표는 코드 주석에 "print로만, 비차단"이라고 명시돼 있어 의도적으로 성능 단언이 빠져 있다. BT-16b(2426)의 §9.9 "1,000 조합" 절대 성능치도 print 전용 참고치이며, 실제 `assert`가 걸린 성능 게이트는 정규화 임계치(elapsed_seconds ≤ threshold) 방식이다.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2/D3 미달 근거) |
|---|---|---|---|---|---|---|---|
| 1237 | BT-1 BacktestConfigV2(domain/models_v2.py, schema_version=backtest-v2) + 스키마 스냅샷 | b7bc171 | D2 | D1 | **NO** | test_models_v2.py: negative 9종(fixed/percent slippage 음수 거부, venue tier 음수 거부, volume impact/partial_fill participation_cap 범위 초과 각 3파라미터, unknown slippage kind, negative latency_ms, negative borrow_apr, unknown calendar) — 전부 pydantic ValidationError | 실패주입 없음(순수 pydantic 검증뿐), 성능단언 없음, 게이트적색 재현 없음 |
| 1338 | BT-2~6 백테스트 체결 현실성 5모듈(slippage·commission·latency·partial_fill·order_types, 순수) | fa3afe4 | D2 | D1 | **NO** | test_fill_models.py: negative 약 20건(slippage 6, commission 3, latency 3, partial_fill 3, order_types 5) — ValueError/ValidationError | 실패주입 없음(입력값 검증뿐, I/O·크래시 시뮬레이션 없음), 성능단언 없음, 게이트적색 재현 없음 |
| 1351 | BT-7 domain/magnifier.py (bar magnifier) | ec9b9ad | D2 | D1 | **NO** | test_magnifier.py: negative 7건(LookAheadError 2, UnsortedLowerBarsError 1, IncompatibleMagnifierTimeframeError 계열 4) | monkeypatch는 방어분기 강제용일 뿐 실패주입 아님, 성능단언 없음, 게이트적색 재현 없음 |
| 1368 | BT-8 백테스트 비용 2종(domain/costs/{funding,borrow}.py) | c653bd6 | D2 | D1 | **NO** | test_cost_models.py: negative 10건(reversed period, naive datetime, non-positive interval, negative notional, nan rate, negative borrow_apr 등) — ValueError/ValidationError | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 1504 | BT-10 application/quick_backtest.py (BT-2~7 체결 모델 조립 + 컬럼 경로 통합, 1개월 M1 ≤5s) | 9a1ae87 | D2 | D1 | **NO** | test_negative_inputs_fail_closed(7개 거부 케이스)+test_window_blocks_future_bars_but_allows_negative_index(2건)=negative 9건 이상; test_round_trip_gate_detects_extra_query로 쿼리 추가 감지 | 실패주입 없음(ChattyStore는 정상응답에 쿼리 하나 추가일 뿐), 성능단언 없음(5s 목표는 주석대로 print 전용·비차단), 게이트적색 재현 불충분(메타적 회귀탐지일 뿐) |
| 1556 | BT-9 backtest/domain/reproducibility.py — 재현 키 | b0ac7ef | D2 | D1 | **NO** | test_reproducibility.py: negative 4건(empty script_hash/data_lineage_hash/rollup_version, non-backtest-config — 전부 pytest.raises(ValueError)), 결정론 단언 다수 | 실패주입 없음(입력검증 ValueError뿐), 성능단언 없음, 게이트적색 재현 없음 |
| 1625 | BT-10b 백테스트 전략 접점 브리지(script_signal_source.py) | fe6182b | D2 | D1 | **NO** | test_script_signal_source.py: negative 8건(length mismatch, side identifier 불량, qty_expr 불량, zero/NA quantity, opts 오검출, columns length mismatch) — ScriptSignalSourceError | 실패주입 없음(순수 컴파일/구조 검증), 성능단언 없음, 게이트적색 재현 없음 |
| 1619 | BT-10c 즉시 백테스트 HTTP API(POST /v1/backtests/quick) | 9ccb238 | D2 | D1 | **NO** | test_backtests_router.py: negative 6건(too_many_bars 400, script_compile_error 400, zero_candles 400, unauthenticated 401, cross_tenant 403, scope denial) + 왕복 1회 검증 | 실패주입 없음(MAX_QUICK_BARS 상수 하향은 값싼 재현일 뿐), 성능단언 없음(호출횟수 카운트뿐), 게이트적색 재현 없음 |
| 1607 | BT-13 차트 즉시 백테스트 패널(BacktestPanel.tsx) + 결과 마커 차트 오버레이 | af035c6 | D2 | D1 | **NO** | BacktestPanel.test.tsx: negative 4건(봉 상한 초과, 스크립트 컴파일 오류, 표시할 캔들 없음, 실행 중 재요청 차단) | 실패주입 없음(mockRejectedValue는 백엔드 400 응답 흉내일 뿐), 성능단언 없음, 게이트적색 재현 없음 |
| 1748 | BT-19 백테스트=라이브 패리티 하네스 (I-05 강제) | 102c1d2 | D2 | D1 | **NO** | test_parity_harness.py: negative/발산 6건(ParityMismatchError); 실패주입 있음(인위적 1틱 가격 어긋남 주입); 게이트적색 재현 있음(DSL-11·BT-9로는 I-05 미강제였던 상태를 1틱 발산 시나리오로 재현해 거부 확인) | 성능단언 없음(latency/throughput assert 전무) — 이 1개 항목만 빠져 D2 미달(20건 중 가장 근접). D3급 동시성/실캡처 트레이스 재생 증거도 없음(합성 fixture) |
| 1754 | BT-20 백테스트 기업행위 반영 | fa5bdbc | D2 | D1 | **NO** | test_corporate_actions.py: negative 7건(reversed window, naive bar time, non-positive ratio/prior_close, negative amount/price/quantity) | 실패주입 없음, 성능단언 없음, 게이트적색 재현 없음 |
| 2152 | BT-11 application/deep_backtest_job.py (체크포인트 중단·재개·진행률) | ba3b308a | D2 | D1 | **NO** | test_deep_backtest_job.py: negative 함수 2건(config_hash mismatch fail-closed, empty columns/blank job_id 내부 raises 2건 — 총 assertion 3건이나 함수 기준 미달) | negative 함수 기준 3건 미달, 실패주입 없음(체크포인트 저장소가 인메모리 dict), 성능단언 없음, 게이트적색 재현 없음 |
| 2181 | BT-14 docs/design/BACKTEST_VECTOR_EVAL.md — 벡터 백테스트 OSS 라이선스 평가 | 1b53ad92 | D2 | D0 | **NO** | 변경 파일이 문서 1개(+64줄)뿐, 코드·테스트 0건. 본문에 "범위: 문서만. 코드 변경 0" 명시 | 테스트 표면 자체 없음(4요소 전부 해당 없음) — 실질 미착수 |
| 2324 | BT-15a 백테스트 벡터 엔진 코어 1/2: vector/{arrays,signals}.py | 13e16cc2 | D2 | D1 | **NO** | test_vector_arrays_signals.py: negative 6건(mismatched lengths, wrong dtype, negative offset, bool signal shape/dtype mismatch 등) — runtime.series와 원소단위 동등성 검증 포함 | 실패주입 없음(입력검증뿐, 예외강제 monkeypatch 없음), 성능단언 없음, 게이트적색 재현 없음(동등성 대조이지 과거 적색 게이트 재현 아님) |
| 2325 | BT-15b 벡터 엔진 코어 2/2: vector/fills.py + 이벤트 엔진 동등성 | 0c4971a9 | D2 | D1 | **NO** | test_vector_event_parity.py: negative 함수 1건(내부 pytest.raises 3건); test_future_candle_values_after_execution_do_not_change_earlier_fill이 미래 봉 오염으로 미래참조 누출 반증(실패주입/게이트적색 재현에 준함) | negative 함수 기준 3건 미달(1건), 성능단언 없음(latency_ms는 설정 필드명일 뿐) |
| 2372 | BT-17 backtest/vector/universe.py — 다종목 유니버스 스윕(컬럼 경로·메모리 상한) | 77871f67 | D2 | D1 | **NO** | test_vector_universe.py: negative 2건(UniverseMemoryLimitError); 성능단언 있음(`assert elapsed_seconds <= threshold_seconds`, 정규화 임계) | negative 3건 미달(2건뿐), 실패주입 없음, 게이트적색 재현 없음 |
| 2371 | BT-16a 벡터 대량 실행 3모듈(grid·walk_forward·monte_carlo) — 실행부만 | f76a07ee | D2 | D1 | **NO** | test_vector_sweeps.py: negative 8건(window spec, walk_forward 빈 조합/짧은 데이터, grid bad combo, monte_carlo non-positive iterations/짧은 곡선/범위밖 percentile/zero segment); 성능단언 있음(정규화 임계) | 실패주입 없음(입력검증뿐), 게이트적색 재현 없음 |
| 2426 | BT-16b 벡터 대량 실행 잔여: 실험 원장(재현 키 기록) + 1,000 조합 성능 단언 | e9be3488 | D2 | D1 | **NO** | test_experiment_ledger.py: negative 3건(blank combo_key, negative combo_index, script_hash mismatch); tests/performance/test_vector_grid_throughput.py: 실제 수치 성능단언 있음(43,200봉×200조합 실측, 9배 정규화 게이트) | 실패주입 없음(순수 함수·입력검증뿐), 게이트적색 재현 없음(§9.9 절대 1,000조합 수치는 print 전용 비차단 참고치) |
| 2159 | BT-12 application/tearsheet.py (성과 리포트 — performance 계약 재사용, 스냅샷 결정론) | 072e931f | D2 | D1 | **NO** | test_tearsheet.py: negative 1건(empty equity curve ValueError), 결정론 스냅샷 테스트 1건 | negative 3건 미달(1건뿐), 실패주입 없음, 성능단언 없음(시간측정 자체 없음), 게이트적색 재현 없음 |
| 2428 | BT-18 SweepResultsPage.tsx — 파라미터 스윕 히트맵·안정성 표면·재현 키 | d55b9e63 | D2 | D1 | **NO** | SweepResultsPage.test.tsx: 4개 it() 전부 happy-path/UI 상태 렌더링(안내 문구, 히트맵 셀, 축 분기, SweepRouteNotImplementedError 메시지) — runSweep은 매번 mock 대체, 백엔드 라우터(`/v1/backtests/sweep`) 자체 미구현 | negative 테스트 없음, 실패주입 없음(mockRejectedValue는 UI 문구 확인용), 성능단언 없음, 게이트적색 재현 없음 — 실행 경로 자체가 유령(서버 라우터 미구현) |

## 발행된 DEEPEN task

D2 하한 미달 20건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 2, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id)은 다음과 같다.

| 원 task id | DEEPEN task id |
|---|---|
| 1237 | 3036 |
| 1338 | 3037 |
| 1351 | 3038 |
| 1368 | 3039 |
| 1504 | 3040 |
| 1556 | 3041 |
| 1625 | 3042 |
| 1619 | 3043 |
| 1607 | 3044 |
| 1748 | 3045 |
| 1754 | 3046 |
| 2152 | 3047 |
| 2181 | 3048 |
| 2324 | 3049 |
| 2325 | 3050 |
| 2372 | 3051 |
| 2371 | 3052 |
| 2426 | 3053 |
| 2159 | 3054 |
| 2428 | 3055 |
