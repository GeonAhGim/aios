# NH(NAMUH PLUG) 어댑터 — 확인된 구조적 갭 (fail-closed 정책)

ADR-2026-09-09-B H-3(task-2615) 대응. 이 문서는 NH investment adapter에서
"공식 문서로 확인했지만 근거 있는 구현이 불가능"하거나 "이번 리프
스콥 밖"이라 명시적으로 fail-closed(`NotImplementedError`)로 남긴 항목과
그 근거를 기록한다. 출처는 전부 자산군별 공식 OpenAPI 스펙
(`https://www.nhplug.com/openapi-docs/krstock/openapi.json`, 도메인이
정본(SSOT)) — 2026-09-16(task-2615) `curl`로 원문 JSON을 직접 내려받아
`components.schemas`/`x-realtime-channels`를 파이썬(`json.load`)으로
직접 파싱해 재확인했다. 이전 조사(task-114, 2026-09-03)는 SDK 소스코드와
WebFetch(요약 모델 경유) 기반이었는데, 문서가 커서(약 450KB) 요약 모델이
일부 절(`x-realtime-channels`)에 도달하기 전에 내용을 잘랐다 — `curl |
json.load`로 직접 키를 나열해서야 발견했다. 이 경험 자체가 교훈이다:
이 규모의 openapi.json을 조사할 때는 WebFetch보다 직접 다운로드 후
스키마 파싱을 우선한다.

## 1. `get_order()` — REST 주문 재조회, 구조적으로 불가능(확정)

**결론**: 구현하지 않는다. `NHAdapter.get_order()`는 `NotImplementedError`를
던진다(fail-closed) — `src/exchanges/nh/trading_mixin.py`.

**근거**(2026-09-16, `components.schemas` 직접 파싱으로 재확인):
- `place_order()`/`modify_order()`가 반환하는 `exchange_order_id`는
  `{iem_cd}:{mkt_orr_no}` 합성키다. `mkt_orr_no`는
  `POST /krstock/order/v1/cashBuy`|`cashSell`|`modify`|`cancel`의
  `Output_0.mkt_orr_no`(정수) 하나의 번호 체계다.
- "주문조회"에 가장 가까운 엔드포인트 `POST /krstock/inquiry/v1/
  dailyOrderExecution`의 응답 `Output_1[]` 항목 필드 **전체**는:
  `itg_orr_no, orr_mkt_cd_nm, mo_itg_orr_no, org_itg_orr_no, iem_cd,
  iem_nm, sby_dit_cd_nm, cor_can_dit_cd_nm, lon_dt, cfd_lon_cd,
  nmn_pr_tp_cd_nm, orr_cnd_dit_cd_nm, orr_qty, orr_pr, tot_cns_qty,
  cns_avg_uit_pr, cns_amt, cns_cnt, ny_cns_qty, cor_qty, can_qty, orr_tm,
  orr_mdi, bnd_byn_dt, syn_ttn_dit_cd_nm, orr_rjt_rsn_cd_nm, pcs_emp_no,
  rmt_mkt_cd, sor_mkt_sli_yn, krx_lnt_opi_sec_co_cd, krx_lnt_opi_act_no,
  krx_lnt_cnf_cpl_hur`. **`mkt_orr_no` 필드가 없다.** 주문 식별자는
  `itg_orr_no`(통합주문번호) 체계뿐이고, `mkt_orr_no`와 연결할 필드가
  스키마 어디에도 없다.
- 요청(`Input_0`) 필수 필드는 `orr_dt, act_no, ost_cns_dit, orr_mkt_cd`고
  선택 필드로 `itg_orr_no`(조회 필터)가 있다 — 이것도 `itg_orr_no` 체계고
  `mkt_orr_no`로 필터할 방법은 없다.
- 또 다른 후보 `POST /krstock/inquiry/v1/reservedInquiry`(예약주문조회)도
  확인했으나 이건 `bkg_orr_no`(예약주문번호) 체계라 무관하다 — 예약주문
  전용이고 이 adapter는 예약주문(`reservedOrder`)을 지원하지 않는다.
- 두 번호 체계(`mkt_orr_no` ↔ `itg_orr_no`)를 연결하는 필드는 공식
  스펙 어디에도 문서화돼 있지 않다. 라이브 계좌로 실제 주문 하나를
  두 엔드포인트에 모두 질의해 값을 비교하기 전까지는 매핑 관계를
  확정할 방법이 없다 — 추측(예: `itg_orr_no == mkt_orr_no`로 가정)으로
  조용히 잘못된 주문 상태를 만드는 것보다 명시적 미구현이 안전하다.

**재개 조건**: 라이브(모의투자 또는 실전) 계좌로 주문 1건을 넣고
`mkt_orr_no`와 `dailyOrderExecution` 조회 결과의 `itg_orr_no`를 나란히
비교해 매핑 규칙(같은 값인지, 오프셋이 있는지, 별도 조회가 필요한지)을
확인한 뒤에만 재시도한다. 그 전까지 체결/주문 재확인은 `get_balance()`
(포지션 변화 관찰) 또는 §2의 WS 체결통보(`d2`, 미구현)로 대체해야 한다.

## 2. WebSocket 실시간 스트림 — 스키마 확인 범위와 구현 스콥

**결론**: 접속/구독/재연결은 전부 구현됨(`websocket_mixin.py`). 데이터
프레임 스키마는 **이번 재조사로 확인됐다**(§2-1) — 이전 결론("SDK가
파싱을 위임해 미확인")은 SDK 소스코드만 봤을 때 얘기였다.

### 2-1. 확인된 채널(발췌 — 21개 채널 중 이번 스콥과 관련된 3개)

| tr_cd | 채널명 | 용도 | 필드 수 |
|---|---|---|---|
| `mc` | 국내주식 실시간체결가통합(KRX+NXT) | 체결가 | 29 |
| `mb` | 국내주식 실시간호가통합(KRX+NXT) | 호가 10단 | 52 |
| `d2` | 국내주식 실시간체결통보 | 체결/주문 통보 | 21 |

푸시 프레임 형식(전 채널 공통): `{"header":{"tr_cd":..,"tr_key":..},
"body":{...}}`, JSON. 구독 ack(`header`에 `tr_type`/`rsp_cd` 존재)와
데이터(둘 다 없음)를 헤더 모양으로 구분한다(websocket_mixin.py 기존
결론 그대로 — 이번 조사로 바뀌지 않음).

`mc`의 `body` 필드 전체(공식 스펙 그대로): `code, time, sign, change,
price, chrate, high, low, offer, bid, volume, volrate, movolume, value,
open, avgprice, janggubun, bidrate, volpower, new_volume, bidvolall,
offvolall, kospigb, value_won, marketgb, main_close, market_sign,
market_change, market_chrate`.

`d2`의 `body` 필드 전체: `userid, itemgb, accountno, orderno, issuecd,
slbygb, concgty, concprc, conctime, ucgb, rejgb, fundcode, sin_gb,
loan_date, ato_ord_tpe_chg, issue_nm, rmt_mkt_cd, snd_mkt_cd,
ord_cond_prc, sor_orrgb, stop_efforn_gb`.

### 2-2. 이번 리프에서 구현한 것

`subscribe_ticker_stream()` — `mc` 채널만 `Ticker`로 매핑한다
(`src/exchanges/nh/websocket_parsing.py::parse_mc_ticker_frame`). 사용
필드(`code/price/offer/bid/volume`)는 전부 위 확인된 29개 필드 목록
안에 있다 — 그 외 필드(고가/저가/등락률/프로그램매매 관련 등)는
`Ticker` 모델에 대응 슬롯이 없어 사용하지 않는다(추측 아님, 스키마
확인 후 선택).

### 2-3. 이번 리프에서 구현하지 않은 것(스콥 밖 — "미확인"이 아님)

- `mb`(호가)/`d2`(체결통보) 채널: 스키마는 확인됐지만 이를 소비하는
  `subscribe_orderbook_stream()`/`subscribe_order_notification_stream()`
  같은 확장 메서드가 아직 `ExchangeAdapter` 계약에도 이 adapter에도
  없다 — KIS(`kis/websocket_mixin.py`)가 먼저 만든 패턴을 재사용할
  후속 리프 후보.
  **주의**: `d2`의 `orderno` 필드(예시값 `"0000000030"`)가 §1의
  `get_order()` 공백을 메울 유력한 후보다 — 다만 이게 `mkt_orr_no`/
  `itg_orr_no` 중 어느 체계인지, 혹은 제3의 체계인지는 라이브 응답
  없이는 확정할 수 없어(추측 금지) 이 문서에 후속 조사 항목으로만
  남긴다.
- `get_ohlcv()`: 경로(`/krstock/quote/v1/currentDaily`)만 확인, 이번
  리프 스콥 밖(기존 결론 유지, `market_data_mixin.py` 참조).

## 3. 요약

| 항목 | 상태 | 근거 |
|---|---|---|
| `get_order()` | `NotImplementedError`(확정, fail-closed) | §1 |
| `subscribe_ticker_stream()`(`mc`) | 구현됨 | §2-2 |
| `subscribe_orderbook_stream()`(`mb`) | 미구현(스콥 밖) | §2-3 |
| 체결통보(`d2`) | 미구현(스콥 밖, `get_order()` 대체 후보) | §2-3 |
| `get_ohlcv()` | `NotImplementedError`(스콥 밖, 기존 결론 유지) | §2-3 |
