# 02b. Bitget API v2 전체 구현 스펙 — v1.0

> **v1.0(2026-09-02) 신설.** 사용자 요청 — "비트겟은 공개된 API 문서를
> 기반으로 모든 기능을 다 활용할 수 있도록 100% 구현해놔야 한다." 02번
> 문서(`02_exchange_adapter_v1.3.md`)는 `ExchangeAdapter` 공통 인터페이스와
> BitgetAdapter의 "지금 구현된 범위"만 다룬다 — 이 문서는 그 구현 범위를
> Bitget이 실제로 제공하는 전체 API 표면까지 넓히기 위한 상세 작업
> 스펙이다. 02번 문서의 인터페이스 계약을 바꾸지 않는다(ExchangeAdapter
> ABC는 그대로, 이 문서가 다루는 확장은 BitgetAdapter 내부 구현 + 필요 시
> 신규 데이터 모델 추가).

## 0. 배경 — 기존 조사 결과 대조

세션 중 사용자가 예전에 작성해둔 구현체(`Desktop\quant\bitget_ex`)를
발견해 대조를 요청했다. 결과:

| 항목 | `bitget_ex`(작년) | 현재 `mihwa-aios` | 이 문서의 판단 |
|---|---|---|---|
| API 버전 | **V1** (`/api/spot/v1/...`, `/api/mix/v1/...`) | **V2** (`/api/v2/spot/...`, `/api/v2/mix/...`) | V2로 통일 진행(V1은 Bitget이 구버전 취급) |
| 구조 | client/service/router 3계층, Spot 8모듈+Margin(cross/isolated)+Futures 10모듈 | 단일 mixin 파일, 극히 일부 엔드포인트만 | **엔드포인트 커버리지 체크리스트로 재활용**(코드 자체는 재사용 안 함 — 버전이 달라 그대로 포팅하면 오히려 퇴보) |
| 인증 방식 | 직접 HTTP + 자체 HMAC 서명 모듈(ccxt 미사용) | 직접 HTTP + 자체 HMAC 서명(`_BitgetHTTPClient`) | **방식 동일** — 서명 로직 자체는 이미 검증된 현재 코드를 그대로 확장 |

`bitget_ex`의 `FUTURES_V1_API_METHODS.md` / `bitget_v1_spot_endpoints.md` /
`bitget_margin_v1_api_list.md` 3개 문서가 "Bitget에 어떤 기능이 있는지"를
빠짐없이 정리해뒀다는 점에서 가치가 크다 — 이 문서의 엔드포인트 목록은
그 체크리스트와, Bitget V2 공식 문서 및 신뢰할 수 있는 커뮤니티 SDK
([tiagosiebler/bitget-api](https://github.com/tiagosiebler/bitget-api),
node-binance-api 계열 관리자로 여러 거래소 SDK를 유지보수하는 신뢰도 높은
소스) 양쪽을 대조해 확정했다.

## 1. 범위 결정 — "100%"의 경계

Bitget V2 API는 500개 이상의 엔드포인트를 갖는데, 그중 상당수는 **거래
플랫폼 도메인 밖**이다(예: Bitget의 브로커/리셀러가 되는 API, Bitget
자체 카피트레이딩 마켓플레이스, P2P 개인간 거래, 예적금형 Earn 상품,
코인담보대출). AIOS는 "자동매매 판단·실행 엔진"이지 이런 상품들을 다시
파는 플랫폼이 아니므로, 아래와 같이 범위를 나눈다 — **제외 항목은
사용자가 이 문서를 검토하며 되살릴 수 있도록 표로 남긴다(침묵 삭제
아님).**

### 1.1 포함 (이 문서가 다루는 100% 구현 대상)

- **Spot**(현물): Market(공개 시세) / Trade(주문) / Account(잔고·입출금)
- **Margin**(마진, cross+isolated 공통 파라미터화): 계좌·대출·상환·주문
- **Futures/Mix**(무기한 선물): Market / Account / Position / Order(Trade)
- **Public/Common**: 공지사항, 서버시간, 수수료율
- **WebSocket**: Public(시세) + Private(계좌/포지션/주문/체결)

### 1.2 제외 (별도 요청 시 별도 문서로 추가 — 이 leaf 범위 아님)

| 카테고리 | 엔드포인트 수(추정) | 제외 사유 |
|---|---|---|
| Broker(브로커/리셀러) | 30 | Bitget의 화이트라벨 리셀러가 되는 API — AIOS는 Bitget의 리셀러가 아님 |
| Copy Trading(카피트레이딩) | 49 | Bitget 자체 카피트레이딩 마켓플레이스 연동 — AIOS 자체 전략 실행(FD-8)과 무관 |
| P2P | 4 | 개인간 법정화폐 거래 — 트레이딩 엔진 도메인 밖 |
| Earn(적금/샤크핀) | 25 | 예치형 이자상품 — 트레이딩이 아닌 자산운용 상품 |
| Loan(코인담보대출) | 17 | 담보대출 상품 — 트레이딩이 아닌 신용상품 |
| Convert(간편환전) | 7 | 지정가/시장가 주문 경로가 아닌 스왑형 환전 — Phase 1 스콥 밖 |
| Subaccount 관리 | 8 | Bitget 계정 자체의 서브계정 관리 — AIOS는 이미 자체 멀티테넌시(FD-11/12)가 있어 중복 |
| Tax/거래기록 | 4 | 세금 신고용 원본 데이터 — FD-20(운용보고서)에서 필요해지면 별도 추가 |

이 표의 항목이 필요해지면 "02c_bitget_api_v2_extended_spec.md" 같은
후속 문서로 별도 추가한다(이 문서의 완료조건을 흐리지 않기 위해).

## 2. 공통 사항

- **Base URL**: `https://api.bitget.com` (기존과 동일, 변경 없음)
- **인증**: 기존 `_BitgetHTTPClient._headers()`(HMAC-SHA256, ACCESS-KEY/
  ACCESS-SIGN/ACCESS-TIMESTAMP/ACCESS-PASSPHRASE) 그대로 재사용 — 신규
  엔드포인트도 동일 서명 방식(V2 전체가 이 방식 하나로 통일돼 있음).
- **Demo 모드**: 기존 `demo_mode` → `paptrading: "1"` 헤더 그대로 재사용.
  단, Margin/Futures가 Bitget Demo Trading에서 실제로 지원되는지는 실키
  확보 후 라이브 확인 필요(2026-08-29 세션 기준 Spot만 확인됨) — 지원
  안 되면 그 범위는 PAPER 모드에서 스킵하고 문서화.
- **원장 원칙(11번 §11.3)**: 새 엔드포인트가 반환하는 오류는 기존
  `RetryableExchangeError`/`FatalExchangeError` 분류를 그대로 따른다 —
  `_FATAL_ERROR_CODES` 목록은 실제 오류 대면 시 계속 확장.
- **모델 재사용 원칙**: 기존 내부 모델(`Ticker`/`Candle`/`OrderBook`/
  `Order`/`Position`/`AccountBalance`)로 표현 가능한 응답은 그걸 그대로
  쓴다. 표현 불가능한 것만(레버리지 설정, 펀딩레이트, 마진 계좌 상태 등)
  §5에서 신규 모델을 정의한다 — 불필요한 모델 증식 방지(17.9-A).

## 3. Spot(현물) 엔드포인트 체크리스트

### 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장

| 함수 목적 | Method | Path | 우선순위 | 내부 모델 |
|---|---|---|---|---|
| 심볼 정보 | GET | `/api/v2/spot/public/symbols` | P1 | 신규(`SpotSymbolInfo`, tick_size/lot_size 등 — Validator가 필요로 하는 값) |
| 코인 정보 | GET | `/api/v2/spot/public/coins` | P2 | 신규(단순 dict로 충분, 별도 모델 불필요) |
| 현재가(전체/단일) | GET | `/api/v2/spot/market/tickers` | **P0(기존 구현됨)** | `Ticker` |
| 호가창 | GET | `/api/v2/spot/market/orderbook` | **P0(기존 구현됨)** | `OrderBook` |
| 병합 호가창 | GET | `/api/v2/spot/market/merge-depth` | P2 | `OrderBook`(depth 파라미터 확장) |
| 캔들 | GET | `/api/v2/spot/market/candles` | **P0(기존 구현됨)** | `Candle` |
| 과거 캔들 | GET | `/api/v2/spot/market/history-candles` | P1 | `Candle`(FD-2.3 백테스트 데이터 확장용) |
| 최근 체결 | GET | `/api/v2/spot/market/fills` | P1 | 신규(`PublicTrade`, 시장 전체 체결 스트림 — FD-2.6 데이터 신뢰도 검증 보강용) |
| 콜옥션 | GET | `/api/v2/spot/market/auction` | P2 | 신규 |
| VIP 수수료율 | GET | `/api/v2/spot/market/vip-fee-rate` | P2 | 신규 |

### 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결

| 함수 목적 | Method | Path | 우선순위 | 비고 |
|---|---|---|---|---|
| 주문 제출 | POST | `/api/v2/spot/trade/place-order` | **P0(기존 구현됨)** | `place_order()` |
| 배치 주문 제출 | POST | `/api/v2/spot/trade/batch-orders` | P1 | FD-19(포트폴리오) 다중 실행 동시 진입 시 유용 |
| 주문 취소 | POST | `/api/v2/spot/trade/cancel-order` | **P0(기존 구현됨)** | `cancel_order()` |
| 배치 주문 취소 | POST | `/api/v2/spot/trade/batch-cancel-order` | P1 | |
| 심볼 전체 취소 | POST | `/api/v2/spot/trade/cancel-symbol-order` | P2 | FD-9.2 강제청산 시 유용(심볼별 일괄 취소) |
| 취소 후 재주문 | POST | `/api/v2/spot/trade/cancel-replace-order` | P1 | FD-4.4(주문 정정)의 실제 구현 — 현재 `modify_order()`가 NotImplementedError |
| 배치 취소·재주문 | POST | `/api/v2/spot/trade/batch-cancel-replace-order` | P2 | |
| 주문 상세 조회 | GET | `/api/v2/spot/trade/orderInfo` | **P0(기존 구현됨)** | `get_order()` |
| 미체결 주문 조회 | GET | `/api/v2/spot/trade/unfilled-orders` | P0 | FD-4.5(UNKNOWN 재조회) 및 FD-16.4(모니터링) 보강 |
| 체결 이력 조회 | GET | `/api/v2/spot/trade/history-orders` | P1 | FD-6.4(재시작 정합성 복구) 보강 |
| 체결 내역(fills) | GET | `/api/v2/spot/trade/fills` | P1 | 평균 체결가 정밀 계산(현재 `average_fill_price` 근사치 개선) |
| 예약(Plan/Trigger) 주문 제출 | POST | `/api/v2/spot/trade/place-plan-order` | P1 | FD-8.1 stop_loss를 거래소 네이티브 트리거로 이관 가능(현재는 폴링 기반) |
| 예약 주문 수정 | POST | `/api/v2/spot/trade/modify-plan-order` | P2 | |
| 예약 주문 취소 | POST | `/api/v2/spot/trade/cancel-plan-order` | P1 | |
| 예약 주문 배치 취소 | POST | `/api/v2/spot/trade/batch-cancel-plan-order` | P2 | |
| 현재 예약 주문 조회 | GET | `/api/v2/spot/trade/current-plan-order` | P1 | |
| 예약 주문 이력 | GET | `/api/v2/spot/trade/history-plan-order` | P2 | |

### 3.3 Account(계좌·입출금)

| 함수 목적 | Method | Path | 우선순위 | 비고 |
|---|---|---|---|---|
| 계좌 정보 | GET | `/api/v2/spot/account/info` | P1 | UID·권한 확인용 |
| 자산 조회 | GET | `/api/v2/spot/account/assets` | **P0(기존 구현됨)** | `get_balance()` |
| 계좌 청구서(bills) | GET | `/api/v2/spot/account/bills` | P1 | FD-20(운용보고서) 원천 데이터 |
| 이체(현물↔선물 등) | POST | `/api/v2/spot/wallet/transfer` | P1 | FD-19 포트폴리오 재구성이 실제 자금이동을 수반할 경우 필요 |
| 입금 주소 조회 | GET | `/api/v2/spot/wallet/deposit-address` | P2 | FD-11.5(출금 화이트리스트)와 대칭되는 입금 관리 — Phase 1은 출금 통제가 핵심이라 낮은 우선순위 |
| 출금 신청 | POST | `/api/v2/spot/wallet/withdrawal` | **금지** | 7.9 원칙 — "이 클래스의 어떤 메서드도 출금 기능을 포함하지 않는다"(`ExchangeAdapter` docstring). 이 엔드포인트는 **절대 구현하지 않는다.** |
| 출금 이력 조회 | GET | `/api/v2/spot/wallet/withdrawal-records` | P2 | 조회는 출금 실행이 아니므로 허용 — FD-20 보강용 |
| 입금 이력 조회 | GET | `/api/v2/spot/wallet/deposit-records` | P2 | |

> **주의(7.9 원칙 재확인)**: 위 표에 없는 출금 관련 엔드포인트
> (`spotCancelWithdrawal` 등)도 "취소"는 출금 자체를 일으키지 않으므로
> 조회/취소는 허용하되, **신규 출금을 발생시키는 모든 엔드포인트는 이
> 구현체에 절대 포함하지 않는다** — 정책문서 7.9와 FD-11.5(화이트리스트
> 기반 출금은 거래소 자체 UI에서만 가능하고, AIOS가 API로 대신 실행하지
> 않는다는 기존 설계)를 그대로 따른다.

## 4. Margin(마진) 엔드포인트 체크리스트

Bitget V2는 `marginType` 파라미터(`crossed`/`isolated`)로 cross/isolated를
통일했다 — `bitget_ex`처럼 두 클래스로 나눌 필요 없이 파라미터화된 함수
하나로 양쪽을 처리할 수 있다(코드 중복 감소).

| 함수 목적 | Method | Path 패턴 | 우선순위 | 비고 |
|---|---|---|---|---|
| 지원 통화 조회 | GET | `/api/v2/margin/currencies` | P1 | |
| 계좌 자산 조회 | GET | `/api/v2/margin/{marginType}/account/assets` | P0 | 신규 모델 필요(§5.1) |
| 대출 실행 | POST | `/api/v2/margin/{marginType}/account/borrow` | P1 | **8.2-A 주의**: 이 호출은 FD-8.3 RiskEngine 승인 이후에만, 레버리지 정책(risk_policy.yaml leverage 섹션) 재검증 통과 시에만 트리거되어야 함 |
| 상환 실행 | POST | `/api/v2/margin/{marginType}/account/repay` | P1 | |
| 리스크율 조회 | GET | `/api/v2/margin/{marginType}/account/risk-rate` | P0 | FD-8.3 지표 4(집중도)·청산 위험 판단 입력값으로 재사용 가능 |
| 최대 대출가능액 | GET | `/api/v2/margin/{marginType}/account/max-borrowable-amount` | P1 | |
| 최대 이체가능액 | GET | `/api/v2/margin/{marginType}/account/max-transfer-out-amount` | P2 | |
| 이자율/한도 조회 | GET | `/api/v2/margin/{marginType}/interest-rate-and-limit` | P1 | |
| 담보 등급표 조회 | GET | `/api/v2/margin/{marginType}/tier-data` | P2 | |
| 빠른 상환 | POST | `/api/v2/margin/{marginType}/account/flash-repay` | P2 | |
| 주문 제출 | POST | `/api/v2/margin/{marginType}/place-order` | **P0** | Executor가 마진 실행을 지원하려면 필수 |
| 배치 주문 제출 | POST | `/api/v2/margin/{marginType}/batch-place-order` | P2 | |
| 주문 취소 | POST | `/api/v2/margin/{marginType}/cancel-order` | **P0** | |
| 미체결 주문 조회 | GET | `/api/v2/margin/{marginType}/open-orders` | P0 | |
| 주문 이력 조회 | GET | `/api/v2/margin/{marginType}/history-orders` | P1 | |
| 체결 내역 조회 | GET | `/api/v2/margin/{marginType}/fills` | P1 | |
| 강제청산 이력 조회 | GET | `/api/v2/margin/{marginType}/liquidation-order` | P1 | FD-9.6(Reconciliation) 입력값으로 유용 |
| 대출/상환/이자/청산/거래 이력 조회 4종 | GET | `/api/v2/margin/{marginType}/{borrow,repay,interest,liquidation}-history` | P2 | FD-20 보강용 |

## 5. Futures/Mix(무기한 선물) 엔드포인트 체크리스트

> 06번 §6.1-A(자산군 확장 원칙)에 따라 파생상품 리스크 지표(레버리지
> 마진콜, 청산가, 그릭스)는 이 문서가 아니라 후속 FD-8.3 확장 스펙에서
> 별도 확정한다 — 이 문서는 "API 연동"만 다루고 "그 값을 리스크 판단에
> 어떻게 쓸지"는 FD-8 담당(8.2-A 경계 유지).

### 5.1 Market

| 함수 목적 | Method | Path | 우선순위 | 내부 모델 |
|---|---|---|---|---|
| 계약 정보 조회 | GET | `/api/v2/mix/market/contracts` | P0 | 신규(`FuturesContractInfo` — 계약단위/최소주문수량/최대레버리지) |
| 현재가(단일/전체) | GET | `/api/v2/mix/market/ticker`, `/tickers` | P0 | `Ticker`(exchange 필드로 구분 재사용) |
| 호가창 | GET | `/api/v2/mix/market/merge-depth` | P0 | `OrderBook` |
| 캔들/과거캔들 | GET | `/api/v2/mix/market/candles`, `/history-candles` | P0 | `Candle` |
| 현재 펀딩레이트 | GET | `/api/v2/mix/market/current-fund-rate` | P0 | 신규(`FundingRate`) — FD-8.3 지표 계산(무기한 선물 보유비용) 입력값 |
| 과거 펀딩레이트 | GET | `/api/v2/mix/market/history-fund-rate` | P1 | |
| 다음 펀딩 시각 | GET | `/api/v2/mix/market/funding-time` | P1 | |
| 오픈 인터레스트 | GET | `/api/v2/mix/market/open-interest` | P1 | FD-2.6류 시장 전체 신호 보강 |
| 심볼별 레버리지 구간표 | GET | `/api/v2/mix/market/query-position-lever` | P1 | |
| 체결/과거체결 | GET | `/api/v2/mix/market/fills`, `/fills-history` | P2 | |

### 5.2 Account

| 함수 목적 | Method | Path | 우선순위 | 비고 |
|---|---|---|---|---|
| 단일/전체 계좌 조회 | GET | `/api/v2/mix/account/account`, `/accounts` | **P0** | `get_balance()` 확장 |
| 레버리지 설정 | POST | `/api/v2/mix/account/set-leverage` | **P0** | FD-8.3 지표 3(Leverage)이 "PAPER는 항상 1.0 고정" Draft를 벗어나려면 필수 — 8.2-A 설계 제약(§설계 제약 4, ADR-2026-08-29-E) 재확인: 파생상품 확장은 별도 leaf |
| 마진 모드 설정(cross/isolated) | POST | `/api/v2/mix/account/set-margin-mode` | P0 | |
| 포지션 모드 설정(단방향/양방향) | POST | `/api/v2/mix/account/set-position-mode` | P0 | |
| 마진 증감 | POST | `/api/v2/mix/account/set-margin` | P1 | |
| 청산가 조회 | GET | `/api/v2/mix/account/liq-price` | P0 | FD-8.3(MDD/청산 위험) 계산 입력값 |
| 최대 개설가능수량 | GET | `/api/v2/mix/account/max-open` | P1 | |
| 계좌 청구서 | GET | `/api/v2/mix/account/bill` | P1 | FD-20 보강용 |

### 5.3 Position

| 함수 목적 | Method | Path | 우선순위 |
|---|---|---|---|
| 단일 포지션 조회 | GET | `/api/v2/mix/position/single-position` | **P0** |
| 전체 포지션 조회 | GET | `/api/v2/mix/position/all-position` | **P0** |
| 과거 포지션 조회 | GET | `/api/v2/mix/position/history-position` | P1 |
| ADL 순위 조회 | GET | `/api/v2/mix/position/adlRank` | P2 |

### 5.4 Order(Trade)

| 함수 목적 | Method | Path | 우선순위 | 비고 |
|---|---|---|---|---|
| 주문 제출 | POST | `/api/v2/mix/order/place-order` | **P0** | |
| 주문 정정 | POST | `/api/v2/mix/order/modify-order` | P0 | FD-4.4가 Futures에서는 실제 정정 API를 직접 지원(Spot의 "취소 후 재주문" 폴백과 다름) |
| 주문 취소 | POST | `/api/v2/mix/order/cancel-order` | **P0** | |
| 전체 취소 | POST | `/api/v2/mix/order/cancel-all-orders` | P1 | FD-9.2 강제청산 시 유용 |
| 포지션 즉시청산 | POST | `/api/v2/mix/order/close-positions` | **P0** | FD-9.2(Watchdog LIQUIDATE) 실제 집행 경로 |
| 주문 상세/체결/이력 조회 | GET | `/api/v2/mix/order/detail`, `/fills`, `/orders-history` | P0 | |
| 미체결 주문 조회 | GET | `/api/v2/mix/order/orders-pending` | P0 | |
| TP/SL 주문 제출 | POST | `/api/v2/mix/order/place-tpsl-order` | P1 | FD-8.1 stop_loss/take_profit 네이티브 이관 후보 |
| 포지션 단위 TP/SL | POST | `/api/v2/mix/order/place-pos-tpsl` | P1 | |
| 예약(Plan) 주문 제출/수정/취소/조회 | POST/GET | `/api/v2/mix/order/place-plan-order` 외 | P1 | |
| 배치 제출/취소 | POST | `/api/v2/mix/order/batch-place-order`, `/batch-cancel-orders` | P2 | |

## 6. WebSocket

기존 `market_data_mixin.py::subscribe_ticker_stream()`이 이미 Public
`wss://ws.bitget.com/v2/ws/public` 연결·재연결(지수 백오프)·구독 패턴을
검증해뒀다 — 이 문서의 확장은 **같은 연결 관리 로직을 재사용**하고
채널만 추가한다(연결 로직 중복 구현 금지).

| 채널 | 구분 | instType 예 | 우선순위 | 비고 |
|---|---|---|---|---|
| `ticker` | Public | SPOT / USDT-FUTURES | P0(기존 구현됨) | |
| `candle{1m,5m,15m,1H,4H,1D}` | Public | SPOT / USDT-FUTURES | P1 | FD-2.2 실시간 캔들 — 현재는 REST 폴링만 |
| `books`/`books5`/`books15` | Public | SPOT / USDT-FUTURES | P1 | 실시간 호가창 |
| `trade` | Public | SPOT / USDT-FUTURES | P2 | 실시간 체결 스트림 |
| `account` | **Private**(로그인 필요) | SPOT / USDT-FUTURES | P1 | 실시간 잔고 변동 — 현재 폴링 기반 FD-16.4 모니터링 보강 |
| `positions` | **Private** | USDT-FUTURES | P1 | |
| `orders` | **Private** | SPOT / USDT-FUTURES | P0 | FD-4.5(UNKNOWN 재조회)를 폴링 대신 실시간 이벤트로 대체할 근본 해결책 — 이 채널이 붙으면 3회 폴링 재시도 로직이 "최후의 폴백"으로 격하되고 정상 경로는 실시간 확인이 됨 |
| `orders-algo` | **Private** | SPOT / USDT-FUTURES | P2 | 예약주문 상태 실시간 |

> **Private 채널 로그인**: `login` op으로 API Key+타임스탬프+서명을 첫
> 메시지로 전송(공식 문서 확인 필요, 이 문서 작성 시점엔 Public 채널만
> 라이브 검증됨) — 구현 leaf 착수 시 실제 문서로 재확인.

## 7. Public/Common

| 함수 목적 | Method | Path | 우선순위 |
|---|---|---|---|
| 서버 시간 | GET | `/api/v2/public/time` | P1(타임스탬프 서명 오차 디버깅에 유용) |
| 공지사항 | GET | `/api/v2/public/annoucements` | P2 |
| 수수료율 조회 | GET | `/api/v2/common/trade-rate` | P1(FD-8.2 수수료 미반영 Draft를 벗어날 때 필요) |

## 8. 신규 데이터 모델(§2 모델 재사용 원칙에 따라 최소한만)

| 모델 | 목적 | 배치 위치 |
|---|---|---|
| `FundingRate` | 무기한 선물 펀딩레이트(현재/예상) | `src/data/models/market_data.py` (Ticker/Candle과 동일 계층) |
| `MarginAccountAssets` | 마진 계좌 자산·부채·리스크율 | `src/data/models/trading.py` |
| `FuturesContractInfo` | 계약단위·최소주문수량·최대레버리지 | `src/data/models/trading.py` |

`AccountBalance`/`Order`/`Position`은 기존 필드로 충분(Position에 이미
`leverage`/`margin` 필드 존재 — ADR-2026-08-28 다자산군 확장 때 이미
반영됨) — 무리하게 새 모델을 만들지 않는다.

## 9. 작업 분해(리프 단위, 최소모듈 원칙)

기존 세션 방식(작은 단일책임 파일)을 그대로 따른다. `bitget/market_data_mixin.py`
/ `account_mixin.py` / `trading_mixin.py` 3개 파일이 이미 있으므로, 각
파일 안에 P0 우선순위부터 순서대로 채워 넣는다(새 mixin 파일을 늘리지
않음 — 이미 있는 3분류 경계가 여전히 유효함):

1. **Spot P0 나머지**: `place-plan-order` 계열 제외, 미체결/체결이력
   조회(`unfilled-orders`, `history-orders`, `fills`) — FD-4.5/6.4가
   당장 필요로 하는 것부터
2. **Margin P0 전체**: 계좌자산·리스크율·주문 3종(제출/취소/미체결조회) —
   cross/isolated 파라미터화된 함수 하나로 구현, 별도 클래스 분리 안 함
3. **Futures P0 전체**: Market(계약정보/현재가/호가/캔들/펀딩레이트) →
   Account(계좌/레버리지/마진모드/포지션모드/청산가) → Position(단일/전체) →
   Order(제출/정정/취소/즉시청산/조회) — 이 순서(하위 계층부터)를 지켜야
   상위 계층 테스트가 가능
4. **WebSocket P0**: `orders` private 채널(로그인 방식 라이브 확인 선행)
5. **P1 전체**: 위 순서 반복
6. **P2**: 필요할 때 추가(완료조건에서 제외 — "100%"는 P0+P1까지를
   1차 완료 기준으로 삼고, P2는 "존재는 알지만 당장 안 씀"으로 명시)

각 리프는 기존 패턴 그대로: MockTransport 기반 통합테스트(실키 없음,
`test_bitget_adapter.py` 확장) → ruff/mypy → 커밋+즉시 push.

## 10. 완료조건(Acceptance Criteria)

- §3~§7의 **P0 표기 전체 항목**이 `BitgetAdapter`(또는 그 mixin)에
  실제 메서드로 존재하고, MockTransport 기반 테스트로 요청 파라미터·
  응답 파싱이 검증됨.
- §3.3의 "금지" 표기(출금 실행)는 어떤 리프에서도 구현되지 않았음을
  코드 리뷰로 재확인(정책 7.9 위반 없음).
- P1 항목은 별도 후속 리프로 순차 진행(이 문서가 그 존재를 이미
  못박아둬 "나중에 발견"이 아니라 "계획된 다음 순서"가 되도록 함).
- 실제 Bitget Demo API 키 확보 후에는 P0 전체를 라이브로 최소 1회씩
  왕복 확인(현재는 계정 미확보로 MockTransport 검증까지만 — 06번
  §6.3 Definition of Done에 이미 명시된 기존 제약과 동일).

## 참고 문헌
- Bitget 공식 API 문서: https://www.bitget.com/api-doc/
- 커뮤니티 SDK 엔드포인트 레퍼런스: https://github.com/tiagosiebler/bitget-api
- `Desktop\quant\bitget_ex`(작년 작성, V1 기준 — 엔드포인트 존재 여부
  체크리스트로만 참고, 코드는 미재사용)
- 02_exchange_adapter_v1.3.md(§2.1, ExchangeAdapter 인터페이스 계약)
- 03_core_modules_v1.2.md §3.8(Executor, FD-8.4)
- 07_logging_config_v1.3.md §7.2(risk_policy.yaml, leverage 섹션)
