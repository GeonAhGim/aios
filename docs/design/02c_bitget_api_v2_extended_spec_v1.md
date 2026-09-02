# 02c. Bitget API v2 확장 구현 스펙 — v1.0 (02b 제외 항목 전부 편입)

Spec 상위: `02_exchange_adapter_v1.3.md`, `02b_bitget_api_v2_full_spec_v1.md`

## 0. 배경

02b §1.2가 "트레이딩 엔진 도메인 밖"이라는 이유로 제외했던 8개 카테고리
(Broker/Copy Trading/P2P/Earn/Loan/Convert/Subaccount/Tax)를 사용자가
"비트겟 api가 제공하는 모든 기능들은 다 구현해"로 명시 요청 — 02b §1.2가
예정해둔 후속 문서("필요해지면 02c 같은 문서로 추가")가 바로 이 문서다.

추가로, 이번 조사(WebFetch, 2026-09-02)에서 02b 작성 시점에는 몰랐던
카테고리 3개를 새로 발견함: **Grid**(그리드봇), **Strategy**(전략주문),
**Inst Loan**(기관 전용 대출). 이 문서가 함께 편입한다.

**이 문서가 다루지 않는 2개**(제외 사유가 "도메인 밖"이 아니라 "이
문서의 구현 방식 자체가 안 맞음"이라 별도 판단 필요 — 사용자 확인 대기):
- **SBE**(Simplified Binary Encoding): REST/WS 엔드포인트가 아니라 기존
  WebSocket JSON 메시지의 **바이너리 인코딩 대안**(저지연용 전송계층
  옵션)이다. "기능"이 아니라 "기존 기능의 다른 직렬화 방식"이라 이
  문서의 엔드포인트 체크리스트 형식과 맞지 않는다 — market_data_mixin.py
  WS 파싱을 바이너리 겸용으로 만들지 여부는 별도 판단 필요.
- **Reality Stock / Stock+ (Stocks & Options)**: 토큰화된 실물 주식·옵션
  거래 — crypto가 아닌 **완전히 다른 자산군**이다. `AssetClass` enum에
  이미 STOCK/OPTION이 있으나(ADR-2026-08-28 다자산군 확장), Bitget이
  이 자산군의 "거래소" 역할을 하는 건 이번이 처음이라 심볼 체계·정산
  방식이 crypto와 판이하게 다를 가능성이 높다 — 잘못 만든 모델을
  나중에 뒤집는 비용이 커서, 정확한 API 조사 없이 지금 손대지 않는다.

## 1. 신규 포함 카테고리 및 엔드포인트 체크리스트

각 카테고리는 새 mixin 파일 하나(최소모듈 원칙, 기존 3분류에 억지로
끼워넣지 않음) + `BitgetAdapter` 베이스에 추가. 필드명은 전부 커뮤니티
SDK 레퍼런스 기준 최선 추정치 — 라이브 검증 필요(02b와 동일 원칙).

### 1.1 Convert(간편환전) — `convert_mixin.py`

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 견적 조회 | GET | `/api/v2/convert/quoted-price` | 환전 실행 전 필수(가격 확정) |
| 환전 실행 | POST | `/api/v2/convert/trade` | quote 응답의 traceId 필요 |
| 지원 코인쌍 조회 | GET | `/api/v2/convert/currencies` | |
| 환전 이력 조회 | GET | `/api/v2/convert/convert-record` | |

### 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py`

AIOS 자체 멀티테넌시(FD-11/12)와는 별개 개념 — 이건 **Bitget 계정
자체**의 서브계정(예: 전략별로 거래소 계정을 분리하고 싶을 때).

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 서브계정 목록 조회 | GET | `/api/v2/user/virtual-subaccount-list` | |
| 서브계정 생성 | POST | `/api/v2/user/create-virtual-subaccount` | |
| 서브계정 API키 생성 | POST | `/api/v2/user/create-virtual-subaccount-apikey` | |
| 서브계정 API키 목록 | GET | `/api/v2/user/virtual-subaccount-apikey-list` | |
| 서브계정 자산 조회 | GET | `/api/v2/account/sub-account-assets` | |
| 서브계정 간 이체 | POST | `/api/v2/spot/wallet/subaccount-transfer` | 계정 내부 이체(7.9 무관, transfer()와 동일 원칙) |

### 1.3 P2P(개인간 법정화폐 거래) — `p2p_mixin.py`

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 광고(주문) 목록 조회 | GET | `/api/v2/p2p/advList` | 내가 올린 P2P 광고 |
| 상인(merchant) 정보 조회 | GET | `/api/v2/p2p/merchantInfo` | |
| P2P 주문 목록 조회 | GET | `/api/v2/p2p/orderList` | |
| P2P 지원 코인 목록 | GET | `/api/v2/p2p/merchantList` | |

### 1.4 Earn(적금/이자상품) — `earn_mixin.py`

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 적금상품 목록 조회 | GET | `/api/v2/earn/savings/product` | |
| 적금 가입 | POST | `/api/v2/earn/savings/subscribe` | |
| 적금 해지(상환) | POST | `/api/v2/earn/savings/redeem` | 이것도 "출금"이 아니라 상품 해지 — 자산은 계정 내부에 남음(7.9 무관) |
| 보유 적금 조회 | GET | `/api/v2/earn/savings/assets` | |
| 적금 가입/해지 이력 | GET | `/api/v2/earn/savings/records` | |

### 1.5 Loan(코인담보대출) — `loan_mixin.py`

Margin의 "마진 대출"(거래용 신용)과 달리, 담보를 맡기고 다른 코인을
빌리는 별도 상품(거래 목적 아님).

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 대출 가능 코인 조회 | GET | `/api/v2/loan/coin-info` | |
| 담보율/청산가 조회 | GET | `/api/v2/loan/hourly-interest-rate` | |
| 대출 신청 | POST | `/api/v2/loan/borrow` | |
| 상환 | POST | `/api/v2/loan/repay` | |
| 담보 추가/감액 | POST | `/api/v2/loan/revise-pledge` | |
| 진행중인 대출 조회 | GET | `/api/v2/loan/ongoing-orders` | |
| 상환 이력 조회 | GET | `/api/v2/loan/repay-history` | |
| 청산 이력 조회 | GET | `/api/v2/loan/liquidation-records` | |

### 1.6 Tax(세금 신고용 원본 데이터) — `tax_mixin.py`

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 스팟 세금 기록 조회 | GET | `/api/v2/tax/spot-record` | FD-20 보강용, get_account_bills()보다 세무 목적에 특화된 필드 |
| 선물 세금 기록 조회 | GET | `/api/v2/tax/future-record` | |
| 마진 세금 기록 조회 | GET | `/api/v2/tax/margin-record` | |
| P2P 세금 기록 조회 | GET | `/api/v2/tax/p2p-record` | |

### 1.7 Broker(브로커/리셀러) — `broker_mixin.py`

AIOS는 Bitget 리셀러가 아니지만, 사용자가 브로커 계정으로 가입했을
가능성을 배제하지 않는다(요청이 "모든 기능"이므로 API 연동만 우선
제공 — 실제 브로커 자격 없이는 거래소가 오류를 반환할 뿐이며, 그건
호출부가 아니라 계정 상태의 문제).

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 브로커 정보 조회 | GET | `/api/v2/broker/info` | |
| 서브계정(브로커 하위) 목록 | GET | `/api/v2/broker/account/subaccount-list` | Subaccount(1.2)와 별개 — 브로커 전용 API 네임스페이스 |
| 서브계정 생성 | POST | `/api/v2/broker/account/create-subaccount` | |
| 서브계정 API키 생성 | POST | `/api/v2/broker/account/create-subaccount-apikey` | |
| 서브계정 자산 조회 | GET | `/api/v2/broker/account/subaccount-assets` | |
| 서브계정 이체 | POST | `/api/v2/broker/account/subaccount-transfer` | |
| 리베이트(수수료 환급) 조회 | GET | `/api/v2/broker/account/subaccount-deposit` (관례상 리베이트 조회 계열) | |

### 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py`

Bitget 자체 카피트레이딩 마켓플레이스 — 트레이더(팔로우 대상)와
팔로워(따라가는 쪽) 양쪽 역할의 API가 다르다. 실제 소비하는 FD-8
호출부는 없다(AIOS 자체 전략 실행과 무관, 17.9-A) — API 연동만 제공.

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 팔로워 — 팔로우 중인 트레이더 조회 | GET | `/api/v2/copy/mix-follower/query-traders` | |
| 팔로워 — 트레이더 팔로우 설정 | POST | `/api/v2/copy/mix-follower/setting` | |
| 팔로워 — 팔로우 해제 | POST | `/api/v2/copy/mix-follower/close-settings` | |
| 팔로워 — 진행중 카피 주문 조회 | GET | `/api/v2/copy/mix-follower/query-current-orders` | |
| 팔로워 — 카피 주문 이력 | GET | `/api/v2/copy/mix-follower/query-history-orders` | |
| 트레이더 — 내 팔로워 목록 조회 | GET | `/api/v2/copy/mix-trader/config-query-followers` | |
| 트레이더 — 프로필 설정 | POST | `/api/v2/copy/mix-trader/config-settings-base` | |
| 트레이더 — 손익 조회 | GET | `/api/v2/copy/mix-trader/order-profit-history-summary` | |

### 1.9 Grid(그리드봇) — `grid_mixin.py`

거래소가 대신 실행하는 자동 매매 전략(등간격 매수/매도 그리드) — 이건
AIOS의 FD-8 전략 엔진과 개념적으로 경쟁 관계이지만("거래소가 대신
전략을 돈다"), API 연동 자체는 요청 범위이므로 제공한다. 실제로 이
메서드들을 언제 쓸지는 별도 전략(FD-8) 판단.

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 그리드 생성(현물) | POST | `/api/v2/spot/grid/place-grid` | |
| 그리드 생성(선물) | POST | `/api/v2/mix/grid/place-grid` | |
| 그리드 취소 | POST | `/api/v2/spot/grid/close-grid` | |
| 진행중 그리드 조회 | GET | `/api/v2/spot/grid/current-grid` | |
| 그리드 이력 조회 | GET | `/api/v2/spot/grid/grid-history` | |
| 그리드 손익 조회 | GET | `/api/v2/spot/grid/grid-profit` | |

### 1.10 Strategy(전략주문) — `strategy_mixin.py`

Bitget이 서버측에서 관리하는 고급 주문 타입(아이스버그/TWAP 등) —
Spot의 Plan(Trigger) 주문과는 다른 네임스페이스.

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 전략주문 생성 | POST | `/api/v2/trace/strategy/place-order` | |
| 전략주문 취소 | POST | `/api/v2/trace/strategy/cancel-order` | |
| 진행중 전략주문 조회 | GET | `/api/v2/trace/strategy/current-order` | |
| 전략주문 이력 조회 | GET | `/api/v2/trace/strategy/history-order` | |

### 1.11 Inst Loan(기관 전용 대출) — `inst_loan_mixin.py`

기관 계정 전용(리테일 API 키로는 대부분 권한 오류가 날 가능성이 높음
— 그래도 API 연동 자체는 제공, 8.3 원칙: 권한 없음도 정상적인 응답
케이스로 처리).

| 함수 목적 | Method | Path | 비고 |
|---|---|---|---|
| 대출 상품 조회 | GET | `/api/v2/ins-loan/product-infos` | |
| 담보 코인 조회 | GET | `/api/v2/ins-loan/ensure-coins-convert` | |
| LTV(대출한도) 조회 | GET | `/api/v2/ins-loan/loan-order` | |
| 상환 | POST | `/api/v2/ins-loan/repaid-history` (조회) / 상환 실행 엔드포인트는 문서 미확인 — 조회만 우선 제공 | |

## 2. 작업 분해(리프 단위)

02b §9와 동일 원칙(최소모듈, MockTransport 테스트, ruff/mypy, 커밋+즉시
push) — 카테고리당 리프 1개, 아래 순서(트레이딩 엔진과의 개념적
근접도 순):

1. Convert (가장 단순, 4개 엔드포인트)
2. Subaccount
3. Tax
4. Earn
5. Loan
6. Grid
7. Strategy
8. P2P
9. Broker
10. Copy Trading
11. Inst Loan

## 3. 완료조건

- 위 11개 카테고리 전부 `BitgetAdapter`에 mixin으로 존재, MockTransport
  테스트로 요청 파라미터·응답 파싱 검증됨.
- SBE/Reality Stock·Stock+는 §0에서 설명한 사유로 이 문서 범위 밖 —
  별도 확인 후 별도 문서로 처리(침묵 배제 아님, 이 문서에 명시).
- 7.9 원칙(출금 실행 금지)은 모든 신규 카테고리에도 동일 적용 — Earn
  해지/Loan 상환/Subaccount 이체는 전부 "계정 내부 이동"이라 저촉 없음.

## 참고 문헌
- Bitget 공식 API 문서: https://www.bitget.com/api-doc/
- 커뮤니티 SDK 엔드포인트 레퍼런스: https://github.com/tiagosiebler/bitget-api
