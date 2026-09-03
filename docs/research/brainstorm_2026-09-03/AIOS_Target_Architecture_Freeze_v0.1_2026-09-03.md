# AIOS Target Architecture — Freeze Draft v0.1

작성: Fable | 2026-09-03 | 상태: **Proposed** (동결 아님 — Codex/ChatGPT 교차검증 및 사용자 승인 대기)

> 근거: [1차 Deep Dive](research_evidence_2026-09-03/) §6(10-plane 제안), [Phase 2B](AIOS_Wide_Scan_Phase2B_Enterprise_Infrastructure_2026-09-03.docx) §8(8-plane 추가),
> [OSS Deep Dive v2](AIOS_OSS_DeepDive_v2_CodeLevel_CrossVerification_2026-09-03.md), [레지스트리 v1](AIOS_Registers_v1_Assumption_Contradiction_Invariant_Failure_2026-09-03.md).
> 이 문서의 목적은 두 제안에서 나온 **18개 이름 붙은 plane을 12개로 압축**하고, 레지스트리의
> Contradiction 6건을 전부 명시적으로 해소하며, Invariant 10건을 각 plane의 책임으로 배정하는 것.
> plane 이름을 늘리는 문서가 아니라 줄이는 문서다.

---

## 0. 이 문서가 지키는 원칙

연구 결정 기록 §5의 "새 아키텍처 추가 기준" 10문항을 모든 plane에 적용했다. 어떤 plane도
"실제 AIOS gap을 해결하는가" 없이는 여기 남지 않았다. 병합·폐기된 plane은 §6에 사유와 함께
기록한다(Rejected가 아니라 Merged라는 점에 유의 — 아이디어 자체를 기각한 게 아니라 중복을
없앤 것).

---

## 1. Plane 목록 (18 → 12)

| # | Plane | 흡수한 원안 | 핵심 책임 | 강제하는 Invariant | 해소하는 Contradiction |
|---|---|---|---|---|---|
| 1 | **Experience Plane** | 1차 동일 | Web/Mobile/API + 대화형 Strategy Builder UI | — | — |
| 2 | **Agent Gateway Plane** | 1차 동일 | 외부 AI/에이전트를 위한 스코프 제한 capability 표면 | I-06, I-08 | C-06 |
| 3 | **Strategy Factory Plane** | 1차 Strategy Factory + 2B Structured Generation Gate | NL intent → **스키마 강제된** canonical Strategy IR | I-07(부분) | A-04, A-06 |
| 4 | **Strategy Registry / Artifact Trust Plane** | 1차 Strategy Registry + 2B Artifact Trust Plane | 모든 아티팩트(전략·모델·플러그인·빌드)의 해시·서명·provenance | I-04 | C-01(부분 — package_ref 표준화) |
| 5 | **Validation & Experiment Plane** | 1차 Validation + 2B Experiment & Registry | backtest/OOS/walk-forward/robustness 게이트 + 실험 이력 | I-07 | — |
| 6 | **Execution Plane** | 1차 동일 + 2B Sandbox Tier Manager(하위 메커니즘) | 브로커 어댑터, 주문 상태기계, 신뢰도별 코드 격리(native/container/gVisor) | I-05 | — |
| 7 | **Policy Plane (Deterministic PDP)** | 1차 Policy + 2B Deterministic PDP | 금융 행위 정책의 단일 결정 지점, 결과는 결정론적 | I-01, I-09 | C-03, C-04(외곽), C-05 |
| 8 | **Event Ledger** | 1차 동일 | command/event/audit/replay SSOT | I-10(증거 저장소) | — |
| 9a | **Execution Ownership Plane** | (신규) 실행 소유권/리스 — [2026-09-03 갱신] 원래 9번과 통합돼 있었으나 [v3 검증](AIOS_OSS_DeepDive_v3_Infrastructure_Candidates_2026-09-03.md)이 지적한 대로 분리 | 초 단위 실행 리스/펜싱(다중 인스턴스 중복 tick 방지) | I-02 | — |
| 9b | **Durable Workflow Plane** | 2B Durable Workflow | 시간~일 단위 장기 다단계 프로세스(검증·승인·배포·DevEngine PR) | — | C-01(주 해결책) |
| 10 | **Marketplace Plane** | 1차 동일 | 검증된 Strategy Package만 게시, 구매≠실행권한 | (I-04, I-07 소비) | — |
| 11 | **Unified Telemetry Plane** | 2B 동일 | OTel 기반 통합 관측성 + AI 평가/추적 | I-10(관측 근거) | — |
| 12 | **DevEngine Plane** | 1차 DevEngine + 2B Security Supply-chain Gate | AIOS 코드 유지보수 전용 trust domain, SBOM/시크릿/취약점 게이트 | — | — |

---

## 2. Contradiction 해소 (레지스트리 §2 6건 전부)

### C-01 — 배포 상태기계 이원화 → **Durable Workflow Plane(9b)이 유일한 권위가 된다**

> [2026-09-03 갱신] 원래 이 절은 "Durable Workflow & Ownership Plane"을 단수로 지칭했다.
> Temporal 코드 레벨 검증(`research_evidence_2026-09-03/ext_temporal.md`)이 리스/펜싱(초 단위,
> 상태 적음)과 장기 워크플로(시간~일 단위, 상태 많음)를 한 plane으로 묶은 것 자체가 설계
> 결함이라고 지적해 9a/9b로 분리했다. C-01(배포 상태기계 이원화)은 9b(Durable Workflow
> Plane)의 책임이다 — 9a는 오직 리스/펜싱만 다룬다.

**결정**: `foundation/paper_control`의 상태기계(`REQUESTED→READY→RUNNING→PAUSED→STOPPED/FAILED/
DEGRADED/RECOVERY_REVIEW`, fence_token 보유)를 canonical로 승격한다. 기존 `strategy_executions`는
이 plane의 **투영(projection)**으로 격하하거나 단계적으로 폐기한다. `package_ref`는 더 이상
불투명 문자열이 아니라 Strategy Registry Plane의 `artifact_hash`를 가리키는 실제 FK가 된다.

**마이그레이션 노트**: 이건 스키마 변경 + 실행 루프(`tick.py`) 재배선을 동반하는 큰 작업이다.
"코드 한 줄 배선"이 아니라 정식 마이그레이션 프로젝트로 다뤄야 한다 — L4급 세부 설계 문서에서
리프 단위로 쪼갠다(§4 참조).

### C-02 — 멱등성 이원화 → **PAP-006 패턴이 플랫폼 표준이 된다**

**결정**: `foundation/paper_control`의 요청 다이제스트 대조 방식(실패도 재현)을 AIOS 전체의
표준 멱등성 계약으로 승격하고, `core/idempotency.py`는 이 계약의 단순화된 특수 케이스로
재구현하거나 deprecate한다. Policy Plane이 이 표준을 `src/api/contracts/idempotency.py`(이미
L4 스펙에 설계돼 있음)를 통해 모든 라우터에 강제한다.

### C-03 — 리스크 한도 이원화(비율 vs 절대) → **Policy Plane이 교집합을 계산하는 단일 합성 지점이 된다**

**결정**: `foundation/mandates`의 비율 기반 한도는 "테넌트가 스스로에게 부과한 상한"으로,
`core/risk/engine.py`의 절대 지표는 "플랫폼이 강제하는 안전 바닥"으로 역할을 나눈다. 어느
쪽도 단독으로 ALLOW를 내지 않는다 — Policy Plane이 둘 다 조회해 **더 엄격한 쪽(min)**을 최종
결정으로 삼고, 이 조회가 실제로 일어났다는 것을 Event Ledger에 남긴다(I-09).

### C-04 — Live 게이트 철학(정적 하드블록 vs 런타임 토글) → **모순이 아니라 계층 분리로 재정의**

**결정**: 이건 둘 중 하나를 선택할 문제가 아니다. AIOS의 현재 정적 PAPER/LIVE 코드 레벨
파티션(Executor 2단 블록)은 **외곽 벽**으로 유지한다 — 이게 뚫리면 안 되는 최후 방어선이라는
성격은 그대로 옳다. 그 안쪽, 즉 LIVE가 실제로 열린 이후의 운영에는 QuantDinger식 런타임
다단 게이트(스코프+allowlist+notional 예약)를 **내곽 벽**으로 추가한다. "정적 벽 + 동적
게이트"의 이중 구조이지 양자택일이 아니다.

### C-05 — "구현됨"의 의미 불일치 → **I-10을 Definition of Done에 편입**

**결정**: Policy Plane 산하 모든 컴포넌트는 "배선 증명"(정적 검사 또는 적대적 통합 테스트)
없이는 완료로 인정하지 않는다. 이건 새 plane이 아니라 기존 개발 프로세스(CI Definition of
Done)에 추가하는 게이트다.

### C-06 — MCP 역할(얇은 프록시 vs 서비스) → **Agent Gateway Plane은 얇은 프록시만 허용**

**결정**: I-08을 표준으로 채택한다. AIOS가 향후 OBaI 스타일의 "MCP가 곧 서비스"인 도구가
필요해지는 경우(예: 순수 리서치용 read-only 도구), 그건 Agent Gateway Plane과 별도의,
명시적으로 표시된 read-only 신뢰 경계로 취급하고 자금 이동 capability와 같은 게이트웨이에
두지 않는다.

---

## 3. Plane 간 데이터 흐름 (요약)

```
Experience Plane ──(NL intent)──> Strategy Factory Plane ──(canonical IR)──>
Strategy Registry/Artifact Trust Plane ──(artifact_hash)──> Validation & Experiment Plane
        │                                                          │
        │ (승인된 아티팩트만)                                        │ (PASS만)
        ▼                                                          ▼
   Marketplace Plane                    Durable Workflow Plane(9b, 배포 요청 승인)
        │                                          → Execution Ownership Plane(9a, 리스 획득) → RUNNING
        │                                                          │
        └──────────────────(구매=리스팅 참조, 실행권한은 별도)────────┤
                                                                    ▼
                                                          Policy Plane (PDP)
                                                     (mandate ∩ RiskEngine 최소값)
                                                                    │
                                                                 ALLOW/DENY
                                                                    ▼
                                                            Execution Plane
                                                   (브로커 어댑터, 주문 상태기계, sandbox)
                                                                    │
                                                                    ▼
                                                              Event Ledger
                                                        (모든 위 화살표가 여기 기록)
                                                                    │
                                                                    ▼
                                                       Unified Telemetry Plane
                                       (모든 plane의 trace/metric/log가 여기로 수렴)

Agent Gateway Plane: Experience Plane과 병렬로 외부 AI가 위 파이프라인의 제한된 부분에만 접근
DevEngine Plane: 이 다이어그램 전체와 물리적으로 분리된 별도 trust domain (코드 변경만 다룸)
```

---

## 4. 우선순위 — 어느 plane부터 세부 설계·구현에 들어가는가

[OSS Deep Dive v2](AIOS_OSS_DeepDive_v2_CodeLevel_CrossVerification_2026-09-03.md) §7의
P0-R1~R3 증거가 정확히 두 plane에 집중된다:

1. **Execution Ownership Plane(9a)** — P0-R1(리스/펜싱)의 최종 정착지. **가장 먼저 세부
   설계가 필요하다** — 이미 [L4 spec](../aios/docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md)로
   완료됨(2026-09-03). Durable Workflow Plane(9b, C-01 해소 담당)은 Temporal 검증 결과
   "아직 도입 근거 부족"으로 세부 설계를 보류한다.
2. **Policy Plane (PDP)** — P0-R2(pre-submit gate 미배선), P0-R3(DataDistrust 미배선)의 최종
   정착지. C-03, C-05 해소도 여기. **두 번째 세부 설계 대상.**
3. 나머지 10개 plane은 위 두 개가 세부 설계·1차 구현까지 간 뒤 순서를 정한다 — 지금 전부
   동시에 설계하면 정확히 이번 리서치 트랙이 스스로 비판했던 "폭 확장" 오류를 반복하게 된다.

이 순서에 따른 L4급 세부 설계는 [`L4_execution_ownership_and_safety_gate_wiring_v1.0.md`](../aios/docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md)로 이미 작성했다(2026-09-03, Proposed). Durable Workflow Plane(9b)의 세부 설계는 §5의 미해결 질문으로 남는다.

---

## 5. 남는 미해결 질문 (Architecture Synthesis 완료 조건이 아니라 의도적 보류)

- Postgres 단일 장애점(A-01)을 어느 plane이 책임질지 — Durable Workflow Plane(9b)인지 별도
  Infrastructure Plane을 신설할지는 미정. 지금 결정하지 않는다(연구 결정 기록 §5 질문 8: "이
  component가 사라져도 contract를 유지할 수 있는가" — Postgres HA는 지금 결정하기엔 근거 부족).
- **Durable Workflow Plane(9b) 자체의 도입 여부** — Temporal 코드 레벨 검증 결과 "아직 근거
  부족"(AIOS의 검증·승인·배포 3개 파이프라인의 실제 복잡도가 durable workflow 엔진을
  정당화할 만큼 큰지 측정된 바 없음). C-01 해소(배포 상태기계 이원화)는 이 plane의
  존재 여부와 무관하게 별도로 진행 가능 — 두 결정을 묶지 않는다.
- **[2026-09-03 해소]** Sandbox Tier Manager(gVisor/Firecracker/WASM) — 4단계 모델(Tier
  0~3)과 착수 기준(Tier 1부터, AIOS가 실제 코드 실행 기능을 추가하기로 결정하는 시점)을
  [v3 검증](AIOS_OSS_DeepDive_v3_Infrastructure_Candidates_2026-09-03.md)에서 확정했다. 지금 당장 만들 필요는 없다(현재 AIOS엔 코드 실행 경로가 없음).
- DevEngine Plane과 나머지 11개 plane 사이의 정확한 경계(어떤 저장소/CI가 물리적으로 나뉘는가)는
  이미 ADR-2026-09-03-B가 부분적으로 다뤘으므로 이 문서에서 재논의하지 않는다.

---

## 6. Merged/폐기된 원안과 사유

| 원안 | 처리 | 사유 |
|---|---|---|
| 2B Structured Generation Gate | Strategy Factory Plane에 흡수 | 별도 plane이 아니라 Factory의 내부 메커니즘(스키마 강제) |
| 2B Artifact Trust Plane | Strategy Registry Plane과 병합 | 1차의 Strategy Registry가 이미 이 책임(해시·서명·provenance)을 전략에 대해 정의했음 — 범위를 아티팩트 일반으로 넓히는 것으로 충분, 별도 plane 불필요 |
| 2B Sandbox Tier Manager | Execution Plane 하위 메커니즘으로 격하 | 독립 plane이 되려면 자체 상태/권위가 있어야 하는데, 이건 Execution Plane이 신뢰도에 따라 호출하는 전략 패턴에 가깝다 |
| 2B Experiment & Registry Plane | Validation Plane과 병합 | 실험 이력과 검증 결과가 같은 아티팩트·같은 정책 버전을 참조하므로 분리하면 C-01류 이원화가 반복될 위험 |
| 2B Security Supply-chain Gate | DevEngine Plane에 흡수 | SBOM/시크릿/취약점 스캔은 DevEngine의 CI 게이트 중 하나이지 별도 권위 축이 아님 |
