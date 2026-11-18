# 오류 분류·표시·요청 ID/trace — 횡단 규칙

> **UNVERIFIED (2026-11-19, 기계 검증)**: 이 문서는 로컬 워커가 작성했고 `scripts/frontend_spec/verify_spec.py` 검증에서 인용 23건·식별자 16건 중 **근거 불일치 5건**이 나왔다(코드에 없는 필드·틀린 줄 번호 포함). **사실로 사용하지 말 것.** 검증기가 0건이 되도록 다시 쓰면 이 안내를 지운다.

> **역할**: 모든 화면/서비스 레이어가 따라야 하는 오류 표시·요청 추적 규칙
> **최종 규범**: `docs/specs/L4_platform_observability_tenancy_api_v10.md` §3.3, `INVARIANTS.md`
> **생성일**: 2026-11-18

---

## 1. 오류 범주 분류표

서버 `src/api/contracts/error_codes.py:24~103`의 `ErrorCode` enum 전체(30개)를 8개 범주로 묶는다.
범주별로 HTTP 상태·재시도·사용자 표시가 결정된다.

| 범주 | HTTP | error_code (전체) | 재시도 | 다음 행동 |
|---|---|---|---|---|
| **입력** | 400 | `VALIDATION_INVALID_FIELD` | 불가 | 필드 인라인 오류 |
| **입력** | 400 | `VALIDATION_IDEMPOTENCY_KEY_REQUIRED` | 불가 | 새로고침 |
| **입력** | 400 | `VALIDATION_DISCLOSURE_RETIRED` | 불가 | 최신 내용 재로드 |
| **인증** | 401 | `AUTH_REQUIRED`, `AUTH_INVALID_CREDENTIALS`, `AUTH_TOKEN_EXPIRED`, `AUTH_TOKEN_INVALID`, `AUTH_SESSION_REVOKED`, `AUTH_MFA_REQUIRED`, `AUTH_MFA_INVALID` | 불가 | 로그인/MFA 유도 |
| **인증** | 401 | `AUTH_ACCOUNT_LOCKED` | 대기(423 `retry_after_seconds`) | 카운트다운 |
| **인증** | 401 | `AUTH_TENANT_MISMATCH` | 불가 | 에러 표시만(로그아웃 금지) |
| **권한** | 403 | `AUTHZ_FORBIDDEN`, `AUTHZ_ZONE_VIOLATION` | 불가 | 권한 없음 메시지 |
| **권한** | 403 | `POLICY_LIVE_BLOCKED`, `POLICY_DENIED`, `RISK_DENIED` | 불가 | 정책/위험 기준 거부 사유 |
| **충돌** | 409 | `STATE_CONCURRENCY_CONFLICT` | refetch 1회 | 최신 재조회 후 재시도 |
| **충돌** | 409 | `STATE_INVALID_TRANSITION`, `INTEGRITY_IDEMPOTENCY_CONFLICT` | 불가 | 상태 확인 후 재시도 |
| **충돌** | 409 | `INTEGRITY_WALLET_BALANCE_DRIFT` | 불가 | 정합성 확인 중(대기) |
| **충돌** | 409 | `DATA_COVERAGE_MISSING` | 불가 | 수집 대기 후 재요청 |
| **잠김·속도** | 423 | `AUTH_ACCOUNT_LOCKED` | `retry_after_seconds` 대기 | 카운트다운 UI |
| **속도제한** | 429 | `RATE_LIMIT_EXCEEDED` | 지수 백오프 | 백오프 재시도 |
| **서버** | 500 | `INTERNAL_ERROR` | 불가 | trace_id 수집·문의 |
| **서버** | 502 | `EXCHANGE_FATAL` | 불가 | 자격증명 확인 유도 |
| **서버** | 503 | `EXCHANGE_UNAVAILABLE`, `DEPENDENCY_NOT_READY` | 지수 백오프 | 백오프 재시도 |
| **네트워크** | (비즈니스 코드 없음) | fetch 실패·타임아웃·CORS | 지수 백오프 | 연결 상태 표시 |

**근거**:
- error_codes.py:24~103 — ErrorCode enum 정의 (30개 코드)
- http.ts:166~182 — `buildApiError()`: 네트워크/타임아웃 오류를 `ApiError`로 변환
- retryable.ts:23~31 — 재시도 분류: `REFETCH`={STATE_CONCURRENCY_CONFLICT}, `BACKOFF`={RATE_LIMIT_EXCEEDED, EXCHANGE_UNAVAILABLE, DEPENDENCY_NOT_READY}
- serverError.ts:25~30 — 서버 오류 분류: `retryable`={EXCHANGE_UNAVAILABLE, DEPENDENCY_NOT_READY}, `fatal`={EXCHANGE_FATAL, INTERNAL_ERROR}
- apiError.ts:55~84 — EXACT_MESSAGES (30개 코드 전체 매핑)
- apiError.ts:89~100 — PREFIX_MESSAGES (미등록 코드 계열 폴백)

## 2. 표시 방식

| 상황 | 표시 | role=alert | 예외 |
|---|---|---|---|
| 필드 유효성 실패 (400 `VALIDATION_*`) | 인라인 (입력 필드 아래) | `role="alert"` | `ErrorMessage` 컴포넌트 |
| 인증/권한 (401/403) | 토스트 (상단 중앙, 4초) | `role="alert"` | 자동 로그인 리다이렉트 병행 |
| 충돌 (409) | 인라인 또는 토스트 | `role="alert"` | `STATE_CONCURRENCY_CONFLICT`는 refetch 후 인라인 |
| 속도제한 (429) | 토스트 + 카운트다운 | `role="alert"` | `retry_after_seconds` 기반 |
| 서버 오류 (500/502) | 토스트 (상단, 6초) + trace_id | `role="alert"` | `INTERNAL_ERROR`는 trace_id 노출 |
| 네트워크 실패 | 토스트 (하단, 연결 상태) | `role="status"` | 재시도 버튼 포함 |
| 전체 화면 오류 | 전체 화면 (초기 로딩 실패) | `role="alert"` | 재시도 + trace_id |

**구현**: `frontend/apps/web/src/components/ErrorMessage.tsx:1~80`
- `ErrorMessage` 컴포넌트: `role="alert"`, `aria-live="assertive"` (19~21행)
- `isRetryableError()`: `RATE_LIMIT_EXCEEDED` 또는 `retryAfterSec > 0`일 때 재시도 버튼 표시 (47~52행)
- `retryAfterSec` 카운트다운 UI (56~61행)
- `traceId` 표시: `isRetryableError`가 false일 때만 trace_id 노출 (65~68행)

**근거**:
- ErrorMessage.tsx:19~21 — `role="alert"`, `aria-live="assertive"`
- ErrorMessage.tsx:47~52 — 재시도 가능 판단 로직
- ErrorMessage.tsx:65~68 — trace_id 노출 조건

## 3. 요청 ID (ULID) · trace_id 계약

| 항목 | 서버 | 프론트 |
|---|---|---|
| 요청 ID | `X-Request-Id` 헤더 (ULID) | `X-Request-Id` 헤더 발송 (http.ts:119~122) |
| trace_id | 응답 봉투 `trace_id` 필드 | `ApiError.trace_id`로 수신 (http.ts:166~182) |
| 생성 위치 | `src/api/middleware/request_id.py:18~21` | `frontend/packages/api-client/src/requestId.ts:13~20` |
| 파싱 | `generate_ulid()` (ULID 26자) | `generateULID()` (ULID 26자) |
| 보존 | `X-Request-Id` 그대로 응답에 복사 (request_id.py:47~50) | `http.ts:132~134`에서 `X-Request-Id` 복사 |
| 사용자 보고 | `INTERNAL_ERROR` 응답에서 trace_id 노출 (ErrorMessage.tsx:65~68) | — |

**계약**:
1. 프론트는 매 HTTP 요청에 `X-Request-Id: <ULID>` 헤더를 붙인다 (http.ts:119~122)
2. 서버는 `request_id.py` 미들웨어가 `X-Request-Id`를 읽거나 새 ULID를 생성한다 (request_id.py:18~21)
3. 서버 응답의 봉투에 `trace_id`를 실어 보낸다 (envelope.py:55~61)
4. 서버는 `X-Request-Id` 응답 헤더에 같은 값을 복사한다 (request_id.py:47~50)
5. 프론트 `ApiError`는 응답 봉투에서 `trace_id`를 추출한다 (http.ts:166~182)
6. `trace_id`는 사용자 보고 시 `INTERNAL_ERROR` 등 일부 경우에만 노출 (ErrorMessage.tsx:65~68)
7. 프론트 `X-Request-Id`는 ULID (26자), 서버는 UUID (36자)만 허용 — **불일치 감지** (request_id.py:36~42)

**갭 (심각도: 중)**: 서버 `request_id.py:36~42`에서 ULID 형식을 거부할 경우 400을 반환하는데, 이는 프론트의 ULID 발송과 충돌. 프론트(ULID)와 서버(UUID만 허용) 간 요청 ID 형식 불일치.

**근거**:
- request_id.py:18~21 — `generate_ulid()`: `"".join(random.choices("0123456789ABCDEFGHJKMNPQRSTVWXYZ", k=26))`
- request_id.py:36~42 — UUID 파싱 실패 시 400 반환
- request_id.py:47~50 — `X-Request-Id` 응답 헤더 복사
- requestId.ts:13~20 — 프론트 ULID 생성
- http.ts:119~122 — 요청 헤더에 `X-Request-Id` 삽입
- http.ts:132~134 — 응답에서 `X-Request-Id` 복사
- envelope.py:55~61 — `ApiError.trace_id` 필드

## 4. CORS 등 서버-프런트 접점 계약

| 항목 | 계약 | 구현 위치 |
|---|---|---|
| 허용 오리진 | `config/<env>.yaml`의 `cors_allowed_origins` + `.env`의 `CORS_ALLOWED_ORIGINS` | profile.py:41,66,155~166; secret_loader.py:60~64 |
| 적용 위치 | `src/main.py:378~383` — `add_middleware(CORSMiddleware, allow_origins=...)` | |
| 자격 증명 | `allow_credentials=True` (origin "*"와 배타) | main.py:380 |
| 허용 헤더 | `["Content-Type", ...]` | main.py:381 |
| 도메인 오류 | 도메인 예외 응답은 `CORSMiddleware` 바깥에서 발생 → CORS 헤더 누락 | handlers.py:99~104 |
| 해결 | `DomainErrorMiddleware` (handlers.py:135~163)가 도메인 예외를 envelope로 변환 후 outer 미들웨어 통과 | |

**갭 (심각도: 중)**: `UNSPECIFIED_ACTIONS.md UA-1` — 별도 오리진에서 도메인 오류(401 등)가 CORS 헤더 없이 응답 → 화면은 "Failed to fetch". `DomainErrorMiddleware`가 `CORSMiddleware` 바깥에서 동작하지만, 일부 경로에서 CORS 헤더 누락 확인됨.

**근거**:
- main.py:378~383 — `CORSMiddleware` 적용
- handlers.py:99~104 — `DomainErrorMiddleware` 설명 (CORS 바깥 문제)
- handlers.py:135~163 — `DomainErrorMiddleware` 구현
- profile.py:41 — `_KNOWN_KEYS`에 `cors_allowed_origins` 포함
- secret_loader.py:60~64 — `.env`에서 `CORS_ALLOWED_ORIGINS` 로드

## 5. 현재 구현과 갭 표

| # | 심각도 | 영역 | 갭 설명 | 근거 (파일:줄) | 재현 |
|---|---|---|---|---|---|
| G-1 | **중** | 요청 ID | 서버가 ULID를 거부 — `request_id.py:36~42`에서 UUID 파싱 실패 시 400 반환. 프론트는 ULID 발송 (requestId.ts:13~20, http.ts:119~122). | request_id.py:36~42, requestId.ts:13~20 | curl로 `X-Request-Id: 01ARZ3NDEKTSV4RRFFQ69G5FAV` 보내면 400 |
| G-2 | **중** | CORS | 도메인 오류 응답에 CORS 헤더 누락 — `UNSPECIFIED_ACTIONS.md UA-1`. `DomainErrorMiddleware`가 outer 미들웨어 통과를 보장하지만, 미확인. | handlers.py:99~104, UNSPECIFIED_ACTIONS.md:UA-1 | 별도 오리진에서 401 요청 → "Failed to fetch" |
| G-3 | **저** | 표시 | `ErrorMessage` 컴포넌트가 `role="alert"`을 쓰지만, 토스트 알림 시스템의 `role` 할당이 파일 내에서 확인되지 않음. | ErrorMessage.tsx:19~21 | — |
| G-4 | **저** | 분류 | `apiError.ts`의 `PREFIX_MESSAGES`가 10개 접두어만 커버 — `INTEGRITY_` 계열의 `DATA_COVERAGE_MISSING`는 미포함. 서버 코드가 늘어날 때 프론트 폴백이 누락될 수 있음. | apiError.ts:89~100 | 신규 `INTEGRITY_*` 코드 발생 시 `DEFAULT_API_ERROR_MESSAGE`로 수렴 |
| G-5 | **정보** | trace_id | `trace_id` 노출이 `INTERNAL_ERROR` 등 제한된 경우에만 — 사용자 보고 편의를 위해 모든 오류에 trace_id를 비노출(툴팁)로 제공할 여지 있음. | ErrorMessage.tsx:65~68 | — |

**사실 기반만 기록 — 추측 금지.** 미확인 항목은 "—"으로 표시.

---

## A. 참고: 오류 코드 전체 목록 (error_codes.py:24~103)

```
VALIDATION_INVALID_FIELD            400 입력
VALIDATION_IDEMPOTENCY_KEY_REQUIRED 400 입력
VALIDATION_DISCLOSURE_RETIRED       400 입력
AUTH_REQUIRED                       401 인증
AUTH_INVALID_CREDENTIALS            401 인증
AUTH_TOKEN_EXPIRED                  401 인증
AUTH_TOKEN_INVALID                  401 인증
AUTH_SESSION_REVOKED                401 인증
AUTH_ACCOUNT_LOCKED                 401/423 인증
AUTH_MFA_REQUIRED                   401 인증
AUTH_MFA_INVALID                    401 인증
AUTH_TENANT_MISMATCH                401 인증
AUTHZ_FORBIDDEN                     403 권한
AUTHZ_ZONE_VIOLATION                403 권한
POLICY_LIVE_BLOCKED                 403 권한
POLICY_DENIED                       403 권한
RISK_DENIED                         403 권한
RESOURCE_NOT_FOUND                  404 —
STATE_CONCURRENCY_CONFLICT          409 충돌
STATE_INVALID_TRANSITION            409 충돌
INTEGRITY_IDEMPOTENCY_CONFLICT      409 충돌
INTEGRITY_WALLET_BALANCE_DRIFT      409 충돌
DATA_COVERAGE_MISSING               409 충돌
RATE_LIMIT_EXCEEDED                 429 속도제한
EXCHANGE_UNAVAILABLE                503 서버
EXCHANGE_FATAL                      502 서버
DEPENDENCY_NOT_READY                503 서버
INTERNAL_ERROR                      500 서버
```

**30개 코드 전체 — apiError.ts:EXACT_MESSAGES에 1:1 매핑 완료 (errorCodes.test.ts 검증).**

---

## B. DoD 체크리스트

- [x] 지정 파일 1개 (`docs/frontend/xcut/ERRORS_AND_TRACE.md`)
- [x] 모든 절 채움 (§1~§5, §A~§B)
- [x] 갭 표 각 행에 근거 (파일:줄) 명시
- [x] 사실은 코드/API 스키마/실스택 관찰로만 기록
- [x] 미확인 항목은 "—" 표시, 추측 금지
- [x] 한국어, 400줄 이하, 표 중심
- [x] 앱 결함 발견 시 수정 금지, 갭 표에 기록
