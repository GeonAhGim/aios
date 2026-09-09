# Bitget UTA(Unified Trading Account) v3 API — 조사 요약 v1

- 근거 task: task-2514 (BR-15)
- 조사일: 2026-09-09 (공식 문서 WebFetch/WebSearch 조사 — **문서 조사이지 실계정
  왕복 검증이 아니다**. `.env`에 Bitget 실키가 없어 이 커밋 시점에는 UTA
  계정으로 실제 호출을 왕복시키지 못했다. 아래 값 중 "미검증" 표기가 없는
  것도 "공식 문서에 명시됨"이지 "이 어댑터로 실측 확인됨"은 아니다.)
- 관련 코드: `src/exchanges/bitget/account_mode.py`,
  `src/exchanges/bitget/error_codes.py`,
  `src/exchanges/bitget/account_mixin.py` (get_balance/get_account_info),
  `src/exchanges/bitget/trading_mixin.py` (place_order/cancel_order),
  `src/exchanges/bitget/trading_query_mixin.py` (get_order)

## 1. 배경 — 2026-09-09 실키 실측 (task-2514 spec)

사용자 실키로 읽기 전용 호출을 시도한 결과:

| 호출 | 결과 |
|---|---|
| 서명·패스프레이즈 검증 | 정상 통과 |
| `GET /api/v2/spot/account/info` (Classic, 라이브 헤더) | `code=40085`, `msg="You are in Unified Account mode, and the Classic Account API is not supported"` |
| 동일 호출 + `paptrading: 1`(데모 헤더) | `code=40099`, `msg="exchange environment is incorrect"` (해당 키가 데모 키가 아님 — 계정 모드와는 무관한 별개 실패) |

즉 이 계정은 이미 Classic → UTA로 전환된 상태이고, 이 어댑터가 그동안
Classic v2 엔드포인트만 호출해왔기 때문에(모든 mixin) 잔고·주문 조회가
전부 40085로 막힌다.

## 2. Classic v2 → UTA v3 핵심 차이 (공식 업그레이드 가이드 조사)

출처: https://www.bitget.com/api-doc/classic/uta-api-upgrade-guide (2026-09-09 조사)

- **서명 메커니즘은 v2/v3 동일** — "The signature mechanism is identical
  between v2 and v3." 기존 v2 API 키가 새 키 발급 없이 v3를 그대로 지원한다.
  즉 `adapter.py`의 `_sign()`/`_headers()`(prehash = timestamp + METHOD +
  request_path(+query) + body, HMAC-SHA256 → base64)는 **무수정**으로
  재사용 가능하다 — 바뀌는 것은 오직 요청 경로/파라미터/body 필드명이다.
- 데모 모드 헤더(`paptrading: 1`)도 그대로 재사용된다(§4 참고).
- 파라미터 개명(공식 가이드 표):

  | v2 | v3 | 비고 |
  |---|---|---|
  | `productType` | `category` | 대문자로 변경(예: `SPOT`, `USDT-FUTURES`) |
  | `marginCoin` | (제거) | UTA가 자동 결정 |
  | `marginMode` | (제거) | 계정 레벨 설정으로 이동, 주문별 지정 안 함 |
  | `size` | `qty` | 필드명 변경 |
  | (없음) | `posSide` | 헤지모드 필수 신규 필드(스팟은 해당 없음) |
  | `idLessThan` | `cursor` | 페이지네이션 |

- **오류 코드 표는 v2/v3 간 동일성이 보장되지 않는다** — 공식 문서가 명시적으로
  경고한다("Error code values and meanings are not guaranteed to be
  identical between v2 and v3"). `error_codes.py`의 `classify_body_code`
  표는 v2 전용으로 유지하고, v3 전용 코드가 나오면 별도 표로 분리해야 한다
  (이번 커밋은 아직 v3 전용 코드 표를 추가하지 않았다 — 40085/40099는 v2
  Classic 엔드포인트가 반환한 코드였고, v3 엔드포인트가 같은 상황에서 같은
  코드를 낸다는 보장은 없다. **미검증**).

## 3. 엔드포인트 매핑 (스팟 스콥, Phase 1)

출처: 각 엔드포인트 문서 페이지(`https://www.bitget.com/api-doc/uta/...`,
2026-09-09 조사).

| 기능 | Classic v2 | UTA v3 |
|---|---|---|
| 계좌 정보 | `GET /api/v2/spot/account/info` | `GET /api/v3/account/info` |
| 잔고 | `GET /api/v2/spot/account/assets` | `GET /api/v3/account/assets` |
| 주문 생성 | `POST /api/v2/spot/trade/place-order` | `POST /api/v3/trade/place-order` |
| 주문 취소 | `POST /api/v2/spot/trade/cancel-order` | `POST /api/v3/trade/cancel-order` |
| 주문 조회 | `GET /api/v2/spot/trade/orderInfo` | `GET /api/v3/trade/order-info` |
| 미체결 조회 | `GET /api/v2/spot/trade/unfilled-orders` | `GET /api/v3/trade/unfilled-orders` |
| 배치 생성/취소 | `.../batch-orders` / `.../batch-cancel-order` | `POST /api/v3/trade/place-batch` / `.../cancel-batch` |
| 포지션(파생) | 없음(스팟은 잔고에서 합성, `account_mixin.get_positions`) | `GET /api/v3/position/current-position` (파생상품 전용 — 스팟은 v2와 동일하게 잔고 합성 유지) |

이번 커밋이 실제로 v2/v3 분기를 구현한 범위: **계좌 정보, 잔고, 주문
생성/취소/단건조회**(`account_mode.py`의 `account_aware_request` +
각 mixin). `get_open_orders`/`get_order_history`/`get_fills`/배치
메서드는 아직 Classic v2 전용으로 남아 있다 — UTA 계정에서 호출하면
여전히 40085로 실패한다(다음 리프 스콥, 이 문서에 기록해 후속 작업이
빠뜨리지 않게 한다).

### 3.1 place-order 응답 shape (공식 문서 예시, 2026-09-09 조사)

v2와 v3 모두 성공 시 `data: {"orderId": "...", "clientOid": "..."}`로
동일하다(필드명 차이 없음) — `place_order()`가 파싱 로직을 분기하지 않고
경로/요청 body만 분기하는 이유.

### 3.2 잔고 응답 shape — **미검증**

`GET /api/v3/account/assets` 응답의 정확한 필드 스키마는 이 조사에서
페이지 원문을 확보하지 못했다(문서 페이지가 별도 링크로 리다이렉트).
검색 엔진 요약 기준으로는 자산 배열의 각 행이 `coin`/`equity`/
`usdValue`/`balance`/`available`/`debt`/`locked` 필드를 갖고, v2의
`frozen`이 v3에서는 `locked`로 통합된 것으로 보인다 — 하지만 이는
2차 출처(검색 요약)이지 1차 문서 원문 확인이 아니다. `account_mixin.py`
의 `_parse_v3_balance_row`/`_v3_asset_rows`는 이 최선 추정을 코드로
옮기되 docstring에 "미검증"을 명시했다. **실 UTA 계정으로 왕복 검증
전까지 이 파싱 로직을 신뢰하지 말 것.**

## 4. 데모 트레이딩과 UTA

출처: `https://www.bitget.com/api-doc/uta/guide` (2026-09-09 조사).

- UTA v3도 데모 트레이딩을 지원한다. 단 **별도의 Demo API Key**를
  새로 발급해야 한다 — 기존 라이브 키에 `paptrading: 1` 헤더만 얹는다고
  데모 키가 되지 않는다. 이는 §1의 실측(40099)과 정확히 일치한다: 이
  task의 실키는 데모 키가 아니었으므로 `paptrading: 1`을 보내자
  "exchange environment is incorrect"로 거부됐다.
- REST 헤더 이름(`paptrading: 1`)과 서명 방식은 Classic과 동일 — 이
  어댑터의 `_headers()`/`_sign()`은 무수정.
- WebSocket 데모 엔드포인트는 REST와 별개 호스트
  (`wss://wspap.bitget.com/v3/ws/{public,private}`)이지만, 이 리프
  스콥(§2-B 계좌·주문)은 REST만 다루므로 WS 데모 분기는 다루지 않는다
  (WS 어댑터를 만질 때 별도로 조사할 것).
- **결론 (BITGET_SPOT_PROFILE 반영, `venue_profile.py` 참고)**: 이
  어댑터로 UTA 계정을 데모 검증하려면 (a) UTA로 전환된 계정에서 (b)
  별도 발급한 Demo API Key 3종(`BITGET_API_KEY`/`_SECRET`/`_PASSPHRASE`)
  을 `.env`에 채워야 한다. 현재 `.env`에는 세 값이 모두 없어(2026-09-09
  기준) `tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`
  와 이 리프가 추가한 잔고 조회 실호출 테스트가 모두 skip된다(사유 출력,
  값은 로그에 남기지 않음).

## 5. 계정 모드 감지 설계 (구현: `account_mode.py`)

- `BitgetAccountMode.CLASSIC`으로 시작(기존 어댑터·테스트와 100% 호환).
- `account_aware_request(client, build)`가 현재 모드로 요청을 보내고,
  **CLASSIC 상태에서 40085를 받으면** `client.account_mode`를
  `UNIFIED`로 전환한 뒤 `build`를 다시 호출해 v3 모양(경로·body 필드명
  둘 다 재조립)으로 1회 재시도한다. 전환은 어댑터 인스턴스 수명 동안
  단조적(UNIFIED → CLASSIC로 되돌아가지 않음) — Bitget의 UTA 전환은
  사람이 대시보드에서 수행하는 단방향 작업이기 때문이다(공식 지원 문서
  기준, 2026-09-09 조사: 되돌리려면 별도 절차가 필요하고 API 키 자체가
  달라진다).
- `place_order`/`cancel_order`는 재시도 시 body를 새로 조립하므로(같은
  dict를 재전송하지 않음) v2 `size` -> v3 `qty`+`category` 같은 구조적
  차이가 안전하게 반영된다. 40085는 체결 엔진 도달 전 거부라는 실측
  근거(§1 — 서명은 통과했지만 요청 자체가 그 API 표면으로 라우팅되지
  않음)로 재시도가 중복 주문을 만들지 않는다고 판단했다.

## 6. 후속 작업 (이 리프가 다루지 않은 것)

- `get_open_orders`/`get_order_history`/`get_fills`/배치 주문·취소는
  아직 Classic v2 전용 — UTA 계정에서는 여전히 40085.
- v3 전용 오류 코드 표(§2의 "동일성 미보장" 경고) — 아직 별도 표를
  만들지 않았다. UTA 데모 키 확보 후 실측하며 채울 것.
- `_parse_v3_balance_row`의 필드 스키마는 미검증 — UTA 데모 키 확보 시
  최우선 검증 대상.
