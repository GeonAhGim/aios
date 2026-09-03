# AIOS 인증/게이트웨이 구현 현황 감사 (2026-09-03 기준)

대상: C:/aios/aios (Python/FastAPI). 비교 축: QuantDinger류 에이전트 게이트웨이,
스코프 토큰, Idempotency-Key, MCP thin wrapper, live-trading gate.

## 1. 인증 표면 (JWT/API Key/Agent Token/MCP)

**JWT — 부분 구현.**
- 발급: `src/services/auth_service.py:244-249` `issue_token()` — payload는 `sub`(user_id)와
  `exp`만 있음. **jti 없음, refresh token 없음, session 테이블 없음.**
- 검증: `src/api/deps.py:42-68` `get_current_user()` — `jwt.decode()` 후 DB에서 유저 재조회,
  `SUSPENDED/DELETED` 상태를 매 요청마다 재검사(logout 이후에도 남는 stale-token 문제를
  최소화하려는 조치이나 revoke 자체는 없음).
- 로그아웃: `src/api/routers/auth.py:44-49` — **완전한 no-op.** "stateless JWT라 서버측
  무효화 메커니즘은 착수 시 확정 필요"라는 주석 그대로, 클라이언트가 토큰을 버리는 것에만
  의존. jti/denylist/세션 revoke 전부 **없음**.
- MFA(TOTP): `src/services/mfa_service.py`, `src/api/routers/auth.py:52-86` — 구현됨.
  step-up 재인증 윈도우(`MFA_STEP_UP_WINDOW=15분`, `src/api/foundation_deps.py:141,157-161`)도
  구현됨.
- 로그인 실패 잠금: `src/services/auth_service.py:176(locked_until 체크)`, `:256-265`
  (실패 카운트 갱신) — 구현됨. 단, L4 스펙(`L4_platform_observability_tenancy_api_v1.0.md`
  §R5)이 "카운터가 비원자적(SELECT 후 +1)"이라고 명시 — **경합 조건 존재, 원자적 UPDATE로
  교체 설계만 있고 미구현**.

**API Key — 없음 (내부 인증 수단으로는).** 코드베이스 전체에서 `api_key` 매치는 전부
사용자별 **거래소(Bitget 등) 자격증명**(`src/services/exchange_credential_service.py`,
`src/api/routers/exchange_credentials.py`)이다. AIOS 자체 API를 호출하기 위한 발급형
API 키(예: `X-Api-Key` 헤더) 메커니즘은 **없음**.

**Machine/Agent Token, 서비스 계정 — 없음.**
- `agent_token`, `external_ai`, `tool_call`, `llm_tool`, `mcp` 키워드로 `src/` 전체를
  검색했으나 매치 0건 — MCP 서버, LLM 도구노출 엔드포인트, "AI 에이전트 전용 API 표면"은
  **존재하지 않는다.** 사람 웹 API(FastAPI 라우터)와 분리된 별도 에이전트 게이트웨이도 없다.
- `capability_tokens` 테이블(`src/db/migrations/versions/1fd699c0c44c_capability_tokens.py`)이
  존재하지만, **서비스 레이어/라우터 어디서도 참조되지 않는다**(마이그레이션 파일 외 참조 0건).
  설계 문서(`docs/design/13_multi_tenancy_auth_v1.4.md:140`)가 명시: "`capability_tokens`는
  DevEngine 자체 작업(코딩 에이전트) 인증 — 최종 사용자 인증과 별개 개념" → **AIOS 트레이딩
  API와 무관한 별개 시스템의 유물이며, 사실상 미사용(dead schema).**
- 결론: QuantDinger류 "스코프가 좁은 에이전트 전용 토큰"에 대응하는 개념 자체가 **없음**.

**디바이스(푸시) 토큰 — 인증 토큰 아님.** `src/api/routers/device_tokens.py`,
`src/api/device_token_deps.py`는 FCM/APNs 푸시 알림 등록용이며, 인증/인가와 무관.

## 2. 인가 (RBAC / 테넌시 / 스코프)

**역할 기반 접근 제어 — 구현됨(단순한 2-플래그 모델).**
- `src/api/deps.py:85-94` `get_current_verifier`/`get_current_admin` — `User.is_verifier`,
  `User.is_platform_admin` 불리언 플래그 기반. 세분화된 role/permission 테이블 없음.
- 라우터 레벨 강제: `src/api/routers/admin.py`, `src/api/routers/foundation/risk_gate.py:145-179`
  (`post_admin_activate_safety_control`은 `get_current_admin` 의존성이라 **라우팅 자체가
  운영자 아니면 도달 불가** — "권한 체크를 애플리케이션 로직에만 맡기지 않는다"는 명시적
  설계 원칙, RSK-006).

**테넌시 — 부분 구현, RLS 없음.**
- `TenantContext` 발급: `src/api/foundation_deps.py:144-167` `get_tenant_context()` —
  **`tenant_id == subject_id == user_id`로 고정, `role="OWNER"` 하드코딩.** 조직/가구
  단위 멀티테넌시, membership 모델은 **없음**.
- 테넌트 격리는 전 경로 애플리케이션 레벨 `WHERE tenant_id = $1`(예:
  `src/foundation/paper_control/adapters/postgres_repository.py:103-146`,
  `src/api/foundation_deps.py:108-114`의 404 처리)로만 이루어진다.
- DB 레벨 RLS(Row-Level Security) — **없음.** `src/core/db/roles.py`는 `aios_app` vs
  `aios_migrator` 역할 분리(WORM 트리거 무력화 방지 목적)만 하며, RLS `CREATE POLICY`는
  코드베이스 전체에서 0건. 설계 문서(§13.4, §13.8)가 "RLS는 Draft, 팀 DB 운영 역량 확인 후
  결정" — **설계만 존재, 미구현.**

**세분화된 제약(전략/종목/명목가/시간창) — 부분 구현, mandate 계층에서만.**
- `src/foundation/mandates/domain/models.py:36-51` `MandateRevision` —
  `max_total_exposure_pct`, `max_single_instrument_pct`, `min_cash_buffer_pct`,
  `max_daily_loss_pct`, `allowed_autonomy`, `forbidden_assets` 등 **비율 기반** 제약만 존재.
  절대 명목가(notional) 한도, 시간창(time-window) 제약은 **없음**.
- 리스크 정책(`config/risk_policy.yaml`, `src/core/risk/limits.py`)에 8개 지표(Daily Loss,
  MDD, 레버리지, 집중도, 전략배분, VaR, 상관관계, 거래빈도)가 결정론적으로 평가되나,
  이는 PAPER 모드 판단 엔진용이며 API 레벨 인가 제약과는 다른 레이어.

**Break-glass(비상권한) — 설계만 존재.**
- `docs/specs/L4_platform_observability_tenancy_api_v1.0.md:110` `src/core/security/break_glass.py`
  (신규 파일)로 `BreakGlassGrant`(approver ≠ requester, 60분 이내 만료, 조건부 소비)
  설계됨. **코드베이스에 해당 파일 없음 — 완전 미구현.**

**Connection scope allowlist(거래소 연결) — 구현됨.**
- `src/foundation/connections/domain/rules.py:35-55` `validate_capability_profile()` —
  `TRADE_*`, `WITHDRAW`, `TRANSFER`, `SIGN_*` 등은 closed-set 밖이라 **하드 거부**(CON-002).
  scope drift 탐지(`compute_scope_fingerprint`, `confirm_connection.py:69`)도 구현됨.
  단, 이는 "AIOS가 외부 거래소에 요청하는 읽기전용 스코프"에 대한 검증이지, "외부 클라이언트가
  AIOS API를 호출하는 스코프"에 대한 것이 아니다 — QuantDinger식 "스코프가 좁은 API 토큰"과는
  방향이 반대.

## 3. Idempotency

**일반 목적 Idempotency-Key — 구현됨(제한적 범위).**
- `src/core/idempotency.py:43-93` `with_idempotency()` — claim-first(`INSERT ... ON CONFLICT
  DO NOTHING`) 원자적 선점, 2xx만 캐시, 4xx/5xx는 캐시하지 않고 자리 해제. 동시 요청은 409.
  **키 스코프는 호출부 책임**(예: 마켓플레이스는 `purchase:{user_id}:{header}`,
  `src/api/routers/marketplace.py:158-159`) — 헤더값 단독으로 만들면 타 사용자 충돌 위험이
  있었던 과거 결함을 문서화(`idempotency.py:11-14`).
- 실제 호출부는 **`marketplace.py`(구매) 1곳뿐**. `admin.py:172-176`(지갑 충전 확인)은
  자체 `idempotency_key`를 서비스에 그대로 전달(구현은 `wallet_service.py` 쪽, DB 상태
  자체가 멱등성 근거).
- 저장: `idempotency_keys` 테이블(`src/db/migrations/versions/d6e7f8a9b0c1_idempotency_keys.py`).
  **tenant_id/expires_at 컬럼 없음** — L4 스펙(§M2)이 이 갭을 명시하고
  `ALTER TABLE ... ADD COLUMN tenant_id, request_digest, expires_at` 및 만료 정리를
  신규로 설계(**미구현**).

**PAP-006 / paper_control REQUEST 멱등 — 구현됨(정교함).**
- `src/foundation/paper_control/application/request_deployment.py:47-62`
  `_compute_request_digest()` — package_ref/connection_id/adapter_type/
  provider_sandbox_account_ref/endpoint_classification의 SHA-256 다이제스트.
- `:65-83` `_replay_or_conflict()` — 같은 `(tenant_id, idempotency_key)`로 재요청이 오면
  digest 비교: 일치하면 최초 응답(성공/실패 포함) 재현, 불일치면 `IdempotencyKeyConflictError`
  (PAP-006 — "다른 내용의 REQUEST에 키 재사용" 차단). FAILED 상태도 재현하는 것까지 구현
  (일반 `with_idempotency`는 실패를 캐시하지 않는 것과 대조적 — 이 계층은 "재시도 응답
  일관성"이 "재시도 가능성"보다 우선).
- 스코프: `(tenant_id, idempotency_key)` 복합 유니크(`postgres_repository.py:120-146`).

**Command/correlation/causation ID — 부분 구현(command_id/causation_id 없음).**
- `EventBus.publish(topic, payload)` (`src/core/event_bus/bus.py:23`) — payload는 `Any`,
  **command_id/causation_id 필드 자체가 프로토콜에 없음.**
- `correlation_id`는 로깅 스키마(`src/core/logging/schema.py:29`)에만 존재하고, HTTP
  request_id를 기본값으로 채우는 정도(§4 참조) — 이벤트 버스로 전파되지 않음.
- `src/foundation/evidence/application/record_command_event.py:57`은 **호출마다 `uuid4()`로
  새 trace_id를 생성** — L4 스펙이 이를 "상관관계가 끊긴다"고 명시적으로 결함 지적
  (`L4_platform_observability_tenancy_api_v1.0.md` §R1).
- `src/contracts/enterprise.py:26`에 `correlation_id: UUID | None` 필드가 있는
  `EnterpriseCommand`류 계약이 있으나, 실사용처는 `paper_strategy_projection.py` 1곳뿐이고
  범용 이벤트 버스 봉투로는 쓰이지 않음.

## 4. RequestContext / Trace 전파

**부분 구현 — request_id만, tenant_id/trace_id 전파는 설계만 존재.**
- `src/core/logging/request_context.py:12` `request_id_var`(ContextVar) +
  `src/api/middleware/request_id.py:29-40` `RequestIdMiddleware` — `X-Request-ID` 헤더
  왕복, JSON 로그의 `correlation_id` 기본값으로 자동 채움. **구현됨.**
- L4 스펙(§R1, §2.1)이 명시: "8필드(`trace_id, tenant_id, actor, component, event,
  duration_ms` 등) 중 request_id 1개만 있다 — tenant_id/actor/component 없음, 이벤트
  버스·백그라운드 루프로 전파 안 됨, legacy `audit_log`엔 trace_id 컬럼 자체가 없음."
  → `RequestContext`(8필드), `RequestContextMiddleware`, `EventEnvelope`,
  `tenant_binding.py`, `StructuredLogLine` 등은 **전부 "신규" 설계 항목이며 코드베이스에
  해당 파일이 존재하지 않음 — 설계만 존재.**
- 로그에 tenant_id가 남지 않으므로, 테넌트별 장애 추적/감사가 request_id(요청 단위)로만
  가능하고 사용자/테넌트 단위 상관관계는 로그만으로는 불가능.

## 5. Live Trading Gate

**Executor 이중 가드(설계 문서는 "triple guard"를 언급하나 코드는 2단) — 구현됨, 강함.**
- `src/core/executor/executor.py:71-85`:
  1. `mode != "PAPER"` → 무조건 `FrozenZoneLiveModeBlockedError` (ADR-2026-08-29-E,
     정책 문서가 아니라 실행 코드 자체의 하드 차단).
  2. `not adapter.is_paper_trading or not adapter.is_sandboxed` → `FrozenZonePaperAdapterBlockedError`
     (DB의 `mode='PAPER'`만으로는 잘못 구성된 실계정 adapter 주입을 못 막는다는 레드팀 감사
     2026-09-01-08 반영, "두 독립 신호 중 하나라도 걸리면 거부").
- `src/exchanges/common/live_guard.py:28-39` `require_paper_sandbox` 데코레이터 — Convert/
  Grid/Strategy/Margin/Futures/Loan/Subaccount 등 **Executor를 거치지 않는 확장 거래소
  메서드**에 대한 별도 방어선(레드팀 #2026-09-02-32). 즉 "Executor 안" + "Executor 밖
  확장 메서드"에 각각 독립된 가드가 있어 실질적으로 2계층×2조건 방어.
- `docs/ADR-2026-08-29-E-frozen-zone-paper-mode-unlock.md` — LIVE 해제 조건을 "15.6-D
  조건 2(실계정 MFA·이중승인 운영 적용) 충족 + **별도 ADR**"로 명문화. 현재 `.env`에
  실키가 없어 **구조적으로 LIVE 해제가 불가능한 상태**임을 ADR 스스로 인정.

**Kill switch / Safety control — 구현됨(스코프형).**
- `src/foundation/risk_gate/` — `SafetyScope`(GLOBAL/TENANT/ACCOUNT/PROVIDER/
  STRATEGY_DEPLOYMENT, 48번 스펙 §4), `activate_safety_control.py`,
  `deactivate_safety_control.py`. `fence_token` 낙관적 동시성 카운터
  (`src/foundation/risk_gate/adapters/postgres_repository.py:119-129`,
  `src/foundation/paper_control/application/submit_paper_intent.py:78-95` — kill switch
  발동 후 fence가 안 바뀌어도 활성 control 존재 여부를 별도로 재확인).
- 라우터 이중 경로: `POST /v1/foundation/risk-gate/safety-controls`(self-service, 일반
  유저도 자기 스코프에 한해)와 `POST /v1/foundation/risk-gate/admin/safety-controls`
  (GLOBAL/TENANT/PROVIDER는 `get_current_admin` 없이는 라우팅 자체 불가) —
  `src/api/routers/foundation/risk_gate.py:86-179`.
- kill switch 우선순위: Watchdog > Human > Circuit Breaker > (Debate, 미구현) —
  ADR-2026-08-29-E §설계 제약 2항에 명문화, `src/core/safety/circuit_breaker.py`,
  `watchdog.py`가 이 순서를 코드로 반영.

**"누가 paper→live를 뒤집을 수 있는가" — 서버 플래그+운영자 allowlist+토큰 스코프
다중조건 패턴은 없음.**
- LIVE 전환은 오직 (a) `execution.mode` DB 값과 (b) adapter 인스턴스 생성 파라미터
  (`demo_mode`)라는 **코드 레벨 상수적 게이트**로만 막혀 있고, 이를 여는 유일한 경로는
  "코드 수정 + 별도 ADR"이다. 즉 **런타임에 토글 가능한 "운영자 allowlist + 서버 플래그"
  방식이 아니라, 배포 시점에 코드 자체를 바꿔야 하는 방식** — QuantDinger류
  "환경변수/allowlist로 런타임 전환, 토큰 스코프로 이중 검증"과는 설계 철학이 다르다
  (더 보수적이지만 유연성은 없음).
- `Autonomy.LIMITED_LIVE`(mandate enum, `src/foundation/mandates/domain/models.py:24-28`)가
  존재하나 이는 "사용자가 위임하고 싶은 상한"을 표현할 뿐, 실제 LIVE 게이트와는 무관함을
  주석이 명시("mandate가 표현하는 상한일 뿐... 실제 LIVE 게이트는 여전히 별도 FROZEN
  영역이 막는다").

## 6. Rate Limiting / Request Size / Security Headers / CORS

**Rate limiting — 없음.**
- `slowapi`, 토큰버킷, 미들웨어 레벨 rate limiter **전무**. `RATE_LIMIT_EXCEEDED`
  에러코드(`src/api/contracts/error_codes.py:52,85,101`)는 정의돼 있으나 **실제로 발생시키는
  코드가 없다**(예약된 상수만 존재).
- L4 스펙 §R5, §97-99가 `RateLimitPolicy`(auth_login 10/60s per ip, mutation 10/60s per
  subject 등), `InMemoryTokenBucket`, `RateLimitMiddleware`를 신규 설계했으나 **전부
  미구현**("Rate limiting 전무"라고 스펙 자체가 현재 상태를 명시).

**Request size limit — 없음.** `TrustedHostMiddleware`, body size 제한, `Content-Length`
검증 등 매치 0건.

**보안 헤더 — 없음.** CSP/HSTS/X-Frame-Options/X-Content-Type-Options 등을 설정하는
미들웨어 없음(`main.py`에 CORS·RequestId 미들웨어만 등록).

**CORS — 구현됨(다소 관대한 설정).**
- `src/main.py:160-166` `CORSMiddleware`: `allow_origins=secrets.cors_allowed_origins`
  (환경설정), `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`.
  Origin 목록 자체는 제한되지만 메서드/헤더 와일드카드 + credentials 병행은 다소 느슨한
  조합.

## 설계됨(docs) vs 미구현 — 요약

`docs/design/13_multi_tenancy_auth_v1.4.md`, `15_api_spec_rbac_v1.6.md`,
`docs/specs/L4_platform_observability_tenancy_api_v1.0.md`를 훑은 결과, L4 스펙 문서
자체가 "현재 구현 갭"을 스스로 표로 정리해 두고 있다(§R1/R3/R5, §2 표). 이 문서 기준으로
**설계만 존재, 미구현**인 항목:

- `RequestContext`(8필드: trace_id/tenant_id/actor_subject_id/command_id/component 등),
  `RequestContextMiddleware`, `EventEnvelope`(이벤트 버스에 trace_id/tenant_id 전파)
- Tenant/Membership 모델(`tenant`, `tenant_membership` 테이블), `resolve_tenant_context()`,
  다중 테넌트(조직/가구) 지원 — 현재는 personal tenant(`tenant_id==user_id`)만 존재
- PostgreSQL RLS(`ENABLE ROW LEVEL SECURITY` + `CREATE POLICY tenant_isolation`),
  `SET LOCAL app.tenant_id` 세션 바인딩(`tenant_scope.py`)
- Refresh token/session 테이블(`auth_session`, refresh rotation, reuse 탐지) —
  `src/services/auth/session_repository.py`는 **설계만, 코드베이스에 없음**. 현재 JWT는
  단일 access token뿐, refresh 개념 자체가 없다.
- `src/core/security/rate_limit/`(policy.py, limiter.py), `RateLimitMiddleware`
- `src/core/security/break_glass.py`(비상권한 승인/소비)
- `Idempotency-Key` 헤더의 표준화된 의존성(`src/api/contracts/idempotency.py`,
  digest 대조, `Idempotency-Replayed` 응답 헤더) — 현재는 라우터마다 개별 구현
  (marketplace만 `with_idempotency` 사용, 나머지는 각자 방식)
- 로그인 실패 카운터의 원자적 UPDATE(현재 SELECT+1 비원자, 경합 가능)
- Prometheus `/metrics` 카디널리티 규율(tenant_id 라벨 금지 등) — 현재
  `src/core/observability/metrics_registry.py`는 있으나 이 규율 적용 여부 별도 확인 필요

## Gap Summary (비교 관점 — QuantDinger류 패턴 대비)

1. **에이전트/외부 AI 전용 API 표면·MCP 서버 — 완전히 없음.** 사람 웹 API와 분리된 게이트웨이,
   스코프가 좁은 에이전트 토큰 개념 자체가 코드베이스에 존재하지 않는다. `capability_tokens`
   테이블은 무관한 DevEngine 코딩-에이전트 시스템의 유물로, AIOS 트레이딩 API에 배선돼 있지
   않다.
2. **JWT에 jti 없음 → 서버측 revoke/logout이 불가능.** 로그아웃은 no-op이며, 탈취된 토큰은
   만료(60분 기본)까지 유효하다. Refresh token/세션 테이블도 없어 "짧은 access + 긴 refresh"
   패턴이 아니다.
3. **Idempotency-Key는 표준화되지 않고 라우터마다 제각각.** 공용 헤더 의존성이 없어 신규
   라우터 작성 시 재발명 위험(마켓플레이스 1곳만 `with_idempotency` 사용). tenant_id/
   expires_at 컬럼도 없어 키 정리(purge)가 불가능(무한 누적).
4. **테넌시가 사실상 "user_id를 tenant_id로 재명명"한 수준.** RLS 없음, 조직 단위 멀티
   테넌시 없음 — 애플리케이션 버그 시 DB 레벨 이중 방어가 전무.
5. **Trace/이벤트 상관관계가 HTTP 요청 하나 안에서만 유효.** 이벤트 버스·백그라운드 루프로
   내려가면 상관관계 ID가 끊긴다(`record_command_event`가 매번 새 UUID 생성) — 장애 재현/
   감사 추적이 어렵다.
6. **Rate limiting, request size 제한, 보안 헤더(CSP/HSTS 등)가 전무.** `RATE_LIMIT_EXCEEDED`
   에러코드만 예약돼 있고 실제 방어선은 없다 — DoS/브루트포스에 취약.
7. **Live-trading gate는 반대로 매우 보수적/견고하다.** 코드 레벨 하드 블록(mode 체크 +
   adapter sandbox 이중 신호 + 확장 메서드 별도 데코레이터) + ADR 기반 해제 절차. 다만
   "런타임 토글형 allowlist/서버 플래그" 방식이 아니라 "코드/ADR 변경 없이는 못 뒤집는" 방식이라
   유연성은 낮다(장점이자 QuantDinger류 대비 차이점).
8. **RBAC은 role 테이블이 아닌 2개 불리언 플래그**(is_verifier/is_platform_admin)뿐이라
   세분화된 스코프(예: read-only operator, per-strategy admin)를 표현할 수 없다.
9. **break-glass(비상권한)는 설계 문서에만 존재**, 코드 0건 — 운영자 비상조치 시 감사가능한
   승인/소비 절차가 없다.
