"""L4-30 — Bitget 스팟 capability 프로파일 상수.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-E, §9 L4-30
      (ADR-2026-09-06-G §11 — "구체적 거부 입력" DoD 강화)

값의 출처(정직 표기, §10 원칙):
- `price_tick`/`qty_lot`/`min_notional`/`max_open_orders_per_symbol`:
  2026-09-08 `GET /api/v2/spot/public/symbols?symbol=<BTC|ETH>USDT`
  **실측**(공개 엔드포인트 — 인증 불필요, `pricePrecision`/`quantityPrecision`
  자릿수를 10의 음수 거듭제곱으로, `minTradeUSDT`/`orderQuantity`를 그대로
  옮김). 이 값들은 place/cancel/get 왕복과 별개로 이미 라이브로 확인됐다.
- `client_order_id_max_len=40`: **미확인**(U2, 04번 §11.3) — 문서 근거만.
- `rate_limits`: **미확인** — 공식 한도 문서를 찾지 못해 보수적 추정치.
- `supports_ws_orders=False`: U3(02b §6 자인) — private WS `orders` 채널
  로그인 서명 방식이 라이브 미검증이라 기본은 REST 폴링 경로로 fail-closed.
- `time_in_force`: Bitget 스팟 `force` 파라미터 공식 값(gtc/ioc/fok) 중
  AIOS 계약(`SubmitOrderCommand.time_in_force`)과 겹치는 부분만 선언.

`verified`는 이 셋 중 **가장 약한 근거**를 따른다 — place/cancel/get 왕복과
잘못된 주문 거부가 Bitget 데모에서 **둘 다** 성공하기 전까지는
`"DOC_ONLY"`를 유지한다(BITGET_API_PASSPHRASE 부재로 이 커밋 시점에는
미검증 — `tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`
docstring 참고). 두 DoD가 실제로 통과한 커밋에서만 `"LIVE_VERIFIED"`로
갱신한다 — 통과하지 않고 값만 바꾸는 것은 금지(ADR-2026-09-06-G §11).
"""
from __future__ import annotations

from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import OrderType
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile

BITGET_SPOT_PROFILE = VenueCapabilityProfile(
    venue="bitget",
    asset_classes=[AssetClass.CRYPTO],
    order_types={OrderType.MARKET, OrderType.LIMIT},
    time_in_force={"GTC", "IOC", "FOK"},
    supports_client_order_id=True,
    client_order_id_max_len=40,
    client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    id_policy="STABLE",
    supports_modify=True,
    supports_cancel="YES",
    supports_ws_orders=False,
    supports_batch=True,
    price_tick={"BTC/USDT": Decimal("0.01"), "ETH/USDT": Decimal("0.01")},
    qty_lot={"BTC/USDT": Decimal("0.000001"), "ETH/USDT": Decimal("0.0001")},
    min_notional={"BTC/USDT": Decimal("1"), "ETH/USDT": Decimal("1")},
    rate_limits={"order": (5, 10), "query": (10, 20)},
    submit_timeout=TimeoutBudget(),
    query_timeout=TimeoutBudget(),
    market_hours=None,
    max_open_orders_per_symbol=200,
    verified="DOC_ONLY",
)
