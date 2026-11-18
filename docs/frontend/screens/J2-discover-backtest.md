# J2 화면 명세 — 스크리너·차트·스크립트·백테스트

> **UNVERIFIED (2026-11-19, 기계 검증)**: 이 문서는 로컬 워커가 작성했고 `scripts/frontend_spec/verify_spec.py` 검증에서 인용 42건·식별자 49건 중 **근거 불일치 1건**이 나왔다(코드에 없는 필드·틀린 줄 번호 포함). **사실로 사용하지 말 것.** 검증기가 0건이 되도록 다시 쓰면 이 안내를 지운다.

**여정**: J2 스크리너→차트·지표→스크립트→백테스트→결과 해석
**참조**: UX_JOURNEYS.md §6.1, ADR-2026-11-18-B §4.2 J2 Phase 1

---

## 1. 스크리너 (`/screener`)

### 1.1 목적

사용자가 유니버스(예: `KRX`, `CRYPTO`)와 필터 조건을 조합해 시세 지표 기반 종목 스캔을 실행하고, 결과 행을 클릭하면 `/chart`로 이동한다.

### 1.2 화면 구조

| 영역 | 컴포넌트 | 파일:줄 |
|---|---|---|
| 헤더 | `PageHeader` | `ScreenerPage.tsx:184` |
| 필터 빌더 | `Card` 내 유니버스·필터 행·정렬 | `ScreenerPage.tsx:186-307` |
| 결과 | `Card` 내 결과 행 목록 | `ScreenerPage.tsx:309-345` |

### 1.3 필터 빌더

- **유니버스 입력**: `Input` (data-testid=`screener-universe`). 필수. 비우면 `universe_required` 검증 오류.
- **필터 행**: 동적 추가/제거. 각 행에 `kind`(indicator/fundamental/research/backtest_stat), `field`(문자열), `operator`(gt/gte/lt/lte/eq/neq), `value`(문자열). research kind일 때 `asOf` 필드 추가.
- **정렬**: `sortField`(문자열) + `sortDirection`(asc/desc). 선택 사항.
- **검증**: `describeScreenDefinitionIssues()`가 실행 시점에 `universe_required`, `filters_required`, `filter_incomplete`, `filter_research_as_of_required` 체크. 오류는 `Alert(tone="danger")`에 나열.

### 1.4 실행 및 결과

- **실행 버튼**: `data-testid="screener-run"`. 클릭 시 `buildDefinition()`으로 `ScreenDefinitionInput` 생성 → 검증 → `submitted` 상태 설정 → `useQuery`가 `enabled: submitted !== null`로 자동 실행.
- **API**: `POST /v1/foundation/screener/run` (ScreenerClient.runScreen).
- **상태 전이**:
  - `submitted === null`: `EmptyState("아직 실행하지 않았습니다.")`
  - `isLoading`: `LoadingState`
  - `isError`: `ScreenerErrorBanner` — `routeApiError()`로 오류 종류 판별(`refetch_retry`/`backoff_retry`는 재시도 가능). 재시도 버튼 포함.
  - `rows.length === 0`: `EmptyState("결과가 없습니다.")`
  - `rows.length > 0`: 총 개수 표시 + `truncated`일 경우 `Alert(tone="warning")` + 행 목록

### 1.5 결과 행

각 행: `symbol`(이름) + `venue`(배지) + `/chart?instrument_id=...` 링크. 링크 클릭 시 차트 페이지로 이동.

### 1.6 시세 데이터 부재·지연

- `submitted`가 설정된 후 `query.isLoading` → `LoadingState` 표시.
- `query.isError` → `ScreenerErrorBanner` 표시. 서버 오류 코드(trace_id 포함)를 `getApiErrorMessage()`로 변환.
- `refetch_retry`/`backoff_retry` 종류: 재시도 버튼 활성화.

---

## 2. 차트 + 지표 (`/chart`)

### 2.1 목적

캔들스틱 차트 표시, 지표 오버레이 추가/제거, 그리기 도구, 비교 심볼, 재생 모드, 그리고 스크립트 백테스트 실행.

### 2.2 화면 구조

| 영역 | 컴포넌트 | 파일:줄 |
|---|---|---|
| 헤더 | `PageHeader` | `ChartPage.tsx:175` |
| 심볼/커버리지 | `CoverageBadge` | `ChartPage.tsx:177-182` |
| 레이아웃/그리기 오류 | `ChartLayoutErrorBanners`, `ChartDrawingsErrorBanners` | `ChartPage.tsx:184-185` |
| 툴바 | `ChartToolbar` | `ChartPage.tsx:187-201` |
| 지표 피커 | `IndicatorPicker` | `ChartPage.tsx:203-209` |
| 차트 캔들 | `ChartPanes` → `CandlestickChart` | `ChartPage.tsx:211-247` |
| 지표 평행성 | `IndicatorParityPanel` | `ChartPage.tsx:250-255` |
| 전략 마커 | `StrategyMarkers` | `ChartPage.tsx:257` |
| 백테스트 패널 | `BacktestPanel` | `ChartPage.tsx:259-267` |
| 비교 심볼 | `CompareSymbols` | `ChartPage.tsx:269-283` |
| 그리기 목록 | `DrawingsList` | `ChartPage.tsx:285` |

### 2.3 데이터 조회

- **캔들**: `useQuery(["chart-candles", ...])`. `enabled: instrumentId !== null && instrumentId.trim().length > 0`. `VISIBLE_CANDLE_COUNT`(200) × `TIMEFRAME_MS[timeframe]` 범위의 `start`/`end` 계산.
- **커버리지**: `useQuery(["chart-coverage", ...])`. 같은 enabled 조건. 서버가 no-coverage를 `200+[]`로 접으므로 독립적으로 항상 시도.
- **데이터 부재**: `instrumentId` 없으면 `NoInstrumentSelected` 표시. 캔들 조회 실패 시 `routeApiError()` → `ErrorMessage`.
- **재생 모드**: `useChartReplaySession`이 `queryData`를 받아 재생 중에는 `displayCandles`로 대체.

### 2.4 지표 시스템

- `useIndicatorSelection()` 훅이 지표 ID 선택, 오버레이 엔트리 매핑, 높이 비율, 리마운트 키 관리.
- `IndicatorPicker`: 지표 선택 → `toggleIndicator()`로 토글.
- `IndicatorParityPanel`: 선택된 지표의 서버 참조 지표 시리즈 비교. `catalog`와 `resolveServerSeries` 포트 주입.
- 레이아웃 저장/복원: `useChartLayout`이 `VENUE:instrumentId` 키로 서버에 저장. timeframe/지표/비교 심볼/그리기/개체 순서/잠긴 지표 포함.

### 2.5 그리기 도구

- `useChartDrawings` 훅: `layoutId`가 확보된 후 활성화. 도구 선택(`drawingTool`), 추가(`addDrawingAt`), 제거(`handleRemoveDrawing`), 지속(`persist`).
- `addDrawingDisabled`: `drawingTool === null || displayCandles.length === 0`.

### 2.6 비교 심볼

- `CompareSymbols`: 기준 시리즈(`baseCandles`)에 정렬/정규화/스프레드 적용 후 비교. `listInstruments`로 심볼 검색.
- `encodeCompareSymbol`/`decodeCompareSymbol`로 `VENUE:instrumentId` 문자열 직렬화.

### 2.7 백테스트 패널 (`BacktestPanel`)

- **입력**: `venue`, `instrumentId`, `timeframe`, `start`, `end`, `points`(캔들 데이터).
- **실행**: `runQuickBacktest` 포트(기본값: `backtestsClient.runQuickBacktest`). `POST /v1/backtests/quick`.
- **상태**:
  - `isPending`: 버튼 비활성화 + "백테스트 중..."
  - `isError`: `getApiErrorMessage()`로 오류 표시. `data-testid="compile-backtest-error"`.
  - `isSuccess`: `BacktestSummary` — 최종 자산, 주문 수, 바 수.
- **데이터 부재**: `points`가 없으면 차트 위에 "차트 데이터를 먼저 로드하세요" 표시.

### 2.8 대용량 결과 렌더 성능

- `VISIBLE_CANDLE_COUNT = 200`으로 표시 캔들 수 고정.
- `useMemo`로 `layoutView` 계산. 캔들 데이터는 `displayCandles`로 재생 상태 분리.
- 차트 엔진(klinecharts 포크)이 자체 가상화/샘플링 적용.

---

## 3. 스크립트 에디터 (`/scripts`)

### 3.1 목적

aios-script DSL로 커스텀 지표를 작성·컴파일·미리보기하고, 즉시 백테스트 실행.

### 3.2 화면 구조

| 영역 | 컴포넌트 | 파일:줄 |
|---|---|---|
| 헤더 | `PageHeader` | `ScriptEditorPage.tsx:240` |
| 에디터 | `ScriptEditor` | `ScriptEditorPage.tsx:242` |
| 컴파일 버튼 | `Button` | `ScriptEditorPage.tsx:244` |
| 컴파일 오류 | `ErrorMessage` | `ScriptEditorPage.tsx:248-254` |
| 컴파일 성공 미리보기 | `CompilePreview` | `ScriptEditorPage.tsx:256-263` |
| 미리보기 패널 | `ScriptPreviewPanes` | `ScriptEditorPage.tsx:264` |

### 3.3 스크립트 에디터

- **입력**: `ScriptEditor` 컴포넌트 — 코드 편집기. `markers`로 컴파일 오류 위치 표시(줄/열/메시지).
- **비활성화**: `disabled={mutation.isPending}` — 컴파일 중 편집 불가.
- **기본 소스**: `DEFAULT_SOURCE`(모멘텀 전략 예제).

### 3.4 컴파일

- **API**: `POST /v1/scripts/compile` (기본값: `apiClient.compileScript`).
- **`useMutation`**: `mutationFn: compileScript`. `mutation.data`가 성공 시에만 바뀜.
- **성공 시**: `CompileScriptView` 응답 — `scriptHash`, `grammarVersion`, `irVersion`, `registryVersion`, `irSha256`, `instrCount`, `resources`(seriesCount/lookbackTotal/opCount/callCount/callDepth/plotCount), `elapsedMs`.
- **컴파일 오류**: `isScriptCompileErrorDetails()` 판정 — `line`, `col`, `code`. 첫 오류에서 멈춤(DSL-12).
- **네트워크 오류**: `showGenericError` → `ErrorMessage` 표시.

### 3.5 컴파일 성공 후 연결 (갭 G-3)

- **`CompilePreview` 컴포넌트**: 해시 표시 + 리소스 통계 + 두 버튼.
  - **"차트에서 백테스트"**: `handleOpenChart()` → `/chart?script_hash=${hash}`.
  - **"즉시 백테스트"**: `backtest.mutate(source)` — 소스 그대로 재전송.
- **갭 G-3**: 컴파일 성공 직후 사용자가 차트로 이동하면 스크립트 해시를 컨텍스트로 전달. `/chart`는 `script_hash` 파라미터를 받으나 현재 아무것도 조회하지 않음(스크립트 컴파일 응답에 instrument 개념 없음).

### 3.6 즉시 백테스트

- **기본 실행**: `defaultRunBacktest` — 7일 기간, `DEFAULT_VENUE`(BITGET), `DEFAULT_TIMEFRAME`(1h), 초기 현금 10000.
- **API**: `POST /v1/backtests/quick` — `scriptSource`(원본 소스 재전송) 사용.
- **결과**: `BacktestSummary` — 최종 자산, 주문 수, 바 수.
- **실패**: `data-testid="compile-backtest-error"` 영역에 오류 메시지.

### 3.7 컴파일 전 상태

- 컴파일 전: "컴파일" 버튼만 활성화. 에디터에 `DEFAULT_SOURCE` 표시.
- 컴파일 중: 버튼 비활성화 + "컴파일 중...".
- 컴파일 실패: `ErrorMessage` 또는 `compileErrorDetails` 표시. 에디터 마커로 오류 위치 강조.
- 컴파일 성공: `CompilePreview`(초록색 Alert) + `ScriptPreviewPanes`.

---

## 4. 백테스트 스윕 결과 (`/backtest/sweep-results`)

### 4.1 목적

파라미터 스윕 백테스트 결과를 히트맵·안정성·재현성 테이블로 시각화.

### 4.2 화면 구조

| 영역 | 컴포넌트 | 파일:줄 |
|---|---|---|
| 헤더 | `PageHeader` | `SweepResultsPage.tsx:234` |
| 요청 없음 | `EmptyState` | `SweepResultsPage.tsx:236-238` |
| 로딩 | `LoadingState` | `SweepResultsPage.tsx:240` |
| 오류 | `SweepErrorBanner` | `SweepResultsPage.tsx:241-243` |
| 경고 | `Alert(tone="warning")` ×2 | `SweepResultsPage.tsx:247-248` |
| 모의 운용 버튼 | `Card` 내 Button | `SweepResultsPage.tsx:250-266` |
| 히트맵 | `SweepHeatmap` | `SweepResultsPage.tsx:267` |
| 안정성 | `SweepStabilityCard` | `SweepResultsPage.tsx:268` |
| 재현성 | `SweepReproducibilityTable` | `SweepResultsPage.tsx:269` |
| 경고 목록 | `Alert(tone="warning")` | `SweepResultsPage.tsx:270-278` |

### 4.3 데이터 흐름

- **입력**: `location.state.sweepRequest` (`SweepResultsLocationState`). `/chart`의 `BacktestPanel` CTA 클릭 시 `navigate("/backtest/sweep-results", { state: { sweepRequest } })`.
- **API**: `POST /v1/backtests/sweep` (기본값: `defaultRunSweep`). `staleTime: Infinity` — 한 번 로드하면 재조회 안 함.
- **요청 없음**: `request === null` → `EmptyState("스윕 요청이 없습니다.")`.

### 4.4 결과 표시

- **경고 고지**: "미래 수익을 보장하지 않습니다." + "실거래 체결·슬리피지와 다를 수 있습니다."
- **히트맵**: `SweepHeatmap` — 두 축(예: length=[7,14])의 조합을 열/행으로, 메트릭값을 색상으로 표현. `heatColor(t)` 함수로 0~1 범위를 색상 매핑. 값 없으면 "—" + 회색 배경.
- **안정성**: `SweepStabilityCard` — bestAxisValues, neighborMean, neighborStd, isolated(과최적화 의심).
- **재현성**: `SweepReproducibilityTable` — 각 콤보의 `comboKey`(seed 포함) + `reproducibilityKey`.
- **경고 목록**: `result.warnings` 배열 각 항목을 불릿 포인트로 나열.

### 4.5 모의 운용 배포

- `bestReproducibilityKey()`: `stability.bestAxisValues`가 가리키는 콤보의 재현 키, 또는 첫 콤보.
- 버튼 클릭 시 `/system/paper-deployments`로 이동 + `state.packageRef`에 재현 키 전달.

### 4.6 대용량 결과 렌더 성능

- 히트맵: `table` 요소로 렌더. 각 셀이 `findPoint`로 O(N) 탐색(콤보 수에 비례).
- 재현성 테이블: `points` 배열 직접 매핑.
- `staleTime: Infinity`로 캐시 — 동일 페이지 재방문 시 네트워크 없이 렌더.

---

## 5. 갭/결함 표

| 갭 ID | 화면 | 심각도 | 설명 | 근거 | 재현 |
|---|---|---|---|---|---|
| G-1 | 차트 | 중 | `instrumentId` 없을 때 `NoInstrumentSelected` 표시 — 차트 페이지 진입 시instrument 선택 전 빈 화면. | `ChartPage.tsx:157-159` | `/chart` 접근 시 빈 화면 표시 |
| G-2 | 차트 | 중 | `CoverageBadge`가 `coverageQuery.data`를 받으나 서버가 no-coverage를 `200+[]`로 접어 빈 배열일 때 "데이터 없음" 구체적 표시 없음. | `ChartPage.tsx:181`, `CoverageBadge.tsx` | instrument_id=BTCUSDT & coverage 없음 |
| G-3 | 스크립트 | 경 | 컴파일 성공 후 "차트에서 백테스트" 버튼이 `/chart?script_hash=...`로 이동하지만, 현재 `/chart`는 `script_hash` 파라미터를 받아도 아무 동작 안 함. | `ScriptEditorPage.tsx:197-199`, `ChartPage.tsx:68` | 컴파일 성공 → "차트에서 백테스트" 클릭 → 차트에서 스크립트 정보 미표시 |
| G-4 | 스크립트 | 경 | `CompilePreview` 내 "즉시 백테스트" 버튼이 `backtest.isPending` 동안만 비활성화 — 컴파일 중 이미 `disabled={mutation.isPending}`으로 에디터 전체가 잠기므로 중복 잠금. | `ScriptEditorPage.tsx:244`, `CompilePreview.tsx:137` | 컴파일 클릭 → 에디터+버튼 동시 비활성화 |
| G-5 | 스크리너 | 경 | `query.data?.total ?? rows.length` — `query.data`가 undefined일 때 `rows.length`로 폴백하지만 `rows`는 이미 `query.data?.rows ?? []`로 비어있음. 총 개수 0 표시. | `ScreenerPage.tsx:179`, `ScreenerPage.tsx:323` | 결과 0件の 경우 total=0 표시 |
| G-6 | 백테스트 | 중 | `SweepResultsPage`가 `location.state`에서 `sweepRequest`를 읽는데, 브라우저 뒤로가기 시 `location.state`가 초기화되어 페이지가 "스윕 요청이 없습니다."로 표시됨. | `SweepResultsPage.tsx:221` | 백테스트 스윕 결과 페이지 → 뒤로가기 → 다시 진입 |
| G-7 | 차트 | 경 | `CompareSymbols`가 `baseCandles`를 받으나 재생 모드(`replayState`) 중에는 `displayCandles`가 대체되나 비교 심볼은 원본 캔들 기준. 재생 중 비교 심볼이 원본 데이터로 고정됨. | `ChartPage.tsx:150`, `ChartPage.tsx:166`, `ChartPage.tsx:269-272` | 차트 재생 중 비교 심볼 추가 |

---

## 6. 실스택 관찰 기록

### 6.1 스크리너

- **관찰**: `POST /v1/foundation/screener/run` — 요청 바디에 `universe`, `filters[]`, `sort`, `columns`. 응답에 `rows[]`, `total`, `truncated`.
- **에러 처리**: `ScreenerErrorBanner`가 `routeApiError()` 결과를 판별해 `refetch_retry`/`backoff_retry` 구분. trace_id를 `supportCode`로 표시.
- **파일**: `ScreenerPage.tsx:314-316`

### 6.2 차트

- **관찰**: 캔들 조회는 `VISIBLE_CANDLE_COUNT`(200) × timeframe 길이. `start`/`end`는 `anchor`(현재 시각) 기준 역산.
- **커버리지**: `GET /v1/foundation/market-data/coverage` — `venue`+`instrumentId`+`timeframe`으로 데이터 범위 확인.
- **레이아웃 저장**: `useChartLayout`이 `layoutId` 확보 후 `PUT /v1/foundation/charting/layouts/{id}`로 서버에 저장.
- **파일**: `ChartPage.tsx:135-148`, `useChartLayout.tsx`

### 6.3 스크립트

- **관찰**: 컴파일 응답에 `scriptHash`(SHA-256 해시) + `resources` 통계(`seriesCount`, `opCount`, `plotCount` 등).
- **즉시 백테스트**: 컴파일 응답이 아닌 원본 소스를 재전송 (`scriptSource`). 서버가 매번 재컴파일.
- **파일**: `ScriptEditorPage.tsx:157-268`

### 6.4 백테스트 스윕

- **관찰**: `POST /v1/backtests/sweep` — `axes`(파라미터 조합), `metric`(최적화 지표), `config`(시뮬레이션 설정). 응답에 `points[]`, `axes`, `stability`, `warnings`.
- **히트맵 렌더**: 각 셀이 `findPoint`로 O(N) 탐색 후 `heatColor(t)`로 색상 결정.
- **파일**: `SweepResultsPage.tsx:217-284`

---

## 7. UAT 스택 확인 사항

- **웹**: `http://127.0.0.1:18080` — 포트 18080에서 Next.js 개발 서버 제공.
- **API**: 포트 18000 — 백엔드 FastAPI 서버.
- **인증**: 로그인 후 JWT 토큰이 `useAuthStore`에 저장, 모든 API 요청에 `Authorization: Bearer` 헤더로 첨부.
- **i18n**: `ko` 카탈로그 사용. 번역 키는 `t("screener.*")`, `t("legacy.*")` 등 화면별 네임스페이스.
