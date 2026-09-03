# AIOS Codex--Fable 교차검토 및 연구방향 결정 기록

-   작성일: 2026-09-03
-   대상: AIOS Architecture Research
-   기준 검토문서:
    `AIOS_Codex_Research_Review_by_Fable_v1_2026-09-03.md`
-   목적: Codex 외부 아키텍처 연구와 Fable의 실제 AIOS 코드 교차검토를
    종합하여 향후 연구 원칙과 보류/우선 항목을 기록한다.

## 1. 결론

AIOS는 현재 구현을 서둘러 확장하기보다 **최종형 엔터프라이즈 아키텍처를
충분히 깊고 넓게 연구한 뒤 구현을 단계적으로 활성화**하는 방향을
유지한다.

다만 외부 후보를 계속 추가하는 것만으로는 충분하지 않다. 앞으로 연구는
다음 두 축을 병렬로 수행한다.

1.  **Horizontal Research** --- 금융 시스템, 분산 시스템, 보안, Agent,
    데이터, SRE, 표준, 논문 및 오픈소스에서 새로운 아키텍처와 failure
    pattern을 폭넓게 탐색한다.
2.  **Vertical Verification** --- 발견된 후보와 원칙을 실제 AIOS 코드,
    테스트, 상태모델, failure semantics와 대조하여 필요한지 검증한다.

새 기술이나 새로운 Plane을 발견했다는 이유만으로 Target Architecture에
편입하지 않는다.

## 2. Fable 검토에서 확인된 중요 사항

### 2.1 Durable execution ownership 문제

Fable 검토에 따르면 현재 `ExecutionLoopScheduler.list_runnable()`은
`RUNNING` 및 `PAPER` 상태를 기준으로 실행 대상을 조회하지만 DB 수준의
lease, heartbeat, worker ownership이 확인되지 않았다.

다중 인스턴스 환경에서 동일 execution을 여러 worker가 처리할 가능성은 P0
수준의 연구 및 검증 대상으로 등록한다.

단, 즉시 특정 lease 컬럼을 추가하는 방식으로 구현을 확정하지 않는다.
향후 durable workflow, fencing, workload identity, command ownership 및
reconciliation 모델과 함께 최종 ownership contract를 설계한다.

### 2.2 Safety mechanism wiring 문제

Fable은 `make_foundation_pre_submit_gate()`에 deterministic kill-switch
로직이 존재하지만 일부 실행 생성 경로에서 gate가 `None`으로 전달되어
실제 authoritative execution path에서 우회될 가능성을 지적했다.

DataDistrust monitor 역시 실제 실행 경로의 wiring 여부를 재검증해야
한다.

이 발견에서 다음 원칙을 도출한다.

> Safety mechanism exists ≠ Safety mechanism is authoritative ≠ Safety
> mechanism cannot be bypassed.

향후 AIOS의 모든 안전·정책·권한 장치는 단순 구현 존재 여부가 아니라
다음을 검증해야 한다.

-   모든 privileged path가 반드시 해당 gate를 통과하는가?
-   bypass path가 존재하는가?
-   fail-open인가 fail-closed인가?
-   policy/gate unavailable 시 동작은 무엇인가?
-   결정과 실행 사이에 TOCTOU 문제가 있는가?
-   어떤 evidence로 실제 enforcement를 증명할 수 있는가?

### 2.3 Artifact Trust는 기존 primitive 확장을 우선 검토

Sigstore, in-toto, SLSA 등의 도입을 독립적인 신규 subsystem으로 간주하기
전에 기존 AIOS의 `artifact_hash`, `bundle_hash`, `result_hash` 및
validation/evidence 구조를 확장하여 해결할 수 있는지 검토한다.

새로운 Plane을 만드는 것보다 기존 primitive의 책임과 assurance를
강화하는 편이 적절한 경우 이를 우선한다.

## 3. Fable 의견 중 채택하지 않는 부분

Fable은 현재 확인된 gate wiring 및 execution lease 문제를 먼저 코드로
수정할 것을 제안했다.

문제 자체는 중요하며 P0 Known Safety Defect 후보로 관리한다. 그러나
**현재 단계에서 즉시 production implementation을 수행하지 않는다.**

이유는 해당 문제들이 다음 Target Architecture 요소와 직접 연결되기
때문이다.

-   Capability Gateway
-   Authorization PDP
-   Financial Policy PDP
-   Deterministic Risk Authority
-   Workflow ownership
-   Workload Identity
-   Fencing
-   Command Authority
-   Event Ledger
-   Reconciliation Authority

이 계약들이 충분히 연구되지 않은 상태에서 현재 구조에 국소적인 수정이나
migration을 추가하면 이후 다시 구조를 변경할 가능성이 있다.

따라서 문제는 기록하고 검증하되 구현 결정은 Architecture Research 이후로
보류한다.

## 4. Codex 연구방법 보완

Codex의 광역조사는 계속한다. 다만 `Discovery`와
`Verified Architecture Candidate`를 명확하게 구분한다.

후보 maturity:

    DISCOVERED
    → SCREENED
    → RELEVANT
    → CODE-VERIFIED
    → FAILURE-ANALYZED
    → AIOS-MAPPED
    → POC-CANDIDATE
    → ADR-CANDIDATE

`DISCOVERED` 상태의 프로젝트는 아무리 많아도 좋지만 Target
Architecture의 근거로 직접 사용할 수 없다.

`CODE-VERIFIED` 이상으로 승격하려면 최소한 실제 구현, 테스트, recovery
semantics, security boundary, failure behavior를 확인해야 한다.

## 5. 새로운 아키텍처 추가 기준

새로운 technology, service 또는 Plane을 추가하기 전에 반드시 다음 질문에
답한다.

1.  어떤 실제 AIOS gap을 해결하는가?
2.  기존 AIOS primitive를 강화하는 것으로 해결할 수 없는가?
3.  새로운 authority가 추가되는가?
4.  새로운 state ownership이 추가되는가?
5.  새로운 failure mode가 생기는가?
6.  새로운 operational burden이 생기는가?
7.  새로운 trust boundary가 생기는가?
8.  해당 component가 사라져도 contract를 유지할 수 있는가?
9.  향후 교체 가능한 adapter 뒤에 둘 수 있는가?
10. 이 추가가 장기적인 structural refactoring 가능성을 줄이는가,
    늘리는가?

위 질문에 명확한 답이 없으면 architecture component로 승격하지 않는다.

## 6. 연구 역할 분담

### Codex --- Architecture Frontier Explorer / Integration Architect

-   외부 architecture space 확대
-   공식 표준/RFC/논문/OSS/금융권 production pattern 조사
-   새로운 failure model 탐색
-   기술 후보 비교
-   AIOS contract 후보 제안
-   `aios-brainstorm`에만 연구 기록
-   `aios`, `aios-meta` 수정 금지

### Claude/Fable --- Internal Architecture Auditor / Red Team

-   실제 AIOS 코드와 연구결론 대조
-   이미 존재하는 capability 탐지
-   wiring gap 탐지
-   duplicate architecture 탐지
-   hidden coupling 탐지
-   bypass/fail-open/partial failure 분석
-   신규 component가 실제 필요한지 공격적으로 검증

### ChatGPT --- Cross-Research / Synthesis Review

-   Codex와 Fable 결론 교차검증
-   외부 조사와 내부 코드 evidence 연결
-   assumption/contradiction/invariant/failure catalog 통합
-   premature convergence 감시
-   Target Architecture 후보로 승격할 evidence 수준 평가

## 7. 연구 루프

    External Research
            ↓
          Codex
            ↓
    Discovery / Candidate
            ↓
    AIOS Code Mapping
            ↓
    Claude/Fable Red-Team Review
            ↓
    Cross Review
            ↓
    Assumption Register
    Contradiction Register
    Invariant Catalog
    Failure Scenario Catalog
            ↓
    Research Backlog Update
            ↓
    반복

Architecture Synthesis는 이 루프가 충분히 반복된 후 수행한다.

## 8. 현재 P0 Known Safety Defect / Research Items

### P0-R1 --- Execution Ownership

다중 process/worker 환경에서 동일 execution의 중복 처리 가능성을
검증하고 최종 lease/fencing/ownership contract를 정의한다.

### P0-R2 --- Foundation Pre-submit Gate Authority

모든 order submission path가 deterministic pre-submit gate를 반드시
경유하는지 전수검증한다.

### P0-R3 --- DataDistrust Enforcement

market-data distrust 상태가 실제 execution authority를 차단하는지
검증한다.

### P0-R4 --- Fail-open Paths

`None`, timeout, unavailable, exception 등의 상태에서 safety/policy
component가 fail-open 되는 모든 경로를 탐색한다.

### P0-R5 --- Authority Wiring Proof

Authorization → Financial Policy → Risk → Command → Adapter →
Reconciliation → Ledger의 각 authority가 단순 존재하는 것이 아니라 실제
실행경로에서 강제됨을 증명할 방법을 설계한다.

## 9. 당분간의 구현 원칙

현재 Research Phase에서는 다음을 유지한다.

-   `aios` production 코드 수정 보류
-   `aios-meta` 수정 보류
-   연구 산출물은 `aios-brainstorm`
-   발견된 결함은 숨기지 않고 P0 register에 기록
-   국소 patch보다 root contract 연구 우선
-   새로운 OSS 도입보다 문제/불변조건 정의 우선
-   구현 속도보다 미래 structural rework 최소화 우선

## 10. 핵심 Architecture Principle 추가

AIOS Target Architecture는 앞으로 다음 세 조건을 별도로 증명해야 한다.

    IMPLEMENTED
        ↓
    WIRED INTO AUTHORITATIVE PATH
        ↓
    NON-BYPASSABLE / FAIL-CLOSED
        ↓
    EVIDENCE-PROVABLE

기능이 코드에 존재하는 것만으로는 capability가 완성된 것으로 간주하지
않는다.

최종적으로 AIOS의 안전성은 "무엇을 구현했는가"가 아니라 **어떤 실행
경로에서도 그 통제를 우회할 수 없고, 그 사실을 사후에 증명할 수
있는가**를 기준으로 평가한다.

------------------------------------------------------------------------

Status: RESEARCH DECISION RECORD\
Decision: CONTINUE DEEP & WIDE RESEARCH WITH CODE-LEVEL VERTICAL
VERIFICATION\
Production Implementation: DEFERRED\
Next Architecture State: NOT YET FROZEN
