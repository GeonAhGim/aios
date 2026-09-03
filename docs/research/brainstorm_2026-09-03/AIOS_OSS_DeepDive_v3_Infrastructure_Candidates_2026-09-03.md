# AIOS Infra 후보 4종 코드 레벨 검증 v3 — Temporal · OPA · Sigstore/in-toto/SLSA · gVisor/Firecracker

작성: Fable | 2026-09-03 | [OSS Deep Dive v2](AIOS_OSS_DeepDive_v2_CodeLevel_CrossVerification_2026-09-03.md) §7
Tier 2("새 인프라 도입 논의")로 미뤄뒀던 4개 후보의 코드 레벨 검증. 원본은
[`research_evidence_2026-09-03/`](research_evidence_2026-09-03/)의 `ext_temporal.md`(399줄),
`ext_opa.md`(410줄), `ext_sigstore_intoto_slsa.md`(254줄), `ext_gvisor_firecracker.md`(447줄).

## 결론부터 — 4개 전부 "도입 아님, 패턴만 차용"

이번 검증에서 가장 중요한 발견은 개별 결론이 아니라 **네 개가 전부 같은 결론에 도달했다는
사실 자체**다. Temporal(durable workflow), OPA(policy engine), Sigstore/Rekor(공개 서명·투명성
로그), gVisor/Firecracker(강격리 샌드박스) — 전부 성숙하고 검증된 인프라이지만, 넷 다 AIOS의
현재 규모·팀 구성·위협 모델에는 과잉이라는 것이 코드 레벨로 확인됐다. 이건 [1차 Deep
Dive](research_evidence_2026-09-03/)가 처음부터 우려했던 "이름 붙은 plane을 늘리기는 쉽지만
배선은 어렵다"는 원칙이, 이번엔 "인프라를 들여오기는 쉽지만 그 인프라가 실제로 필요한지는
전혀 다른 질문"이라는 형태로 재확인된 것이다.

| 후보 | 담당 Plane | 검증 결과 | 차용할 것 | 도입 안 하는 이유 |
|---|---|---|---|---|
| **Temporal** | Durable Workflow & Ownership | 도입 안 함 | RangeID 기반 **fencing token** 메커니즘 — 이미 [L4 spec](../aios/docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md)에 이 패턴으로 설계해 뒀다 | AIOS의 실제 필요(중복 tick 방지)는 리스 테이블 하나로 끝난다. Temporal의 진짜 강점(장기 workflow replay, human-in-loop 대기, saga)이 AIOS의 3개 파이프라인(검증·승인·배포)에 실제로 필요한지 아직 측정 안 됨 |
| **OPA** | Policy Plane (PDP) | 도입 안 함 | `opa test` 스타일 정책 단위테스트 관행, 번들 서명의 fail-safe 원칙(서명 실패 시 기존 번들 유지) | 팀이 Python 전용인데 Rego는 별도 언어. Sidecar 방식은 매 주문마다 네트워크 홉 추가(저지연 요구와 충돌). VaR·상관계수 같은 통계 계산 능력이 아예 없어 `core/risk/engine.py`는 그대로 남아야 함 — PDP 이전 필요성 자체가 약해짐 |
| **Sigstore/in-toto/SLSA** | Strategy Registry / Artifact Trust | 도구는 도입 안 함, 데이터 모델은 채택 | in-toto **Statement/Predicate** JSON 스키마(`_type`/`subject.digest`/`predicateType`/`predicate`) — AIOS의 기존 `artifact_hash`를 감싸는 봉투로 그대로 재사용 가능 | cosign의 keyless 서명은 Rekor라는 **공개** 투명성 로그에 서명·신원을 영구 기록한다 — AIOS 마켓플레이스의 비공개 판매용 전략 IP와 정면충돌. 자체 키(기존 `KeyRing` 확장) + Event Ledger(비공개 append-only)로 대체 |
| **gVisor/Firecracker** | Execution Plane (Sandbox Tier Manager) | 도입 안 함(아직) | 4단계 샌드박스 모델의 **개념** — Tier 0(현재, 실행 코드 없음)~Tier 3(Firecracker) | AIOS는 지금 **사용자 코드를 실행하는 경로가 아예 없다**(조건식 문자열만 평가). gVisor/Firecracker는 "악의적 게스트 OS로부터 호스트 커널을 지킨다"는 위협모델이라 과잉방어. 실제로 필요해지면 QuantDinger의 AST 화이트리스트(`safe_exec.py`)나 AgenticTrading의 서브프로세스+env allowlist 수준(Tier 1)부터 시작해야 한다 |

## 개별 요약

### Temporal — 그리고 내 설계 자체에 대한 지적

가장 값진 발견은 채택/기각 판단이 아니라 **이 조사가 제 [Target Architecture Freeze
v0.1](AIOS_Target_Architecture_Freeze_v0.1_2026-09-03.md)의 결함을 찾아냈다는 것**이다. 조사는
"Durable Workflow & Ownership Plane"이라는 이름 아래 실행 리스/펜싱(초 단위, 상태 적음)과
장기 workflow(검증·승인·배포, 시간~일 단위, 상태 많음)를 한 plane으로 묶은 것 자체가 설계
냄새(smell)라고 지적했다 — 두 문제의 요구곡선이 다르기 때문이다. 이건 타당한 지적이다.
**§3에서 이 plane을 분리하는 것으로 갱신했다.**

### OPA — 통계 계산 능력 부재가 결정적

`v1/ast/builtins.go`의 산술·집계 빌트인을 전수 확인한 결과 평균/표준편차/VaR/상관계수 등
금융 통계 함수가 전무하다. 즉 OPA를 들여와도 `core/risk/engine.py`의 9지표 평가기는 그대로
필요하고, OPA는 그 위에 얇은 합성 레이어 하나를 얹는 것뿐이다 — 지금 AIOS의 순수 Python
합성 지점(레지스트리 I-09)이 이미 하는 일과 실질적으로 같다.

### Sigstore/in-toto/SLSA — "공개 투명성 로그 vs 비공개 IP" 긴장 해소

cosign의 keyless 서명(Fulcio+Rekor)은 오픈소스 배포용으로 설계됐다 — "서명·신원이 공개
로그에 영구 기록되며 나중에 지울 수 없다"는 것이 README에 명시돼 있다. AIOS 마켓플레이스는
정반대(판매자가 돈을 받고 파는 비공개 IP)이므로 이 도구를 그대로 쓰면 안 된다. 다행히
in-toto의 Statement 데이터 모델은 서명 방식과 분리돼 있어, **모델만 빌리고 서명은 AIOS
자체 키로** 하는 절충이 가능하다는 것이 이번 조사의 핵심 성과다. `in-toto` Python 패키지가
실제로 PyPI에 존재하고 활발히 유지되는 것도 확인했다(Go 바이너리에 의존할 필요 없음).

### gVisor/Firecracker — "아직 필요 없다"는 것 자체가 유효한 결론

AIOS는 지금 사용자 제출 코드를 실행하는 경로가 **0건**이다(전략은 선언적 조건식 문자열).
gVisor/Firecracker의 위협모델(악의적 게스트 커널 탈출 방어)은 AIOS의 실제 리스크(마켓플레이스
플러그인이 예상 못한 동작을 하는 것)보다 훨씬 무겁다. 4단계 모델(Tier 0~3)을 세워두되, 실제
착수 시점은 AIOS가 정말로 임의 코드 실행 기능(Strategy Factory가 코드 생성까지 가는 경우 등)을
추가하기로 결정하는 순간으로 미룬다 — 이것도 유효한 연구 결론이다(late-binding이 항상 나쁜
것은 아니다).

## 3. Target Architecture Freeze v0.1 갱신 사항

위 Temporal 지적을 반영해 원 문서를 다음과 같이 수정했다(원 문서에 직접 갱신 표시):

- **Plane 9 분리**: "Durable Workflow & Ownership Plane" → **"Execution Ownership Plane"**(리스/
  펜싱, 초 단위, [L4 spec](../aios/docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md)이
  이미 이 축소된 범위로 설계돼 있었으므로 문서와 코드가 이제 일치한다)과 **"Durable Workflow
  Plane"**(장기 다단계 프로세스, 도입 여부는 이번 조사에서 "아직 근거 부족"으로 보류)로 나눈다.
- **Plane 6(Execution Plane) 하위 Sandbox Tier Manager**: "PoC 전까지 보류"였던 §5 미해결
  질문이 이제 구체적 4단계 모델(Tier 0~3)과 "Tier 1부터 시작"이라는 착수 기준을 갖게 됐다 —
  여전히 지금 당장 만들 필요는 없지만(코드 실행 경로가 없으므로), 필요해지는 순간의 설계는
  끝나 있다.
- **Plane 7(Policy Plane)**: OPA 도입은 기각, 순수 Python 합성 지점(I-09) 방향을 재확인.
  변경 없음.
- **Plane 4(Strategy Registry/Artifact Trust)**: in-toto Statement 모델 채택을 §3 계약에
  구체화할 근거가 생겼다 — 다음 세부 설계 문서(L4급) 대상으로 큐에 추가.

## 4. 다음 단계

- Plane 9 분리에 따라 [레지스트리 v1](AIOS_Registers_v1_Assumption_Contradiction_Invariant_Failure_2026-09-03.md)의
  I-02는 그대로 Execution Ownership Plane 소관으로 남고, Durable Workflow Plane에 대응하는
  새 Invariant는 아직 근거 부족으로 추가하지 않는다.
- Strategy Registry / Artifact Trust Plane의 L4급 세부 설계(in-toto Statement 봉투 + AIOS
  자체 서명)를 다음 우선순위로 제안한다 — [Execution Ownership L4 spec](../aios/docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md)과
  같은 지위(연구 산출물, 작업지시서 아님)로 작성할 것을 권한다.
- Temporal/OPA/gVisor·Firecracker는 성숙도 사다리상 **CODE-VERIFIED + FAILURE-ANALYZED
  완료, 결론은 "채택 보류"** — 이 상태로 레지스트리에 고정하고, AIOS의 실제 파이프라인
  복잡도나 코드 실행 요구가 커지면 재평가한다.
