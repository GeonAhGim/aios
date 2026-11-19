# J1-MfaSetupPage — `/onboarding/mfa-setup`

> 스켈레톤(자동 생성). 사실 시트: `docs/frontend/facts/MfaSetupPage.facts.md`. `TODO`가 남은 블록은 아직 채우지 않은 것이다. 규칙은 `docs/frontend/README.md` 참고.

## 1. 개요

- 화면 / 라우트 / 소스: `/onboarding/mfa-setup` / `frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx`
- 여정 소속: J1


### 1. 개요 보충 (페르소나·목적·진입 조건·준비물)

<!-- BLOCK:J1-MfaSetupPage:p1 -->
| 항목 | 내용 |
|---|---|
| 주 페르소나(P1~P11) | P1 (개인 트레이더/투자자) — 온보딩 여정 J1의 첫 단계. [확인] `PERSONAS_AND_JOURNEYS.md` §3: J1 시작·온보딩의 주 페르소나. |
| 목적(한 문장) | MFA(2단계 인증)를 설정하여 계정 보안을 강화한다. [확인] 화면 문구 `legacy.mfaSetupPage.title1` "2단계 인증 설정 (필수)" (:80). |
| 진입 조건 | `frontend/apps/web/src/router.tsx:87` — `/onboarding/mfa-setup` 라우트는 `protect(<MfaSetupPage />)`로 감김. `frontend/apps/web/src/components/ProtectedRoute.tsx:32-33` — `me.mfaEnabled === false`일 때 `/onboarding/mfa-setup`로 리다이렉트. 즉 MFA 미설정 상태의 인증된 사용자만 진입 가능. [확인] |
| 사용자 준비물 | QR 코드를 스캔할 인증 앱(Google Authenticator 등). [확인] `MfaSetupPage.tsx:81` subtitle "Google Authenticator 등 인증 앱으로 QR코드를 스캔해주세요". |
<!-- /BLOCK -->

### 2. 데이터와 계약

<!-- BLOCK:J1-MfaSetupPage:data -->
| 데이터(훅) | 출처 | 갱신 방식 | 실패 시 |
|---|---|---|---|
| `useSetupMfa` | `frontend/packages/shared-hooks/src/useAuth.ts:66` | 변경 요청(mutation), 화면 마운트 시 1회 호출 (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:51-65` useEffect에서 setupRequested ref 가드와 함께 setupMfa.mutate 1회 호출) | onError(59-61줄): ApiError면 그대로, 아니면 t("legacy.mfaSetupPage.t7")로 감싸 setSetupError 저장. 저장된 오류는 MfaError(86줄)가 렌더링 — classifyBadRequest(shared-types/src/badRequest.ts:34) → BadRequestNotice, classifyForbidden(shared-types/src/forbidden.ts:31) → ForbiddenNotice, 그 외 ErrorMessage(MfaSetupPage.tsx:25). **403 AUTH_MFA_REQUIRED는 http.ts:93에서 handleRequestFailure가 가로채 handleMfaStepUp(137-141줄)으로 step-up을 시도하므로, onError에 도달하기 전에 자동 step-up이 동작함.** |
| `useVerifyMfa` | `frontend/packages/shared-hooks/src/useAuth.ts:70` | 사용자 제출 시 변경 요청, 성공하면 ["me"] 무효화 (onSuccess에서 qc.invalidateQueries({ queryKey: ["me"] }) 호출, useAuth.ts:74) | handleSubmit(67-76줄) catch: verifyMfa.mutateAsync(totpCode) 실패 시 ApiError면 그대로, 아니면 t("legacy.mfaSetupPage.t8")로 감싸 setVerifyError 저장. 저장된 오류는 MfaError(108줄)가 동일 분기 렌더링. **403 AUTH_MFA_REQUIRED는 http.ts:93에서 동일하게 가로채 step-up 시도.** |
<!-- /BLOCK -->

### 3-a. 상태 매트릭스: 로딩·빈·정상·낡은 데이터

<!-- BLOCK:J1-MfaSetupPage:st-a -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 로딩 | `qrDataUrl === null`인 동안(초기 마운트, `MfaSetupPage.tsx:40`) | QR 이미지 대신 회색 영역에 `legacy.mfaSetupPage.t3` "QR코드 생성 중..." 문구(:90-91). 제출 버튼 `disabled={!qrDataUrl}`(:109)로 비활성. `loading={verifyMfa.isPending}`(:109) — 설정 발급 중이 아니라 제출 버튼의 로딩 상태. | 없음(제출 버튼 비활성). 서버는 useEffect(:51-65)에서 `setupRequested` ref 가드와 함께 `setupMfa.mutate` 1회 호출. | `구현됨` — 로딩 문구(:87-92), 버튼 비활성(:109). 다만 로딩 표시의 시각 형태(회색 영역, h-40 div)는 ui-web 확인 전이므로 '미확인'. 제출 중 로딩(`loading={verifyMfa.isPending}`)은 `구현됨`. |
| 로딩(제출 중) | `verifyMfa.isPending === true`(:109) | 버튼에 로딩 인디케이터 표시. | 없음(버튼 disabled 대체로 로딩 상태). | `구현됨`(:109). |
| 빈 상태 | 목록형 데이터가 없는 화면이므로 빈 상태 전용 UI 없음. `MfaSetupPage.tsx:87-92`에 빈 상태 분岐가 없음. `onSuccess`의 `QRCode.toDataURL`(:57)이 실패하면 try/catch 없으므로 `qrDataUrl`이 null로 남고 "QR코드 생성 중..."이 계속 보임 — 해석: 이 경로는 코드로 읽은 해석. | — | — | 없음. (해석: `QRCode.toDataURL` 실패 시 예외가 잡히지 않아 "QR코드 생성 중..."이 영구 표시됨. `미확인(코드 추론)`.) |
| 정상 | `qrDataUrl`과 `secret`이 모두 설정된 상태(`MfaSetupPage.tsx:40-41`). `setupMfa.mutate` onSuccess(:55-58)에서 `setSecret`(:56)과 `setQrDataUrl`(:57) 호출 후 진입. | QR 이미지 `alt` = `legacy.mfaSetupPage.alt2` "MFA QR 코드"(:88). 수동 입력 코드 영역: `legacy.mfaSetupPage.t4` "수동 입력용 코드: {{secret}}"(:95). 입력 필드 placeholder: `legacy.mfaSetupPage.placeholder5` "6자리 코드"(:103). 제출 버튼: `legacy.mfaSetupPage.t6` "인증 완료"(:110). | 6자리 코드 입력 후 제출, QR 스캔 또는 수동 코드 입력. | `구현됨` — QR 이미지(:88), 수동 코드 표시(:95), 입력 필드(:103), 버튼(:110). |
| 정상(제출 성공) | `verifyMfa.mutateAsync(totpCode)`(:71) 성공 후 | `/onboarding/risk-assessment`로 이동(:72). `["me"]` 무효화(`useAuth.ts:74`). | — | `구현됨`(:72, `useAuth.ts:74`). |
| 낡은 데이터(신선도 초과) | `useSetupMfa`는 `useMutation`이며 staleTime·refetch 설정이 없다(`useAuth.ts:66-68`). shared-hooks에서 `staleTime`은 이 화면 외에 `SweepResultsPage.tsx:225`에만 `Infinity`로 설정됨. QR과 secret은 컴포넌트 state(`MfaSetupPage.tsx:40-41`)에만 보관되고 재조회 경로가 없음. 주석상 setup 재호출은 서버 secret을 덮어쓴다(:46-48). | 다른 탭·세션에서 재발급하면 화면의 QR이 낡음. 그러나 재조회 경로가 없어 사용자가 이를 인지할 수 없음. | 재발급 요청 버튼 없음. 페이지 새로고침 시 useEffect가 다시 동작하지만 React StrictMode(dev)에서는 `setupRequested` 가드가 있더라도 두 번 실행될 수 있음(:46-48 주석). | 신선도 로직 `없음`(`useAuth.ts:66-68`). 다른 탭·세션에서 재발급 시 화면의 QR이 낡을 수 있으나 실측하지 않았으므로 `미확인`. |
<!-- /BLOCK -->

### 3-b. 상태 매트릭스: 입력 오류·401·403·409

<!-- BLOCK:J1-MfaSetupPage:st-b -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 입력 오류(400/검증) | TODO | TODO | TODO | TODO |
| 인증 필요/세션 만료(401) | TODO | TODO | TODO | TODO |
| 권한 없음/MFA 필요(403) | TODO | TODO | TODO | TODO |
| 충돌/상태 불일치(409) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 3-c. 상태 매트릭스: 잠김/속도 제한·서버 오류·네트워크·부분 실패

<!-- BLOCK:J1-MfaSetupPage:st-c -->
| 상태 | 조건 | 사용자가 보는 것 | 가능한 행동 | 구현 여부 |
|---|---|---|---|---|
| 잠김/속도 제한(423/429) | TODO | TODO | TODO | TODO |
| 서버 오류(5xx) | TODO | TODO | TODO | TODO |
| 네트워크 단절 | TODO | TODO | TODO | TODO |
| 부분 실패(일부 데이터만 실패) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 4. 사용자 행동과 전이

<!-- BLOCK:J1-MfaSetupPage:act -->
| 행동(요소) | 전제 | 결과(성공) | 결과(실패) | 다음 화면 |
|---|---|---|---|---|
| `<Button>` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:109`) | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 5. 금전·위험 행동 규칙

<!-- BLOCK:J1-MfaSetupPage:money -->
| 항목 | 내용 |
|---|---|
| 해당 여부와 이유(없으면 '해당 없음' + 이유) | TODO |
<!-- /BLOCK -->

### 6. 오류 문구와 복구 안내

<!-- BLOCK:J1-MfaSetupPage:err -->
| 오류 범주 | 사용자 문구(i18n 키) | 다음 행동 안내 | trace_id 노출 |
|---|---|---|---|
| `classifyBadRequest` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:21`) | TODO | TODO | TODO |
| `BadRequestNotice` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:21`) | TODO | TODO | TODO |
| `classifyForbidden` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:22`) | TODO | TODO | TODO |
| `ForbiddenNotice` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:22`) | TODO | TODO | TODO |
| `routeApiError` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:23`) | TODO | TODO | TODO |
| `ErrorMessage` (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:25`) | TODO | TODO | TODO |
<!-- /BLOCK -->

### 7·8·9·10. 접근성·i18n·성능·관측

<!-- BLOCK:J1-MfaSetupPage:quality -->
| 영역 | 확인한 사실 | 갭 |
|---|---|---|
| 접근성(WCAG 2.2 AA) | TODO | TODO |
| i18n/지역화 | TODO | TODO |
| 성능 예산 | TODO | TODO |
| 텔레메트리 | TODO | TODO |
<!-- /BLOCK -->

### 11. 수용 기준

<!-- BLOCK:J1-MfaSetupPage:accept -->
| # | 수용 기준(테스트로 검증 가능한 문장) | 검증 수단 |
|---|---|---|
| A1 | TODO | TODO |
| A2 | TODO | TODO |
| A3 | TODO | TODO |
<!-- /BLOCK -->

### 12. 구현 대비 갭 표

<!-- BLOCK:J1-MfaSetupPage:gap -->
| # | 명세(기대) | 현재 구현(근거) | 심각도 | 처리 |
|---|---|---|---|---|
| G1 | TODO | TODO | TODO | TODO |
| G2 | TODO | TODO | TODO | TODO |
| G3 | TODO | TODO | TODO | TODO |
<!-- /BLOCK -->

### 13. 미규정 액션 후보

- 없음(발견 시 UNSPECIFIED_ACTIONS.md에 기록하고 링크)
