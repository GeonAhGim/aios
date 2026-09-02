# 15. REST API 명세 · 에러코드 · RBAC — v1.6

> **v1.6(2026-08-28) = 다자산군(Multi-Asset-Class) 확장 라운드.** ADR-2026-08-28
> 반영 — `/marketplace/listings?asset_class=`가 이제 01번 §1.0 `AssetClass`
> enum 값을 받는다(자유 문자열 아님). `POST /strategies`(§15.5-A)에 파생상품
> 필드 예시 추가. 신규 에러코드 `UNSUPPORTED_ASSET_CLASS`(02번 §2.0-A
> capability-gated 원칙) 추가.

> 근거: 레드팀 재검토(2026-08) — FD-11/12/13(인증·거래소연동·마켓플레이스)이 프론트엔드에서
> 호출할 실제 HTTP 엔드포인트가 지금까지 정의되지 않았음을 지적받아 신설.
> **개정이력**: v1.0(기존) → v1.1(2026-08-10, FD-14~21용 §15.5-A~H 신규 엔드포인트
> 추가) → v1.2(2026-08-10, 재점검 라운드: §15.5-G/H에 예시 보강, RBAC 표에 v1.1
> 기능 권한 귀속 명시) → v1.3(2026-08-10, 재점검 라운드: 누락 엔드포인트 6개
> 추가, 경로명 정정, 출금권한 응답 예시) → v1.4(2026-08-10, "0번부터 재검토":
> 리뷰/분쟁/검색정렬 엔드포인트 추가(FD-13.8~13.10), 결제확인 엔드포인트
> 추가(FD-18.5a/b)) → **v1.5(2026-08-10) = 번호 충돌 정정.** 이 문서의
> §15.1~15.7이 정책문서(docx) 15장(DevEngine 연계 경계, 15.1~15.7 — 이
> 프로젝트에서 가장 먼저 만들어진 장 중 하나라 이 충돌이 아마 가장 오래됐을
> 가능성이 높음)과 완전히 동일한 번호로 겹치는 것을 발견 — 16/17번 문서와
> 동일 라운드에 동일 조치. 모든 최상위 헤더를 "§15.X"로 전면 변경.
> 범위: Phase 1 SCAFFOLD 대상 API만(FD-8 매매판단 관련 API는 FROZEN 이후).

## §15.1 API 공통 규칙

- Base path: `/api/v1/`
- 인증: `Authorization: Bearer <JWT>` 헤더(FD-11.1에서 발급).
- 모든 응답은 JSON. 성공 시 `{"data": ...}`, 실패 시 §15.3 에러 포맷.
- 페이지네이션(목록 API): `?page=1&size=20` 쿼리 파라미터, 응답에 `{"data": [...], "meta": {"total": N, "page": 1, "size": 20}}`.
- Idempotency: 상태를 변경하는 POST 요청 중 금전/주문 관련은 `Idempotency-Key` 헤더 지원(7.5 원칙과 연동).

## §15.2 인증 API (FD-11)

| 메서드 | 경로 | 설명 | 인증 필요 |
|---|---|---|---|
| POST | `/auth/register` | 회원가입 | X |
| POST | `/auth/login` | 로그인 | X |
| POST | `/auth/mfa/setup` | MFA 활성화(secret 발급) | O |
| POST | `/auth/mfa/verify` | MFA 코드 최초 검증 | O |
| POST | `/auth/logout` | 세션 무효화 | O |
| GET | `/users/me` | 내 정보 조회 | O |
| PUT | `/users/me/approval-settings` | 승인 설정(ApprovalMode) 변경 | O |
| DELETE | `/users/me` | 회원탈퇴 요청(RUNNING 실행 존재 시 409, FD-11.4/v1.1) | O |
| POST | `/users/me/withdrawal-whitelist` | 비상출금 목적지 등록(위기상황 중 409, FD-11.5, "다시 0번부터" 라운드 추가) | O |
| GET | `/users/me/withdrawal-whitelist` | 등록된 목적지 목록 조회 | O |

### 예시: POST /auth/login

```
Request:
{
  "email": "user@example.com",
  "password": "********",
  "totp_code": "123456"   // mfa_enabled=true인 경우만 필수
}

Response 200:
{
  "data": {
    "access_token": "eyJhbGci...",
    "expires_in": 3600,
    "user": { "user_id": "uuid", "email": "user@example.com", "display_name": "..." }
  }
}

Response 401:
{
  "error_code": "AUTH_INVALID_CREDENTIALS",
  "message": "이메일 또는 비밀번호가 올바르지 않습니다."
}

Response 423 (계정 잠금):
{
  "error_code": "AUTH_ACCOUNT_LOCKED",
  "message": "로그인 시도가 5회 초과하여 15분간 잠겼습니다.",
  "retry_after_seconds": 900
}
```

## §15.3 표준 에러 응답 포맷 및 에러코드 체계

```
{
  "error_code": "{DOMAIN}_{REASON}",
  "message": "사람이 읽을 수 있는 한국어 메시지",
  "details": { }
}
```

| HTTP 상태 | 의미 | 사용 예 |
|---|---|---|
| 400 | 요청 형식 오류 | VALIDATION_INVALID_FIELD |
| 401 | 인증 실패/만료 | AUTH_INVALID_CREDENTIALS, AUTH_TOKEN_EXPIRED |
| 403 | 권한 없음 | AUTHZ_FORBIDDEN, AUTHZ_ZONE_VIOLATION(16장 FROZEN Zone 접근 시도) |
| 404 | 대상 없음 | RESOURCE_NOT_FOUND |
| 409 | 상태 충돌 | ORDER_ALREADY_FILLED(이미 체결된 주문 취소 시도 등) |
| 423 | 잠김 | AUTH_ACCOUNT_LOCKED |
| 429 | 요청 과다 | RATE_LIMIT_EXCEEDED |
| 500 | 내부 오류 | INTERNAL_ERROR(상세 원인은 응답에 노출하지 않고 서버 로그로만 — 07번 §7.1 마스킹 원칙과 동일) |
| 503 | 일시적 불가 | EXCHANGE_UNAVAILABLE(8.6 Circuit Breaker 발동 중), DATA_DISTRUST_MODE(8.1-A 발동 중) |
| 400 | 자산군 미지원(v1.6 신규, ADR-2026-08-28) | UNSUPPORTED_ASSET_CLASS(대상 거래소가 이 자산군을 지원하지 않음, 02번 §2.0-A capability-gated 원칙) |

`MihwaError` 계층(11번 §11.3)과의 매핑: `CurrencyMismatchError`→500(내부 버그로 취급), `RetryableExchangeError`→503, `FatalExchangeError`→502, `ZoneViolationError`→403.

## §15.4 거래소 연동 API (FD-12)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/exchange-credentials` | 내가 등록한 거래소 목록 |
| POST | `/exchange-credentials` | 거래소 자격증명 등록 |
| DELETE | `/exchange-credentials/{exchange}` | 자격증명 해지 |
| GET | `/exchange-credentials/{exchange}/balance` | 잔고 조회(FD-3.2 경유) |
| GET | `/exchange-credentials/{exchange}/positions` | 포지션 조회 |

### 예시: POST /exchange-credentials

```
Request:
{ "exchange": "bitget", "api_key": "...", "api_secret": "..." }

Response 201:
{ "data": { "exchange": "bitget", "linked_at": "2026-08-09T12:00:00Z", "verified": true } }

Response 400 (키 유효성 검증 실패):
{ "error_code": "EXCHANGE_CREDENTIAL_INVALID", "message": "거래소 응답: 인증 실패. API Key/Secret을 확인하세요." }

Response 400 (출금 권한 포함된 키 — "0번부터 재검토" 라운드 추가):
{ "error_code": "WITHDRAWAL_PERMISSION_DETECTED", "message": "이 API 키에는 출금 권한이 포함되어 있습니다. 거래소에서 출금 권한을 제외하고 키를 재발급해주세요." }
```

## §15.5 마켓플레이스 API (FD-13)

| 메서드 | 경로 | 설명 | 역할 제약(15.6 참조) |
|---|---|---|---|
| GET | `/marketplace/listings?asset_class=&exchange=&min_backtest_months=&max_price=` | 리스팅 목록(공개), 검증통과일 역순 정렬(FD-13.8, 재점검 라운드에서 필터·정렬 파라미터 추가 — 14번 §14.4가 처음부터 요구했으나 누락돼 있었음). `asset_class`는 v1.6부터 01번 §1.0 `AssetClass` enum 값(예: `CRYPTO`, `KR_EQUITY`) — 자유 문자열 아님, 잘못된 값은 400 VALIDATION_INVALID_FIELD | 전체 |
| GET | `/marketplace/listings/{id}` | 리스팅 상세(로직 비공개, 10.3-B) | 전체 |
| POST | `/marketplace/listings` | 리스팅 생성 | 판매자(전략 소유자) |
| POST | `/marketplace/listings/{id}/submit-verification` | 검증 요청 | 판매자 |
| POST | `/marketplace/listings/{id}/verify` | 검증 승인/반려 | 검증담당자만 |
| POST | `/marketplace/listings/{id}/purchase` | 구매 | 구매자(본인 리스팅 구매 불가) |
| GET | `/marketplace/my-purchases` | 내 구매 목록 | 구매자 |
| POST | `/marketplace/listings/{id}/reviews` | 리뷰 작성(FD-13.9, 재점검 라운드 신설 — 14번 §14.2가 요구했으나 DB·API 둘 다 누락돼 있었음) | 구매자(구매이력 필수) |
| GET | `/marketplace/listings/{id}/reviews` | 리뷰 목록(5건 미만은 평균 별점 미표시, 원문은 항상 노출) | 전체 |
| POST | `/disputes` | 분쟁 접수(FD-13.10, 재점검 라운드 신설 — 17번 프론트엔드 `DisputeSubmitPage`가 호출할 백엔드가 없었음) | 구매자(본인 구매건만) |

### 예시: POST /marketplace/listings/{id}/verify (검증담당자 전용)

```
Request:
{ "decision": "APPROVE", "checklist": {"overfitting_checked": true, "lookahead_checked": true, "paper_trading_3mo_confirmed": true} }
   또는
{ "decision": "REJECT", "reason": "Paper Trading 기간 부족(45일)" }

Response 200:
{ "data": { "listing_id": 123, "status": "LISTED" } }

Response 403 (검증담당자 아님):
{ "error_code": "AUTHZ_FORBIDDEN", "message": "이 작업은 검증담당자만 수행할 수 있습니다." }
```

### 예시: POST /marketplace/listings/{id}/reviews

```
Request:
{ "rating": 4, "comment": "3개월 운용해봤는데 백테스트와 실측이 크게 다르지 않았습니다." }

Response 201:
{ "data": { "review_id": 45, "listing_id": 123, "rating": 4, "created_at": "2026-08-10T12:00:00Z" } }

Response 403 (구매 이력 없음):
{ "error_code": "AUTHZ_FORBIDDEN", "message": "이 전략을 구매한 사용자만 리뷰를 작성할 수 있습니다." }

Response 400 (중복 작성):
{ "error_code": "VALIDATION_DUPLICATE_REVIEW", "message": "이미 이 전략에 리뷰를 작성하셨습니다." }
```

### 예시: POST /disputes

```
Request:
{ "purchase_id": 456, "reason": "리스팅에 표시된 샤프비율(1.8)과 실제 3개월 운용 결과(0.3)가 크게 다릅니다." }

Response 201:
{ "data": { "dispute_id": 12, "status": "OPEN", "created_at": "2026-08-10T12:00:00Z" } }

Response 403 (타인 구매건):
{ "error_code": "AUTHZ_FORBIDDEN", "message": "본인 구매 건에 대해서만 분쟁을 제기할 수 있습니다." }

Response 400 (진행중 분쟁 이미 존재):
{ "error_code": "VALIDATION_DUPLICATE_DISPUTE", "message": "이 구매 건은 이미 처리중인 분쟁이 있습니다." }
```

## §15.5-A 전략 편집기 API (FD-14, v1.1 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| GET | `/indicators` | 사용 가능 지표·파라미터 목록 조회 | 전체 |
| GET | `/indicators/{name}/compute` | 특정 지표 계산값 조회(symbol/timeframe/params 쿼리) | 전체 |
| POST | `/strategies` | 전략 저장(GENERATED 진입) | 로그인 사용자 |
| GET | `/strategies/{id}/preview` | 경량 프리뷰(정식 백테스트 아님) | 소유자만 |
| GET | `/strategies/{id}` | 전략 상세(생애주기 상태 포함) | 소유자만(9.1 상태 조회) |

### 예시: POST /strategies

```
Request:
{
  "target_asset": "BTC/USDT",
  "exchange": "bitget",
  "entry_condition": { "indicator": "RSI", "period": 14, "operator": "<", "value": 30 },
  "exit_condition": { "indicator": "RSI", "period": 14, "operator": ">", "value": 70 },
  "stop_loss_pct": 5.0
}

Response 201:
{
  "data": {
    "strategy_id": "uuid",
    "version": "1.0.0",
    "status": "GENERATED",
    "fsm_definition": { "...": "9.11 스키마로 컴파일된 결과" }
  }
}

Response 400 (조건 조합이 FSM으로 컴파일 불가):
{
  "error_code": "STRATEGY_CONDITION_INVALID",
  "message": "진입 조건과 손절 조건이 동시에 만족 가능한 상태가 없습니다.",
  "details": { "conflicting_states": ["BUY_ORDER_PENDING", "STOP_LOSS"] }
}
```

### 예시: POST /strategies (파생상품 — v1.6 신규, ADR-2026-08-28)

```
Request:
{
  "target_asset": "AAPL 250117C00200000",
  "asset_class": "US_OPTION",
  "exchange": "kis",
  "underlying_symbol": "AAPL",
  "option_type": "CALL",
  "strike_price": 200.00,
  "expiry_date": "2025-01-17",
  "entry_condition": { "indicator": "RSI", "period": 14, "operator": "<", "value": 30 },
  "exit_condition": { "indicator": "RSI", "period": 14, "operator": ">", "value": 70 }
}

Response 400 (대상 거래소가 이 자산군 미지원):
{
  "error_code": "UNSUPPORTED_ASSET_CLASS",
  "message": "kis 거래소는 현재 US_OPTION을 지원하지 않습니다.",
  "details": { "exchange": "kis", "requested": "US_OPTION", "supported": ["KR_EQUITY"] }
}
```

> 위 `asset_class`는 예시일 뿐 — 01번 §1.0 실제로는 `OVERSEAS_OPTION`이
> 별도 값으로 예약돼 있고 `US_OPTION`은 정의돼 있지 않다(사용자가 명시적으로
> 요구한 목록에 해외옵션이 없었기 때문). 실제 착수 시 미국 옵션을 지원할지
> 결정되면 `US_OPTION`을 01번에 추가하거나 `OVERSEAS_OPTION`으로 통합할지
> 재검토한다 — 이 예시는 "파생 필드가 요청/응답에 어떻게 나타나는지" 형식만
> 보여주기 위함이다.

### 예시: GET /strategies/{id}/preview

```
Response 200:
{
  "data": {
    "period": "2026-05-01/2026-08-01",
    "signals": [
      { "timestamp": "2026-06-12T09:00:00Z", "type": "ENTRY", "price": 42000 },
      { "timestamp": "2026-06-20T14:00:00Z", "type": "EXIT", "price": 43500 }
    ],
    "disclaimer": "이것은 정식 백테스트가 아닙니다. 정식 검증은 마켓플레이스 등록 시 진행됩니다."
  }
}
```

## §15.5-B 투자자 적합성평가 API (FD-15, v1.1 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| POST | `/users/me/risk-assessment` | 적합성평가 설문 제출 | 로그인 사용자, 온보딩 필수 게이트 |
| GET | `/users/me/risk-profile` | 현재 위험등급 조회 | 본인만 |
| GET | `/users/me/risk-profile/history` | 재평가 이력 조회 | 본인만 |

### 예시: POST /users/me/risk-assessment

```
Request:
{
  "investment_experience_years": 3,
  "capital_at_risk_pct": 20,
  "max_acceptable_loss_pct": 15,
  "investment_horizon": "SHORT_TERM",
  "liquidity_needs": "LOW"
}

Response 201:
{
  "data": {
    "risk_profile": "중립형",
    "assessed_at": "2026-08-10T12:00:00Z",
    "next_reassessment_due": "2027-08-10T12:00:00Z"
  }
}
```

**기존 §15.5 마켓플레이스 구매 API 응답 확장(FD-15.3 연동, v1.1)**:

```
POST /marketplace/listings/{id}/purchase 응답에 필드 추가:

Response 200:
{
  "data": {
    "purchase_id": 456,
    "status": "PENDING_PAYMENT",
    "risk_warning": true,               ← 신규
    "risk_warning_reason": "이 전략은 공격형으로 분류되어 있으나, 회원님의 투자성향은 안정형입니다.",  ← 신규
    "requires_explicit_consent": true   ← 신규, true인 경우 별도 동의 API 호출 필요(Draft)
  }
}
```

## §15.5-C 전략 실행 제어 API (FD-16, v1.1 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| POST | `/strategy-executions` | 자본배분·거래소·모드 지정하여 실행 설정 생성 | 전략 소유자(구매자 포함) |
| POST | `/strategy-executions/{id}/start` | 시작 (LIVE+임계초과 시 승인요청 생성) | 소유자 |
| POST | `/strategy-executions/{id}/pause` | 일시정지 | 소유자 |
| POST | `/strategy-executions/{id}/retire` | 중지(포지션 처리방식 지정) | 소유자 |
| GET | `/strategy-executions` | 내 실행중 전략 목록 및 손익 | 소유자 |
| POST | `/strategy-executions/{id}/convert-to-live` | PAPER→LIVE 전환(신규 실행 생성, FD-16.5) | 소유자 |

### 예시: POST /strategy-executions/{id}/start

```
Response 200 (PAPER 모드, 즉시 시작):
{ "data": { "execution_id": "uuid", "status": "RUNNING", "mode": "PAPER" } }

Response 202 (LIVE 모드, 승인 대기 생성됨):
{
  "data": {
    "execution_id": "uuid",
    "status": "PENDING_APPROVAL",
    "approval_request_id": "uuid",
    "message": "배분 규모가 승인 임계치를 초과하여 Critical Risk 승인이 필요합니다."
  }
}

Response 409 (Watchdog/Circuit Breaker로 이미 PAUSED 상태):
{
  "error_code": "EXECUTION_BLOCKED_BY_SAFETY_LAYER",
  "message": "안전장치가 발동 중이어서 시작할 수 없습니다. 현재 상태를 확인하세요."
}
```

## §15.5-D 알림 API (FD-17, v1.1 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| GET | `/notifications` | 내 알림 이력 조회 | 본인만 |
| GET | `/users/me/notification-preferences` | 현재 알림 설정 조회 | 본인만 |
| PUT | `/users/me/notification-preferences` | 알림 설정 변경(강제 항목 요청 시 403) | 본인만 |

### 예시: PUT /users/me/notification-preferences (강제 항목 시도)

```
Request: { "human_approval_requested_email": false }

Response 403:
{
  "error_code": "NOTIFICATION_MANDATORY_CHANNEL",
  "message": "Critical Risk 승인 알림은 끌 수 없습니다."
}
```

## §15.5-E 운영자 도구 API (FD-18, v1.2 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| GET | `/admin/verification-queue` | 검증 대기중 리스팅 목록(본인 리스팅 제외) | 검증담당자 |
| GET | `/admin/disputes` | 분쟁 티켓 목록 | 플랫폼 운영자 |
| GET | `/admin/disputes/{id}` | 분쟁 상세(검증 당시 근거자료 포함) | 플랫폼 운영자 |
| POST | `/admin/disputes/{id}/resolve` | 분쟁 처리(종결 또는 DELISTED+환불) | 플랫폼 운영자 |
| GET | `/admin/users` | 사용자 목록(email 검색) | 플랫폼 운영자 |
| PUT | `/admin/users/{id}/status` | 사용자 상태 변경(ACTIVE↔SUSPENDED만) | 플랫폼 운영자 |
| POST | `/admin/users/{id}/suspend-seller` | 판매자 자격 정지 | 플랫폼 운영자 |
| GET | `/admin/purchases?status=PENDING_PAYMENT` | 결제 대기 목록(FD-18.5a, 재점검 라운드 추가, 페이지네이션 적용) | 플랫폼 운영자 |
| POST | `/admin/purchases/{id}/confirm-payment` | 구매 결제 확인(PENDING_PAYMENT→CONFIRMED, FD-18.5b, 재점검 라운드 추가, Idempotency-Key 필요) | 플랫폼 운영자 |

### 예시: POST /admin/disputes/{id}/resolve

```
Request:
{ "decision": "DELISTED_AND_REFUND", "reason": "검증 당시 오버피팅 은폐 확인" }
   또는
{ "decision": "NORMAL_RISK_REALIZATION", "reason": "검증 당시 데이터 정상, 이후 시장상황 변화" }

Response 200:
{ "data": { "dispute_id": 789, "listing_status": "DELISTED", "resolved_at": "2026-08-10T15:00:00Z" } }
```

### 예시: PUT /admin/users/{id}/status (허용 범위 밖 시도)

```
Request: { "status": "DELETED" }

Response 400:
{
  "error_code": "ADMIN_STATUS_TRANSITION_INVALID",
  "message": "DELETED/PENDING_DELETION 전이는 사용자 본인 탈퇴 절차(FD-11.4) 전용입니다."
}
```

## §15.5-F 포트폴리오 관리 API (FD-19, v1.3 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| GET | `/portfolio` | 통합 포트폴리오 조회(전략별 비중·손익) | 본인만 |
| PUT | `/portfolio/rebalance` | 여러 실행의 배분 동시 재조정 | 본인만 |

### 예시: PUT /portfolio/rebalance

```
Request:
{
  "adjustments": [
    { "execution_id": "uuid-1", "new_allocated_capital": 3000000 },
    { "execution_id": "uuid-2", "new_allocated_capital": 1000000 }
  ]
}

Response 200:
{ "data": { "adjusted": 2, "pending_approval": 0 } }

Response 202 (일부 조정이 승인 임계치 초과):
{
  "data": { "adjusted": 1, "pending_approval": 1 },
  "approval_request_ids": ["uuid-approval-1"]
}
```

## §15.5-G 운용보고서 API (FD-20, v1.3 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| GET | `/reports?period_start=&period_end=&execution_id=` | 기간별 보고서 조회 | 본인만 |

### 예시: GET /reports?period_start=2026-07-01&period_end=2026-07-31

```
Response 200:
{
  "data": {
    "period": "2026-07-01/2026-07-31",
    "total_return_pct": 4.2,
    "win_rate_pct": 61.5,
    "max_drawdown_pct": 3.1,
    "trade_count": 26,
    "by_strategy": [
      { "strategy_id": "uuid-1", "contribution_pct": 2.8 },
      { "strategy_id": "uuid-2", "contribution_pct": 1.4 }
    ],
    "daily_pnl_series": [
      { "date": "2026-07-01", "cumulative_pnl": 12000 },
      { "date": "2026-07-02", "cumulative_pnl": 8500 }
    ]
  }
}

Response 200 (해당 기간 거래 없음, FD-20.1 예외 상황):
{
  "data": {
    "period": "2026-07-01/2026-07-31",
    "trade_count": 0,
    "message": "이 기간 거래 내역이 없습니다."
  }
}

Response 400 (기간 파라미터 누락):
{ "error_code": "VALIDATION_INVALID_FIELD", "message": "period_start, period_end는 필수입니다." }
```

## §15.5-H 모바일 전용 API (FD-21, v1.3 신규)

| 메서드 | 경로 | 설명 | 역할 제약 |
|---|---|---|---|
| POST | `/devices` | 디바이스 푸시 토큰 등록 | 로그인 사용자 |
| DELETE | `/devices/{token}` | 토큰 해지(로그아웃/앱삭제 시) | 본인만 |

### 예시: POST /devices

```
Request:
{ "device_token": "fcm-abc123...", "platform": "Android" }

Response 201:
{ "data": { "device_id": 42, "registered_at": "2026-08-10T12:00:00Z", "is_active": true } }

Response 400 (중복 등록 — 이미 활성 상태인 동일 토큰):
{ "error_code": "DEVICE_TOKEN_ALREADY_REGISTERED", "message": "이미 등록된 디바이스입니다." }
```

### 예시: DELETE /devices/{token}

```
Response 200:
{ "data": { "device_token": "fcm-abc123...", "is_active": false } }

Response 404 (존재하지 않거나 이미 해지된 토큰):
{ "error_code": "RESOURCE_NOT_FOUND", "message": "등록된 디바이스를 찾을 수 없습니다." }
```

> 생체인증(FD-21.2)은 서버 API가 아니라 클라이언트(OS Keychain/Keystore) 로직
> 위주 — 기존 `/auth/login`으로 발급된 토큰을 재사용하므로 별도 엔드포인트 없음.

## §15.6 RBAC — 역할별 권한 매트릭스 (신규)

정책문서 4.5의 Permission Level(0~6)은 AI Agent 대상이다. 아래는 사람 역할 대상 — 별개 체계이며 혼동하지 않는다.

| 역할 | 정의 | 권한 |
|---|---|---|
| 일반 사용자(User) | 회원가입한 모든 사용자 기본값 | 본인 거래소 연동, 본인 전략 생성·백테스트, 마켓플레이스 조회·구매, 본인 승인설정 관리 |
| 판매자(Seller) | 마켓플레이스에 전략을 리스팅한 사용자(User의 부분집합) | User 권한 + 본인 전략 리스팅 생성/수정/철회 |
| 구매자(Buyer) | 전략을 구매한 사용자(User의 부분집합) | User 권한 + 구매한 전략 실행(FD-13.4), 판매자 전략 로직 조회는 불가(10.3-B) |
| 검증담당자(Verifier) | 플랫폼 운영자가 지정(v3.2 MVP 수동검증 브릿지, 9.5-A) | 리스팅 검증 승인/반려, 검증 이력 조회. 본인이 판매자인 리스팅은 검증 불가(이해상충 방지, 신규 규칙) |
| 플랫폼 운영자(Platform Admin) | 정책문서 4.10 "플랫폼 레벨" 승인권자 | 전체 사용자 조회/상태변경, 시스템 Circuit Breaker/Kill Switch 관련 승인(16.6), 검증담당자 지정 |

- 역할은 별도 role 컬럼이 아니라 행위 기반으로 판정한다(판매자=리스팅 존재 여부, 구매자=구매이력 존재 여부) — User/Seller/Buyer는 배타적이지 않고 중첩 가능. Verifier/Platform Admin만 명시적 지정이 필요하므로 users 테이블에 별도 플래그(Draft: is_verifier, is_platform_admin boolean) 추가 필요(04번 개정 대상).
- 이해상충 규칙(신규, 레드팀 지적으로 발견): 검증담당자가 본인 전략을 검증하는 것은 9.5-A 원문의 "동일한 Agent가 자기 자신이 만든 전략을 검증하고 승인하는 구조를 금지한다"(9.9 절대원칙) 원칙을 사람에게도 동일 적용한 것 — API 레벨에서 verifier_user_id != listing.seller_user_id 강제 검증.
- **v1.1 추가**: FD-14(전략편집기)·FD-15(적합성평가)·FD-16(실행제어판)·FD-17(알림)은
  전부 기존 User 기본 권한 범위 내에서 동작한다 — 신규 역할을 만들지 않는다(17.9-A
  과잉설계 방지 원칙과 일치).
- **v1.2 추가**: FD-18(운영자 도구)은 15.6 표에 이미 명시돼 있던 Verifier/Platform
  Admin 권한을 실제 API로 구현한 것 — RBAC 자체의 변경은 없다. `users.is_verifier`,
  `is_platform_admin` 플래그는 04번/13번 DB스키마에 이번에 함께 반영(이전까지 표에만
  예고되고 미병합 상태였음).

## §15.7 의도적으로 지금 안 정하는 것

- 시퀀스 다이어그램(시각화) — 각 FD-x.y의 "처리 단계"가 텍스트 시퀀스 역할을 겸함, 별도 다이어그램 도구(Mermaid 등) 도입은 실제 착수 시 검토.
- 정량적 NFR(TPS·동시접속자) — 사용자 10인 규모에서는 조기 확정이 오히려 과잉설계(17.9-A 원칙), 실사용 데이터 축적 후 Phase 2~3에서 재검토.
- 암호화 대상 전체 인벤토리 표 — 13.3(자격증명)·13.2(비밀번호)는 이미 확정, 나머지 필드별 전수 조사는 법률검토(19장)와 함께 진행하는 것이 합리적(개인정보 분류가 법적 판단과 얽혀있음).
