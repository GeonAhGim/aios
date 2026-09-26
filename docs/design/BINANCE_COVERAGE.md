# 바이낸스 API 엔드포인트 커버리지 매트릭스

BR-22a(ADR-2026-09-26-A, task-7599). 생성: `python scripts/binance_endpoint_coverage.py`(오프라인, 결정적, 네트워크 호출 0건).
- 기준 소스: Binance Spot + USDS-M Futures public REST API docs (offline curation)
- 스냅샷 캡처 시각: 2026-09-26T00:00:00Z
- 기준 엔드포인트 수: 11개

범위: 이 매트릭스는 원본 거래소가 공개한 REST 엔드포인트 수만 집계한다 -- `src/exchanges/binance/`의 구현 여부는 대조하지 않는다(task-7599 범위 밖, 최소 카테고리 커버리지만 확인).

## 카테고리별 엔드포인트 수

| 카테고리 | 엔드포인트 수 | 하위 분류 |
|---|---|---|
| account | 3 | balance=2, positions=1 |
| market_data | 4 | candles=1, orderbook=1, ticker=2 |
| order | 4 | cancel=1, create=1, query=2 |

## 전체 엔드포인트 매트릭스

| Method | Path | 카테고리 | 하위 분류 | 설명 |
|---|---|---|---|---|
| GET | `/api/v3/account` | account | balance | Spot account information (signed) |
| GET | `/fapi/v2/balance` | account | balance | USDS-M futures account balance (signed) |
| GET | `/fapi/v2/positionRisk` | account | positions | USDS-M futures position information (signed) |
| GET | `/api/v3/klines` | market_data | candles | Kline/candlestick bars |
| GET | `/api/v3/depth` | market_data | orderbook | Order book depth |
| GET | `/api/v3/ticker/24hr` | market_data | ticker | 24hr rolling window price change statistics |
| GET | `/api/v3/ticker/price` | market_data | ticker | Latest price for a symbol or symbols |
| DELETE | `/api/v3/order` | order | cancel | Cancel an active spot order (signed) |
| POST | `/api/v3/order` | order | create | Place a new spot order (signed) |
| GET | `/api/v3/openOrders` | order | query | Query all open spot orders (signed) |
| GET | `/api/v3/order` | order | query | Query a spot order's status (signed) |
