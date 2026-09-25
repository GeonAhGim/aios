# NH OpenAPI 엔드포인트 커버리지 매트릭스

BR-17(ADR-2026-09-24-A D5). 생성: `python scripts/nh_openapi_coverage.py`(오프라인).
기준 목록 갱신(네트워크 필요): `python scripts/nh_openapi_fetch.py`.

- 기준 소스: NH Open API 공식 문서 `https://www.nhplug.com/openapi-docs/krstock/openapi.json`
- 기준 목록 추출 시각: 2026-09-24T13:27:28+00:00
- 기준 엔드포인트 수: 31개
- API 버전: v1 (NH투자증권 Open API — 국내주식 (Domestic Stock))

## 요약

| 구현됨 | 실전계좌필요 | 범위밖 | 미착수 | 합계 | 구현률 |
|---|---|---|---|---|---|
| 31 | 0 | 0 | 0 | 31 | 100.00% |

완료 정의(ADR D2): `미착수` 0건. `실전계좌필요`·`범위밖`은 남을 수 있으나 각각 사유가 있다.

## 전체 엔드포인트 매트릭스

| 경로 | 메서드 | 상태 | 설명 |
|---|---|---|---|
| `/krstock/inquiry/v1/assetStatus` | POST | 구현됨 | 투자계좌자산현황조회 |
| `/krstock/inquiry/v1/balance` | POST | 구현됨 | 주식잔고조회 |
| `/krstock/inquiry/v1/buyableQuantity` | POST | 구현됨 | 매수가능수량조회 |
| `/krstock/inquiry/v1/dailyOrderExecution` | POST | 구현됨 | 주식일별주문체결조회 |
| `/krstock/inquiry/v1/dailyPnl` | POST | 구현됨 | 실현손익일별합산조회 |
| `/krstock/inquiry/v1/integratedMargin` | POST | 구현됨 | 주식통합증거금 현황 |
| `/krstock/inquiry/v1/realizedPnl` | POST | 구현됨 | 주식잔고조회_실현손익 |
| `/krstock/inquiry/v1/reservedInquiry` | POST | 구현됨 | 주식예약주문조회 |
| `/krstock/inquiry/v1/rightsHeld` | POST | 구현됨 | 기간별계좌권리현황조회보유 |
| `/krstock/inquiry/v1/rightsScheduled` | POST | 구현됨 | 기간별계좌권리현황조회예정 |
| `/krstock/inquiry/v1/sellableQuantity` | POST | 구현됨 | 매도가능수량조회 |
| `/krstock/inquiry/v1/tradingPnl` | POST | 구현됨 | 종목별실현손익현황조회 |
| `/krstock/order/v1/cancel` | POST | 구현됨 | 주식주문(정정취소) 취소 |
| `/krstock/order/v1/cashBuy` | POST | 구현됨 | 주식주문(현금) 매수 |
| `/krstock/order/v1/cashSell` | POST | 구현됨 | 주식주문(현금) 매도 |
| `/krstock/order/v1/creditBuy` | POST | 구현됨 | 주식주문(신용) 매수 |
| `/krstock/order/v1/creditSell` | POST | 구현됨 | 주식주문(신용) 매도 |
| `/krstock/order/v1/modify` | POST | 구현됨 | 주식주문(정정취소) 정정 |
| `/krstock/order/v1/reservedCancel` | POST | 구현됨 | 주식예약주문취소 |
| `/krstock/order/v1/reservedOrder` | POST | 구현됨 | 주식예약주문 |
| `/krstock/quote/v1/afterHoursCurrent` | POST | 구현됨 | 국내주식 시간외현재가 |
| `/krstock/quote/v1/afterHoursExpected` | POST | 구현됨 | 주식현재가 시간외시간별예상 |
| `/krstock/quote/v1/currentAfterHoursDaily` | POST | 구현됨 | 주식현재가 시간외일자별주가 |
| `/krstock/quote/v1/currentAfterHoursExecution` | POST | 구현됨 | 주식현재가 시간외시간별체결 |
| `/krstock/quote/v1/currentDaily` | POST | 구현됨 | 주식현재가 일자별 |
| `/krstock/quote/v1/currentExecution` | POST | 구현됨 | 주식현재가 당일시간대별체결 |
| `/krstock/quote/v1/currentInvestor` | POST | 구현됨 | 주식현재가 투자자 |
| `/krstock/quote/v1/currentPrice` | POST | 구현됨 | 주식현재가 시세 |
| `/krstock/quote/v1/etfComponents` | POST | 구현됨 | ETF 구성종목시세 |
| `/krstock/quote/v1/etfCurrent` | POST | 구현됨 | ETF/ETN 현재가 |
| `/krstock/quote/v1/period` | POST | 구현됨 | 국내주식기간별시세(일/주/월/년) |
