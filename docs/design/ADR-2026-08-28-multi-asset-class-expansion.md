# ADR-2026-08-28: 다자산군(Multi-Asset-Class) 지원 확장

> 상태: 확정(구조 원칙) — 개별 거래소/브로커의 실제 지원 범위는 해당 Adapter
> 착수 시(작업트리 6번) 재확인 필요.

## 배경

기존 스펙(00~17번)은 Phase 1을 "크립토 단일 자산군, Bitget 실거래 + KIS는
인터페이스만(조회성 API까지만)"으로 명시적으로 좁혀왔다(06번 §6.1).
사용자가 실제 서비스 목표로 다음을 명시했다:

- 코인(crypto)
- 국내주식(kr_equity)
- 해외주식, 특히 미국증시(us_equity)
- 국내 선물옵션(kr_futures, kr_option) + 국내 ETN/ETF(kr_etn, kr_etf)
- 해외선물(overseas_futures) + 해외 ETN/ETF(overseas_etn, overseas_etf)

## 결정

### 1. AIOS 코어는 전체 자산군을 기본 지원하고, 개별 거래소는 자신이 지원하는
   범위만 선언한다 (Capability-Gated 원칙)

사용자 지침 그대로: **"거래소 API가 제공하는 모든 상품을 거래할 수 있도록
각 거래소 API 구현 시에 사전에 파악하고 연결될 수 있도록, AIOS에서는
기본적으로 다 구현되어야 하고 거래소 API가 지원하지 않을 경우 비활성화되는
식으로."**

즉:
- 데이터 모델(01번)·DB 스키마(04번)·API 계약(15/16/17번)은 크립토·주식·
  선물·옵션·ETN·ETF를 표현할 수 있는 **공통 상위집합**으로 설계한다.
- 각 `ExchangeAdapter` 구현체(02번)는 `ExchangeCapability.supported_asset_classes`
  로 자신이 실제 지원하는 자산군만 선언한다.
- Validator(03번 §3.3)는 주문 검증 시 대상 거래소의 `supported_asset_classes`에
  없는 자산군이면 **즉시 거부**(형식 오류가 아니라 `UNSUPPORTED_ASSET_CLASS`류
  명시적 에러) — 침묵 실패나 임의 폴백 금지.
- 어떤 브로커가 실제로 무엇을 지원하는지(예: KIS가 해외선물까지 지원하는지,
  아니면 별도 브로커가 필요한지)는 **지금 여기서 확정하지 않는다** — 02/06번
  문서에 "확인 필요(Draft)"로 남기고, 실제 해당 Adapter를 구현하는 시점
  (작업트리 6번, 해당 브로커 공식 API 문서 확인 후)에 확정한다. 이 원칙
  덕분에 브로커 조사가 늦어져도 코어 타입 설계는 지금 진행할 수 있다.

### 2. AssetClass 타입 체계 (01번에 반영)

```
AssetClass:
  CRYPTO            — 코인 현물/파생 (Bitget 등)
  KR_EQUITY         — 국내주식
  KR_ETF            — 국내 ETF
  KR_ETN            — 국내 ETN
  KR_FUTURES        — 국내 선물
  KR_OPTION         — 국내 옵션
  US_EQUITY         — 해외주식(미국 우선)
  US_ETF            — 해외 ETF
  US_ETN            — 해외 ETN
  OVERSEAS_FUTURES  — 해외 선물
  OVERSEAS_OPTION   — 해외 옵션(Draft — 사용자가 명시하지 않았으나 타입
                       체계 일관성을 위해 예약, 실제 지원은 미확정)
```

파생상품 특화 필드(옵션의 행사가/만기, 선물의 만기월/계약승수)는 `Order`/
`Position`에 **모두 Optional**로 추가한다 — 크립토/주식 현물 주문에는 항상
`None`. 별도 `Instrument` 참조 테이블은 지금 도입하지 않는다(Phase 1
과잉설계 방지, 17.9-A 원칙과 동일 정신) — 필요해지면(예: 옵션체인 대량 조회
성능 문제) 별도 라운드에서 재검토.

### 3. 영향받는 문서와 처리 방식

| 문서 | 처리 |
|---|---|
| 01_data_models | AssetClass/OptionType 엔티티 추가, Order/Position에 파생 필드 추가 |
| 02_exchange_adapter | `ExchangeCapability.asset_class: str` → `supported_asset_classes: list[AssetClass]`, Validator의 capability-gate 원칙 명시 |
| 04_db_schema | orders/positions에 nullable 컬럼 추가(asset_class, option_type, strike_price, expiry_date, contract_multiplier, underlying_symbol) |
| 06_mvp_scope | "지금 실제로 실거래하는 대상"과 "타입 체계가 지원하는 전체 범위"를 분리 서술 — Phase 1 실거래 대상 자체(Bitget 크립토)는 변경하지 않음, 단 KIS의 실거래 확장 범위(국내주식 등)는 재검토 대상으로 명시 |
| 10_task_tree | 데이터모델 섹션에 신규 리프 추가, DB 마이그레이션 섹션에 신규 컬럼 추가 리프 언급 |
| 13_multi_tenancy | 변경 없음 — `exchange_credentials.exchange`는 이미 자산군 무관 문자열 |
| 15_api_spec | 마켓플레이스 `asset_class` 필터 타입을 AssetClass enum으로 명시, 전략 생성 API에 파생 필드 예시 추가 |
| 16_backend_signatures | `StrategyCreateRequest`/마켓플레이스 검색 서비스에 파생 필드·AssetClass 타입 반영 |
| 17_frontend_architecture | `StrategyCreateRequest` TS 인터페이스 동기화 |

### 4. 지금 정하지 않는 것

- 해외선물/해외옵션을 실제로 어느 브로커로 연결할지(KIS 단독 vs 별도
  브로커) — 06번 §6.1에 "확인 필요"로 남김.
- 자산군별 리스크 정책(옵션 그릭스, 선물 증거금율 등) — `risk_policy.yaml`
  확장은 실제 해당 자산군 착수 시점에 별도 라운드로.
- 옵션체인/선물 캘린더 등 UI 특화 컴포넌트 — 17번 프론트엔드 문서는 지금은
  타입만 동기화하고 화면 설계는 해당 자산군 착수 시점에.

## 근거

사용자 지시(2026-08-28 세션): "코인, 국내주식, 해외주식(특히 미국증시),
국내 선물옵션과 ETN/ETF, 해외선물과 ETN/ETF 등 파생상품들도 모두
지원해야해" + "각 거래소별 API가 지원하는 모든 상품을 취급할 수 있어야해.
거래소API가 제공하는 모든 상품을 거래할 수 있도록 각 거래소API 구현시에
사전에 파악하고 연결될 수 있도록 AIOS에서는 기본적으로 다 구현되어야하고
거래소API가 지원하지 않을경우 비활성화되는 식으로 해."
