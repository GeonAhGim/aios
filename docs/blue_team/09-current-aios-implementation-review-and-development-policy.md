# AIOS 현재 구현상태 검토 및 개발정책 전환 기록

Status: REVIEWED → adopted via ADR-2026-09-10-C (2026-09-10); NOT-AN-IMPLEMENTATION-ORDER  
Date: 2026-09-10  
Target repository: `GeonAhGim/aios`  
Observed main HEAD: `3b8b427cccfb5d79be3234135a408852c9b6d0be`

> 이 문서는 현재 구현 중인 AIOS의 구조와 개발 진행상태를 검토한 기록이다.
> 새로운 기능을 즉시 구현하라는 지시가 아니다.
> 특히 300 LOC 정책 변경은 Architecture 재설계가 아니라 현재 개발단계에 맞는
> Implementation Policy의 승격 제안이다.
> 실제 반영은 Architecture/Implementation Constitution 또는 ADR/승인된 task로
> 승격한 뒤 수행한다.

---

# 1. 검토 목적

AIOS는 초기에 "AI가 대규모 코드를 빠르게 생성하면서 구조가 통제 불능으로 커지는 것"을 막는 것이 중요했다.
그래서 작은 leaf, 작은 파일, 강한 zone, 강한 CI, 300줄 이하 소스파일 같은 정량적 통제가 유효했다.

그러나 현재 AIOS는 초기 scaffold 단계가 아니다.

현재 저장소는 다음과 같이 실제 금융 실행계층이 상당부분 구현되어 있다.

```text
Risk / Safety
Mandate / Compliance
OMS
Execution
Exchange Adapters
Ledger
Evidence
Idempotency
Execution Ownership
Event Store
Reconciliation
Market Data
Backtest
Portfolio / Allocation
Performance
Marketplace foundation
Paper Control
Frontend
```

따라서 이제 개발정책도 초기와 동일하게 유지하면 안 된다.

핵심 질문은 두 가지다.

1. 현재 AIOS의 큰 Architecture 방향이 잘못되어 전면 재설계가 필요한가?
2. 아니면 큰 방향은 맞고, 이미 형성된 bounded context를 완결하고 production-hardening을 해야 하는가?

검토 결과는 두 번째에 가깝다.

> AIOS는 Architecture를 다시 설계할 단계가 아니라,
> 이미 만든 Architecture를 완결하고 깊이를 높이는 단계다.

다만 이것은 "거의 완성되었다"는 뜻은 아니다.

Architecture-level correction은 많지 않지만,
LIVE funds를 허용하기 전 닫아야 할 production hardening은 여전히 상당하다.

---

# 2. 현재 Repository가 실제로 구현한 수준

README 기준 AIOS는 기관·자산운용사급을 목표로 하는 AI Trading OS이며,
결정론적 Risk Engine이 최상위 Master Authority이고,
LLM은 실시간 주문 경로에 들어가지 않는다.

현재는 PAPER 전용이고 LIVE는 코드 수준에서 hard-guarded 상태다.

이 방향은 매우 중요하다.

AI를 투자판단에 사용하더라도 최종 주문권한까지 LLM에게 넘기는 구조가 아니라:

```text
AI / Strategy / User Intent
            │
            ▼
    Deterministic Policy
            │
            ▼
      Risk Authority
            │
            ▼
   Mandate / Compliance
            │
            ▼
           OMS
            │
            ▼
        Execution
            │
            ▼
    Broker / Exchange
```

가 되어야 한다.

현재 Repository는 단순한 설계문서 저장소가 아니다.

`src/core`에는 다음과 같은 실제 모듈이 존재한다.

```text
approval
bitemporal
db
event_bus
eventstore
exceptions
executor
idempotency
indicators
loader
logging
notifications
observability
portfolio
rate_limit
...
```

`src/foundation`에는 다음 도메인들이 존재한다.

```text
allocation
backtest
charting
connections
ems
entities
evidence
execution_ownership
ledger
mandates
market_data
marketplace
paper_control
performance
positions
...
```

이 구조는 AIOS가 이미 "기능 하나씩 붙이는 자동매매 프로그램" 단계를 넘어
여러 금융 bounded context를 가진 운영체제로 발전했다는 증거다.

---

# 3. 현재 Architecture의 핵심 평가

현재 AIOS에서 유지해야 할 가장 중요한 구조는 다음이다.

```text
Deterministic Risk Authority
          ↓
Mandate / Compliance
          ↓
          OMS
          ↓
       Execution
          ↓
        Adapter
```

그리고 이를 다음 요소들이 둘러싸고 있다.

```text
Ledger
Evidence
Idempotency
Execution Ownership
Event Store
Reconciliation
Paper Control
Observability
Meta-Control Plane
```

이 방향은 다시 뜯어고칠 이유가 없다.

오히려 이제부터는 기존 Architecture를 기본값으로 보존하는 쪽이 중요하다.

향후 Claude Code / Codex / DevEngine의 Architecture 변경 원칙은 다음이 적절하다.

> 기존 Target Architecture를 기본적으로 보존한다.
> Architecture 변경은 invariant 위반, domain-boundary 오류,
> duplicate authority, unsafe bypass 또는 명확한 확장성 병목이 증명될 때만 허용한다.
> 그 외에는 기존 bounded context를 완결하고 failure semantics와 operational depth를 높인다.

즉 "더 좋은 구조가 떠올랐다"는 이유만으로 계속 재설계하면 안 된다.

현재는 Architecture churn보다 Architecture completion이 더 중요하다.

---

# 4. `src/contracts`는 미성숙 영역으로 보면 안 된다

이 부분은 최초 검토 시 오판하기 쉬운 영역이었다.

`src/contracts/__init__.py`는 사실상 placeholder이지만,
이는 구현을 못 해서 비어 있는 것이 아니다.

과거 `enterprise.py`에 있던:

```text
EvidenceReference
OrderIntent
PolicyDecision
StrategyPackage
```

등이 실제 호출되지 않고,
`src/foundation/mandates/**`에 이미 같은 책임이 실제 배선된 상태였기 때문에
중복 authority를 없애기 위해 제거되었다.

`.aios-zone`에서 `src/contracts/**`가 SCAFFOLD로 선언되어 있고
zone policy 자체는 agent가 수정할 수 없기 때문에 빈 package만 남아 있다.

따라서:

```text
src/contracts가 작다
→ Architecture가 미성숙하다
```

라는 결론은 잘못이다.

다만 장기적으로 Marketplace public SDK,
external strategy contracts,
versioned external API 등의 필요성이 커진다면
"public contract layer"를 별도로 도입할 이유가 생길 수 있다.

그때도 현재 내부 domain type을 중복해서 다시 만드는 방식은 피해야 한다.

---

# 5. 최근 개발의 질적 변화

최근 commit 흐름에서 중요한 변화는
"새 기능을 하나 더 구현"하는 것보다
기존 기능의 depth를 끌어올리는 작업이 많아졌다는 점이다.

현재 observed HEAD:

```text
3b8b427cccfb5d79be3234135a408852c9b6d0be
```

commit:

```text
test(oms-l4-32):
deepen overseas capability promotion with
failure-injection + perf + gate-red + concurrency
```

여기서는 KIS 해외주식 capability에 단순 positive test만 추가한 것이 아니다.

다음이 추가되었다.

## Failure Injection

예:

- network drop
- retry exhaustion
- malformed JSON
- business rejection
- token-fetch failure

중요한 것은 실패가 발생했을 때
"예외 없이 그냥 지나가나?"가 아니라:

```text
잘못된 SUBMITTED state 생성 방지
fail-closed
상태 불변
retry policy 상한 준수
```

를 검증한다는 것이다.

## Numeric Performance Assertion

절대적인 `100ms 이하` 같은 기준보다
같은 process에서 측정한 baseline과의 ratio를 사용하는 방향이 보인다.

공유 CI 환경의 machine variance 때문에
절대 ms threshold가 불필요한 red를 만들 수 있다는 것을 고려한 선택이다.

## Gate-Red Proof

단순히 test가 green인지 보는 것이 아니라
실제로 중요한 guard를 제거했을 때 test가 red로 뒤집히는지 확인한다.

이것이 중요하다.

테스트가 존재한다고 해서 실제로 invariant를 보호하는 것은 아니다.

예:

```text
Guard 존재
    ↓
Test GREEN

Guard를 mutation으로 제거
    ↓
Test RED
```

가 되어야 그 test가 load-bearing gate라는 것을 증명할 수 있다.

## Concurrency / Replay

단일-thread happy path만 보는 것이 아니라:

- shared adapter
- multi-exchange concurrent dispatch
- token fetch contention
- replay
- duplicate send
- live-configured subclass guard

등을 본다.

특히 Adapter에서 같은 Order를 다시 보내면 dedupe되지 않는 사실을
무조건 Adapter bug로 처리하지 않고:

> idempotency authority는 OMS가 가진다.

라는 domain ownership을 명확히 한 점은 좋은 방향이다.

하위 Adapter마다 별도의 idempotency semantics를 갖기 시작하면
duplicate authority가 발생할 수 있기 때문이다.

---

# 6. 현재 강점

## 6.1 Deterministic Risk Master Authority

AIOS의 가장 중요한 설계 선택 중 하나다.

LLM이나 Strategy가 주문을 "제안"할 수는 있어도
최종 허용/거부 authority를 가져서는 안 된다.

Risk, mandate, kill-switch, execution ownership이
결정론적 코드로 존재해야 한다.

현재 AIOS는 이 방향을 강하게 유지하고 있다.

## 6.2 PAPER-only / LIVE Hard Guard

현재 LIVE를 빠르게 열지 않고 PAPER 전용으로 묶어둔 것은 맞다.

LIVE readiness는 기능 수가 아니라:

```text
risk
mandate
compliance
recovery
broker uncertainty
DR
deployment
observability
change governance
```

가 모두 닫혔는지로 판단해야 한다.

## 6.3 Real DB 중심 테스트

단순 mock/unit test만으로 금융 실행시스템을 검증하면
transaction, concurrency, lock, outbox, recovery 문제를 놓칠 수 있다.

현재 AIOS는 실제 Postgres integration test를 상당히 사용하고 있으며,
최근 commit도 real DB concurrency / rollback / outbox fence 등을 깊게 본다.

## 6.4 WORM / Outbox / Idempotency / Kill-Switch

ADR의 자체 감사에서도
이 영역은 실제 wiring이 존재하는 강점으로 평가된다.

특히 OMS와 회계/감사 경로에서:

```text
immutable evidence
outbox
idempotency
kill-switch
```

가 서로 분리된 책임을 가지면서 실제 경로에 배선되는 것이 중요하다.

## 6.5 Failure Semantics

금융시스템은 정상 성공보다 실패시 의미론이 더 중요하다.

예:

```text
전송했는지 모름
DB write 실패
broker ack 유실
network timeout
worker crash
late response
duplicate replay
cancel/modify race
```

이때 "무슨 상태로 수렴하는가"가 핵심이다.

최근 테스트가 이쪽으로 깊어지는 방향은 매우 적절하다.

## 6.6 Meta-Control Plane

`aios-meta`를 분리하고,
AI coding agent가 직접 바꾸지 못하는 guard authority로 사용하는 방향은 좋다.

원칙적으로:

```text
DevEngine / Agent
        │
     change proposal
        ▼
       PR
        │
        ▼
Independent Guard
        │
        ▼
      AIOS
```

여야 한다.

DevEngine이 AIOS runtime policy authority가 되어서는 안 된다.

DevEngine은 변경 생산자이고,
최종 gate는 독립되어야 한다.

---

# 7. 현재 가장 중요한 열린 결함 — Mandate / Compliance Enforcement

현재 audit baseline에는 알려진 열린 결함이 하나 남아 있다.

```text
require_mandate_false
```

의미는 다음이다.

운영 composition에서:

```text
make_foundation_pre_submit_gate(
    pool,
    require_mandate=False
)
```

가 사용되고 있다.

이 상태에서는 mandate가 없는 경우에도
특정 경로가 fail-closed REJECT가 아니라
audit-log-only passthrough 형태가 될 수 있다.

이 문제는 단순 코드스타일 문제가 아니다.

AIOS가 주장하는:

```text
Risk Authority
+
Mandate / Compliance Authority
```

중 Mandate/Compliance가 실제 운영경로에서 강제되지 않으면
이중 권위가 설계문서에만 있고 runtime에서는 절반만 존재하게 된다.

현재 검색에서 production wiring의 실제 예로:

```text
src/api/execution_deps.py
src/services/background_loops.py
src/services/oms/application/wiring.py
```

등에 `require_mandate=False`가 나타난다.

주의할 점은 문서 간 개수 표현이 일치하지 않는다는 것이다.

- ADR-2026-09-09-B: production composition 3곳이라고 기록
- audit-baseline.json: 4곳이라고 기록

따라서 실제 수정 전에 정확한 current hit count를 다시 계산해야 한다.

그러나 숫자가 3이냐 4냐보다 중요한 것은:

> defect 자체가 아직 열린 상태라는 점이다.

해결 목표는:

```text
mandate binding
→ require_mandate=True
→ no mandate = deterministic REJECT
→ adversarial test
→ audit baseline close
```

다.

이 항목은 현재 최우선 P0로 봐야 한다.

---

# 8. Compliance Gate와 Runtime Authority를 구분해야 한다

현재 CI에는 `Compliance gate wiring` 정적 검사가 있다.

이 검사는 매우 유용하지만,
정적 wiring check가 존재한다고 runtime compliance가 완결되었다는 뜻은 아니다.

구분해야 한다.

```text
Static Gate
- 필요한 decision id가 있는가?
- 잘못된 factory를 사용하지 않았는가?
- 우회 factory가 생기지 않았는가?

Runtime Enforcement
- mandate가 없으면 실제 REJECT 되는가?
- compliance deny가 order path를 실제 차단하는가?
- concurrency/replay에서도 우회되지 않는가?
```

즉:

```text
Static CI PASS
≠
Runtime Authority Complete
```

다.

H-1은 runtime composition을 실제로 닫는 작업이다.

---

# 9. NH Adapter의 현재 안전한 미완성

NH adapter에는 `get_order()`가 아직 `NotImplementedError`다.

이것은 단순히 구현을 빼먹은 것과 조금 다르다.

현재 코드의 설명은
공식 API의 `dailyOrderExecution` 응답과
AIOS가 사용하는 `exchange_order_id` 식별체계의 대응관계가
충분히 검증되지 않아
추측으로 잘못된 주문상태를 만드는 것보다
명시적으로 미구현하는 것이 안전하다는 판단이다.

이 철학은 맞다.

금융 시스템에서는:

```text
불확실한 구현
```

보다

```text
명확한 Unsupported / Fail Closed
```

가 낫다.

다만 LIVE readiness 관점에서는
broker 장애 후 주문상태를 재조회할 수 없는 것이 큰 operational gap이므로
그대로 남겨둘 수는 없다.

필요한 것은:

- 실계좌/공식자료를 통한 identifier mapping 확인
- REST order re-query
- 불가능하면 명확한 fail-closed recovery policy
- WS schema/subscription

이다.

---

# 10. H-4 Backup / PITR / Restore Drill

현재 root에서 확인한 범위에서는:

```text
scripts/backup/pg_basebackup.py
```

가 아직 존재하지 않는다.

이는 LIVE funds 이전에 반드시 해결해야 한다.

금융시스템에서:

```text
backup 있음
```

만으로는 부족하다.

필요한 것은:

```text
backup
→ restore
→ consistency verification
→ healthcheck
→ periodic drill
```

이다.

즉 "백업 파일을 만든다"가 아니라
"실제로 복구할 수 있음을 반복 증명한다"가 목표여야 한다.

---

# 11. H-6 Production Container / Deployment

현재 root `Dockerfile`도 확인되지 않았다.

개발환경용 PostgreSQL compose만 있고
production composition이 완결되지 않았다면
LIVE readiness라고 할 수 없다.

향후 필요한 범주는:

```text
API image
Worker image
Frontend image
Production compose / orchestration
Health endpoints
Migration strategy
Startup ordering
Secrets
Environment isolation
Smoke tests
Rollback
```

이다.

그러나 이것은 Architecture를 다시 만드는 일이 아니다.

현재 application/domain Architecture를
운영 가능한 deployment artifact로 완결하는 작업이다.

---

# 12. H-5 Supply-Chain Gate는 이미 상당부분 구현되었다

이 부분은 과거 ADR을 읽을 때 주의해야 한다.

ADR 작성 당시에는 H-5가:

```text
pip-audit 없음
npm audit 없음
dependabot 없음
```

등의 gap으로 기록되었지만,
현재 `.github/workflows/quality.yml`에는:

- gitleaks
- pip toolchain upgrade
- pip-audit wrapper
- npm audit high
- zone manifest
- compliance wiring
- ruff
- mypy
- pytest
- coverage ratchet
- meta guards

등이 실제로 들어가 있다.

따라서 H-5를 현재도 "아무것도 안 됨"이라고 판단하면 안 된다.

AIOS는 현재 이미 공급망/secret scan을 CI에 넣는 방향으로 진전했다.

---

# 13. CI 운영 모델의 장점과 위험

현재 `quality.yml`은 push마다 GitHub Actions를 돌리지 않는다.

이유는 worker fleet가 높은 빈도로 push하여
`cancel-in-progress` 때문에 workflow가 끝까지 완료되지 않는 문제가 있었기 때문이다.

현재 전략은:

```text
Local CI
→ commit-level 1차 검증

GitHub Actions
→ 3시간마다 latest main 재검증
```

이다.

이 구조는 비용/실행폭주 관점에서 이해할 수 있다.

그러나 중요한 위험이 있다.

```text
main에 새 commit 존재
        │
        ├─ local CI만 통과
        │
        └─ independent GitHub Actions는 아직 미실행
```

인 시간이 생긴다.

현재 observed HEAD에 대해 connector에서 combined status가 별도로 확인되지 않았다.

따라서 원칙은:

> "최근 어떤 commit이 green이었다"와
> "현재 main HEAD가 독립 CI에서 green이다"를 구분해야 한다.

특히 LIVE 준비단계에서는:

```text
Current HEAD
+
Required independent checks
+
Green
```

이 명확해야 한다.

---

# 14. Branch / Change Governance

이전 검토에서 main branch protection이 비활성 상태로 관찰된 적이 있다.

이번 재점검에서는 GitHub integration이 branch-protection admin endpoint를 403으로 막아
현재 상태를 독립적으로 재확인하지 못했다.

따라서 이 항목은:

```text
Previously observed gap
Current status: re-verification required
```

로 기록해야 한다.

AIOS의 최종 governance 목표는 다음이어야 한다.

```text
No direct push to main
No force push
PR only
Required checks
Guard checks
Current HEAD green
Review policy
Signed release/tag where appropriate
```

특히 DevEngine/Codex/Claude가 직접 main을 쓸 수 있으면
Meta-Control Plane의 의미가 약해진다.

AI agent의 이상적인 권한은:

```text
branch
commit
PR
```

까지고,
보호된 main merge는 policy gate를 거쳐야 한다.

---

# 15. 왜 300 LOC 제한이 초기에는 맞았는가

300 LOC 규칙을 "잘못된 정책이었다"고 해석하면 안 된다.

초기 AI 개발에서 파일 길이를 강하게 제한한 것은
다음 문제를 막는 데 효과적이었다.

## AI Agent Code Explosion

Agent는 한 task에서 요구사항을 만족시키려고
한 파일에 계속 코드를 붙일 수 있다.

300줄 제한은 이를 강제로 멈추게 한다.

## God Object / God Module 억제

초기 domain이 아직 정착되지 않은 상태에서는
한 파일에:

```text
API
DB
business rule
broker call
logging
validation
```

이 몰리는 것을 막는 효과가 있다.

## Reviewability

작은 diff는 human/AI review가 쉽다.

## Early Structural Discipline

초기에는 정확한 bounded context보다
무질서한 성장 방지가 더 급하다.

따라서 초기 정책으로서는 합리적이었다.

---

# 16. 왜 지금 300 LOC Hard Cap을 풀어야 하는가

현재 AIOS는 상황이 달라졌다.

이미:

```text
OMS
Risk
Mandates
Ledger
Execution Ownership
Evidence
Market Data
Backtest
```

등의 도메인이 형성되어 있다.

이제 가장 중요한 것은
한 domain invariant가 local하게 이해되는가이다.

300줄 제한이 hard CI rule로 남으면
Agent는 architecture quality보다 line count를 최적화하기 시작한다.

예:

```text
submit.py
validation.py
helpers.py
state.py
utils.py
common.py
```

겉으로는 각 파일이 작다.

그러나 실제로 주문 하나의 lifecycle을 이해하려면
6개 파일을 왕복해야 한다.

이것은:

```text
Small Files
≠
Good Architecture
```

라는 대표적 사례다.

---

# 17. 금융시스템에서 특히 위험한 Artificial Fragmentation

AIOS 같은 시스템에서는
다음 invariant가 한 흐름에서 추적 가능해야 한다.

```text
Execution Ownership
        ↓
Mandate
        ↓
Risk
        ↓
Compliance
        ↓
Kill Switch
        ↓
Idempotency
        ↓
OMS Transition
        ↓
Outbox
        ↓
Broker Adapter
```

이 흐름이 단순히 300줄 제한 때문에
파일 10개 이상으로 흩어지면:

- ALLOW authority 추적이 어려워진다.
- REJECT authority 추적이 어려워진다.
- bypass audit가 어려워진다.
- agent가 전체 invariant를 놓치기 쉽다.
- duplicate authority가 생기기 쉽다.
- change impact가 보이지 않는다.

즉 line count를 줄이다가
safety reasoning surface를 늘리는 역설이 생긴다.

---

# 18. 새 원칙 — Domain Cohesion First

300 LOC hard cap을 없애고
아무 제한도 두지 않는 것이 아니다.

통제 기준을 다음으로 승격한다.

```text
File Length
    ↓

Bounded Context
Aggregate
Capability
Invariant Ownership
Independent Change Axis
Dependency Direction
Public Contract
Safety Boundary
```

핵심 문장:

> 파일은 줄 수가 아니라 책임과 invariant의 경계로 나눈다.

---

# 19. 권장 LOC 정책

다음 정도가 적절하다.

## 300 LOC

Hard fail 폐지.

## 400~500 LOC

정상 허용 범위.

파일이 한 capability를 응집하고 있다면 문제로 보지 않는다.

## 600 LOC 이상

Warning.

리뷰어가 책임이 섞였는지 확인한다.

자동 분할하지 않는다.

## 800~1,000 LOC 이상

Architecture Review Trigger.

질문:

- 책임이 둘 이상인가?
- 독립적인 change axis가 있는가?
- public/private 부분을 분리할 수 있는가?
- testability가 나빠졌는가?
- dependency fan-out이 커졌는가?

## 1,000 LOC 이상

강한 review가 필요하지만
그 자체를 CI fail 이유로 삼지 않는다.

예외적으로 하나의 generated table,
protocol mapping,
deterministic rule matrix처럼
실제로 한 책임이면 유지할 수도 있다.

---

# 20. LOC는 Architecture Constraint가 아니라 Observation Metric

중요한 정책 문구:

> LOC는 관찰 지표다.
> LOC 자체는 Architecture invariant가 아니다.

즉:

```text
600 LOC
→ 무조건 나쁨 X

180 LOC
→ 무조건 좋음 X
```

180줄짜리 파일도:

```text
risk
ledger
broker
auth
```

4개 authority를 섞고 있다면 잘못된 파일이다.

반대로 700줄이어도
하나의 deterministic state machine을
명확하게 담고 있다면 더 나을 수 있다.

---

# 21. 실제 CI가 봐야 할 것

LOC hard gate 대신 다음을 본다.

## Complexity

- cyclomatic complexity
- cognitive complexity
- deeply nested branch

## Dependency

- circular dependency
- forbidden import direction
- domain → adapter 역의존
- foundation boundary 침범

## Authority

- duplicate risk authority
- duplicate mandate authority
- duplicate idempotency authority
- local bypass

## State

- hidden mutable global
- side effect leakage
- ambiguous transaction ownership

## Safety

- fail-open
- missing negative tests
- missing failure injection
- missing gate-red proof
- missing concurrency proof
- missing replay/idempotency proof

## Public API

- accidental public surface expansion
- incompatible contract change

이런 항목이 실제 architecture quality와 더 강하게 연결되어 있다.

---

# 22. `utils.py`, `helpers.py`, `common.py` 정책

300줄 제한의 가장 흔한 부작용이
의미 없는 helper 파일이다.

예:

```text
order_service.py
order_helpers.py
order_utils.py
order_common.py
```

이런 분리는 책임을 명확하게 하지 않는다.

오히려 domain ownership을 숨긴다.

따라서 원칙은:

> Generic helper 이름으로 domain logic을 숨기지 않는다.

가능하면:

```text
order_submission/
  policy.py
  transition.py
  repository.py
```

처럼 의미 있는 capability 이름을 사용한다.

---

# 23. 권장 Package 구조 예

## OMS

```text
oms/
  order_submission/
    model.py
    policy.py
    service.py
    repository.py
    errors.py
    tests/

  cancel_modify/
    service.py
    policy.py
    repository.py
    tests/

  unknown_resolution/
    resolver.py
    policy.py
    tests/
```

## Mandates

```text
mandates/
  enforcement/
  lifecycle/
  cache/
  evidence/
```

## Ledger

```text
ledger/
  posting/
  reconciliation/
  integrity/
```

"한 도메인 = 한 파일"이 아니다.

"한 도메인 = 의미 있는 package boundary"다.

그리고 package 내부 파일은
실제 책임이 분리될 때만 나눈다.

---

# 24. Safety-Critical Invariant Locality

AIOS에 별도 원칙으로 두는 것을 권장한다.

> Safety-critical invariant를 이해하기 위해
> 불필요하게 많은 파일과 layer를 왕복하게 만들지 않는다.

예:

주문이 broker로 전송되는 이유를 확인하려면
리뷰어가 최소한 다음 질문에 답할 수 있어야 한다.

1. 누가 이 주문을 실행할 authority를 가졌나?
2. mandate가 존재하는가?
3. risk는 왜 ALLOW했나?
4. compliance는 왜 ALLOW했나?
5. kill-switch는 어떤 상태였나?
6. duplicate인가?
7. state transition은 valid한가?
8. outbox write와 transaction은 어떻게 묶였나?

이 질문의 답이 프로젝트 전체에 산재하면 안 된다.

---

# 25. Strongly Co-Changing Code는 같이 둔다

파일 분리 판단의 가장 중요한 실무 기준 중 하나다.

> 항상 같이 바뀌는 코드를 억지로 분리하지 않는다.

예:

```text
OrderTransition
TransitionValidation
TransitionError
```

가 항상 같은 commit에서 같이 바뀐다면
과도한 분리는 benefit이 적다.

반대로:

```text
KIS transport
Risk policy
Ledger posting
```

이 독립적으로 바뀐다면
반드시 경계를 나눠야 한다.

---

# 26. Independent Change Axis가 있을 때 분리한다

분리 기준:

- 서로 다른 팀/owner가 변경하는가?
- release cadence가 다른가?
- dependency가 다른가?
- security boundary가 다른가?
- public contract가 다른가?
- persistence가 다른가?
- test harness가 다른가?

이 중 실제 독립성이 있으면 분리한다.

줄 수가 많다는 이유만으로 분리하지 않는다.

---

# 27. 300 LOC 정책을 갑자기 대규모 리팩터링 신호로 사용하면 안 된다

이 부분도 중요하다.

300줄 제한을 푼다고 해서:

```text
기존 파일 전부 합치기
```

를 하면 안 된다.

지금 AIOS에는 이미 수많은 테스트와 imports와 zone과 ownership이 존재한다.

대규모 구조이동은 unnecessary churn을 만든다.

권장 migration 방식:

## Step 1

300 LOC hard fail만 제거.

## Step 2

LOC warning으로 전환.

## Step 3

기존 파일은 기본적으로 그대로 둔다.

## Step 4

도메인을 실제 수정하는 task가 생길 때
Artificial Fragmentation이 발견되면 그때 local consolidation 검토.

## Step 5

합치는 것 자체를 목표로 하지 않는다.

목표는:

```text
Domain Cohesion
Invariant Locality
```

다.

---

# 28. 개발단계에 따라 구현정책은 바뀌어야 한다

이번 논의의 가장 중요한 일반화다.

## Phase 1 — Structural Sprawl Prevention

초기 AI 개발.

주요 정책:

- 작은 leaf
- LOC cap
- strict zone
- small diffs
- dependency restriction

목표:

> AI agent의 구조 폭주를 막는다.

## Phase 2 — Domain Formation

bounded context가 나타나기 시작한다.

주요 정책:

- domain ownership
- aggregate
- capability
- domain event
- contract

목표:

> 코드 조각을 실제 도메인으로 정착시킨다.

## Phase 3 — Domain Completion / Failure Semantics

현재 AIOS가 여기에 가깝다.

주요 정책:

- domain cohesion
- invariant locality
- failure injection
- concurrency
- replay
- mutation/gate-red
- fail-closed

목표:

> 정상동작이 아니라 실패해도 안전한 도메인을 만든다.

## Phase 4 — LIVE Production Hardening

주요 정책:

- DR
- PITR
- restore drills
- alert routing
- HA
- env separation
- deployment
- change governance
- compliance runtime enforcement

목표:

> 실제 돈을 다루어도 운영 가능하게 만든다.

## Phase 5 — Commercial / Institutional

주요 정책:

- SLO/SLA
- migration discipline
- backward compatibility
- tenant isolation
- versioned public contracts
- cost governance
- audit/regulatory evidence

목표:

> 한 사용자 시스템에서 다수 고객/기관이 신뢰할 플랫폼으로 만든다.

---

# 29. "정책이 바뀐다"는 의미

이것은 규칙을 느슨하게 하는 것이 아니다.

초기:

```text
300줄 넘으면 안 됨
```

은 단순하지만 낮은 수준의 통제다.

후기:

```text
domain authority
dependency
complexity
invariant
failure semantics
```

을 보는 것은 더 어렵지만 더 높은 수준의 통제다.

즉:

> 규칙 완화가 아니라 통제 수준의 승격이다.

---

# 30. AI Agent에게 정량규칙만 주면 생기는 문제

Claude/Codex/DevEngine은 명시적인 수치 목표를 매우 잘 따른다.

이 장점이 동시에 위험하다.

예:

```text
파일은 300줄 이하여야 한다.
```

라고 하면 Agent는:

```text
299줄
299줄
299줄
```

로 자르는 것을 성공으로 볼 수 있다.

하지만 Human Architect가 원하는 것은:

```text
좋은 bounded context
명확한 authority
안전한 invariant
```

다.

따라서 Agent policy는 "결과의 구조적 의미"를 평가해야 한다.

---

# 31. AI Coding Policy 후보

다음 문구를 Implementation Constitution 후보로 둘 수 있다.

> 소스 파일 300 LOC hard limit을 폐지한다.
> 코드는 파일 길이가 아니라 bounded context, aggregate, capability,
> invariant ownership 및 independent change axis를 기준으로 응집한다.
> 파일 크기는 architecture observation metric으로만 사용하며,
> LOC 단독 초과를 CI failure로 사용하지 않는다.
> 파일 분리는 독립된 책임, 독립된 변경축 또는 명확한 dependency/security boundary가 있을 때 수행한다.
> safety-critical invariant를 단순 LOC 준수를 위해 여러 파일에 분산시키는 것을 금지한다.
> generic `utils.py`, `helpers.py`, `common.py`에 domain authority를 숨기는 것을 금지한다.

---

# 32. 추가 Agent Guard 후보

## A. Domain Ownership

한 invariant는 하나의 authority만 갖는다.

예:

```text
Order idempotency → OMS authority
Risk decision     → Risk authority
Mandate approval  → Mandate authority
```

Adapter가 별도 authority를 만들지 않는다.

## B. Refactor Justification

Agent가 구조를 변경하려면 다음 중 하나를 제시한다.

```text
invariant violation
dependency cycle
duplicate authority
security boundary breach
measured performance bottleneck
unmaintainable independent change axes
```

단순 "더 깔끔해 보인다"는 이유는 부족하다.

## C. No Mass Refactor Without Evidence

대규모 package 이동은 별도 Architecture task로 격리한다.

## D. Preserve Failure Evidence

failure-injection / gate-red / concurrency tests를
refactor 과정에서 약화시키면 안 된다.

---

# 33. 현재 AIOS 방향성에 대한 최종 판단

현재 AIOS는:

```text
큰 방향이 틀려서 다시 만들어야 하는 상태
```

가 아니다.

더 정확히는:

> 상당히 잘 설계된 Trading Platform / Financial OS를
> production-hardening하는 단계다.

Architecture-level로 수정할 것은 제한적이다.

그러나 LIVE readiness까지 남은 operational work는 적지 않다.

따라서:

```text
"몇 가지 수정만 하면 된다"
```

는 표현을 다음처럼 이해해야 한다.

## Architecture 관점

맞다.

핵심 구조는 보존해도 된다.

## Production 관점

아니다.

실자금 전에는 H-1~H-13 같은 hardening이 모두 증명되어야 한다.

---

# 34. 현재 우선순위

## P0 — Mandate Enforcement

`require_mandate=False` 제거 및
실제 mandate binding.

```text
No Mandate
→ REJECT
```

를 adversarial test로 증명.

audit baseline close.

## P0/P1 — Change Governance

- branch protection 재검증
- PR-only
- no force push
- required checks
- current HEAD green
- agent direct-main 방지

## P1 — Broker Recovery

NH get_order / WS / uncertainty recovery.

## P1 — DR

backup / WAL / PITR / restore drill.

## P1 — Deployment

Docker/container, environment separation, healthcheck, smoke, rollback.

## P1 — Human Alerting

Alertmanager / Slack / PagerDuty류 실제 사람 호출.

## P1 — Environment Separation

dev / staging / live separation.

## P1 — H-1~H-13 전체 종료

HB-5 / LIVE funds 전에 모두 CI evidence로 닫는다.

---

# 35. MVP-2 기능 확장을 너무 빨리 당기면 안 되는 이유

AIOS에는 이미 매력적인 MVP-2 아이디어가 많다.

예:

- realtime WS
- richer DSL
- bracket/OCA
- screener
- unified P&L
- BYOAI
- marketplace
- portfolio marketplace
- no-code automation
- tax/report
- PWA

하지만 현재 H-1 같은 authority gap이 열린 상태에서
MVP-2 기능을 공격적으로 늘리면
새 기능이 기존 불완전한 authority 위에 올라간다.

예:

```text
Marketplace Strategy
      ↓
AUTO PAPER/LIVE
      ↓
Mandate authority가 아직 완결되지 않음
```

이면 나중에 훨씬 비싼 refactor가 된다.

따라서 순서는:

```text
MVP-1 Architecture Completion
→ Production Hardening
→ LIVE readiness evidence
→ MVP-2 activation
```

이 더 안전하다.

---

# 36. Architecture-first 원칙은 계속 유지

"처음부터 최종형 Target Architecture를 설계하고,
구현은 단계적으로 활성화한다"는 방향은
현재 검토에서도 유효하다.

다만 중요한 보완은:

> Target Architecture를 설계한 뒤에는
> 계속 새 Architecture를 그리는 것이 목표가 아니다.

어느 시점부터는:

```text
architecture exploration
```

에서

```text
architecture completion
```

으로 전환해야 한다.

현재 AIOS는 그 전환점에 와 있다.

---

# 37. 현재 Repository의 의미

현재 Repository는 더 이상:

```text
아이디어
+
거대한 설계문서
+
얕은 프로토타입
```

으로만 평가하면 안 된다.

실제 구현에는:

- real DB tests
- outbox
- idempotency
- WORM
- risk
- mandates
- paper control
- exchange adapters
- execution ownership
- reconciliation
- backtest
- market data
- performance
- marketplace foundation
- frontend
- adversarial tests
- mutation/gate-red tests

가 존재한다.

따라서 다음 리스크는 "아무것도 구현 안 됨"이 아니라:

```text
구현된 수많은 도메인이
최종 operational composition에서
모두 같은 invariant를 지키는가?
```

이다.

이 질문이 현재 단계에서 더 중요하다.

---

# 38. 완료 정의도 바뀌어야 한다

초기 task의 Done:

```text
코드 있음
test 있음
```

이었다면,

현재 Done은:

```text
positive
negative
failure injection
transaction rollback
concurrency
replay
gate-red
performance regression
operational wiring
```

까지 확대되어야 한다.

모든 leaf에 무조건 모든 항목을 기계적으로 요구할 필요는 없지만,
Safety-critical path는 이 수준이 적절하다.

---

# 39. Deepening 작업의 장점과 주의점

최근 D1 → D2/D3 deepening 방식은 좋다.

특히:

```text
failure injection
performance assertion
gate-red
multi-instance
replay
```

를 추가하는 것은
금융시스템의 실제 robustness를 높인다.

다만 주의할 점도 있다.

"모든 테스트 파일에 performance test 하나씩 넣기"처럼
새로운 정량 checklist가 또 목적화되면 안 된다.

각 test는 실제 threat/failure model에 근거해야 한다.

예:

```text
이 코드에 실제 concurrency가 존재하지 않음
→ 억지 concurrency test 불필요
```

반대로:

```text
outbox worker
unknown resolver
broker dispatch
```

는 concurrency가 핵심이므로 반드시 깊게 본다.

즉 300 LOC와 같은 실수를
D3 checklist에서도 반복하지 않아야 한다.

---

# 40. 최종 개발정책 원칙

AIOS의 다음 개발 단계에서는
다음 우선순위를 권장한다.

```text
1. Correct Authority
2. Correct Invariant
3. Correct Failure Semantics
4. Correct Domain Boundary
5. Correct Operational Wiring
6. Test Evidence
7. Performance
8. File Size
```

File size는 가장 아래에 가깝다.

---

# 41. 최종 결론

현재 AIOS의 방향성은 맞다.

특히:

- Deterministic Risk Master Authority
- LLM 비실시간 주문경로
- PAPER-only hard guard
- OMS 중심 idempotency
- Ledger/Evidence/Outbox
- Execution Ownership
- Mandate/Compliance 분리
- Meta-Control Plane
- failure-injection / concurrency / gate-red 강화

는 유지할 가치가 있다.

현재 필요한 것은 대규모 재설계가 아니다.

필요한 것은:

```text
Mandate enforcement 완결
Change governance
Current HEAD independent green
Broker recovery
DR/PITR
Production deployment
Human alert routing
Environment isolation
H-1~H-13 closure
```

이다.

그리고 구현정책은 현재 단계에 맞춰:

```text
300 LOC Hard Cap
```

에서

```text
Domain Cohesion
+
Invariant Locality
+
Complexity / Dependency / Authority Gates
```

로 전환하는 것이 맞다.

이를 한 문장으로 정리하면:

> AIOS는 지금 Architecture를 다시 만드는 단계가 아니라,
> 이미 형성된 Architecture의 도메인을 완결하고
> failure semantics와 production-hardening을 깊게 만드는 단계이며,
> 300 LOC 제한 해제는 그 단계 전환에 맞춘 구현정책의 승격이다.

---

# 42. 채택 전 검토 체크리스트

이 문서의 300 LOC 정책을 실제 적용하기 전에 다음을 확인한다.

- 현재 Meta Guard에 LOC hard fail이 실제 존재하는가?
- local CI에 LOC checker가 있는가?
- Claude/Codex worker prompt에 300 LOC가 hard requirement로 들어가는가?
- file split 관련 기존 ADR이 있는가?
- zone policy와 충돌하는가?
- 600/800/1000 warning을 어디서 출력할 것인가?
- complexity metric을 무엇으로 측정할 것인가?
- dependency cycle/forbidden import 검사를 무엇으로 할 것인가?
- "generic helper"를 정적으로 잡을지 review rule로 둘지?
- 기존 artificial fragmentation을 즉시 합칠 것인지?
  - 권장: 즉시 대규모 병합하지 않음
- 기존 tests/import paths를 보존하면서 점진적으로 consolidation할 수 있는가?

이 검토 후 Architecture/Implementation Constitution에 승격한다.

---

# 43. Evidence / Current Repository References

이 기록을 작성할 때 재확인한 주요 현재 경로:

```text
README.md
audit-baseline.json
docs/design/ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md
.github/workflows/quality.yml
src/contracts/__init__.py
src/api/execution_deps.py
src/services/background_loops.py
src/services/oms/application/wiring.py
src/exchanges/nh/trading_mixin.py
```

Observed current main HEAD:

```text
3b8b427cccfb5d79be3234135a408852c9b6d0be
```

Current head commit deepening focus:

```text
KIS overseas capability
failure injection
numeric performance
gate-red mutation proof
concurrency/replay
```

Current audit-baseline known open item:

```text
require_mandate_false
```

Current root checks performed during this review:

```text
Dockerfile                         NOT FOUND
scripts/backup/pg_basebackup.py    NOT FOUND
```

Branch-protection endpoint could not be read during this review because the
current GitHub integration returned 403 for the administrative endpoint.
The previous audit observation should therefore be treated as needing
re-verification, not as newly re-proven fact.
