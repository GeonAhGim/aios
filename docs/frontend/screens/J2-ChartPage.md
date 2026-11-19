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
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J2-ChartPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J2-ChartPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
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
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J2-ChartPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/chart/ChartPage.tsx:161`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/chart/ChartPage.tsx:221`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J2-ChartPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
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
