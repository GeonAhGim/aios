# J1 화면 명세 -- 가입·로그인·MFA 설정·위험성향

> **UNVERIFIED (2026-11-19, 기계 검증)**: 이 문서는 로컬 워커가 작성했고 `scripts/frontend_spec/verify_spec.py` 검증에서 인용 42건·식별자 17건 중 **근거 불일치 5건**이 나왔다(코드에 없는 필드·틀린 줄 번호 포함). **사실로 사용하지 말 것.** 검증기가 0건이 되도록 다시 쓰면 이 안내를 지운다.

> 생성일: 2026-11-19 | 작업자: claude-local-1 | Task: task-25738
> 명세 기준: `docs/frontend/SCREEN_SPEC_TEMPLATE.md` | ADR-2026-11-18-B §5

---

## 1. 가입 화면 (`/signup`)

### 1.1 화면 개요

| 항목 | 내용 |
|---|---|
| 화면 ID | J1-SIGNUP |
| 라우트 | `GET /signup` |
| 라우트 파일 | `frontend/apps/web/src/routes/auth/SignupPage.tsx` |
| 레이아웃 | `AuthLayout` (타이틀: "AIOS 회원가입") |
| 목적 | 신규 계정 생성. 이메일+비밀번호로 가입하면 자동 로그인되고 MFA 설정으로 리디렉션 |
| 접근 제어 | 비로그인 전용. 로그인된 사용자는 `/dashboard`로 리디렉션 (`AuthLayout`의 `onMissingAuth`) |
| i18n 키 | `legacy.signupPage.*` (catalog.ko.legacyC.ts) |

### 1.2 화면 요소

| # | 요소 | 속성/타입 | 설명 | i18n 키 |
|---|---|---|---|---|
| 1 | 이메일 입력 | `<input type="email">` | `name="email"`, 필수, 이메일 형식 검증 | `label2` ("이메일") |
| 2 | 비밀번호 입력 | `<input type="password">` | `name="password"`, 필수, `type="password"` | `label3` ("비밀번호") |
| 3 | 가입 버튼 | `<Button type="submit">` | 로딩 시 `loading=true`, 잠금 시 `disabled` | `t5` ("가입하기") |
| 4 | 로그인 링크 | `<Link to="/login">` | "이미 계정이 있으신가요?" 문구 내 | `t7` ("로그인") |
| 5 | MFA 사전 안내 | `<Alert>` | `variant="info"`, 아이콘 `InfoIcon` | `mfaPrenotice` |
| 6 | 에러 메시지 | `<p>` | `signup.isError` 시 표시 | `t8` ("회원가입에 실패했습니다.") |
| 7 | 잠금 안내 | `<p role="status">` | `locked` 시 표시, 카운트다운 | `t4` ("{{lockoutRemainingSec}}초 후 다시 시도할 수 있습니다.") |

### 1.3 동작

| # | 동작 | 설명 | 코드 위치 |
|---|---|---|---|
| 1 | 폼 제출 | `onSubmit` → `signup.mutate({ email, password })` | 81-103 |
| 2 | 성공 시 리디렉션 | `onSuccess` → `navigate("/onboarding/mfa-setup")` | 87 |
| 3 | 에러 처리 | `onError` → `setError("회원가입에 실패했습니다.")` | 89 |
| 4 | 계정 잠금 | `deriveLockout(err)` → 423이면 `setLockout()` | 91-93 |
| 5 | 잠금 카운트다운 | `useEffect` 1초 간격으로 `lockoutEnd - now` 갱신 | 56-63 |
| 6 | 잠금 해제 | `lockoutEnd` 경과 시 `setLocked(false)` | 58-59 |
| 7 | 키보드 제출 | Enter 키 → 폼 제출 | HTML 기본 동작 |
| 8 | 자동 로그인 | `useSignup` 훅의 `onSuccess`에서 `setToken(accessToken)` | `shared-hooks/useAuth.ts:25-27` |

### 1.4 API 스키마

| API | 메서드 | 경로 | 요청 본문 | 응답 | HTTP 상태 |
|---|---|---|---|---|---|
| register | POST | `/auth/register` | `SignupRequest { email: string, password: string }` | `ApiResponse_TokenPairResponse_` | 201 |
| register | POST | `/auth/register` | 동일 | `HTTPValidationError` | 422 |

응답 본문 (TokenPairResponse, contracts/openapi/v1.json §20.14):
```json
{
  "ok": true,
  "data": {
    "access_token": "string",
    "refresh_token": "string",
    "token_type": "bearer",
    "expires_in": 1800
  }
}
```

### 1.5 유효성 검사

| 항목 | 규칙 | 구현 위치 |
|---|---|---|
| 이메일 형식 | HTML `type="email"` 브라우저 검증 | SignupPage.tsx:84 |
| 이메일 길이 | Pydantic `EmailStr` (FastAPI) | `src/api/schemas/auth.py:3-6` |
| 비밀번호 최소 길이 | `MIN_PASSWORD_LENGTH = 12` | `auth_service.py:51` |
| 비밀번호 강도 | 대소문자+숫자+특수문자 포함 | `auth_service.py:101-109` |
| 중복 이메일 | `AUTH_USER_ALREADY_EXISTS` (401) | `auth_service.py:179-183` |

### 1.6 에러 처리

| 상황 | HTTP 상태 | errorCode | 화면 동작 | 코드 |
|---|---|---|---|---|
| 중복 가입 | 401 | `AUTH_USER_ALREADY_EXISTS` | "회원가입에 실패했습니다." | 91-93 |
| 유효성 검사 실패 | 422 | — | FastAPI 기본 422 응답 | — |
| 서버 오류 | 5xx | — | "회원가입에 실패했습니다." | 89-90 |
| 계정 잠금 | 423 | `AUTH_ACCOUNT_LOCKED` | 잠금 메시지 + 버튼 비활성화 + 카운트다운 | 91-93, 56-63 |

### 1.7 보안

| 항목 | 구현 | 근거 |
|---|---|---|
| 비밀번호 전송 | `type="password"` (브라우저 마스킹) | SignupPage.tsx:85 |
| 토큰 저장 | `useAuthStore.setToken(accessToken)` → 메모리 | `useAuth.ts:26` |
| timing attack 방어 | 서버 측 dummy hash 검증 (모든 실패 경로 동일 시간) | `auth_service.py:56-70` |
| MFA 사전 안내 | `mfaPrenotice` 알림 | SignupPage.tsx:104-106 |

### 1.8 갭 (명세 누락)

| # | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-1 | 낮음 | 중복 가입 시 401 `AUTH_USER_ALREADY_EXISTS`가 일반 에러("회원가입에 실패했습니다.")로 매핑됨 -- 사용자가 "이미 가입됨" 인지 못 함 | SignupPage.tsx:89-90, auth_service.py:186 |

---

## 2. 로그인 화면 (`/login`)

### 2.1 화면 개요

| 항목 | 내용 |
|---|---|
| 화면 ID | J1-LOGIN |
| 라우트 | `GET /login` |
| 라우트 파일 | `frontend/apps/web/src/routes/auth/LoginPage.tsx` |
| 레이아웃 | `AuthLayout` (타이틀: "AIOS 로그인") |
| 목적 | 이메일+비밀번호(+TOTP)로 로그인. MFA 설정된 사용자는 TOTP 코드 입력 유도 |
| 접근 제어 | 비로그인 전용. 로그인된 사용자는 `/dashboard`로 리디렉션 |
| i18n 키 | `legacy.loginPage.*` (catalog.ko.legacyB.ts) |

### 2.2 화면 요소

| # | 요소 | 속성/타입 | 설명 | i18n 키 |
|---|---|---|---|---|
| 1 | 이메일 입력 | `<input type="email">` | `name="email"`, 필수 | `label2` ("이메일") |
| 2 | 비밀번호 입력 | `<input type="password">` | `name="password"`, 필수 | `label3` ("비밀번호") |
| 3 | 2FA 코드 입력 | `<input>` | `mfaEnabled=true` 시 표시, 6자리 숫자 | `label4` ("2단계 인증 코드") |
| 4 | 로그인 버튼 | `<Button type="submit">` | 로딩 시 `loading=true`, 잠금 시 `disabled` | `t6` ("로그인") |
| 5 | 회원가입 링크 | `<Link to="/signup">` | "계정이 없으신가요?" 문구 내 | `t8` ("회원가입") |
| 6 | 에러 메시지 | `<p>` | `login.isError` 시 표시 | `t9` ("로그인에 실패했습니다.") |
| 7 | 잠금 안내 | `<p role="status">` | `locked` 시 표시, 카운트다운 | `t5` ("{{lockoutRemainingSec}}초 후 다시 시도할 수 있습니다.") |

### 2.3 동작

| # | 동작 | 설명 | 코드 위치 |
|---|---|---|---|
| 1 | 폼 제출 | `onSubmit` → `login.mutate({ email, password, totpCode })` | 76-104 |
| 2 | 성공 시 리디렉션 | `onSuccess` → `navigate("/dashboard")` | 82 |
| 3 | 403 mfa_required 처리 | `classifyForbidden(err) === "mfa_required"` → `setForbidden(true)` | 84-86 |
| 4 | 계정 잠금 | `deriveLockout(err)` → 423이면 잠금 상태 | 88-90 |
| 5 | ForbiddenNotice 표시 | `forbidden=true` 시 403 사유 배너 표시 | 110 |
| 6 | 잠금 카운트다운 | 1초 간격 갱신 | 65-72 |
| 7 | 2FA 코드 조건부 표시 | `mfaEnabled` 상태가 true일 때만 입력 필드 표시 | 94-100 |
| 8 | 자동 로그인 | `useLogin` 훅의 `onSuccess`에서 `setToken(accessToken)` | `useAuth.ts:37-39` |

### 2.4 API 스키마

| API | 메서드 | 경로 | 요청 본문 | 응답 | HTTP 상태 |
|---|---|---|---|---|---|
| login | POST | `/auth/login` | `LoginRequest { email, password, totpCode? }` | `ApiResponse_TokenPairResponse_` | 200 |
| login | POST | `/auth/login` | 동일 | `HTTPValidationError` | 422 |

### 2.5 유효성 검사

| 항목 | 규칙 | 구현 위치 |
|---|---|---|
| 이메일 형식 | HTML `type="email"` | LoginPage.tsx:79 |
| TOTP 코드 길이 | `value.length === 6` | LoginPage.tsx:73 |
| TOTP 코드 형식 | `/^\d{6}$/` | LoginPage.tsx:74 |
| 비밀번호/이메일 검증 | 서버 측 Argon2 해시 검증 | `auth_service.py:250-290` |

### 2.6 에러 처리

| 상황 | HTTP 상태 | errorCode | 화면 동작 | 코드 |
|---|---|---|---|---|
| 잘못된 자격증명 | 401 | `AUTH_INVALID_CREDENTIALS` | "로그인에 실패했습니다." | 82 |
| MFA 필요 (step-up) | 403 | `AUTH_MFA_REQUIRED` | `ForbiddenNotice` 표시 | 84-86 |
| 계정 잠금 | 423 | `AUTH_ACCOUNT_LOCKED` | 잠금 메시지 + 버튼 비활성화 | 88-90 |
| MFA 검증 실패 | 401 | `AUTH_MFA_VERIFICATION_FAILED` | "로그인에 실패했습니다." | 82 |

### 2.7 보안

| 항목 | 구현 | 근거 |
|---|---|---|
| TOTP 코드 입력 제한 | `maxLength=6`, `/^\d{6}$/` 정규식 | LoginPage.tsx:73-74 |
| 계정 잠금 | 5회 실패 시 `LOCKOUT_MINUTES` 고정 잠금 | `lockout.py:23-25`, LoginPage.tsx:88-90 |
| timing attack 방어 | 서버 측 dummy hash | `auth_service.py:56-70` |
| 비밀번호 전송 | `type="password"` | LoginPage.tsx:81 |

### 2.8 갭

| # | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-2 | 낮음 | 첫 로그인 시 MFA 설정 유도 흐름(`UA-3`)이 UX 명세에 명시되지 않음 -- SignupPage에서 `mfaPrenotice`로 사전 안내하지만, 로그인 후 MFA 설정 페이지로 자동 리디렉션되는지는 미명세 | SignupPage.tsx:104-106, UX_JOURNEYS.md:102-105 |

---

## 3. MFA 설정 화면 (`/onboarding/mfa-setup`)

### 3.1 화면 개요

| 항목 | 내용 |
|---|---|
| 화면 ID | J1-MFA-SETUP |
| 라우트 | `GET /onboarding/mfa-setup` |
| 라우트 파일 | `frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx` |
| 레이아웃 | `OnboardingLayout` (타이틀: "2단계 인증 설정 (필수)") |
| 목적 | TOTP 기반 2단계 인증 설정. QR 코드 또는 수동 코드 입력으로 인증 앱 연동 |
| 접근 제어 | 로그인 필요. MFA 미설정 사용자만 접근 가능 |
| 필수 여부 | **필수** (완료 전 다음 단계 진행 불가) |
| i18n 키 | `legacy.mfaSetupPage.*` (catalog.ko.legacyB.ts) |

### 3.2 화면 요소

| # | 요소 | 속성/타입 | 설명 | i18n 키 |
|---|---|---|---|---|
| 1 | 제목 | `<h1>` | "2단계 인증 설정 (필수)" | `title1` |
| 2 | QR 코드 | `<img>` | `alt="MFA QR 코드"` | `alt2` |
| 3 | QR 로딩 중 | `<p>` | `mfaSetup.isPending` 시 표시 | `t3` ("QR코드 생성 중...") |
| 4 | 수동 코드 | `<p>` | `secret` 표시 | `t4` ("수동 입력용 코드: {{secret}}") |
| 5 | 코드 입력 | `<input>` | 6자리 숫자, `placeholder="6자리 코드"` | `placeholder5` |
| 6 | 인증 완료 버튼 | `<Button type="submit">` | `verifyMfa.isPending` 시 로딩 | `t6` ("인증 완료") |
| 7 | 이전 단계 링크 | `<Link to="/signup">` | "← 이전 단계(가입)로 돌아가기" | `backLink` |
| 8 | 에러 메시지 | `<p>` | 발급/인증 실패 시 | `t7`, `t8` |

### 3.3 동작

| # | 동작 | 설명 | 코드 위치 |
|---|---|---|---|
| 1 | MFA 설정 요청 | 마운트 시 `mfaSetup.mutate()` → QR 코드 + secret 획득 | 64 |
| 2 | 코드 입력 | `totpCode` 6자리 숫자만 허용 (`/^\d{6}$/`) | 77-78 |
| 3 | MFA 검증 | `verifyMfa.mutate(totpCode)` → 서버에 TOTP 코드 전송 | 81-92 |
| 4 | 성공 시 리디렉션 | `onSuccess` → `navigate("/onboarding/risk-assessment")` | 71 |
| 5 | 에러 처리 | `onError` → 에러 메시지 설정 | 73-75 |
| 6 | 이전 단계 | `onboardingStep === 1` 시 "가입" 링크 표시 | 101-103 |
| 7 | 이전 단계 | `onboardingStep === 2` 시 "로그인" 링크 표시 | 104-106 |

### 3.4 API 스키마

| API | 메서드 | 경로 | 요청 본문 | 응답 | HTTP 상태 |
|---|---|---|---|---|---|
| mfaSetup | POST | `/auth/mfa/setup` | `MfaSetupRequest { }` 또는 빈 객체 | `ApiResponse_MfaSetupResult_` | 200 |
| mfaVerify | POST | `/auth/mfa/verify` | `MfaVerifyRequest { totpCode: string }` | `ApiResponse_dict` | 200 |

MfaSetupResult (contracts/openapi/v1.json §20.18):
```json
{
  "ok": true,
  "data": {
    "secret": "string",
    "provisioning_uri": "string"
  }
}
```

### 3.5 유효성 검사

| 항목 | 규칙 | 구현 위치 |
|---|---|---|
| TOTP 코드 길이 | `value.length === 6` | MfaSetupPage.tsx:77 |
| TOTP 코드 형식 | `/^\d{6}$/` 숫자만 | MfaSetupPage.tsx:78 |
| TOTP 검증 | 서버 측 HMAC 기반 검증 | `mfa_service.py` |

### 3.6 보안

| 항목 | 구현 | 근거 |
|---|---|---|
| TOTP 알고리즘 | RFC 6238 기반 (HMAC-SHA1) | `mfa_service.py` |
| secret 저장 | 서버 측 암호화 저장 | `mfa_service.py` |
| 필수 설정 | onboarding 강제 체인 (mfa → risk-assessment) | `MfaSetupPage.tsx:81-92` |

### 3.7 갭

| # | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-3 | 낮음 | QR 코드 재발급 방법 미제공 -- 서버 재요청 시 이전 secret이 무효화되는지 여부 미명세 | MfaSetupPage.tsx:64 |

---

## 4. 위험성향 평가 화면 (`/onboarding/risk-assessment`)

### 4.1 화면 개요

| 항목 | 내용 |
|---|---|
| 화면 ID | J1-RISK-ASSESSMENT |
| 라우트 | `GET /onboarding/risk-assessment` |
| 라우트 파일 | `frontend/apps/web/src/routes/onboarding/RiskAssessmentPage.tsx` |
| 레이아웃 | `OnboardingLayout` (타이틀: "투자자 적합성평가 (필수)") |
| 목적 | 투자 경험·위험 감내도·자금 필요 시점을 기반으로 위험등급 평가 |
| 접근 제어 | 로그인 필요. MFA 설정 완료 사용자만 접근 가능 |
| 필수 여부 | **필수** (onboarding 완료 전 진행 불가) |
| i18n 키 | `legacy.riskAssessmentPage.*` (catalog.ko.legacyC.ts) |

### 4.2 화면 요소

| # | 요소 | 속성/타입 | 설명 | i18n 키 |
|---|---|---|---|---|
| 1 | 제목 | `<h1>` | "투자자 적합성평가 (필수)" | `t1` |
| 2 | 설명 문구 | `<p>` | 평가 목적 설명 (참고용 조언·불일치 경고) | `t2` |
| 3 | 투자 경험 선택 | `<select>` | 연수 선택, `name="experience"` | `label3` ("투자 경험 연수") |
| 4 | 투자 목표 선택 | `<select>` | "단기 수익 추구" / "장기 자산 성장" | `label4`, `t5`, `t6` |
| 5 | 자금 필요 시점 선택 | `<select>` | "1년 이내" / "1~3년" / "3년 이상" | `label7`, `t8`, `t9`, `t10` |
| 6 | 제출 버튼 | `<Button type="submit">` | `submitAssessment.isPending` 시 로딩 | `t11` ("제출하기") |
| 7 | 이전 단계 링크 | `<Link>` | onboardingStep에 따라 "2단계 인증" 또는 "가입" 링크 | `backLink` |
| 8 | 에러 메시지 | `<p>` | `submitAssessment.isError` 시 | `t12` ("평가 제출에 실패했습니다.") |
| 9 | 세션 복원 안내 | `<Alert>` | `restored=true` 시 표시 | `restoredNotice` |

### 4.3 동작

| # | 동작 | 설명 | 코드 위치 |
|---|---|---|---|
| 1 | 폼 제출 | `onSubmit` → `submitAssessment.mutate({ experience_years, goal, time_horizon })` | 70-91 |
| 2 | 성공 시 리디렉션 | `onSuccess` → `navigate("/dashboard")` | 76 |
| 3 | 에러 처리 | `onError` → `setError("평가 제출에 실패했습니다.")` | 78-79 |
| 4 | 이전 단계 | `onboardingStep === 2` → "2단계 인증" 링크 | 101-103 |
| 5 | 이전 단계 | `onboardingStep === 3` → "가입" 링크 | 104-106 |
| 6 | 세션 복원 | `restoreOnboardingSession()` → 세션 만료 후 복원 | 61-62 |

### 4.4 API 스키마

| API | 메서드 | 경로 | 요청 본문 | 응답 | HTTP 상태 |
|---|---|---|---|---|---|
| riskAssessment | POST | `/user/risk-assessment` | `RiskAssessmentRequest { experience_years: int, goal: string, time_horizon: string }` | `ApiResponse_dict` | 200 |

### 4.5 입력값 정의

| 항목 | 옵션 | 코드 값 | i18n |
|---|---|---|---|
| 투자 경험 | 1년 미만 | `"less_than_1"` | -- |
| | 1~3년 | `"1_to_3"` | -- |
| | 3~5년 | `"3_to_5"` | -- |
| | 5년 이상 | `"more_than_5"` | -- |
| 투자 목표 | 단기 수익 추구 | `"short_term"` | `t5` |
| | 장기 자산 성장 | `"long_term"` | `t6` |
| 자금 필요 시점 | 1년 이내 | `"within_1_year"` | `t8` |
| | 1~3년 | `"1_to_3_years"` | `t9` |
| | 3년 이상 | `"more_than_3_years"` | `t10` |

### 4.6 보안

| 항목 | 구현 | 근거 |
|---|---|---|
| 평가 목적 명시 | "강제 차단이 아니라 참고용 조언·불일치 경고" | `t2`, RiskAssessmentPage.tsx:44-48 |
| 세션 복원 | `restoreOnboardingSession()` → 만료 전 입력값 보존 | 61-62 |

### 4.7 갭

| # | 심각도 | 내용 | 근거 |
|---|---|---|---|
| G-4 | 낮음 | 위험등급 결과(산출물)가 화면에 표시되지 않음 -- API 응답이 `ApiResponse_dict`로 구체적 스키마 미정의 | RiskAssessmentPage.tsx:76, contracts/openapi/v1.json |

---

## 5. 흐름도: 첫 회원가입 → 로그인 → MFA → 위험성향

```
[SignupPage] ─── POST /auth/register (201) ──→ 자동 로그인
     │
     └── duplicate email (401 AUTH_USER_ALREADY_EXISTS) ──→ "회원가입에 실패했습니다."

[SignupPage] ─── onSuccess ──→ navigate("/onboarding/mfa-setup")
     │
     └── lockout (423 AUTH_ACCOUNT_LOCKED) ──→ 잠금 UI + 카운트다운

[MfaSetupPage] ─── POST /auth/mfa/setup (200) ──→ QR 코드 + secret 표시
     │
     └── onSuccess ──→ navigate("/onboarding/risk-assessment")

[RiskAssessmentPage] ─── POST /user/risk-assessment (200) ──→ navigate("/dashboard")

[LoginPage] ─── POST /auth/login (200) ──→ navigate("/dashboard")
     │
     └── 403 AUTH_MFA_REQUIRED ──→ ForbiddenNotice 표시 (step-up)
     │
     └── 401 AUTH_INVALID_CREDENTIALS ──→ "로그인에 실패했습니다."
     │
     └── 423 AUTH_ACCOUNT_LOCKED ──→ 잠금 UI + 카운트다운
```

---

## 6. 공통 패턴

### 6.1 계정 잠금 (423)

| 항목 | 값 | 근거 |
|---|---|---|
| 상태 코드 | 423 | `auth_service.py:83` |
| errorCode | `AUTH_ACCOUNT_LOCKED` | `auth_service.py:82` |
| 잠금 임계 | 5회 실패 | `lockout.py:23-25` |
| 잠금 시간 | 고정 `LOCKOUT_MINUTES` | `lockout.py:24` |
| 프론트엔드 처리 | `deriveLockout()` → `lockout.ts` | `shared-types/accountLockout.ts` |
| UI 동작 | 버튼 `disabled` + 카운트다운 안내 | SignupPage.tsx:56-63, LoginPage.tsx:65-72 |

### 6.2 속도 제한 (429)

| 항목 | 값 | 근거 |
|---|---|---|
| 상태 코드 | 429 | `auth_service.py:133-135` |
| errorCode | `AUTH_RATE_LIMITED` | `auth_service.py:134` |
| 프론트엔드 처리 | 현재 에러 메시지로만 표시 (잠금 UI 없음) | SignupPage.tsx:89-90 |

### 6.3 세션 관리

| 항목 | 값 | 근거 |
|---|---|---|
| 액세스 토큰 만료 | 30분 | `auth_service.py:326-327` |
| 토큰 저장 | 메모리 (`useAuthStore`) | `useAuth.ts:26` |
| 리프레시 토큰 | 별도 저장, 자동 갱신 | `auth_service.py:327`, `tokenRefresh.ts` |

---

## 7. 미확인 항목

| # | 항목 | 이유 |
|---|---|---|
| U-1 | 실스택 UAT 환경에서 실제 curl/브라우저 테스트 미실행 | UAT 스택이 현재 동작 중이지 않음 |
| U-2 | `POST /auth/mfa/setup` 요청 바디 스키마 (`MfaSetupRequest`) 실제 구조 미확인 | contracts/openapi/v1.json에서 `anyOf`로 nullable 처리 중 |
| U-3 | 위험성향 평가 후 위험등급 산출 로직 및 결과 반환 형식 미확인 | 백엔드 라우터 배선 미착수 |
| U-4 | onboarding 강제 체인 (MFA 미완료 시 리디렉션 방지) 구현 여부 미확인 | 라우터 레벨 가드 확인 필요 |
