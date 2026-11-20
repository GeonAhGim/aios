# J1-OnboardingFlowPage — `/onboarding/first-run`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/OnboardingFlowPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/first-run` / `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-OnboardingFlowPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더) — PERSONAS_AND_JOURNEYS.md §3: "시작·온보딩(J1) | P1" [확인] |
| 목적(한 문장) | 거래소 연결 → 전략 생성 → 페이퍼 실행의 3단계 온보딩 흐름에서 현재 진행 상황을 보여주고 다음 할 일을 안내하여 첫 실행 완주까지 유도 |
| 진입 조건 | `frontend/apps/web/src/router.tsx:89` — `/onboarding/first-run` 라우트. `protect()`로 ProtectedRoute 감싸짐: (1) 인증 토큰 필요, (2) MFA 활성화 필요(`/onboarding/mfa-setup` 통과 시), (3) 위험 프로필 작성 필요(`/onboarding/risk-assessment` 통과 시). 이 세 가드를 모두 통과한authenticated 사용자만 접근 가능 [확인] |
| 사용자 준비물 | 로그인, MFA 활성화, 위험 프로필 작성 완료(ProtectedRoute 가드 통과) [확인] |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-OnboardingFlowPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useExchangeCredentials` | `frontend/packages/shared-hooks/src/useExchanges.ts:5` | 1회 조회 (react-query default `staleTime: 0`, `refetchOnWindowFocus: true`). Mutation(`useRegisterExchangeCredential:16`, `useRevokeExchangeCredential:24`) 호출 시 `onSuccess` 콜백이 `qc.invalidateQueries({ queryKey: ["exchangeCredentials"] })`로 수동 무효화·재조회 (useExchanges.ts:16, 24) | react-query default `retry: 3`. 훅 내부 에러 처리 없음. `credentials.isError` → `deriveOnboardingProgress` (onboardingProgress.ts:65)에서 fail-closed(`false` 반환). 화면 에러 메시지·토스트·재시도 버튼 미표시 (OnboardingFlowPage.tsx:50-54) |
| `useMyStrategies` | `frontend/packages/shared-hooks/src/useStrategyBuilder.ts:14` | 1회 조회 (react-query default `staleTime: 0`, `refetchOnWindowFocus: true`). 전략 생성 Mutation(`useCreateStrategy:31`, `useGenerateWizardStrategy:51`, `useGenerateFromPrompt:57`)은 mutation 성공 시 `["myStrategies"]` 키 무효화 콜백이 없어 — 수동 갱신 필요 | react-query default `retry: 3`. 훅 내부 에러 처리 없음. 훅 반환값 `isError` (OnboardingFlowPage.tsx:53) → fail-closed. 화면 에러 메시지 미표시. 전략 생성 후 목록이 자동 갱신되지 않음 (gap) |
| `usePaperDeployments` | `frontend/packages/shared-hooks/src/usePaperControl.ts:7` | 1회 조회 (react-query default `staleTime: 0`, `refetchOnWindowFocus: true`). Mutation(`useRequestPaperDeployment:24`, `useDeploymentCommand:40`) 호출 시 `onSuccess` 콜백이 `qc.invalidateQueries({ queryKey: PAPER_DEPLOYMENTS_KEY })`로 수동 무효화·재조회 | react-query default `retry: 3`. 훅 내부 에러 처리 없음. 훅 반환값 `isError` (OnboardingFlowPage.tsx:54) → fail-closed. 화면 에러 메시지 미표시 |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-OnboardingFlowPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `credentials.isLoading \|\| strategies.isLoading \|\| deployments.isLoading` (OnboardingFlowPage.tsx:50) | `<LoadingState />` 컴포넌트 (OnboardingFlowPage.tsx:62-63) | 대기 | `구현됨` — `LoadingState` 렌더링: `OnboardingFlowPage.tsx:62-63` |
| 빈 상태 | 로딩 완료, `progress.isComplete === false`, 모든 단계의 데이터가 비어 있음 (OnboardingFlowPage.tsx:64, 72) | 단계별 카드 3개 표시: 각 카드에 번호(1~3), 제목/설명, 상태 뱃지("예정") — 현재 단계(`isCurrent`)는 CTA 버튼 (OnboardingFlowPage.tsx:73-105) | 현재 단계 CTA 버튼 클릭 → 해당 페이지로 이동 | `구현됨` — 단계 목록 렌더링: `OnboardingFlowPage.tsx:73-105`, 뱃지/CTA 분기: `OnboardingFlowPage.tsx:91-99` |
| 정상 | 로딩 완료, `progress.isComplete === false`, 일부 단계 완료 | 완료된 단계는 "완료" 뱃지(`Badge tone="success"`, OnboardingFlowPage.tsx:92), 현재 단계는 CTA 버튼, 미수행 단계는 "예정" 뱃지 (OnboardingFlowPage.tsx:98) | 완료된 단계 확인, 현재 단계 CTA 클릭 | `구현됨` — `progress.steps` 순회 및 `complete`/`isCurrent` 분기: `OnboardingFlowPage.tsx:74-99` |
| 낡은 데이터(신선도 초과) | react-query 기본 `staleTime: 0` — 조회 직후 stale. `refetchOnWindowFocus: true`로 윈도우 포커스 시 재조회 (data 블록 참조) | 별도의 신선도 표시 UI 없음. stale 여부 UI에 반영되지 않음 | 윈도우 포커스 시 자동 재조회; 수동 갱신 버튼 없음 | `없음` — stale 상태 시각화 없음. `refetchOnWindowFocus`로만 대응 |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-OnboardingFlowPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | 이 화면에는 입력 폼이 없다(facts.md Form fields 표 비어 있음). mutation 호출도 없다(OnboardingFlowPage.tsx:46-48은 조회 훅 3개뿐). 발생 경로: 없음 | 없음(오류 문구 키 없음) | 없음 | 없음 |
| 인증 필요/세션 만료(401) | 조회 훅(`useExchangeCredentials:useExchanges.ts:5`, `useMyStrategies:strategyBuilder.ts:14`, `usePaperDeployments:usePaperControl.ts:7`)이 401 응답 받으면 react-query가 `refreshAccessToken` 호출(tokenRefresh.ts:50). refresh 실패 시 `clearHandler` 호출 → `useAuthStore.logout()(useAuthStore.ts:49)` → `ProtectedRoute`에서 `!token` 분기(ProtectedRoute.tsx:20) → `/login?next=...` 리다이렉트 | 없음(오류 문구 키 없음). 화면이 `isError`를 읽지만(OnboardingFlowPage.tsx:50-54) 에러 UI 미표시. `deriveOnboardingProgress` (onboardingProgress.ts:65)에서 fail-closed(`false` 반환) | 없음(화면 내 행동 없음). 전역 처리: 401 → 로그아웃 → `/login` 리다이렉트 (ProtectedRoute.tsx:20-21) | 없음(화면 외 전역 처리: `tokenRefresh.ts:50` refresh → `useAuthStore.ts:49` logout → `ProtectedRoute.tsx:20` loginRedirect) |
| 권한 없음/MFA 필요(403) | 조회 훅이 403 응답 받으면 `tokenRefresh.ts:66`의 `clearHandler` 호출 → `useAuthStore.logout()` → `ProtectedRoute`에서 `!token` 분기 → `/login` 리다이렉트. `ProtectedRoute`는 403을 직접 처리하지 않음(토큰 폐기 후 401 경로로 우회). `protect()` 정의: `router.tsx:70` → `ProtectedRoute` | 없음(오류 문구 키 없음). 화면이 403을 직접 받지 않음(토큰 폐기 → 리다이렉트) | 없음(화면 내 행동 없음). 전역 처리: 403 → 토큰 폐기 → 로그아웃 → `/login` 리다이렉트 (ProtectedRoute.tsx:20-21) | 없음(화면 외 전역 처리: `tokenRefresh.ts:66` clearHandler → `useAuthStore.ts:49` logout → `ProtectedRoute.tsx:20` loginRedirect. `protect()` 정의: `router.tsx:70`) |
| 충돌/상태 불일치(409) | 이 화면에 mutation 호출이 없다(OnboardingFlowPage.tsx:46-48). 발생 경로: 없음 | 없음(오류 문구 키 없음) | 없음 | 없음 |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-OnboardingFlowPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | API 응답이 423(Locked) 또는 429(Too Many Requests). react-query 기본 `retry: 3`으로 재시도 후 `isError` 전환 (useExchanges.ts:5, useStrategyBuilder.ts:14, usePaperControl.ts:7) | 재시도 실패 시 `isError` → `deriveOnboardingProgress`의 `hasNonEmptyList`가 `false` 반환 (onboardingProgress.ts:65). 화면에는 해당 단계가 "예정" 뱃지로 표시됨. 속도 제한 문구·재시도 시각적 피드백 없음 | — | `구현됨` — 속도 제한 전용 UI 없음. react-query retry 후 fail-closed로 "예정" 표시: `onboardingProgress.ts:65`, `OnboardingFlowPage.tsx:98` |
| 서버 오류(5xx) | API 응답이 5xx. react-query 기본 `retry: 3` 후 `isError` 전환 | `isError` → `hasNonEmptyList`가 `false` 반환. 화면에 에러 메시지·토스트·재시도 버튼 미표시. 전체 화면이 에러 상태로 전환되지 않고 단계별 "예정" 표시로 국소화됨 (OnboardingFlowPage.tsx:50-54, onboardingProgress.ts:65) | — | `구현됨` — 5xx 전용 UI 없음. fail-closed로 단계별 "예정" 표시: `OnboardingFlowPage.tsx:50-54`, `onboardingProgress.ts:65` |
| 네트워크 단절 | 연결 끊김으로 react-query 요청 실패. 기본 `retry: 3` 후 `isError` 전환. `refetchOnWindowFocus: true`로 윈도우 포커스 시 자동 재시도 시도 (data 블록 참조) | 네트워크 복구 시 `refetchOnWindowFocus`로 재조회 시도. 단 수동 재시도 버튼 없음. 실패 시 5xx와 동일하게 fail-closed | 윈도우 포커스 시 자동 재조회; 수동 재시도 버튼 없음 | `없음` — 네트워크 전용 UI 없음. react-query retry + `refetchOnWindowFocus`로만 대응: `data` 블록 |
| 부분 실패(일부 데이터만 실패) | 예: exchangeCredentials는 성공, strategies는 실패. 각 훅(`useExchangeCredentials`, `useMyStrategies`, `usePaperDeployments`)이 독립적으로 `isError` 판단 (OnboardingFlowPage.tsx:46-48) | 실패한 단계만 "예정" 뱃지로 표시. 성공한 단계는 "완료" 뱃지. 전체가 아닌 해당 단계만 "예정"으로 국소화됨. 에러 메시지 미표시 (OnboardingFlowPage.tsx:74-99, onboardingProgress.ts:65) | — | `구현됨` — 부분 실패 시 국소화 "예정" 표시: `OnboardingFlowPage.tsx:74-99`, `onboardingProgress.ts:65`. 단 에러 메시지 없음 (gap) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-OnboardingFlowPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:69`) — "대시보드로 이동" | `isLoading === false` (OnboardingFlowPage.tsx:62) AND `progress.isComplete === true` (OnboardingFlowPage.tsx:64). `deriveOnboardingProgress`(:51-55)가 세 단계 모두 `complete === true`를 반환: `hasExchangeConnection` (onboardingProgress.ts:73), `hasStrategy` (onboardingProgress.ts:74), `hasPaperDeployment` (onboardingProgress.ts:75-78). [확인] | `<Link to="/dashboard">` (:68) 라우팅으로 대시보드 이동. `react-router-dom`의 `Link` 컴포넌트 — 클라이언트 사이드 네비게이션. `frontend/apps/web/src/router.tsx:89`의 `/onboarding/first-run` 라우트에서 `/dashboard` (`frontend/apps/web/src/router.tsx:84`의 `"="/" → "/dashboard"`)로 이동. [확인] | `<Link>`는 네비게이션 실패 시 아무것도 렌더링하지 않음 — 에러 바운더리 없음. `frontend/apps/web/src/router.tsx:84`의 `"="/" → "/dashboard"` 가드가 `/dashboard` 라우트 정의 존재 여부만 확인 (`frontend/apps/web/src/router.tsx:110` `DashboardPage` 참조). [확인] | `/dashboard` (`frontend/apps/web/src/router.tsx:84`) |
| `<Button>` (`frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:95`) — 단계별 CTA ("다음 단계 시작" 등) | `isLoading === false`, `progress.isComplete === false` (OnboardingFlowPage.tsx:64), 현재 단계가 `isCurrent === true` (OnboardingFlowPage.tsx:77), 해당 단계가 `complete === false` (OnboardingFlowPage.tsx:91-93). `ONBOARDING_STEPS` 배열에서 현재 인덱스의 `step.to`가 결정됨: connectExchange→`/exchanges`, createStrategy→`/strategy-builder`, runPaper→`/system/paper-deployments` (OnboardingFlowPage.tsx:23, 30, 37). [확인] | `<Link to={step.to}>` (:94) 라우팅으로 해당 단계 페이지로 이동. react-router-dom 클라이언트 사이드 네비게이션. [확인] | `<Link>`는 네비게이션 실패 시 아무것도 렌더링하지 않음 — 에러 바운더리 없음. `step.to` 경로(`/exchanges`, `/strategy-builder`, `/system/paper-deployments`)가 라우트에 정의되지 않은 경우 `react-router-dom` 기본 404 처리로 갈 수 있음. [확인] | `step.to` (OnboardingFlowPage.tsx:94): `/exchanges` OR `/strategy-builder` OR `/system/paper-deployments` |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-OnboardingFlowPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | 해당 없음. 이 화면은 온보딩 진행 상황(거래소 연결·전략 생성·페이퍼 실행 완료 여부)을 **전용 읽기용**으로 표시하고, 각 단계의 `<Link>`로 해당 페이지(거래소 연결 페이지, 전략 빌더, 페이퍼 배포 페이지)로 **네비게이션**만 한다(`OnboardingFlowPage.tsx:68-69, 94`). 소스에 Mutation 훅(`useMutation`)이 없고, 주문·실행 생성·LIVE 전환·위임장 변경·출금 등 금전·위험 행동이 전혀 없음. 모든 훅(`useExchangeCredentials`, `useMyStrategies`, `usePaperDeployments`)은 조회 전용(read-only) — `shared-hooks/src/useExchanges.ts:5`, `shared-hooks/src/useStrategyBuilder.ts:14`, `shared-hooks/src/usePaperControl.ts:7`. |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-OnboardingFlowPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `useExchangeCredentials()` 조회 실패 (`credentials.isError === true`, react-query default `retry: 3` 소진 후) | 미정의 — 화면에 에러 메시지 미표시. `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:46` 훅 내부 에러 처리 없음. `frontend/apps/web/src/routes/onboarding/onboardingProgress.ts:65` `hasNonEmptyList`가 `isError` 시 `false` 반환(fail-closed) | 화면에 아무 표시 없음(빈 상태 또는 로딩 중인 데이터로 동작). 수동 재시도 버튼 없음. 윈도우 포커스 시 `refetchOnWindowFocus: true`로 자동 재조회 (`shared-hooks/src/useExchanges.ts:6-9` react-query 기본 옵션) | 미노출 — ErrorMessage 컴포넌트 호출 없음 (`:50-54`에서 `credentials.isError`를 progress 계산에만 사용) |
| `useMyStrategies()` 조회 실패 (`strategies.isError === true`) | 미정의 — 화면에 에러 메시지 미표시. `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:47` 훅 내부 에러 처리 없음. `onboardingProgress.ts:65` fail-closed | 화면에 아무 표시 없음. 전략 생성 Mutation(`useCreateStrategy`, `useGenerateWizardStrategy`, `useGenerateFromPrompt`)의 `onSuccess` 콜백이 `["myStrategies"]` 키 무효화 콜백을 보내지 않아 — 전략 생성 후 목록이 자동 갱신되지 않음(`shared-hooks/src/useStrategyBuilder.ts:31-35` mutation 정의에 `onSuccess` 없음; `shared-hooks/src/useExchanges.ts:16`과 `shared-hooks/src/usePaperControl.ts:24`의 `onSuccess`는 각각 `exchangeCredentials`/`paperDeployments` 키만 무효화) | 미노출 — progress 계산에만 사용, UI 렌더링에 에러 정보 전달 안 함 |
| `usePaperDeployments()` 조회 실패 (`deployments.isError === true`) | 미정의 — 화면에 에러 메시지 미표시. `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.tsx:48` 훅 내부 에러 처리 없음. `onboardingProgress.ts:65` fail-closed | 화면에 아무 표시 없음. Mutation(`useRequestPaperDeployment`, `useDeploymentCommand`)은 `onSuccess`에서 수동 무효화(`shared-hooks/src/usePaperControl.ts:24`) — 하지만 initial query 실패 시 재시도는 react-query default `retry: 3`에만 의존 | 미노출 — progress 계산에만 사용 |
| `deriveOnboardingProgress` 전체 실패 (3개 훅 중 2개 이상 `isError`) | 미정의 —同上 | fail-closed: 모든 실패 단계가 "미완료"로 처리됨. `currentStepId`는 첫 `complete === false` 단계(보통 `connectExchange`)로 고정. 사용자가 문제를 인지할 수 없는 상태 | 미노출 |
| react-query 네트워크 오류 (전체 훅 공통) | `errors.retryAfterSeconds` (`catalog.en.ts:38`, `catalog.ko.ts:30`) — 하지만 현재 화면에서 이 키를 호출하는 코드 없음 (`OnboardingFlowPage.tsx:44-55`에 error 관련 `t()` 호출 없음) | react-query 기본 `retry: 3`, `retryDelay: 1000, 2000, 4000ms`. 3회 소진 후 `isError`로 전이. 백그라운드에서 네트워크 복구 시 `refetchOnWindowFocus: true` 또는 `refetchOnMount: true`로 자동 재시도 | 미노출 — traceId 추출/표시 코드 없음 |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-OnboardingFlowPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `<ol>`(line 73)으로 단계 순서 semantics 제공. `<span>` 번호 배지(line 83)는 장식용(`font-semibold`, 시각적 구분). `<Link>`이 `<Button>`을 감싸(line 94-96) 키보드 포커스 가능. `PageHeader`(line 60)는 heading semantics 포함. `LoadingState`(line 63)는 컴포넌트 내부 aria 처리에 의존. | `aria-label`/`aria-live` 없음 — 로딩/완료 상태 변경 시 screen reader 알림 부재(line 62-71). 번호 배지(span, line 83)에 `aria-hidden="true"` 없음(장식적 숫자가 accessibility tree에 노출). CTA `<Button>`에 `aria-current="step"` 미부여(line 93-96)로 현재 단계 식별 불가. WCAG 2.5.3(ラ벨명), 4.1.2(이름·역할·값) 미준수 항목 존재. |
| i18n/지역화 | 사용 키 15건 모두 catalog.en.ts·catalog.ko.ts에 존재: `onboarding.pageTitle`(en:276/ko:283), `onboarding.completeTitle`(en:280/ko:287), `onboarding.completeDescription`(en:281/ko:288), `onboarding.goToDashboard`(en:282/ko:289), `onboarding.stepDone`(en:278/ko:285), `onboarding.stepUpcoming`(en:279/ko:286), `onboarding.steps.{connectExchange,createStrategy,runPaper}.{title,description,cta}`(en:286-302/ko:294-310). `useTranslation()` 사용(line 45) — 리터럴 문자열 직접 사용 없음. | `t(step.titleKey as any)` 등 `as any` 캐스트 3건(line 87, 88, 95) — 타입 안전성 손실. react-i18next `TFunction`이 키 타입을 좁혀주지 않아 `as any`로 우회. catalog에 키가 모두 있어 runtime fallback은 정상 동작하나, 키 삭제/이름 변경 시 tsc가 잡지 못함. |
| 성능 예산 | 훅 3개(mount 시 3회 동시 API 조회): `useExchangeCredentials`(line 46), `useMyStrategies`(line 47), `usePaperDeployments`(line 48). react-query default `staleTime: 0` → 매 렌더 stale, `refetchOnWindowFocus: true`로 윈도우 포커스 시 재조회. `deriveOnboardingProgress`(line 51-55) — 단순 객체 탐색, O(n) (n=3). 렌더: 1회 마운트 시 `<ol>` 3-card 순회, DOM 노드 수 적음(~20). 폴링/interval 없음. | `staleTime: 0` + `refetchOnWindowFocus: true` 조합 — 윈도우 포커스(탭 전환, 알림 센터 등) 시 3개 훅이 모두 재조회(line 46-48). 온보딩 페이지는 빈번한 탭 전환 시 불필요한 서버 부하 발생 가능. `refetchInterval` 설정 없음(데이터 변경 시 자동 갱신 없음). |
| 텔레메트리 | 오류 보고 호출 여부 미확인 — `OnboardingFlowPage.tsx`(line 1-110 전체)에 `captureException`(Sentry 등), `trackEvent`(analytics), `logError`(내부) 호출 없음. react-query `isError` 분기(line 52-54)에서 에러 상태가 `deriveOnboardingProgress`로 전달될 뿐, 별도 에러 리포팅/로그 호출 없음. |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-OnboardingFlowPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | 세 조회(`useExchangeCredentials`, `useMyStrategies`, `usePaperDeployments`)가 모두 완료이면 완료 카드에 "대시보드로 이동" 링크가 `/dashboard`를 가리키고, 단계 목록(`<ol>`)은 나타나지 않는다. | `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.test.tsx:65-75` — 세 훅에 모두 mock data를 넣고 `screen.getByRole("link", {name: "대시보드로 이동"})`의 `href`가 `/dashboard`임을, `queryByRole("list")`가 없음을 확인 |
| A2 | 로딩 중(`isLoading === true`)에는 `<LoadingState />`만 보이고, 완료 카드와 단계 CTA 버튼이 렌더되지 않는다. | `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.test.tsx:77-82` — `credentials.isLoading = true`로 목하고 `screen.queryByRole("link", {name: "거래소 연결하기"})`가 없음을 확인 |
| A3 | 세 조회 중 한 개가 `isError`여도 화면이 완료로 판정되지 않으며(`progress.isComplete === false`), 실패한 단계는 "예정" 뱃지로 남는다. 현재 단계 CTA는 `step.to`를 가리킨다. | `frontend/apps/web/src/routes/onboarding/OnboardingFlowPage.test.tsx:84-90` — `strategies.isError = true`로 목하고 `screen.getByRole("link", {name: "전략 만들기"})`가 존재하며 `href="/strategy-builder"`임을 확인 (fail-closed: `onboardingProgress.ts:65`에서 `isError` 시 `false` 반환) |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-OnboardingFlowPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
