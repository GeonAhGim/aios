# Module Scaffold and Naming Standard v1.0

> 상태: **Mandatory cross-cutting standard.** 34번 §6(최소 코드 단위)와 35번 §9.2(권장
> 코드 구조 원칙)가 정한 폴더 트리 원칙을, 71번(FND-01~10 work package)이 실제로
> 만들 첫 파일들까지 복붙 가능한 수준으로 구체화한다.
>
> 작성자: Claude Code(구현 세션). 근거: `mihwa-aios`의 기존 `src/services/*.py`
> 단일 파일 서비스 패턴(11번 문서 시절 관행)과, 34/35번이 요구하는 `domain/
> application/ports/adapters` 분리 패턴 사이의 간극을 메운다.
>
> 작성일: 2026-09-02

---

## 1. 배경 — 두 세대의 코드가 공존한다

`mihwa-aios`의 기존 코드(`src/services/*.py`, 예: `verification_service.py`,
`strategy_builder_service.py`)는 **한 파일 = 한 서비스 클래스**(도메인 규칙 + DB
쿼리 + 이벤트 발행이 한 파일에 섞임) 패턴이다. 34번 §6과 35번 §9.2는 새 bounded
context가 `domain/application/ports/adapters/contracts/tests`로 계층을 분리하라고
요구한다. 71번 §2가 `src/foundation/`을 새 위치로 지정했으니, **기존 서비스를
리팩터링하지 않고 새 계층만 이 표준을 따른다** — 두 세대가 당분간 공존하는 것은
의도된 전환 전략이지 방치가 아니다.

---

## 2. 폴더 트리 — 실제 예시 (FND-01 Trust Core 기준)

71번 §2가 정한 위치를 파일 단위까지 구체화하면:

```text
src/foundation/trust/
  __init__.py
  domain/
    __init__.py
    models.py          # Subject, Membership, Consent, SuitabilityProfile (pure dataclass/pydantic)
    rules.py            # is_membership_transition_allowed(), is_consent_fresh() 등 순수 함수
  application/
    __init__.py
    grant_membership.py       # 명령 1개 = 파일 1개 (파일이 커지면 분리 기준)
    suspend_membership.py
    accept_disclosure.py
    revoke_consent.py
    submit_suitability.py
    evaluate_trust_freshness.py   # query
  ports/
    __init__.py
    repository.py        # Protocol: TrustRepository (add_membership, get_membership, ...)
    idp_provider.py       # Protocol: IdentityProviderPort
  adapters/
    __init__.py
    postgres_repository.py   # TrustRepository의 asyncpg 구현
  contracts/
    __init__.py
    v1.py                # TenantContext, ConsentDecision, SuitabilityDecision (pydantic, versioned)
  projections.py         # TrustStatusView 읽기 전용 프로젝션 조립

src/api/routers/foundation/
  trust.py               # FastAPI router — transport만, 판단 로직 없음

src/api/schemas/foundation/
  trust.py               # 요청/응답 Pydantic (contracts/v1.py를 감싸되 HTTP 세부만 추가)

src/db/migrations/versions/
  <hash>_trust_core_foundation.py   # 이 context 전용 마이그레이션, 기존 테이블 미변경

tests/foundation/
  unit/trust/
    test_rules.py         # domain/rules.py 순수 함수 테스트, DB 없음
  integration/trust/
    test_grant_membership.py
    test_accept_disclosure.py
    ...
  contract/trust/
    test_v1_contract_compatibility.py   # 106번 §5, 107번 계약 테스트
  adversarial/trust/
    test_cross_tenant_isolation.py
```

이 트리는 71번 §2의 뼈대(`domain/models.py, domain/rules.py, application/<command>.py,
ports/*.py, adapters/*.py, contracts/v1.py, projections.py`)를 그대로 따르되, 파일
확장자·복수형 규칙·라우터/스키마 위치까지 못박아 두 세션이 같은 트리를 다르게
해석하지 않게 한다.

---

## 3. 네이밍 규칙

### 3.1 파일/모듈

| 대상 | 규칙 | 예시 |
|---|---|---|
| command 파일 | `<동사>_<목적어>.py`, snake_case, 동사원형 | `grant_membership.py`, `revoke_consent.py` |
| query 파일 | `<동사>_<대상>.py` 또는 `get_<대상>.py` | `evaluate_trust_freshness.py` |
| domain 모델 | `models.py` 고정(context당 1개, 커지면 `models/` 디렉터리로 승격) | — |
| repository port | `repository.py`(Protocol), 구현은 `adapters/<provider>_repository.py` | `postgres_repository.py` |
| contract 버전 파일 | `contracts/v<N>.py`, 이전 버전은 삭제하지 않고 유지(107번 참조) | `contracts/v1.py` |

### 3.2 클래스/함수

| 대상 | 규칙 | 예시 |
|---|---|---|
| 도메인 예외 | `<Context><실패내용>Error`, 반드시 `Exception` 상속, 모든 도메인 예외의 공통 베이스는
context당 1개(`TrustError` 등) | `ConcurrencyConflictError`(105번 공용), `TrustMembershipTransitionError` |
| command handler 함수 | `async def <verb>_<object>(...)`, 클래스로 감싸지 않는다(35번 §9.1 원칙 3 — 오케스트레이터는 판단하지 않는다) | `async def grant_membership(...)` |
| repository 메서드 | CRUD 동사 고정: `get_`, `list_`, `save_`(insert/update 겸용 금지 — `insert_`/`update_conditional_`로 분리), `delete_`는 원칙적으로 미사용(append-only 우선) | `get_membership_by_id`, `update_conditional_membership_state` |
| pydantic contract 클래스 | `<Noun>`(동사 없음), 이벤트는 `<Noun>Event`, 커맨드는 `<Verb><Noun>Command` | `TenantContext`, `MembershipGrantedEvent`, `GrantMembershipCommand` |

### 3.3 DB 테이블/컬럼

| 대상 | 규칙 | 예시 |
|---|---|---|
| 테이블명 | snake_case 단수형 금지(복수형 고정), context 접두어 불필요(스키마/폴더가 이미 구분) | `tenant_membership`, `consent_record` |
| PK | `id`(UUID), 예외적으로 기존 코드의 `int` PK 패턴(예: `strategy_listings.id`)은 유지, 새 테이블은 UUID 고정 | — |
| 상태 컬럼 | `state` 또는 `status`(기존 코드 관용을 존중 — 새 context는 `state`로 통일) | `state` |
| 낙관적 동시성 | `revision`(integer, 105번 표준의 대안 축 — `state` 조건과 `revision` 조건 중 도메인에 맞는 쪽 선택, 둘 다 필요하면 둘 다) | `revision` |
| 감사 컬럼 | `created_at`, `created_by`, `updated_at`(mutable일 때만) — 72번 §3 표준 봉투와 일치 | — |

---

## 4. 파일 하나의 책임 경계 — 커지면 자르는 기준

35번 §9.1 원칙 1("한 파일은 한 책임")을 실무 기준으로 내린다. 아래 신호가 하나라도
나오면 그 시점에 분리한다(사전에 미리 잘게 쪼개지 않는다 — CLAUDE.md 전역 원칙과
동일):

1. `application/<command>.py` 파일이 **하나의 command/query 이외의 이름**을 export하기
   시작하면 → 그 다른 이름을 별 파일로.
2. `domain/rules.py`가 서로 다른 aggregate(예: Membership 규칙과 Consent 규칙)를
   섞기 시작하면 → `domain/rules/membership.py`, `domain/rules/consent.py`로 승격.
3. `adapters/postgres_repository.py`가 200줄을 넘고 서로 다른 aggregate의 쿼리를
   섞으면 → aggregate별 repository 클래스로 분리(단, `ports/repository.py`의
   Protocol 하나는 유지 — 구현만 나뉜다).
4. API 라우터 파일이 5개 이상의 엔드포인트를 갖게 되면 → 하위 리소스 기준으로
   라우터 분리(`trust/memberships.py`, `trust/consents.py`).

반대로 금지되는 것: "나중에 커질 것 같아서" 미리 `domain/rules/` 디렉터리를 만들어
파일 하나만 넣어두는 것(35번 §9.2 마지막 문장 — "미세한 파일 분할만 늘리고
contract·owner·test가 없는 모듈 증식도 금지").

---

## 5. Definition of Done에 대한 이 문서의 기여

34번 §9와 35번 §6이 요구하는 "서비스별 DoD" 중 "owner와 bounded context"·"data
ownership" 항목은, 새 context가 이 문서의 폴더 트리(§2)와 네이밍(§3)을 따랐다는
사실 자체로 자동 충족된다 — 리뷰어가 매번 처음부터 판단하지 않아도 된다. L3
문서(73~81, 이후 도메인)는 "Fine-grained modules" 표를 쓸 때 이 문서 §2의 파일
목록을 그대로 인용하는 것을 권장한다.
