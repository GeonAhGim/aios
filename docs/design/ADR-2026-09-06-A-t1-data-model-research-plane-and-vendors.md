# ADR-2026-09-06-A: T1급 데이터 모델 선반영, 리서치 데이터 플레인(비가격 데이터) 개방, 데이터 벤더 선정

## Status
Accepted (2026-09-06, Chief Architect). 사용자 지시: "시세·지표·백테스트를 T1 이상으로 확장할 것이므로 미리 감안해 설계하고,
범위 밖으로 뺐던 부분도 언제든 추가할 수 있게 하라. 데이터 벤더는 수익률·금융상품 확장성·신뢰도를 보고 CA가 판단해 추가하고,
오픈소스 등 다양한 방법을 모색할 전권을 준다."

## Context
- 현 데이터 모델은 **OHLCV 캔들 + 현물 심볼**에 맞춰져 있다(ADR-2026-09-04-B D1). T1(Bloomberg AIM/EMSX·FlexTrade·Charles River급)은
  틱·호가(L1/L2)·체결 테이프, 파생(옵션 체인·선물 만기·승수), 시점 기준(point-in-time) 참조 데이터, 나노초 타임스탬프를 전제한다.
  이 셋은 **나중에 얹을 수 없는 축**이다 — 캔들 전용 스키마로 1년치 데이터를 쌓은 뒤 틱·옵션을 넣으려면 전면 재적재가 된다.
- ADR-2026-09-05-A는 뉴스·공시·거시·대안 데이터를 "범위 밖"으로 뺐다. 그 결과 AI 연구 계층이 조사할 수 있는 세계가 시세·지표·백테스트로
  갇혔다. 사용자 지시대로 **포트를 지금 뚫어 두고 소스는 나중에 꽂는** 형태로 바꾼다.
- 벤더 선택은 지금까지 "사람 결정"으로 유보돼 있었다. 사용자가 CA에게 위임했다.

## Decision

### D1. 시장데이터 계약을 T1 형태로 미리 넓힌다 (contracts/v2, 지금 착수)
- **마이크로구조 3종 신설**: `TradeTick{ts_event, ts_recv(ns), price, size, aggressor, trade_id, venue}`,
  `QuoteL1{ts, bid, bid_size, ask, ask_size}`, `BookL2{ts, side, level, price, size, seq}` — 캔들은 이들의 파생으로 정의한다
  (`CandleColumns`는 유지, 생성 계보에 소스 종류를 기록).
- **시간**: 모든 시장데이터 타임스탬프는 **나노초 정수(UTC epoch ns)** + `ts_event`(거래소 발생)와 `ts_recv`(수신) 분리. 기존 캔들의
  `datetime`은 유지하되 신규 계약은 ns 정수를 정본으로 한다.
- **파생상품 심볼 모델**: `Instrument`에 `kind = spot|future|perp|option|bond|fund|index`, `underlying_id`, `expiry`, `strike`,
  `option_right`, `contract_multiplier`, `settlement`, `currency`, `country`, `mic`(ISO 10383)를 추가한다(v2, 기존 v1 불변).
  옵션 체인은 `underlying_id + expiry` 질의로 조회한다.
- **Point-in-time 참조 데이터**: 종목 속성·재무·기업행위는 `known_at`(그 사실을 알 수 있었던 시각)을 함께 저장하고, 백테스트는
  `known_at <= bar_ts`만 읽는다. 재작성(restatement)은 새 행으로 append(수정 금지).
- **저장**: 틱·호가는 캔들과 다른 파티션 전략(일자×종목, 컬럼지향)으로 warm 계층에 직행. hot(Postgres)에는 최근 N일 캔들과
  파생 메타데이터만 둔다.

### D2. Research Data Plane 신설 — 비가격 데이터는 "포트 먼저, 소스는 나중"
- 새 컨텍스트 `src/foundation/research_data/**`와 새 명세 `L4_research_data_and_market_ecosystem_v1.0.md`(리프 **RD-**).
- 4종 포트: `NewsProvider`(기사·헤드라인), `FilingProvider`(공시·재무, 원문 링크 필수), `MacroProvider`(시계열 지표),
  `AltDataProvider`(그 외 — 소셜·검색량·ESG 등). 전부 같은 골격: capabilities → fetch(span) → 정규화 → `known_at` 부여 → 계보 기록.
- **정규화 계약**: 모든 항목은 `ResearchItem{item_id, source_id, kind, published_at, known_at, instruments: [instrument_id], title,
  body_ref, url, language, hash}`로 통일된다. 본문 원문은 저장소가 아니라 참조(URL·object key)로 둔다(저작권).
- **AI 연구 계층 연결**: Agent Gateway의 `read`/`research` 스코프에 `research_data` 조회 도구를 추가한다. 권한(entitlement)은
  기존 DC-9 정책을 그대로 쓴다. 라이선스가 재배포를 금지하는 소스는 **본문 미저장·요약 금지·링크만** 규칙을 소스 메타에 박아 강제한다.
- **백테스트 안전장치**: 리서치 데이터를 쓰는 전략은 `known_at` 필터를 통과해야 하며, 이를 어기는 조회는 예외로 거부한다(누수 차단).

### D3. 데이터 벤더 — CA 선정 (3계층)
선정 기준: (a) 라이선스가 SaaS 재배포·유료 서비스 제공을 허용하는가, (b) 금융상품 폭(주식·ETF·선물·옵션·FX·채권·암호),
(c) 데이터 신뢰도·정정 정책, (d) 비용 대비 커버리지, (e) 어댑터 유지비.

| 계층 | 소스 | 채택 이유 | 비용 |
|---|---|---|---|
| **A. 즉시(무료·공개·라이선스 명확)** | **OpenDART**(금감원 전자공시), **한국은행 ECOS**, **KOSIS**, **KRX 정보데이터시스템**, **FRED**(미 연준), **SEC EDGAR**, **거래소 공개 API**(Binance·Bybit·OKX·Upbit), **GDELT**(글로벌 뉴스 이벤트) | 국내외 공시·거시·시세의 1차 출처. 신뢰도 최상(원본 발행자), 비용 0, 이용약관이 명확 | 0 |
| **B. 유료(MVP-1 종료 후 계약, 어댑터는 미리 작성)** | **Databento**(틱·L2, 미국 주식/선물/옵션), **Polygon.io**(미국 주식·옵션·FX·암호), **EODHD**(전 세계 거래소 광범위·펀더멘털, 저가), **Interactive Brokers**(상품 폭 최고 + 브로커 겸용) | 상품 확장성·정밀도가 필요한 지점만 유료로. Databento는 라이선스·스키마 문서가 가장 명확하고, EODHD는 커버리지 대비 최저가, IBKR은 다자산 실행까지 한 계약 | 유료 |
| **C. 금지·제한** | yfinance류 비공식 스크래핑, 이용약관이 재배포를 금지하는 무료 API | 상용 서비스 제공 불가·정정 정책 없음. **개발 픽스처로만** 허용, 프로덕션 경로 반입 금지 | — |

- **국내 우선순위**: OpenDART(공시·재무) → ECOS/KOSIS(거시) → KRX(공식 시세·지수) → 증권사 API(KIS 기연동, 키움 검토).
  국내 리서치 데이터가 해외보다 먼저다 — 사용자 시장이 국내이고 1차 출처가 전부 무료다.
- **각 벤더는 반입 전 평가 리프를 통과해야 한다**: 이용약관·라이선스 원문 인용 + 커버리지 실측 + 정정(restatement) 정책 확인
  (CH-0·IND-9·BT-14와 같은 형식). 확인 전 코드 반입 금지.
- 오픈소스 클라이언트(pykrx·FinanceDataReader·OpenDART 파이썬 래퍼 등)는 라이선스 확인 후 **어댑터 내부 구현으로만** 사용하며,
  AIOS 계약을 오염시키지 않는다.

### D4. 개방 원칙 (범위 밖 항목을 언제든 추가)
- 데이터·모델·에이전트는 전부 **포트 + 어댑터**로만 붙는다. 새 소스 추가 = 어댑터 파일 1개 + 평가 리프 1개. 코어 계약 변경 없음.
- 계약은 v2로 넓히되 v1은 불변(P5). 새 필드는 optional + 기본값으로 들어가 기존 소비자를 깨지 않는다.
- "범위 밖"이라는 표현은 앞으로 **"포트는 있고 어댑터가 없다"**를 의미한다. 포트 자체가 없는 영역은 남기지 않는다.

## Consequences
- 신규 리프: DC-19~26(T1 데이터 모델·틱 저장·파생 심볼·point-in-time), RD-1~18(리서치 데이터 플레인). MVP-1에 포함, 약 +44 리프.
- 순서: DC-19~22(계약·심볼 v2·PIT)는 **DC-13 직후**(뒤로 밀면 재적재 비용 발생), 틱 저장(DC-23~26)은 벤더 계약 시점,
  RD-1~10(포트·정규화·국내 무료 소스)은 AI 게이트웨이(AI-15)와 병행, RD-11~18(해외·뉴스·대안)은 그 뒤.
- 유료 벤더 계약·비용은 여전히 사용자 결정이지만, **어댑터와 평가는 미리 준비**돼 계약 즉시 붙는다.

## Rejected
- 캔들 전용 스키마 유지 후 나중 확장: 재적재 비용이 지금 확장 비용보다 크다.
- 비공식 스크래핑 소스를 프로덕션에 넣는 안: 상용 제공 불가·정정 정책 부재.
- 벤더 1곳 독점 채택: 상품군별 최적이 다르고 단일 장애점이 된다.
