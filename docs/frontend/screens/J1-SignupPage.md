# J1-SignupPage — `/signup`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/SignupPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/signup` / `frontend/apps/web/src/routes/auth/SignupPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-SignupPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1(개인 트레이더). 근거: PERSONAS_AND_JOURNEYS.md:22 — "P1 | **개인 트레이더(투자자)** | 전략을 만들거나 고르고, 검증하고, 모의->실운용하며 감독 | J1~J6 전체 [확인]". :36 — "기존 J1~J6(+UX 문서의 J7~J9 후보)은 **P1 중심**". :40 — "시작·온보딩(J1) | P1, 이후 모든 페르소나가 **자기 버전**을 가진다". |
| 목적(한 문장) | 이메일과 비밀번호로 계정을 만들고, 성공하면 MFA 설정 화면(`/onboarding/mfa-setup`)으로 넘어간다. 근거: SignupPage.tsx:63-64(`signup.mutateAsync` 후 `navigate("/onboarding/mfa-setup")`). 부제: ":73('자동매매를 시작하기 위한 첫 단계입니다')". |
| 진입 조건 | 공개 라우트. `router.tsx:85`에서 `/signup`은 `protect(...)`로 감싸지 않고 `<SignupPage />`를 바로 렌더링한다. :87-89의 온보딩 라우트는 `protect`를 쓴다(대비용으로 인용). 로그인 상태 가드는 `router.tsx:85` 기준으로 없다. 이미 로그인한 사용자가 `/signup`에 왔을 때의 리다이렉트 여부는 코드에서 확인하지 못했으므로 "미확인"이다. 기능 플래그 게이트는 `router.tsx:85` 줄에서 확인되지 않는다. 로그인 화면에서 넘어오는 경로는 미확인. |
| 사용자 준비물 | 이메일 주소: SignupPage.tsx:75-80(`type="email"`, `required`). 비밀번호 12자 이상: SignupPage.tsx:89("12자 이상"), :94(`minLength={12}`). 인증 앱(Google Authenticator 등): SignupPage.tsx:104-105(`mfaPrenotice` 문구, "가입 전에 미리 설치해두세요"). 다음 단계(MFA 설정)에서 필요하다는 점도 같은 문구에 근거. 인증 앱 설치는 가입 전에 해둬야 한다. |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-SignupPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useFieldErrors` | `frontend/apps/web/src/hooks/useFieldErrors.ts:15` | 로컬 상태hook. `handleSubmit` catch에서 `setFromError(err)`로 동기 업데이트(1회). `extractFieldErrors(err)`가 `details.fields[]`를 `Record<string,string>`으로 파싱. 사용자가 이메일/비밀번호 입력값을 수정하면 `clearField("email")`/`clearField("password")`가 해당 키만 제거(나머지 필드 오류 유지). 폴링/재시도 없음. | `fieldErrors` 상태에 필드별 오류 메시지가 저장됨. SignupPage.tsx:75(`error={fieldErrors.email}`), :89(`error={fieldErrors.password}`)에서 `<Field error>`로 인라인 표시. `SignupError` 컴포넌트(:26-27)가 `classifyBadRequest`/`classifyForbidden`으로 400/403를 배너(`BadRequestNotice`/`ForbiddenNotice`)로 표시하고, 그 외는 `ErrorMessage`로 전달. 423(`AUTH_ACCOUNT_LOCKED`)은 `deriveLockout(err).retryAfterSec`로 잠금 카운트다운 시작(:68). |
| `useSignup` | `frontend/packages/shared-hooks/src/useAuth.ts:20` | `useMutation`. 폼 제출에서 mutateAsync로 1회 POST `/auth/register`(폴링 없음). onSuccess에서 setToken(data.accessToken) + qc.invalidateQueries({ queryKey: ["me"] }) 후 /onboarding/mfa-setup로 네비게이트. | 폼 제출 catch에서 setError로 에러 상태 설정, setFromError로 필드 오류 저장, deriveLockout으로 잠금 시간 설정. SignupError 컴포넌트가 오류 분류 후 BadRequestNotice/ForbiddenNotice/ErrorMessage로 렌더. 423일 때 useEffect가 1초 간격으로 lockoutRemainingSec 카운트다운. Button은 isPending으로 loading 표시. |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-SignupPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `signup.isPending`이 true (POST `/auth/register` 전송 중) | Button에 스피너 회전 + 버튼 라벨 `legacy.signupPage.t5` "가입하기" 계속 표시 (Button.tsx:47-50, children는 항상 렌더). 입력 필드는 비활성화되지 않으나 Button `disabled={locked}` 외 별도 disabled 없음. | 제출 차단: Button `loading` → `disabled={disabled \|\| loading}`(ui-web Button.tsx:39)으로 클릭 불가. | 구현됨 (SignupPage.tsx:111 `loading={signup.isPending}`, ui-web Button.tsx:39) |
| 빈 상태 | 초기 마운트 — email, password가 모두 빈 문자열 (`useState("")` :42-43) | 이메일/비밀번호 입력 필드가 빈 채로 표시. `legacy.signupPage.mfaPrenotice` 안내 문구 항상 보임 (:104-105). | 이메일/비밀번호 입력 후 "가입하기" 제출. 비밀번호는 minLength=12 (:94) 검증 있음. | 구현됨 (SignupPage.tsx:42-43 초기값, :75-102 입력 필드) |
| 정상 | `signup.mutateAsync` 성공 후 (`/auth/register` 2xx) | 별도 성공 문구 없음. `/onboarding/mfa-setup`로 이동 (:64). 사전 안내 문구 `legacy.signupPage.mfaPrenotice` ("다음 단계에서 Google Authenticator 등 인증 앱이 필요합니다. 가입 전에 미리 설치해두세요.")는 입력 화면에 항상 보이는 상태 (:104-105). | 로그인 링크 `legacy.signupPage.t7` "로그인"으로 `/login` 이동 (:115-116). | 구현됨 (SignupPage.tsx:63-64 `navigate("/onboarding/mfa-setup")`) |
| 낡은 데이터(신선도 초과) | 해당 없음 — 이 화면은 `useQuery`로 서버 데이터를 조회하지 않음. `useSignup`(:47)은 mutation 전용, `useMe`(:10-17)는 이 페이지에서 호출하지 않음. `useSignup.onSuccess`의 `qc.invalidateQueries({ queryKey: ["me"] })`(:27, useAuth.ts)는 토큰 갱신 후 신선도 갱신이지 본 화면 데이터就不是 | — | — | 없음 (조회 데이터 없음) |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-SignupPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | `handleSubmit` catch(`SignupPage.tsx:65`)에서 서버가 400 반환 → `setError(err)`(`:66`), `setFromError(err)`(`:67`). `SignupError` 컴포넌트(`:106`)가 `classifyBadRequest(error)`(`:26`) true 시 `<BadRequestNotice>` 렌더 | `BadRequestNotice` 컴포넌트(서버 메시지 노출, `BadRequestNotice.tsx:40-81`). `classifyBadRequest`가 "field"일 때 `details.fields` 비어 있으면 배너로 폴백(`BadRequestNotice.tsx:48-51`). `VALIDATION_INVALID_FIELD` → 필드별 인라인 오류(`Field.error` :75, :89) | 이메일/비밀번호 수정 시 `clearField("email")`(`:85`)/`clearField("password")`(`:100`)로 해당 필드 오류 제거 후 재제출 | 구현됨 (`SignupPage.tsx:65-68`, `:26`, `:106`, `BadRequestNotice.tsx:40-81`). 구체적인 400 처리 호출명: `classifyBadRequest` (`:26`), `BadRequestNotice` (`:26`) |
| 인증 필요/세션 만료(401) | 서버가 401 반환 → `classifyBadRequest` false(401이 아님), `classifyForbidden` false(403 아님) → `routeApiError(error)`(:28) 경로. `routeApiError`에서 `isSessionExpiredErrorCode` 확인 → `auth_required` 반환 또는 `classifyServerError`/`classifyRetry`로 전달 | `ErrorMessage` 컴포넌트 렌더(:28-37). `errorCode`/`message`/`traceId` 전달. `fieldErrors`도 전달되어 비어 있으면 배너 표시 | 로그인 링크 `legacy.signupPage.t7` "로그인"으로 `/login` 이동 (:115-116). `ErrorMessage`의 재시도 버튼은 `onRetry`가 없으므로 표시 안 됨 | `routeApiError`(:28)로 분기, `ErrorMessage`(:28-37) 렌더. 구체적인 401 처리 호출명: `routeApiError` (`:28`), `ErrorMessage` (`:30`). 401 전용 배선 없음(전체 catch 경로 통합) |
| 권한 없음/MFA 필요(403) | 서버가 403 반환 → `classifyForbidden(error)`(:27) true. errorCode에 따라 `mfa_required`/`tenant_mismatch`/`policy`/`forbidden` 분류 | `ForbiddenNotice` 컴포넌트 렌더(:27). `getApiErrorMessage`로 메시지 표시. `policy`일 때 `DenialReasons` 표시. `mfa_required`일 때 `onStepUp` 버튼(SignupPage에서는 없음) | 권한 문제이므로 재시도 무의미. 로그인 링크로 이동 가능 (:115-116) | 구현됨 (`:27`). 구체적인 403 처리 호출명: `classifyForbidden` (`:27`), `ForbiddenNotice` (`:27`) |
| 충돌/상태 불일치(409) | 서버가 409 반환 → `classifyBadRequest` false, `classifyForbidden` false → `routeApiError(error)`(:28). `routeApiError`에서 `classifyStateConflict` 확인 → `refetch_retry` 또는 `invalid_transition` 반환 | `ErrorMessage` 컴포넌트 렌더(:28-37). `classifyRetry`가 "refetch"이면 재시도 버튼 표시 가능(SignupPage에서는 `onRetry` 없음) | 이메일 중복 시 다른 이메일로 재입력 또는 로그인 링크 (:115-116). `ErrorMessage`에 `onRetry` 없음 | `routeApiError`(:28)로 분기, `ErrorMessage`(:28-37) 렌더. 구체적인 409 처리 호출명: `routeApiError` (`:28`). 409 전용 배선 없음(전체 catch 경로 통합) |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-SignupPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | 423(`AUTH_ACCOUNT_LOCKED`): `setError(err)`(:66) → `deriveLockout(err)`(:68)가 `retryAfterSec` 리턴 → `lockoutRemainingSec`(:45)가 0보다 큼 → `locked=true`(:49). 429(`RATE_LIMIT_EXCEEDED`): `setError`(:66) → `ErrorMessage`(:30-37) 렌더 | 423: `legacy.signupPage.t4` 카운트다운(:108-109). Button `disabled={locked}`(:111), 입력 필드 `disabled={locked}`(:82,:97). 429: `ErrorMessage`(:30-37) 렌더 | 423: 잠김 해제 후 "가입하기" 재시도. 429: `ErrorMessage` 재시도 버튼 클릭 시 재전송 가능(SignupPage에서는 `onRetry` 없음) | 423 구현됨(`lockoutRemainingSec`(:45), 카운트다운 useEffect(:51-55), `locked`(:49), `disabled={locked}`(:111,:82,:97), `t4`(:108-109)). 429: `setError`(:66) → `ErrorMessage`(:30-37) 렌더 |
| 서버 오류(5xx) | 서버가 5xx 반환 → `setError(error)`(:66) → `ErrorMessage`(:30-37) 렌더. `traceId` 있으면 `legacy.errorMessage.t1`로 trace_id 노출(:60) | `ErrorMessage`(:30-37) 렌더, 재시도 버튼과 카운트다운 표시 | 재시도 버튼 클릭 시 재전송 가능(SignupPage에서는 `onRetry` 없음). fatal은 수동 재시도 필요 | `setError`(:66) → `ErrorMessage`(:30-37) 렌더. 재시도 버튼 `onRetry` props 없음 |
| 네트워크 단절 | `apiClient.register`(:24) 네트워크 오류 → `catch`에서 `err instanceof ApiError` false → `new Error(t("legacy.signupPage.t8"))`(:66) | `ErrorMessage`(:30-37) 렌더. `errorCode` undefined, `message="회원가입에 실패했습니다."`(:66). `traceId` 없음. 재시도 버튼 없음 | "가입하기" 버튼 재클릭으로 재시도 | `ErrorMessage`에 `message` 전달. 재시도 버튼 없음. 수동 재시도만 가능 |
| 부분 실패(일부 데이터만 실패) | 해당 없음 — 회원가입은 단일 POST `/auth/register`(:24). `useSignup` 훅(`useAuth.ts:24`)은 `mutationFn` 하나만 정의 | N/A | N/A | 해당 없음(단일 요청 화면) |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-SignupPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:111`) | 이메일·비밀번호 입력 후 `<form onSubmit={handleSubmit}>` (:74) 제출. `locked`가 false (:59 조기 반환, :111 `disabled={locked}`). 비밀번호 `minLength={12}` (:94)와 `required` (:79, :93)는 브라우저 검증. `loading`은 `signup.isPending` (:111). ui-web Button.tsx:39에서 `disabled={disabled || loading}`이 DOM `disabled` 속성에 전달. `type="submit"`은 ButtonProps가 ButtonHTMLAttributes를 확장하므로 `<button>`으로 전달됨. | `handleSubmit` (:57-70)의 `signup.mutateAsync({ email, password })` (:63) 완료 후 `navigate("/onboarding/mfa-setup")` (:64). `useSignup.onSuccess` (shared-hooks/useAuth.ts:25-27)에서 `setToken(data.accessToken)` + `qc.invalidateQueries({ queryKey: ["me"] })` 실행. | catch (:65-68)에서 `setError` (:66), `setFromError` (:67), `setLockoutRemainingSec(deriveLockout(err).retryAfterSec)` (:68). `navigate` 없으므로 `/signup` 유지. `SignupError` (:106)가 400/403/그 외 분기 (:26-37). 423(`AUTH_ACCOUNT_LOCKED`)이면 `locked`가 되어 `useEffect` (:51-55)의 1초 간격 카운트다운 (:107-110) 표시. 423 외 오류는 `deriveLockout`이 `{ locked: false, retryAfterSec: 0 }` 반환 (shared-types/accountLockout.ts:47) — 잠금 없음. | 성공: `/onboarding/mfa-setup` (router.tsx:87 `{ path: "/onboarding/mfa-setup", element: protect(<MfaSetupPage />) }`). 실패: `/signup` 유지. |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-SignupPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-SignupPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`) | 분류기는 문구를 만들지 않는다. 400이면 `SignupError`가 `BadRequestNotice`를 렌더(`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`). | 분기 판정만 한다. 400이 아니면 다음 분기(`classifyForbidden`)로 진행(`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`). | 노출 없음. 분기 조건만 사용하며 traceId를 넘기지 않음(`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`). |
| `BadRequestNotice` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`, `frontend/apps/web/src/components/BadRequestNotice.tsx:40-81`) | 문구는 `getApiErrorMessage(errorCode, message)` 결과(`frontend/apps/web/src/components/BadRequestNotice.tsx:62`). 고정 한국어 문자열이며 i18n 키 없음. 알려진 코드는 `EXACT_MESSAGES`(`frontend/packages/shared-types/src/apiError.ts:55`), 미지 코드는 `PREFIX_MESSAGES` 접두 매칭(`frontend/packages/shared-types/src/apiError.ts:113`), 그 외 `DEFAULT_API_ERROR_MESSAGE`(`frontend/packages/shared-types/src/apiError.ts:115`). `field` 갈래에서 필드맵이 비면 서버 message 우선(`frontend/apps/web/src/components/BadRequestNotice.tsx:54`). 버튼 키는 `legacy.badRequestNotice.t1`/`t2`(`frontend/apps/web/src/components/BadRequestNotice.tsx:70`, `frontend/apps/web/src/components/BadRequestNotice.tsx:76`). | `field` 갈래에서 `extractFieldErrors` 결과가 있으면 null 반환(`frontend/apps/web/src/components/BadRequestNotice.tsx:49`) → 입력 옆 인라인 오류가 담당. 이 화면은 `onReload`/`onFocusMfaCode`를 넘기지 않아(`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`) 재조회·코드 재입력 버튼(`frontend/apps/web/src/components/BadRequestNotice.tsx:67-77`)은 렌더되지 않는다 → 사용자가 직접 입력을 고쳐 재제출해야 함. | 노출 없음. 컴포넌트가 traceId prop을 받지 않음(`frontend/apps/web/src/components/BadRequestNotice.tsx:22-26`). |
| `classifyForbidden` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`) | 분류기는 문구를 만들지 않는다. 403이면 `SignupError`가 `ForbiddenNotice`를 렌더(`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`). | 분기 판정만 한다. 아니면 `routeApiError` 경로로 진행(`frontend/apps/web/src/routes/auth/SignupPage.tsx:28`). | 노출 없음(`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`). |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`, `frontend/apps/web/src/components/ForbiddenNotice.tsx:31-50`) | 문구는 `getApiErrorMessage(extractErrorCode(error), extractMessage(error))`(`frontend/apps/web/src/components/ForbiddenNotice.tsx:36`). i18n 키 없음, 예: `AUTHZ_FORBIDDEN`·`AUTH_MFA_REQUIRED`는 `EXACT_MESSAGES` 고정 문구(`frontend/packages/shared-types/src/apiError.ts:65`, `frontend/packages/shared-types/src/apiError.ts:68`). 버튼 키 `legacy.forbiddenNotice.t1`(`frontend/apps/web/src/components/ForbiddenNotice.tsx:45`). | `policy` 갈래는 `DenialReasons`로 상세 사유 표시(`frontend/apps/web/src/components/ForbiddenNotice.tsx:41`). 이 화면은 `onStepUp`을 넘기지 않아(`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`) step-up 버튼(`frontend/apps/web/src/components/ForbiddenNotice.tsx:42-47`)은 렌더되지 않는다 → 안내는 문구뿐. | 노출 없음. traceId prop 없음(`frontend/apps/web/src/components/ForbiddenNotice.tsx:14-17`). |
| `routeApiError` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:28`) | 문구를 렌더하지 않는다. 결과의 `kind`가 `"backoff_retry"`일 때만 `afterSec`를 `retryAfterSec`로 `ErrorMessage`에 전달(`frontend/apps/web/src/routes/auth/SignupPage.tsx:34`). | 400/403이 아닌 오류의 재시도 카운트다운 값 산출에만 사용. 그 외 `kind`별 처리는 이 화면에서 쓰이지 않음(미확인: `routeApiError` 구현은 이 작업에서 열람하지 않음). | 이 호출은 traceId를 전달하지 않음(`frontend/apps/web/src/routes/auth/SignupPage.tsx:34`). traceId는 `ErrorMessage`에 직접 전달(아래 행). |
| `ErrorMessage` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:30`, `frontend/apps/web/src/components/ErrorMessage.tsx:27-72`) | 문구는 `getApiErrorMessage(errorCode, message)`(`frontend/apps/web/src/components/ErrorMessage.tsx:36`). i18n 키 없음. 보조 키: 추적 ID `legacy.errorMessage.t1`(`frontend/apps/web/src/components/ErrorMessage.tsx:60`), 카운트다운 `legacy.errorMessage.t2`(`frontend/apps/web/src/components/ErrorMessage.tsx:64`), 재시도 버튼 `legacy.errorMessage.t3`(`frontend/apps/web/src/components/ErrorMessage.tsx:67`). | `fieldErrors`가 비어 있지 않으면 배너 생략(`frontend/apps/web/src/components/ErrorMessage.tsx:55`) → 필드별 인라인 오류가 담당. 이 화면은 `onRetry`를 넘기지 않아(`frontend/apps/web/src/routes/auth/SignupPage.tsx:30-36`) 재시도 버튼(`frontend/apps/web/src/components/ErrorMessage.tsx:61`)과 카운트다운 표시는 렌더되지 않는다 → 사용자가 직접 재제출. | **노출함.** `ApiError`이면 `error.traceId`를 전달(`frontend/apps/web/src/routes/auth/SignupPage.tsx:33`)하고 값이 있을 때 "추적 ID" 문구로 표시(`frontend/apps/web/src/components/ErrorMessage.tsx:60`). `ApiError`가 아니면 undefined라 숨김. |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-SignupPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | `Input` 필드에 `htmlFor`/`id` 매칭: email(`:75-77`), password(`:89-91`). `Button`에 `type="submit"`(`:111`). 비밀번호 필드에 `autocomplete="new-password"`(`:95`), 이메일에 `autocomplete="email"`(`:80`). `role="note"`으로 MFA 안내(`:104`), `role="status"`으로 잠금 카운트다운(`:108`). 오류 배너는 `SignupError` → `BadRequestNotice`/`ForbiddenNotice`/`ErrorMessage`가 자체적으로 `role="alert"` 또는 `role="status"`를 쓴다(하위 컴포넌트 확인 필요). 키보드 TAB 순서: email → password → Button → Login Link(HTML 순서 그대로). 포커스 관리: 제출 실패 시 특정 필드로 포커스 이동하는 코드는 없음(`:65-68` — `setError`만 호출). | **개선**: 포커스 이동 미구현. 제출 실패 시 첫 오류 필드(email)로 `focus()` 이동이 바람직(WCAG 2.2 AA 3.3.1). `Field` 컴포넌트의 `aria-describedby`가 `error` 메시지와 연결되는지 하위 확인 필요. |
| i18n/지역화 | 모든 가시 문자열이 i18n 키로 추출됨(`catalog.en.legacyC.ts:70-80`, `catalog.ko.legacyC.ts:72-82`). 키 접두사 `legacy.signupPage.`. 포함: title1, label2, label3, t4(카운트다운), t5(버튼), t6+t7(로그인 링크), t8(실패 메시지), mfaPrenotice. 숫자 형식: 카운트다운 `{{lockoutRemainingSec}}`는 정수만 표시(`Math.max(0, sec-1)`). 통화/날짜/시간대 없음. 복수형 없음. | **확인**: `t6`가 `"이미 계정이 있으신가요?{{val}}"`로 `val: " "`(공백)을 전달받아 연결함(`:114`). 영어판은 `"Already have an account?{{val}}"`으로 동일 패턴. 지역화 갭 없음. |
| 성능 예산 | 렌더 비용: 폼 요소 2개, 버튼 1개, 링크 1개 — 경량. 외부 요청 없음(초기 마운트 시 `useSignup`은 mutation 전용, `isPending` false). POST `/auth/register`는 1회만 발생. LCP/INP 영향: 최소. 폴링/재시도 없음. | **확인**: 이 화면은 차트·긴 목록·이미지 없음. LCP는 `<h1>`(타이틀)으로 이미 DOM에 존재. INP는 버튼 클릭 1회만 발생. 성능 예산 초과 위험 없음. |
| 텔레메트리 | 코드에서 명시적 telemetry/event 추적 없음(`:1-121`). 오류는 `ErrorMessage.traceId`로 `error.traceId`를 표시(`:33`) — 개발자 디버깅용일 뿐 이벤트 로깅 아님. `useSignup.onSuccess`에서 `qc.invalidateQueries({ queryKey: ["me"] })` 후 네비게이트(`:63-64`) — React Query 캐시 무효화만 있고 이벤트 기록 없음. | **개선**: 회원가입 성공/실패 이벤트(종류, 결과 코드) 추적 없음. WCAG 3.3.1 포커스 이동 구현 전까지 telemtry 우선순위 낮음. |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-SignupPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | 이메일과 비밀번호(12자 이상)를 입력하고 "가입하기"를 누르면 `/onboarding/mfa-setup`로 이동한다. | `SignupPage.test.tsx:54-61` — `mutateAsync` mock 성공 후 `screen.getByText("MFA 설정 페이지")` 검출 |
| A2 | 회원가입 실패 시 원본 API 메시지(raw error)를 사용자에게 노출하지 않는다(400→BadRequestNotice 매핑 문구, 403→ForbiddenNotice, 그 외→"회원가입에 실패했습니다.", 423→카운트다운 안내). | `SignupPage.test.tsx:65-143` — 423/400/NetworkError/VALIDATION_INVALID_FIELD 네 가지 실패 경로에서 raw message(`"raw lock detail"`, `"ECONNRESET"`)가 렌더되지 않음을 검증 |
| A3 | MFA 사전 고지 문구("다음 단계에서 Google Authenticator 등 인증 앱이 필요합니다. 가입 전에 미리 설치해두세요.")가 초기 마운트 시 항상 표시된다. | `SignupPage.test.tsx:43-51` — `screen.getByText`로 사전 고지 문구 존재 검출 |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-SignupPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | 제출 실패 시 첫 오류 필드로 포커스 이동(WCAG 2.2 AA 3.3.1) | 포커스 이동 코드 없음(`:65-68` — `setError`만 호출, `useRef`로 input 참조 안 함) | 개선 | Phase 1 |
| G2 | `protect(...)` 없이 `/signup`이 공개 라우트 — 이미 로그인한 사용자가 `/signup`에 접근할 때 리다이렉트 여부 미확인 | `router.tsx:85`에서 `/signup`은 `protect` 없이 직접 렌더링. 로그인 상태 체크 후 리다이렉트 코드 없음 | 개선 | Phase 1 |
| G3 | `Field` 컴포넌트의 `error` prop이 `aria-describedby`로 오류 메시지 요소와 연결되는지 불명 | `Field` 컴포넌트 소스 미확인(하위 컴포넌트 의존). `error` prop 전달은 확인(`:75`, `:89`) | 개선 | 확인 후 기록 |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
