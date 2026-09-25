Status: ACCEPTED → promoted to ADR-2026-09-10-C (2026-09-10)

# 01. 개발단계에 따른 구현정책 진화

Status: IDEA / REVIEW  
Date: 2026-09-10  
Related repository: `GeonAhGim/aios`

## 출발점

AIOS 개발 초기에는 AI 에이전트가 코드를 빠르게 확장하면서 파일이 과도하게 비대해지고, 한 파일이 여러 책임을 갖는 것을 막기 위해 소스 파일을 대체로 300줄 이하로 유지하도록 강한 구현정책을 적용했다.

이 정책은 초기에 유효했다. 작은 leaf, 작은 파일, 강한 zone 제한, 빠른 테스트 피드백은 AI 에이전트가 무제한으로 구조를 확장하거나 거대한 파일에 로직을 몰아넣는 것을 막는 안전장치였다.

그러나 프로젝트가 중기 이후로 들어오면서 동일한 정책이 오히려 다른 종류의 기술부채를 만들 수 있다는 문제의식이 생겼다.

## 300 LOC hard cap의 부작용

완결된 주문 제출 도메인이 논리적으로 하나인데, 파일 길이 때문에 아래처럼 잘리는 상황을 생각한다.

```text
submit.py
validation.py
helpers.py
state.py
utils.py
common.py
```

각 파일은 300줄 이하일 수 있지만, 주문 제출의 전체 invariant를 이해하려면 여섯 파일을 왕복해야 한다.

특히 금융 시스템에서 다음과 같은 흐름은 전체가 한눈에 검증되어야 한다.

```text
execution ownership
→ mandate
→ risk
→ compliance
→ kill switch
→ idempotency
→ OMS
→ broker adapter
```

LOC 제한 때문에 이 흐름이 불필요하게 흩어지면 코드의 외형은 작아져도 다음 품질은 악화될 수 있다.

- domain cohesion
- invariant locality
- auditability
- reasoning locality
- failure-path 이해 가능성
- agent review 정확성
- human review 정확성

AI 코딩 에이전트는 명확한 정량규칙을 매우 잘 최적화한다. 따라서 `≤300 LOC`가 hard gate라면 아키텍처 품질보다 gate 통과를 위해 파일을 기계적으로 분해할 유인이 생긴다.

`xxx_service.py`, `xxx_service_helpers.py`, `xxx_service_utils.py`, `xxx_service_internal.py`, `xxx_service_common.py` 같은 의미 없는 분해가 반복될 수 있다.

## 정책 변경 방향

300줄 제한을 단순 폐지하고 아무 제한도 두는 것이 아니라 다음으로 승격한다.

> File-size-first에서 Domain-cohesion-first로 전환한다.

분해의 기준은 줄 수가 아니라 다음이 된다.

- bounded context
- aggregate
- capability
- invariant ownership
- independent change axis
- dependency direction
- public contract
- safety boundary

## 권장 정책

### LOC

- 300 LOC hard fail 제거
- 400~500 LOC: 정상 범위로 허용
- 600 LOC 이상: warning 후보
- 800~1,000 LOC 이상: architecture review 후보
- 단순 LOC 초과만으로 CI RED 금지
- 1,000 LOC를 넘는다고 자동 분할하지 않음
- 큰 파일이 실제로 하나의 강한 책임과 invariant를 가진다면 유지 가능
- 반대로 150줄짜리 파일이어도 여러 도메인의 권위를 섞으면 분할 대상

### 실제 gate 후보

LOC 대신 다음을 더 강한 품질지표로 사용한다.

- cyclomatic complexity
- cognitive complexity
- dependency cycle
- forbidden dependency direction
- domain boundary violation
- duplicate domain authority
- excessive fan-in / fan-out
- hidden mutable state
- fail-open path
- invariant without tests
- mutation test failure
- gate-red proof failure
- unsafe runtime bypass
- ambiguous ownership
- helper/common dumping ground

### 분해 원칙

> 강하게 함께 변경되는 코드는 함께 둔다.

> 서로 독립적으로 변경되는 책임만 분리한다.

> 파일 크기 때문에 domain invariant를 분산시키지 않는다.

> `utils.py`, `helpers.py`, `common.py`는 명확한 도메인 의미가 없으면 새로 만들지 않는다.

## Safety-Critical Invariant Locality

AIOS에서는 특히 안전 관련 invariant를 가까이 두는 원칙이 필요하다.

예를 들어 주문이 실제 broker로 나가기 전 필요한 통제:

```text
ownership
mandate
risk
compliance
kill switch
idempotency
```

이 흐름은 하나의 bounded context 또는 명확하게 연결된 application flow 안에서 추적 가능해야 한다.

한 주문이 왜 ALLOW 되었고 왜 REJECT 되었는지를 리뷰어가 파일 수십 개를 추적하지 않고 재구성할 수 있어야 한다.

## 개발단계별 정책 변화

### 초기: 구조 폭주 방지

주요 목표:

- AI agent code explosion 억제
- 작은 task / leaf
- 작은 파일
- dependency 제한
- zone 제한
- 테스트 기반 조기 안정화

이 단계에서는 LOC cap이 유효한 안전장치다.

### 중기: Domain Formation

주요 목표:

- bounded context 정착
- capability ownership
- aggregate
- domain event
- invariant ownership
- contract stabilization

이 단계부터 파일 길이보다 응집도가 중요해진다.

### 현재 AIOS: Domain Completion + Failure Semantics

주요 목표:

- 이미 형성된 구조를 함부로 재설계하지 않음
- failure injection
- concurrency
- replay
- idempotency
- mutation/gate-red
- fail-closed
- operational composition

300줄 정책을 계속 강제하면 오히려 완결된 도메인을 분해할 위험이 있다.

### LIVE 직전: Production Hardening

주요 목표:

- mandate/compliance 실제 enforcement
- backup/PITR
- restore drill
- container/deployment
- environment isolation
- alert routing
- broker uncertainty recovery
- CI governance
- branch protection
- change approval

### 상용화 이후: Operational Governance

주요 목표:

- SLO/SLA
- compatibility
- migration discipline
- tenant isolation
- versioned contracts
- cost governance
- performance regression governance
- operational evidence

## AIOS 전체 방향성에 대한 판단

현재 큰 방향을 다시 설계할 단계는 아니다.

주요 구조:

```text
Deterministic Risk Authority
→ Mandate / Compliance
→ OMS
→ Execution
→ Adapter
```

그 주변에:

```text
Ledger
Evidence
Idempotency
Execution Ownership
Event Store
Reconciliation
Meta-Control Plane
```

을 둔 방향은 유지하는 것이 맞다.

현재 단계는 Architecture Redesign보다 Architecture Completion에 가깝다.

따라서 Claude/Codex/DevEngine에 적용할 정책은 다음 방향이 적절하다.

> 기존 Target Architecture를 기본적으로 보존한다. 구조 변경은 invariant 위반, domain-boundary 오류, duplicate authority, 명확한 확장성 병목이 증명될 때만 허용한다. 그 외에는 기존 도메인을 완결하고 failure semantics와 production hardening의 깊이를 높인다.

300 LOC 정책의 전환 역시 Architecture 방향 변경이 아니라 개발단계에 따른 구현정책의 승격으로 본다.
