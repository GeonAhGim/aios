# 08. 2026-09-10 AIOS Idea Bank Session Record — Full Detail

Status: RAW-DISCUSSION-RECORD  
Purpose: 이번 대화에서 구체화된 판단과 아이디어를 요약본으로 대체하지 않고, 논의 순서와 논리를 보존한다.

---

## 1. 300줄 제한 해제와 완결된 도메인 단위 그루핑

### 사용자 문제제기

> 나는 코드를 300줄 이하로 강제했던 것을 해제하고 완결된 도메인 단위로 그루핑하라고 할까 싶다.

### 논의

AIOS에는 300줄 hard cap을 해제하는 쪽이 더 적합하다는 판단이 나왔다. 단순히 줄 수 제한을 없애는 것이 아니라 LOC 기반 강제규칙을 domain cohesion, dependency, complexity 기반 규칙으로 교체하는 것이 핵심이다.

초기에는 코드 비대화 방지에 유효하지만 OMS, Risk, Mandate, Ledger, Execution Ownership처럼 invariant가 많은 금융 도메인에서는 300줄 규칙이 오히려 한 흐름을 여러 파일로 기계적으로 찢을 수 있다.

예:

```text
submit.py
validation.py
helpers.py
state.py
utils.py
common.py
```

코드 줄 수는 작지만 하나의 주문 invariant를 이해하려면 여러 파일을 왕복하게 된다.

대신 다음 원칙을 제안했다.

> File-size first → Domain-cohesion first

파일이 아니라 Bounded Context / Aggregate / Capability를 기준으로 자른다.

예:

```text
oms/
  order_submission/
    model.py
    policy.py
    service.py
    repository.py
    errors.py
    tests/

  execution_recovery/
    ...

mandates/
  enforcement/
  lifecycle/
  cache/
  evidence/

ledger/
  posting/
  reconciliation/
  integrity/
```

완결된 도메인을 하나의 package/module boundary에 모으되 모든 것을 하나의 2,000줄 파일에 몰아넣는 것은 아니다. 책임이 실제로 독립될 때만 내부 파일을 분할한다.

권장 LOC 정책:

- 300줄 hard fail 제거
- 400~500줄 허용
- 600줄 이상 warning
- 800~1,000줄 이상 architecture review 후보
- LOC만으로 CI RED 금지

대신 실제 gate 후보:

- cyclomatic/cognitive complexity
- dependency cycle
- forbidden dependency direction
- public API surface
- domain boundary violation
- duplicate domain authority
- excessive fan-in/fan-out
- hidden mutable state
- fail-open path
- invariant/test 부재
- mutation/gate-red 검증 실패

추가 원칙:

- 강하게 함께 변경되는 코드는 함께 둔다.
- 서로 독립적으로 변경되는 책임만 분리한다.
- `utils.py`, `helpers.py`, `common.py` dumping ground를 금지한다.
- domain invariant를 파일 크기 때문에 분산시키지 않는다.

특히 Safety-critical invariant locality를 강조했다.

```text
ownership
→ mandate
→ risk
→ compliance
→ kill-switch
→ idempotency
```

이 흐름은 300줄 규칙 때문에 서로 멀리 흩어져서는 안 된다.

AI agent는 정량규칙을 잘 최적화하므로 300 LOC가 hard gate면 architecture보다 gate 통과를 위한 기계적 파일분해를 할 가능성이 있다.

따라서 다음 문구가 후보가 되었다.

> 소스 파일 300 LOC 제한을 폐지한다. 코드는 파일 길이가 아니라 bounded context, aggregate, capability 및 invariant ownership을 기준으로 응집한다. 파일 크기는 경고 지표일 뿐 분할 사유가 아니며, 분할은 독립된 책임·독립된 변경축·명확한 의존 경계가 존재할 때만 수행한다.

---

## 2. 개발단계별 구현정책 변화

### 사용자 문제제기

> 개발과정에서는 단계별로 구현정책이 바뀌어야 하는구나. 검토결과는 몇 가지 부분만 수정하면 되고 방향성은 맞는가?

### 논의

같은 구현정책을 처음부터 끝까지 고정하면 후반에는 정책 자체가 기술부채가 될 수 있다는 판단이다.

AIOS의 개발단계를 다음처럼 보았다.

### 초기 — 구조 폭주 방지

- LOC 제한
- 작은 leaf
- 강한 dependency 제한
- zone 제한

### 중기 — 도메인 형성

- bounded context
- capability
- aggregate
- invariant ownership

### 현재 — 도메인 완결성과 failure semantics 강화

- LOC 제한 완화
- domain cohesion
- fail-closed
- concurrency
- replay
- mutation testing

### LIVE 직전 — production hardening

- DR
- HA
- deployment
- observability
- compliance enforcement
- supply-chain
- change governance

### 상용화 이후

- SLO/SLA
- compatibility
- migration discipline
- tenant isolation
- versioned contracts
- cost/performance governance

규칙을 완화하는 것이 아니라 통제의 기준이 더 고차원으로 올라가는 것으로 해석했다.

AIOS 큰 방향은 전면 재설계할 이유가 없다는 판단을 유지했다.

핵심 구조:

```text
Deterministic Risk Authority
→ Mandate/Compliance
→ OMS
→ Execution
→ Adapter
```

주변:

```text
Ledger
Evidence
Idempotency
Execution Ownership
Event Store
Reconciliation
Meta-Control Plane
```

따라서 Architecture Redesign보다 Architecture Completion 단계라고 정의했다.

Claude/Codex/DevEngine 정책 후보:

> 기존 Target Architecture를 기본적으로 보존한다. 변경은 invariant 위반, domain-boundary 오류, 중복 authority, 명확한 확장성 병목이 증명될 때만 허용한다. 그 외에는 현재 도메인을 완결하고 깊이를 높인다.

---

## 3. 구현 후 실제 사용자 사용법

### 사용자 질문

> 이게 구현되면 사용자는 어떻게 쓸 수 있을까? 사용법.

### 논의

내부의 OMS, Risk Engine, Mandate, Ledger, Event Store 같은 복잡성은 사용자에게 숨긴다.

사용자 흐름:

```text
계좌 연결
→ 시장 탐색
→ 전략 만들기/선택
→ 검증
→ 위험 설정
→ PAPER
→ LIVE 승인
→ 자동 운용
→ 모니터링
```

계좌 연결 예:

- KIS
- NH
- Bitget

첫 화면:

```text
총 자산
국내주식
해외주식
Crypto
Cash
오늘 손익
월 수익률
MDD
```

통합 워치리스트/스크리너에서 가격, 거래량, 지표, 수급, 재무 조건 등으로 종목을 찾고 차트→분석→전략→백테스트→주문으로 이어지는 경험을 구상했다.

사용자는 코딩을 몰라도 자연어로 아이디어를 표현한다.

예:

> 코스닥에서 거래량이 전일보다 200% 이상 증가하고 외국인이 3일 연속 순매수하면서 20일선을 돌파하면 매수. 손절 -4%, 익절 +10%, 한 종목 5% 제한.

하지만 이 단계에 대한 설명은 이후 BYOAI 논의에서 중요한 수정이 이루어졌다. AIOS 자체 LLM이 반드시 이 요청을 처리하는 것이 아니라 사용자가 연결한 자신의 AI Agent가 처리할 수 있어야 한다.

전략은 실행 전에 backtest, walk-forward, 과적합 검증, 수수료/슬리피지/스트레스 테스트 등을 거친다.

사용자는 risk profile / mandate를 설정한다.

예:

```text
최대 투자금
종목당 최대 비중
일 손실한도
월 MDD
레버리지
거래시장 제한
시간 제한
```

PAPER를 먼저 운용하여 backtest 대비 실시간 성과괴리를 추적하고, 충분한 검증 후 LIVE 승격을 요청하는 구조를 선호했다.

LIVE에서는 AIOS가 주문이 왜 실행 또는 차단되었는지를 설명할 수 있어야 한다.

---

## 4. AI Assistant는 BYOAI Connector가 핵심

### 사용자 수정

> AI Assistant는 사용자가 본인이 쓰는 AI 에이전트를 연동할 수 있도록 커넥터를 만들어서 쓰는 것 아니냐. AIOS는 사용자에게 토큰을 제공하지 않는다.

### 논의

이 지적을 반영하여 AIOS AI 구조를 명확하게 재정의했다.

AIOS는 자체 LLM 토큰을 제공하거나 재판매하는 것을 기본 모델로 삼지 않는다.

사용자는:

- Claude / Claude Code
- ChatGPT / Codex
- Gemini
- 기타 MCP Agent
- 사내 Agent
- Local LLM

을 연결한다.

AIOS가 발급하는 Agent Token은 LLM inference token이 아니라 AIOS capability authorization token이다.

비용구조:

```text
Claude/OpenAI/Gemini 비용
→ 사용자 자신의 provider 계정

AIOS 이용료
→ AIOS
```

External Agent Connector가 기본 권장 방식이다.

```text
사용자 AI
  ↓
MCP / Agent Connector
  ↓
AIOS Agent Gateway
```

Agent scope:

```text
read
research
propose
paper
```

존재하지 않아야 하는 scope:

```text
live
fund withdrawal
risk policy modification
kill switch disable
credential administration
other tenant access
AIOS code modification
```

사용자가 Claude에게:

> 내 AIOS 계좌와 시장 데이터를 보고 전략을 연구해.

라고 하면 Claude가 read/research를 사용한다.

> 전략 만들어.

라고 하면 임의 Python code deployment가 아니라 structured StrategyProposal을 제출한다.

AIOS는:

```text
schema
→ compile
→ static validation
→ resource limit
→ backtest
→ risk
```

를 적용한다.

PAPER 실행도 사용자 confirmation이 필요하다.

BYOK Provider Connector는 선택 기능으로 둘 수 있다. 사용자가 AIOS 자체 UI에서 대화하고 싶으면 자기 OpenAI/Anthropic/Gemini API key 또는 Ollama/vLLM endpoint를 연결한다.

결론:

> AIOS AI Assistant = AIOS 자체 모델

이 아니라

> 사용자의 AI ↔ AIOS를 연결하는 Agent Gateway / Connector Experience

에 가깝다.

제품 포지션도:

> AI가 포함된 자동매매 프로그램

보다

> 어떤 AI든 연결 가능한 금융 운영체제

가 적합하다는 논의가 이루어졌다.

---

## 5. Marketplace를 핵심 제품축으로 확대

### 사용자 질문

> 마켓플레이스에도 신경을 쓰고 싶다. 사용자 관점으로 분석.

### 논의

Marketplace를 단순 전략 판매몰로 만들면 약하다.

핵심:

> 전략 발견 → AIOS 독립 검증 → 내 계좌 재검증 → PAPER → 구독 → Risk/Mandate 안에서 운용

첫 화면은 단순 수익률 랭킹이 아니라 사용자 적합성을 보여준다.

예:

```text
Korea Momentum Alpha

AIOS 검증기간
PAPER/LIVE 기간
CAGR
MDD
Sharpe
내 계좌 적합도
현재 포트폴리오와 상관관계

[내 계좌로 검증]
[PAPER 체험]
```

Marketplace의 killer feature 후보는 `내 계좌로 검증`이다.

판매자 기준:

```text
CAGR 27.8%
MDD -9.4%
```

사용자 계좌 기준:

```text
CAGR 21.1%
MDD -11.7%
```

차이 원인:

- capital
- broker
- commission
- slippage
- unavailable instruments
- mandate cap

Buyer Journey:

```text
Discover
→ Trust
→ Fit to Me
→ Trial
→ Subscribe & Deploy
→ Monitor
→ Exit
```

Creator Journey:

```text
AI Agent
→ research
→ strategy
→ compile
→ backtest
→ verification
→ protected listing
→ subscribers
→ settlement
```

Protected Source는 Creator 유입에 중요하다.

Visibility:

```text
Open
Protected
Invite-only
Private
```

Protected strategy는 source를 client에 내려주지 않고 server-side controlled execution을 사용한다.

장기적으로 Portfolio Marketplace도 검토했다.

```text
Korea Momentum
US Quality
Crypto Trend
Market Neutral
Cash/Hedge
```

같은 여러 전략을 하나의 risk-aware portfolio로 묶어 사용자 자산에 적용할 수 있다.

BYOAI와 Marketplace를 결합하면 사용자가 자신의 AI에게 Marketplace를 탐색시킬 수 있다.

예:

> 내 포트폴리오와 상관 0.3 이하, MDD 10% 이하, 최소 180일 검증된 전략 5개 찾아.

이것은 AIOS 자체 모델 없이도 AI-native Marketplace를 만들 수 있게 한다.

---

## 6. Marketplace Trust / Verification

전략 Marketplace의 핵심 불신:

- 성과 조작
- cherry picking
- overfitting
- slippage 누락
- 버전 변경
- backtest/PAPER/LIVE 혼동
- self-review
- 복제

AIOS는 다음을 분리해야 한다.

```text
Seller Claimed
AIOS Reproduced
PAPER Verified
LIVE Verified
```

`Verified` 하나로 뭉뚱그리지 않는다.

Immutable Version 정책:

사용자가 v1.7을 구독했는데 Creator가 v2.0을 만들면 자동교체하지 않는다.

```text
현재 v1.7
새 버전 v2.0
변경사항
재검증 결과

[현재 유지]
[새 버전 PAPER]
[업데이트]
```

실적은 버전별로 귀속한다.

AIOS Trust Score 후보:

```text
Reproducibility
Track Record
Risk Stability
Out-of-Sample
Execution Quality
Creator Reliability
Performance Stability
Dispute Rate
Version Stability
```

팔로워수와 financial trust를 동일하게 취급하지 않는다.

---

## 7. 구독료 정산과 돈세탁 위험

### 사용자 문제제기

> 구독료를 정산할 때 잘못하면 돈세탁 창구가 될 수 있다.

### 논의

잘못 설계하면 전략 Marketplace가 사실상 자금이전/정산 플랫폼으로 악용될 수 있다.

기본방향:

> AIOS가 돈을 자유롭게 보관/이체하는 주체가 아니라 외부의 적합한 PG/PSP를 이용하고, AIOS는 entitlement, settlement ledger, fee calculation, risk/hold policy를 관리한다.

초기에는 wallet-like 기능을 피한다.

금지 후보:

```text
user-to-user transfer
creator-to-creator transfer
transferable balance
cash-equivalent points
creator earnings로 다른 상품 구매
third-party payout
crypto payout
```

Seller KYC/KYB가 필요하다.

Payout destination은 Seller identity와 일치해야 한다.

Refund는 원결제수단으로 돌리는 구조를 기본으로 한다.

즉시 정산하지 않고:

```text
Payment
→ Pending
→ refund/fraud/AML review
→ Available
→ Payout
```

로 처리한다.

AML/Fraud signal 후보:

- buyer/seller 동일인
- 동일 은행계좌
- 동일 결제수단
- 동일 device/IP
- 상호 반복 구매
- 비정상 고가
- 구매 후 반복 환불
- 신규 Seller 매출 폭증
- 국가/신원/정산계좌 불일치
- 소수 Buyer에 매출 집중
- 거래 쪼개기
- 다계정

Risk:

```text
LOW → 정상
MEDIUM → 정산지연
HIGH → EDD/Hold
CRITICAL → Payout Freeze/Case Review
```

성과보수는 초기에는 피하고 고정 월구독이 관리하기 쉽다는 판단이 나왔다.

---

## 8. AIOS Fee는 어떻게 받는가

### 사용자 질문

> 사용자 구독료 정산 때 AIOS fee는 어떻게 결제받는가?

### 논의

판매자가 돈을 받은 뒤 AIOS에 fee를 송금하는 구조가 아니다.

결제 순간 server-side로 분리한다.

예:

```text
구독료             100,000
AIOS Fee            20,000
Creator Payable      80,000
```

PG/processor cost의 부담은 pricing policy로 결정한다.

AIOS 자체 SaaS 이용료와 Marketplace commission을 분리한다.

예:

```text
AIOS Pro              99,000
Marketplace Strategy  49,000
Total                 148,000
```

Ledger:

```text
AIOS SaaS revenue      99,000

Marketplace gross      49,000
  AIOS commission       9,800
  Creator payable      39,200
```

사용자는 한 번 결제할 수 있지만 내부 경제적 귀속은 분리한다.

가능한 경우 external PSP split settlement를 선호한다.

```text
Customer
  ↓
External PSP
  ├ AIOS platform fee
  └ Creator pending balance
```

Creator 돈이 AIOS 운영계좌에 들어왔다가 재송금되는 구조를 최소화한다.

핵심 invariant:

```text
MarketplaceFee = server-calculated
Creator payout <= verified Creator payable
AML Hold > Payout
Refund/Chargeback > Payout
```

---

## 9. 다음으로 볼 주제

### 사용자 질문

> 다음으로 봐야 할 것을 추천한다면?

### 논의

가장 먼저 `Marketplace Product Constitution`을 숙제로 남기는 것을 제안했다.

질문:

> AIOS Marketplace에서 무엇을 사고팔 수 있고, 각 상품을 산 사용자는 어디까지 할 수 있는가?

상품 후보:

```text
Indicator
Strategy
Signal
Portfolio
Automation
Managed Strategy
```

각 상품은 규제, 검증, 실행권한, source visibility, 판매자 자격, pricing, refund가 다르다.

그다음 과제:

- Buyer Trust System
- Creator Economy
- Marketplace → 운용 전환 규칙
- pricing/revenue model

Architecture는 지금 확정하되 기능 활성화는 MVP 단계에 맞춰 순차적으로 진행하는 원칙을 유지한다.

---

## 10. Community / SNS / Online Broadcast

### 사용자 아이디어

> 커뮤니티나 SNS, 온라인방송 같은 것도 지원한다면 상당히 인프라가 몰려드는 플랫폼이 되지 않을까?

### 논의

가능성이 크다. 다만 SNS를 별도 부가기능으로 붙이는 것이 아니라 Marketplace와 금융 객체를 중심으로 묶는 network layer가 되어야 한다.

일반 Creator는 현재:

```text
YouTube
Telegram
TradingView
별도 홈페이지
결제서비스
자동매매 프로그램
```

등을 따로 사용한다.

AIOS가 이를:

```text
Create
Publish
Community
Verify
Monetize
Operate
AI
```

로 묶을 수 있다.

라이브 방송 예:

방송자가 차트와 전략을 설명하면 시청자는 같은 화면에서:

```text
[전략 분석]
[내 계좌로 백테스트]
[PAPER 체험]
```

할 수 있다.

차트 drawing:

```text
[Add to my chart]
```

전략:

```text
[Inspect Strategy]
```

Backtest:

```text
[Reproduce]
```

단순 영상 스트리밍이 아니라 Interactive Financial Broadcast가 된다.

## 11. 말과 검증된 실적 연결

금융 SNS의 문제는 사후 주장이다.

AIOS에서는:

```text
Creator 주장
→ immutable timestamp/version
→ 실제 market outcome
→ PAPER/LIVE
→ AIOS verification
→ reputation
```

으로 연결할 수 있다.

Social popularity와 financial trust는 분리한다.

Verified label 후보:

```text
AIOS Verified Position
AIOS Verified PAPER Performance
AIOS Verified LIVE Performance
Sponsored
Creator Holds This Asset
Backtest Only
```

`Creator가 말한 것`과 `AIOS가 증명한 것`을 분리한다.

## 12. Market Manipulation / Moderation

금융 SNS는 일반 SNS보다 높은 수준의 moderation이 필요하다.

위험:

- 허위 수익률
- 종목 선동
- pump-and-dump
- sponsored disclosure 누락
- hidden position
- bot
- fake followers
- fake reviews
- paid promotion
- coordinated trading room

따라서 Social & Creator Economy Constitution에는:

- financial feed
- symbol community
- posts/ideas
- live broadcast
- paid community
- reputation
- verified performance
- sponsorship disclosure
- moderation
- anti-manipulation
- marketplace link
- BYOAI access

를 포함하는 것이 좋다는 결론이다.

## 13. 전체 Flywheel

```text
Market Data
→ Analysis / Idea
→ SNS / Broadcast
→ Creator Audience
→ Strategy / Indicator
→ AIOS Verification
→ Marketplace
→ PAPER
→ LIVE
→ Verified Performance
→ Creator Reputation
→ More Users
→ More Creators
```

여기에 BYOAI가 discovery/research/strategy authoring/monitoring을 가속한다.

장기 플랫폼 후보 정의:

```text
Trading OS
+ BYOAI Agent Platform
+ Strategy Marketplace
+ Financial Social Network
+ Creator Platform
```

핵심은 기능 수가 아니라 이들이 동일한 identity, market data, strategy version, evidence, track record, risk, execution, settlement, reputation, social graph를 공유한다는 점이다.

---

## 14. 이번 세션에서 숙제로 남긴 것

즉시 구현 지시로 넣지 않고 Idea Bank에서 추가 검토한다.

1. Marketplace Product Constitution
2. Marketplace Financial Constitution
3. Buyer Trust Constitution
4. Creator Economy Constitution
5. Social & Creator Economy Constitution
6. Interactive Financial Broadcast
7. Financial Knowledge Graph
8. Recommendation Engine
9. Portfolio Marketplace
10. BYOAI Marketplace/Social capability scopes
11. 국내외 금융규제/PG/AML/세무 분류
12. 300 LOC 정책을 Domain Cohesion 정책으로 승격
13. Idea Bank 자체 governance

각 항목은 검토 후 `IDEA → REVIEW → RESEARCHED → ACCEPTED → ADR CANDIDATE → SPEC → IMPLEMENTATION` 경로를 거친다.
