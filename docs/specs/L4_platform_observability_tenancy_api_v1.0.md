# L4 구현 명세 — 플랫폼: 관측성 · 테넌시/인증 · API 계약 · 엔지니어링 게이트 v1.0

> 템플릿: `docs/specs/_TEMPLATE.md`. 기준 HEAD `b2bbe9d`(커밋된 alembic head `b3f7e0c1a4d5`; 작업트리에는 타 세션의 미커밋 리비전 `5ed4921f9873`이 있어 이 문서의 M1~M7은 그 뒤에 직렬화한다).
> 이 문서는 도메인(주문·리스크·정산)이 아니라 **모든 도메인이 딛고 서는 바닥**을 다룬다.
> 여기서 정한 것은 이후 모든 L4 명세가 "이미 있는 것"으로 전제한다.

## 0. 문서 메타

| 항목 | 값 |
|---|---|
| status | DRAFT → 리프 PLT-01 머지 시 ACTIVE |
| owner role | Platform Engineering(관측성·보안·CI). FROZEN_PAPER_ONLY 경로는 건드리지 않는다 |
| supersedes | `07_logging_config_v1.3.md` §7.1(로그 필드 — 108번이 우선), `16_backend_signatures.md` §16.1의 `/auth/logout` Draft, `13_multi_tenancy_auth_v1.4.md` §13.4의 "RLS Draft" |
| depends on | 108(로그/메트릭 필드), 107(계약 버전), 106(스캐폴드), 105(동시성), 103 P0-02/P0-05/§8, 73 §2.1/§5/§8/§10, 15 §15.1/§15.3/§15.6, 13 §13.2/§13.3 |
| implemented by | §2 표의 "신규" 파일 전체 + 기존 파일 수정 목록(§2-A) |
| verification evidence | `tests/platform/**`(신규 트리), `tests/contract/**`, `tests/foundation/adversarial/trust/test_membership_isolation.py`, CI `quality.yml` 신규 스텝 5개 |

---

## 1. 기관급 요구 (왜 기초 수준으로는 부족한가)

자산운용사·기관이 이 네 영역에 요구하는 것과 현재 코드(`docs/FULL_AUDIT_2026-09-02.md`)의 격차:

| # | 기관 요구 | 근거 | 현재 코드 수준(감사 인용) | 격차 |
|---|---|---|---|---|
| R1 | **모든 요청이 끝까지 추적된다.** HTTP 진입 → 서비스 → 이벤트 버스 → 백그라운드 루프 → 감사 기록까지 하나의 `trace_id`로 grep 가능 | 108 §2, 103 P0-05 | §3: "`configure_logging` 호출됨(2e943c9) — trace_id·tenant_id 필드 관통은 §11 8단계로 남음". **진행 중(타 세션, 미커밋)**: `src/core/logging/request_context.py`(`request_id_var` ContextVar) + `src/api/middleware/request_id.py`(`RequestIdMiddleware`, `X-Request-ID` 왕복) + `schema.py`가 `correlation_id` 기본값으로 request_id를 채움. 그러나 이것은 8필드 중 1개(request_id)뿐이다 — `tenant_id`·`actor`·`component`·`event`·`duration_ms` 없음, 이벤트 버스·루프로 전파 안 됨, `record_command_event`는 호출마다 `uuid4()`로 **새** trace_id를 만들어 상관관계가 끊긴다(`evidence/application/record_command_event.py:57`), legacy `audit_log`에는 trace_id 컬럼 자체가 없다 | 타 세션의 request_id 축 위에 `RequestContext`(8필드)·이벤트 봉투·루프 바인딩·감사 컬럼을 얹는다(§2.1 — 기존 두 파일은 대체가 아니라 확장) |
| R2 | **수치 SLO와 알림.** "성공률을 측정한다" 수준이 아니라 메트릭 이름·임계·runbook까지 | 108 §3/§5, 73 §10 | §5: "관측성 미구현 — 메트릭 라이브러리 없음, 리스크 거부가 `logger.info`로만" | 메트릭 레지스트리·이름 상수·`/metrics`·alert rule·runbook 신규 |
| R3 | **테넌트 격리가 테스트로 증명된다.** 앱 버그가 있어도 DB가 타 테넌트 행을 돌려주지 않는다 | 13 §13.4, 73 §2.1 "no caller-supplied tenant filter is trusted" | §8: "전 경로 `user_id` 조건. RLS 없음". `TenantContext`는 `tenant_id == subject_id == user_id`, role `"OWNER"` 고정(`foundation_deps.py:114`) | tenant/membership 모델, RLS + `SET LOCAL app.tenant_id`, 앱 전용 role, 적대 테스트 신규 |
| R4 | **세션이 실제로 끝난다.** 로그아웃·정지·키 유출 시 토큰이 즉시 죽는다 | 73 §3.1 "invalidate sensitive sessions", 15 §15.2 | §8: "JWT `sub`+`exp`만. refresh·jti·revocation 없음 → **logout이 no-op**"(`routers/auth.py:47`) | jti·세션 테이블·refresh 회전·폐기 신규 |
| R5 | **인증 카운터는 원자적, 남용은 차단된다** | 15 §15.2 423/`retry_after_seconds`, 73 §8 rate limits | §8: "잠금 카운터 비원자(SELECT값+1). Rate limiting 전무" | 단일 조건부 UPDATE, 미들웨어 rate limiter 신규 |
| R6 | **비밀은 필요한 시간만 프로세스에 있고, 키는 회전되며, PAPER 런타임은 LIVE 비밀을 물리적으로 못 푼다** | 103 P0-02, 108 §2.1, 82 RT-08 | §8: "AES-256-GCM 단일 키, 암호문에 키 버전 없음. base64→BYTEA 이중 인코딩". `SecretBundle`은 SecretStr(양호). `get_decrypted()`가 평문 tuple 반환. 자격증명 등록/해지 `audit_log` 기록은 타 세션이 추가 중(미커밋 diff) | KeyRing(kid)·scope별 KEK·secret_ref·회전 스크립트 신규 |
| R7 | **API 계약이 깨질 수 없다.** 응답 봉투·에러 taxonomy·페이지네이션이 하나이고, 필드 제거는 버전 증가 없이 머지 불가 | 15 §15.1/§15.3, 107 §3/§6 | §8: "응답 봉투·`error_code`·페이지네이션 래퍼 어느 라우터도 미채택. 전역 exception_handler 0건. `page=0` → OFFSET 음수 500" | 봉투·핸들러·OpenAPI 스냅샷 호환성 게이트 신규 |
| R8 | **금전 엔드포인트 멱등성이 한 규격이다** | 15 §15.1, 105, 73 §6 규칙 6 | §2-A: 구매 멱등키는 수정됨(claim-first). 그러나 헤더 규격·digest·스코프가 라우터마다 다르다: `marketplace.py`(헤더+user_id), `admin.py:184`(헤더만), `paper_control.py`(body 필드), `executions.py`(없음) | 공용 `require_idempotency_key` + digest 컬럼 신규 |
| R9 | **CI 게이트가 유일한 머지 경로이며, 로컬 결과가 CI와 같다** | 103 §8, 08 §8.7 | §1: "2차 114 errors, 3차 7 failed — 전부 환경 간섭". §9: 커버리지 임계 없음, `type: ignore` 178건 중 160건이 거래소 믹스인(현재 grep 226건), ruff 5규칙군만 | 세션별 DB(있음, 보강)·커버리지 ratchet·Protocol·마이그레이션 체인 검사·릴리스 게이트 신규 |

**설계 원칙 3개** (이 문서 전체를 관통):
1. 컨텍스트는 **주입되지 않고 전파된다** — 함수 시그니처에 `trace_id`를 추가하지 않는다. `ContextVar` 하나가 미들웨어·이벤트 버스·루프에서 바인딩되고, 로거·감사·메트릭은 그것을 읽는다. 기존 40여 서비스의 시그니처를 건드리지 않는 유일한 방법이다.
2. 격리는 **코드가 아니라 DB가 강제한다** — `WHERE user_id = $1`은 계속 쓰되, RLS가 이중 방어선이다. 적대 테스트는 WHERE 없이 조회해서 0행을 확인한다.
3. **호환성은 파일 diff가 판정한다** — OpenAPI 스냅샷과 계약 fixture가 리뷰어 대신 판단한다(107 §6).

---

## 2. 모듈 분해 (최소단위)

Zone: `src/**` 신규 파일은 전부 SCAFFOLD(`.aios-zone` 기존 패턴 `src/core/**`, `src/api/**`에 포함되므로 매니페스트 수정 불필요). `tests/**`, `scripts/**`, `config/**`, `docs/**`는 OPEN. FROZEN_PAPER_ONLY(`src/core/{strategy,portfolio,risk,executor}`)는 **수정하지 않는다** — 리스크 결정 메트릭은 호출부(`order_service/gate.py`, `foundation/risk_gate`)에서 계측한다.

### 2.1 (A) 관측성

| 파일 경로 | 신규/기존 | 단일 책임 | 공개 계약 | 의존(포트) | 상한 |
|---|---|---|---|---|---|
| `src/core/logging/request_context.py` | 기존(타 세션, 미커밋) | `request_id_var: ContextVar[str\|None]`, `get_current_request_id()` | PLT-01 이후 `get_current_request_id()`는 `current().request_id`를 반환하는 1줄 shim으로 바뀐다(타 세션 커밋 후, 시그니처 불변) | — | 30 |
| `src/core/observability/context.py` | 신규 | 요청 컨텍스트 ContextVar(8필드) | `class RequestContext(BaseModel, frozen)`: `trace_id: UUID, request_id: str, tenant_id: UUID\|None, actor_subject_id: UUID\|Literal["system"], command_id: UUID\|None, component: str`. `def current() -> RequestContext`, `@contextmanager def bind(**overrides) -> Iterator[RequestContext]`, `def bind_system(component: str) -> ...`(actor="system", 새 trace_id). 내부적으로 `request_id_var`도 같은 값으로 set하여 타 세션 코드와 항상 일치 | request_context | 120 |
| `src/api/middleware/request_id.py` | 기존(타 세션, 미커밋) | `RequestIdMiddleware` — `X-Request-ID` 왕복 | 유지. PLT-05가 `RequestContextMiddleware`를 등록하면 `main.py`에서는 이것을 **등록하지 않는다**(둘 다 등록하면 request_id를 두 번 set — 무해하지만 헤더 대소문자 `X-Request-ID` 하나로 통일) | — | 45 |
| `src/api/middleware/request_context.py` | 신규 | HTTP 진입점 바인딩(request_id의 상위 집합) | `class RequestContextMiddleware(RequestIdMiddleware)`: 부모의 request_id 처리 위에 `traceparent`(W3C, 있으면 trace_id 채택, 없으면 uuid4) 읽기 → `bind()` → 응답 헤더 `X-Request-ID`, `X-Trace-Id` → 종료 시 `event=http_request_completed` 로그 1줄(`duration_ms`, `status`, `route`) + 메트릭 | context, metrics | 140 |
| `src/core/observability/tenant_binding.py` | 신규 | 인증 후 tenant/actor 재바인딩 | `def rebind_tenant(ctx: TenantContext) -> None` — `get_tenant_context` 성공 직후 호출. 이미 바인딩된 trace_id에 다른 tenant_id가 오면 `tenant_mismatch` 카운터 증가 + `warn`(108 §5-4) | context, metrics | 60 |
| `src/core/logging/fields.py` | 신규 | 108 §2 필수 필드 스키마 | `class StructuredLogLine(BaseModel)`: `timestamp, level: Literal["debug","info","warn","error"], trace_id, tenant_id, actor_subject_id, command_id, component, event, duration_ms, message, extra`. `def from_record(record: LogRecord, ctx: RequestContext) -> StructuredLogLine` | context | 100 |
| `src/core/logging/schema.py` | 기존 수정(타 세션 미커밋 diff — `get_current_request_id()` 기본값 — 위에 얹음) | `JSONLinesFormatter`가 `fields.py`로 위임, `LogEntry`는 하위호환용 유지(07 §7.1 소비자 없음 확인 후 v2에서 삭제). `correlation_id`는 `request_id`의 별칭으로 계속 출력(타 세션 테스트 `tests/unit/core/logging/test_schema.py` 무수정 통과) | `configure_logging(level, *, redact: bool = True)` | fields, redaction | 100 |
| `src/core/logging/redaction.py` | 신규 | 108 §2.1 금지 필드 차단 | `DENY_KEYS = frozenset({"api_key","api_secret","secret","password","totp","token","authorization","private_key","raw_payload","answers"})`, `def redact(payload: Mapping) -> dict`(키 부분일치·대소문자 무시, 값은 `"<redacted>"`), `class RedactionFilter(logging.Filter)`. 64자 hex·`eyJ` 접두 문자열 값도 마스킹 | 없음 | 90 |
| `src/core/observability/metric_names.py` | 신규 | 메트릭 이름 단일 출처 | §7.2 표의 상수 전부. `def to_prom(name: str) -> str`(`.`→`_`) | 없음 | 80 |
| `src/core/observability/metrics.py` | 신규 | 레지스트리 포트 + prometheus 어댑터 | `class MetricsPort(Protocol)`: `counter(name, labels) -> None`, `observe(name, value, labels)`, `gauge(name, value, labels)`. `class PrometheusMetrics(MetricsPort)`, `class NullMetrics`. `def metrics() -> MetricsPort`(프로세스 싱글턴, 테스트는 `set_metrics(NullMetrics())`) | `prometheus-client>=0.20`(신규 의존) | 160 |
| `src/core/observability/instrument.py` | 신규 | 계측 데코레이터 | `def observe_command(component: str, event: str)` — async 함수를 감싸 `duration_ms` 로그 + `*.duration_seconds` + `*.count_total{outcome}`; 예외 클래스명을 `outcome=error` 라벨·`event=<event>_failed`로. `def timed(metric_name)` | context, metrics | 110 |
| `src/core/observability/loop_health.py` | 신규 | 백그라운드 루프 건강 | `class LoopHealth`: `record_tick(loop: str, ok: bool, duration_s: float)`, `last_success_age(loop) -> float`, `snapshot() -> dict[str, LoopStatus]`. `LoopStatus(last_success_at, consecutive_failures, interval_sec)` | metrics | 90 |
| `src/core/safety/base_loop.py` | 기존 수정 | 매 tick `bind_system(f"safety.{name}")` + `LoopHealth.record_tick` | `run_safety_loop(name, interval_sec, tick_fn, *, health: LoopHealth)` | context, loop_health | 60 |
| `src/core/event_bus/envelope.py` | 신규 | 이벤트 봉투 | `class EventEnvelope(BaseModel)`: `event_id: UUID, topic, trace_id, tenant_id, actor_subject_id, occurred_at, schema_version="v1", payload: Any`. `def wrap(topic, payload) -> EventEnvelope`(현재 컨텍스트에서), `def unwrap(obj) -> tuple[EventEnvelope\|None, Any]`(봉투 아니면 그대로 — 전환기 호환) | context | 80 |
| `src/core/event_bus/in_process.py` | 기존 수정 | `publish()`가 `wrap()`, 워커가 핸들러 호출 전 `bind(trace_id=env.trace_id, ...)`; `queue_depth` gauge·`handler.count_total` | 시그니처 불변 | envelope, metrics | 300(현재 ~230) |
| `src/core/logging/audit_log.py` | 기존 수정 | `trace_id`·`component` 컬럼 기록 | `record_audit_log(conn, *, ..., trace_id: UUID\|None = None)` — None이면 `current().trace_id` | context | 70 |
| `src/foundation/evidence/application/record_command_event.py` | 기존 수정 | `trace_id=uuid4()` → `current().trace_id` | 시그니처 불변 | context | 70 |
| `src/api/routers/health.py` | 신규 | liveness/readiness/metrics | `GET /healthz`→`{"status":"ok"}`(DB 미접촉). `GET /readyz`→ `ReadinessReport`(§3.2): pool ping, `alembic_version == 기대 head`, event bus running, 루프별 `last_success_age < 3×interval`, 하나라도 실패 시 503. `GET /metrics`→ Prometheus text, `AIOS_METRICS_TOKEN` 헤더 또는 loopback만 | pool, loop_health, metrics | 150 |
| `config/observability/alert_rules.yaml` | 신규 | Prometheus rule 형식 알림 정의(§7.4) | — | — | 150 |
| `docs/runbooks/RB-01..RB-08.md` | 신규 | §7.5 runbook 8개 | — | — | 각 80 |

### 2.2 (B) 테넌시 · 인증 · 키 관리

| 파일 경로 | 신규/기존 | 단일 책임 | 공개 계약 | 의존 | 상한 |
|---|---|---|---|---|---|
| `src/foundation/trust/domain/models.py` | 기존 수정 | `Tenant(id, kind: TenantKind, state, created_at)`, `Membership(id, tenant_id, subject_id, role: MembershipRole, state: MembershipState, revision, created_at)` 추가 | dataclass | — | 120 |
| `src/foundation/trust/domain/rules/membership.py` | 신규(106 §4-2 승격: `rules.py`가 consent와 membership을 섞게 되므로) | 73 §3.1 전이표·last-owner 규칙 | `def is_membership_transition_allowed(from_: MembershipState, to: MembershipState, *, actor_role: MembershipRole) -> bool`, `def would_remove_last_owner(active_owners: int, target_is_owner: bool, to: MembershipState) -> bool`, `def role_can(role, action: Literal["read","mutate","admin"]) -> bool` | 없음 | 90 |
| `src/foundation/trust/domain/rules.py` | 기존 → `rules/consent.py`로 이동, `rules/__init__.py`가 재수출 | 기존 import 경로 유지 | — | — | 60 |
| `src/foundation/trust/ports/membership_repository.py` | 신규 | membership 저장소 포트 | `class MembershipRepository(Protocol)`: `get_active_membership(tenant_id, subject_id) -> Membership\|None`, `list_memberships_for_subject(subject_id) -> list[Membership]`, `count_active_owners(tenant_id) -> int`, `insert_membership(...) -> Membership`, `update_conditional_membership_state(membership_id, *, expected_state, expected_revision, new_state) -> Membership`(105 조건부), `get_personal_tenant(subject_id) -> Tenant\|None`, `insert_tenant(...)` | models | 80 |
| `src/foundation/trust/adapters/postgres_membership_repository.py` | 신규 | asyncpg 구현 | 위 Protocol. 상태 전이는 `conditional_update` + `revision = revision + 1` | conditional_write | 200 |
| `src/foundation/trust/application/grant_membership.py` | 신규 | 73 §4 `GrantMembership` | `async def grant_membership(repo, ctx: TenantContext, *, subject_id, role) -> MembershipView` — 규칙: 호출자 ACTIVE OWNER/ADMIN, `ctx.mfa_verified` 필수(`AUTH_MFA_REQUIRED`), 중복 활성이면 `STATE_DUPLICATE_COMMAND`. 감사 `trust.membership_granted.v1` | membership repo, evidence | 100 |
| `src/foundation/trust/application/suspend_membership.py` | 신규 | `SuspendMembership` | 동일 패턴 + last-owner 거부 `STATE_LAST_OWNER`; 부작용: 해당 subject의 `auth_session` 전부 revoke(포트 `SessionRevoker`) | membership repo, session revoker | 90 |
| `src/foundation/trust/application/revoke_membership.py` | 신규 | `RevokeMembership` | 동일 | — | 80 |
| `src/foundation/trust/application/resolve_tenant_context.py` | 신규 | 인증 사용자 + 요청된 tenant → `TenantContext` | `async def resolve_tenant_context(repo, *, user: User, requested_tenant_id: UUID\|None, mfa_verified: bool) -> TenantContext` — 미지정 시 personal tenant(id == user_id). membership 없음/비활성 → `TenantMismatchError`(403 `AUTH_TENANT_MISMATCH`) | membership repo | 80 |
| `src/foundation/trust/contracts/v1.py` | 기존 수정(MINOR, 107 §3.2) | optional 필드 추가: `membership_id: UUID\|None = None`, `tenant_kind: str = "PERSONAL"`, `auth_level: Literal["PASSWORD","MFA_VERIFIED"] = "PASSWORD"`. `MembershipView`, `TenantKind`, `MembershipRole`, `MembershipState` enum 추가 | pydantic | — | 140 |
| `src/api/foundation_deps.py` | 기존 수정 | `get_tenant_context`가 `resolve_tenant_context` 사용, `X-Tenant-Id` 헤더 optional, 성공 시 `rebind_tenant()` | 시그니처 불변 | resolve, tenant_binding | 130 |
| `src/api/routers/foundation/trust_memberships.py` | 신규(106 §4-4: trust.py 엔드포인트 5개 도달) | `GET /v1/foundation/trust/memberships`(cursor), `POST .../memberships`, `POST .../memberships/{id}:suspend`, `:revoke` | 봉투(§2.3) | 150 |
| `src/services/auth/tokens.py` | 신규 | JWT 발급/검증(claims 고정) | `class AccessClaims(BaseModel)`: `sub: UUID, tid: UUID, jti: UUID, sid: UUID, iat, exp, auth_level, kid: str`. `class TokenIssuer`: `issue_access(user, session) -> str`, `issue_refresh() -> tuple[str, str]`(평문, sha256). `class TokenVerifier.verify(token) -> AccessClaims`(kid별 키, `exp`·`nbf` 검증, alg 고정 HS256 — 알고리즘 협상 금지) | `JWT_SIGNING_KEYS="k1:hex,k2:hex"`, `JWT_ACTIVE_KID` | 150 |
| `src/services/auth/session_repository.py` | 신규 | `auth_session` 테이블 CRUD | `insert_session(user_id, tenant_id, refresh_hash, ip_hash, ua_hash, expires_at) -> Session`, `get_active(session_id) -> Session\|None`, `rotate_refresh(session_id, *, expected_hash, new_hash) -> Session`(조건부 UPDATE — 재사용 감지 시 `RefreshReuseDetected`), `revoke(session_id, reason)`, `revoke_all_for_user(user_id, reason) -> int` | conditional_write | 160 |
| `src/services/auth/login.py` | 신규 | 로그인 = authenticate + 세션 생성 + 토큰 쌍 | `async def login(auth: AuthService, sessions, issuer, *, email, password, totp_code, client: ClientInfo) -> LoginResult(access_token, refresh_token, expires_in, session_id)` | AuthService(기존) | 90 |
| `src/services/auth/refresh.py` | 신규 | refresh 회전 | `async def refresh(sessions, issuer, *, refresh_token) -> LoginResult` — hash 대조 → 회전; 재사용 감지 시 세션 revoke + 감사 `auth.refresh_reuse_detected` | session repo | 80 |
| `src/services/auth/logout.py` | 신규 | 세션 폐기 | `async def logout(sessions, *, session_id, user_id) -> None`, `async def logout_all(sessions, *, user_id) -> int` | — | 50 |
| `src/services/auth/lockout.py` | 신규 | 원자적 실패 카운터 | `async def register_failed_attempt(conn, user_id) -> LockoutState(attempts, locked_until)` — 단일 SQL: `UPDATE users SET failed_login_attempts = failed_login_attempts + 1, locked_until = CASE WHEN failed_login_attempts + 1 >= $2 THEN now() + $3 ELSE locked_until END WHERE user_id = $1 RETURNING failed_login_attempts, locked_until`. `async def reset(conn, user_id)`. `def retry_after_seconds(locked_until, now) -> int` | asyncpg | 70 |
| `src/services/auth_service.py` | 기존 수정 | `_register_failed_attempt` → `lockout.register_failed_attempt`; 잠금 시 `AccountLockedError(retry_after_seconds)`(→423); `issue_token` deprecated shim → `TokenIssuer` | `AuthError` 유지 | lockout | 272→250 |
| `src/api/deps.py` | 기존 수정 | `get_current_user`가 `TokenVerifier` + `session_repository.get_active(sid)`(revoked → 401 `AUTH_SESSION_REVOKED`); `User`에 `session_id`, `auth_level` 부착 | — | tokens, sessions | 130 |
| `src/api/routers/auth.py` | 기존 수정 | `/login`이 토큰 쌍 반환, `/refresh` 신설, `/logout`(현재 세션), `/logout-all` | `TokenPairResponse` | login/refresh/logout | 120 |
| `src/core/security/rate_limit/policy.py` | 신규 | 73 §8 수치의 단일 출처 | `class RateLimitPolicy(BaseModel)`: `name, limit, window_seconds, key: Literal["ip","subject","tenant"]`. `POLICIES = {"auth_login": (10, 60, "ip"), "read": (120, 60, "subject"), "mutation": (10, 60, "subject"), "admin": (30, 60, "tenant"), "metrics": (30, 60, "ip")}` | — | 50 |
| `src/core/security/rate_limit/limiter.py` | 신규 | 토큰 버킷 포트 + 인메모리 구현 | `class RateLimiter(Protocol)`: `async def acquire(policy, key) -> Decision(allowed, retry_after_s, remaining)`. `class InMemoryTokenBucket(RateLimiter)`(단일 프로세스 — 05번 §5.2와 동일하게 분산은 Redis 어댑터로 교체, §10) | policy | 110 |
| `src/api/middleware/rate_limit.py` | 신규 | 라우트 → 정책 매핑 + 429 | `RateLimitMiddleware(app, limiter, resolve_policy: Callable[[Request], RateLimitPolicy\|None])`; 응답 헤더 `RateLimit-Limit/Remaining/Reset`, `Retry-After`; 초과 시 §2.3 봉투 `RATE_LIMIT_EXCEEDED` | limiter, envelope | 100 |
| `src/core/security/key_ring.py` | 신규 | 키 버전 관리 | `class KeyRing`: `from_env(scope: SecretScope) -> KeyRing`(`CREDENTIAL_ENCRYPTION_KEYS_PAPER="v1:hex64,v2:hex64"`, `CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER="v2"`; LIVE 변수는 PAPER 런타임에 **없어야** 하며 있으면 기동 거부 `AIOS_RUNTIME_MODE=PAPER`), `active_kid -> str`, `key(kid) -> bytes`, `kids() -> tuple[str,...]`. 레거시 단일 `CREDENTIAL_ENCRYPTION_KEY`는 `kid="legacy"`로 흡수 | — | 100 |
| `src/core/security/encryption.py` | 기존 수정 | 키 버전 있는 포맷 | `encrypt(plaintext, ring) -> str` → `"aios1$<kid>$<b64(nonce+ct)>"`, `decrypt(token, ring) -> str`(접두 없는 레거시 토큰은 `legacy` kid로 복호). AAD = kid. 기존 `encrypt(plaintext, key: str)` 시그니처는 `legacy_encrypt`로 이름 변경 후 호출부 3곳 이관 | key_ring | 90 |
| `src/core/security/envelope.py` | 신규 | 봉투 암호화(레코드별 DEK) | `class SealedRecord(BaseModel)`: `kid, wrapped_dek: bytes, nonce, ciphertext`. `seal(plaintext: bytes, ring) -> SealedRecord`, `open_(rec, ring) -> bytes`, `rewrap(rec, ring) -> SealedRecord`(DEK 재래핑만 — 본문 재암호화 없이 회전) | key_ring | 110 |
| `src/core/security/secret_ref.py` | 신규 | 불투명 참조 | `class SecretRef(BaseModel, frozen)`: `scope: SecretScope, kind: Literal["exchange_credential","mfa_secret","withdrawal_dest"], id: str, kid: str`. `def parse(s) -> SecretRef`, `__str__` → `secref://paper/exchange_credential/123@v2`. 로그·이벤트·API에는 이 문자열만 | — | 60 |
| `src/core/security/secret_handle.py` | 신규 | 평문 생존기간 최소화 | `class SecretHandle`: `async with resolver.open(ref) as h: h.api_key_bytes`; `__aexit__`에서 `bytearray` zero-fill + 참조 해제; `expose()` 호출 시 `aios.security.secret_decrypt.count_total{scope,kind}` 증가. 정직한 한계: Python `str`은 zeroize 불가 → 어댑터 생성자에 `bytes`를 넘기고 어댑터가 서명 시점에만 디코딩(§10-3) | metrics | 80 |
| `src/services/exchange_credential_service.py` | 기존 수정 | `scope` 컬럼·`key_id` 기록, BYTEA 이중 인코딩 제거(`bytes` 직접 저장), `get_decrypted()` → `get_secret_ref()`; 복호는 `credential_resolver`만 | `register(..., scope: SecretScope = PAPER)` | envelope, secret_ref | 188→200 |
| `src/services/credential_resolver.py` | 기존 수정 | `SecretRef` → `SecretHandle` → 어댑터 생성. 캐시 키에 `scope` 포함. LIVE ref는 `AIOS_RUNTIME_MODE=PAPER`에서 `FrozenZoneLiveModeBlockedError` | — | secret_handle | 120 |
| `scripts/rotate_credential_keys.py` | 신규 | 회전 배치(PAPER 고정) | `python -m scripts.rotate_credential_keys [--batch-size 100] [--max-rows N] [--dry-run]`(저장소 루트에서 모듈 실행; scope 인자 없음 — LIVE는 조회 대상에서 제외, PLT-33 §10-8): `WHERE scope='PAPER' AND key_version <> active_kid` 행을 행당 트랜잭션에서 `FOR UPDATE` → 옛 kid로 복호 → 새 kid로 재암호화 → `UPDATE ... WHERE id=$1 AND scope='PAPER' AND key_version=$old`(105 조건부) → 같은 트랜잭션에서 감사 `security.key_rotated`(행당 1건, from_kid/to_kid만). `--dry-run`은 대상 행 수만 출력. 행마다 `aios.security.key_rotation.count_total{scope,outcome}`(failure는 복호·재암호화·UPDATE·감사 INSERT 오류 모두 포함). `exchange_credentials`는 PLT-31 직접암호화 포맷(wrapped_dek 없음)이라 `envelope.rewrap` 대신 decrypt→re-encrypt(PM 승인 task-1542; 봉투 전환은 별도 리프·CA ADR). 평문은 bytearray 버퍼에만 두고 zero-fill, 예외는 원인 체인 없이 행 id·kid·stage·예외 타입명만 재포장 | encryption, key_ring | 220 |
| `src/core/db/tenant_scope.py` | 신규 | RLS 세션 변수 바인딩 | `@asynccontextmanager async def tenant_transaction(pool, tenant_id: UUID\|None) -> AsyncIterator[asyncpg.Connection]` — `BEGIN; SET LOCAL app.tenant_id = $1`(None이면 `''` → 정책이 시스템 행만 허용), 종료 시 COMMIT/ROLLBACK. `async def system_transaction(pool)`(`app.role='system'`) | asyncpg | 70 |
| `src/db/roles.sql` | 신규 | 앱 role 정의(마이그레이션 밖 — 환경별 1회) | `CREATE ROLE aios_app LOGIN NOBYPASSRLS; GRANT ... ; REVOKE UPDATE, DELETE ON audit_log, audit_event, wallet_transactions FROM aios_app` — §8 "REVOKE는 소유자 role에 무력" 해소 | — | 60 |
| `src/core/security/break_glass.py` | 신규 | 비상 권한 | `class BreakGlassGrant(id, admin_id, approved_by, reason, scope: Literal["kill_switch_override","tenant_read","credential_revoke"], expires_at ≤ 60min, used_at)`. `async def request_grant(...)`, `async def approve_grant(...)`(approver ≠ requester, 둘 다 `auth_level=MFA_VERIFIED`), `async def consume(conn, grant_id, admin_id) -> BreakGlassGrant`(조건부 UPDATE `used_at IS NULL`). 감사 `security.break_glass_used`, 메트릭 | conditional_write | 140 |
| `src/api/admin_deps.py` | 기존 수정 | `get_current_admin`은 `auth_level == "MFA_VERIFIED"` 필수(`AUTH_MFA_REQUIRED`); `require_break_glass(scope)` 의존성(`X-Break-Glass-Grant` 헤더) | — | break_glass | 80 |

### 2.3 (C) API 계약

| 파일 경로 | 신규/기존 | 단일 책임 | 공개 계약 | 의존 | 상한 |
|---|---|---|---|---|---|
| `src/api/contracts/envelope.py` | 신규 | 15 §15.1 봉투 | `class ApiResponse(BaseModel, Generic[T])`: `data: T, meta: Meta\|None`. `class Meta`: `trace_id: UUID, as_of: datetime, page: PageMeta\|None`. `class ApiError(BaseModel)`: `error_code: str, message: str, details: dict = {}, trace_id: UUID, retry_after_seconds: int\|None = None`. `def ok(data, *, page=None) -> ApiResponse` | context | 80 |
| `src/api/contracts/error_codes.py` | 신규 | 에러 taxonomy 단일 출처 | `class ErrorCode(str, Enum)` §3.3 표 전부 + `HTTP_STATUS: dict[ErrorCode, int]` + `RETRYABLE: frozenset[ErrorCode]`. 도메인별 접두 `AUTH_/AUTHZ_/VALIDATION_/STATE_/INTEGRITY_/POLICY_/RISK_/EXCHANGE_/RATE_LIMIT_/INTERNAL_/DEPENDENCY_` 외 금지(테스트로 강제) | — | 120 |
| `src/api/contracts/exception_mapping.py` | 신규 | 도메인 예외 → ErrorCode | `EXCEPTION_MAP: list[tuple[type[Exception], ErrorCode]]`(선언 순서 = 우선순위; 서브클래스 먼저). `def map_exception(exc) -> tuple[ErrorCode, str, dict]` | error_codes, 도메인 예외 import | 120 |
| `src/api/contracts/handlers.py` | 신규 | 전역 exception handler 4종 | `install_exception_handlers(app)`: `HTTPException`(detail 문자열 → `INTERNAL_LEGACY_DETAIL`이 아니라 상태코드별 기본 코드 매핑), `RequestValidationError`→400 `VALIDATION_INVALID_FIELD`(details.fields), `MihwaError`/도메인 예외→`map_exception`, `Exception`→500 `INTERNAL_ERROR`(메시지 고정, 원인은 `error` 로그만). 모든 응답에 `trace_id` | envelope, mapping | 130 |
| `src/api/contracts/pagination.py` | 신규 | 페이지/커서 | `class PageParams(BaseModel)`: `page: int = Field(1, ge=1), size: int = Field(20, ge=1, le=100)`; `offset` 프로퍼티. `class CursorParams`: `cursor: str\|None, limit: int = Field(50, ge=1, le=100)`. `class PageMeta`: `total: int\|None, page, size, next_cursor: str\|None`. `def encode_cursor(**keys) -> str`(base64url JSON + HMAC), `decode_cursor` | — | 100 |
| `src/api/contracts/idempotency.py` | 신규 | 금전 POST 헤더 규격 | `async def require_idempotency_key(request, ctx=Depends(get_tenant_context)) -> IdempotencyScope(key: str, tenant_id, subject_id, route: str, digest: str)` — 헤더 `Idempotency-Key`(16~128자, `[A-Za-z0-9_-]`) 필수 아니면 400 `VALIDATION_IDEMPOTENCY_KEY_REQUIRED`; digest = sha256(정규화 JSON body). `async def run_idempotent(pool, scope, compute) -> tuple[int, dict]` — `with_idempotency` 위에 digest 대조(불일치 409 `INTEGRITY_IDEMPOTENCY_CONFLICT`), 재생 시 응답 헤더 `Idempotency-Replayed: true` | idempotency(기존) | 130 |
| `src/core/idempotency.py` | 기존 수정 | `tenant_id`·`request_digest`·`expires_at` 저장, 만료 행 정리 `purge_expired(pool)` | `with_idempotency(pool, key, compute, *, tenant_id, digest)` | — | 93→140 |
| `src/api/versioning.py` | 신규 | `/api/v1` 마운트·레거시 alias | `def mount_v1(app, routers)`: 모든 라우터를 `/api/v1` 아래 등록, 기존 경로(`/auth`, `/v1/foundation/...`)는 alias로 유지하되 응답 헤더 `Deprecation: true`, `Sunset: <date>`(107 §4 전환 기간 ≥ 1 배포 주기) | — | 80 |
| `contracts/openapi/v1.json` | 신규(저장소 루트 `contracts/`, 107 §5 registry 위치) | OpenAPI 스냅샷(=계약) | `scripts/export_openapi.py`가 생성. 커밋 대상 | — | — |
| `scripts/check_openapi_compat.py` | 신규 | 107 §3 MAJOR 판정 자동화 | 스냅샷 vs 현재 app: 경로/메서드 제거, 응답 필드 제거, 응답 필드 타입 변경, 요청 required 추가, enum 값 제거 → **FAIL**(exit 1). 통과 조건: 없음 또는 `contracts/openapi/v2.json`이 함께 추가되고 v1 스냅샷은 불변 | — | 200 |
| `src/api/routers/*.py`(레거시 15개) | 기존 수정 | 반환을 `ApiResponse`로, `HTTPException(status, str)` → 도메인 예외 그대로 raise(핸들러가 매핑), `page/page_size` → `PageParams` | — | — | 각 ≤300 |

### 2.4 (D) 엔지니어링 플랫폼

| 파일 경로 | 신규/기존 | 단일 책임 | 공개 계약 | 상한 |
|---|---|---|---|---|
| `tests/support/db.py` | 신규 | 세션·워커별 DB | `session_database_url(worker_id) -> str`: `TEST_DATABASE_URL`을 템플릿으로 `aios_test_<session>_<worker>`를 `CREATE DATABASE ... TEMPLATE` 복제(마이그레이션 재실행 없이 ~1초). `@pytest.fixture async def tx_conn(pool)`: 트랜잭션 열고 테스트 끝에 ROLLBACK(단일 커넥션 테스트용) | 120 |
| `tests/conftest.py` | 기존 수정 | xdist `worker_id` 픽스처 연동, `set_metrics(NullMetrics())`, `RateLimiter`를 무제한으로 override | 150 |
| `scripts/setup_test_db.py` | 기존 수정 | `--template` 옵션: `aios_test_template` 생성·마이그레이션 후 이후 세션은 복제 | 120 |
| `scripts/coverage_ratchet.py` | 신규 | 커버리지 단조 증가 게이트 | `coverage-baseline.txt`(정수 %) 읽어 `coverage.xml` line-rate와 비교: 미달 FAIL; 초과 시 baseline 파일 갱신 diff를 출력하고 "PR에 커밋하라" 안내(자동 커밋 안 함). 첫 baseline = 첫 CI 측정치 − 0 | 80 |
| `src/exchanges/common/http_client.py` | 신규 | 믹스인 타입 계약 | `class SignedRequestClient(Protocol)`: `async def _request(self, method: str, path: str, *, params: Mapping[str, Any]\|None = None, body: Mapping[str, Any]\|None = None) -> dict[str, Any]`; `_demo_mode: bool`; `_capabilities: ExchangeCapability`. KIS/NH는 `_request`가 `tr_id`를 받으므로 `class KisRequestClient(SignedRequestClient)`로 확장 | 60 |
| `src/exchanges/{bitget,kis,nh}/*_mixin.py`(31개) | 기존 수정 | 각 메서드 `self: SignedRequestClient` 주석, `# type: ignore[attr-defined]` 제거 | 파일당 상한 300 유지 |
| `pyproject.toml` | 기존 수정 | `[tool.mypy] warn_unused_ignores = true`; ruff `select += ["S","BLE","ARG","PGH","T20"]`(기존 `noqa: BLE001/S608/ARG002`가 실효되게), `per-file-ignores`로 tests의 S101 허용; `[tool.coverage.report] fail_under`는 쓰지 않고 ratchet 스크립트가 판정 | — |
| `scripts/check_type_ignore_budget.py` | 신규 | `type: ignore` 예산 | `type-ignore-budget.txt`(정수) 초과 시 FAIL, 감소 시 파일 갱신 안내(coverage와 동일 ratchet). 시작값 = 현재 226, 목표 ≤ 20(§9 PLT-40) | 60 |
| `scripts/check_migration_chain.py` | 신규 | 마이그레이션 체인 정책 | (1) head 정확히 1개 (2) 새 리비전(`git diff --name-only origin/main`)의 `down_revision`이 base의 head와 일치 (3) merge 리비전(`down_revision`이 tuple) 금지 — 예외: 커밋 트레일러 `Migration-Merge-Approved: <PM>`. (4) `upgrade`/`downgrade` 양쪽 정의 (5) 파일명 `<rev>_<snake>.py` | 120 |
| `scripts/check_zone_diff.py` | 신규 | PR diff 기반 zone 게이트 | 변경 파일이 FROZEN → FAIL. FROZEN_PAPER_ONLY → 커밋 트레일러 `Zone-Approval: <ADR 또는 PM 세션 id>` 없으면 FAIL. 기존 `check_zone_manifest.py`는 그대로 | 90 |
| `.gitleaks.toml` | 신규 | 프로젝트 규칙 | 64자 hex(`CREDENTIAL_ENCRYPTION_KEYS_*`), `secref://`는 allowlist, Bitget/KIS 키 형태, `.env.example` 빈 값 allowlist | — |
| `config/release_gates.yaml` + `scripts/check_release_gate.py` | 신규 | 103 §8 단계별 증거 체크리스트 | `--stage internal_paper` 등: 각 stage의 `required_evidence`(파일 경로·CI 체크 이름·문서 status)를 검사 | 100 |
| `.github/workflows/quality.yml` | 기존 수정 | 스텝 추가: `check_migration_chain`, `check_zone_diff`, `check_openapi_compat`, `check_type_ignore_budget`, `coverage_ratchet`; pytest `-n auto`(xdist) | — |
| `docs/TESTING.md` | 기존 수정 | 템플릿 DB·xdist·트레일러 규칙 | — |

### 2.5 마이그레이션(신규 7개 — 리비전 id는 PM이 부여, parent는 착수 시점 head)

| ID | 파일명 접미 | DDL 요지 | downgrade |
|---|---|---|---|
| M1 | `audit_log_trace_id` | `ALTER TABLE audit_log ADD COLUMN trace_id UUID, ADD COLUMN component VARCHAR(80); CREATE INDEX idx_audit_trace ON audit_log(trace_id)`. 기존 행 NULL 허용(과거 감사증적 불변 — 107 §4-5) | 컬럼 DROP |
| M2 | `idempotency_keys_scope_digest` | `ALTER TABLE idempotency_keys ADD COLUMN tenant_id UUID, ADD COLUMN request_digest CHAR(64), ADD COLUMN expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'; CREATE INDEX idx_idem_expires ON idempotency_keys(expires_at)` | 컬럼 DROP |
| M3 | `auth_session` | 아래 DDL | DROP TABLE |
| M4 | `tenant_and_membership` | 아래 DDL + backfill | DROP 2 테이블(backfill 행만이므로 안전) |
| M5 | `rls_policies_foundation` | foundation 8 테이블 `ENABLE ROW LEVEL SECURITY` + `CREATE POLICY tenant_isolation ON <t> USING (tenant_id::text = current_setting('app.tenant_id', true)) WITH CHECK (동일)`; `tenant_id` NULL 허용 테이블(`audit_event` 시스템 행)은 `OR (tenant_id IS NULL AND current_setting('app.role', true) = 'system')` | `DROP POLICY` + `DISABLE` |
| M6 | `exchange_credentials_key_version_scope` | `ALTER TABLE exchange_credentials ADD COLUMN key_id VARCHAR(16) NOT NULL DEFAULT 'legacy', ADD COLUMN scope VARCHAR(5) NOT NULL DEFAULT 'PAPER' CHECK (scope IN ('PAPER','LIVE')); ALTER TABLE ... DROP CONSTRAINT exchange_credentials_user_id_exchange_key; ADD CONSTRAINT uq_exchange_credentials_user_exchange_scope UNIQUE (user_id, exchange, scope)` | 역순(scope='LIVE' 행 존재 시 downgrade 거부) |
| M7 | `break_glass_grant` | 아래 DDL | DROP TABLE |

```sql
-- M3
CREATE TABLE auth_session (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(user_id),
    tenant_id     UUID NOT NULL,
    refresh_hash  CHAR(64) NOT NULL UNIQUE,          -- sha256 hex, 평문 저장 금지
    auth_level    VARCHAR(16) NOT NULL DEFAULT 'PASSWORD'
                  CHECK (auth_level IN ('PASSWORD','MFA_VERIFIED')),
    ip_hash       CHAR(64), ua_hash CHAR(64),        -- 원문 저장 금지(108 §2.1)
    issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at    TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ NOT NULL,
    revoked_at    TIMESTAMPTZ,
    revoke_reason VARCHAR(40)                        -- logout|logout_all|admin_suspend|refresh_reuse|membership_suspend|password_change
);
CREATE INDEX idx_auth_session_user_active ON auth_session(user_id) WHERE revoked_at IS NULL;

-- M4 (73 §2.1 데이터 사전 그대로; 106 §3.3 복수형 대신 73번 표기 유지 — 기존 consent_record와 일관)
CREATE TABLE tenant (
    id           UUID PRIMARY KEY,
    kind         VARCHAR(12) NOT NULL CHECK (kind IN ('PERSONAL','HOUSEHOLD','ORGANIZATION')),
    display_name VARCHAR(100),
    state        VARCHAR(10) NOT NULL DEFAULT 'ACTIVE' CHECK (state IN ('ACTIVE','SUSPENDED','DELETED')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE tenant_membership (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id  UUID NOT NULL REFERENCES tenant(id),
    subject_id UUID NOT NULL REFERENCES users(user_id),
    role       VARCHAR(8) NOT NULL CHECK (role IN ('OWNER','ADMIN','MEMBER','AUDITOR','SERVICE')),
    state      VARCHAR(10) NOT NULL DEFAULT 'ACTIVE' CHECK (state IN ('ACTIVE','SUSPENDED','REVOKED')),
    revision   INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_tenant_membership_active ON tenant_membership(tenant_id, subject_id) WHERE state = 'ACTIVE';
CREATE INDEX idx_tenant_membership_subject ON tenant_membership(subject_id);
-- backfill: personal tenant id == user_id (기존 foundation 행의 tenant_id 불변조건 유지)
INSERT INTO tenant (id, kind) SELECT user_id, 'PERSONAL' FROM users;
INSERT INTO tenant_membership (tenant_id, subject_id, role, created_by) SELECT user_id, user_id, 'OWNER', user_id FROM users;

-- M7
CREATE TABLE break_glass_grant (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requester_id UUID NOT NULL REFERENCES users(user_id),
    approver_id  UUID REFERENCES users(user_id),
    scope       VARCHAR(24) NOT NULL CHECK (scope IN ('kill_switch_override','tenant_read','credential_revoke')),
    reason      TEXT NOT NULL,
    state       VARCHAR(10) NOT NULL DEFAULT 'REQUESTED' CHECK (state IN ('REQUESTED','APPROVED','USED','EXPIRED')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    CHECK (expires_at <= created_at + interval '60 minutes'),
    CHECK (approver_id IS NULL OR approver_id <> requester_id)
);
```

### 2-A. 기존 파일 수정 요약(경로 공지 대상, `docs/TESTING.md` 규칙 1)

`src/main.py`(PM 직렬화): 미들웨어 등록(순서: RateLimit → RequestContext → CORS; 타 세션의 `RequestIdMiddleware`는 `RequestContextMiddleware`가 상속하므로 별도 등록 없음), `install_exception_handlers`, `health.router`, `LoopHealth`·`KeyRing`·`RateLimiter`·`SessionRepository`를 `app.state`에. `src/core/logging/schema.py`, `src/core/logging/request_context.py`(shim 1줄), `src/core/logging/audit_log.py`, `src/core/safety/base_loop.py`, `src/core/event_bus/in_process.py`, `src/core/idempotency.py`, `src/core/security/encryption.py`, `src/api/deps.py`, `src/api/admin_deps.py`, `src/api/foundation_deps.py`(타 세션이 `LiveReadonlyAccountProvider` 배선 중 — 그 커밋 뒤에 `get_tenant_context`만 수정), `src/api/routers/auth.py`, `src/services/auth_service.py`, `src/services/exchange_credential_service.py`(타 세션 audit_log diff 커밋 뒤), `src/services/credential_resolver.py`, `src/foundation/trust/**`(domain/contracts/adapters), `src/foundation/evidence/application/record_command_event.py`, `src/exchanges/**/*_mixin.py`, 레거시 라우터 15개.

---

## 3. 계약 (Contract)

### 3.1 컨텍스트·로그·이벤트

```python
# src/core/observability/context.py
class RequestContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    trace_id: UUID                          # 진입점에서 1회 생성/전파, 하위에서 재생성 금지(108 §2)
    request_id: str                         # ULID 26자; HTTP X-Request-Id 그대로(≤128자, 검증 실패 시 무시하고 생성)
    tenant_id: UUID | None = None           # 인증 전 None
    actor_subject_id: UUID | Literal["system"] = "system"
    command_id: UUID | None = None
    component: str = "api.gateway"          # "<context>.<layer>"
    schema_version: Literal["v1"] = "v1"
```

`StructuredLogLine`(`src/core/logging/fields.py`)의 필드는 108 §2 표와 **이름·타입이 동일**하다. `level`은 `warn`(not `warning`), `critical`은 로그 레벨로 쓰지 않고 `error` + `event=*_critical`로. `timestamp`는 tz-aware UTC ISO-8601 ms. `duration_ms`는 int(반올림).

```python
# src/core/event_bus/envelope.py
class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: UUID                          # uuid4, 핸들러 dedupe 키(at-least-once 대비)
    topic: str
    trace_id: UUID                          # 발행 시점 current().trace_id — 재생성 금지
    tenant_id: UUID | None
    actor_subject_id: UUID | Literal["system"]
    occurred_at: datetime                   # tz-aware UTC
    schema_version: Literal["v1"] = "v1"
    payload: Any                            # 기존 페이로드 그대로(dict/pydantic) — 마스킹은 로거 책임

def wrap(topic: str, payload: Any) -> EventEnvelope: ...
def unwrap(obj: Any) -> tuple[EventEnvelope | None, Any]: ...   # 봉투 아니면 (None, obj)
```

구독자는 `unwrap()`으로 페이로드만 받으므로 기존 핸들러 시그니처 불변. 워커는 핸들러 호출 직전 `bind(trace_id=env.trace_id, tenant_id=env.tenant_id, actor_subject_id=env.actor_subject_id, component=f"event_bus.{topic}")`. 봉투 없는 publish(전환기 외부 발행자)는 `trace_id`를 새로 만들고 `event=envelope_missing` warn 1줄.

로그 1줄의 실제 형태(JSON Lines, 키 순서 고정):

```json
{"timestamp":"2026-09-03T02:14:07.118Z","level":"info","trace_id":"3f0c…","tenant_id":"9b2e…","actor_subject_id":"9b2e…","command_id":null,"component":"api.executions","event":"http_request_completed","duration_ms":41,"message":"POST /api/v1/executions/12/start 200","extra":{"route":"/api/v1/executions/{execution_id}/start","status":200,"request_id":"01J6…"}}
```

### 3.2 헬스·메트릭

```python
class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]       # db_pool, migration_head, event_bus, loop:<name>...
    as_of: datetime
class CheckResult(BaseModel):
    ok: bool; detail: str | None = None; observed: float | None = None; threshold: float | None = None
```

`/metrics`는 Prometheus text 0.0.4. 라벨 카디널리티 상한: `route`는 템플릿 경로(`/executions/{execution_id}/start`), `tenant_id` 라벨 **금지**(108 §2.1 교차 테넌트 집계 금지 — 테넌트별 수치는 read model에서).

### 3.3 API 봉투·에러 taxonomy

```python
# src/api/contracts/envelope.py
T = TypeVar("T")
class PageMeta(BaseModel):
    total: int | None            # 카운트 비용이 큰 목록은 None 허용(커서 방식)
    page: int | None
    size: int
    next_cursor: str | None
class Meta(BaseModel):
    trace_id: UUID
    as_of: datetime              # 서버 시각(UTC) — 프로젝션 lag 판정용(73 §7)
    page: PageMeta | None = None
class ApiResponse(BaseModel, Generic[T]):
    data: T
    meta: Meta
class ApiError(BaseModel):
    error_code: str              # ErrorCode 값 — enum 자체를 쓰지 않는 이유: 클라이언트는 미지 코드에 fallback해야(107 §3.2)
    message: str                 # 한국어, 사람용, 내부 정보 없음
    details: dict[str, Any] = Field(default_factory=dict)
    trace_id: UUID
    retry_after_seconds: int | None = None
```

성공: `{"data": ..., "meta": {"trace_id", "as_of", "page"}}`. 실패: 15 §15.3 포맷 + `trace_id`. `Content-Type: application/json; charset=utf-8`. 배열 응답도 `data: [...]`로 감싼다(현재 `list_credentials`처럼 top-level 배열 반환 금지 — MINOR가 아니라 MAJOR이므로 §9 PLT-17~21에서 `/api/v1` 경로에만 적용하고 레거시 alias 경로는 구형 그대로 반환).

| error_code | HTTP | 재시도 | 호출자 조치 | 매핑되는 예외(기존) |
|---|---|---|---|---|
| `VALIDATION_INVALID_FIELD` | 400 | 아니오 | `details.fields[]` 수정 | `RequestValidationError`, pydantic `ValidationError` |
| `VALIDATION_IDEMPOTENCY_KEY_REQUIRED` | 400 | 아니오 | 헤더 추가 | 신규 |
| `VALIDATION_DISCLOSURE_RETIRED` | 400 | 아니오 | 최신 revision 재조회 | `DisclosureRetiredError`(현재 422 → 400으로 통일. 73 §5의 422는 15 §15.3과 충돌 — 15가 우선, §10) |
| `AUTH_REQUIRED` | 401 | 아니오 | 로그인 | 토큰 없음 |
| `AUTH_INVALID_CREDENTIALS` | 401 | 아니오 | — | `AuthError` |
| `AUTH_TOKEN_EXPIRED` / `AUTH_TOKEN_INVALID` / `AUTH_SESSION_REVOKED` | 401 | expired만 refresh | refresh 또는 재로그인 | `jwt.ExpiredSignatureError` / `PyJWTError` / 세션 revoked |
| `AUTH_ACCOUNT_LOCKED` | 423 | `retry_after_seconds` 후 | 대기 | `AccountLockedError`(신규) |
| `AUTH_MFA_REQUIRED` | 403 | 아니오 | step-up | admin/민감 커맨드 auth_level 미달 |
| `AUTH_MFA_INVALID` | 400 | 아니오 | 새 코드 | `MfaError` |
| `AUTH_TENANT_MISMATCH` | 403 | 아니오 | — | `TenantMismatchError`, `CrossTenant*Error`(foundation 4종), `PermissionError`(trust repo) |
| `AUTHZ_FORBIDDEN` | 403 | 아니오 | — | verifier/admin 미달 |
| `AUTHZ_ZONE_VIOLATION` | 403 | 아니오 | — | `ZoneViolationError` |
| `POLICY_LIVE_BLOCKED` | 403 | 아니오 | — | `FrozenZoneLiveModeBlockedError`, `FrozenZonePaperAdapterBlockedError` |
| `POLICY_*` / `RISK_*` | 403 | 아니오 | `details.reason_codes` | `OrderDeniedByRiskGateError`, foundation policy deny |
| `RESOURCE_NOT_FOUND` | 404 | 아니오 | — | `LookupError`, `CredentialNotFoundError`, `*NotFoundError` |
| `STATE_CONCURRENCY_CONFLICT` | 409 | 예(재조회 후) | 재조회·재시도 | `ConcurrencyConflictError` |
| `STATE_INVALID_TRANSITION` | 409 | 아니오 | — | `ExecutionControlError`, `ConsentAlreadyActiveError`, `STATE_LAST_OWNER` 등 |
| `INTEGRITY_IDEMPOTENCY_CONFLICT` | 409 | 아니오 | 새 키 | digest 불일치 / 처리 중 |
| `RATE_LIMIT_EXCEEDED` | 429 | `Retry-After` 후 | 대기 | limiter |
| `EXCHANGE_UNAVAILABLE` | 503 | 예 | 백오프 | `RetryableExchangeError`, `ProviderUnavailableError` |
| `EXCHANGE_FATAL` | 502 | 아니오 | 자격증명 확인 | `FatalExchangeError` |
| `DEPENDENCY_NOT_READY` | 503 | 예 | — | readiness 실패 |
| `INTERNAL_ERROR` | 500 | 아니오 | trace_id로 문의 | 그 외 전부 |

규칙: `HTTPException(status, "문자열")`은 **신규 코드에서 금지**(ruff 커스텀 검사 대신 `tests/contract/test_no_raw_http_exception.py`가 `src/api` AST를 스캔). 레거시 라우터는 리프 PLT-2x에서 이관.

### 3.4 인증 토큰·세션

```python
# src/services/auth/tokens.py
class AccessClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sub: UUID            # user_id
    tid: UUID            # tenant_id (personal이면 == sub)
    sid: UUID            # auth_session.id
    jti: UUID            # 토큰 고유 id — 세션당 access 토큰은 여러 개(refresh마다 새 jti)
    iat: int; exp: int; nbf: int
    auth_level: Literal["PASSWORD", "MFA_VERIFIED"]
    schema_version: Literal["v1"] = "v1"

class TokenPairResponse(BaseModel):          # /auth/login, /auth/refresh 응답 data
    access_token: str
    refresh_token: str                       # 응답에 1회만 노출, 로그 금지(DENY_KEYS "token")
    token_type: Literal["bearer"] = "bearer"
    expires_in: int                          # access TTL 초
    session_id: UUID

class ClientInfo(BaseModel):                 # login()에 넘기는 요청 메타 — 해시만 저장
    ip: str | None; user_agent: str | None
```

| 항목 | 값 |
|---|---|
| access JWT | HS256 고정, header `kid`, claims §2.2 `AccessClaims`. TTL `JWT_EXPIRE_MINUTES`(기본 60 → **15**로 하향, refresh가 생기므로) |
| refresh | 32바이트 urlsafe 랜덤(평문은 응답에만), DB에는 sha256 hex. TTL 14일. 사용 시 회전. 이전 해시 재사용 감지 → 세션 revoke + `auth.refresh_reuse_detected` |
| `auth_level` | `PASSWORD` 또는 `MFA_VERIFIED`(TOTP 통과 후 `MFA_STEP_UP_WINDOW`=15분 내). 토큰 재발급 없이 `users.mfa_verified_at`으로 매 요청 계산(기존 `foundation_deps.py` 로직 유지) |
| 폐기 | logout(세션 1개), logout-all, admin suspend(`revoke_all_for_user`), 비밀번호 변경, membership suspend. 매 요청 `auth_session.revoked_at IS NULL` 확인(DB 1회 — 기존 `get_user_by_id` 조회에 JOIN) |

### 3.5 테넌트 계약(MINOR 변경)

`TenantContext` v1에 optional 3필드 추가(§2.2). 기존 소비자(mandates/paper_control 등)는 무시 가능 → 107 §3.2 MINOR, `schema_version="v1"` 유지. `tenant_id == subject_id` 불변조건은 **PERSONAL tenant에 한해** 유지된다(마이그레이션 M2가 personal tenant id = user_id로 backfill하므로 기존 foundation 행은 그대로 유효).

```python
# src/foundation/trust/contracts/v1.py — 추가분(MINOR)
class TenantKind(str, Enum): PERSONAL = "PERSONAL"; HOUSEHOLD = "HOUSEHOLD"; ORGANIZATION = "ORGANIZATION"
class MembershipRole(str, Enum): OWNER = "OWNER"; ADMIN = "ADMIN"; MEMBER = "MEMBER"; AUDITOR = "AUDITOR"; SERVICE = "SERVICE"
class MembershipState(str, Enum): ACTIVE = "ACTIVE"; SUSPENDED = "SUSPENDED"; REVOKED = "REVOKED"

class TenantContext(BaseModel):              # 기존 5필드 + 아래 3개(모두 기본값 있음 → 107 §3.2 MINOR)
    tenant_id: UUID; subject_id: UUID; role: str = "OWNER"; mfa_verified: bool
    membership_id: UUID | None = None
    tenant_kind: TenantKind = TenantKind.PERSONAL
    auth_level: Literal["PASSWORD", "MFA_VERIFIED"] = "PASSWORD"
    schema_version: str = SCHEMA_VERSION

class MembershipView(BaseModel):             # 신규 계약 — trust_memberships 라우터·감사 페이로드
    membership_id: UUID; tenant_id: UUID; subject_id: UUID
    role: MembershipRole; state: MembershipState; revision: int
    created_at: datetime; updated_at: datetime
    schema_version: str = SCHEMA_VERSION
```

`role`을 `str`에서 enum으로 바꾸지 않는다(타입 변경 = MAJOR). v2에서 정리.

### 3.6 비밀 계약

```python
# src/core/security/secret_ref.py
SecretScope = Literal["PAPER", "LIVE"]
class SecretRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    scope: SecretScope
    kind: Literal["exchange_credential", "mfa_secret", "withdrawal_dest"]
    id: str                                  # 테이블 PK 문자열
    kid: str                                 # 암호화 당시 key id
    def __str__(self) -> str: return f"secref://{self.scope.lower()}/{self.kind}/{self.id}@{self.kid}"

# src/core/security/secret_handle.py
class SecretHandle:
    ref: SecretRef
    async def __aenter__(self) -> "SecretHandle": ...      # 복호 + secret_decrypt 카운터
    async def __aexit__(self, *exc) -> None: ...           # bytearray zero-fill
    @property
    def api_key(self) -> bytes: ...
    @property
    def api_secret(self) -> bytes: ...
    @property
    def extra(self) -> Mapping[str, bytes]: ...
```

암호문 포맷 `aios1$<kid>$<b64>`; `exchange_credentials.api_key_encrypted`는 BYTEA에 **b64 디코딩된 원 바이트**(`aios1$kid$` 접두는 별도 컬럼 `key_id`로). `SecretRef` 문자열만 로그·이벤트·API 노출.

### 3.7 멱등 스코프

```python
# src/api/contracts/idempotency.py
class IdempotencyScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    header_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    route: str                               # request.scope["route"].name — 라우트 간 키 충돌 방지
    tenant_id: UUID
    subject_id: UUID
    digest: str = Field(min_length=64, max_length=64)   # sha256(canonical_json(body)) — 키 정렬, 공백 제거, Decimal은 문자열
    @property
    def storage_key(self) -> str: return f"{self.route}:{self.tenant_id}:{self.subject_id}:{self.header_key}"
```

적용 대상(§9 PLT-15): `POST /marketplace/listings/{id}/purchase`, `POST /admin/purchases/{id}/confirm-payment`, `POST /wallet/topups`·`/admin/wallet/topups/{id}/confirm`, `POST /executions`, `POST /executions/{id}/start`, `POST /executions/{id}/convert-to-live`, `PUT /portfolio/rebalance`, `POST /v1/foundation/paper-control/*`(5개, 현재 body 필드 → 헤더로 이동하되 body 필드는 전환기 동안 alias), `POST /exchange-credentials`, `POST /v1/foundation/trust/consents`.

---

## 4. 불변조건·상태기계

| 불변조건 | 강제 지점 | 위반 시 |
|---|---|---|
| I1 하나의 trace_id에 둘 이상의 tenant_id가 관측되지 않는다 | 코드(`rebind_tenant`) + 알림 A4 | fail-open(요청은 진행) + warn + 카운터. 차단하지 않는 이유: 정당한 admin 교차 조회가 있음 — 대신 100% 감사 |
| I2 로그 라인에 `DENY_KEYS` 값이 없다 | 코드(`RedactionFilter`) + 테스트 | fail-closed(마스킹) |
| I3 tenant-scoped 테이블은 `app.tenant_id`와 다른 행을 반환하지 않는다 | **DB**(RLS policy, `aios_app` NOBYPASSRLS) | fail-closed(0행). `app.tenant_id` 미설정 시 정책이 `false` → 0행 |
| I4 tenant당 ACTIVE OWNER ≥ 1 | 코드(`would_remove_last_owner`, 같은 트랜잭션) + DB(`(tenant_id, subject_id) WHERE state='ACTIVE'` 부분 UNIQUE) | fail-closed `STATE_LAST_OWNER` |
| I5 revoke된 세션의 토큰은 어떤 라우트도 통과하지 못한다 | 코드(`get_current_user`) | fail-closed 401 |
| I6 `failed_login_attempts`는 동시 시도 N회에 정확히 N 증가 | **DB**(단일 UPDATE 식) | — |
| I7 PAPER 런타임 프로세스에 LIVE KEK가 없다 | 코드(`KeyRing.from_env` 기동 검사) | fail-closed: 기동 거부 |
| I8 같은 `Idempotency-Key`+다른 digest는 409 | DB(`idempotency_keys.request_digest`) + 코드 | fail-closed |
| I9 응답 스키마에서 필드 제거/타입 변경은 v1 스냅샷 불변 + v2 추가 없이는 머지 불가 | CI(`check_openapi_compat`) | fail-closed(머지 차단) |
| I10 `type: ignore`·커버리지는 단조 개선 | CI(ratchet) | fail-closed |
| I11 마이그레이션 head는 항상 1개, merge 리비전은 PM 승인 트레일러 필수 | CI(`check_migration_chain`) | fail-closed |
| I12 break-glass grant는 요청자≠승인자, ≤60분, 1회 소비 | DB(CHECK `expires_at <= created_at + interval '60 min'`, `used_at` 조건부 UPDATE) + 코드 | fail-closed |

### 4.1 상태 전이표

**Membership**(73 §3.1 준수)

| from | event | guard | to | side-effect | 감사 이벤트 |
|---|---|---|---|---|---|
| — | GrantMembership | actor ACTIVE OWNER/ADMIN, `mfa_verified`, 중복 활성 없음 | ACTIVE | — | `trust.membership_granted.v1` |
| ACTIVE | SuspendMembership | actor OWNER/ADMIN, not last owner, actor ≠ target 또는 OWNER ≥ 2 | SUSPENDED | 대상 subject 세션 전부 revoke | `trust.membership_suspended.v1` |
| ACTIVE/SUSPENDED | RevokeMembership | not last owner | REVOKED | 세션 revoke | `trust.membership_revoked.v1` |
| REVOKED | GrantMembership(regrant) | actor OWNER + `mfa_verified` | ACTIVE(새 revision) | — | `trust.membership_granted.v1` |

**AuthSession**: `ACTIVE --(logout|revoke_all|refresh_reuse|expiry)--> REVOKED`(종단). refresh 회전은 상태 불변, `refresh_hash`·`rotated_at`만 갱신(조건부).

**BreakGlassGrant**: `REQUESTED --approve(다른 admin, MFA)--> APPROVED --consume--> USED`; `REQUESTED/APPROVED --expiry--> EXPIRED`(lazy).

---

## 5. 동시성·멱등성·트랜잭션 경계 (105번)

| 쓰기 | 방식 | 근거 |
|---|---|---|
| `users.failed_login_attempts` | 단일 UPDATE 식(`= col + 1`, CASE) + RETURNING | 105 §2.2 "단조 증가" 예외 — 조건 없이도 원자적 |
| `auth_session.refresh_hash` 회전 | `conditional_update(expected_state_column="refresh_hash", expected_state_value=old_hash)` — RETURNING 없음 = 재사용 | 105 §2 |
| `auth_session.revoked_at` | `WHERE revoked_at IS NULL` 조건부; 0행이면 이미 revoked(멱등, 에러 아님) | — |
| `tenant_membership.state` | `conditional_update`(state + revision 둘 다) + last-owner 검사를 **같은 트랜잭션**에서 `SELECT ... FOR UPDATE`로 활성 OWNER 행 잠금 | 73 §6-5 "same transaction" |
| `exchange_credentials` 회전 | `UPDATE ... WHERE id=$1 AND key_id=$from_kid` | 105 §2 |
| `idempotency_keys` | 기존 claim-first 유지 + digest 컬럼; 키 스코프 `"{route}:{tenant_id}:{subject_id}:{header}"` | 감사 §2-A 수정분 위에 확장 |
| `break_glass_grant.used_at` | `WHERE used_at IS NULL AND expires_at > now() AND state='APPROVED'` | — |
| `audit_log`/`audit_event` | append-only(기존) | 105 §2.2 |
| 요청 컨텍스트 | ContextVar — 태스크마다 복제되므로 `asyncio.create_task` 시점의 값이 상속. 백그라운드 루프는 tick마다 `bind_system`으로 **명시적 초기화**(부모 요청 컨텍스트 누수 방지) | — |

트랜잭션 경계: `tenant_transaction()`이 유일한 RLS 바인딩 지점. 응용 계층이 트랜잭션을 열지 않는 기존 foundation 문제(감사 §6)는 이 문서 범위 밖이나, 새 membership 커맨드 3개는 `tenant_transaction` 안에서 repo 호출을 완결한다(부분 실패 없음). outbox: 없음(05번 in-process 버스 유지, §10).

---

## 6. 실패 모드와 복구

| 실패 | 감지 | 즉시 조치 | 복구 절차 | 감사 기록 |
|---|---|---|---|---|
| 미들웨어 예외(컨텍스트 바인딩 실패) | `event=request_context_bind_failed` error | 요청은 새 trace_id로 진행(fail-open — 관측성 결함이 거래를 막지 않는다) | 없음 | 로그만 |
| `/metrics` 수집기 장애 | Prometheus `up==0` | 없음(앱 무관) | RB-01 | — |
| 루프 tick 연속 실패 | `loop.last_success_age > 3×interval` → readyz 503, 알림 A3 | 배포 롤아웃 중단(readyz) | RB-01: 로그 trace_id로 원인, 필요 시 kill switch | tick 실패는 각 서비스가 audit_log(기존) |
| 로그 sink 지연/stdout 막힘 | `duration_ms` 급증 | `QueueHandler`로 로깅 비동기화(`configure_logging`) | — | — |
| 세션 테이블 불가(DB) | 모든 요청 500 | 이미 DB 없이는 어떤 요청도 불가 — 추가 fail-open 없음 | — | — |
| 키 회전 중 프로세스 종료 | `key_id` 혼재 행 | 양쪽 kid 모두 KeyRing에 남아 있으므로 서비스 영향 없음 | 스크립트 재실행(멱등: `WHERE key_id=$from`) | `security.key_rotated`(배치 단위) |
| LIVE KEK가 PAPER 런타임에 주입 | 기동 검사 | 기동 거부 | 환경변수 제거 | 로그 `event=runtime_scope_violation`(error) |
| refresh 재사용 감지 | `rotate_refresh` 0행 | 세션 revoke, 401 | 사용자 재로그인, 운영자 통지 | `auth.refresh_reuse_detected` |
| 잠금 폭주(자격증명 스터핑) | 알림 A6 | rate limit `auth_login`(IP 10/분) | RB-07 | `auth.account_locked`(기존) |
| RLS 정책 누락 테이블 신설 | `tests/platform/adversarial/test_rls_coverage.py`(pg_policies 조회, tenant_id 컬럼 있는 모든 테이블에 정책 존재) | CI 실패 | 마이그레이션 추가 | — |
| OpenAPI 스냅샷 드리프트 | CI | 머지 차단 | v2 추가 또는 변경 철회 | — |
| 마이그레이션 두 head | CI | 머지 차단 | PM이 직렬화(`docs/TESTING.md`) | — |
| 시계 드리프트(JWT `exp`, TOTP) | `readyz`가 `pg now()`와 프로세스 시각 차 > 30s면 `clock_skew` check 실패 | 503 | NTP | — |
| 재시작 | 컨텍스트·limiter·LoopHealth는 인메모리 → 초기화. 세션·멱등키·잠금은 DB → 보존 | — | — | — |

---

## 7. 성능·SLO·관측성 (108번)

### 7.1 목표 수치(측정 지점 = `RequestContextMiddleware`, 거래소 왕복 포함)

| SLI | SLO | 메트릭 |
|---|---|---|
| read 라우트 p95 | < 300 ms(73 §10) | `aios.api.request.duration_seconds{method=GET}` |
| mutation 라우트 p95(거래소 미포함) | < 800 ms | 동상 `{method=POST}` |
| 주문 제출 p95(거래소 포함) | < 2 s | `aios.order.submit.duration_seconds` |
| 미들웨어 오버헤드 p95 | < 1 ms | 벤치마크 테스트(§8) |
| 루프 신선도 | `last_success_age < 3×interval` 99.9% | `aios.loop.last_success_age.seconds` |
| readiness 가용성 | 99.9%/30일 | `aios.readiness.status` gauge |
| 감사 append 실패 | 0 | `aios.audit.append.count_total{outcome=error}` |
| rate limiter 판정 | 10k acquire/s 단일 프로세스 | 벤치마크 |

### 7.2 메트릭 이름(`src/core/observability/metric_names.py` — 108 §3 형식; Prometheus 노출 시 `.`→`_`)

| 이름 | 타입 | 라벨 | 계측 지점 |
|---|---|---|---|
| `aios.api.request.count_total` | counter | `route,method,status,error_code` | middleware |
| `aios.api.request.duration_seconds` | histogram | `route,method` | middleware |
| `aios.order.submit.count_total` | counter | `exchange,mode,outcome=accepted\|rejected\|denied\|error` | `order_service/submit.py` |
| `aios.order.submit.duration_seconds` | histogram | `exchange` | 동상 |
| `aios.order.fill.count_total` | counter | `exchange,status` | `order_service/position_ledger.py` |
| `aios.order.unknown_state.gauge` | gauge | `exchange` | `order_service/reconcile.py` |
| `aios.risk.decision.count_total` | counter | `engine=core\|foundation,effect,reason_code` | `order_service/gate.py`, `foundation/risk_gate/application/evaluate_risk_gate.py`(FROZEN 경로 밖) |
| `aios.risk.evaluation.duration_seconds` | histogram | `engine` | 동상 |
| `aios.foundation_paper_control.order_intent.count_total` | counter | `mode=paper\|live_blocked` | `submit_paper_intent.py`(108 §3 예시 그대로) |
| `aios.loop.tick.count_total` / `aios.loop.tick.duration_seconds` / `aios.loop.last_success_age.seconds` | counter/histogram/gauge | `loop` | `base_loop.py`, `main.py` 4개 루프, `scheduler.py` |
| `aios.adapter.request.count_total` / `.duration_seconds` | counter/histogram | `exchange,endpoint_group,outcome=success\|retryable\|fatal\|non_json` | `_BitgetHTTPClient._request` 등 3곳 |
| `aios.event_bus.queue_depth.gauge` / `aios.event_bus.handler.count_total` | gauge/counter | `topic` / `topic,outcome` | `in_process.py` |
| `aios.auth.login.count_total` | counter | `outcome=success\|invalid\|locked\|mfa_failed` | `auth/login.py` |
| `aios.auth.lockout.count_total`, `aios.auth.refresh_reuse.count_total`, `aios.auth.tenant_mismatch.count_total` | counter | — | lockout / refresh / tenant_binding |
| `aios.auth.rate_limited.count_total` | counter | `policy` | rate_limit middleware |
| `aios.audit.append.count_total` | counter | `sink=audit_log\|audit_event,outcome` | `audit_log.py`, `append_audit_event.py` |
| `aios.security.secret_decrypt.count_total` | counter | `scope,kind` | `secret_handle.py` |
| `aios.security.break_glass.count_total` | counter | `scope,phase=requested\|approved\|used` | `break_glass.py` |
| `aios.security.key_rotation.count_total` | counter | `scope,outcome` | 회전 스크립트 |
| `aios.readiness.status` | gauge(1/0) | `check` | health router |

### 7.3 로그 필드·이벤트 어휘(108 §4)

모든 라인: `trace_id, tenant_id, actor_subject_id, command_id, component, event, level, duration_ms`. 이벤트 예: `http_request_completed`, `membership_granted`, `membership_suspend_denied`, `session_revoked`, `refresh_reuse_detected`, `account_locked`, `rate_limit_exceeded`, `secret_decrypted`, `break_glass_used`, `key_rotated`, `loop_tick_failed`, `envelope_missing`, `tenant_mismatch_observed`, `*_concurrency_conflict`(105).

### 7.4 알림 규칙(`config/observability/alert_rules.yaml`, 5분 평가)

| ID | 조건 | 심각도 | runbook |
|---|---|---|---|
| A1 | `rate(*_concurrency_conflict[5m]) > 3 × rate(...[1h])` (108 §5-1) | warn | RB-08 |
| A2 | 이벤트 버스 `queue_depth > 800`(max 1000의 80%) 5분 지속 (108 §5-2 대체 — outbox 없음) | warn | RB-01 |
| A3 | `loop.last_success_age > 3×interval` 2분 | **critical** | RB-01 |
| A4 | `increase(aios.foundation_paper_control.order_intent.count_total{mode="live_blocked"}[5m]) > 0` (108 §5-3) | **critical** | RB-03 |
| A5 | `increase(aios.auth.tenant_mismatch.count_total[5m]) > 0` (108 §5-4) | **critical** | RB-04 |
| A6 | `increase(aios.auth.lockout.count_total[10m]) > 10` 또는 `rate_limited{policy="auth_login"}` 급증 | warn | RB-07 |
| A7 | `increase(aios.audit.append.count_total{outcome="error"}[5m]) > 0` | **critical** | RB-02 |
| A8 | `increase(aios.security.break_glass.count_total{phase="used"}[1h]) > 0` | **critical**(정보성이지만 반드시 사람 확인) | RB-06 |
| A9 | `aios.adapter.request.count_total{outcome="fatal"}` 비율 > 5%/5분 | warn | RB-05(자격증명) |
| A10 | `aios.readiness.status == 0` 1분 | critical | RB-01 |
| A11 | `secret_decrypt{scope="LIVE"} > 0` in PAPER 런타임(레이블 `runtime_mode`) | critical | RB-03 |

`alert_rules.yaml` 형식(A3·A5 예시 — 나머지도 동일 구조; 테스트가 `expr` 안의 메트릭 이름을 `metric_names.py`와 대조):

```yaml
groups:
  - name: aios-platform
    interval: 1m
    rules:
      - alert: A3_LoopStale
        expr: aios_loop_last_success_age_seconds > 3 * aios_loop_interval_seconds
        for: 2m
        labels: {severity: critical, runbook: RB-01}
        annotations: {summary: "{{ $labels.loop }} 루프가 {{ $value }}초 동안 성공하지 못함"}
      - alert: A5_TenantMismatchOnTrace
        expr: increase(aios_auth_tenant_mismatch_count_total[5m]) > 0
        for: 0m
        labels: {severity: critical, runbook: RB-04}
        annotations: {summary: "같은 trace_id에 둘 이상의 tenant_id 관측(108 §5-4)"}
```

### 7.5 runbook 목록(`docs/runbooks/`)

RB-01 루프 정지/readiness 실패 · RB-02 감사 append 실패(거래 중단 판단 기준 포함: `audit_event` 실패 시 새 배포 start 금지) · RB-03 LIVE 차단 관측(설정 오류 vs 공격) · RB-04 tenant mismatch(confused deputy 조사 절차) · RB-05 키 회전 절차(`rotate_credential_keys.py`, 검증 쿼리, 롤백=이전 kid 유지) · RB-06 break-glass 사용(사후 리뷰 체크리스트) · RB-07 인증 남용(잠금·rate limit 폭주) · RB-08 동시성 충돌 급증(105 §1 재발 패턴 탐색).

---

## 8. 테스트 계획

트리: `tests/platform/{unit,integration,adversarial,contract,perf}/`, `tests/contract/`(API 전역). 모든 리프에 negative 테스트 ≥ 1.

| 분류 | 테스트(파일 → 핵심 단언) |
|---|---|
| 단위 | `platform/unit/test_context.py`: `bind` 중첩·복원, `create_task` 상속, `bind_system`이 부모 trace_id를 **상속하지 않음**(negative). `test_redaction.py`: DENY_KEYS 부분일치, 64-hex 값, `eyJ` 값 마스킹; `secref://`는 통과. `test_fields.py`: 108 §2 필드 집합 정확히 일치(추가·누락 모두 실패). `test_metric_names.py`: 정규식 `^aios\.[a-z_]+\.[a-z_]+\.[a-z_]+(_total\|_seconds\|_bytes\|\.gauge)?$` 전수. `test_error_codes.py`: 접두 화이트리스트·HTTP 매핑 전수. `test_membership_rules.py`: 전이표 전수 + last-owner. `test_lockout_math.py`. `test_key_ring.py`: LIVE 변수 존재 시 PAPER 기동 거부(negative). `test_encryption_format.py`: 레거시 토큰 복호, 잘못된 kid 실패. `test_pagination.py`: `page=0` → ValidationError. `test_idempotency_scope.py`: digest 정규화(키 순서 무관) |
| 통합(실DB) | `platform/integration/test_middleware_trace.py`: 요청 → 서비스 로그 → audit_log.trace_id → 이벤트 핸들러 로그가 **같은 trace_id**(caplog JSON 파싱). `test_readyz.py`: 루프 stale 주입 시 503. `test_sessions.py`: login→refresh→logout→401; refresh 재사용 → 세션 revoke. `test_lockout_atomic.py`: `asyncio.gather` 10회 틀린 비밀번호 → `failed_login_attempts == 5 이상 정확`, `locked_until` 설정, 423 + `retry_after_seconds` (105 §4.1 형태 A). `test_membership_commands.py`: grant/suspend/revoke + 세션 revoke 부작용. `test_rls_scoping.py`: `aios_app`로 접속, `SET LOCAL app.tenant_id=A`, `SELECT * FROM consent_record`(WHERE 없음) → A행만. `test_key_rotation.py`: v1→v2 회전 후 복호 성공, `key_version` 갱신, 중단 재실행 멱등. `test_key_rotation_negative.py`: subprocess `python -m scripts.rotate_credential_keys` 배선(I-10), encrypt 실패 시 평문 미노출, 감사 INSERT DB 오류 failure 메트릭·롤백. `test_idempotency_digest.py`: 같은 키 다른 body → 409; 재생 헤더. `test_break_glass.py`: 자기 승인 거부, 61분 거부(DB CHECK), 2회 consume 거부 |
| 적대적 | `platform/adversarial/test_cross_tenant_membership.py`: 테넌트 B의 ADMIN이 `X-Tenant-Id: A`로 A의 membership 조회/변경 → 403, DB 무변경. `test_rls_bypass_attempt.py`: `SET LOCAL app.tenant_id`를 다른 값으로 두 번 설정해도 트랜잭션 밖으로 누수 없음; `RESET`후 0행. `test_rls_coverage.py`: `tenant_id` 컬럼 보유 테이블 전수에 policy 존재. `test_token_tamper.py`: alg=none, 다른 kid, 서명 변조, `sid` 위조 → 401. `test_log_leak.py`(TRU-009 확장): 로그인·자격증명 등록·MFA setup 전 요청의 caplog·응답·audit_log에 평문 secret 0건. `test_rate_limit_storm.py`(TRU-012): 121번째 read → 429, 부분 변경 없음. `test_secret_scope_isolation.py`: PAPER 런타임에서 LIVE ref 해제 시도 → `FrozenZoneLiveModeBlockedError`, `secret_decrypt{scope=LIVE}` 0 |
| 계약 | `contract/test_openapi_snapshot.py`: `contracts/openapi/v1.json`과 현재 앱 스키마의 `check_openapi_compat` 통과. `contract/test_envelope_everywhere.py`: `app.routes` 전수 순회, 응답 모델이 `ApiResponse[...]`이거나 `/healthz|/metrics` 예외 목록. `contract/test_no_raw_http_exception.py`: AST 스캔. `contract/test_tenant_context_v1_compat.py`: v1 fixture(`contracts/v1/fixtures/tenant-context.valid.json`, 신규 3필드 없음)가 여전히 파싱. `contract/test_alert_rules_reference_known_metrics.py`: yaml의 모든 메트릭이 `metric_names.py`에 존재 |
| 성능 | `platform/perf/test_middleware_overhead.py`: 1,000 요청 p95 오버헤드 < 1 ms(NullMetrics 대비). `test_rate_limiter_throughput.py`: 10k acquire < 1 s. `test_db_template_clone.py`: 템플릿 복제 < 3 s |

RLS 적대 테스트의 실제 형태(`tests/platform/adversarial/test_rls_scoping.py` — 105 §4 형태 B와 같은 "직접 주입" 스타일):

```python
async def test_select_without_where_returns_only_bound_tenant(app_role_pool, seeded_two_tenants):
    tenant_a, tenant_b = seeded_two_tenants                      # 각 tenant에 consent_record 3행
    async with tenant_transaction(app_role_pool, tenant_a) as conn:
        rows = await conn.fetch("SELECT tenant_id FROM consent_record")   # WHERE 없음
    assert {r["tenant_id"] for r in rows} == {tenant_a}

async def test_unbound_transaction_returns_nothing(app_role_pool, seeded_two_tenants):
    async with app_role_pool.acquire() as conn:                   # app.tenant_id 미설정
        assert await conn.fetch("SELECT 1 FROM consent_record") == []

async def test_insert_for_other_tenant_is_rejected(app_role_pool, seeded_two_tenants):
    tenant_a, tenant_b = seeded_two_tenants
    async with tenant_transaction(app_role_pool, tenant_a) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):   # WITH CHECK 위반
            await conn.execute("INSERT INTO consent_record (tenant_id, ...) VALUES ($1, ...)", tenant_b)
```

`app_role_pool` 픽스처는 `aios_app` role로 접속한다(CI `quality.yml`에 `psql -f src/db/roles.sql` 스텝 추가, 로컬은 `scripts/setup_test_db.py`가 실행). owner role로 접속한 기존 픽스처 `pool`은 RLS를 우회하므로 이 테스트에 쓰면 **거짓 통과** — `test_rls_coverage.py`가 `current_user`가 owner인 커넥션으로 이 파일이 실행되면 실패하도록 가드한다.

OpenAPI 호환성 검사기(`scripts/check_openapi_compat.py`)의 판정 규칙(107 §3.3을 기계 판정으로):

| 변경 | 판정 |
|---|---|
| path·method 제거 | MAJOR → FAIL |
| 응답 스키마 property 제거 / `type` 변경 / `format` 변경 / `nullable` true→false | MAJOR → FAIL |
| 응답 property 추가 | MINOR → OK(스냅샷 갱신 필요, 같은 PR) |
| 요청 `required` 항목 추가 / 요청 property 제거 / 요청 enum 값 제거 | MAJOR → FAIL |
| 요청 property 추가(optional) / 요청 enum 값 추가 | MINOR → OK |
| 응답 enum 값 추가 | MINOR → OK(클라이언트 fallback 의무는 `ApiError.error_code`처럼 `str` 타입으로 표현) |
| 응답 enum 값 제거 | MAJOR → FAIL |
| `x-deprecated: true` 추가 | OK |
| 위 MAJOR가 `contracts/openapi/v2.json` 신규 + `v1.json` 무변경과 함께 오면 | OK(107 §4 이중 발행) |

CI 신규 게이트 테스트: `tests/platform/unit/test_check_scripts.py`가 `check_migration_chain`·`check_zone_diff`·`check_openapi_compat`·`coverage_ratchet`·`check_type_ignore_budget`을 임시 디렉터리 fixture로 각각 통과/실패 케이스 1개씩 실행.

---

## 9. 리프 목록 (구현 순서)

마이그레이션은 PM이 리비전 id를 직렬화한다(`docs/TESTING.md`). 아래 M1~M7의 parent는 착수 시점 head(현재 `5ed4921f9873`).

| 리프 | 파일 | 선행 | DoD(검증 명령 · 기대 결과) | 크기 |
|---|---|---|---|---|
| PLT-01 | `src/core/observability/context.py` + `tests/platform/unit/test_context.py` (+ 타 세션 `request_context.py` 커밋 후 `get_current_request_id` 1줄 shim) | 타 세션 request_id 커밋 | `pytest tests/platform/unit/test_context.py` 통과, 상속/비상속 케이스 포함; `tests/unit/core/logging/test_schema.py` 무수정 통과 | 200 |
| PLT-02 | `src/core/logging/redaction.py`, `fields.py` + 단위 테스트 | 01 | 108 §2 필드 집합 테스트 통과 | 250 |
| PLT-03 | `src/core/logging/schema.py` 수정(fields 위임, QueueHandler) | 02 | 기존 `tests/unit/core/test_logging*` 통과 + 신규 JSON 라인에 8필드 | 100 |
| PLT-04 | `src/core/observability/metric_names.py`, `metrics.py` + `prometheus-client` 의존 추가 | — | `test_metric_names.py` 정규식 전수 통과; `NullMetrics` 기본 | 240 |
| PLT-05 | `src/api/middleware/request_context.py`(타 세션 `RequestIdMiddleware` 상속), `src/core/observability/tenant_binding.py`, `instrument.py` + `main.py` 등록 교체(PM) | 01,03,04 | 통합 `test_middleware_trace.py` 1차(응답 헤더 `X-Request-ID`·`X-Trace-Id`, 로그 1줄 8필드); 타 세션 `tests/unit/api/middleware/test_request_id.py` 무수정 통과 | 310 |
| PLT-06 | `src/core/event_bus/envelope.py` + `in_process.py` 수정 | 01 | 기존 `test_event_bus.py` 무수정 통과 + 핸들러 내부 `current().trace_id == 발행 시점` | 200 |
| PLT-07 | M1 `audit_log_trace_id` 마이그레이션 + `audit_log.py`·`record_command_event.py` 수정 | 01 | `test_middleware_trace.py` 2차: audit_log·audit_event 동일 trace_id | 120 |
| PLT-08 | `loop_health.py` + `base_loop.py` 수정 + `main.py` 4루프·scheduler 계측(PM) | 04,05 | `loop.last_success_age` 노출, stale 주입 테스트 | 220 |
| PLT-09 | `src/api/routers/health.py` + `main.py` 등록(PM) | 08 | `GET /readyz` 정상 200, pool 끊으면 503; `/metrics` 토큰 없이 403 | 200 |
| PLT-10 | 도메인 계측 지점 5곳(`order_service/submit.py`, `gate.py`, `position_ledger.py`, `reconcile.py`, `submit_paper_intent.py`, adapter `_request` 3곳) | 04 | 각 메트릭이 단위 테스트에서 `NullMetrics` 스파이로 관측; FROZEN 경로 diff 0 | 250 |
| PLT-11 | `config/observability/alert_rules.yaml` + `docs/runbooks/RB-01..08.md` + `test_alert_rules_reference_known_metrics.py` | 04 | yaml 파싱·메트릭 참조 테스트 통과 | 600(문서) |
| PLT-12 | `src/api/contracts/error_codes.py`, `envelope.py`, `pagination.py` + 단위 테스트 | 01 | 접두 화이트리스트·HTTP 매핑·`page=0` negative | 300 |
| PLT-13 | `src/api/contracts/exception_mapping.py`, `handlers.py` + `main.py` 설치(PM) | 12 | `ConcurrencyConflictError` raise → 409 봉투; 미지 예외 → 500 봉투 + trace_id | 250 |
| PLT-14 | `src/api/contracts/idempotency.py` + `core/idempotency.py` 수정 + M2 `idempotency_keys_scope_digest` | 12 | `test_idempotency_digest.py`; 기존 `test_idempotency.py` 통과 | 270 |
| PLT-15 | 금전 라우트 이관 6곳(marketplace purchase, admin confirm-payment, wallet, executions create/start/convert, portfolio rebalance, paper_control 5개 → 헤더 규격) | 14 | 각 라우트 헤더 없음 → 400; 재생 헤더; 기존 라우터 테스트 통과 | 300 |
| PLT-16 | `src/api/versioning.py` + `scripts/export_openapi.py` + `contracts/openapi/v1.json` 최초 스냅샷 + `check_openapi_compat.py` | 13 | `python scripts/check_openapi_compat.py` exit 0; 필드 삭제 시뮬레이션 exit 1 | 350 |
| PLT-17~21 | 레거시 라우터 15개 봉투·예외 이관(3개씩 5리프: auth/users/exchange_credentials → marketplace/strategy_builder/suitability → executions/portfolio/reports → notifications/alerts/device_tokens/wallet → admin + foundation 8개의 `HTTPException` 제거) | 13,16 | `test_envelope_everywhere.py`·`test_no_raw_http_exception.py` 통과; 스냅샷 재생성은 **MINOR만**(필드 추가) | 각 ≤300 |
| PLT-22 | `src/services/auth/lockout.py` + `auth_service.py` 수정 + `test_lockout_atomic.py` | 13 | gather 10회 정확 카운트, 423 + retry_after | 200 |
| PLT-23 | M3 `auth_session` + `session_repository.py` + `tokens.py` | 22 | 단위: kid 회전, alg 고정; 통합: rotate 재사용 감지 | 320 |
| PLT-24 | `auth/login.py`, `refresh.py`, `logout.py` + `routers/auth.py`·`deps.py` 수정 | 23 | `test_sessions.py`; 기존 `test_auth_router.py`는 토큰 쌍 응답으로 갱신 | 350 |
| PLT-25 | `rate_limit/policy.py`, `limiter.py`, `api/middleware/rate_limit.py` + `main.py`(PM) | 12 | `test_rate_limit_storm.py`; conftest override로 기존 테스트 무영향 | 260 |
| PLT-26 | M4 `tenant_and_membership`(tenant, tenant_membership, backfill personal tenant id=user_id, 부분 UNIQUE) + `trust/domain` 수정 + `rules/membership.py` | — | `alembic upgrade head && downgrade -1` 왕복; 기존 trust 테스트 통과 | 300 |
| PLT-27 | `ports/membership_repository.py` + `adapters/postgres_membership_repository.py` | 26 | 통합 CRUD + 조건부 전이 충돌 테스트 | 280 |
| PLT-28 | `application/resolve_tenant_context.py` + `contracts/v1.py` MINOR + `foundation_deps.py` 수정 + fixture 호환 테스트 | 27,05 | `X-Tenant-Id` 없음 → personal; 비회원 → 403 `AUTH_TENANT_MISMATCH`; v1 fixture 파싱 | 250 |
| PLT-29 | `grant/suspend/revoke_membership.py` + `trust_memberships.py` 라우터 + 적대 테스트 | 28,24 | last-owner 거부, 세션 revoke 부작용, cross-tenant 403 | 400 |
| PLT-30 | `src/db/roles.sql` + M5 `rls_policies`(tenant_id 컬럼 테이블 전수) + `core/db/tenant_scope.py` + `test_rls_*` 3종 | 26 | CI Postgres에서 `aios_app` role 생성 스텝 추가; WHERE 없는 SELECT 0행 | 350 |
| PLT-31 | `key_ring.py` + `encryption.py` 포맷 변경 + 단위 테스트 | — | 레거시 토큰 복호, LIVE 변수 기동 거부 | 220 |
| PLT-32 | `envelope.py`(봉투 암호화), `secret_ref.py`, `secret_handle.py` | 31 | seal/open/rewrap 왕복; 핸들 종료 후 bytearray 0 | 260 |
| PLT-33 | M6 `exchange_credentials_key_version_scope`(key_id, scope, UNIQUE 교체) + `exchange_credential_service.py`·`credential_resolver.py` 수정 | 32 | 기존 자격증명 테스트 통과(legacy kid 복호), `test_secret_scope_isolation.py`, `test_log_leak.py` | 350 |
| PLT-34 | `scripts/rotate_credential_keys.py` + `test_key_rotation.py` + RB-05 | 33 | 100행 회전 멱등·중단 재실행 | 200 |
| PLT-35 | M7 `break_glass_grant` + `break_glass.py` + `admin_deps.py` 수정(MFA 필수) | 24 | 자기승인·만료·이중소비 거부; admin 라우트 MFA 미달 403 | 300 |
| PLT-36 | `tests/support/db.py` + `conftest.py`·`setup_test_db.py` 수정 + `pytest-xdist` | — | `pytest -n 4` 1회 전체 통과(격리 오류 0) | 250 |
| PLT-37 | `scripts/coverage_ratchet.py` + `coverage-baseline.txt` + CI 스텝 | 36 | 첫 측정치로 baseline 커밋; 인위 하락 시 FAIL | 100 |
| PLT-38 | `scripts/check_migration_chain.py`, `check_zone_diff.py` + CI 스텝 + `test_check_scripts.py` | — | 두 head fixture FAIL; FROZEN diff FAIL | 250 |
| PLT-39 | `.gitleaks.toml` + `config/release_gates.yaml` + `check_release_gate.py` | — | `--stage internal_development` 통과, `internal_paper` 미충족 항목 목록 출력 | 200 |
| PLT-40 | `exchanges/common/http_client.py` Protocol + 믹스인 31개 `type: ignore` 제거(거래소별 3리프: bitget 18 / kis 9 / nh 4) + `warn_unused_ignores` + `check_type_ignore_budget.py`(시작 226 → 목표 ≤ 20) | — | `mypy src` 통과, budget 파일 감소, 기존 어댑터 테스트 통과 | 각 ≤300 diff |
| PLT-41 | `pyproject.toml` ruff 규칙군 확장(`S,BLE,ARG,PGH,T20`) + 위반 정리 | 40 | `ruff check src tests scripts` 통과 | 200 |
| PLT-42 | `docs/TESTING.md`·`README` 갱신, `.env.example`에 `JWT_SIGNING_KEYS`, `CREDENTIAL_ENCRYPTION_KEYS_PAPER`, `AIOS_RUNTIME_MODE`, `AIOS_METRICS_TOKEN` | 전부 | 문서 리뷰 | 100 |

의존 그래프 요약: A(01→11)와 C(12→21)는 병렬 가능(PLT-05/13만 교차). B는 12·24 이후. D(36~41)는 독립이며 **가장 먼저 착수해도 된다**(PLT-36·38은 다른 세션의 안정성을 즉시 높인다). `main.py` 수정은 PLT-05/08/09/13/25 다섯 번 — PM이 한 번에 묶어 커밋해도 된다.

---

## 10. 미확정·리스크

1. **RLS와 커넥션 풀.** `SET LOCAL`은 트랜잭션 범위라 안전하지만, 기존 40여 서비스는 `pool.acquire()`로 트랜잭션 없이 쿼리한다 → 그 경로는 `app.tenant_id` 미설정 → RLS 정책이 0행을 반환해 **기존 기능이 깨진다.** 결정: PLT-30은 foundation 8개 컨텍스트 테이블만 RLS 적용(전부 repo 경유), 레거시 테이블(`orders`, `positions`, `strategy_executions` 등 `user_id` 컬럼)은 **정책은 만들되 `ENABLE`하지 않고** 서비스가 `tenant_transaction`으로 이관될 때마다 테이블 단위로 ENABLE한다(리프별). 이관 완료 전까지 레거시 테이블 격리는 코드(`WHERE user_id`)에만 의존 — 감사 §8 "양호" 판정 유지.
2. **`aios_app` role 생성 권한.** 마이그레이션이 role을 만들면 CI/로컬/운영 권한 모델이 갈린다. `src/db/roles.sql`을 환경 부트스트랩(§16.12-A 순서에 `psql -f` 1단계 추가)으로 두고, `readyz`가 `current_user`가 owner이면 `rls_enforced=false` check 실패로 노출한다. 운영 role 이름·비밀 관리는 미확인.
3. **Python에서 비밀 zeroize.** `str`은 불변이라 메모리에서 지울 수 없다. `SecretHandle`은 `bytearray`까지만 보장하고, 어댑터(`_BitgetHTTPClient`)가 `api_secret`을 `str`로 보관하는 현재 구조는 PLT-33에서 `bytes`로 바꾸되 `hmac.new(key_bytes, ...)`가 매 요청 복사본을 만든다 — "필요한 시간만"은 **참조 수명** 기준이지 물리 메모리 기준이 아님을 정직하게 기록.
4. **rate limiter 분산.** in-process 토큰 버킷은 프로세스가 2개가 되는 순간 2배 허용. 05번 §5.2와 동일한 전환 전략(포트 뒤 Redis 어댑터)이지만 Redis 도입 시점 미확정. `readyz`에 `rate_limiter_backend=memory`를 노출해 운영자가 알게 한다.
5. **트레이스 백엔드.** 108 §6과 동일 — 필드만 고정, 수집기(Loki/Tempo/OTel) 미정. `traceparent`는 읽기만 하고 span은 만들지 않는다(OTel SDK 도입은 별도 결정).
6. **422 vs 400.** 73 §5(422 `VALIDATION_*`)와 15 §15.3(400) 충돌. 이 문서는 15를 택했다(프론트엔드 17번이 15를 기준으로 구현). 73번 개정 필요 — AIOSproject 저장소 소유.
7. **JWT 알고리즘.** HS256 유지(단일 발급자). 서비스 간 검증이 생기면 RS256/EdDSA로 MAJOR(kid 있으므로 무중단 전환 가능). 미확정.
8. **`exchange_credentials` UNIQUE 교체**(`(user_id, exchange)` → `(user_id, exchange, scope)`)는 `ON CONFLICT` 절을 바꾸므로 PLT-33이 서비스와 마이그레이션을 **같은 커밋**에 넣어야 한다. 프론트엔드가 scope를 모르는 동안 기본 PAPER.
9. **커버리지 baseline 값**과 **type-ignore 예산 시작값(226)**은 PLT-36/40 착수 시점 실측으로 갱신.
10. **break-glass 2인 승인**은 정책문서 4.9 "플랫폼 레벨 2인" 미해결(13 §13.1)과 같은 인선 문제 — 운영자 1인 체제(ADR-2026-08-10)에서는 승인자 부재로 grant가 불가능하다. 1인 체제 동안은 `approved_by`를 **동일인 불가 + 사후 24h 내 사용자(소유자) 확인** 으로 완화할지 PM 결정 필요. 미확정.
11. **Deprecation/Sunset 기간**(레거시 경로 alias 유지 기간)은 프론트엔드 이관 속도에 종속. 최소 1 배포 주기(107 §4-3), 실제 날짜 미확정.
