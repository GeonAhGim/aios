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
| `useFieldErrors` | `frontend/apps/web/src/hooks/useFieldErrors.ts:15` | 로컬 상태(폴링 없음). `setFromError(err)` 호출 시 `extractFieldErrors(err)`(`shared-types/fieldErrors.ts:46`)가 `details.fields[]`를 파싱해 `fieldErrors: Record<string,string>`에 세팅. 각 필드 입력의 `onChange`에서 `clearField(field)` 호출 시 해당 키만 삭제(`useFieldErrors.ts:22-28`). 1회 세팅/지움 방식. | `extractFieldErrors`는 VALIDATION_ 접두사 코드 + 배열이 아닌 경우 빈 객체를 반환(`fieldErrors.ts:47-55`), throw하지 않음. 세팅된 `fieldErrors`는 각 `<Field error={...}>` props(`SignupPage.tsx:75,89`)로 전달되어 인라인 오류 문구로 표시됨. |
| `useSignup` | `frontend/packages/shared-hooks/src/useAuth.ts:20` | `useMutation` 기반. `signup.mutateAsync({email, password})` 호출 시 `apiClient.register(body)`(`useAuth.ts:24`)로 POST `/auth/register` 요청. `onSuccess` 시 `setToken(data.accessToken)` 후 `qc.invalidateQueries({queryKey:["me"]})`(`useAuth.ts:26-27`). 폴링/반복 없음( mutation). | `mutateAsync` reject → `handleSubmit` catch(`SignupPage.tsx:65-69`)에서 `setError(err)`, `setFromError(err)`, `setLockoutRemainingSec(deriveLockout(err).retryAfterSec)`. `SignupError`(`SignupPage.tsx:25-38`)가 `classifyBadRequest`→`BadRequestNotice`, `classifyForbidden`→`ForbiddenNotice`, 그 외→`ErrorMessage`로 라우팅(`SignupPage.tsx:26-36`). 423 `AUTH_ACCOUNT_LOCKED`은 `deriveLockout`(`shared-types/accountLockout.ts:41`)이 `retryAfterSec`를 읽어서 1초 타이머로 카운트다운(`SignupPage.tsx:51-55`). |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-SignupPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | TODO | TODO | TODO | TODO |
| 빈 상태 | TODO | TODO | TODO | TODO |
| 정상 | TODO | TODO | TODO | TODO |
| 낡은 데이터(신선도 초과) | TODO | TODO | TODO | TODO |
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
