# mihwa-aios Foundation Implementation Work Packages v1.0

> 상태: **Claude Code/Codex execution plan**. 42~51번의 설계를 현재 `GeonAhGim/mihwa-aios` 코드 구조에 매핑한다.
> 기준: 작은 PR, paper-only, 기존 LIVE/credential/execution 경로의 안전 guard를 약화시키지 않음.

## 1. 현재 코드베이스 판단

현재 backend에는 `src/services/`, `src/api/`, `src/data/models/`, `src/exchanges/`, `src/core/` 중심의 구현과 전략·적합성·계좌 credential·execution·audit 관련 선행 코드가 있다. 그러나 Foundation target은 이를 한 거대한 리팩터링으로 교체하지 않는다. 새 bounded context를 병렬로 추가하고, 기존 경로는 contract adapter를 통해 점진적으로 감싼다.

특히 다음은 **FROZEN/owner review** 영역이다.

- `src/exchanges/**`, `src/services/execution_loop/**`, `src/services/order_service/**`
- `src/services/credential_resolver.py`, `src/services/exchange_credential_service.py`
- LIVE conversion과 execution API 관련 router/dependency

이 영역에 새 기능을 직접 넣기보다 Foundation command가 검증한 `PAPER` contract를 기존 adapter에 좁게 연결한다.

## 2. 새 모듈 위치

```text
src/foundation/
  trust/                 # 43
  connections/           # 44
  mandates/              # 45
  strategy_packages/     # 46
  paper_control/         # 47
  risk_gate/             # 48
  evidence/              # 49
  reconciliation/        # 50
  performance/           # 51
  contracts/             # shared versioned Foundation DTO only
tests/foundation/
  unit/
  integration/
  contract/
  adversarial/
```

각 context는 `domain/models.py`, `domain/rules.py`, `application/<command>.py`, `ports/*.py`, `adapters/*.py`, `contracts/v1.py`, `projections.py`로 시작한다. 파일이 비대해지면 command/aggregate별로 분리한다. 기존 `src/services`의 만능 서비스 파일에 새 기능을 추가하지 않는다.

## 3. PR 실행 순서

| PR | 문서 | 새 paths | 최소 산출물 | 필수 negative test |
|---|---|---|---|---|
| FND-01 | 43 | `foundation/trust`, `tests/foundation/unit/trust` | TenantContext, consent/suitability freshness port와 pure rules | cross-tenant, revoked/expired, MFA 부족 |
| FND-02 | 45 | `foundation/mandates` | immutable mandate revision, PolicyDecision contract/evaluator | no mandate, material change, deterministic replay |
| FND-03 | 49 | `foundation/evidence` | AuditEvent/EvidenceReference envelope, in-memory adapter, trace propagation | event mutation, secret in payload, cross-tenant read |
| FND-04 | 46 | `foundation/strategy_packages` | package hash/lifecycle, validation bundle interface | missing evidence/hash, altered artifact, forbidden universe |
| FND-05 | 44 | `foundation/connections` | read-only connection registry/capability model and fake provider port | trade/withdraw scope, revoked connection, secret leak |
| FND-06 | 48 | `foundation/risk_gate` | deterministic `RiskDecision` and deployment/pre-intent checks | deny path calls adapter, stale data, kill switch |
| FND-07 | 47 | `foundation/paper_control` | paper deployment state machine + command handlers | paper/live provenance mismatch, duplicate command, pause race |
| FND-08 | 50 | `foundation/reconciliation` | order/position/balance compare, mismatch projection/stop event | duplicate retry, material mismatch resume |
| FND-09 | 51 | `foundation/performance` | statement calculation contract/projection and evidence binding | gross/net mix, paper/live mix, missing as-of |
| FND-10 | 42 | API/BFF thin adapters + integration suite | vertical user flow: mandate→package→paper start/stop→timeline | direct router bypass, tenant isolation |

FND-01~04는 provider/execution code를 호출하지 않는다. FND-05~08에서만 fake paper adapter를 통해 integration을 검증한다. 실 provider integration은 60번 document의 별도 governed workstream이다.

## 4. Contract ownership

| Contract | Owner context | Consumer | 규칙 |
|---|---|---|---|
| `TenantContext`, `ConsentDecision`, `SuitabilityDecision` | trust | all Foundation contexts | API body에서 생성 금지 |
| `PortfolioMandate`, `PolicyDecision` | mandates | packages, paper_control, risk_gate | immutable revision/ref만 전달 |
| `StrategyPackage`, `ValidationResult` | strategy_packages | paper_control, performance | hash/evidence 필수 |
| `AccountConnection`, `AccountSnapshot` | connections | mandates/risk/reconciliation | read-only capability P0 |
| `RiskDecision` | risk_gate | paper_control/order adapter | adapter 전 final veto |
| `AuditEvent`, `EvidenceReference` | evidence | all | append-only, no secret |
| `ReconciliationState` | reconciliation | risk/paper/performance | material mismatch = pause |
| `PerformanceStatement` | performance | BFF/export | methodology/as-of/evidence |

공통 contract는 `src/foundation/contracts/v1.py`에만 두고, transport(Pydantic/FastAPI schema)는 `src/api/schemas/foundation/`에 별도 둔다. `domain`은 FastAPI·SQLAlchemy·외부 HTTP에 의존하지 않는다.

## 5. persistence 접근법

첫 PR은 existing test/database conventions을 따른 repository port와 fake/in-memory adapter로 시작한다. schema migration은 aggregate 하나당 독립 migration으로 추가하며, legacy table을 무단 변경하거나 데이터를 재해석하지 않는다. 영속화 전에도 event/contract/negative test를 확정해 과도한 migration을 피한다.

각 write record에는 tenant ID, revision/version, created/updated time, actor/trace, classification을 포함한다. secret은 Foundation DB model에 저장하지 않고 existing credential boundary의 opaque `secret_ref`만 참조한다.

## 6. API 구현 규칙

`src/api/routers/foundation/`과 `src/api/schemas/foundation/`을 새로 만들고, router는 authentication/TenantContext injection/transport validation/command invocation만 담당한다. policy/risk/package 판단을 router에 두지 않는다.

초기 endpoint는 아래처럼 좁힌다.

```text
POST /v1/foundation/mandates
POST /v1/foundation/mandates/{id}/activate
GET  /v1/foundation/mandates/{id}
POST /v1/foundation/strategy-packages/{id}/validate
POST /v1/foundation/paper-deployments
POST /v1/foundation/paper-deployments/{id}:pause
POST /v1/foundation/paper-deployments/{id}:stop
GET  /v1/foundation/trust-timeline
```

connection 등록/secret flow와 실제 exchange endpoint는 FND-05에서 fake provider contract로 먼저 검증하고, real router 공개는 provider/legal review 후 결정한다.

## 7. Claude Code 작업 지시

1. 하나의 FND PR을 선택하고 해당 43~51 명세를 먼저 읽는다.
2. 현재 관련 model/service/test를 읽고 overlapping user changes를 보존한다.
3. public contract와 pure invariant tests를 먼저 만든다.
4. 정상 흐름, 모든 table의 negative test, audit/evidence impact를 같이 구현한다.
5. `ruff`, `mypy` 대상 모듈, unit/contract/integration tests를 실행한다.
6. 완료 보고에 changed paths, contract, migration, test result, failure/rollback path, FROZEN 영역 미변경 여부를 적는다.

## 8. Definition of implementation-ready

42~70번 문서는 **설계·계약·상태·완료/거부 기준**을 제공한다. 이 71번 문서는 그것을 실제 repo의 모듈·PR·테스트·API 경로로 연결한다. 따라서 Claude Code는 FND-01부터 추가 설계 결정을 크게 만들지 않고 구현을 시작할 수 있다. 단, LIVE/provider/custody 관련 PR은 60~63번의 승인 게이트를 통과하기 전에는 만들지 않는다.
