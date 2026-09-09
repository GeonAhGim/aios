"""L4-30 — Bitget 데모 계정 실키 왕복(place/cancel/get) + 잘못된 주문 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §8.6, §9 L4-30
      (ADR-2026-09-06-G §11 — "구체적 거부 입력" DoD 강화)

이 파일의 두 테스트는 `pytest.mark.live_demo`로만 실행된다(기본 CI
`addopts`가 `not live_demo`로 제외 — pyproject.toml). Bitget REST 서명은
ACCESS-KEY/ACCESS-SIGN/ACCESS-PASSPHRASE 3종을 요구하는데, 이 커밋
시점에는 `.env`에 `BITGET_API_KEY`/`BITGET_API_SECRET`만 등록돼 있고
`BITGET_API_PASSPHRASE`가 없다(task-2179 note) — 서명 자체가 불가능하므로
두 테스트 모두 `pytest.skip()`으로 정확한 사유를 출력한다(빈 통과 금지).
세 값이 모두 채워지면 이 파일을 고치지 않고도 Bitget 데모 계정에 대해
place/cancel/get 왕복과 틱사이즈 위반 거부를 실측한다.

`BITGET_SPOT_PROFILE.verified`(venue_profile.py)를 `"LIVE_VERIFIED"`로
바꾸는 것은 이 두 테스트가 **둘 다** 실제로 통과한 뒤 사람이 하는
별도 커밋이다 — 테스트가 스스로 소스를 고치지 않는다(§10 정직 표기,
ADR-2026-09-06-G §11 "통과하지 않고 값만 바꾸는 것은 금지").

레드팀 원칙(redaction) — 키 값은 어떤 assert 메시지·print·로그에도
보간하지 않는다. skip 사유는 "어떤 변수가 없는지"만 말하지, 값은 절대
말하지 않는다.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest

from src.core.exceptions import ExchangeAPIError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE

pytestmark = pytest.mark.live_demo

_SYMBOL = "BTC/USDT"
_CREDENTIAL_ENV_VARS = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE")

# 왕복용 지정가 — 실측(2026-09-08) 공개 심볼가보다 훨씬 낮게 잡아 체결
# 위험 없이 미체결 상태로 place/get/cancel을 왕복한다. 수량은 min_notional
# ($1, venue_profile.py 실측)을 넉넉히 넘긴다.
_SAFE_PRICE = Decimal("10000")
_SAFE_QUANTITY = Decimal("0.0002")


def _missing_credential_env_vars() -> list[str]:
    return [name for name in _CREDENTIAL_ENV_VARS if not os.environ.get(name)]


@pytest.fixture
async def demo_adapter() -> BitgetAdapter:
    missing = _missing_credential_env_vars()
    if missing:
        pytest.skip(
            "Bitget 데모 왕복 테스트 skip — 누락된 환경변수: "
            f"{', '.join(missing)} (값 자체는 절대 출력하지 않음, redaction)"
        )
    adapter = BitgetAdapter(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        demo_mode=True,
    )
    await adapter.sync_server_time()
    try:
        yield adapter
    finally:
        await adapter.aclose()


def _order(*, price: Decimal, quantity: Decimal = _SAFE_QUANTITY, tag: str) -> Order:
    return Order(
        client_order_id=f"l4-30-{tag}-{os.urandom(4).hex()}",
        strategy_id="l4-30-live-demo",
        strategy_version="v1",
        symbol=_SYMBOL,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.USDT),
        asset_class=AssetClass.CRYPTO,
    )


async def test_get_balance_succeeds_regardless_of_account_mode(
    demo_adapter: BitgetAdapter,
) -> None:
    """L4-31(task-2514) DoD 3 — 이 계정이 Classic이든 UTA(Unified)든 잔고
    조회가 성공해야 한다. `account_aware_request()`(account_mode.py)가
    40085를 관측하면 자동으로 UNIFIED로 전환해 v3로 재시도하므로, 이
    테스트는 계정 모드를 미리 알 필요가 없다 — 두 모드 중 어느 쪽이든
    예외 없이 리스트가 나오면 통과다."""
    balances = await demo_adapter.get_balance()
    assert isinstance(balances, list)


async def test_place_get_cancel_roundtrip(demo_adapter: BitgetAdapter) -> None:
    """DoD 1/2 — place/get/cancel 왕복이 Bitget 데모에서 성공."""
    placed = await demo_adapter.place_order(_order(price=_SAFE_PRICE, tag="roundtrip"))
    assert placed.exchange_order_id
    assert placed.status == OrderStatus.SUBMITTED

    fetched = await demo_adapter.get_order(placed.exchange_order_id)
    assert fetched.exchange_order_id == placed.exchange_order_id
    assert fetched.status != OrderStatus.UNKNOWN

    cancelled = await demo_adapter.cancel_order(placed.exchange_order_id)
    assert cancelled is True


async def test_tick_size_violation_rejected_with_mapped_error(
    demo_adapter: BitgetAdapter,
) -> None:
    """DoD 3 — 틱사이즈(가격 최소 단위) 위반 주문이 거부된다(ADR-2026-09-06-G
    §11의 "구체적 거부 입력"). `BITGET_SPOT_PROFILE.price_tick["BTC/USDT"]`
    (0.01, 실측)보다 세밀한 소수 자리를 넣어 위반시킨다. 응답이 `"00000"`이
    아니면 `_classify_body`가 예외로 승격시킨다(UNKNOWN_RESPONSE로 새는
    비JSON 응답이 아닌 이상 `ExchangeAPIError`의 서브클래스) — 조용히
    성공 처리되면 안 된다."""
    tick = BITGET_SPOT_PROFILE.price_tick[_SYMBOL]
    assert tick == Decimal("0.01"), "이 테스트는 실측 tick=0.01 가정 위에 서 있다."
    violating_price = _SAFE_PRICE + (tick / Decimal("100"))  # 0.0001 단위 위반

    with pytest.raises(ExchangeAPIError) as exc_info:
        await demo_adapter.place_order(_order(price=violating_price, tag="tick-violation"))

    # 미확인 사실(U4)을 성공으로 위장하지 않는다 — 예외 메시지에 Bitget
    # 원본 응답(code/msg)이 그대로 담겨 있어야 사람이 읽고 error_codes.py의
    # classify_body_code 표를 확장할 수 있다(§10 U4 "실키 후 확장").
    assert str(exc_info.value).strip()
