# 05. 구독결제·AIOS Fee·Creator 정산·AML

Status: IDEA / LEGAL-REVIEW-REQUIRED  
Date: 2026-09-10

주의: 아래는 제품/아키텍처 아이디어다. 실제 한국 및 해외 상용화 전 PG, 전자금융, AML, 투자자문/일임, 세무, 회계 규제 검토가 필요하다.

## 기본 원칙

Marketplace가 성장하면 전략 구독료를 결제하고 Creator에게 정산해야 한다.

이때 AIOS가 사용자 돈을 자유롭게 보관하고 사용자 간 이동을 허용하면 불필요하게 자금이전/정산/AML 위험이 커질 수 있다.

권장 방향:

> AIOS는 자금을 보관·송금하는 자유로운 wallet이 아니라, 검증된 외부 PSP/PG가 실제 결제와 정산 rail을 담당하고 AIOS는 entitlement, fee calculation, immutable settlement ledger, risk/hold policy를 관리한다.

## AIOS Fee를 받는 방식

판매자가 돈을 받은 뒤 AIOS에 수수료를 송금하는 구조는 사용하지 않는다.

사용자 결제 시점에 AIOS fee와 Creator payable을 계산한다.

예:

```text
Strategy subscription gross      100,000원

AIOS platform fee                  20,000원
Creator payable                    80,000원
```

PG 비용을 누가 부담하는지는 별도 pricing policy로 정한다.

예:

### 모델 A

```text
Gross                    100,000
AIOS fee                  20,000
PG cost                    3,000
Creator payable           77,000
```

### 모델 B

AIOS가 PG cost를 platform fee 안에서 부담:

```text
Gross                    100,000
Creator share             80,000
AIOS gross fee            20,000
  └ PG cost                3,000
AIOS net                  17,000
```

사용자 UX는 단순한 모델 B가 이해하기 쉽다.

## AIOS 자체 SaaS 요금과 Marketplace Fee 분리

두 수익원을 장부에서 구분한다.

예:

```text
AIOS Pro monthly          99,000
Strategy subscription     49,000
Total user payment       148,000
```

내부:

```text
AIOS SaaS revenue         99,000

Marketplace gross         49,000
  AIOS commission          9,800
  Creator payable         39,200
```

상품 UI에서 한 번 결제되더라도 내부 ledger는 귀속을 분리한다.

## Split Settlement

가능한 경우 외부 PSP/Marketplace payment provider가 결제금액을 platform fee와 Creator balance로 나누는 방식을 선호한다.

개념:

```text
Customer
   │ 100,000
   ▼
External PSP
   ├──────── AIOS platform fee
   └──────── Creator pending balance
```

Creator 귀속액이 AIOS 운영계좌에 들어왔다가 다시 나가는 구조를 최소화한다.

## Settlement Ledger

AIOS는 실제 돈을 임의 이동하는 대신 다음 상태를 기록한다.

```text
Subscription
Invoice
Payment
PlatformFee
ProcessorFee
Tax
CreatorPayable
Reserve
Refund
Chargeback
Payout
SettlementAdjustment
```

회계적으로 Creator 귀속액이 AIOS 매출인지 payable인지 여부는 계약구조에 따라 전문가 검토가 필요하지만, 시스템은 처음부터 separate account concepts를 가져야 한다.

## Creator Payout

결제 즉시 출금하지 않는다.

예:

```text
Payment
→ Creator Payable: Pending
→ fraud/refund/AML window
→ Available
→ Payout
```

신규 Creator와 고위험 Creator는 더 긴 hold/reserve를 적용할 수 있다.

예:

```text
Established creator   T+14
New creator           T+30
Elevated risk         longer hold / rolling reserve
```

구체 숫자는 정책·국가·PSP에 따라 결정한다.

## Wallet을 만들지 않는 방향

초기 Marketplace에서 다음은 금지하는 것이 안전하다.

```text
User-to-user transfer
Creator-to-creator transfer
Transferable marketplace balance
Cash-equivalent points
Creator earnings used directly to buy other strategies
Third-party payout account
Crypto payout
Crypto settlement
```

Creator revenue는 본인확인된 payout rail로만 나간다.

## KYC/KYB

유료 Seller onboarding은 무료 게시보다 강해야 한다.

개인 후보:

- real name
- residence
- identity verification
- contact
- tax information
- verified bank account
- sanctions/PEP screening if required by compliance program

법인 후보:

- legal entity
- registration
- representative
- verified corporate account
- beneficial owner / UBO
- tax information
- jurisdiction
- business purpose

## Same-Name Payout

기본 원칙:

```text
Creator legal identity
==
Payout account owner
```

법인이라면 법인 판매수익은 검증된 법인계좌로 지급.

계좌 변경은 재검증 이벤트로 취급한다.

## Refund

환불은 원결제수단으로 돌아가는 것을 기본으로 한다.

```text
Card A → payment → refund → Card A
```

다른 계좌로 임의 refund 하지 않는다.

## AML / Fraud 악용 시나리오

예:

1. Seller A가 자신의 전략을 비정상적으로 비싼 가격에 등록
2. 경제적으로 연관된 Buyer B가 반복 구매
3. 전략 거래 매출처럼 보이게 함
4. Seller payout으로 자금 회수

따라서 Marketplace Financial Crime Risk Engine이 필요하다.

## 탐지 후보

- buyer/seller same identity
- same bank account
- same payment instrument
- same device
- same IP / network
- repeated reciprocal purchase
- high price outside normal band
- purchase → refund patterns
- sudden sales concentration
- new creator with anomalous volume
- country mismatch
- identity/payout mismatch
- many buyers controlled by common device
- concentrated revenue from one buyer
- structured transactions
- repeated account creation
- review manipulation tied to payment graph

## Risk Action

```text
LOW
→ normal

MEDIUM
→ payout delay / additional verification

HIGH
→ EDD / hold / review

CRITICAL
→ payout freeze / case investigation / provider escalation
```

법적으로 필요한 경우 의심거래 대응은 PSP/규제의무 주체와 역할분담을 명확히 해야 한다.

## 가격통제

초기에는 완전 자유가격보다 guardrail을 둘 수 있다.

예:

- normal price bands
- high-price manual review
- abnormal deviation detection
- creator tier-based limit

목표는 가격통제가 아니라 고액 거래를 통한 자금이전 abuse를 줄이는 것이다.

## 성과보수

초기 Marketplace 수익모델에서는:

```text
월 고정 구독료
```

가

```text
수익의 20%
```

같은 performance fee보다 단순하고 규제/회계/분쟁 관리가 쉽다.

Managed strategy / discretionary management / performance fee는 별도 법률·상품영역으로 분리 검토한다.

## 권장 Financial Invariants

### MKT-FIN-01

AIOS는 Marketplace balance를 자유 송금 가능한 사용자 wallet으로 제공하지 않는다.

### MKT-FIN-02

Creator payout은 검증된 동일명의 payout destination으로만 가능하다.

### MKT-FIN-03

Creator payable은 다른 사용자에게 양도할 수 없다.

### MKT-FIN-04

Refund는 가능한 한 원결제수단으로만 반환한다.

### MKT-FIN-05

Fraud/AML Hold는 Payout Schedule보다 우선한다.

### MKT-FIN-06

모든 Payment → Fee → Payable → Refund → Chargeback → Payout은 immutable ledger로 연결되어야 한다.

### MKT-FIN-07

경제적으로 동일한 Buyer/Seller의 self-dealing 의심 거래는 정상 reputation과 settlement에서 제외 또는 hold한다.

### MKT-FIN-08

Marketplace fee는 server-calculated이며 Seller/Agent가 임의 수정할 수 없다.

### MKT-FIN-09

```text
Creator payout <= verified available Creator payable
```

을 강제한다.

### MKT-FIN-10

정산이나 수수료 계산은 Marketplace strategy execution 권한과 분리된 bounded context에서 관리한다.

## Product/Legal Classification 숙제

다음 상품을 같은 규제범주로 취급하지 않는다.

```text
Indicator
Strategy
Signal
Portfolio
Automation
Managed Strategy
```

특히:

- 단순 소프트웨어/지표 판매
- 실시간 매매 signal
- 자동주문
- 포트폴리오 allocation
- discretionary managed strategy
- performance fee

는 국가별 규제 검토가 달라질 수 있다.
