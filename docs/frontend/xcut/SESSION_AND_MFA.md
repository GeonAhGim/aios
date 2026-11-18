# 세션·MFA·재인증·권한 — 횡단 규칙

> **범위**: 로그인 → 세션 수명 → MFA 설정/검증 → 권한 없음(403) → 계정 잠금 → 동시 로그인
> **최종 업데이트**: 2026-11-18
> **사실 근거**: 코드(파일:줄), API 스키마(`contracts/openapi/v1.json`), 실스택 관찰
> **추측 금지**: 확인 못 한 항목은 `미확인`으로 표시

---

## 1. 세션 수명·토큰 보관·리프레시·만료 시 동작

| 항목 | 규칙 | 근거 |
|---|---|---|
| 액세스 토큰 TTL | `ACCESS_TTL_MINUTES = 15` (900초) (`src/services/auth/tokens.py:27`) | 코드 |
| 리프레시 토큰 TTL | `REFRESH_TTL_DAYS = 14` (`src/services/auth/tokens.py:28`) | 코드 |
| 토큰 형식 | JWT HS256 (`src/services/auth/tokens.py:29`) | 코드 |
| 리프레시 토큰 DB 저장 | `sha256` 해시로 저장 (`src/services/auth/tokens.py:76-78`) | 코드 |
| 세션 DB 저장 | `auth_session` 테이블: `refresh_hash`, `expires_at`, `auth_level`, `ip_hash`, `ua_hash` (`src/services/auth/session_repository.py:50-56`) | 코드 |
| 리프레시 요청 필수 필드 | `refresh_token` + `session_id` (`contracts/openapi/v1.json#RefreshRequest`) | API 스키마 |
| 리프레시 시 토큰 회전 | 새 액세스+리프레시 토큰 쌍 발급, 구 리프레시 해지 (`src/services/auth/refresh.py:55-100`) | 코드 |
| 리프레시 재사용 탐지 | `expires_at > now` 조건에서 `refresh_hash` 비교 — 일치하면 재사용으로 간주, 세션 전체 폐기 (`src/services/auth/refresh.py:68-75`) | 코드 |
| 토큰 브라우저 보관 | `sessionStorage` (`frontend/packages/api-client/src/tokenStore.ts:64-68`) | 코드 |
| `sessionStorage` 선택 이유 | 탭/브라우저 종료 시 자동 삭제 → 세션 만료와 연동 | 코드 주석 |
| 리프레시 자동 재시도 | `http.ts` `ApiError` 401/403 → `tryRefresh()` 호출 (`frontend/packages/api-client/src/http.ts:133-154`) | 코드 |
| 리프레시 실패 시 | `logout()` 호출 후 `window.location.href = '/login'` (`frontend/packages/api-client/src/http.ts:166-170`) | 코드 |
| 리프레시 중 요청 큐잉 | `refreshing` 플래그 + `retries[]` 대기열 — 중복 리프레시 방지 (`frontend/packages/api-client/src/tokenRefresh.ts:42-83`) | 코드 |
| 다른 탭 토큰 동기화 | `sessionStorage`는 탭 격리 → 다른 탭과 토큰 공유 안 함. 로그아웃 시 서버에서 `session_id` 기반 세션 폐기만 수행 (`src/api/routers/auth.py:118-125`) | 코드 |

### 만료 시 동작 (사용자 경험)

| 상황 | 동작 | 근거 |
|---|---|---|
| 액세스 토큰 만료 (401) | `http.ts`가 `tryRefresh()` 자동 호출. 성공 시 원 요청 재시도. 실패 시 `/login` 리다이렉트 | `frontend/packages/api-client/src/http.ts:133-170` |
| 리프레시 토큰 만료 (`expires_at <= now`) | 서버가 `401 AUTH_TOKEN_EXPIRED` 반환 → `tryRefresh()` 실패 → 로그아웃 → `/login` | `src/services/auth/refresh.py:68-75`, `http.ts:166-170` |
| 리프레시 재사용 탐지 (토큰 도용) | 서버가 세션 전체 폐기 → 모든 탭/기기에서 로그아웃 | `src/services/auth/refresh.py:76-100` |
| 브라우저 종료 | `sessionStorage` 자동 삭제 → 다음 탭 열 때 로그인 필요 | `tokenStore.ts:64-68` |

---

## 2. MFA 흐름

### 2.1 회원가입 → MFA 설정 강제

```
[회원가입] → (201) → /onboarding/mfa-setup 페이지로 리다이렉트
                         ↓
                  TOTP secret + provisioning_uri 발급
                         ↓
                  사용자 "인증 앱에 코드 입력" 안내
                         ↓
                  코드 검증 실패 → 403 "MFA code verification failed"
                         ↓
                  코드 검증 성공 → mfa_enabled=true, mfa_verified_at 설정
                         ↓
                  (백엔드: 토큰 재발급 없이 로그인 흐름으로 이어짐)
```

| 항목 | 규칙 | 근거 |
|---|---|---|
| MFA 설정 강제 여부 | **강제 아님** — 회원가입 후 `/onboarding/mfa-setup`로 리다이렉트하지만, 라우트 보호 없음 (`frontend/apps/web/src/router.tsx:26-27`) | 코드 |
| MFA 설정 API | `POST /auth/mfa/setup` — password 필드 선택적 (초기 설정 시 생략 가능) (`contracts/openapi/v1.json#MfaSetupRequest`) | API 스키마 |
| MFA 설정 응답 | `secret` (base32 문자열) + `provisioning_uri` (otpauth:// URI) (`contracts/openapi/v1.json#MfaSetupResult`) | API 스키마 |
| MFA 검증 API | `POST /auth/mfa/verify` — `totp_code` 필수 (`contracts/openapi/v1.json#MfaVerifyRequest`) | API 스키마 |
| MFA 설정 페이지 UI | QR 코드 + 수동 입력 필드 + "인증 앱에 코드 입력" 안내 (`frontend/apps/web/src/routes/onboarding/MfaSetupPage.tsx:110-122`) | 코드 |
| MFA 설정 실패 시 | 403 반환 → `ForbiddenNotice` 표시 (`MfaSetupPage.tsx:73`) | 코드 |

### 2.2 로그인 시 MFA 2단계

| 항목 | 규칙 | 근거 |
|---|---|---|
| MFA 활성화 사용자 로그인 | `POST /auth/login`에 `totp_code` 필드 추가 (`src/api/routers/auth.py:34-35`) | 코드 |
| MFA 코드 검증 | `AuthService.authenticate()` 내부에서 검증 (`src/services/auth_service.py:82-94`) | 코드 |
| MFA 미설정 사용자 | `totp_code` 없이 로그인 → `auth_level="PASSWORD"` 토큰 발급 (`src/services/auth/login.py:41-42,70`) | 코드 |
| MFA 설정 사용자 | `totp_code` 필요 → 검증 성공 시 `auth_level="MFA_VERIFIED"` 토큰 발급 (`src/services/auth/login.py:41-42,70`) | 코드 |
| MFA 미입력 시 로그인 | `AuthService.authenticate()`가 `MFARequiredError` 발생 → 400 반환 (`src/services/auth_service.py:92-94`) | 코드 |
| MFA 코드 오류 | `MFACodeError` 발생 → 400 반환 (`src/services/auth_service.py:93`) | 코드 |

### 2.3 복구 방법

| 항목 | 규칙 | 근거 |
|---|---|---|
| MFA 복구 코드 | **없음** — 복구 코드 발급/검증 API 미존재 (`src/api/routers/auth.py` 전체 스캔) | 코드 |
| MFA 재설정 | `POST /auth/mfa/setup` — 기존 MFA 활성화 상태에서 password 확인 후 재설정 가능 (`src/api/routers/auth.py:150-177`) | 코드 |
| MFA 해제 | **API 미존재** — MFA 해제는 관리 기능일 뿐 사용자 자체 해제 API 없음 | 코드 |

---

## 3. 권한 없음 / MFA 필요 (403) 표시와 이동

| 항목 | 규칙 | 근거 |
|---|---|---|
| `ForbiddenNotice` 컴포넌트 | `frontend/apps/web/src/components/ForbiddenNotice.tsx` — "접근이 거부되었습니다" 메시지 | 코드 |
| `BadRequestNotice` 컴포넌트 | `frontend/apps/web/src/components/BadRequestNotice.tsx` — 입력 오류 메시지 | 코드 |
| 로그인 페이지 MFA 필요 시 | `MFARequiredError` → `BadRequestNotice` 표시 (`frontend/apps/web/src/routes/auth/LoginPage.tsx:111-112`) | 코드 |
| 로그인 페이지 MFA 코드 오류 | `MFACodeError` → `BadRequestNotice` 표시 (`LoginPage.tsx:111-112`) | 코드 |
| `classifyForbidden` 판정 | `error.code === "AUTH_MFA_REQUIRED"` 또는 `"AUTH_FORBIDDEN"` (`frontend/packages/shared-types/src/error.ts:24-25`) | 코드 |
| `ProtectedRoute` 403 처리 | `isForbidden(err)` → `<ForbiddenNotice>` 표시 후 `/login`으로 리다이렉트 (`frontend/apps/web/src/components/ProtectedRoute.tsx:61-65`) | 코드 |
| `ProtectedRoute` 401 처리 | `isUnauthorized(err)` → `handleUnauthorized()` → 리프레시 시도 → 실패 시 `/login` (`ProtectedRoute.tsx:54-60`) | 코드 |

---

## 4. 계정 잠금·속도 제한 표시

| 항목 | 규칙 | 근거 |
|---|---|---|
| 잠금 임계 | `max_login_attempts = 5` (`src/services/auth_service.py:26`) | 코드 |
| 잠금 시간 | `lockout_window_sec = 300` (5분) (`src/services/auth_service.py:26`) | 코드 |
| 잠금 동작 | `max_login_attempts` 실패 후 `lockout_until` 설정 — 기간 중 모든 시도를 동일 시간으로 응답 (`src/services/auth_service.py:44-77`) | 코드 |
| 프론트엔드 잠금 표시 | `deriveLockout(err)` → `retryAfterSec` 추출 → 카운트다운 표시 (`LoginPage.tsx:101-108`) | 코드 |
| 잠금 중 입력 비활성화 | `disabled={locked}` (`LoginPage.tsx:81,89,108`) | 코드 |
| signup 잠금 | 동일한 `deriveLockout` 로직 재사용 (`frontend/apps/web/src/routes/auth/SignupPage.tsx:68`) | 코드 |
| rate limit 헤더 | API 응답에 `Retry-After` 헤더 사용 명시 없음 — 오류 본문 `retryAfterSec` 필드로만 전달 | API 스키마 |
| MFA 코드 잠금 | MFA별 시도 제한 — 코드 없음 (`src/services/auth_service.py:82-94`) | 코드 |

---

## 5. 다른 탭·동시 로그인

| 항목 | 규칙 | 근거 |
|---|---|---|
| 세션 ID | UUID (`contracts/openapi/v1.json#TokenPairResponse.session_id`) | API 스키마 |
| 동시 로그인 허용 | **허용** — 동일 계정으로 여러 기기/탭에서 로그인 시 각각 고유 세션 생성 (`src/services/auth/login.py:62-71`) | 코드 |
| 세션 목록 조회 | `GET /auth/sessions` — 현재 세션 포함 활성 세션 목록 (`contracts/openapi/v1.json#sessions_endpoint`) | API 스키마 |
| 세션 단건 조회 | `GET /auth/sessions/{session_id}` — `user_agent`, `ip`, `last_seen_at` 등 (`contracts/openapi/v1.json#session_detail`) | API 스키마 |
| 현재 세션 식별 | 응답에 `is_current: boolean` 필드 (`contracts/openapi/v1.json#SessionViewResponse`) | API 스키마 |
| 세션 폐기 (로그아웃) | `POST /auth/logout` — 단일 세션 폐기 (`src/api/routers/auth.py:110-127`) | 코드 |
| 전역 로그아웃 | `POST /auth/logout-all` — 사용자 모든 세션 폐기 (`src/api/routers/auth.py:129-147`) | 코드 |
| 세션 revoke | `POST /auth/sessions/{session_id}/revoke` — 관리자/사용자 세션 폐기 (`src/api/routers/auth.py:149-179`) | 코드 |
| Sessions 페이지 | `frontend/apps/web/src/routes/settings/SessionsPage.tsx` — 세션 목록 + revoke + 현재 세션 표시 | 코드 |
| `sessionStorage` 격리 | 각 탭이 독립적인 `sessionStorage` 보유 → 다른 탭의 토큰 변경 감지 안 함 | `tokenStore.ts:64-68` |
| 다른 탭 로그아웃 시 | 다른 탭에서 logout/logout-all 시 → 이 탭의 `sessionStorage` 토큰은 살아있으나 API 호출 시 401 → `tryRefresh()` 실패 → `/login` 리다이렉트 | `http.ts:133-170` |
| 세션 last_seen_at 갱신 | API 미존재 — `last_seen_at`은 세션 생성/리프레시 시에만 업데이트 (`src/services/auth/session_repository.py:150-165`) | 코드 |

---

## 6. 현재 구현과 갭 표

| 심각도 | 갭 설명 | 재현 절차 | 근거 (파일:줄) |
|---|---|---|---|
| **높음** | 회원가입 후 MFA 설정 페이지에 라우트 보호 없음 — MFA 미설정 상태에서 URL 직접 접근 시 메인 앱으로 진입 가능 | 1. 회원가입 2. MFA 설정 안 하고 URL에 `/dashboard` 직접 입력 3. 메인 앱 진입 | `router.tsx:26-27` — `/onboarding/*`에 `ProtectedRoute` 없음 |
| **높음** | MFA 재설정 시 기존 세션의 `auth_level`이 `PASSWORD`로 downgrade — 재설정 중 로그인 상태 유지 안 됨 | 1. MFA 활성화 로그인 2. `/onboarding/mfa-setup`에서 재설정 3. 새 MFA 설정 후 토큰 재발급 여부 확인 | `auth.py:150-177` — 재설정 시 토큰 재발급 로직 없음 |
| **중간** | 다른 탭에서 로그아웃/전역 로그아웃 시 현재 탭이 401을 받을 때까지 지연 발생 — UI 피드백 없음 | 1. 탭 A, B에서 동시 로그인 2. 탭 B에서 "전역 로그아웃" 3. 탭 A에서 API 호출 전까지 "알 수 없음" 상태 유지 | `http.ts:133-170` — 최초 API 호출 시에만 401 감지 |
| **중간** | Sessions 페이지에서 "현재 세션" revoke 버튼 노출 — 사용자自身が 현재 세션 폐기 시 즉시 로그아웃 | `SessionsPage.tsx:93-96` — `canRevoke` 판정에 `isCurrentSession` 제외 | `shared-types/src/session.ts:61-63` |
| **중간** | MFA 복구 방법 없음 — MFA 기기 분실 시 계정 잠금 | `src/api/routers/auth.py` — MFA 해제/복구 API 없음 | 코드 전체 스캔 |
| **낮음** | `sessionStorage`는 탭 격리되지만 브라우저 전체 종료 시 토큰 삭제 → 리프레시 토큰 TTL 14일과 불일치 | `tokenStore.ts:64-68` vs `tokens.py:28` | 코드 |
| **낮음** | `logout()` 시 서버 `POST /auth/logout` 호출 전 로컬 상태 먼저 비움 — 서버 응답 실패 시 서버 세션은 살아있지만 로컬은 로그아웃됨 | `useAuth.ts:56-62` — `await client.logout()` 전 `store.clear()` | 코드 |
| **정보** | 로그인 페이지에서 MFA 필요 시 `BadRequestNotice` 표시 — "MFA 코드를 입력해주세요" 등 구체적 메시지 아님 | `LoginPage.tsx:111-112` | 코드 |
| **정보** | `retryAfterSec` 카운트다운 표시하지만 잠금 상태임을 나타내는 배너/아이콘 없음 | `LoginPage.tsx:106-109` | 코드 |

---

## 7. 사용자 준비물 (인증 앱) 안내

| 단계 | 안내 시점 | 안내 내용 | 근거 |
|---|---|---|---|
| 회원가입 | `SignupPage` 제출 전 | "MFA 설정을 권장합니다. 로그인 시 추가 인증이 필요합니다." (`SignupPage.tsx:104-105`) | 코드 |
| MFA 설정 페이지 | 진입 시 | "인증 앱(Google Authenticator 등)을 설치하고 이 QR 코드를 스캔하세요" (`MfaSetupPage.tsx:110-112`) | 코드 |
| MFA 설정 페이지 | 코드 입력 필드 | "인증 앱에서 표시되는 6자리 코드를 입력하세요" (`MfaSetupPage.tsx:116-118`) | 코드 |
| 로그인 (MFA 활성화) | 로그인 폼에 `totp_code` 필드 추가 (`LoginPage.tsx:68-75`) | "인증 앱 코드" 라벨 + 입력 필드 | 코드 |

> **갭**: 회원가입 시 "인증 앱이 필요합니다" 안내가 있지만, 어떤 앱을 설치해야 하는지 (Google Authenticator, Authy 등) 구체적 안내 없음.

---

## 8. 요약 — 구현 현황

| 영역 | 구현 상태 | 비고 |
|---|---|---|
| 세션 수명 (액세스 15분 / 리프레시 14일) | ✅ 구현 | `tokens.py:27-28` |
| 토큰 sessionStorage 보관 | ✅ 구현 | `tokenStore.ts:64-68` |
| 리프레시 + 토큰 회전 | ✅ 구현 | `refresh.py:55-100` |
| 리프레시 재사용 탐지 | ✅ 구현 | `refresh.py:68-75` |
| 자동 리프레시 + 요청 큐잉 | ✅ 구현 | `tokenRefresh.ts:42-83`, `http.ts:133-170` |
| MFA 설정 (초기) | ✅ 구현 | `auth.py:150-177`, `MfaSetupPage.tsx` |
| MFA 로그인 (2단계) | ✅ 구현 | `auth_service.py:82-94`, `LoginPage.tsx:68-75` |
| MFA 강제 설정 | ⚠️ 부분 | 라우트 보호 없음 (`router.tsx:26-27`) |
| MFA 복구 | ❌ 미구현 | 복구 API 없음 |
| 계정 잠금 (5회/5분) | ✅ 구현 | `auth_service.py:26` |
| 잠금 카운트다운 UI | ✅ 구현 | `LoginPage.tsx:101-108` |
| 세션 목록/단건/revoked | ✅ 구현 | `auth.py:180-239`, `SessionsPage.tsx` |
| 전역 로그아웃 | ✅ 구현 | `auth.py:129-147` |
| 동시 로그인 허용 | ✅ 구현 | 여러 세션 동시 생성 |
| 403 처리 | ✅ 구현 | `ForbiddenNotice` + `/login` 리다이렉트 |
| 토큰 만료 시 진행 중 입력 보존 | ❌ 미구현 | 리프레시 실패 시 전체 로그아웃 — 입력된 폼 데이터 손실 |
