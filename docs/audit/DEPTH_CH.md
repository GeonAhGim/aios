# DEPTH 감사 — CH done 리프 D0~D3 판정 (ADR-2026-09-09-C)

- 감사일: 2026-09-10 (task-2729, qa-4)
- 대상: `pm/tasks`의 done implement task 중 제목이 `/CH-/`에 맞는 리프 전수(44건, 60건 이하이므로 전수 대조).
- 축 하한: CH(차트)는 ADR-2026-09-09-C의 안전축 목록(R, L4, LA/LB/LC, FA, CM, EO, DC)에 없다 → **D2 하한**(안전축 D3 적용 대상 아님). 44건 전부 동일하게 D2 하한을 적용한다.
- 판정 기준(ADR-2026-09-09-C): D0=스텁, D1=happy path+단위 테스트, D2=경계·실패·동시성·복구 테스트 **전부** 충족(negative≥3, 실패 주입 1, 성능 단언 1, 게이트 적색 재현 1), D3=D2 + 적대적/리플레이/다중 인스턴스 증명.
- 방법: 각 리프의 커밋(`git show <commit> --stat`)과 테스트 파일을 직접 열어 `test(`/`it(`/`describe(`(프론트) 또는 `def test_`(백엔드) 개수, negative-path 단언, `mockRejectedValue`/`monkeypatch`/`MagicMock`/`FakeWorker` 기반 실패 주입, `toBeLessThan(ms)`/`perf_counter` 등 수치 성능 단언, CI 게이트가 실제로 적색이 되는지 증명하는 before/after 테스트 유무를 실측했다. 코드 수정 없음. 44건을 4개 배치로 나눠 병렬 실측한 뒤 통합했다.

## 요약

- 전체 44건 중 **44건 전부 D2 미달**(meets_floor=false 44/44, 0/44 충족).
- 등급 분포: D0 5건(1133, 1509, 2052, 2053, 2097) · D1 39건 · D2 0건 · D3 0건.
- 가장 흔한 단일 누락 항목: **게이트 적색 재현 테스트 부재**(44/44 전부). 여러 커밋 메시지가 "이 배선을 되돌리면 테스트가 N건 FAIL한다"는 주장을 남겼지만(2001, 2012, 2038, 2044, 2045, 2127 등) 실제로 자동화된 before/after 단언이 테스트 파일 안에 존재하는 경우는 하나도 없다 — 전부 저자의 수기 확인 서술뿐이다. 두 번째로 흔한 항목은 **수치 성능 단언 부재**(43/44 — 유일한 예외는 2043의 `expect(result.length).toBeLessThanOrEqual(2000)` 다운샘플 상한 단언이나, ms/fps가 아니라 카운트 상한이라 완전한 성능 단언으로 보기엔 약하다). 렌더 성능을 직접 다루는 CH-19a/b(1958, 1959)조차 ms/fps 수치 단언이 없다 — 1959의 밀도 벤치 CI 래칫(`density_bench.mjs`)도 baseline 대비 상대 회귀율(20%)만 검사할 뿐 고정 ms/fps 임계값이 없다.
- 실패 주입(mocked network/DB/exception)을 보유한 리프는 소수(1569 `mockRejectedValue`, 2012 `mockRejectedValue`, 2039 `FakeWorker.onerror`/`pool.submit` 강제 reject, 1953 워커 콜백 강제 throw, 1954 수치 편차 주입)뿐이다. 나머지 다수는 순수 도메인 검증 예외(1616, 1710, 1711, 1732, 1809 등, fetch/DB 의존 자체가 없어 실패 주입이 구조적으로 불가능)이거나 실 Postgres 제약 위반(1557, 1904 — 진짜 시스템 동작이지 모의 실패가 아니라 인정하지 않음)이다.
- 완전 미시작/조사 전용 2건: task-1133(CH-0, 포크 후보 채점표+10만봉 fps 벤치 스크립트 — `bench_chart_candles.mjs`는 pass/fail 단언 없이 JSON 리포트만 출력)과 task-2053(CH-14d, **커밋 자체 없음** — `tsc -b --force` 프로브 2회로 "코드 변경 불필요" 결론에 도달한 정당한 조사 종료, note에 재현 근거 기록)은 애초에 테스트 가능한 코드 변경이 없어 D0.
- 순수 CI 인프라 커밋 2건도 D0: task-1509(P0 lockfile 워크스페이스 항목 1건 추가, CH-1a 후속 CI green화 커밋 — 정규식이 커밋 메시지 속 "CH-1a" 언급에 매치된 것에 가깝고 실질적 CH 구현 리프는 아니다, CM-2099류와 유사한 패턴)과 task-2052(CH-19d 밀도 벤치 CI 배선 — npm script 등록·baseline 수치 갱신뿐, 동반 vitest 테스트 파일 0건).
- task-2097(CH-19e 밀도 벤치 절대 임계+다중런 중앙값)은 CH 세트 전체에서 가장 강한 "실제 ms 수치 절대 임계값" 후보(`panZoomFrameMsP95<=16.7ms` 등 CH-19 명세 3종)를 코드로 갖추지만, 이 커밋 자체에는 그 로직(`checkAbsoluteThresholds`/`measureCalibMs`)을 검증하는 단위테스트가 0건이라(검증 테스트 `densityRatchet.test.mjs`는 이후 별도 task-2479/a5b0d938에서 추가됨, 이 리프 범위 밖) D0으로 판정했다.
- task-1559(CH-6a)에 원래 배정된 커밋 `8ec0980`은 실제로는 task-1558의 1줄짜리 `tsc -b` 빌드 수정이며 CH-6a 실 구현이 아니다 — 진짜 구현 커밋 `02a788dc`로 대체해 채점했다.

## 전수 판정표

| id | title | commit | axis | grade | floor met | evidence | missing (D2 미달 근거) |
|---|---|---|---|---|---|---|---|
| 1133 | CH-0 차트 엔진 포크 평가(후보 4종 채점표 + 10만 봉 fps 벤치 스크립트) | b0826da | D2 | D0 | **NO** | docs/design/CHART_ENGINE_FORK_EVAL.md(139줄 채점표) + bench_chart_candles.mjs(317줄, esbuild+puppeteer-core 헤드리스 Chrome 실측, 미설치 시 UNMEASURED로 우아 실패) | 유닛테스트 파일 0건, pass/fail 단언 없이 JSON 리포트만 출력, negative-path 0, 실패 주입 0, 게이트 적색 재현 0, D3 없음 — docs+측정스크립트 리프라 테스트 인프라 자체가 없음 |
| 1375 | CH-1a chart-engine 패키지 골격 + workspace 등록 + src/core 래퍼 계약 + 테스트 | 87da8d1 | D2 | D1 | **NO** | renderer/series/priceScale/timeScale.test.ts 총 19 tests; negative≥12(RangeError 8건, "already exists"·"disposed" 사용후 거부 4건) | 실패 주입(mock network/DB) 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1401 | CH-3 chart-engine indicators/overlayRegistry.ts (페인·오버레이 매핑) | df1fd5f | D2 | D1 | **NO** | overlayRegistry.test.ts; negative≥9(CHART_OVERLAY_DUPLICATE/UNKNOWN/INVALID it.each 6건+fail-fast 2건+resolve 미지id); backend spec.py/specs_talib.py 실파일 파싱 drift guard 4건 | drift guard는 실파일 비교일 뿐 monkeypatch식 실패 주입 아님, 수치 성능 단언 없음, "가드 우회시 CI 적색" 재현 테스트 없음, D3 없음 |
| 1402 | CH-4 chart-engine drawings/{model,tools,serialize}.ts (도구 5종·직렬화 왕복) | 8bd4077 | D2 | D1 | **NO** | model/tools/serialize.test.ts; negative≥14(it.each invalid drawing) + seeded PRNG 왕복 300회·이동역연산 200회 property test | 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1501 | CH-1b chart-engine vendor/klinecharts 포크 반입(LICENSE·NOTICE-AIOS.md) + src/core 래퍼→vendor 위임 | 03f5929 | D2 | D1 | **NO** | klinechartsBackend.test.ts 8 tests, jsdom 스텁으로 실 vendor 인스턴스 mount·시리즈 생성/업데이트/리사이즈/좌표변환 위임 검증 | negative-path 0건(전부 happy-path 통합), 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1509 | P0 CI 적색(frontend): package-lock.json에 @aios/chart-engine@0.0.0 워크스페이스 항목 누락 — CH-1a 후속 수정 | e095abc | D2 | D0 | **NO** | package-lock.json diff +11/-0(워크스페이스 항목 1건)뿐, 테스트 파일 변경 없음. CH-1a(87da8d1)의 lock 누락을 고치는 순수 CI green화 커밋이며 실질 CH 구현 리프가 아니다(정규식이 커밋 메시지 속 "CH-1a" 언급에 매치) | 테스트 없음, D2 4요건 전부 없음 |
| 1523 | CH-2 chart-engine data/candleStream.ts (페이지네이션+실시간 병합, 중복·역순·갭 fail-closed) | df59201 | D2 | D1 | **NO** | candleStream.test.ts 19 tests; negative≥14(parse_invalid·unsupported_schema_version·key_mismatch×4·invalid_time×2·confirmed_regression×3·duplicate_conflict·disposed×2); seeded shuffle 5 seed 결정론 | 실패 주입 없음(fakeSource는 도메인 스텁, 네트워크/DB 실패 시뮬레이션 아님), 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1539 | CH-7 chart-engine replay/replayController.ts(재생·속도·스텝·seek, 백테스트 동일 데이터 경로) | 527e719 | D2 | D1 | **NO** | replayController.test.ts 12 tests; negative≥7(RangeError 5건, dispose후 거부 2건); 백테스트 골든 시퀀스와 동일성 단언; I-10 배선 정적 검사(fetch·파서·전역타이머 부재 grep) | 실패 주입 없음, 수치 성능 단언 없음(정적 소스 grep일 뿐), 게이트 적색 재현 없음, D3 없음 |
| 1557 | CH-5 backend charting 컨텍스트(chart_layouts·drawings 계약·마이그레이션·저장소·API, 낙관적 잠금 409·타 테넌트 404) | 06e5560 | D2 | D1 | **NO** | test_rules.py negative 14건(pytest.raises); test_charting_lifecycle.py 실 Postgres 통합 9건(409 ConcurrencyConflictError, 404 CrossTenantChartLayoutAccessError×3, malformed document 6종 parametrize); test_schema_snapshot.py 계약 스냅샷 | mock 기반 실패 "주입" 0건(실 DB 동시성/404는 진짜 시스템 동작이지 모의 실패 아님), 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1559 | CH-6a 차트 화면 조립 1/2: routes/chart/ChartPage.tsx + ChartToolbar.tsx + IndicatorPicker.tsx | 02a788dc(실 구현 커밋, 지시된 8ec0980은 task-1558 tsc 빌드수정 1줄) | D2 | D1 | **NO** | ChartPage/ChartToolbar/IndicatorPicker.test.tsx 총 19 tests; negative 명시 5건 | fetch 실패(reject) 주입 테스트 없음(fetchCandles 항상 성공 mock), 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1569 | CH-10 chart StrategyMarkers.tsx — PAPER 실행의 신호·체결 마커를 ChartPage에 표시 | 6f526b6 | D2 | D1 | **NO** | StrategyMarkers.test.tsx 9 tests; **실패 주입 2건**(getPositionJournal.mockRejectedValue ApiError 404/403 → NotFoundState/ForbiddenNotice 분기); negative-path 3건(404·403·미매칭 포지션 시 저널 미요청 가드) | 수치 성능 단언 없음, 게이트 적색 재현 없음 — 4요건 중 2개 미충족 |
| 1570 | CH-9 chart AlertFromChart.tsx — 차트 컨텍스트에서 가격/지표 알림 규칙 생성 | 9554f6b | D2 | D1 | **NO** | AlertFromChart.test.tsx 12 + ChartToolbar.test.tsx 배선증명 3 = 15 tests; negative 3(400 VALIDATION_INVALID_FIELD·403 POLICY_LIVE_BLOCKED·429 RATE_LIMIT_EXCEEDED, mockRejectedValue ApiError 실패 주입); 포커스 트랩·Esc 닫기 접근성 테스트 | 수치 성능 단언 없음, 게이트 적색 재현 없음(다이얼로그 마운트 배선은 통합 테스트뿐, before/after 아님), D3 없음 |
| 1593 | CH-8 chart-engine layout/{layoutModel,persistence}.ts + api-client charting 클라이언트(멀티차트·워치리스트) | a74d6b4 | D2 | D1 | **NO** | persistence.test.ts 11 tests(negative 7: 404·409·5xx rethrow·malformed layoutState/drawings), charting.test.ts 14 tests(negative 3: 409·404·필수필드 누락 throw); mockRejectedValue/stubFetch로 서버 실패 주입 | 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1594 | CH-6b 차트 화면 조립 2/2: ChartPage 레이아웃 서버 저장·복원 배선(CH-8 소비, 멀티차트 패널·워치리스트 복원 + 409 충돌 안내) | 5bcd92b | D2 | D1 | **NO** | ChartPage.test.tsx 신규 3 + useChartLayout.test.ts 11 tests(negative 5: 429 복원실패 재시도·409 충돌·404 폴백·마지막 패널 제거 등); fakePort mockRejectedValue로 실패 주입 | 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1616 | CH-13a 멀티 심볼 비교 코어(chart-engine compare/{align,normalize,spread}.ts) — 정규화 가격·스프레드 순수 계산 | 62ffb08 | D2 | D1 | **NO** | compare.test.ts 17 tests, negative 9(빈 시리즈·미겹침·anchor 미존재/0값·미정렬·quote scale 불일치 등) 전부 순수 함수 도메인 검증 | 실패 주입(네트워크/DB 모킹) 없음—전부 순수 계산 예외, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1617 | CH-13b 멀티 심볼 비교 화면 배선(ChartPage 비교 심볼 추가·오버레이·스프레드 페인) + 페인 레이아웃 저장 | 0fa2c91 | D2 | D1 | **NO** | CompareSymbols.test.tsx 10 tests, negative 7(404·403 mockRejectedValue ApiError 주입, 교집합 0봉, 중복/자기자신 거부, computeComparison empty 2건) | 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1710 | CH-14 chart-engine panes/{paneModel,paneLayout,crosshairSync}.ts (페인 CRUD·높이비율·크로스헤어 동기화) | 64b6fd9 | D2 | D1 | **NO** | paneModel.test.ts 11 + paneLayout.test.ts 6 + crosshairSync.test.ts 7 = 24 tests, negative≥10(미존재 id·합≠1·마지막 main pane 제거 거부·중복 id·범위 밖 ratio·NaN/Infinity) | 실패 주입 없음(순수 모델, fetch/DB 의존 없음), 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1711 | CH-16 chart-engine legend/{statusLine,dataWindow,objectTree}.ts (크로스헤어 값 표시·데이터 윈도우·지표 트리) | 76584ad | D2 | D1 | **NO** | statusLine.test.ts 7 + dataWindow.test.ts 9 + objectTree.test.ts 9 = 25 tests, negative≥10(빈 데이터·범위 밖 dataIndex·중복 id·미존재 id 조작·coverage mismatch) | 실패 주입 없음, 수치 성능 단언 없음(30개 지표 동시투영 DoD 테스트는 개수 검증일 뿐 지연시간 단언 아님), 게이트 적색 재현 없음, D3 없음 |
| 1731 | CH-11 chart-engine plugins/indicatorPlugin.ts — 지표 플러그인 API(오버레이/페인/스타일 스키마, 3층 레지스트리 소비) | e982ea3 | D2 | D1 | **NO** | indicatorPlugin.test.ts 16 tests(negative 다수: placement 충돌·중복 instanceId·미지 output·lineWidth≤0·malformed style) + indicators.test.ts 4 tests(negative 1); loadIndicatorCatalog 1건 Error("network down") 모킹→routeApiError 분류 확인(실패 주입 1건) | 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1732 | CH-15 chart-engine render/{plotRenderers,scaleBinding,fillBetween}.ts — PlotSpec 6종 렌더러·스케일 바인딩 | 6c714c2 | D2 | D1 | **NO** | plotRenderers.test.ts 11 + fillBetween.test.ts 5 + scaleBinding.test.ts, negative 5종(미지 kind/scale·미지 필드·시리즈 부재·fill 대상 부재·길이/시각 불일치); CONTRACT_FIELD_SPECS에 PlotSpec 등록해 기존 contractDrift.test.ts 확장 | 실패 주입 없음(순수 함수), 수치 성능 단언 없음, 이 리프 자체의 게이트 적색 재현 테스트 없음, D3 없음 |
| 1808 | CH-12 chart-engine plugins/scriptPreview.ts + 편집기 연동 — 스크립트 지표/전략 즉시 미리보기(컴파일→계산→오버레이) | 1ce7831 | D2 | D1 | **NO** | scriptPreview.test.ts 7 + ScriptEditorPage.test.tsx 신규 3 tests, 상태전이(등록·해제·교체·축소) 위주 happy-path | negative-path(throw/reject) 사실상 0건(D2 기준 3건에 크게 미달), 실패 주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1809 | CH-17a 지표 템플릿 순수 모델(chart-engine templates/{templateModel,applyTemplate}.ts) — 서버·레지스트리 무관 | ff52230 | D2 | D1 | **NO** | templateModel.test.ts 9 + applyTemplate.test.ts 5 = 14 tests, negative 다수(no_active_panel·inventory_missing_indicator·schema_version 불일치/누락·중복 pane id·미지 필드·NaN param·unknown_indicator·no main pane) | 실패 주입 없음(순수 함수), 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1904 | CH-17b charting 컨텍스트 확장: 지표 템플릿(세트·설정·페인 배치) 저장·조회·삭제 API + 마이그레이션 | a639962 | D2 | D1 | **NO** | test_indicator_template_lifecycle.py 7 tests, negative=5(중복명→409 ConcurrencyConflict·미존재 get→404·교차테넌트 get/delete→404 동형 2건·삭제 후 재조회→404) | 실 Postgres 통합테스트뿐 mocked 실패주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1905 | CH-17c 지표 템플릿 화면 배선: 현재 차트 → 템플릿 저장, 템플릿 선택 → 적용(동일 화면 재현) | ca36e59 | D2 | D1 | **NO** | ChartTemplates.test.tsx 6 tests, negative=2(빈 이름 저장 차단·knownIndicatorIds 누락 시 fail-closed 거부+배너) | negative<3, mocked 실패주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음 |
| 1914 | CH-14·CH-16 화면 배선: ChartPage 멀티페인 + statusLine/dataWindow/objectTree 패널 | b19b7a4 | D2 | D1 | **NO** | ChartLegend.test.tsx 2 + ChartPanes.test.tsx 3 = 5 tests, negative=2(오브젝트 트리 빈 목록 안내·heightRatio 합≠1 저장 레이아웃 fail-closed 거부+배너); heightRatio 합=1 불변식·크로스헤어 동기화 DOM 실증 | negative<3, failure-injection 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음 |
| 1953 | CH-18a 클라이언트 지표 계산 1/2: chart-engine compute/{workerPool,clientEngine}.ts (참조 벡터 통과 지표 화이트리스트만) | ee2ca19 | D2 | D1 | **NO** | clientEngine/verifiedIndicators/workerPool 3파일 27 tests, negative≥12(화이트리스트 미등재·hash 드리프트·필수입력 누락·비정상 period·resolveVerified류 fail-closed 5건 등); workerPool "propagates a handler's thrown error"/"wraps a backend failure"=워커 강제 throw 준-실패주입 2건 | mocked network/DB 실패주입 아님(워커 콜백 throw만), 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 1954 | CH-18b 클라이언트 지표 계산 2/2: compute/parityCheck.ts + 불일치 시 서버 폴백(무음 금지) | f044251 | D2 | D1 | **NO** | parityCheck.test.ts 6 + ChartPage.test.tsx 신규 3 = 9 tests, negative=4(1e-6 편차 주입 시 mismatch 검출·서버 폴백 전환·onMismatch 호출·서버참조 없으면 unverified fail-closed) | 실패주입은 수치 데이터 편차뿐(mocked network/exception 아님), 수치 성능 단언 없음, 게이트 적색 재현 없음 |
| 1958 | CH-19a 렌더 성능 1/2: LOD 다운샘플링(극값 보존) + 뷰포트 컬링 순수 모듈 | cd08ca3 | D2 | D1 | **NO** | lod.test.ts 7 + viewport.test.ts 7 = 14 tests, negative=6(폭 0/음수·빈 배열·시간역순 각 3건); 10만봉 다운샘플에서 버킷 내부 spike(첫/끝 아닌 위치)까지 극값 보존 실증 | failure-injection 없음, "렌더 성능" 리프인데 ms/fps 수치 성능 단언 자체가 없음(순수 계산 정확성만), 게이트 적색 재현 없음 |
| 1959 | CH-19b 렌더 성능 2/2: 레이어 분리·오프스크린 캔버스 배선 + 밀도 벤치 CI 래칫 | 4226858 | D2 | D1 | **NO** | layers.test.ts 4 + layers.noVendorReimpl.test.ts 4 = 8 tests, negative=2(LAYER_PANE_NOT_FOUND 2건); density_bench.mjs+densityRatchet.mjs가 baseline 대비 20% 초과 회귀 시 exit 1하는 CI 게이트를 local_ci.py에 배선 | 상대 래칫뿐 고정 ms/fps 절대 임계값 없어 수치 성능 단언 불인정; checkRatchet()에 회귀 수치를 넣어 실패를 확인하는 단위테스트 없어 게이트 적색 재현도 파일 내 실증 없음; negative<3, failure-injection 없음 |
| 2001 | CH-18c 클라이언트 지표 계산 실배선: ChartPage가 clientEngine 워커 경로를 실제로 호출 + 미검증 지표 서버 폴백(무음 금지) | cc5ec7c5 | D2 | D1 | **NO** | IndicatorParityPanel.test.tsx 3 tests, negative=2(BBANDS 영구 미검증 지표 서버 폴백 표면화·서버참조 없으면 unverified fail-closed); SMA 회귀 방지 1건 | negative<3, failure-injection 없음, 수치 성능 단언 없음; 커밋 메시지의 "되돌리면 negative 2건 FAIL" 주장은 파일 내 자동 revert-and-check 없어 게이트 적색 재현 불인정 |
| 2002 | CH-15b PlotSpec 렌더러 실배선: 차트 화면이 plotRenderers/scaleBinding/fillBetween을 경유(지표 추가 시 화면 코드 무변경) | af7da9a7 | D2 | D1 | **NO** | ChartPlotLayer.test.tsx 4 + ChartPanes.test.tsx 신규 2 + plotRenderers.test.ts 신규 5 = 11 tests, negative=3(unknown PlotSpec.kind 명시적 거부 2건 + deriveOverlayPlotSpec이 decodePlotSpec 항상 통과 확인) | failure-injection 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 2012 | CH-4b 그리기 서버 영속화 실배선: ChartPage 도형이 GET/PUT /v1/foundation/charting/layouts/{id}/drawings를 경유 | a4269edc | D2 | D1 | **NO** | ChartPage.test.tsx 총 16 tests(신규 3), negative≥7(429 재시도·409 충돌 2건·404·drawings 409·drawings 404 등); putDrawings/getDrawings를 mockRejectedValue(409/404)로 강제 실패시켜 로컬 도형 보존·배너 표면화 검증=진짜 mocked 실패주입 존재(4건) | 수치 성능 단언 없음; "onSave 체이닝 되돌리면 3건 중 2건 FAIL" 주장은 파일 내 실제 revert로 실증되지 않아 게이트 적색 재현 불인정, D3 없음 |
| 2013 | CH-16b 오브젝트 트리 순서·잠금 서버 저장 실배선(moveEntry/setEntryLocked/encodeObjectTreeState 소비) | 242f346 | D2 | D1 | **NO** | ChartLegend.test.tsx(신규4, 총6)+useChartLayout.test.ts(신규2)+layoutModel.test.ts(신규2)+objectTree.test.ts(신규4)=12 tests, negative≥8(빈 목록·첫/끝 항목 순서버튼 비활성·오버레이 항목 잠금버튼 미노출·layoutModel 비배열/비문자 필드 거부 2건·sortByPersistedOrder 3건) | failure-injection(mocked network/DB) 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음, D3 없음 |
| 2028 | CH-15 결함 수정(리뷰 task-2025 REJECT): color_rule·fill direction 소비 경로 신설(파싱만 되고 그려지지 않는다) | 053b3d7 | D2 | D1 | **NO** | plotRenderers.test.ts 신규 7건: decodePlotSpec 3건(color_rule=null 허용/"sign" 허용/미지원값"rainbow" 거부), renderPlot color_rule=sign histogram 2건(spy 구조 비교), fill_between direction 2건(above/below 색 다름, equal 미그림) | negative 1건뿐(>=3 미달), mock 실패주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 테스트 없음, D3 없음 |
| 2038 | CH-18d verifiedIndicators 실배선: 미검증 지표의 클라이언트 계산 차단(현재 화이트리스트가 프로덕션 경로에 없다) | b82607b7 | D2 | D1 | **NO** | IndicatorParityPanel.test.tsx: computeIndicatorSeries를 spy화(실구현 유지), 신규 3건(미검증 BBANDS→0회 호출, entry_hash drift 카탈로그→0회 호출, 화이트리스트 통과 1회 호출) | negative 2건뿐(>=3 미달), 진짜 실패주입 없음(spy는 실구현 그대로), 수치 성능 단언 없음, "가드 되돌리면 실패" 주장은 자동 재현 아님 |
| 2039 | CH-18e workerPool 실배선: 클라이언트 지표 계산을 Web Worker로(현재 메인 스레드 동기 계산) | 17ef81ee | D2 | D1 | **NO** | workerPool.test.ts 신규 5건(FakeWorker double, concurrency cap 30건→max 4 동시실행, ok:false reject=실패주입, onerror reject=워커 크래시 실패주입, dispose 후 WORKER_POOL_DISPOSED); IndicatorParityPanel.test.tsx 신규 4건(pool.submit 강제 reject→서버 폴백=실패주입 등). negative-path 4건 이상, 실패주입 명확 | 수치 성능(ms/fps) 단언 없음(동시성 상한 카운트는 성능 단언 아님), 게이트 적색 재현 테스트 없음, D3 없음 |
| 2043 | CH-19c 렌더 성능 실배선: 캔들 렌더 경로가 render/lod·render/viewport를 실제로 소비한다 | 117e023c | D2 | D1 | **NO** | useVisibleCandles.test.ts 신규 4건(10만봉→`expect(result.length).toBeLessThanOrEqual(2000)`+구간고저보존=실제 수치 상한 단언, 뷰포트 밖 컬링, 역전 뷰포트 거부 2건); ChartPanes.test.tsx 1건(`toBeLessThan(5000)`) — 수치 성능 단언으로 인정 | negative 2건뿐(>=3 미달, 동일 ViewportError 축), mock 실패주입 없음, 게이트 적색 재현 없음(커밋 메시지 주장뿐), D3 없음 |
| 2044 | CH-16d 데이터 윈도우 실배선: legend/dataWindow 타입 경계 분리 후 지표 30종 값 동시 표시 패널 | 86931a9c | D2 | D1 | **NO** | DataWindowPanel.test.tsx 신규 3건(1행 렌더, 지표 30종 동시 30행 DOM, negative 중복 indicatorId→에러 표면화); ChartPanes.test.tsx 1건(30개 지표 배선 통합) | negative 1건뿐, 실패주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음(커밋 메시지 주장뿐) |
| 2045 | CH-16e 상태줄 실배선: legend/statusLine을 벤더 tooltip legend 콜백으로 크로스헤어 OHLCV 표시 | 4db8102f | D2 | D1 | **NO** | ChartPanes.test.tsx 신규 2건(3개 서로 다른 OHLCV 바 중 가운데 바 크로스헤어→O/H/L/C/Vol 정밀도까지 일치, negative 캔들 없을 때 7필드 전부 '--' fail-closed) | negative 1건뿐, 실패주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음(커밋 메시지 주장뿐) |
| 2052 | CH-19d 밀도 벤치 CI 배선: density_bench.mjs가 한 번도 실행된 적 없다 — npm script + local_ci frontend step | 5adbc86b | D2 | D0 | **NO** | 테스트 파일 변경 0건 — package.json에 bench:density 스크립트 등록, density-baseline.json 수치 갱신뿐. local_ci.py 등록은 저장소 밖이라 커밋에 미포함 | 자동 재현 가능한 vitest 테스트 전무. "배선증명"은 baseline 손 변조 후 exit 1 확인·원복이라는 커밋 메시지 서술뿐, 자동화된 before/after 테스트 아님 |
| 2053 | CH-14d 배럴 값 재수출 경계 분리: core/klinecharts*가 vendor를 값으로 끌고 오는 탓에 apps/web이 타입조차 못 쓴다 | (커밋 없음, commit="") | D2 | D0 | **NO** | 코드/테스트 커밋 자체가 없음(status=done, commit=""). tests 필드: "no code change; verified via apps/web npx tsc -b --force probes (2 runs)" — note에 재현 근거(core/klinecharts·klinechartsSeries deep-import 시 vendor발 TS7053 등 79개 오류 재현, core/renderer·series는 vendor-free 확인) | 증빙할 커밋 코드/테스트가 전무 — 2331/2658류의 "중복 취소"가 아니라 실제 조사(tsc -b 프로브 2회)를 수행하고 "코드 변경 불필요"에 정당히 도달한 investigation-only 종료 |
| 2097 | CH-19e 밀도 벤치: 명세 절대 임계 단언 추가 + 다중런 중앙값으로 노이즈 오탐 제거 | aceeeca2 | D2 | D0 | **NO** | densityRatchet.mjs 신설 checkAbsoluteThresholds/measureCalibMs: CH-19 명세 절대 임계 3종(panZoomFrameMsP95<=16.7ms, indicatorAddMs<=100ms, tickUpdateMsP95<=8ms)을 host-load 정규화해 적용, density_bench.mjs 3(min)→5(median) 변경 후 상대 래칫+절대 임계 실패 시 exit 1 | 이 커밋에 테스트 파일 0건(densityRatchet.test.mjs는 이후 task-2479/a5b0d938에서 신설, 본 리프 범위 밖) — 로직을 검증하는 단위테스트 없음, negative-path/실패주입/게이트 적색 자동 재현 전무 |
| 2101 | CH-4c drawings/serialize 실배선: 도형 저장·복원이 chart-engine 직렬화기를 경유한다(자체 인코딩 제거) | 2ef3c544 | D2 | D1 | **NO** | useChartDrawings.test.ts 신규 2건(복원 직후 무변경 persist→putDrawings 미호출, 도형 1개 그려 저장 후 재persist 시 putDrawings 1회만=중복 PUT 생략 확인) | negative-path 0건(신규 범위 내), 실패주입 없음, 수치 성능 단언 없음, 게이트 적색 재현 없음 |
| 2127 | CH-15c render/fillBetween 실배선: 미배선 래칫 잔여 1건 해소(밴드 렌더가 프로덕션 경로에 없다) | f345136b | D2 | D1 | **NO** | ChartPlotLayer.test.tsx 신규 1건(negative: length mismatch upperband/lowerband→FILL_BETWEEN_LENGTH_MISMATCH 거부, `querySelectorAll("polygon")` 0개로 실렌더 미발생 확인) | negative 1건뿐(>=3 미달), 실패주입 없음(도메인 검증), 수치 성능 단언 없음, 게이트 적색 재현 없음(커밋 메시지의 "catch 제거 시 uncaught 즉시 실패" 주장은 자동 재현 아님) |

## 발행된 DEEPEN task

D2 미달 44건 전부에 대해 `orchestrator.create_task`로 `DEEPEN <원 task id> <부족한 증빙>` implement task를 발행했다(우선순위 2, role은 원 task와 동일, tasks/ 직접 쓰기 없음). 발행된 id 목록(원 task id -> DEEPEN task id):

| 원 task id | DEEPEN task id |
|---|---|
| 1133 | 3068 |
| 1375 | 3069 |
| 1401 | 3070 |
| 1402 | 3071 |
| 1501 | 3072 |
| 1509 | 3073 |
| 1523 | 3074 |
| 1539 | 3075 |
| 1557 | 3076 |
| 1559 | 3077 |
| 1569 | 3078 |
| 1570 | 3079 |
| 1593 | 3080 |
| 1594 | 3081 |
| 1616 | 3082 |
| 1617 | 3083 |
| 1710 | 3084 |
| 1711 | 3085 |
| 1731 | 3086 |
| 1732 | 3087 |
| 1808 | 3088 |
| 1809 | 3089 |
| 1904 | 3090 |
| 1905 | 3091 |
| 1914 | 3092 |
| 1953 | 3093 |
| 1954 | 3094 |
| 1958 | 3095 |
| 1959 | 3096 |
| 2001 | 3097 |
| 2002 | 3098 |
| 2012 | 3099 |
| 2013 | 3100 |
| 2028 | 3101 |
| 2038 | 3102 |
| 2039 | 3103 |
| 2043 | 3104 |
| 2044 | 3105 |
| 2045 | 3106 |
| 2052 | 3107 |
| 2053 | 3108 |
| 2097 | 3109 |
| 2101 | 3110 |
| 2127 | 3111 |
