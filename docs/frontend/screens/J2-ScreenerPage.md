# J2-ScreenerPage — `/screener`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/ScreenerPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/screener` / `frontend/apps/web/src/routes/screener/ScreenerPage.tsx`
- 여정 소속: J2


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J2-ScreenerPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — J2 여정(종목 발견)의 주체. PERSONAS_AND_JOURNEYS.md §3: J2는 P1(+P2) 중심 [확인] |
| 목적(한 문장) | 필터 빌더로 조건을 구성해 종목을筛选하고, 결과를 차트/백테스트로 연결하여 종목 발견을 시작한다. |
| 진입 조건 | `/screener` 라우트(`router.tsx:121`) — `protect()`(`router.tsx:70-72`)가 `ProtectedRoute`로 감싸 인증 필요. feature flag 없음 [확인] |
| 사용자 준비물 | 없음. 인증된 사용자면 즉시 접근 가능 [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J2-ScreenerPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useQuery(["screener-run", submitted])` → `client.runScreen` | `POST /v1/foundation/screener/run` (`frontend/packages/api-client/src/apiRoutes.ts:431`, implemented=true), 호출은 `frontend/packages/api-client/src/clients/screener.ts:27-32`, 훅 사용 `frontend/apps/web/src/routes/screener/ScreenerPage.tsx:143-147` | 변경 요청 성격의 조회: `submitted`가 null이면 비활성(`enabled: submitted !== null`, `frontend/apps/web/src/routes/screener/ScreenerPage.tsx:146`), 「실행」이 검증을 통과해 `setSubmitted`할 때만 1회 조회(`handleRun`, `{P}:170-176`). refetchInterval 옵션 없음 → 폴링 없음. 수동 재시도는 오류 배너의 `query.refetch()`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:315`). retry·staleTime 등은 이 훅에서 지정하지 않고, 앱 루트 `new QueryClient()`도 옵션 없이 생성(`frontend/apps/web/src/main.tsx:15`) — 라이브러리 기본값의 실제 값은 미확인 | `query.isError`이면 `ScreenerErrorBanner`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:314-316`)가 `ErrorMessage`로 errorCode·message·traceId 표시(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:109-115`). `routeApiError` 결과가 `refetch_retry`/`backoff_retry`일 때만 재시도 버튼, `backoff_retry`면 `retryAfterSec` 전달(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:107,113`). 로딩은 `LoadingState`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:313`). 401/403/409/429 각각의 세부 분기는 `routeApiError` 정의(shared-types)를 읽지 않아 미확인 |
| `ScreenerRouteNotImplementedError` 단락 | `frontend/packages/api-client/src/clients/screener.ts:17-32` | `isRouteImplemented("screener.run")`이 false일 때만 fetch 전에 던짐. 현재 `apiRoutes.ts:431`이 true라 현재 설정에서는 도달하지 않는 방어 경로 | 던져지면 같은 `ScreenerErrorBanner`로 표시하되 재시도 버튼은 끔(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:105-107`) |
| `defaultScreenerClient()` (`useMemo`) | `createScreenerClient(baseUrl, getToken)` — baseUrl은 `VITE_API_BASE_URL ?? "http://localhost:8000"`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:52`), 토큰은 `useAuthStore.getState().token`(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:54-57`) | 마운트·`screenerClient` prop 변경 시에만 재생성(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:134`). 서버 조회 아님 | 해당 없음(생성 시 오류 경로 없음). 토큰 누락·만료 시 처리는 기반 HTTP 클라이언트(http.ts)를 읽지 않아 미확인 |
| `useState`(universe, filters, sort, validationIssues, submitted) | 로컬 상태 `frontend/apps/web/src/routes/screener/ScreenerPage.tsx:136-141` | 사용자 입력 시 갱신, 서버 호출 없음 | 검증 실패는 `describeScreenDefinitionIssues`가 `validationIssues`로 담아 `screener-validation-alert`로 표시하고 조회하지 않음(`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:174-176`) |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J2-ScreenerPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J2-ScreenerPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J2-ScreenerPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J2-ScreenerPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:257`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:271`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:303`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J2-ScreenerPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J2-ScreenerPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `routeApiError` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:9`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:18`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/screener/ScreenerPage.tsx:109`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J2-ScreenerPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J2-ScreenerPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J2-ScreenerPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
