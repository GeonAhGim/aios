"""EM-19 (stop/stop-limit/OCO/trailing) D2 하한 증빙 보강.

DEEPEN(task-3112, depends_on task-1961) — `docs/audit/DEPTH_EM.md`(task-2731,
qa-2) #1751행이 원 리프(task-1751)를 D1로 판정하며 지적한 3개 결손을 채운다:
"failure-injection 없음(순수 트리거 판정 로직, I/O 없음); 수치 성능 단언 없음;
게이트 적색 재현 없음(task-1751은 기능 티켓 인용일 뿐 사고 재현 아님)".

`domain/order_types/{stop,stop_limit,oco,trailing}.py`는 설계상 I/O가 없는
순수 함수라(그 자체가 이 축의 불변식) 그 안에는 결정적 실패를 주입할 지점이
없다. 대신 이 트리거들이 실제로 흘러드는 유일한 I/O 경계는
`domain/venue_profile.py.assert_supported()`가 지키는
`application/submit_order.py`의 진입점이다(trigger_price/trailing_offset이
있으면 "아직 실행 경로에 배선되지 않았다"며 fail-closed로 거부 — task-1961이
`tests/unit/oms/test_venue_profile.py::test_assert_supported_rejects_trigger_price_not_wired`
로 이미 negative 테스트를 갖고 있다). 이 파일은 그 경계에서:

1. 실패 주입의 대안(어댑터/브로커 결함 시뮬레이션) — DB pool을 고장난
   브로커/DB 어댑터로 시뮬레이션해, 트리거 주문이 그 고장난 어댑터에
   도달하기도 전에 가드가 막는다는 것을 `acquire()` 호출 횟수로 증명한다.
2. 수치 성능 단언 — 스톱·트레일링·OCO 세 판정을 한 틱마다 순서대로 호출하는
   실거래 핫패스를 흉내내 p95 지연과 처리량을 단언한다.
3. 게이트 적색 재현 — `assert_supported`의 trigger 체크 한 줄만 몽키패치로
   제거해, 가드가 사라지면 스톱 주문이 검증을 그대로 통과해 실제로 어댑터
   경계(pool.acquire)까지 도달함을 before/after로 실행 증명한다(모듈
   docstring이 경고하는 "조용히 시장가로 나가는 I-10 위반"이 실제로 벌어지는
   지점).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import AssetClass, Currency
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.contracts.v1 import (
    EntityContext,
    Fund,
    LegalEntity,
    Portfolio,
    SubAccount,
)
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.errors import OrderValidationError
from src.services.oms.domain.order_types.oco import OcoGroup
from src.services.oms.domain.order_types.stop import is_stop_triggered
from src.services.oms.domain.order_types.trailing import (
    initial_trailing_state,
    is_trailing_triggered,
    update_trailing_stop,
)
from tests.performance.oms._fixtures import submit_profile, submit_registry
from tests.support.oms_outbox_fakes import allow_gate


def _trigger_command(tenant_id: UUID) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=tenant_id,
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        scope=scope,
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        trigger_price=Decimal("90"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=tenant_id,
        issued_at=datetime.now(timezone.utc),
    )


@dataclass
class _WorkingEntityRepo:
    """`verify_entity_context`(task-1925)를 실제로 통과시키는 가짜 저장소 —
    이 테스트들이 재는 것은 그 뒤에 있는 단일 I/O 경계(pool)이므로, 그
    앞단인 엔티티 조회는 정상 응답으로 시뮬레이션한다."""

    legal_entity_id: UUID
    fund_id: UUID
    portfolio_id: UUID

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        return LegalEntity(
            entity_id=entity_id, tenant_id=tenant_id, name="x", jurisdiction="US", region_tag="us"
        )

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        return Fund(
            fund_id=fund_id,
            entity_id=self.legal_entity_id,
            base_currency=Currency.USDT,
            inception=date(2020, 1, 1),
        )

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None:
        return Portfolio(portfolio_id=portfolio_id, fund_id=self.fund_id, venue_account_ref="acct")

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None:
        return SubAccount(
            sub_account_id=sub_account_id, portfolio_id=self.portfolio_id, owner_ref=tenant_id
        )


def _entity_context(tenant_id: UUID) -> EntityContext:
    return EntityContext(
        tenant_id=tenant_id,
        legal_entity_id=uuid4(),
        fund_id=uuid4(),
        portfolio_id=uuid4(),
        sub_account_id=uuid4(),
    )


class _PoisonedBrokerAdapterPool:
    """DB pool 대역 — `acquire()`가 불리면 브로커/DB 어댑터의 연결 장애를
    흉내낸다(순수 트리거 판정 로직에는 주입할 I/O가 없어, 그 대신 트리거
    명령이 실제로 흘러드는 유일한 어댑터 경계를 고장난 것으로 시뮬레이션).
    호출 횟수는 "이 고장난 어댑터에 도달했는가"를 그대로 증명한다."""

    def __init__(self) -> None:
        self.acquire_calls = 0

    def acquire(self) -> object:  # noqa: ANN201 - 테스트 대역, 호출되면 즉시 실패
        self.acquire_calls += 1
        raise ConnectionError("simulated broker/DB adapter connection drop")


# ---------------------------------------------------------------------------
# 1. failure injection 대안 — adapter/broker fault simulation (D2)
# ---------------------------------------------------------------------------


async def test_trigger_order_rejected_before_reaching_faulty_broker_adapter() -> None:
    tenant_id = uuid4()
    entity_context = _entity_context(tenant_id)
    entity_repo = _WorkingEntityRepo(
        legal_entity_id=entity_context.legal_entity_id,
        fund_id=entity_context.fund_id,
        portfolio_id=entity_context.portfolio_id,
    )
    pool = _PoisonedBrokerAdapterPool()

    with pytest.raises(OrderValidationError) as exc_info:
        await submit_order(
            _trigger_command(tenant_id),
            pool=pool,  # type: ignore[arg-type]
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=allow_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,  # type: ignore[arg-type]
        )

    assert exc_info.value.code == "OMS_VALIDATION_TRIGGER_ORDER_NOT_WIRED"
    assert pool.acquire_calls == 0, (
        "트리거 주문이 고장난 브로커/DB 어댑터에 도달했습니다 — "
        "fail-closed 가드가 그 앞에서 막아야 합니다."
    )


# ---------------------------------------------------------------------------
# 2. 수치 성능 단언(p95 지연 / 처리량) (D2)
# ---------------------------------------------------------------------------

_TICK_COUNT = 5_000
_P95_TARGET_MS = 1.0
_THROUGHPUT_TARGET_PER_SEC = 20_000.0


def test_stop_trailing_oco_tick_pipeline_perf_p95_and_throughput() -> None:
    """실거래 핫패스(틱마다 스톱·트레일링·OCO 판정을 순서대로 호출)를 흉내내
    p95 지연과 처리량을 단언한다 — 순수 함수라 결정적 실패는 주입할 수
    없지만, 이 세 판정이 매 틱마다 서버 지연을 지배하지 않는다는 상한은
    수치로 고정할 수 있다."""
    side = OrderSide.SELL
    trailing_state = initial_trailing_state(
        side=side, reference_price=Decimal("100"), trailing_offset=Decimal("2")
    )
    stop_trigger_price = Decimal("90")
    oco = OcoGroup()
    ticks = [Decimal("100") - (Decimal(i) * Decimal("0.001")) for i in range(_TICK_COUNT)]

    latencies_ms: list[float] = []
    for tick in ticks:
        started = time.perf_counter()
        trailing_state = update_trailing_stop(
            side=side, state=trailing_state, last_price=tick, trailing_offset=Decimal("2")
        )
        is_stop_triggered(side=side, trigger_price=stop_trigger_price, last_price=tick)
        is_trailing_triggered(side=side, state=trailing_state, last_price=tick)
        oco.try_trigger("a")
        latencies_ms.append((time.perf_counter() - started) * 1000.0)

    latencies_ms.sort()
    p95_ms = latencies_ms[int(len(latencies_ms) * 0.95)]
    throughput_per_sec = _TICK_COUNT / (sum(latencies_ms) / 1000.0)

    assert p95_ms < _P95_TARGET_MS, f"틱 파이프라인 p95({p95_ms:.4f}ms) > 목표({_P95_TARGET_MS}ms)"
    assert throughput_per_sec > _THROUGHPUT_TARGET_PER_SEC, (
        f"틱 파이프라인 처리량({throughput_per_sec:.0f}/s) < 목표"
        f"({_THROUGHPUT_TARGET_PER_SEC:.0f}/s)"
    )


# ---------------------------------------------------------------------------
# 3. 게이트 적색 재현 (D2) — task-1751(원 리프)/task-1961(negative 테스트를
#    추가한 QA)/task-2731(DEPTH_EM.md 감사)/task-3112(이 DEEPEN) 인용.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_trigger_guard_removal_lets_stop_order_reach_broker_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`venue_profile.assert_supported()`의 trigger_price/trailing_offset
    체크(task-1961의 `test_assert_supported_rejects_trigger_price_not_wired`가
    지키는 바로 그 줄, DEPTH_EM.md task-2731 #1751행이 "게이트 적색 재현
    없음"으로 지적)가 회귀로 사라지면 무슨 일이 일어나는지 before/after로
    실행 증명한다. 가드가 있으면 스톱 주문은 브로커/DB 어댑터에 전혀
    도달하지 않는다(OrderValidationError, acquire 0회). 정확히 그 한 줄만
    몽키패치로 무력화하면(나머지 검증은 원본 그대로) 같은 스톱 주문이 검증을
    통과해 실제로 어댑터 경계(pool.acquire)까지 도달한다 — 모듈 docstring이
    경고하는 "조용히 시장가로 나가는 I-10 위반"이 실행으로 재현되는 지점."""
    tenant_id = uuid4()
    entity_context = _entity_context(tenant_id)
    entity_repo = _WorkingEntityRepo(
        legal_entity_id=entity_context.legal_entity_id,
        fund_id=entity_context.fund_id,
        portfolio_id=entity_context.portfolio_id,
    )
    cmd = _trigger_command(tenant_id)

    # before: 가드 활성 — 어댑터에 도달하지 않는다.
    pool_before = _PoisonedBrokerAdapterPool()
    with pytest.raises(OrderValidationError):
        await submit_order(
            cmd,
            pool=pool_before,  # type: ignore[arg-type]
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=allow_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,  # type: ignore[arg-type]
        )
    assert pool_before.acquire_calls == 0

    # red repro: 회귀가 지울 수 있는 정확히 그 한 줄(trigger 체크)만 제거한다
    # — asset_class/order_type/tif 검증은 그대로 남긴다(다른 체크는 건재하다는
    # 통제를 위해).
    import src.services.oms.application.submit_order as target
    from src.services.oms.domain.venue_profile import VenueCapabilityProfile

    def _assert_supported_without_trigger_guard(
        profile: VenueCapabilityProfile, cmd: SubmitOrderCommand
    ) -> None:
        if cmd.asset_class not in profile.asset_classes:
            raise OrderValidationError("UNSUPPORTED_TYPE", "asset_class unsupported")
        if cmd.order_type not in profile.order_types:
            raise OrderValidationError("UNSUPPORTED_TYPE", "order_type unsupported")
        if cmd.time_in_force not in profile.time_in_force:
            raise OrderValidationError("UNSUPPORTED_TIF", "time_in_force unsupported")
        # trigger_price/trailing_offset 체크가 여기 있었지만 회귀로 지워졌다.

    monkeypatch.setattr(target, "assert_supported", _assert_supported_without_trigger_guard)

    pool_after = _PoisonedBrokerAdapterPool()
    with pytest.raises(ConnectionError):  # 가드 없이 실제 어댑터 경계까지 도달해 그 자리에서 실패
        await submit_order(
            cmd,
            pool=pool_after,  # type: ignore[arg-type]
            profile=submit_profile(),
            registry=submit_registry(),
            pre_submit_gate=allow_gate,
            entity_context=entity_context,
            entity_repo=entity_repo,  # type: ignore[arg-type]
        )
    assert pool_after.acquire_calls == 1, (
        "가드 제거 후에도 어댑터에 도달하지 못했습니다 — 이 재현이 실제로 "
        "그 가드를 우회했는지 다시 확인하세요."
    )
