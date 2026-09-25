# 07. 후속 숙제 / 아직 확정하지 않은 문제

Status: OPEN  
Date: 2026-09-10

이 문서는 이번 대화에서 중요성이 드러났지만 즉시 AIOS 구현에 집어넣지 않고 별도 설계 과제로 남겨야 할 항목을 기록한다.

## 1. Marketplace Product Constitution

가장 먼저 깊게 검토할 문제:

> AIOS Marketplace에서는 정확히 무엇을 사고팔 수 있고, 각 상품을 산 사용자는 어디까지 할 수 있는가?

초기 Matrix 후보:

| 상품 | 판매 | 소스보호 | 백테스트 | PAPER | LIVE | 규제/운영 위험 |
|---|---|---:|---:|---:|---:|---|
| Indicator | 가능 | 가능 | 선택/검증 | - | - | 낮음 |
| Strategy | 가능 | 가능 | 필수 | 가능 | 조건부 | 중 |
| Signal | 조건부 | 가능 | 검증 | 가능 | 조건부 | 높음 |
| Portfolio | 가능 | 가능 | 필수 | 가능 | 조건부 | 높음 |
| Automation | 제한 | 가능 | 필수 | 가능 | 강한 Gate | 매우 높음 |
| Managed Strategy | 별도 영역 | 가능 | 필수 | 가능 | 법률검토 필수 | 최고 |

각 상품마다 확정할 것:

- contract
- licensing
- creator eligibility
- source visibility
- verification
- execution authority
- price
- refund
- settlement
- tax
- dispute
- risk disclosure
- jurisdiction
- LIVE eligibility

## 2. Marketplace Financial Constitution

확정 필요:

- 외부 PG/PSP 선정 기준
- split settlement 지원
- marketplace seller onboarding
- KYC/KYB 역할
- AML 역할분담
- settlement cycle
- reserve
- chargeback
- refund
- tax invoice
- VAT
- overseas seller
- FX
- sanctions
- payout rail
- platform fee accounting
- creator payable accounting

## 3. 한국 규제 분류

법률 전문가 검토 필요:

- PG 해당성
- 전자금융업
- 투자자문
- 투자일임
- 투자중개 오인 가능성
- 자동매매 software와 금융투자업 경계
- strategy subscription
- signal subscription
- portfolio product
- performance fee
- financial advertising
- creator sponsorship disclosure

## 4. Buyer Trust Constitution

확정할 것:

- verification levels
- AIOS Trust Score
- ranking formula
- DSR/PBO policy
- out-of-sample
- PAPER minimum
- LIVE minimum
- imported performance
- version rollover
- verified review
- manipulation resistance
- delisting
- failed strategy lifecycle

## 5. Creator Economy Constitution

확정할 것:

- creator tiers
- free vs paid
- creator onboarding
- seller qualification
- pricing limits
- platform take-rate
- payout cycle
- protected source
- copyright/IP
- plagiarism
- dispute
- creator analytics
- creator storefront
- referral/affiliate
- sponsored strategy rules
- institutional creator

## 6. Social & Creator Economy Constitution

설계 범위:

- Creator Profile
- Follow Graph
- Financial Feed
- Symbol Community
- Posts
- Ideas
- Chart Snapshot
- Live Broadcast
- Paid Community
- Reputation
- Verified Performance
- Sponsorship Disclosure
- Conflict Disclosure
- Moderation
- Anti-Manipulation
- Bot detection
- Marketplace linking
- BYOAI access

## 7. Interactive Financial Broadcast

일반 live streaming이 아닌 금융 action과 결합.

검토:

- low-latency streaming
- chart sync
- strategy reference
- instrument tagging
- synchronized replay
- audience backtest
- clone drawing
- inspect strategy
- subscription conversion
- paid room
- moderation
- recording
- timestamp evidence

## 8. Financial Knowledge Graph

Social/Marketplace/Market/Execution 객체 연결 모델을 검토한다.

예:

```text
Creator
→ Post
→ Instrument
→ Strategy
→ Experiment
→ Marketplace Listing
→ Subscriber
→ PAPER Run
→ LIVE Run
→ Performance
→ Reputation
```

이 데이터는 검색/추천/BYOAI context의 핵심이 될 수 있다.

개인정보와 tenant 데이터가 public graph로 유출되지 않도록 data classification이 필요하다.

## 9. Recommendation Engine

`인기순`을 넘어:

- portfolio correlation
- mandate fit
- capital fit
- broker compatibility
- tax/country
- risk tolerance
- creator trust
- strategy drift
- verified duration

기반의 personalized marketplace ranking을 검토한다.

## 10. Portfolio Marketplace

단일 전략 외에 여러 전략을 결합한 portfolio 상품.

문제:

- allocation ownership
- multiple creator fee split
- correlated drawdown
- strategy replacement
- creator exit
- rebalance
- portfolio versioning
- nested entitlement
- aggregate mandate
- fee stacking

## 11. BYOAI Marketplace/Social Capabilities

외부 Agent에게 노출 가능한 새 scope를 설계한다.

후보:

```text
marketplace.read
marketplace.search
marketplace.compare
marketplace.simulate
social.read
social.search
creator.follow
```

구매/구독/PAPER/LIVE 실행은 별도 confirmation이 필요하다.

AI가 사용자 모르게 paid subscription을 시작하면 안 된다.

## 12. 개발정책 Constitution 승격

300 LOC hard cap 폐지 또는 완화와 Domain Cohesion 정책을 AIOS Implementation Constitution에 반영할지 검토한다.

확정 후보 문구:

> 소스 파일 길이는 architecture constraint가 아니라 관찰 지표다. 코드는 bounded context, aggregate, capability, invariant ownership을 기준으로 응집하며, 분할은 독립된 책임과 변경축이 존재할 때 수행한다.

함께 정의할 것:

- LOC warning threshold
- complexity gate
- dependency gate
- domain ownership
- helper/common prohibition
- safety-critical invariant locality
- agent refactoring policy

## 13. Idea Bank Governance

이 저장소 자체 운영정책도 필요하다.

후보 workflow:

```text
IDEA
→ REVIEW
→ RESEARCHED
→ ACCEPTED
→ ADR CANDIDATE
→ SPEC
→ IMPLEMENTATION
```

Idea Bank 문서를 AIOS 본 저장소가 런타임 또는 CI에서 직접 의존하지 않는다.

AIOS에 반영하려면 반드시 정식 ADR/Spec을 거친다.
