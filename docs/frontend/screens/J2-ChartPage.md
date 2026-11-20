# J2-ChartPage — `/chart`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ChartPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/chart` / `frontend/apps/web/src/routes/chart/ChartPage.tsx`
- 여정 소속: J2


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J2-ChartPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) [확인: PERSONAS_AND_JOURNEYS.md §2, J2 여정 소속] |
| 목적(한 문장) | 선택한 심볼의 캔들 차트를 표시하고, 지표/도형/비교 심볼을 추가하며, 즉시 백테스트를 실행한다. |
| 진입 조건 | `router.tsx:120` — `{ path: "/chart", element: protect(<ChartPage />) }`. `protect()` 래퍼가 인증 가드(로그인 세션 필요). `ChartPage.tsx:68`에서 `searchParams.get("instrument_id")`로 읽고, `ChartPage.tsx:157-158`에서 `!instrumentId`이면 `NoInstrumentSelected`로 전환. |
| 사용자 준비물 | 로그인 세션, 차트 볼 심볼 (또는 /market/instruments에서 선택), 거래소(VENUE) 및 시간봉(Timeframe) 기본값(chartPageConfig.ts:14-15: BITGET/1h) |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J2-ChartPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useIndicatorSelection` (`useIndicatorSelection.ts:21`) | 로컬 state (`createDefaultOverlayRegistry`, `chart-engine/overlayRegistry`) | 1회 초기화(마운트 시 `useMemo`로 `createDefaultOverlayRegistry().list()` 1회 호출, `ChartPage.tsx:85`). 지표 선택/해제 시 `toggleIndicator`/`setSelectedIndicatorIds`로 변경 요청(`ChartPage.tsx:191`). 서버 통신 없음. | 해당 없음 — 순수 로컬 state |
| `useChartLayout` (`useChartLayout.ts:61`) | 서버 `CH-8 layout/persistence` (`chartingPort`, `chartingClient`, `chartPageConfig.ts:19`) | 1회 restore: `listLayouts(port)` 호출 후 최신 레이아웃 선택(`useChartLayoutPersistence.ts:97-101`). layoutId 확보 후 `onApplyView`로 뷰→모델 동기화(`ChartPage.tsx:107-113`). 변경 요청: `save()` 호출 시 `saveLayout(port, ...)` (`useChartLayoutPersistence.ts:140-158`). 폴링 없음. | `status: "restore_failed"`이면 `ChartLayoutErrorBanners.tsx:18-30`에서 `ErrorMessage` 표시. `routeApiError`로 오류 분류(`ChartLayoutErrorBanners.tsx:13`). 재시도 가능(`retryRestore`, `ChartPage.tsx:213`). save 실패 시 `saveStatus: "conflict"/"not_found"/"error"` 배너(`ChartLayoutErrorBanners.tsx:31-39`) |
| `useChartDrawings` (`useChartDrawings.ts:112`) | 서버 `CH-8 drawings` (`loadDrawings`/`saveDrawings`, `chartPageConfig.ts:19`) | 1회 restore: `layoutStatus`가 `"ready"`이고 `layoutId`가 null이 아닐 때 `loadDrawings(port, layoutId)` 1회 호출(`useChartDrawings.ts:96-107`). 변경 요청: `persist(layoutId)` 호출 시 `saveDrawings(port, ...)` (`useChartDrawings.ts:152-168`). 동적 폴링 없음. `lastSyncedRef`로 콘텐츠 변경 시에만 네트워크 호출(`useChartDrawings.ts:139-148`). | restore 실패: `restoreStatus: "restore_failed"` → `ChartDrawingsErrorBanners.tsx:25-36`에서 `ErrorMessage` + `retryRestore`. 404: `restoreStatus: "not_found"` → `ChartDrawingsErrorBanners.tsx:24`. save 실패: `saveStatus: "conflict"`(`STATE_CONCURRENCY_CONFLICT`), `"not_found"`(`RESOURCE_NOT_FOUND`), `"error"` → `ChartDrawingsErrorBanners.tsx:38-51`. `retryPersist`로 수동 재시도 |
| candles (`useQuery`, `ChartPage.tsx:135`) | `fetchCandles` (기본값 `marketDataClient.getCandles`, `chartPageConfig.ts:18`) | 1회 조회: `queryKey: ["chart-candles", venue, instrumentId, timeframe, start, end]` (`ChartPage.tsx:136`). `enabled: instrumentId !== null && instrumentId.trim().length > 0` (`ChartPage.tsx:138`). `VISIBLE_CANDLE_COUNT=200`, `DEFAULT_TIMEFRAME="1h"`, `DEFAULT_VENUE="BITGET"` (`chartPageConfig.ts:13-15`). react-query 기본 재시도(3회) 적용. 폴링/자동 갱신 없음. | `query.error` 발생 시 `routeApiError(query.error)` (`ChartPage.tsx:161`). `canRetry` 판정 후 `ErrorMessage` 재시도 버튼(`ChartPage.tsx:162`). candle 차트는 빈 상태로 표시(`points: []`). |
| coverage (`useQuery`, `ChartPage.tsx:144`) | `fetchCoverage` (기본값 `marketDataClient.getCoverage`, `chartPageConfig.ts:18`) | 1회 조회: `queryKey: ["chart-coverage", venue, instrumentId, timeframe]` (`ChartPage.tsx:145`). 같은 `enabled` 조건 (`ChartPage.tsx:147`). candles 조회와 독립적으로 항상 시도(`DC-18b`, `ChartPage.tsx:141-143`). react-query 기본 재시도(3회). | `coverageQuery.data`가 없으면 `CoverageBadge`에 빈 배열 전달(`ChartPage.tsx:181`). 별도 오류 배너 없음 — candles 오류와 독립 처리 |
| `useChartReplaySession` (`useChartReplaySession.ts:39`) | 로컬 state + candleStream (`chart-engine/candleStream`, `chart-engine/replay/replayController`) | 1회 초기화: `venue/instrumentId/timeframe` 변경 시 `createCandleStream` + `createReplayController` (`useChartReplaySession.ts:53-72`). `queryData` 도착 시 `stream.applyPage(series)` 1회 병합 (`useChartReplaySession.ts:76-79`). 재시도 없음 — candles 조회 결과에 의존. | candles 데이터가 없으면 `replayDisabled=true` (`useChartReplaySession.ts:85`). `displayCandles=[]`, `points=[]`. 별도 오류 처리 없음 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J2-ChartPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `query.isLoading`이 true — `instrumentId`가 있고 candles `useQuery`가 아직 데이터를 받지 못함 (`ChartPage.tsx:228`) | 회색 스피너 + "불러오는 중..." (`Feedback.tsx:31-37`) | 없음(자동 재시도: react-query 기본 재시도 3회) | 구현됨 (`ChartPage.tsx:228-229`: `query.isLoading ? <LoadingState />`) |
| 빈 상태 | `query.isError`가 false이고 `points.length === 0` — candles 조회는 성공했으나 서버가 빈 배열을 반환 (`ChartPage.tsx:230`) | 점선 테두리 박스 안에 "표시할 캔들이 없습니다." (`ChartPage.tsx:231`: `t("legacy.chartPage.t2")`) | 없음 — 사용자가 변경할 수 있는 입력 없음(심볼 선택은 이미 완료됨) | 구현됨 (`ChartPage.tsx:230-231`: `points.length === 0 ? <EmptyState>{t("legacy.chartPage.t2")}</EmptyState>`, 사실 시트: `legacy.chartPage.t2` @ `ChartPage.tsx:231`) |
| 정상 | `query.isError` false, `query.isLoading` false, `points.length > 0` — candles 데이터를 받음 (`ChartPage.tsx:232`) | 캔들 차트(`CandlestickChart`, `ChartPage.tsx:246`) + 지표 오버레이 + 도형 + 비교 심볼 + 백테스트 패널 등 전체 UI | 지표 추가/제거(`toggleIndicator`, `ChartPage.tsx:191`), 도형 그리기(`handleAddDrawing`, `ChartPage.tsx:197`), 템플릿 적용(`ChartPage.tsx:212-216`), 비교 심볼 추가/제거(`CompareSymbols`, `ChartPage.tsx:269-283`), 백테스트 실행(`BacktestPanel`, `ChartPage.tsx:259-267`) | 구현됨 (`ChartPage.tsx:232-248`: else 분기 전체) |
| 낡은 데이터(신선도 초과) | candles `useQuery`에 stale/freshness 옵션 미설정 (`ChartPage.tsx:135-139`) | (별도 UI 없음) | 없음 | 없음 (`ChartPage.tsx:135-139`: staleTime 등 freshness 옵션 미설정, 폴링/자동 갱신 없음) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J2-ChartPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | candles 조회(`ChartPage.tsx:135`)가 400을 던져 `query.isError`가 참 (`ChartPage.tsx:220`) | `ErrorMessage`가 `errorCode`/`message`로 문구를 만든다(`ErrorMessage.tsx:36`, 문구는 shared-types getApiErrorMessage 매핑 — 개별 문구는 미확인). `traceId`가 있으면 `legacy.errorMessage.t1` 표시(`ErrorMessage.tsx:60`). `fieldErrors`를 넘기지 않으므로(`ChartPage.tsx:221-227`) 필드별 표시·배너 숨김은 없다 | 재시도 없음: `canRetry`는 `refetch_retry`/`backoff_retry`일 때만 참(`ChartPage.tsx:162`) | 부분: 공통 배너로만 처리, 400 전용 분기는 없음 (`ChartPage.tsx:161`에서 `routeApiError(query.error)`로 분류하지만 kind별 분기 렌더는 없다 — 모든 오류가 `ChartPage.tsx:221`의 `ErrorMessage` 하나로 간다)|
| 인증 필요/세션 만료(401) | candles 조회가 401을 던짐 (`ChartPage.tsx:220`) | 위와 같은 `ErrorMessage` 배너(`ChartPage.tsx:221`). 문구는 getApiErrorMessage 매핑(개별 문구는 미확인) | 재시도 없음(`ChartPage.tsx:162`). 로그인 화면 이동 등 이 화면 안의 401 전용 처리는 없음 — 라우트 보호는 `protect()`(`router.tsx:120`)이며 401 응답 시 동작은 미확인 | 부분: 공통 배너만, 401 전용 분기 없음 (`ChartPage.tsx:161`, `ChartPage.tsx:221`)|
| 권한 없음/MFA 필요(403) | candles 조회가 403을 던짐 (`ChartPage.tsx:220`) | 위와 같은 `ErrorMessage` 배너(`ChartPage.tsx:221`). 문구는 getApiErrorMessage 매핑(개별 문구는 미확인) | 재시도 없음(`ChartPage.tsx:162`). MFA 진입 등 403 전용 행동 없음 | 부분: 공통 배너만, mfa_required/forbidden 분기 렌더 없음 (`ChartPage.tsx:161`, `ChartPage.tsx:221`)|
| 충돌/상태 불일치(409) | candles 조회가 409를 던짐 (`ChartPage.tsx:220`) | 위와 같은 `ErrorMessage` 배너(`ChartPage.tsx:221`). 문구는 getApiErrorMessage 매핑(개별 문구는 미확인) | `routeApiError`가 `refetch_retry`로 분류하면 `canRetry`가 참이 되어 재시도 버튼이 나오고 `query.refetch()`를 호출(`ChartPage.tsx:162`, `ChartPage.tsx:226`; 버튼 렌더 `ErrorMessage.tsx:61-67`). data_coverage_missing/invalid_transition은 `canRetry`가 거짓 — 버튼 없음 | 구현됨: 409 중 `refetch_retry`만 재시도 가능(`ChartPage.tsx:162`). 나머지 409는 배너만|
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J2-ChartPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | HTTP 423 `AUTH_ACCOUNT_LOCKED` 또는 429 `RATE_LIMIT_EXCEEDED` | `ErrorMessage` 배너(Alert danger tone)에 한국어 메시지 표시. 423: "계정이 잠겼습니다. 잠시 후 다시 시도해주세요." (`apiError.ts:64`). 429: "요청이 너무 많습니다. 잠시 후 다시 시도해주세요." (`apiError.ts:78`). 재시도 가능 시 버튼 + 카운트다운 (`ErrorMessage.tsx:63-67`). | 423: `deriveLockout` → `kind: "account_locked"` (`accountLockout.ts:41-50`). `ErrorMessage`에서 `classifyRetry` → backoff 카운트다운. `retryAfterSec`은 api-client가 §9 PLT-25 규칙으로 파싱 (`accountLockout.ts:3-4`). 429: `classifyRetry` → `BACKOFF_ERROR_CODES` (`retryable.ts:27-31`) → `kind: "backoff_retry"`. 백오프 종료 후 재시도 버튼 활성화. | 구현됨. candles 쿼리(`ChartPage.tsx:135`)의 `query.error`가 423/429이면 `routeApiError` (`ChartPage.tsx:161`)가 `errorRouting.ts:88`의 `RoutedApiError`로 분류하고, `ErrorMessage` (`ChartPage.tsx:221`)가 UI 렌더링. react-query 기본 재시도(3회)도 먼저 적용 (`ChartPage.tsx:138`). |
| 서버 오류(5xx) | HTTP 500 `INTERNAL_ERROR`, 502 `EXCHANGE_FATAL`, 503 `EXCHANGE_UNAVAILABLE`/`DEPENDENCY_NOT_READY` | `ErrorMessage` 배너. 500: "일시적인 오류가 발생했습니다. 문제가 계속되면 문의해주세요." (`apiError.ts:83`). 502: "거래소 자격증명를 확인해주세요." (`apiError.ts:80`). 503: "거래소 연결이 원활하지 않습니다. 잠시 후 다시 시도해주세요." (`apiError.ts:79`) 또는 "서비스가 준비 중입니다. 잠시 후 다시 시도해주세요." (`apiError.ts:81`). trace_id 노출: 500/502는 `server_fatal` → traceId 표시(`ErrorMessage.tsx:60`). 503 retryable은 traceId 없음. | 500/502: `classifyServerError` → `kind: "fatal"` (`serverError.ts:47-49`). `ErrorMessage`에서 `canRetry=false` → 재시도 버튼 숨김. 503: `classifyServerError` → `kind: "retryable"` → `classifyRetry` → `kind: "backoff_retry"`. 재시도 버튼 표시. 모두 candles 쿼리(`ChartPage.tsx:135`)에서 `routeApiError`(`ChartPage.tsx:161`)로 분류. | 구현됨. `serverError.ts`가 4개 5xx 코드를 retryable/fatal로 분류 (`serverError.ts:39-52`). `errorRouting.ts:115-117`에서 갈래 분기. |
| 네트워크 단절 | 서버 응답 없음(DNS 실패, 연결 끊김, timeout). react-query 재시도 횟수 소진 후 `query.error` 발생 | `ErrorMessage` 배너. `errorCode`가 없으므로 `getApiErrorMessage`는 fallback: 서버 `message` 있으면 그 값, 없으면 "요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요." (`apiError.ts:117`). trace_id 없음. 재시도 버튼: `classifyRetry({errorCode: undefined})` → `kind: "none"` → `canRetry=false` → 버튼 숨김. | 네트워크 실패는 `ApiError` 인스턴스가 아님 → `query.error instanceof ApiError` false (`ChartPage.tsx:222`). `routeApiError`도 `isErrorCodeLike` 실패 → `{kind: "unknown"}` (`errorRouting.ts:132`). `canRetry=false` (`ChartPage.tsx:162`). 재시도 버튼 없음. react-query 기본 재시도(3회)가 먼저 시도 (`ChartPage.tsx:135-139`). | 구현됨(기본 동작). 단, 네트워크 실패 시 재시도 버튼이 표시되지 않음 — 명시적 retry UI 없음. react-query 재시도 3회만. |
| 부분 실패(일부 데이터만 실패) | candles 성공 + coverage 실패, 또는 candles 실패 + coverage 성공, 또는 레이아웃/도형 restore 실패 | candles 실패: `ErrorMessage` 배너 전체 표시. 차트 영역은 캔들 없음. coverage 실패: 별도 배너 없음. `CoverageBadge`에 빈 배열 전달 (`ChartPage.tsx:181`). 레이아웃 restore 실패: `ChartLayoutErrorBanners` 배너 (`ChartLayoutErrorBanners.tsx:18-30`). 도형 restore 실패: `ChartDrawingsErrorBanners` 배너 (`ChartDrawingsErrorBanners.tsx:25-36`). | candles+coverage는 독립 `useQuery` (`ChartPage.tsx:135`, `:144`). coverage 실패 시 별도 에러 핸들링 없음 — 갭. 레이아웃/도형 실패: 각각 `retryRestore`/`retryPersist`로 수동 재시도 가능 (`ChartPage.tsx:213`). | candles 실패 시 전체 에러 표시: 구현됨 (`ChartPage.tsx:220-228`). coverage 실패 시 별도 처리: 없음(갭). 레이아웃/도형 배너: 구현됨. |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J2-ChartPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `useIndicatorSelection` (`useIndicatorSelection.ts:21`) | 로컬 state (`createDefaultOverlayRegistry`, `chart-engine/overlayRegistry`) | 1회 초기화(마운트 시 `useMemo`로 `createDefaultOverlayRegistry().list()` 1회 호출, `ChartPage.tsx:85`). 지표 선택/해제 시 `toggleIndicator`/`setSelectedIndicatorIds`로 변경 요청(`ChartPage.tsx:191`). 서버 통신 없음. | 해당 없음 — 순수 로컬 state |
| `useChartLayout` (`useChartLayout.ts:61`) | 서버 `CH-8 layout/persistence` (`chartingPort`, `chartingClient`, `chartPageConfig.ts:19`) | 1회 restore: `listLayouts(port)` 호출 후 최신 레이아웃 선택(`useChartLayoutPersistence.ts:97-101`). layoutId 확보 후 `onApplyView`로 뷰→모델 동기화(`ChartPage.tsx:107-113`). 변경 요청: `save()` 호출 시 `saveLayout(port, ...)` (`useChartLayoutPersistence.ts:140-158`). 폴링 없음. | `status: "restore_failed"`이면 `ChartLayoutErrorBanners.tsx:18-30`에서 `ErrorMessage` 표시. `routeApiError`로 오류 분류(`ChartLayoutErrorBanners.tsx:13`). 재시도 가능(`retryRestore`, `ChartPage.tsx:213`). save 실패 시 `saveStatus: "conflict"/"not_found"/"error"` 배너(`ChartLayoutErrorBanners.tsx:31-39`) |
| `useChartDrawings` (`useChartDrawings.ts:112`) | 서버 `CH-8 drawings` (`loadDrawings`/`saveDrawings`, `chartPageConfig.ts:19`) | 1회 restore: `layoutStatus`가 `"ready"`이고 `layoutId`가 null이 아닐 때 `loadDrawings(port, layoutId)` 1회 호출(`useChartDrawings.ts:96-107`). 변경 요청: `persist(layoutId)` 호출 시 `saveDrawings(port, ...)` (`useChartDrawings.ts:152-168`). 동적 폴링 없음. `lastSyncedRef`로 콘텐츠 변경 시에만 네트워크 호출(`useChartDrawings.ts:139-148`). | restore 실패: `restoreStatus: "restore_failed"` → `ChartDrawingsErrorBanners.tsx:25-36`에서 `ErrorMessage` + `retryRestore`. 404: `restoreStatus: "not_found"` → `ChartDrawingsErrorBanners.tsx:24`. save 실패: `saveStatus: "conflict"`(`STATE_CONCURRENCY_CONFLICT`), `"not_found"`(`RESOURCE_NOT_FOUND`), `"error"` → `ChartDrawingsErrorBanners.tsx:38-51`. `retryPersist`로 수동 재시도 |
| candles (`useQuery`, `ChartPage.tsx:135`) | `fetchCandles` (기본값 `marketDataClient.getCandles`, `chartPageConfig.ts:18`) | 1회 조회: `queryKey: ["chart-candles", venue, instrumentId, timeframe, start, end]` (`ChartPage.tsx:136`). `enabled: instrumentId !== null && instrumentId.trim().length > 0` (`ChartPage.tsx:138`). `VISIBLE_CANDLE_COUNT=200`, `DEFAULT_TIMEFRAME="1h"`, `DEFAULT_VENUE="BITGET"` (`chartPageConfig.ts:13-15`). react-query 기본 재시도(3회) 적용. 폴링/자동 갱신 없음. | `query.error` 발생 시 `routeApiError(query.error)` (`ChartPage.tsx:161`). `canRetry` 판정 후 `ErrorMessage` 재시도 버튼(`ChartPage.tsx:162`). candle 차트는 빈 상태로 표시(`points: []`). |
| coverage (`useQuery`, `ChartPage.tsx:144`) | `fetchCoverage` (기본값 `marketDataClient.getCoverage`, `chartPageConfig.ts:18`) | 1회 조회: `queryKey: ["chart-coverage", venue, instrumentId, timeframe]` (`ChartPage.tsx:145`). 같은 `enabled` 조건 (`ChartPage.tsx:147`). candles 조회와 독립적으로 항상 시도(`DC-18b`, `ChartPage.tsx:141-143`). react-query 기본 재시도(3회). | `coverageQuery.data`가 없으면 `CoverageBadge`에 빈 배열 전달(`ChartPage.tsx:181`). 별도 오류 배너 없음 — candles 오류와 독립 처리 |
| `useChartReplaySession` (`useChartReplaySession.ts:39`) | 로컬 state + candleStream (`chart-engine/candleStream`, `chart-engine/replay/replayController`) | 1회 초기화: `venue/instrumentId/timeframe` 변경 시 `createCandleStream` + `createReplayController` (`useChartReplaySession.ts:53-72`). `queryData` 도착 시 `stream.applyPage(series)` 1회 병합 (`useChartReplaySession.ts:76-79`). 재시도 없음 — candles 조회 결과에 의존. | candles 데이터가 없으면 `replayDisabled=true` (`useChartReplaySession.ts:85`). `displayCandles=[]`, `points=[]`. 별도 오류 처리 없음 |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J2-ChartPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음. ChartPage는 캔들 차트 표시·지표 추가·도형 그리기·백테스트 시뮬레이션 전용 화면. 백테스트(`BacktestPanel.tsx:66-77`)는 `slippage.bps:0`, `commission.*:0`, `latencyMs:0`, 모든 `orderTypes:false`의 고정 프리셋으로 체결을 시뮬레이션할 뿐, 실제 주문/실행/LIVE 전환/위임장 변경/출금을 일으키는 경로를 호출하지 않음(`ChartPage.tsx:61` `runQuickBacktest=backtestsClient.runQuickBacktest` — POST `/v1/backtests/quick`는 백테스트 엔드포인트). `CoverageBadge`(ChartPage.tsx:181), `IndicatorParityPanel`(ChartPage.tsx:250), `StrategyMarkers`(ChartPage.tsx:257), `CompareSymbols`(ChartPage.tsx:269), `DrawingsList`(ChartPage.tsx:285) 모두 읽기 전용. 주문·실행·위임장·출금 관련 버튼·훅·API 호출 없음. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J2-ChartPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/chart/ChartPage.tsx:161`) | 없음 — 순수 분류 함수(`errorRouting.ts:88`). `query.error`를 `RoutedApiError`로 분류해 `kind`(refetch_retry/backoff_retry/server_fatal/not_found/forbidden/unknown 등) 반환. 화면에 직접 텍스트 렌더링 안 함. | 재시도 가능 여부(`kind === "refetch_retry" || kind === "backoff_retry"`)를 `canRetry`에 저장해 `ErrorMessage`의 `onRetry`/`retryAfterSec`에 전달(`ChartPage.tsx:162`). `server_fatal`이면 `traceId`를 downstream에 넘김. | 아니요 — `routeApiError` 자체는 UI 렌더링 안 함. 분류 결과의 `traceId`는 `server_fatal.kind`에 `serverError.traceId`로 담김(`errorRouting.ts:117`) |
| `ErrorMessage` (`frontend/apps/web/src/routes/chart/ChartPage.tsx:221`) | `getApiErrorMessage(errorCode, message)` — `shared-types/apiError.ts:106`. 알려진 errorCode면 `EXACT_MESSAGES` 고정 한국어 메시지, 모르면 `DEFAULT_API_ERROR_MESSAGE`. 재시도 버튼: `legacy.errorMessage.t3`="다시 시도". 백오프 카운트다운: `legacy.errorMessage.t2`="{{remainingSec}}초 후 재시도 가능". trace_id 안내: `legacy.errorMessage.t1`="지원코드: {{traceId}}". (`catalog.ko.legacyB.ts:9-13`) | `classifyRetry(kind)`가 "none" 아닐 때만 재시도 버튼 표시(`ErrorMessage.tsx:39`). backoff이면 `retryAfterSec` 초 카운트다운(`ErrorMessage.tsx:49-53`). 버튼 클릭 시 `onRetry` → `query.refetch()`(`ChartPage.tsx:226`). fieldErrors가 있으면 배너 숨김(`ErrorMessage.tsx:55`). | 예 — `query.error instanceof ApiError`일 때 `query.error.traceId`를 props로 전달(`ChartPage.tsx:224`). `ErrorMessage.tsx:60`에서 `{traceId && <p>...{{traceId}}</p>}`로 화면에 표시 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J2-ChartPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `data-testid="chart-instrument-id"` 존재(`ChartPage.tsx:178`). `PageHeader`가 `<h1>` 렌더(`Feedback.tsx:43`). `ErrorMessage`가 traceId/재시도 버튼 표시(`ErrorMessage.tsx:59-67`). | `aria-*` 속성 전무(`ChartPage.tsx:172-287` 전체 스캔 결과 `role=`, `aria-`, 키보드 핸들러 없음). `EmptyState`가 `<div>`만 렌더(`Feedback.tsx:24`), `aria-live` 또는 `role="status"` 없음. `LoadingState`가 스피너만 렌더(`Feedback.tsx:34`), `aria-busy="true"` 및 `role="status"` 누락. `ChartPanes`/`CandlestickChart`에 포커스 관리·키보드 네비게이션 확인 안 함 — 미확인. |
| i18n/지역화 | `useTranslation` import 및 1회 호출(`ChartPage.tsx:66`). `legacy.chartPage.title1`="차트"(`catalog.ko.legacyD.ts:119`), `legacy.chartPage.t2`="표시할 캔들이 없습니다."(`catalog.ko.legacyD.ts:120`) — 양쪽 카탈로그(ko/eng)에 모두 존재. `ErrorMessage`가 `legacy.errorMessage.t1/t2/t3` 사용(`ErrorMessage.tsx:60-67`) — `catalog.ko.legacyB.ts:9-13`, `catalog.en.legacyB.ts:7-11`에 정의. 카탈로그 누락 키: 없음. | `t()` 호출이 2곳(`ChartPage.tsx:175,231`)뿐 — 정적 텍스트(예: "차트", "표시할 캔들이 없습니다.")는 i18n화되었으나, UI 동적 텍스트(예: `instrumentId` 표시, `CoverageBadge` 라벨, `ChartToolbar` 버튼 텍스트, `CompareSymbols` 라벨, `DrawingsList` 텍스트)에 대한 i18n 키 사용 여부 확인 안 함 — 미확인. |
| 성능 예산 | `useQuery` 2건(candles, coverage) — 모두 1회 조회(`ChartPage.tsx:135-148`). 자동 폴링 없음. `VISIBLE_CANDLE_COUNT=200`, `DEFAULT_TIMEFRAME="1h"`(`chartPageConfig.ts:13-15`). `useMemo`로 `layoutView` 계산(`ChartPage.tsx:93-101`). react-query 기본 재시도 3회. `useChartDrawings`가 `lastSyncedRef`로 변경 감지 시에만 네트워크 호출(`useChartDrawings.ts:139-148`). | `handleAddDrawing`이 매 렌더마다 재생성(`ChartPage.tsx:168-170`, `useCallback` 없음). `setVenue`, `setTimeframe`, `setDrawingTool`이 `ChartToolbar`에 prop으로 전달되나 `useCallback` 감싸지 않음(`ChartPage.tsx:189-194`). candles 데이터가 변경될 때마다 `ChartPanes`→`CandlestickChart` 전체 재렌더 — memoization 확인 안 함(`ChartPage.tsx:240-247`) — 미확인. |
| 텔레메트리 | `routeApiError`로 오류 분류(`ChartPage.tsx:161`). `ErrorMessage`가 traceId 표시(`ErrorMessage.tsx:60`). | 외부 보고/로깅/애널리틱스 호출 전무(`ChartPage.tsx` 전체 스캔 결과). 오류 발생 시 `routeApiError`로 분류만 하고 외부 보고/로깅 연결 여부 확인 안 함 — 미확인. |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J2-ChartPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J2-ChartPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
