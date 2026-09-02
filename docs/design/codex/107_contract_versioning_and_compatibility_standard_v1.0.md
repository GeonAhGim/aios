# Contract Versioning and Compatibility Standard v1.0

> 상태: **Mandatory cross-cutting standard.** 22번(core enterprise contracts)과
> `contracts/v1/`(실제 JSON Schema + fixture)이 정의한 4개 FROZEN-CANDIDATE 계약,
> 34번 §7(인터페이스 계약), 72번 §3(cross-cutting standard contracts)이 계약의
> **존재**를 정의했다면, 이 문서는 계약이 **바뀔 때** 무엇을 해야 하는지를 정의한다
> — 이 규칙이 지금까지 어디에도 없었다.
>
> 작성자: Claude Code(구현 세션). 근거: `mihwa-aios/src/contracts/enterprise.py`가
> `contracts/v1/core-contracts.schema.json`을 실제로 구현하고 있다는 걸 확인했고
> (PR #4), 이 최초의 성공 사례를 반복 가능한 절차로 만든다.
>
> 작성일: 2026-09-02

---

## 1. 왜 필요한가

103번 §4 P1-01이 "API/event schema와 versioning 규칙"을 모든 모듈의 필수 항목으로
요구하지만, **버전이 언제 몇 단계 올라가는지, 이전 소비자가 어떻게 되는지**를 정한
문서는 없었다. `contracts/v1/`이 `v1`이라는 이름을 갖고 있다는 것 자체가 이미 "v2가
언젠가 생긴다"를 전제하는데, 그 절차가 없으면 34번 §7의 "계약 변경은 compatibility
check, consumer test, release note, rollback policy를 요구한다"는 문장이 실행
불가능한 선언으로 남는다.

---

## 2. 계약의 3계층 — 변경 파급 범위가 다르다

| 계층 | 예시 | 변경 시 파급 |
|---|---|---|
| **L1 — Frozen core contract** | `PolicyDecision`, `EvidenceReference`, `StrategyPackage`, `OrderIntent`(22번 4개) | 전체 시스템의 모든 소비자, LIVE 게이트와 직결 |
| **L2 — Domain contract** | `TenantContext`, `PortfolioMandate`, `RiskDecision`(34번 §7 목록) | 해당 도메인 + 직접 소비 컨텍스트 |
| **L3 — Context-local contract** | 한 bounded context 내부 command/event(예: `MembershipGrantedEvent`) | 그 context 내부만(다른 context는 L2 계약을 통해서만 이걸 간접 소비) |

계층이 높을수록 변경 승인 문턱이 높다. L1 계약 변경은 이 표준 §5의 전체 절차를
요구하고, L3는 해당 context owner의 PR 리뷰만으로 충분하다(단, §3 호환성 규칙은
계층 무관하게 항상 적용).

---

## 3. 호환성 규칙 — Semantic Versioning의 계약 버전

계약은 `schema_version`(72번 §3 봉투 필드)을 코드가 아니라 **필드 목록**으로
판단한다.

### 3.1 PATCH — 버전 번호 불변, 아무 필드도 안 바뀜

문서/설명 수정, 내부 구현 변경으로 스키마에 영향 없음. 소비자 조치 불필요.

### 3.2 MINOR — 버전 접미어 없이 그대로, 소비자는 무시 가능

- 새 optional 필드 추가(기본값 있음 또는 `null` 허용).
- 새 enum value 추가 **단, 기존 소비자가 미지의 enum을 만났을 때 죽지 않고
  fallback(예: `UNKNOWN` 처리 또는 무시)하는 게 계약으로 이미 명시돼 있어야 함**
  — 그렇지 않으면 MAJOR로 취급한다.

### 3.3 MAJOR — `schema_version` 필드 증가 필수(`v1` → `v2`)

- 필드 제거, 필드 타입 변경, 필수(required) 필드 추가, 기존 enum value 제거/의미
  변경, 필드 이름 변경(별칭 없이).
- 새 버전은 **기존 파일을 덮어쓰지 않는다**. `contracts/v1.py` 옆에
  `contracts/v2.py`를 만든다(106번 §3.1 네이밍 규칙과 일치). 발행자(producer)는
  전환 기간 동안 v1과 v2를 **동시에** 발행할 수 있어야 한다(§4 전환 절차).

이 판단 기준은 `contracts/v1/core-contracts.schema.json`의 JSON Schema
`required`/`enum` 필드로 자동 검증 가능해야 한다 — 사람이 매번 눈으로 판단하지
않는다(§6 CI 요구사항 참조).

---

## 4. MAJOR 변경 절차 — 전환 기간 필수

1. **제안**: 변경 사유와 영향받는 소비자 목록을 `contracts/v<N+1>/README.md`에
   기록(기존 `contracts/v1/README.md` 형식을 따름 — 이 계약이 대체하는 문서 번호,
   fixture 경로 포함).
2. **fixture 우선 작성**: 새 valid/invalid fixture를 `contracts/v<N+1>/fixtures/`에
   먼저 만든다 — 코드보다 계약이 먼저(72번 §6 "writing sequence" 원칙과 동일하게,
   fixture 없이 구현 코드부터 쓰지 않는다).
3. **이중 발행 기간**: producer가 v(N)과 v(N+1)을 동시에 발행. 최소 기간은 계층별로
   다르다 — L1은 30일 이상 또는 모든 알려진 소비자의 마이그레이션 완료 중 늦은 쪽,
   L2/L3는 해당 context owner 재량(단 0일 금지 — 반드시 하나 이상의 배포 주기를
   거친다).
4. **소비자 마이그레이션**: 각 소비자는 자신의 contract test(§5)를 v(N+1) 기준으로
   갱신하고, v(N) 대응 코드는 이 시점부터 "deprecated" 표시(제거는 아직 아님).
5. **폐기(retire)**: 이중 발행 기간이 끝나고 알려진 v(N) 소비자가 없음을 확인하면
   producer가 v(N) 발행을 중단한다. **v(N) 파일 자체(`contracts/v1.py`,
   `contracts/v1/fixtures/`)는 삭제하지 않고 보존한다** — 과거 감사증적(audit
   evidence)이 그 시점의 스키마로 기록됐을 수 있기 때문(49번 Evidence Graph
   원칙과 일치).

---

## 5. Contract Registry — 지금 당장은 파일 시스템이 registry다

35번 §2.6과 92번이 "Contract Registry" 서비스를 미래 컴포넌트로 명시하지만, 아직
구현 전이다. 그 전까지는 **`AIOSproject/contracts/v<N>/`가 유일한 authoritative
registry**다. 규칙:

- 모든 L1/L2 계약은 여기 JSON Schema로 먼저 존재해야 하고, `mihwa-aios`의 Pydantic
  구현(`src/contracts/enterprise.py` 및 향후 `src/foundation/*/contracts/v1.py`)은
  이 스키마의 **번역**이지 원본이 아니다. 둘이 갈라지면 JSON Schema가 이긴다.
- 실제 Contract Registry 서비스(35번 §2.6)가 구현되면, 이 파일들을 그 서비스의
  초기 seed 데이터로 그대로 이관한다 — 지금부터 그 이관을 염두에 두고 파일 구조를
  유지한다(임의로 다른 디렉터리 관례를 만들지 않는다).

---

## 6. CI 요구사항 (아직 없음 — 다음 PR에서 추가할 것)

1. `contracts/v*/fixtures/*.valid.json`은 대응 schema에 대해 반드시 통과해야 한다.
2. `contracts/v*/fixtures/*.invalid-*.json`은 반드시 실패해야 한다(어떤 규칙
   위반인지 파일명에 명시 — 기존 `order-intent.invalid-direct-execution.json`
   패턴을 따름).
3. `mihwa-aios`의 Pydantic 계약 클래스(`src/contracts/enterprise.py` 등)가 JSON
   Schema의 필드 집합과 일치하는지 검증하는 테스트(현재 없음 — §5 "번역이지 원본이
   아니다" 원칙을 코드로 강제하는 첫 걸음).
4. MAJOR 변경 PR은 §4의 5단계 중 최소 1~2단계(제안 문서, fixture)가 없으면 merge
   차단.

이 CI가 없는 지금은 이 문서 자체가 코드 리뷰 체크리스트를 대신한다 — §4를 따르지
않은 계약 변경 PR은 사람이 반려한다.
