# 02d. 한국투자증권(KIS) Open API 전체 구현 스펙 — v1.0

Spec 상위: `02_exchange_adapter_v1.3.md`. Bitget(`02b`/`02c`)과 동일한
"스펙 먼저, 그 다음 리프 단위 구현" 방법론을 그대로 적용한다.

## 0. 배경 — 기존 구현 상태 조사

`src/exchanges/kis/`에 이미 초기 구현이 존재한다(6.9/6.10 작업, 이전
세션). OAuth2 토큰 발급/캐싱, 모의/실전 tr_id 자동 치환(T/J/C→V),
현재가·호가·일봉 조회, 잔고 조회, 주문 제출·정정·취소·조회,
health_check까지 — **국내주식(domestic_stock) 매매의 최소 골격은 이미
동작한다.** WebSocket은 `NotImplementedError`로 명시적 미구현
(승인키 기반 별도 인증 체계, 6.9/6.10 스콥 밖으로 문서화됨).

이번 조사(WebFetch, `github.com/koreainvestment/open-trading-api`,
2026-09-02)로 확인한 KIS 공식 API의 전체 카테고리:

| 카테고리 | 설명 | 규모(체감) |
|---|---|---|
| 국내주식(domestic_stock) | 시세조회+매매+계좌, Phase 1 대상(06번 §6.1) | 매우 큼(100개 이상 조회 엔드포인트) |
| 국내채권(domestic_bond) | 채권 시세·매매 | 중간 |
| 국내선물옵션(domestic_futureoption) | 국내 파생상품 | 큼 |
| 해외주식(overseas_stock) | 미국/중국/일본/베트남 등 해외 상장주식 | 매우 큼 |
| 해외선물옵션(overseas_futureoption) | 해외 파생상품 | 큼 |
| ELW | 주식워런트증권 시세 | 작음 |
| ETF/ETN | 시세(NAV 등 특화 필드) | 작음 |
| WebSocket(실시간) | 체결가/호가/체결통보, 별도 승인키 인증 | 전 카테고리 공통 |

**"100%"의 현실적 정의(Bitget 02b §1과 동일 원칙)**: 국내주식(domestic_
stock)의 조회 엔드포인트만 100개가 넘고, 대부분(재무비율/기관수급/
프로그램매매/공시 등)은 커뮤니티 SDK든 공식 문서든 이 세션이 정확한
tr_id·필드명을 확신할 수 있는 수준이 아니다 — 틀린 값으로 구현하면
"구현됨"이라는 거짓 안도감만 준다(11번 §11.3 "정직한 최선 추정치"
원칙의 반대 극단). 따라서:

- **P0**: 이미 구현됨 + FD-4(주문 전송)가 당장 필요로 하는 나머지
  (분봉, 매수/매도가능조회 등) — 이 세션이 tr_id/필드명을 실제
  공식 예제 코드 수준으로 확신하는 것만.
- **P1**: 국내주식 조회 확장(재무비율, 투자자매매동향, 공시정보 등) —
  구현하되 "커뮤니티 SDK/공식 예제 참고, tr_id 재확인 필요"를 반드시
  병기.
  국내채권/해외주식/ELW/ETF도 P1으로 포함(각자 규모가 작아 시도 가능).
- **P2**: 국내선물옵션·해외선물옵션(Phase 1 자산군 밖 — 06번 §6.1-A
  파생상품 확장 시 별도), 초장기 백데이터성 조회(예: 20년치 재무제표),
  프로그램매매 상세.
- **WebSocket**: 별도 리프(P0) — 승인키 발급 메커니즘부터 확인 필요.

## 1. 공통 사항 (기존 구현 재확인)

- Base URL: 실전 `https://openapi.koreainvestment.com:9443`, 모의
  `https://openapivts.koreainvestment.com:29443`
- OAuth2: `POST /oauth2/tokenP` (REST 토큰, 1일 유효, adapter.py에 이미
  캐싱 구현됨)
- WS 접속키: `POST /oauth2/Approval` (별도 발급, REST 토큰과 다름 —
  이 문서 §5에서 신규 확인)
- tr_id 모의투자 치환 규칙(기존 구현 그대로): 앞글자 T/J/C → V, 시세류
  (F로 시작)는 치환 없음
- 응답 포맷: `{rt_cd, msg_cd, msg1, output/output1/output2}`

## 2. 국내주식(domestic_stock) — P0 나머지

| 함수 목적 | tr_id | Path | 비고 |
|---|---|---|---|
| 분봉 조회 | FHKST03010200 | `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` | 현재 일봉만 지원 — FD-2.2/FD-2.3에 필요 |
| 매수가능조회 | TTTC8908R | `/uapi/domestic-stock/v1/trading/inquire-psbl-order` | FD-4.1 사전검증(주문가능금액/수량) |
| 매도가능수량조회 | TTTC8408R | `/uapi/domestic-stock/v1/trading/inquire-psbl-sell` | |
| 정정취소가능조회 | TTTC0084R | `/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl` | FD-4.4 정정 전 검증 |
| 실현손익 조회 | TTTC8494R | `/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl` | FD-20 보강용 |
| 휴장일 조회 | CTCA0903R | `/uapi/domestic-stock/v1/quotations/chk-holiday` | 시장 스케줄(MarketHours) 정확도 향상 |

## 3. 국내주식 — P1 (조회 확장, tr_id 재확인 필요)

| 함수 목적 | 비고 |
|---|---|
| 기간별 매매손익 조회 | FD-20 |
| 재무비율/재무제표(손익계산서·재무상태표) 조회 | FD-2.6류 펀더멘털 참고 지표 후보 |
| 투자자매매동향(외국인/기관) 조회 | 시장 신호 보강 |
| 배당/증자/감자 등 공시정보(KSD) 조회 | 이벤트 리스크 참고 |
| 종목별 프로그램매매 조회 | |
| 신용잔고/대주 관련 조회 | Phase 1은 신용거래 대상 아님(현금 매매만) — 조회만 |

## 4. 그 외 자산군(카테고리) — P1

각 카테고리는 새 mixin 파일 하나(최소모듈 원칙). 국내주식과 인증
방식은 동일(OAuth2 재사용), tr_id/path만 다르다.

| 카테고리 | mixin 파일 | 최소 커버리지 |
|---|---|---|
| 국내채권 | `kis/domestic_bond_mixin.py` | 시세조회, 매수/매도 주문 |
| 해외주식 | `kis/overseas_stock_mixin.py` | 현재가, 주문, 잔고(거래소코드 NASD/NYSE/AMEX/SEHK/TSE/HASE/VNSE 등) |
| ELW | `kis/elw_mixin.py` | 시세조회 |
| ETF/ETN | `kis/etf_mixin.py` | 시세조회(NAV 포함) |

## 5. 국내선물옵션/해외선물옵션 — P2

06번 §6.1-A(자산군 확장 원칙)에 따라 파생상품은 Phase 1 스콥 밖 —
"존재는 알지만 당장 안 씀"으로 명시(Bitget 02b P2와 동일 처리). 별도
요청 시 `02e_kis_derivatives_spec.md`로 확장.

## 6. WebSocket(실시간) — P0

기존 `subscribe_ticker_stream()`이 `NotImplementedError`로 막혀있다 —
승인키 인증부터 확인 필요.

| 항목 | 내용 |
|---|---|
| 접속키 발급 | `POST /oauth2/Approval`, body `{grant_type, appkey, secretkey}` → `approval_key`(REST access_token과 별개, 재사용 가능) |
| 접속 URL | 실전 `ws://ops.koreainvestment.com:21000`, 모의 `ws://ops.koreainvestment.com:31000` |
| 구독 메시지 | `{"header": {"approval_key", "custtype":"P", "tr_type":"1", "content-type":"utf-8"}, "body": {"input": {"tr_id": ..., "tr_key": 종목코드}}}` |
| 실시간 체결가 | tr_id `H0STCNT0` |
| 실시간 호가 | tr_id `H0STASP0` |
| 실시간 체결통보(주문 체결/거부) | tr_id `H0STCNI0`(모의는 `H0STCNI9`) — FD-4.5(UNKNOWN 재조회) 폴링을 실시간으로 대체할 후보(Bitget WS orders 채널과 동일 가치) |
| 메시지 포맷 | 파이프(`^`)로 구분된 고정폭 텍스트(JSON 아님!) — Bitget과 근본적으로 다른 파싱 필요, 필드 순서가 tr_id마다 다름(공식 문서의 "응답상세" 표 순서를 그대로 따라야 함) |

**리스크 표기**: 이 섹션 전체가 "라이브 검증 필요" 최선 추정치 중에서도
가장 불확실도가 높다(공식 문서 원문을 이번 조사에서 직접 확인하지
못했고, 커뮤니티 SDK의 관례적 값을 재구성한 것) — 실제 구현 시
파이프 구분 파싱 로직은 필드 개수가 하나만 틀려도 전부 어긋나므로,
Bitget의 WS 리프처럼 "메시지 파싱만 먼저 테스트 가능하게" 접근하되
가짜 데이터가 아니라 공식 문서의 실제 샘플 메시지 문자열을 구해 파싱
테스트를 작성해야 한다(이 문서 작성 시점엔 확보하지 못함).

## 7. 작업 분해(리프 단위)

1. 국내주식 P0 나머지(§2, 6개 엔드포인트) — `market_data_mixin.py`/
   `trading_mixin.py` 확장
2. 국내주식 P1(§3) — 신규 조회 위주, `account_mixin.py` 또는 신규
   `domestic_stock_extra_mixin.py`(재무/수급/공시가 성격이 달라 별도
   파일 분리, 최소모듈 원칙)
3. 해외주식(§4) — `overseas_stock_mixin.py`
4. 국내채권(§4) — `domestic_bond_mixin.py`
5. ELW/ETF(§4) — `elw_mixin.py`, `etf_mixin.py`
6. WebSocket(§6) — 승인키 인증 + 체결가/호가/체결통보 3채널(Bitget
   WS 리프와 동일하게 연결관리/파싱 분리, 단 파이프 구분 텍스트
   파서가 새로 필요 — JSON 파서 재사용 불가)
7. P2(국내/해외 선물옵션) — 필요해지면 별도 문서

## 8. 완료조건

- §2(P0)+§3~4(P1) 전체가 `KISAdapter`(또는 mixin)에 실제 메서드로
  존재, MockTransport 테스트로 검증됨.
- WebSocket 파이프 파서는 최소 1개 tr_id(체결가 H0STCNT0)에 대해
  공식 문서/커뮤니티 SDK가 제공하는 실제 샘플 문자열로 파싱 테스트
  통과.
- Phase 1 KR_EQUITY 스콥(06번 §6.1) 밖인 국내/해외 선물옵션은 P2로
  명시적으로 남겨두고 침묵 배제하지 않는다.
- 실제 KIS 모의투자 API 키 확보 후 P0 전체를 라이브로 최소 1회씩
  왕복 확인(현재 미확보 — 06번 §6.3 기존 제약과 동일).

## 참고 문헌
- KIS Open API 포털: https://apiportal.koreainvestment.com/
- 공식 예제 저장소: https://github.com/koreainvestment/open-trading-api
