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
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-SignupPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-SignupPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:111`) | TODO | TODO | TODO | TODO |
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
| `classifyBadRequest` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:26`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:27`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:28`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/auth/SignupPage.tsx:30`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-SignupPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-SignupPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-SignupPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
