# 03. Marketplace 제품 개념과 사용자 여정

Status: IDEA / REVIEW  
Date: 2026-09-10

## Marketplace를 단순 전략 판매몰로 만들지 않는다

AIOS Marketplace의 핵심은 디지털 상품을 나열하고 구매시키는 것이 아니다.

핵심 경험:

> 남이 만든 투자 아이디어를 발견하고 → AIOS가 독립 검증하고 → 내 계좌 조건으로 다시 검증하고 → PAPER에서 경험하고 → 내가 허용한 Risk/Mandate 범위에서 운용한다.

즉 Marketplace는 `전략 유통 + 검증 + 개인 적합성 분석 + 운용 배포` 레이어다.

## 사용자 홈

첫 화면을 단순 `수익률 높은 순`으로 만들면 안 된다.

과도한 백테스트 수익률은 사용자 행동을 왜곡하고 고위험/과최적화 전략을 상단으로 밀어낼 수 있다.

사용자에게 더 중요한 것은:

- 내 자본에서 실행 가능한가
- 내 broker에서 가능한가
- 내 포트폴리오와 상관관계가 어떤가
- 내 risk mandate에 맞는가
- 독립 재현되는가
- PAPER/LIVE 실적이 있는가
- 현재 성과가 과거 백테스트에서 이탈하고 있는가

예시:

```text
FOR YOU

현재 포트폴리오와 상관관계가 낮은 전략

Korea Momentum Alpha      VERIFIED

AIOS 검증기간       2.8년
실전/PAPER          241일
연환산 수익률       +21.4%
MDD                 -8.7%
Sharpe               1.72
내 계좌 적합도       91/100
현재 포트와 상관     0.18

월 ₩39,000

[내 계좌로 검증] [PAPER 체험]
```

핵심 CTA는 `구매`보다 `내 계좌로 검증`이 되어야 한다.

## 내 계좌로 검증

판매자의 backtest는 판매자의 조건일 뿐이다.

사용자가 5천만원, KIS broker, 특정 수수료와 mandate를 가진다면 AIOS가 해당 조건으로 전략을 재실행한다.

예:

```text
판매자 기준
CAGR       27.8%
MDD        -9.4%
Sharpe      1.93

내 계좌 기준
CAGR       21.1%
MDD       -11.7%

차이 원인
- 주문규모
- 수수료
- 슬리피지
- 일부 종목 거래불가
- 사용자 mandate에 의한 position cap
```

이것이 Marketplace의 핵심 차별점 후보다.

## 전체 Buyer Journey

### 1. Discover

사용자는 다음으로 탐색할 수 있다.

- 시장: 한국/미국/Crypto
- asset class
- timeframe
- momentum/value/mean reversion/hedge
- MDD
- Sharpe
- minimum capital
- turnover
- frequency
- broker compatibility
- tax jurisdiction
- strategy age
- PAPER days
- LIVE days
- independent verification level
- portfolio correlation

AI 추천도 인기순이 아니라 사용자 계좌와 포트폴리오 context를 고려한다.

### 2. Trust

판매자 주장과 AIOS가 검증한 결과를 섞지 않는다.

페이지에서 명확히 구분:

```text
SELLER CLAIMED
AIOS REPRODUCED
PAPER VERIFIED
LIVE VERIFIED
```

Backtest와 actual performance도 같은 그래프에서 오해되도록 합치지 않는다.

### 3. Fit to Me

사용자의:

- capital
- broker
- fee
- slippage
- instruments
- holding restrictions
- mandate
- max position
- max daily loss
- max MDD
- leverage
- existing portfolio

를 기반으로 개인화 검증한다.

Marketplace의 목표는 `좋은 전략`을 랭킹하는 것이 아니라 `이 사용자에게 적합한 전략`을 찾는 것이다.

### 4. Trial

구매 후 바로 LIVE가 아니라 PAPER trial을 권장한다.

예:

```text
7일
30일
90일
```

그리고 다음을 보여준다.

```text
Backtest expected
vs
AIOS independent reproduction
vs
My PAPER result
```

### 5. Subscribe & Deploy

구독과 execution authority를 분리한다.

구독했다고 자동 LIVE 활성화되지 않는다.

사용자가 별도로:

- max capital
- position limit
- daily loss
- MDD
- leverage
- market/time restrictions

을 승인해야 한다.

AIOS Master Risk Authority는 Marketplace 상품 위에 존재한다.

### 6. Monitor

구독 이후:

- last 30 days
- backtest drift
- PAPER-to-LIVE divergence
- slippage
- rejection events
- risk events
- current strategy version
- creator change history

를 지속적으로 본다.

### 7. Exit

구독 해지와 포지션 종료를 분리해야 한다.

사용자는 사전에 종료정책을 선택할 수 있다.

```text
신규 주문만 중단하고 기존 포지션 유지
전략의 정상 exit rule을 따라 청산
즉시 포지션 종료
```

## Seller / Creator Journey

Creator는 다른 SaaS를 여러 개 결합하지 않고 AIOS에서 다음을 수행할 수 있다.

```text
내 AI Agent 연결
→ 아이디어 연구
→ 전략 생성
→ compile
→ backtest
→ independent verification
→ PAPER qualification
→ protected listing
→ subscription
→ settlement
→ update/version management
```

예:

```text
Compiler              PASS
Lookahead              PASS
Resource Limit         PASS
Independent Backtest   PASS
Walk-forward           PASS
DSR/PBO                PASS
Plagiarism             PASS
PAPER 90 days          PASS

Marketplace Eligible
```

## Protected Source

Creator가 좋은 전략을 올리지 않는 가장 큰 이유 중 하나는 복제 위험이다.

Visibility 후보:

```text
Open
Protected
Invite-only
Private
```

Protected 전략은 source를 사용자에게 전달하지 않고 server-side controlled execution을 사용한다.

사용자는 전략 결과와 검증 근거는 볼 수 있지만 proprietary implementation을 그대로 가져갈 수 없어야 한다.

## 상품 종류를 한 종류로 취급하지 않는다

향후 Marketplace Product Constitution에서 아래 상품을 분리해야 한다.

```text
Indicator
Strategy
Signal
Portfolio
Automation
Managed Strategy
```

각각 다음이 달라진다.

- 규제위험
- 검증방법
- 실행권한
- source visibility
- 판매자 자격
- 가격정책
- refund
- LIVE 가능 여부
- risk disclosure

## Portfolio Marketplace

장기적으로 단일 전략 판매보다 포트폴리오 조합이 더 큰 사용자 가치가 될 수 있다.

예:

```text
AIOS Balanced Alpha Portfolio

Korea Momentum       25%
US Quality           25%
Crypto Trend         15%
Market Neutral       20%
Cash/Hedge           15%

AIOS Verified MDD    -7.8%
Strategies           4
Creators             3

[내 5천만원으로 시뮬레이션]
```

AIOS가 이미 Portfolio / Allocation / Risk / Execution / Ledger를 가진다는 전제에서 여러 Creator의 전략을 risk-aware portfolio로 조합할 수 있다.

## BYOAI + Marketplace

Marketplace가 커질수록 사람의 manual search만으로는 한계가 있다.

사용자는 자기 AI에게 말할 수 있다.

> 내 현재 포트폴리오와 상관계수가 0.3 이하이고, 최근 2년 독립검증 MDD가 10% 이하이며, 최소 180일 PAPER나 LIVE 이력이 있는 국내주식 전략 5개를 찾아줘.

AI Agent가 Marketplace search capability를 사용한다.

그다음:

> 2번과 4번을 내 계좌 조건으로 backtest해.

> 4번을 천만원으로 30일 PAPER 테스트해.

이런 흐름으로 연결한다.

즉 AIOS는 자체 AI 토큰을 제공하지 않아도 AI-native marketplace가 될 수 있다.
