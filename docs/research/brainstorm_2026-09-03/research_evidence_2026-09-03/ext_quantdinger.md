# QuantDinger 코드 레벨 분석 — Enterprise Trading-OS 설계 스터디

- 분석 대상: `C:/Users/aiaa1/AppData/Local/Temp/claude/.../scratchpad/ext/QuantDinger`
- Repo: https://github.com/OpenByteInc/QuantDinger
- License: **Apache License 2.0** (`LICENSE`, 표준 Apache-2.0 전문, NOTICE 요구사항 포함)
- VERSION 파일: `5.0.17`
- Git 상태: 로컬 clone은 `git rev-parse --is-shallow-repository` → `true`, 로그에 커밋 1개(`bd169b4c`, 2026-09-02, "fix: stabilize private execution streams")만 노출됨 — shallow/squashed snapshot이라 실제 히스토리 전체는 아님. `CONTRIBUTORS.md`에는 외부 PR 기여자(@leonideos MOEX 데이터소스, @lollipopkit OKX precision fix, @likawa3b auth/secret hardening 등) 다수가 기록돼 있어 실제로는 다인 프로젝트.
- 언어/규모: Python 백엔드(`backend_api_python/app`) 약 **25,904 LOC**, MCP 서버(`mcp_server/src`) 약 **1,700 LOC**(테스트 제외), 저장소 전체 829 파일 / 약 16MB. 프런트엔드(Vue)는 별도 저장소로 분리되어 있고 이 clone에는 포함되지 않음(도커 이미지로만 pull).

---

## 1. 아키텍처 개요

`docs/architecture/ARCHITECTURE.md`와 `docs/architecture/PROCESS_ROLES_AND_TASKS.md`가 "실행 표면(Runtime Surfaces)"과 "프로세스 역할"을 문서로 명시해 두고 있다. 하나의 백엔드 이미지를 프로세스 역할별로 다르게 기동하는 구조다.

`docs/architecture/PROCESS_ROLES_AND_TASKS.md:5-12`
```
| Role | Command | Responsibility |
| API | `gunicorn -c gunicorn_config.py run:app` | HTTP, authentication, validation, durable command submission |
| Migration | `python -m app.commands.migrate` | Fail-fast schema application before services start |
| Trading | `python -m app.commands.trading_worker` | Strategy runtimes, pending orders, grid fills, exchange connections |
| Scheduler | `python -m app.commands.scheduler` | Portfolio monitoring, deployment schedules, payment scans, signal alerts |
| Celery Worker | `celery -A app.celery_app:celery_app worker` | AI, backtests, reports, and maintenance jobs |
| Celery Beat | `celery -A app.celery_app:celery_app beat` | Periodic maintenance dispatch |
```

`docker-compose.yml`이 이 역할들을 각각 독립 컨테이너로 기동한다(`backend`, `trading-worker`, `scheduler-worker`, `celery-worker`, `celery-beat`, 그리고 `migration`은 1회성 init 컨테이너). 각 워커는 `QD_PROCESS_ROLE` 환경변수로 자기 역할을 식별하며(`docker-compose.yml:223,319,350,382,412`), `depends_on: migration: condition: service_completed_successfully`로 스키마 적용 후에만 기동하도록 강제한다(`docker-compose.yml:160-166, 306-312`). Redis는 캐시용(`redis`, evictable, `allkeys-lru`)과 Celery 브로커용(`redis-jobs`, AOF 영속화 + `noeviction`)으로 **명시적으로 분리**되어 있다(`docker-compose.yml:116-158`, `PROCESS_ROLES_AND_TASKS.md:36-38`).

거버넌스 규칙이 문서로 못박혀 있는 점이 눈에 띈다: "HTTP processes never start trading or scheduler threads", "Celery must not own long-lived strategy loops, exchange polling, broker sessions, or grid runtime state"(`PROCESS_ROLES_AND_TASKS.md:16,34`). 즉 Celery는 유한한 job(백테스트, AI 분석, 정리 작업)만 소유하고, 상태를 가진 장기 실행 루프(전략 loop, 거래소 폴링)는 반드시 `trading-worker`가 소유한다.

DB 테이블(마이그레이션 `20260713_process_roles_and_strategy_commands.sql`)이 이 분리를 뒷받침한다:

- `qd_strategy_commands` — start/stop/restart/reconcile **커맨드 큐** (`idempotency_key VARCHAR(128) UNIQUE`, `available_at`, `claimed_by`, `lease_expires_at`, `attempts`) — §8에서 상세.
- `qd_strategy_runtime_leases` — 전략별 런타임 소유권 리스(`fencing_token`, `heartbeat_at`).
- `qd_worker_heartbeats` — 프로세스별 헬스(`role IN (api,trading,scheduler,celery,celery-beat)`, `heartbeat_at`).
- `qd_process_leases` — 글로벌 리더(거래소 폴러, 스케줄러 루프) 리더 선출용 리스.

오퍼레이션 엔드포인트도 문서화되어 있다: `/api/health`(liveness), `/api/health/ready`(PG+Celery broker readiness), `/api/health/workers`(트레이딩/스케줄러/셀러리 하트비트 요약) (`PROCESS_ROLES_AND_TASKS.md:49-53`).

**AIOS 시사점**
- 프로세스 역할을 문서(표)로 명시하고, "이 역할은 이걸 하면 안 된다"는 부정형 규칙까지 적어둔 점은 AI 코딩 에이전트가 실수로 잘못된 레이어에 코드를 넣는 것을 막는 데 효과적 — AIOS의 아키텍처 문서도 이런 "ownership 표 + 금지 규칙" 형식을 채택할 가치가 있다.
- 캐시 Redis와 job-queue Redis를 evict 정책 기준으로 분리한 것은 사소해 보이지만 실전에서 큐 유실을 막는 중요한 설계 — AIOS도 캐시/큐를 같은 Redis 인스턴스에 두지 않는 원칙을 명문화해야 한다.
- 다만 이 구조는 여전히 "단일 Postgres" 의존이 강함(리스/커맨드/하트비트 모두 PG 트랜잭션 기반) — 기관급으로 가려면 PG 자체의 HA/failover 전략이 별도로 필요하며 이 저장소 문서에는 그 부분이 없다.

---

## 2. Agent Gateway — 토큰 모델과 미들웨어

설계 문서 3종(`docs/agent/AGENT_QUICKSTART.md`, `AI_INTEGRATION_DESIGN.md`, `AGENT_ENVIRONMENT_DESIGN.md`)과 `docs/agent/agent-openapi.json`이 `/api/agent/v1/...` 아래의 별도 API 표면을 정의한다. 설계 문서 자체가 인상적인 부분은 "capability class" 개념을 명문화한 표다.

`docs/agent/AI_INTEGRATION_DESIGN.md:49-58`
```
| Class | Examples | Default for new tokens |
| R — Read | Market data, klines, indicators... | Allowed |
| W — Workspace write | Create/update Strategy V2 deployments... | Allowed (workspace-scoped) |
| B — Backtest / simulation | Run Strategy API V2 backtests | Allowed |
| N — Notifications & misc side-effects | Send test notification, write prefs | Allowed (rate-limited) |
| C — Credentials | Store/rotate exchange or LLM credentials | Denied by default; admin-only |
| T — Trading / capital | Quick trade, place/cancel order... | Denied by default; per-tenant opt-in + allowlist |
```

구현은 `backend_api_python/app/utils/agent_auth.py`(799줄) 한 모듈에 인증·스코프·감사로그·아이도포턴시·레이트리밋이 모두 모여 있다. 토큰 자체는 JWT가 아니라 **opaque random token을 해시해 DB에 저장**하는 방식이다.

`app/utils/agent_auth.py:208-218`
```python
def generate_token() -> tuple[str, str, str]:
    """Generate a new agent token.
    Returns:
        (full_token, token_prefix, token_hash). Only the hash is stored;
        the full token is shown to the operator exactly once.
    """
    body = secrets.token_urlsafe(32).rstrip("=")
    full = f"{TOKEN_PREFIX}{body}"
    prefix = full[: len(TOKEN_PREFIX) + 8]      # qd_agent_XXXXXXXX
    return full, prefix, _hash_token(full)
```
해시는 SHA-256(`_hash_token`, `agent_auth.py:204-205`)이며, 조회는 `qd_agent_tokens.token_hash` UNIQUE 인덱스로 lookup한다(`agent_auth.py:360-376`). 토큰 스키마(`agent_auth.py:64-81`)는 `scopes`(CSV), `markets`/`instruments` allowlist(CSV, `*`=전체), `paper_only BOOLEAN DEFAULT TRUE`, `rate_limit_per_min`, `max_order_notional`/`max_daily_notional`, `status`, `expires_at`를 갖는다.

`agent_required(scope)` 데코레이터(`agent_auth.py:603-736`)가 매 요청마다 순서대로 검사한다: (1) Bearer 헤더 파싱 → (2) 토큰 조회 → (3) `status == 'active'` → (4) `expires_at` 만료 체크 → (5) 요청한 scope가 토큰의 scope 집합에 포함되는지 → (6) per-token + per-tenant 레이트리밋(Redis Lua `INCR`+`EXPIRE`, 실패 시 in-process fallback, `agent_auth.py:296-333`) → (7) mutating 요청이면 Idempotency-Key 검증(§3) → (8) 핸들러 실행 → (9) 모든 분기가 `qd_agent_audit`에 감사로그를 남김(`_audit`, `agent_auth.py:442-480`, `_REDACT_KEYS` 셋으로 password/token/secret 류 자동 마스킹).

인간 JWT 인증(`app/utils/auth.py:55-86`)은 이와 완전히 분리된 파이프라인이다 — stateless HS256 JWT(`exp`, `role`, `token_version`)로 서버 측 저장 없이 서명 검증만 하며, `/api/agent/v1`에는 통하지 않는다(`AI_INTEGRATION_DESIGN.md:121`: "Existing JWT user sessions are **not** valid for `/api/agent/v1` and vice versa"). 즉 에이전트 토큰은 (a) DB에 저장되어 언제든 즉시 revoke 가능하고 (b) scope/allowlist/rate-limit/notional 한도를 가진 세밀한 capability token인 반면, 인간 JWT는 세션 편의를 위한 넓은 권한의 stateless 토큰 — **폐기 가능성(revocability)과 세분화(granularity)를 기준으로 두 인증 체계를 의도적으로 분리**했다.

토큰 발급/조회/폐기는 `app/services/agent_token_service.py`가 담당하며, 라이브 트레이딩 스코프 발급 시 리스크 공시 동의를 강제한다.

`app/services/agent_token_service.py:133-144`
```python
paper_only = bool(body.get("paper_only", True))
if "T" in scopes and not paper_only:
    paper_only = False
    if not body.get("ack_live_trading_risk"):
        raise TokenIssueError(
            "Issuing a live-eligible T-scope token requires "
            "ack_live_trading_risk=true after reviewing the risk disclosure.",
            code=400,
            details="Set paper_only=true for paper-only trading, or pass "
            "ack_live_trading_risk=true to confirm you accept live-trading risks.",
        )
```
또한 `C`(credentials) 스코프는 self-service 발급 경로(`issue_agent_token(..., allow_c_scope=False)`)에서 원천 차단된다(`agent_token_service.py:117-124`, `USER_SCOPES = frozenset(s for s in ALL_SCOPES if s != SCOPE_C)`).

**AIOS 시사점**
- "capability class" 알파벳 라벨(R/W/B/N/C/T)로 스코프를 소수 집합으로 고정하고 문서·코드·DB 세 곳에서 동일 용어를 쓰는 방식은 재사용 가치가 높다 — AIOS도 에이전트 권한을 자유 텍스트 permission 문자열이 아니라 닫힌 enum으로 강제해야 감사·리뷰가 쉬워진다.
- opaque token + server-side hash 저장은 JWT 대비 revoke가 즉시 가능하다는 강점이 있다 — AIOS의 에이전트 인증도 stateless JWT보다는 이 패턴을 우선 고려할 만하다.
- 인간 인증과 에이전트 인증을 물리적으로 다른 모듈/미들웨어로 분리해 "교차 사용 불가"를 코드로 강제한 점은 보안 사고(스코프가 넓은 인간 세션이 에이전트 엔드포인트로 새는 것)를 원천 차단한다.
- 다만 레이트리밋의 Redis 실패 시 in-memory fallback(`_memory_rate_limit`)은 멀티 프로세스(gunicorn worker 여러 개) 환경에서 프로세스별로 카운터가 분리돼 실제 한도보다 더 많은 트래픽을 허용할 수 있음 — 기관급이라면 fallback 시 "fail closed"(거부) 정책이 더 안전할 수 있다는 점은 보완 필요.

---

## 3. Idempotency-Key 처리

에이전트 게이트웨이는 mutating 요청(`GET/HEAD/OPTIONS` 이외이면서 scope가 `W`/`B`/`N`/`T`)에 대해 `Idempotency-Key` 헤더를 강제한다(`agent_auth.py:661-664`). 저장 테이블은 `qd_agent_idempotency`이며, **키 스코핑은 `(agent_token_id, method, route, idempotency_key)` 4중 UNIQUE**로 이루어진다 — 즉 토큰 단위(사실상 user+token) + HTTP method + route 조합별로 키가 독립적이다.

`app/utils/agent_auth.py:148-163`
```python
CREATE TABLE IF NOT EXISTS qd_agent_idempotency (
    id BIGSERIAL PRIMARY KEY,
    agent_token_id INTEGER NOT NULL REFERENCES qd_agent_tokens(id) ON DELETE CASCADE,
    method VARCHAR(8) NOT NULL,
    route VARCHAR(200) NOT NULL,
    idempotency_key VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'started',
    response_body JSONB,
    response_status INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(agent_token_id, method, route, idempotency_key)
);
```

요청 본문 재현 방지를 위해 `request_hash`(SHA-256 of `METHOD\nPATH\nquery\nbody`, `_request_fingerprint`, `agent_auth.py:499-509`)를 같이 저장하고, 동일 키로 **다른** 본문이 들어오면 `409 Idempotency-Key was already used with a different request`로 거부한다(`agent_auth.py:690-697`). 예약 로직(`_reserve_idempotency`, `agent_auth.py:512-575`)은 `INSERT ... ON CONFLICT DO NOTHING`으로 원자적 선점을 시도하고, 이미 `started` 상태인 행이 있으면 진행 중 요청으로 보고 `409 ... still in progress`를 반환한다(재시도 가능·retriable=true). 단, `AGENT_IDEMPOTENCY_IN_PROGRESS_TTL_SEC`(기본 900초)를 넘겨 `started`로 고아가 된 행은 stale로 간주해 재사용을 허용한다(`agent_auth.py:540-567`) — 워커 크래시로 완료 처리가 안 된 요청이 영구히 막히는 것을 방지하는 TTL 기반 복구 장치다.

성공 응답은 `_complete_idempotency`가 `response_body`/`response_status`와 함께 `status='completed'`로 갱신하며(`agent_auth.py:578-600`), 이후 동일 키 재요청은 저장된 응답을 그대로 재생하고 `Idempotent-Replayed: true` 헤더를 붙인다(`agent_auth.py:707-714`).

이와 별개로 라우트 레벨에서 쓰는 `with_idempotency(kind)` 컨텍스트 매니저(`agent_auth.py:741-773`)는 `qd_agent_jobs` 테이블을 `(agent_token_id, kind, idempotency_key)` UNIQUE로 조회해 "이미 실행된 job"을 찾는 애플리케이션 레벨 idempotency이며, `quick_trade.py:449-454`처럼 실제 주문 실행 전에 "duplicate" 여부를 한 번 더 판별하는 용도로 쓰인다. 즉 **미들웨어 레벨(HTTP 응답 재생) + 서비스 레벨(도메인 job 재생) 이중 방어**다.

**AIOS 시사점**
- idempotency 키 스코프를 "토큰 단위"로 잡은 것은 합리적 — 사용자 단위로만 잡으면 같은 사용자의 여러 에이전트/세션이 키를 충돌시킬 수 있다.
- request_hash로 "같은 키, 다른 payload"를 명시적으로 409 거부하는 방어는 필수 패턴으로 채택할 만하다.
- in-progress에 TTL을 두어 죽은 요청이 영구 락이 되지 않게 한 것은 실전에서 중요한 디테일이지만, 이 TTL 동안 클라이언트가 계속 409/retriable 응답만 받는 구조라 "정확히 몇 초 후 안전하게 재시도 가능"이라는 계약을 클라이언트 SDK/MCP 레벨에도 노출해야 완성도가 높아진다(현재 MCP 서버는 이 재시도 시맨틱을 별도로 설명하지 않음).

---

## 4. 라이브 트레이딩 게이트 — 다중 조건 인가

라이브 주문이 나가려면 **토큰 스코프 + paper_only 플래그 + 서버 kill switch + notional 한도**가 전부 통과해야 하며, 이 계약이 라우트 모듈 docstring에 명시돼 있다.

`app/routes/agent_v1/quick_trade.py:1-11`
```python
"""Trading (class T) — paper-only by default, hard-gated for live execution.

Live execution from agents requires *all* of the following:
  1. Token has scope `T`.
  2. Token has `paper_only=false` (operator must flip explicitly).
  3. Server-side env `AGENT_LIVE_TRADING_ENABLED=true` (deployment kill switch).

Until live is unlocked, this endpoint records orders to `qd_agent_paper_orders`
using the latest market price as the simulated fill — so AI workflows can
exercise the round trip without ever touching exchange credentials.
"""
```

실제 게이트 코드(`quick_trade.py:456-480`):
```python
if not paper_only() and not _live_trading_kill_switch():
    return error(
        501,
        "Live agent trading is disabled by AGENT_LIVE_TRADING_ENABLED",
        http=501,
    )

if not paper_only():
    reference_price = ...
    notional = qty_f * reference_price
    reserved, limit_state = _reserve_live_notional(notional)
    if not reserved:
        return error(403, "Live agent trading notional limit exceeded",
                      details=limit_state, http=403)
```

`_reserve_live_notional`(`quick_trade.py:128-190`)은 `qd_agent_tokens`에서 `max_order_notional`/`max_daily_notional`을 `SELECT ... FOR UPDATE`로 잠근 뒤, 주문 notional이 per-order 한도를 넘는지, 그리고 당일 `qd_agent_notional_reservations`(status IN reserved/executed) 합계 + 이번 주문이 daily 한도를 넘는지 검사한다. 두 검사를 모두 통과해야 예약 행을 삽입하며, 이 예약 자체도 `(agent_token_id, idempotency_key)` UNIQUE라 재시도 안전(idempotent)하다. 시장/종목 allowlist 체크(`market_allowed`/`instrument_allowed`, `agent_auth.py:786-793`)는 그 이전 단계에서(`quick_trade.py:444-447`) 이미 걸린다.

정리하면 인가 체인은: **scope(`T`) → market/instrument allowlist → idempotency 예약 → paper_only 여부 → (live인 경우) 서버 kill switch → notional 예약(per-order, daily) → 실주문**. 이 중 하나라도 실패하면 401/403/429/501 중 하나로 즉시 거부된다.

에이전트가 **자기 자신의 권한을 스스로 상향**할 수 있는지 확인하기 위해 자기서비스 토큰 라우트(`app/routes/agent_v1/me_tokens.py`)를 조사했다. 이 라우트들은 `@login_required`(인간 JWT 전용 데코레이터, `app/utils/auth.py`)로 보호되며 `agent_required`(에이전트 Bearer 토큰)로는 보호되지 않는다.

`app/routes/agent_v1/me_tokens.py:37-50`
```python
@agent_v1_bp.route("/me/tokens", methods=["POST"])
@login_required
def issue_my_token():
    body, err = get_json_or_400()
    if err:
        return err
    user_id = int(get_current_user_id() or 0)
    ...
    data = issue_agent_token(user_id, body, allow_c_scope=False)
```
즉 **에이전트 토큰 자체로는 `/me/tokens`를 호출할 수 없다** — 새 토큰 발급·기존 토큰 스코프 변경(애초에 PATCH 엔드포인트 자체가 없음, 발급/조회/취소만 존재)은 오직 사람의 로그인 세션(JWT)으로만 가능하다. 다시 말해 에이전트는 자신의 스코프를 확장하는 API 표면에 물리적으로 접근할 수 없고, 상위 인간 계정만 새 토큰을 만들거나 기존 토큰을 폐기(`revoke_my_token`)할 수 있다.

**AIOS 시사점**
- "권한 상승 경로 자체가 다른 인증 체계에 있다"는 설계(에이전트 인증 ≠ 토큰 관리 인증)는 프롬프트 인젝션으로 에이전트가 스스로 권한을 늘리는 시나리오를 원천 차단하는 강력한 패턴 — AIOS의 에이전트 권한 관리 API도 반드시 별도의, 더 강한 인증(예: 사람 세션 + 재인증)으로만 접근 가능해야 한다.
- 4단 게이트(스코프/paper_only/서버 kill switch/notional)를 한 함수 안에서 순차 검사하고 각 실패를 별도 HTTP status로 표현한 것은 감사·디버깅 관점에서 우수 — AIOS 주문 실행 경로도 "왜 막혔는지"를 단계별로 구분되는 에러 코드로 노출해야 한다.
- notional 한도가 "토큰당" 한도이지 "포트폴리오/계정 전체"의 리스크 한도(예: 총 익스포저, 레버리지 상한, 상관 자산 집중도)까지는 다루지 않는다 — 기관급으로 가려면 여러 토큰/여러 전략을 넘나드는 계정 레벨 리스크 엔진이 별도로 필요하다는 점이 이 저장소의 한계.

---

## 5. Strategy API V2 — template → compile → save → immutable version → deployment

### 데이터 모델
- `qd_script_templates` — 시스템 시드 템플릿(예: `strategy_v2_single_ma`), `code`, `param_schema JSONB`(마이그레이션 `strategy_v2_templates.sql`).
- `qd_script_sources`(`migrations/init.sql:322-338`) — 사용자별 "현재" 소스: `code TEXT`, `param_schema`, `visibility`, `status`, `metadata JSONB`. **가변(mutable) 헤드**.
- `qd_script_source_versions`(`migrations/init.sql:344-357`) — 버전 스냅샷: `source_id FK`, `version_no INTEGER`, `code`, `param_schema`, `metadata`, `created_at`, **`UNIQUE(source_id, version_no)`**. `updated_at` 컬럼이 없다 — 즉 스키마 설계상 이 테이블에 UPDATE를 하도록 만들어져 있지 않다.
- `strategy_runs`(`migrations/init.sql:2032-2050`) — 배포 실행 인스턴스: `strategy_id`, `source_version_id VARCHAR(64)`, `code_hash VARCHAR(128)`, `parameter_snapshot_json`, `runtime_epoch BIGINT`(재기동 시 증가하는 fencing 성격의 epoch), `runtime_status`.

### 버전 불변성(immutability)이 강제되는 방식
불변성은 **DB 제약이 아니라 애플리케이션 코드 관례**로 강제된다. `app/services/script_source.py`의 `_insert_version`은 항상 `MAX(version_no)+1`을 계산해 새 행을 INSERT만 하고, 코드베이스 전체에서 `qd_script_source_versions`에 대한 UPDATE 문은 발견되지 않는다.

`app/services/script_source.py:104-145`
```python
def _insert_version(self, cur, source_id, user_id, name, description, code,
                     template_key, param_schema, metadata) -> int:
    cur.execute(
        """
        SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version
        FROM qd_script_source_versions
        WHERE source_id = ? AND user_id = ?
        """,
        (int(source_id), int(user_id)),
    )
    row = cur.fetchone() or {}
    version_no = int(row.get("next_version") or 1)
    cur.execute(
        """
        INSERT INTO qd_script_source_versions
          (source_id, user_id, version_no, name, description, code,
           template_key, param_schema, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb, NOW())
        """, (...),
    )
    return version_no
```
"restore"(과거 버전 복원)조차 과거 버전의 `code`를 **다시 새 버전 번호로 insert**하는 방식으로 구현되어 있다(`script_source.py:437-452`, `restored["version_no"] = version_no`) — 즉 "되돌리기"도 히스토리를 지우지 않고 새 항목을 추가하는 append-only 모델이다.

### 컴파일(compile)
`compile_strategy_v2(code)`(`app/services/strategy_v2/contract.py:195-233`)는 사용자 Python 코드를 실제로 실행하되, `app/utils/safe_exec.py`의 화이트리스트 builtin 샌드박스(`build_safe_builtins`) + AST 정적 검증(`_validate_dataframe_truthiness`, `_validate_strategy_api_calls` 등)을 통과해야만 한다.

`app/utils/safe_exec.py:44-59`
```python
# Whitelisted builtins (strict)
# Only pure computational builtins. No I/O, no introspection, no code gen.
_BUILTINS_WHITELIST: Set[str] = {
    'bool', 'int', 'float', 'complex', 'str', 'bytes', 'bytearray',
    'list', 'tuple', 'dict', 'set', 'frozenset',
    'range', 'slice', 'memoryview',
    'abs', 'round', 'pow', 'divmod', 'min', 'max', 'sum',
    'len', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
    ...
```
컴파일 결과 `StrategyManifest`가 만들어지며 `code_hash = sha256(code.strip())`(`contract.py:51-58,301-303`)를 갖는다. 이 매니페스트가 universe/instrument/frequency/leverage/direction_mode 등 전략의 "소유권"을 갖고, 백테스트나 배포 시점에 별도로 이런 필드를 다시 지정할 수 없다(`MCP_SETUP.md:58`: "The strategy manifest owns market, instrument, frequency, warmup, dependency, and leverage scope").

### 배포(deployment)가 버전을 참조하는 방식
`StrategyV2DeploymentService.save`(`app/services/strategy_v2/deployment.py:20-34`)는 `sourceId`로 **현재 소스**(qd_script_sources의 최신 code, 특정 version_no가 아님)를 읽어 `compile_strategy_v2`를 다시 실행하고, 그 결과 `manifest.code_hash`로 "이 코드가 최근에 백테스트를 통과했는지"를 검증한다.

`app/services/strategy_v2/deployment.py:20-34`
```python
def save(self, *, user_id: int, payload: dict[str, Any], strategy_id: int | None = None) -> int:
    source_id = int(payload.get("sourceId") or 0)
    source = get_script_source_service().get_source(source_id, user_id=user_id) if source_id else None
    if not source:
        raise StrategyV2ContractError("strategyV2.sourceNotFound")
    program = compile_strategy_v2(str(source.get("code") or ""))
    manifest = program.manifest
    ...
    if adaptation.get("requires_backtest") and not self._has_current_version_backtest(
        user_id=int(user_id), source_id=source_id, code_hash=str(manifest.code_hash or ""),
    ):
        raise StrategyV2ContractError("strategyV2.backtestRequiredForAdaptedStrategy")
```
즉 배포는 "버전 번호"가 아니라 **재컴파일 시점의 code_hash**로 "이 정확한 코드가 백테스트를 통과했는가"를 재확인하는 방식이며, `strategy_runs.code_hash`/`source_version_id`가 실행 시점에 어떤 소스 스냅샷으로 돌고 있는지를 사후 추적 가능하게 기록한다(§1 `migrations/init.sql:2036-2037`).

### start/stop 시맨틱
배포된 전략의 시작/정지는 라우트가 아니라 §1/§8에서 다룬 `qd_strategy_commands` 큐(`start`/`stop`/`restart`/`reconcile`)를 통해 트레이딩 워커가 비동기로 처리한다 — 즉 "배포 저장"과 "런타임 시작"이 명확히 분리된 2단계 프로세스다.

**AIOS 시사점**
- code_hash 기반의 "이 정확한 바이트열이 백테스트를 통과했는가" 검증은 전략 코드와 백테스트 결과 사이의 신뢰 사슬을 만드는 좋은 패턴 — AIOS가 AI가 생성한 전략/워크플로우 코드를 다룬다면 동일하게 "실행 전 hash 재검증"을 채택할 만하다.
- 다만 버전 불변성이 DB 제약(트리거, REVOKE UPDATE, append-only 테이블 권한)이 아니라 "코드에서 UPDATE를 안 쓴다"는 관례에만 의존하는 것은 기관급 감사 관점에서 약점이다 — AIOS는 버전 테이블에 대해 애플리케이션 DB 계정 자체의 UPDATE/DELETE 권한을 제거하거나 DB 트리거로 불변성을 강제해야 한다.
- 사용자 코드를 실제로 `exec()`하는 방식(화이트리스트 builtins + AST 검증)은 강력하지만 여전히 "동적 실행 기반 샌드박스"의 본질적 리스크(사이드채널, 자원 고갈)를 안고 있다 — AIOS는 가능하면 프로세스/컨테이너 격리까지 이중화하는 것이 바람직하다.

---

## 6. MCP 서버 — REST의 얇은 래퍼 확인

`docs/agent/MCP_SETUP.md:3`: "QuantDinger's MCP server wraps the Agent Gateway and keeps the REST API as the source of truth." 코드 레벨에서도 이 주장이 사실임을 확인했다. `mcp_server/src/quantdinger_mcp/server.py`(1,178줄)의 모든 `@mcp.tool()` 함수는 `_get`/`_post`/`_patch`/`_delete` 헬퍼를 통해 `/api/agent/v1/...` HTTP 엔드포인트를 호출할 뿐이다.

`mcp_server/src/quantdinger_mcp/server.py:276-280`
```python
@mcp.tool()
def whoami() -> Any:
    """Return the calling token's identity, scopes, and allowlists."""
    return _get("/api/agent/v1/whoami")
```

`_unwrap`(`server.py:222-232`)이 하는 일은 봉투(envelope) `{"data": ...}` 해제와 `redact_secrets()`(`security.py`) 적용뿐이며, 어떤 도메인 로직(리스크 계산, 인가 판단)도 없다. 40여 개 도구(`list_markets`, `get_klines`, `list_strategies`, `stop_strategy`, `place_quick_order`, `compile_strategy_code`, `save_strategy_source`, `restore_strategy_source_version`, `submit_backtest`, `wait_for_job`, `stream_job_until_done` 등)가 모두 이 패턴을 따른다.

유일하게 순수 프록시를 벗어나는 곳은 `place_quick_order`인데, 여기서도 **인가 로직이 아니라 UX 안전장치**만 추가한다: `confirm_order=false`이면 즉시 400을 반환하고, `whoami()`로 토큰의 `paper_only`를 조회해 `false`인데 `confirm_live_trading=true`가 없으면 역시 400을 반환한다(`server.py:399-423`). 이는 LLM 클라이언트가 "다시 한번 명시적으로 확인 후 호출"하도록 강제하는 이중 확인(double-confirmation) 장치이며, 실제 권한/한도 판단은 여전히 서버(Agent Gateway, §4)가 수행한다 — MCP 계층이 이를 우회하거나 대체하지 않는다.

인증도 REST 토큰을 그대로 위임한다: `QUANTDINGER_AGENT_TOKEN`(업스트림, Agent Gateway용)과 `QUANTDINGER_MCP_AUTH_TOKEN`(인바운드, MCP 리스너 자체 보호용, 32자 이상 강제)이 분리되어 있다(`docs/agent/MCP_SETUP.md:22`). Non-loopback 리스너는 인바운드 토큰 없이는 기본 거부된다.

**AIOS 시사점**
- MCP를 "REST의 얇은 프록시 + 시크릿 리댁션 + 재확인 UX"로만 유지하고 인가/리스크 로직을 절대 복제하지 않는 원칙은 이중 구현으로 인한 정책 드리프트(REST와 MCP의 권한 판단이 어긋나는 문제)를 막는 핵심 설계 — AIOS의 MCP/도구 계층도 이 원칙을 그대로 채택해야 한다.
- `place_quick_order`의 이중 확인 패턴(툴 파라미터로 명시적 확인 플래그를 받는 것)은 LLM이 실수로 위험한 액션을 한 번의 호출로 실행하지 못하게 하는 저비용·고효과 가드레일이다.
- MCP 리스너 자체의 인바운드 토큰과 업스트림 에이전트 토큰을 분리한 것은 "MCP 서버가 뚫려도 업스트림 계정 전체가 노출되지 않는다"는 방어 심도(defense in depth) 관점에서 좋은 예 — 다만 두 토큰 다 탈취되면 결국 동일한 권한이 노출되므로 근본적 완화는 아니라는 한계도 있다.

---

## 7. 시장 데이터 어댑터 — 정규화 패턴

`app/data_sources/base.py`의 `BaseDataSource` 추상클래스가 모든 시장 데이터 어댑터(`crypto.py`, `us_stock.py`, `cn_stock.py`, `hk_stock.py`, `forex.py`, `futures.py`, `moex.py`, `asia_stock_kline.py` 등)가 지켜야 할 **정규화 스키마**를 명시한다.

`app/data_sources/base.py:67-84`
```python
def format_kline(
    self, timestamp: int, open_price: float, high: float,
    low: float, close: float, volume: float,
) -> Dict[str, Any]:
    """Normalize one K-line row while preserving provider price precision; volume keeps two decimals."""
    return {
        'time': timestamp,
        'open': float(open_price),
        'high': float(high),
        'low': float(low),
        'close': float(close),
        'volume': round(float(volume), 2),
    }
```
모든 K-line은 `{time, open, high, low, close, volume}`의 고정 스키마로 수렴하며, `TIMEFRAME_SECONDS` 표(`base.py:15-25`, `1m`~`1W`)로 타임프레임을 초 단위로 통일한다. Provider별 특이 컬럼(거래소 고유 필드, 원본 코드 형식 등)은 **어댑터 내부에만 갇혀 있고 정규화 리턴값에는 노출되지 않는다** — 예를 들어 `tencent.py`의 `normalize_cn_code`/`normalize_hk_code`(중국/홍콩 종목코드 형식 변환)나 `forex.py`의 `normalize_forex_pair_symbol`은 어댑터 진입 전 정규화만 담당하고, `crypto.py`의 CCXT 응답 파싱·페이지네이션(`_get`, OHLCV aggregate) 로직도 최종적으로는 동일한 `format_kline` 리턴 형태로 수렴한다.

품질 검증도 베이스 클래스가 공통 제공한다 — `log_result`(`base.py:140-175`)가 최신 봉의 시각과 현재 UTC 시각의 갭을 타임프레임별 허용 임계값(예: 일봉은 5일, 그 이상은 21일)과 비교해 지연 데이터를 경고 로그로 잡아낸다. `MODULE_BOUNDARIES.md`/`ARCHITECTURE.md`(§1)에서도 "Cache keys must include market, exchange, market type, symbol, timeframe, and limit" 규칙으로 캐시 오염을 방지하도록 명문화되어 있다(`ARCHITECTURE.md:102`).

**AIOS 시사점**
- "정규화된 출력 스키마 하나 + provider별 특이사항은 어댑터 내부 함수로만 존재" 원칙은 멀티 데이터 소스 시스템의 정석 — AIOS가 여러 브로커/거래소/데이터벤더를 통합할 때 그대로 채택할 가치가 있다.
- 데이터 지연 감지를 어댑터 공통 로직(`log_result`)으로 넣어 모든 provider가 자동으로 "이 데이터가 오래됐다"는 관측성을 얻는 것은 좋은 재사용 패턴.
- 다만 이 지연 감지는 로그 경고 수준이며 자동 알람/서킷브레이커로 연결되는지는 확인되지 않음(`circuit_breaker.py`가 별도로 존재하긴 하나 요청 실패율 기반으로 보임) — 기관급이라면 "데이터 신선도" 자체를 서킷브레이커/전략 일시정지 트리거로 승격할 필요가 있다.

---

## 8. Reconciliation / Worker Lease / Heartbeat / 재기동 복구

트레이딩 워커(`app/workers/trading.py`, `TradingWorker`)가 이 저장소에서 가장 정교한 분산 조정(coordination) 코드다. 핵심 개념 3가지가 함께 동작한다.

**(1) 커맨드 클레임 — `SKIP LOCKED` 기반 경쟁 없는 큐 소비.**
`app/services/strategy_command_repository.py:107-146`의 `claim_next`는 CTE + `FOR UPDATE OF command SKIP LOCKED`로 여러 워커 인스턴스가 동시에 폴링해도 같은 커맨드를 중복 실행하지 않도록 하고, `command_type IN ('start','reconcile')`이 아닌 한 해당 전략의 런타임 리스를 가진 워커(또는 리스가 만료된 경우)만 커맨드를 가져가도록 제한한다.

**(2) 런타임 리스 + fencing token.**
`acquire_strategy_lease`(`strategy_command_repository.py:231-263`)는 `ON CONFLICT (strategy_id) DO UPDATE`로 리스를 갱신하되, **소유자가 바뀌는 경우에만 `fencing_token`을 +1**한다:
```sql
fencing_token = CASE
    WHEN qd_strategy_runtime_leases.owner_id = EXCLUDED.owner_id
        THEN qd_strategy_runtime_leases.fencing_token
    ELSE qd_strategy_runtime_leases.fencing_token + 1
END,
```
(`strategy_command_repository.py:242-246`) — 만료된 리스를 다른 워커가 탈취(takeover)하면 fencing token이 증가해, 구 소유자가 뒤늦게 보낸 주문/명령이 새 소유자의 epoch과 어긋난다는 것을 판별할 수 있게 한다(§5의 `strategy_runs.runtime_epoch`와 같은 계열의 개념).

**(3) 하트비트 + 재기동 시 복구.**
`TradingWorker.run_forever`(`app/workers/trading.py:42-69`)는 시작 즉시 `restore_desired_strategies()`(76-91줄)를 호출한다:
```python
def restore_desired_strategies(self) -> None:
    from app.services.strategy import StrategyService
    rows = StrategyService().get_running_strategies_with_type()
    restored = 0
    for row in rows or []:
        strategy_id = int(row["id"])
        if not self._acquire_runtime(strategy_id):
            continue
        if self.executor.start_strategy(strategy_id):
            restored += 1
        else:
            self.repository.release_strategy_lease(strategy_id=strategy_id, owner_id=self.worker_id)
            StrategyService().update_strategy_status(strategy_id, "stopped")
```
즉 DB에 `status='running'`으로 기록된 전략을 **프로세스 재기동 시 자동으로 다시 인메모리 런타임에 로드**하며, 리스 획득에 실패하면(다른 워커가 이미 소유) 건너뛴다. 메인 루프에서는 매 tick마다 `_heartbeat()`(10초 간격, `qd_worker_heartbeats` 갱신 + `fail_exhausted_commands` 정리)와 `_renew_runtime_leases()`(리스 기간의 1/3마다 갱신, 갱신 실패 시 즉시 `executor.stop_strategy` — 리스를 잃은 전략은 즉각 로컬에서 중지)를 실행한다(`trading.py:180-219`). 글로벌 리더(거래소 폴러 등)는 `qd_process_leases`로 별도 선출되며, 리스를 잃으면 프로세스 자체를 `self._stop.set()`으로 종료시킨다(`trading.py:231-262`).

프로세스 종료(SIGINT/SIGTERM) 시에는 `_shutdown_local_runtimes()`가 로컬에서 돌던 모든 전략을 정지시키고 리스를 명시적으로 반환한다(`trading.py:221-229`) — graceful shutdown이 리스 반환까지 포함한다.

**AIOS 시사점**
- `SKIP LOCKED` + 소유자 변경 시에만 fencing token 증가 + 주기적 하트비트 + 재기동 시 "desired state(DB의 running 상태)"를 다시 읽어 복구하는 조합은 분산 워커 오케스트레이션의 교과서적 구현 — AIOS가 자체 워커 풀을 운영한다면 그대로 채택할 가치가 매우 높다.
- "리스 갱신 실패 시 즉시 로컬 중지"라는 fail-safe 방향(계속 돌리다가 이중 실행되는 것보다 멈추는 쪽을 택함)은 자금이 걸린 시스템에서 올바른 기본값이다.
- 이 모든 조정 로직이 Postgres 트랜잭션 하나에 의존한다 — PG가 단일 장애점이 될 수 있으므로, 기관급에서는 리더 선출/리스 메커니즘을 별도의 합의 시스템(예: etcd, Zookeeper) 이중화까지 검토하거나 최소한 PG 자체의 HA 구성을 architecture 문서에 명시해야 한다.

---

## 9. 프로덕션 하드닝 — Dockerfile, CI

### Dockerfile
`backend_api_python/Dockerfile`은 기본 이미지에서 non-root 사용자를 만들고(`groupadd/useradd uid=10001`), `gosu`로 권한을 낮춰 엔트리포인트가 실행되도록 한다(`docker-entrypoint.sh:143: exec gosu quantdinger "$@"`). 기본 `docker-compose.yml`에는 `no-new-privileges:true`, `pids_limit`, 로그 로테이션(`x-backend-runtime`, `docker-compose.yml:38-48`)이 anchor로 공통 적용된다.

더 엄격한 하드닝은 opt-in 오버레이 `docker-compose.production.yml` 전체에 있다:

`docker-compose.production.yml:1-16`
```yaml
services:
  migration: &locked-backend
    user: "10001:10001"
    read_only: true
    cap_drop:
      - ALL
    tmpfs:
      - /tmp:rw,noexec,nosuid,nodev,size=64m
      - /home/quantdinger:rw,noexec,nosuid,nodev,size=16m
    volumes:
      - backend_logs:/app/logs
      - backend_data:/app/data
      - ./backend_api_python/.env:/app/.env:ro
    mem_limit: ${MIGRATION_MEMORY_LIMIT:-512m}
    cpus: ${MIGRATION_CPU_LIMIT:-1.0}
```
모든 백엔드 계열 컨테이너(backend, trading-worker, scheduler-worker, celery-worker, celery-beat)가 이 YAML 앵커를 상속해 **non-root 고정 UID, read-only rootfs, 모든 Linux capability 제거, `noexec/nosuid/nodev` tmpfs, `.env`를 읽기전용으로만 마운트, 컨테이너별 mem/cpu 한도**를 일괄 적용받는다. CI(`basic-ci.yml:165-166`)가 이 오버레이 조합(`docker-compose.yml -f docker-compose.production.yml -f docker-compose.observability.yml config -q`)이 항상 유효한 Compose 구성인지 매 PR마다 검증한다.

### CI 워크플로 (`.github/workflows/`)
- `basic-ci.yml` — lint(`ruff`), 구조 가드레일 스크립트(`backend_quality_check.py`), 요구사항 lock 가드(`check_requirements_lock.py`), Postgres+Redis 서비스 컨테이너를 띄운 실제 통합 테스트(2-way sharded pytest), `tests/release_gate` 별도 실행, Compose 구성 4종 검증, 버전/인코딩 일관성 체크.
- `openapi-ci.yml` — **API 호환성 게이트**: `export_openapi.py`로 스펙을 재생성해 커밋된 `docs/api/openapi.yaml`과 diff 비교, Spectral로 두 스펙(사람용/에이전트용) lint, `oasdiff breaking ... --fail-on ERR`로 **breaking change를 CI에서 강제 차단**(`openapi-ci.yml:87-93`).
- `security-ci.yml` — `pip-audit`(의존성 CVE), `bandit -lll -ii`(고심각도 소스 취약점만), Gitleaks(`docker run ... git --redact --verbose /repo`, git 히스토리 전체 시크릿 스캔, 매주 월요일 스케줄 추가 실행), CodeQL(Python) — 3중 보안 스캔이 push/PR/주간 스케줄로 돈다.
- `mcp-ci.yml`, `docker-publish.yml` — MCP 패키지 별도 CI, GHCR 이미지 퍼블리시.
- 저장소 루트의 `.gitleaksignore`는 알려진 false-positive를 관리하는 용도로 별도 존재.

**AIOS 시사점**
- "기본 compose는 관대하게, 프로덕션 hardening은 별도 오버레이 파일 + CI가 그 오버레이의 유효성을 검증"하는 구조는 로컬 개발 경험을 해치지 않으면서 프로덕션 보안을 강제하는 좋은 절충안 — AIOS 배포 템플릿도 이 이원화를 채택할 만하다.
- `oasdiff breaking --fail-on ERR`로 API breaking change를 CI에서 자동 차단하는 것은 에이전트 게이트웨이처럼 외부 계약이 중요한 API에 특히 유효 — AIOS의 에이전트 도구/REST 계약에도 동일한 게이트를 넣을 가치가 있다.
- 보안 CI가 "스케줄 스캔(주 1회) + PR 스캔"을 병행하는 것은 새 CVE가 기존 lock 파일에서 발견되는 경우까지 잡아낸다 — 단, `bandit -lll`(고심각도만)이라 중간 심각도 이슈는 리포트되지 않을 수 있어 기관급에서는 임계값을 낮추고 트리아지 프로세스를 별도로 두는 것이 안전하다.

---

## 10. 기타 기관급 관련 요소 — 레이트리밋, 감사로그, 키 관리, 백테스트 엔진

**레이트리밋**은 §2에서 다룬 토큰별(`rate_limit_per_min`, 1~6000 범위 강제, `agent_token_service.py:145-147`) + 테넌트 전체(`AGENT_TENANT_RATE_LIMIT_PER_MIN`, 기본 600/min, `agent_auth.py:335-347`) 이중 구조이며, 모든 응답에 `X-RateLimit-Limit/Remaining/Reset` 헤더와 429 시 `Retry-After`를 채워 표준 HTTP 컨벤션을 따른다(`agent_auth.py:490-496`).

**감사 로그**(`qd_agent_audit`, `agent_auth.py:106-121`)는 성공/실패를 가리지 않고 **모든** 에이전트 요청을 기록한다(401/403/429/503/409 조기 반환 경로 각각에서 `_audit()`이 호출됨, `agent_auth.py:619-731` 전체를 참조). 요청/응답 본문은 `_redact()`(`agent_auth.py:395-439`)로 `password/secret/token/api_key/authorization/...` 등 20여개 키를 재귀적으로 마스킹한 뒤 8000자로 잘라 저장한다. 사용자 자신은 `/me/audit`(`me_tokens.py:73-83`)으로 자기 로그만 조회 가능.

**키(자격증명) 관리**는 `app/utils/credential_crypto.py`가 담당하며, 거래소 API 키 등은 Fernet 대칭암호로 저장된다. 암호화 키는 `CREDENTIAL_ENCRYPTION_KEY`를 우선 사용하고 없으면 `SECRET_KEY`로 폴백하되, **복호화 시에는 두 키를 순서대로 모두 시도**해 JWT 서명 키(`SECRET_KEY`) 로테이션이 과거에 암호화된 자격증명을 읽지 못하게 만드는 사고를 방지한다.

`app/utils/credential_crypto.py:1-5, 39-45`
```python
"""Fernet encryption for persisted credentials and MFA secrets.

New installations use ``CREDENTIAL_ENCRYPTION_KEY`` so rotating the JWT/session
``SECRET_KEY`` does not make broker credentials unreadable. ...
"""
def _encryption_secret() -> str:
    secret = _credential_key() or _secret_key()
    if not secret:
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY or SECRET_KEY must be set to encrypt persisted credentials"
        )
    return secret
```
다만 이는 KMS/HSM이 아니라 **환경변수 기반 대칭키**이며, 키 로테이션은 "새 키로 재암호화"가 아니라 "복호화 시 구/신 키를 순차 시도"하는 방식이라 진짜 로테이션(재암호화 후 구 키 폐기)은 별도 배치 작업이 필요해 보인다(코드상 그런 배치는 확인되지 않음).

**백테스트 엔진**은 `app/services/strategy_v2/runtime.py`의 `StrategyV2BacktestRunner`(1632줄~)가 담당하며, 핵심 설계는 **백테스트와 라이브 실행이 동일한 컴파일된 전략 프로그램(`CompiledStrategyV2`)과 동일한 `StrategyRuntimeContext` 추상화를 공유**한다는 점이다 — 백테스트는 `MultiAssetSimulationBroker`(커미션·슬리피지 시뮬레이션 포함)를, 라이브는 `StrategyV2LiveSession`(같은 파일, 2173줄~)을 브로커 구현으로 주입만 다르게 한다.

`app/services/strategy_v2/runtime.py:1651-1669`
```python
self.program: CompiledStrategyV2 = compile_strategy_v2(code)
...
self.portal = MultiAssetDataPortal(
    frames, frequency_frames=frequency_frames,
    driving_frequency=self.program.manifest.driving_frequency,
    universe_resolver=universe_resolver,
)
self.broker = MultiAssetSimulationBroker(
    initial_capital=initial_capital, leverage=requested_leverage,
    commission=commission, slippage=slippage,
    instrument_rules=instrument_rules,
)
```
이 "동일 코드, 다른 브로커 어댑터" 구조는 백테스트-라이브 패리티(parity) 문제 — 즉 "백테스트에서 통과한 전략이 실전에서 다르게 동작한다"는 흔한 실패 모드를 구조적으로 줄인다.

**AIOS 시사점**
- 감사로그를 "성공/실패 관계없이, 인가 단계 이전부터" 남기는 것은 침해 사고 조사에 필수 — AIOS도 인증 실패·스코프 거부까지 감사 대상에 포함해야 한다.
- 레이트리밋 응답에 표준 헤더(`X-RateLimit-*`, `Retry-After`)를 일관되게 채우는 것은 에이전트/LLM 클라이언트가 백오프 전략을 자동으로 세울 수 있게 하는 저비용 개선.
- 자격증명 암호화가 환경변수 대칭키 + "구/신 키 순차 시도" 폴백에 머무는 것은 스타트업 단계 SaaS엔 충분하지만, 기관급 AIOS라면 KMS/HSM 연동, 실제 재암호화 로테이션 배치, 키 접근 자체의 감사로그까지 갖춰야 한다 — 이 저장소를 그대로 참고하되 키 관리 부분만은 격상해야 한다.
- 백테스트와 라이브가 동일 컴파일 산출물·동일 컨텍스트 추상화를 공유하는 아키텍처는 그대로 채택할 가치가 매우 높다 — AIOS가 전략/워크플로우 시뮬레이션과 실거래 실행을 별도 코드 경로로 이원화하면 반드시 패리티 버그가 누적되므로, 이 "단일 프로그램 + 교체 가능한 실행 어댑터" 패턴을 원칙으로 삼는 것을 권장한다.

---

## 요약 — 채택 우선순위 제안

| 우선순위 | 패턴 | 근거 섹션 |
|---|---|---|
| 높음 | 스코프를 닫힌 enum(R/W/B/N/C/T)으로 강제 + opaque hashed token | §2 |
| 높음 | Idempotency-Key: 토큰 스코프 키 + request_hash 불일치 409 + TTL 복구 | §3 |
| 높음 | 다단 라이브 트레이딩 게이트(스코프→allowlist→paper_only→kill switch→notional) | §4 |
| 높음 | 백테스트/라이브 단일 컴파일 산출물 공유 아키텍처 | §10 |
| 중간 | SKIP LOCKED 커맨드 큐 + fencing token 리스 + 재기동 복구 | §1, §8 |
| 중간 | MCP는 절대 얇게, 인가 로직 복제 금지 | §6 |
| 중간 | Compose 기본/프로덕션 하드닝 오버레이 이원화 + CI 검증 | §9 |
| 보완 필요 | 버전 불변성을 DB 레벨(트리거/권한)로 격상 | §5 |
| 보완 필요 | 키 관리를 KMS/HSM 수준으로 격상 | §10 |
| 보완 필요 | 계정/포트폴리오 레벨 통합 리스크 엔진(토큰 단위 한도의 상위 레이어) | §4 |
