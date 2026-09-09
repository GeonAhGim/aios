# 04. Marketplace 신뢰·검증·버전·평판

Status: IDEA / REVIEW  
Date: 2026-09-10

## 핵심 문제

전략 Marketplace에서 사용자가 가장 불신하는 것은 다음이다.

- 저 수익률이 실제인가
- 판매자가 유리한 기간만 골랐나
- 과최적화인가
- 슬리피지와 수수료를 제대로 반영했나
- 내 계좌에서도 같은 결과가 나나
- 판매자가 전략을 바꾸면 과거 실적과 동일한 전략인가
- 실전 기록인가, PAPER인가, backtest인가
- 판매자가 잘못된 리뷰나 self-purchase로 평판을 만들 수 있나

AIOS의 강점은 이 문제를 `독립 재현 + 불변 버전 + 실제 사용 기반 평판 + 사용자 계좌별 검증`으로 해결할 수 있다는 것이다.

## 검증 레벨을 분리한다

한 개의 `Verified` 배지만 두면 의미가 희석된다.

예:

```text
SOURCE VERIFIED
COMPILE VERIFIED
BACKTEST REPRODUCED
WALK-FORWARD VERIFIED
PAPER VERIFIED
LIVE VERIFIED
EXECUTION QUALITY VERIFIED
```

사용자는 어떤 수준이 검증되었는지 알아야 한다.

## Seller Claimed와 AIOS Verified 분리

전략 상세 화면:

```text
Seller Claimed
--------------
CAGR 34.2%
MDD -7.1%

AIOS Reproduced
---------------
CAGR 26.8%
MDD -11.4%

My Account Simulation
---------------------
CAGR 22.6%
MDD -12.0%
```

세 결과를 하나로 섞지 않는다.

## Immutable Version

사용자가 `Momentum Alpha v1.7`을 검증하고 구독했다면 판매자가 v2.0을 올려도 자동교체하면 안 된다.

예:

```text
현재 운용: v1.7
Artifact hash: a93f...

새 버전: v2.0

변경:
- RSI 조건 변경
- Stop Loss 5% → 7%
- Position sizing 변경

AIOS 재검증:
MDD -8.4% → -12.7%

[현재 버전 유지]
[새 버전 PAPER]
[업데이트]
```

전략 버전은 immutable artifact로 취급한다.

기존 실적은 기존 버전에 귀속된다.

새 버전이 과거 버전의 track record를 자동상속하면 안 된다.

## Change Log

Creator는 버전 변경 시 다음을 구조화해서 제출한다.

- signal logic change
- risk parameter change
- execution behavior change
- supported instrument change
- data dependency change
- expected regime change

AIOS는 diff와 재검증 결과를 사용자에게 보여준다.

## Reputation

별점 하나로 평가하지 않는다.

후보:

```text
AIOS TRUST SCORE

Reproducibility
Track Record
Risk Stability
Out-of-Sample
Execution Quality
Creator Reliability
Performance Stability
Dispute Rate
Version Stability
Subscriber Retention
```

수익률이 높다고 자동으로 상단에 노출되지 않아야 한다.

## Ranking에서 반영할 변수

- verified track record duration
- MDD
- tail loss
- drawdown recovery
- DSR
- PBO
- out-of-sample
- walk-forward
- turnover
- slippage sensitivity
- fee sensitivity
- live/PAPER divergence
- independent reproduction
- number of unique verified users
- creator account age
- dispute rate
- refund rate
- abnormal review patterns
- strategy update frequency
- portfolio correlation

## Verified Usage 기반 평판

단순 구매수나 리뷰수가 아니라 실제 AIOS entitlement와 execution/experiment record가 있는 사용자의 평가에 더 높은 신뢰도를 준다.

예:

```text
Unverified review weight       low
Subscribed user                medium
PAPER user                     higher
LIVE verified user             highest
```

단, 투자손익이 리뷰권한을 결정하는 구조는 피하고 verified relationship만 증명한다.

## 표절/복제

Protected source라도 유사전략 복제 문제가 생길 수 있다.

검토 후보:

- AST/IR structural similarity
- parameter-insensitive similarity
- signal correlation
- trade sequence similarity
- source hash
- lineage
- creator provenance

다만 전략 아이디어 자체의 일반적 유사성까지 독점권으로 오판하면 안 된다.

## 성과 조작 방지

다음은 별도 구분되어야 한다.

```text
Backtest
Paper
Live
Imported external performance
```

Imported external performance는 AIOS에서 직접 검증한 실적과 동일한 배지를 쓰지 않는다.

## Social Reputation과 Marketplace Reputation의 연결

팔로워 수가 판매자 신뢰도를 결정하면 안 된다.

인기 Creator와 좋은 전략 Creator가 반드시 같지 않다.

Social graph는 discovery에 사용하되 financial trust score는 독립 검증 데이터 중심으로 계산한다.

## 사용자에게 중요한 질문

전략 페이지가 답해야 할 질문:

1. 누가 만들었나?
2. 어떤 버전인가?
3. 언제 만들어졌나?
4. AIOS가 독립적으로 재현했나?
5. backtest/PAPER/LIVE 중 무엇인가?
6. 얼마나 오래 검증되었나?
7. 가장 큰 손실은 무엇이었나?
8. 어떤 시장에서 실패하나?
9. 수수료/슬리피지에 민감한가?
10. 내 자산/계좌에서 어떤가?
11. 판매자가 최근 무엇을 바꿨나?
12. 지금 성과가 과거 기대범위에서 벗어나고 있나?
