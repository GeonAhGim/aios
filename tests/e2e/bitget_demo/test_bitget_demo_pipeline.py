"""L4-30 — Bitget 데모 계정 실키로 OMS `submit_order()` 파이프라인 전체를
왕복시켜 `paptrading` 스팟 유효성을 확정한다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-30
      ("Demo 왕복 1회, `paptrading` 스팟 유효성 확정")
Spec: docs/design/INVARIANTS.md I-09(주문 최종 ALLOW/DENY는 독립 권위 통과가
      전제) — 이 리프는 게이트 자체를 재구현하지 않고 실제
      `make_foundation_pre_submit_gate`를 통과시켜 파이프라인 배선을 그대로
      쓴다.

`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py`(task-2179/
2795)는 `BitgetAdapter`를 단독으로만 왕복시킨다 — 이 파일의 델타는 실
Postgres(TEST_DATABASE_URL) 위에서 `submit_order()`(OMS 제출 파이프라인,
claim -> 거래소 전송 -> DB 영속화 -> positions 정산)를 실제 Bitget 데모
어댑터와 함께 실행해, "OMS가 실제로 Bitget 데모에 스팟 주문을 낼 수 있다"는
더 넓은 계약을 검증하는 것이다.

이 파일의 모든 테스트는 `pytest.mark.live_demo`로만 실행된다(기본 CI
`addopts`가 `not live_demo`로 제외 — pyproject.toml). `BITGET_DEMO_API_KEY`/
`SECRET`/`PASSPHRASE`가 없으면 `demo_adapter` 픽스처가 사유를 출력하며
skip한다(빈 통과 아님) — 이 환경에는 실키가 없으므로 이번 실행은 skip으로
끝난다. `BITGET_SPOT_PROFILE.verified`를 `"LIVE_VERIFIED"`로 바꾸는 것은
이 테스트들이 실제로 전부 통과한 뒤 사람이 하는 별도 커밋이다(ADR-2026-09-
06-G §11 — 통과하지 않고 값만 바꾸는 것은 금지).

task-6522 (esc-ci-replay_verify.json bisect_culprit=9546feaca1f0): 각 테스트
시그니처는 `demo_adapter`를 `pool`보다 앞에 둔다 -- 같은 scope 픽스처는
시그니처 순서대로 setup되므로(의존성 없는 한), 자격증명이 없을 때
`demo_adapter`의 `skip_if_missing_demo_credentials()`가 먼저 발동해
`pool`(실 `DATABASE_URL`에 직접 붙는 `tests/e2e/conftest.py`의 asyncpg
pool, 재시도 없음)이 아예 열리지 않는다. 원래 순서(`pool` 먼저)는 자격증명
없는 이 환경에서도 테스트마다 매번 쓸모없이 실 커넥션을 열고 닫아,
`scripts/replay_verify.py`가 같은 `DATABASE_URL`에 거는 커넥션과 경합을
늘렸다(esc-ci-replay_verify.json이 이 파일을 도입한 커밋을 bisect_culprit
으로 지목한 근거) -- 재시도 예산을 더 키우는 대신 불필요한 커넥션 자체를
없앤다(DECISION_GUIDELINES B-2).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg
import pytest

from scripts import replay_verify
from src.core.exceptions import ExchangeAPIError
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from src.services.order_service.foundation_gate import make_foundation_pre_submit_gate
from src.services.order_service.submit import submit_order
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.execution_ownership.conftest import create_execution

pytestmark = pytest.mark.live_demo

_SYMBOL = "BTC/USDT"

# 실측(2026-09-08) 공개 심볼가보다 훨씬 낮게 잡아 체결 위험 없이 미체결
# 상태로 place/cancel을 왕복한다(venue_profile.py와 동일 관례).
_SAFE_PRICE = Decimal("10000")
_SAFE_QUANTITY = Decimal("0.0002")


def _order(execution_id: int, *, price: Decimal, quantity: Decimal, tag: str) -> Order:
    return Order(
        client_order_id=f"l4-30-e2e-{tag}-{os.urandom(4).hex()}",
        strategy_id="l4-30-e2e-demo",
        strategy_version="1.0.0",
        execution_id=execution_id,
        symbol=_SYMBOL,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=Money(amount=price, currency=Currency.USDT),
        status=OrderStatus.CREATED,
        asset_class=AssetClass.CRYPTO,
    )


async def test_submit_order_pipeline_roundtrip_confirms_paptrading_spot_validity(
    demo_adapter: BitgetAdapter, pool: asyncpg.Pool
) -> None:
    """happy path — DoD 본체: `submit_order()`가 실제 Bitget 데모에 스팟
    지정가 주문을 내고(place), DB에 그 결과가 영속화되며(get 동치),
    이후 어댑터로 직접 취소(cancel)해 계정에 미결 주문을 남기지 않는다.
    세 동작(place/get-equivalent/cancel)이 전부 성공해야
    "paptrading 스팟 유효성"이 확정된다(U1)."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _order(execution_id, price=_SAFE_PRICE, quantity=_SAFE_QUANTITY, tag="roundtrip")

    persisted = await submit_order(
        order, user_id=user_id, adapter=demo_adapter, pool=pool, pre_submit_gate=gate
    )

    assert persisted.exchange_order_id
    assert persisted.status == OrderStatus.SUBMITTED

    async with pool.acquire() as conn:
        db_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        db_exchange_order_id = await conn.fetchval(
            "SELECT exchange_order_id FROM orders WHERE client_order_id = $1",
            order.client_order_id,
        )
    assert db_status == "SUBMITTED"
    assert db_exchange_order_id == persisted.exchange_order_id

    cancelled = await demo_adapter.cancel_order(persisted.exchange_order_id)
    assert cancelled is True


async def test_tick_size_violation_fails_closed_no_position_written(
    demo_adapter: BitgetAdapter, pool: asyncpg.Pool
) -> None:
    """negative #1 — 틱사이즈 위반 주문은 거래소가 거부하고, `submit_order()`
    는 그 실패를 삼키지 않고 전파한다. claim 행은 UNKNOWN으로 귀결되고
    (§3.4 classify_submit_failure), positions에는 아무것도 안 남는다
    (fail-closed, INVARIANTS.md I-09 배선의 전제인 "거부는 흔적을 남기지
    않는다"와 동일한 종류)."""
    tick = BITGET_SPOT_PROFILE.price_tick[_SYMBOL]
    assert tick == Decimal("0.01"), "이 테스트는 실측 tick=0.01 가정 위에 서 있다."
    violating_price = _SAFE_PRICE + (tick / Decimal("100"))

    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _order(
        execution_id, price=violating_price, quantity=_SAFE_QUANTITY, tag="tick-violation"
    )

    with pytest.raises(ExchangeAPIError):
        await submit_order(
            order, user_id=user_id, adapter=demo_adapter, pool=pool, pre_submit_gate=gate
        )

    async with pool.acquire() as conn:
        db_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        position = await conn.fetchval(
            "SELECT 1 FROM positions WHERE execution_id = $1", execution_id
        )
    assert db_status == "UNKNOWN"
    assert position is None


async def test_below_min_notional_rejected_no_position_written(
    demo_adapter: BitgetAdapter, pool: asyncpg.Pool
) -> None:
    """negative #2 — `min_notional`(venue_profile.py 실측, $1) 미만 수량은
    거래소가 거부한다. tick 위반과 다른 별도의 거부 사유를 실제로
    재현해, `error_codes.py`가 매핑하지 못한 코드는 성공으로 위장되지
    않고 `ExchangeAPIError`로 승격된다는 계약을 다시 고정한다(U4)."""
    min_notional = BITGET_SPOT_PROFILE.min_notional[_SYMBOL]
    below_min_quantity = (min_notional / _SAFE_PRICE) / Decimal("100")
    assert below_min_quantity * _SAFE_PRICE < min_notional

    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _order(
        execution_id, price=_SAFE_PRICE, quantity=below_min_quantity, tag="below-min-notional"
    )

    with pytest.raises(ExchangeAPIError):
        await submit_order(
            order, user_id=user_id, adapter=demo_adapter, pool=pool, pre_submit_gate=gate
        )

    async with pool.acquire() as conn:
        db_status = await conn.fetchval(
            "SELECT status FROM orders WHERE client_order_id = $1", order.client_order_id
        )
        position = await conn.fetchval(
            "SELECT 1 FROM positions WHERE execution_id = $1", execution_id
        )
    assert db_status == "UNKNOWN"
    assert position is None


async def test_pipeline_write_replays_byte_identical(
    demo_adapter: BitgetAdapter, pool: asyncpg.Pool
) -> None:
    """D3 — `scripts/replay_verify.py`(FA-15)가 이 리프의 실제 쓰기를
    byte-identical로 재생할 수 있어야 한다(§5 D3 "passing `replay_verify`").
    `submit_order()`가 쓴 `order_events`만으로 orders 투영이 현재 테이블
    행과 일치하는지 확인한다 — 이 리프가 이벤트 없이 상태를 바꾸지
    않았다는 독립 증거."""
    user_id = await create_test_tenant(pool)
    execution_id = await create_execution(pool, user_id)
    gate = make_foundation_pre_submit_gate(pool, require_mandate=False)
    order = _order(execution_id, price=_SAFE_PRICE, quantity=_SAFE_QUANTITY, tag="replay")

    persisted = await submit_order(
        order, user_id=user_id, adapter=demo_adapter, pool=pool, pre_submit_gate=gate
    )
    assert persisted.exchange_order_id is not None
    await demo_adapter.cancel_order(persisted.exchange_order_id)

    as_of = datetime.now(timezone.utc) + timedelta(minutes=1)
    report = await replay_verify.verify(pool, as_of=as_of, hours=1)

    mismatched_keys = {diff.key for diff in report.mismatches}
    assert str(persisted.order_id) not in mismatched_keys, report.mismatches
