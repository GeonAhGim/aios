# J6-SessionsPage — `/settings/sessions`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/SessionsPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/settings/sessions` / `frontend/apps/web/src/routes/settings/SessionsPage.tsx`
- 여정 소속: J6


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J6-SessionsPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 — 개인 트레이더(투자자). PERSONAS_AND_JOURNEYS.md §3: J6(설정·승인·감사) 여정의 주 페르소나. |
| 목적(한 문장) | 사용자가 로그인한 활성 세션(기기)을 확인하고, 의심 세션을 폐기하거나 전체 로그아웃하여 계정 접근을 관리한다. |
| 진입 조건 | `router.tsx:151` — 경로 `/settings/sessions`, `protect(<SessionsPage />)`으로 `ProtectedRoute` 가드 적용(로그인 필요). |
| 사용자 준비물 | 로그인 상태(access token 보유). `SessionsPage.tsx:51` — `VITE_API_BASE_URL` 환경 변수(기본 `http://localhost:8000`). |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J6-SessionsPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| 세션 목록 (`useQuery`, queryKey: `["sessions"]`) | `SessionsPage.tsx:132-135` — `queryFn: fetchSessions` | 마운트 시 1회 조회. 폴링 미설정(반복 갱신 없음). `queryClient.invalidateQueries({ queryKey: ["sessions"] })`로 수동 갱신 — `handleRevoke` 성공 시(현재 세션 아닌 경우) `SessionsPage.tsx:157`에서 호출. | `ErrorBanner` 컴포넌트(`SessionsPage.tsx:63-76`)가 오류 분류: `classifyForbidden`(`SessionsPage.tsx:10`) true → `ForbiddenNotice`(`SessionsPage.tsx:64`) 표시. 그 외 → `routeApiError`(`SessionsPage.tsx:13`)로 라우트 후 `ErrorMessage`(`SessionsPage.tsx:68`) 표시. `ErrorMessage`는 `backoff_retry` 또는 `refetch_retry`일 때 `onRetry` 버튼 제공(사용자 재시도). `fetchSessionsDefault`(`SessionsPage.tsx:53-54`)는 현재 `SessionsRouteNotImplementedError`를 던짐(서버 API 미구현 중). |
| 개별 세션 폐기 (`handleRevoke`) | `SessionsPage.tsx:142-163` — `client.revoke(view.sessionId)` | 사용자가 "폐기" 버튼 클릭 시 1회 요청(`SessionsPage.tsx:105-106`). `window.confirm` 확인 필요. | `try/catch`로 `actionError`에 저장(`SessionsPage.tsx:159`) → `ErrorBanner(error={actionError})`(`SessionsPage.tsx:188`) 표시. 현재 세션이면 `navigate("/login")`(`SessionsPage.tsx:154`)으로 리다이렉트. 비동기 실패 시 `setPendingId(null)`(`SessionsPage.tsx:161`)로 버튼 비활성화 해제. |
| 전체 로그아웃 (`handleRevokeAll`) | `SessionsPage.tsx:168-174` — `client.revokeAll()` → `logoutClient.logoutAll()` | 사용자가 "전체 로그아웃" 버튼 클릭 시 1회 요청(`SessionsPage.tsx:182-183`). `window.confirm` 확인 필요(`SessionsPage.tsx:169`). | `logout.ts`의 logoutAll은 "best-effort"(서버 결과 무시, 로컬 정리만 보장, task-454 decision) — 여기서 성공/실패 분기하지 않음. 무조건 `navigate("/login", { replace: true })`(`SessionsPage.tsx:173`). `pendingAll` 상태(`SessionsPage.tsx:122`)로 버튼 로딩 표시. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J6-SessionsPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J6-SessionsPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J6-SessionsPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J6-SessionsPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:105`) | TODO | TODO | TODO | TODO |
| `<Button>` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:182`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J6-SessionsPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J6-SessionsPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyForbidden` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:10`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:13`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:64`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:68`) | TODO | TODO | TODO |
| `Alert` (`frontend/apps/web/src/routes/settings/SessionsPage.tsx:206`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J6-SessionsPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J6-SessionsPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J6-SessionsPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
