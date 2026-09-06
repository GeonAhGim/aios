# 비트겟 API 엔드포인트 커버리지 매트릭스

BR-9(ADR-2026-09-06-I D5). 생성: `python scripts/bitget_coverage.py`(오프라인, 결정적).
기준 목록: `docs/design/02b_bitget_api_v2_full_spec_v1.md` + `02c_bitget_api_v2_extended_spec_v1.md`의 Method/Path 표.

범위: REST만. WebSocket 채널(02b §6)은 Method/Path 표가 아니라 `test_bitget_ws_messages.py`/`test_bitget_ws_parsers.py`가 별도로 검증한다.

- 기준 엔드포인트 수: 156개(위 두 문서에서 기계 추출)

## 요약

| 구현됨 | 범위밖 | 미착수 | 합계 | 구현률 |
|---|---|---|---|---|
| 132 | 1 | 23 | 156 | 84.62% |

완료 정의(D5): `미착수` 0건 또는 우선순위(P0/P1/P2)로 사유가 남아있음. `범위밖`은 문서가 명시한 정책 배제(7.9 원칙, 출금)만 해당한다.

## 카테고리별

| 카테고리 | 구현됨 | 전체 | 구현률 |
|---|---|---|---|
| 1.1 Convert(간편환전) — `convert_mixin.py` | 4 | 4 | 100.00% |
| 1.10 Strategy(전략주문) — `strategy_mixin.py` | 4 | 4 | 100.00% |
| 1.11 Inst Loan(기관 전용 대출) — `inst_loan_mixin.py` | 4 | 4 | 100.00% |
| 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 6 | 6 | 100.00% |
| 1.3 P2P(개인간 법정화폐 거래) — `p2p_mixin.py` | 4 | 4 | 100.00% |
| 1.4 Earn(적금/이자상품) — `earn_mixin.py` | 5 | 5 | 100.00% |
| 1.5 Loan(코인담보대출) — `loan_mixin.py` | 8 | 8 | 100.00% |
| 1.6 Tax(세금 신고용 원본 데이터) — `tax_mixin.py` | 4 | 4 | 100.00% |
| 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 7 | 7 | 100.00% |
| 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 8 | 8 | 100.00% |
| 1.9 Grid(그리드봇) — `grid_mixin.py` | 6 | 6 | 100.00% |
| 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 6 | 10 | 60.00% |
| 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 12 | 17 | 70.59% |
| 3.3 Account(계좌·입출금) | 4 | 8 | 50.00% |
| 4. Margin(마진) 엔드포인트 체크리스트 | 13 | 18 | 72.22% |
| 5.1 Market | 11 | 13 | 84.62% |
| 5.2 Account | 9 | 9 | 100.00% |
| 5.3 Position | 3 | 4 | 75.00% |
| 5.4 Order(Trade) | 12 | 14 | 85.71% |
| 7. Public/Common | 2 | 3 | 66.67% |

## 전체 엔드포인트 매트릭스

| Method | Path | 카테고리 | 상태 | 우선순위 | 이름 | 출처 |
|---|---|---|---|---|---|---|
| GET | `/api/v2/account/sub-account-assets` | 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 구현됨 | - | 서브계정 자산 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/broker/account/create-subaccount` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 서브계정 생성 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/broker/account/create-subaccount-apikey` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 서브계정 API키 생성 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/broker/account/subaccount-assets` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 서브계정 자산 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/broker/account/subaccount-deposit` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 리베이트(수수료 환급) 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/broker/account/subaccount-list` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 서브계정(브로커 하위) 목록 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/broker/account/subaccount-transfer` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 서브계정 이체 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/broker/info` | 1.7 Broker(브로커/리셀러) — `broker_mixin.py` | 구현됨 | - | 브로커 정보 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/common/trade-rate` | 7. Public/Common | 구현됨 | P1 | 수수료율 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/convert/convert-record` | 1.1 Convert(간편환전) — `convert_mixin.py` | 구현됨 | - | 환전 이력 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/convert/currencies` | 1.1 Convert(간편환전) — `convert_mixin.py` | 구현됨 | - | 지원 코인쌍 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/convert/quoted-price` | 1.1 Convert(간편환전) — `convert_mixin.py` | 구현됨 | - | 견적 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/convert/trade` | 1.1 Convert(간편환전) — `convert_mixin.py` | 구현됨 | - | 환전 실행 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/copy/mix-follower/close-settings` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 팔로워 — 팔로우 해제 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/copy/mix-follower/query-current-orders` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 팔로워 — 진행중 카피 주문 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/copy/mix-follower/query-history-orders` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 팔로워 — 카피 주문 이력 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/copy/mix-follower/query-traders` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 팔로워 — 팔로우 중인 트레이더 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/copy/mix-follower/setting` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 팔로워 — 트레이더 팔로우 설정 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/copy/mix-trader/config-query-followers` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 트레이더 — 내 팔로워 목록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/copy/mix-trader/config-settings-base` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 트레이더 — 프로필 설정 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/copy/mix-trader/order-profit-history-summary` | 1.8 Copy Trading(카피트레이딩) — `copy_trading_mixin.py` | 구현됨 | - | 트레이더 — 손익 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/earn/savings/assets` | 1.4 Earn(적금/이자상품) — `earn_mixin.py` | 구현됨 | - | 보유 적금 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/earn/savings/product` | 1.4 Earn(적금/이자상품) — `earn_mixin.py` | 구현됨 | - | 적금상품 목록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/earn/savings/records` | 1.4 Earn(적금/이자상품) — `earn_mixin.py` | 구현됨 | - | 적금 가입/해지 이력 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/earn/savings/redeem` | 1.4 Earn(적금/이자상품) — `earn_mixin.py` | 구현됨 | - | 적금 해지(상환) | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/earn/savings/subscribe` | 1.4 Earn(적금/이자상품) — `earn_mixin.py` | 구현됨 | - | 적금 가입 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/ins-loan/ensure-coins-convert` | 1.11 Inst Loan(기관 전용 대출) — `inst_loan_mixin.py` | 구현됨 | - | 담보 코인 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/ins-loan/loan-order` | 1.11 Inst Loan(기관 전용 대출) — `inst_loan_mixin.py` | 구현됨 | - | LTV(대출한도) 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/ins-loan/product-infos` | 1.11 Inst Loan(기관 전용 대출) — `inst_loan_mixin.py` | 구현됨 | - | 대출 상품 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/ins-loan/repaid-history` | 1.11 Inst Loan(기관 전용 대출) — `inst_loan_mixin.py` | 구현됨 | - | 상환 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/loan/borrow` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 대출 신청 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/loan/coin-info` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 대출 가능 코인 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/loan/hourly-interest-rate` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 담보율/청산가 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/loan/liquidation-records` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 청산 이력 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/loan/ongoing-orders` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 진행중인 대출 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/loan/repay` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 상환 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/loan/repay-history` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 상환 이력 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/loan/revise-pledge` | 1.5 Loan(코인담보대출) — `loan_mixin.py` | 구현됨 | - | 담보 추가/감액 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/margin/currencies` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 지원 통화 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/account/assets` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P0 | 계좌 자산 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/margin/{marginType}/account/borrow` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 대출 실행 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/margin/{marginType}/account/flash-repay` | 4. Margin(마진) 엔드포인트 체크리스트 | 미착수 | P2 | 빠른 상환 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/account/max-borrowable-amount` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 최대 대출가능액 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/account/max-transfer-out-amount` | 4. Margin(마진) 엔드포인트 체크리스트 | 미착수 | P2 | 최대 이체가능액 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/margin/{marginType}/account/repay` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 상환 실행 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/account/risk-rate` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P0 | 리스크율 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/margin/{marginType}/batch-place-order` | 4. Margin(마진) 엔드포인트 체크리스트 | 미착수 | P2 | 배치 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/margin/{marginType}/cancel-order` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P0 | 주문 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/fills` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 체결 내역 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/history-orders` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 주문 이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/interest-rate-and-limit` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 이자율/한도 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/liquidation-order` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P1 | 강제청산 이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/open-orders` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P0 | 미체결 주문 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/margin/{marginType}/place-order` | 4. Margin(마진) 엔드포인트 체크리스트 | 구현됨 | P0 | 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/tier-data` | 4. Margin(마진) 엔드포인트 체크리스트 | 미착수 | P2 | 담보 등급표 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/margin/{marginType}/{borrow,repay,interest,liquidation}-history` | 4. Margin(마진) 엔드포인트 체크리스트 | 미착수 | P2 | 대출/상환/이자/청산/거래 이력 조회 4종 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/account/account` | 5.2 Account | 구현됨 | P0 | 단일/전체 계좌 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/account/accounts` | 5.2 Account | 구현됨 | P0 | 단일/전체 계좌 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/account/bill` | 5.2 Account | 구현됨 | P1 | 계좌 청구서 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/account/liq-price` | 5.2 Account | 구현됨 | P0 | 청산가 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/account/max-open` | 5.2 Account | 구현됨 | P1 | 최대 개설가능수량 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/account/set-leverage` | 5.2 Account | 구현됨 | P0 | 레버리지 설정 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/account/set-margin` | 5.2 Account | 구현됨 | P1 | 마진 증감 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/account/set-margin-mode` | 5.2 Account | 구현됨 | P0 | 마진 모드 설정(cross/isolated) | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/account/set-position-mode` | 5.2 Account | 구현됨 | P0 | 포지션 모드 설정(단방향/양방향) | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/grid/place-grid` | 1.9 Grid(그리드봇) — `grid_mixin.py` | 구현됨 | - | 그리드 생성(선물) | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/mix/market/candles` | 5.1 Market | 구현됨 | P0 | 캔들/과거캔들 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/contracts` | 5.1 Market | 구현됨 | P0 | 계약 정보 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/current-fund-rate` | 5.1 Market | 구현됨 | P0 | 현재 펀딩레이트 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/fills` | 5.1 Market | 미착수 | P2 | 체결/과거체결 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/fills-history` | 5.1 Market | 미착수 | P2 | 체결/과거체결 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/funding-time` | 5.1 Market | 구현됨 | P1 | 다음 펀딩 시각 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/history-candles` | 5.1 Market | 구현됨 | P0 | 캔들/과거캔들 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/history-fund-rate` | 5.1 Market | 구현됨 | P1 | 과거 펀딩레이트 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/merge-depth` | 5.1 Market | 구현됨 | P0 | 호가창 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/open-interest` | 5.1 Market | 구현됨 | P1 | 오픈 인터레스트 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/query-position-lever` | 5.1 Market | 구현됨 | P1 | 심볼별 레버리지 구간표 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/ticker` | 5.1 Market | 구현됨 | P0 | 현재가(단일/전체) | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/market/tickers` | 5.1 Market | 구현됨 | P0 | 현재가(단일/전체) | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/batch-cancel-orders` | 5.4 Order(Trade) | 미착수 | P2 | 배치 제출/취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/batch-place-order` | 5.4 Order(Trade) | 미착수 | P2 | 배치 제출/취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/cancel-all-orders` | 5.4 Order(Trade) | 구현됨 | P1 | 전체 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/cancel-order` | 5.4 Order(Trade) | 구현됨 | P0 | 주문 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/close-positions` | 5.4 Order(Trade) | 구현됨 | P0 | 포지션 즉시청산 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/order/detail` | 5.4 Order(Trade) | 구현됨 | P0 | 주문 상세/체결/이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/order/fills` | 5.4 Order(Trade) | 구현됨 | P0 | 주문 상세/체결/이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/modify-order` | 5.4 Order(Trade) | 구현됨 | P0 | 주문 정정 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/order/orders-history` | 5.4 Order(Trade) | 구현됨 | P0 | 주문 상세/체결/이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/order/orders-pending` | 5.4 Order(Trade) | 구현됨 | P0 | 미체결 주문 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/place-order` | 5.4 Order(Trade) | 구현됨 | P0 | 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST/GET | `/api/v2/mix/order/place-plan-order` | 5.4 Order(Trade) | 구현됨 | P1 | 예약(Plan) 주문 제출/수정/취소/조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/place-pos-tpsl` | 5.4 Order(Trade) | 구현됨 | P1 | 포지션 단위 TP/SL | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/mix/order/place-tpsl-order` | 5.4 Order(Trade) | 구현됨 | P1 | TP/SL 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/position/adlRank` | 5.3 Position | 미착수 | P2 | ADL 순위 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/position/all-position` | 5.3 Position | 구현됨 | P0 | 전체 포지션 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/position/history-position` | 5.3 Position | 구현됨 | P1 | 과거 포지션 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/mix/position/single-position` | 5.3 Position | 구현됨 | P0 | 단일 포지션 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/p2p/advList` | 1.3 P2P(개인간 법정화폐 거래) — `p2p_mixin.py` | 구현됨 | P2 | 광고(주문) 목록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/p2p/merchantInfo` | 1.3 P2P(개인간 법정화폐 거래) — `p2p_mixin.py` | 구현됨 | - | 상인(merchant) 정보 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/p2p/merchantList` | 1.3 P2P(개인간 법정화폐 거래) — `p2p_mixin.py` | 구현됨 | - | P2P 지원 코인 목록 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/p2p/orderList` | 1.3 P2P(개인간 법정화폐 거래) — `p2p_mixin.py` | 구현됨 | - | P2P 주문 목록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/public/annoucements` | 7. Public/Common | 미착수 | P2 | 공지사항 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/public/time` | 7. Public/Common | 구현됨 | P1 | 서버 시간 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/account/assets` | 3.3 Account(계좌·입출금) | 구현됨 | P0 | 자산 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/account/bills` | 3.3 Account(계좌·입출금) | 구현됨 | P1 | 계좌 청구서(bills) | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/account/info` | 3.3 Account(계좌·입출금) | 구현됨 | P1 | 계좌 정보 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/grid/close-grid` | 1.9 Grid(그리드봇) — `grid_mixin.py` | 구현됨 | - | 그리드 취소 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/spot/grid/current-grid` | 1.9 Grid(그리드봇) — `grid_mixin.py` | 구현됨 | - | 진행중 그리드 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/spot/grid/grid-history` | 1.9 Grid(그리드봇) — `grid_mixin.py` | 구현됨 | - | 그리드 이력 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/spot/grid/grid-profit` | 1.9 Grid(그리드봇) — `grid_mixin.py` | 구현됨 | - | 그리드 손익 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/spot/grid/place-grid` | 1.9 Grid(그리드봇) — `grid_mixin.py` | 구현됨 | - | 그리드 생성(현물) | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/spot/market/auction` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 미착수 | P2 | 콜옥션 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/candles` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 구현됨 | P0 | 캔들 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/fills` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 구현됨 | P1 | 최근 체결 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/history-candles` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 구현됨 | P1 | 과거 캔들 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/merge-depth` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 미착수 | P2 | 병합 호가창 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/orderbook` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 구현됨 | P0 | 호가창 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/tickers` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 구현됨 | P0 | 현재가(전체/단일) | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/market/vip-fee-rate` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 미착수 | P2 | VIP 수수료율 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/public/coins` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 미착수 | P2 | 코인 정보 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/public/symbols` | 3.1 Market(공개 시세) — `src/exchanges/bitget/market_data_mixin.py` 확장 | 구현됨 | P1 | 심볼 정보 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/batch-cancel-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 배치 주문 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/batch-cancel-plan-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 미착수 | P2 | 예약 주문 배치 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/batch-cancel-replace-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 미착수 | P2 | 배치 취소·재주문 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/batch-orders` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 배치 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/cancel-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P0 | 주문 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/cancel-plan-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 예약 주문 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/cancel-replace-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 취소 후 재주문 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/cancel-symbol-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 미착수 | P2 | 심볼 전체 취소 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/trade/current-plan-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 현재 예약 주문 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/trade/fills` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 체결 내역(fills) | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/trade/history-orders` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 체결 이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/trade/history-plan-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 미착수 | P2 | 예약 주문 이력 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/modify-plan-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 미착수 | P2 | 예약 주문 수정 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/trade/orderInfo` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P0 | 주문 상세 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/place-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P0 | 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/trade/place-plan-order` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P1 | 예약(Plan/Trigger) 주문 제출 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/trade/unfilled-orders` | 3.2 Trade(주문) — FD-4 주문 전송 계층과 직결 | 구현됨 | P0 | 미체결 주문 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/wallet/deposit-address` | 3.3 Account(계좌·입출금) | 미착수 | P2 | 입금 주소 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/wallet/deposit-records` | 3.3 Account(계좌·입출금) | 미착수 | P2 | 입금 이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/wallet/subaccount-transfer` | 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 구현됨 | - | 서브계정 간 이체 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/spot/wallet/transfer` | 3.3 Account(계좌·입출금) | 구현됨 | P1 | 이체(현물↔선물 등) | `02b_bitget_api_v2_full_spec_v1.md` |
| POST | `/api/v2/spot/wallet/withdrawal` | 3.3 Account(계좌·입출금) | 범위밖 | 금지 | 출금 신청 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/spot/wallet/withdrawal-records` | 3.3 Account(계좌·입출금) | 미착수 | P2 | 출금 이력 조회 | `02b_bitget_api_v2_full_spec_v1.md` |
| GET | `/api/v2/tax/future-record` | 1.6 Tax(세금 신고용 원본 데이터) — `tax_mixin.py` | 구현됨 | - | 선물 세금 기록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/tax/margin-record` | 1.6 Tax(세금 신고용 원본 데이터) — `tax_mixin.py` | 구현됨 | - | 마진 세금 기록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/tax/p2p-record` | 1.6 Tax(세금 신고용 원본 데이터) — `tax_mixin.py` | 구현됨 | - | P2P 세금 기록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/tax/spot-record` | 1.6 Tax(세금 신고용 원본 데이터) — `tax_mixin.py` | 구현됨 | - | 스팟 세금 기록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/trace/strategy/cancel-order` | 1.10 Strategy(전략주문) — `strategy_mixin.py` | 구현됨 | - | 전략주문 취소 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/trace/strategy/current-order` | 1.10 Strategy(전략주문) — `strategy_mixin.py` | 구현됨 | - | 진행중 전략주문 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/trace/strategy/history-order` | 1.10 Strategy(전략주문) — `strategy_mixin.py` | 구현됨 | - | 전략주문 이력 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/trace/strategy/place-order` | 1.10 Strategy(전략주문) — `strategy_mixin.py` | 구현됨 | - | 전략주문 생성 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/user/create-virtual-subaccount` | 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 구현됨 | - | 서브계정 생성 | `02c_bitget_api_v2_extended_spec_v1.md` |
| POST | `/api/v2/user/create-virtual-subaccount-apikey` | 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 구현됨 | - | 서브계정 API키 생성 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/user/virtual-subaccount-apikey-list` | 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 구현됨 | - | 서브계정 API키 목록 | `02c_bitget_api_v2_extended_spec_v1.md` |
| GET | `/api/v2/user/virtual-subaccount-list` | 1.2 Subaccount(서브계정 관리) — `subaccount_mixin.py` | 구현됨 | - | 서브계정 목록 조회 | `02c_bitget_api_v2_extended_spec_v1.md` |
