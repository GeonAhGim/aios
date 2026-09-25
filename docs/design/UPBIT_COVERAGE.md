# Upbit 엔드포인트 커버리지 매트릭스

BR-21(task-7147, CTO 2026-09-25 승인). 생성: `python scripts/upbit_openapi_coverage.py`(오프라인).
기준 목록 갱신(네트워크 필요): `python scripts/upbit_openapi_fetch.py`.

파이프라인 선제 검증 전용 매트릭스다 -- 구현 없음, 전부 `미착수`(BR-18 참고,
`docs/exchanges/ADDING_AN_EXCHANGE.md`). 실제 어댑터 구현은 별도 task.

- 기준 소스: Upbit 공식 문서 `https://docs.upbit.com/kr/reference`
- 추출 방식: no public OpenAPI/Swagger JSON exists for docs.upbit.com (ReadMe.io SPA, requires an authenticated internal API to hydrate reference pages); entries are human-curated candidates confirmed live against api.upbit.com (REST: 200/401, WS: 101 handshake) -- unconfirmed candidates are dropped
- 기준 목록 추출 시각: 2026-09-25T09:47:14+00:00
- 기준 엔드포인트 수: 39개
- API 버전: unversioned (curated + live-verified, not machine-parsed) (Upbit Open API (Quotation/Exchange/WebSocket))

## 요약

| 구현됨 | 실전계좌필요 | 범위밖 | 미착수 | 합계 | 구현률 |
|---|---|---|---|---|---|
| 0 | 0 | 0 | 39 | 39 | 0.00% |

완료 정의(BR-18 패턴 재사용): `미착수` 0건이 되려면 실제 `src/exchanges/upbit/` 어댑터 구현(별도 task)이 선행돼야 한다.

## 전체 엔드포인트 매트릭스

| 경로 | 메서드 | 상태 | 설명 |
|---|---|---|---|
| `/v1/accounts` | GET | 미착수 | 전체 계좌 조회 |
| `/v1/api_keys` | GET | 미착수 | API 키 리스트 조회 |
| `/v1/candles/days` | GET | 미착수 | 일(Day) 캔들 조회 |
| `/v1/candles/minutes/1` | GET | 미착수 | 분(Minute) 캔들 조회 |
| `/v1/candles/months` | GET | 미착수 | 월(Month) 캔들 조회 |
| `/v1/candles/seconds` | GET | 미착수 | 초(Second) 캔들 조회 |
| `/v1/candles/weeks` | GET | 미착수 | 주(Week) 캔들 조회 |
| `/v1/deposit` | GET | 미착수 | 개별 입금 조회 |
| `/v1/deposits` | GET | 미착수 | 입금 리스트 조회 |
| `/v1/deposits/coin_address` | GET | 미착수 | 개별 입금 주소 조회 |
| `/v1/deposits/coin_addresses` | GET | 미착수 | 전체 입금 주소 조회 |
| `/v1/deposits/generate_coin_address` | POST | 미착수 | 입금 주소 생성 요청 |
| `/v1/deposits/krw` | POST | 미착수 | 원화 입금하기 |
| `/v1/market/all` | GET | 미착수 | 마켓 코드 조회 |
| `/v1/order` | DELETE | 미착수 | 주문 취소 접수 |
| `/v1/order` | GET | 미착수 | 개별 주문 조회 |
| `/v1/orderbook` | GET | 미착수 | 호가 정보 조회 |
| `/v1/orderbook/instruments` | GET | 미착수 | 호가 모아보기 지원 정보 조회 |
| `/v1/orders` | POST | 미착수 | 주문하기 |
| `/v1/orders/cancel_and_new` | POST | 미착수 | 취소 후 재주문 |
| `/v1/orders/chance` | GET | 미착수 | 주문 가능 정보 조회 |
| `/v1/orders/closed` | GET | 미착수 | 종료된 주문 조회 |
| `/v1/orders/open` | DELETE | 미착수 | 주문 일괄 취소 접수 |
| `/v1/orders/open` | GET | 미착수 | 체결 대기 주문 조회 |
| `/v1/orders/uuids` | GET | 미착수 | UUID로 주문 리스트 조회 |
| `/v1/status/wallet` | GET | 미착수 | 입출금 현황 조회 |
| `/v1/ticker` | GET | 미착수 | 현재가 정보 조회 |
| `/v1/trades/ticks` | GET | 미착수 | 최근 체결 내역 조회 |
| `/v1/withdraw` | GET | 미착수 | 개별 출금 조회 |
| `/v1/withdraws` | GET | 미착수 | 출금 리스트 조회 |
| `/v1/withdraws/chance` | GET | 미착수 | 출금 가능 정보 조회 |
| `/v1/withdraws/coin` | POST | 미착수 | 코인 출금하기 |
| `/v1/withdraws/coin_addresses` | GET | 미착수 | 출금 허용 주소 조회 |
| `/v1/withdraws/krw` | POST | 미착수 | 원화 출금하기 |
| `/websocket/v1` | WS:myAsset | 미착수 | 실시간 내 자산(myAsset) 구독 -- 인증 필요 |
| `/websocket/v1` | WS:myOrder | 미착수 | 실시간 내 주문/체결(myOrder) 구독 -- 인증 필요 |
| `/websocket/v1` | WS:orderbook | 미착수 | 실시간 호가(orderbook) 구독 |
| `/websocket/v1` | WS:ticker | 미착수 | 실시간 현재가(ticker) 구독 |
| `/websocket/v1` | WS:trade | 미착수 | 실시간 체결(trade) 구독 |
