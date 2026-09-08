"""L4-29(task-2180) 적대적 1/2 — LIVE 모드 우회 3경로가 전부 차단되는지 실증한다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-29. 선행 L4-23
(paper 시뮬레이터, `is_sandboxed=True` 상수)·L4-15(`inbox_processor.py`)는
`test_tampered_provider_event.py`가 다룬다 — 이 파일은 "LIVE 실주문이 나가는가"만
본다.

3경로:
  (1) `strategy_executions.mode`가 LIVE인 실행을 `Executor.execute(mode="LIVE")`로
      제출 — `mode != "PAPER"` 하드 차단(ADR-2026-08-29-E).
  (2) Executor를 거치지 않고 거래소 어댑터의 확장 메서드(`place_order` 등)를
      직접 호출 — `@require_paper_sandbox`(live_guard.py)가 demo_mode=False로
      구성된 실 어댑터(BitgetAdapter)에서 막아야 한다.
  (3) `is_paper_trading`/`is_sandboxed`를 True로 위조한 가짜 어댑터를 LIVE
      실행에 주입 — Executor의 mode 검사는 adapter 객체의 자기신고를 전혀
      참조하지 않는 독립 신호라, 어댑터가 무엇을 자칭하든 mode="LIVE"면 여전히
      막혀야 한다(방어 심층화).

각 경로 뒤에는 "배선증명"(ADR-2026-09-06-G §1 — 한 번도 실패한 적 없는 게이트는
안 돌고 있는 것이다) 짝 테스트를 둔다. (1)·(3)은 Executor.execute() 안의 `if
mode != "PAPER": raise ...`가 별도 심볼 없는 인라인 조건이라 몽키패치로 무력화할
수 없다 — 대신 그 검사가 유일하게 읽는 입력(mode)만 바꿔 같은 시나리오가 성공으로
뒤집힘을 보여 "이 예외가 실제로 그 검사에서 나온다"를 증명한다(우연한 다른 실패가
아님). (2)는 `require_paper_sandbox`가 참조하는 `is_paper_trading`/`is_sandboxed`
프로퍼티를 몽키패치로 True 고정하면 진짜로 HTTP 요청이 나간다는 것을 성공 응답
mock으로 실증한다 — 이게 이 데코레이터의 한계(자기신고만 본다)이기도 하고, 그래서
(3)의 독립적인 mode 검사가 필요한 이유이기도 하다.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import httpx
import pytest
from dotenv import dotenv_values

from src.core.exceptions import FrozenZoneLiveModeBlockedError, FrozenZonePaperAdapterBlockedError
from src.core.executor.executor import Executor
from src.core.portfolio.models import AllocationDecision
from src.core.risk.models import RiskCheckResult
from src.data.models.base import AssetClass
from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import Order, OrderSide, OrderType
from src.exchanges.bitget.adapter import BitgetAdapter
from src.services.condition_compiler import ConditionCompiler
from src.services.order_service.gate import GateDecision, GateOutcome
from src.services.preview_service import PreviewCondition
from tests.integration.conftest import create_test_tenant
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


async def _allow_gate(context: object) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


def _fsm_config():
    return ConditionCompiler().compile(
        strategy_id="strat-live-bypass-test",
        version="1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        author_agent="test",
        entry_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=30.0)],
        exit_conditions=[PreviewCondition(indicator="RSI", operator=">", threshold=70.0)],
        stop_loss_conditions=[PreviewCondition(indicator="RSI", operator="<", threshold=10.0)],
    )


async def _seed_execution(pool: asyncpg.Pool, user_id: uuid.UUID, *, mode: str) -> int:
    strategy_id = f"live-bypass-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status, fsm_state)
            VALUES ($1, '1.0.0', $2, 'bitget', $3, 100, 'USDT', 'RUNNING', 'BUY_ORDER_PENDING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            mode,
        )
    return row["id"]


def _approved_risk_result() -> RiskCheckResult:
    return RiskCheckResult(approved=True, rejection_reason=None, checked_rules=["daily_loss"])


def _allocation() -> AllocationDecision:
    return AllocationDecision(
        symbol="BTC/USDT",
        strategy_id="strat-live-bypass-test",
        approved_quantity=Decimal("0.01"),
        capital_pct=Decimal("10"),
    )


async def _noop_writer(execution_id: int, expected: FSMState, new: FSMState) -> None:
    raise AssertionError(
        "이 파일의 테스트는 거부/성공 여부만 확인한다 — fsm_state_writer가 호출됐다면 "
        "차단이 실제로는 실패해 체결까지 간 것이므로 그 자체가 결함이다."
    )


async def _execute(
    pool: asyncpg.Pool, execution_id: int, user_id: uuid.UUID, *, mode: str, adapter
):
    return await Executor().execute(
        _allocation(),
        _approved_risk_result(),
        adapter,
        execution_id=execution_id,
        user_id=user_id,
        strategy_version="1.0.0",
        mode=mode,
        side=OrderSide.BUY,
        pending_fsm_state=FSMState.BUY_ORDER_PENDING,
        fsm_config=_fsm_config(),
        fsm_state_writer=_noop_writer,
        pool=pool,
        pre_submit_gate=_allow_gate,
    )


# ---------------------------------------------------------------------------
# 경로 (1) — RUNTIME_MODE(strategy_executions.mode)를 LIVE로 두고 제출 시도.
# ---------------------------------------------------------------------------

async def test_path1_live_mode_execution_is_hard_blocked_before_any_order(pool):
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id, mode="LIVE")
    adapter = FakeExchangeAdapter()

    with pytest.raises(FrozenZoneLiveModeBlockedError):
        await _execute(pool, execution_id, user_id, mode="LIVE", adapter=adapter)

    assert adapter.place_order_call_count == 0


async def test_path1_wiring_proof_same_adapter_succeeds_when_mode_is_paper(pool):
    """배선증명 — mode 인자 하나만 "PAPER"로 바꾸면(가드가 읽는 유일한 입력)
    같은 어댑터·같은 실행 흐름이 실제로 체결까지 간다. 즉 경로 (1)의 차단은
    이 mode 값에서 나온다(우연한 TypeError 등 다른 이유가 아니다)."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id, mode="PAPER")
    adapter = FakeExchangeAdapter()

    result = await _execute(pool, execution_id, user_id, mode="PAPER", adapter=adapter)

    assert adapter.place_order_call_count == 1
    assert result.status is not None  # 실제로 제출까지 진행됐다(예외 없음)


# ---------------------------------------------------------------------------
# 경로 (2) — Executor를 거치지 않고 거래소 어댑터 확장 메서드를 직접 호출.
# ---------------------------------------------------------------------------

def _order() -> Order:
    return Order(
        client_order_id="live-bypass-c-1",
        strategy_id="s-1",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
    )


def _blocked_transport() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("가드가 막았어야 할 요청이 실제로 나갔습니다.")

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)


def _accepting_transport() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"code": "00000", "data": {"orderId": "leaked-live-order-1"}}
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(base_url="https://api.bitget.com", transport=transport)


async def test_path2_extended_adapter_method_bypassing_executor_is_blocked():
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=_blocked_transport()
    )

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await live_adapter.place_order(_order())


async def test_path2_wiring_proof_disabling_is_paper_trading_lets_request_through(monkeypatch):
    """배선증명 — `require_paper_sandbox`가 읽는 신호(`is_paper_trading`/
    `is_sandboxed`)를 몽키패치로 True 고정하면, demo_mode=False로 구성된 실
    adapter가 실제로 HTTP 요청을 내보낸다(성공 응답 mock으로 확인). 즉 경로
    (2)의 차단은 이 두 프로퍼티 확인에서 나온다 — 동시에 이 데코레이터가
    adapter의 자기신고만 본다는 한계도 보여준다(경로 (3)의 독립 mode 검사가
    필요한 이유)."""
    monkeypatch.setattr(BitgetAdapter, "is_paper_trading", property(lambda self: True))
    monkeypatch.setattr(BitgetAdapter, "is_sandboxed", property(lambda self: True))
    live_adapter = BitgetAdapter(
        "key", "secret", "passphrase", demo_mode=False, http_client=_accepting_transport()
    )

    result = await live_adapter.place_order(_order())

    assert result.exchange_order_id == "leaked-live-order-1"


# ---------------------------------------------------------------------------
# 경로 (3) — is_paper_trading/is_sandboxed를 True로 위조한 가짜 어댑터 주입.
# ---------------------------------------------------------------------------

async def test_path3_fake_adapter_spoofing_paper_flags_is_still_blocked_by_live_mode(pool):
    """공격자가 어댑터 객체를 완전히 통제해 `is_paper_trading=True`/
    `is_sandboxed=True`를 자칭해도(경로 (2)의 데코레이터라면 속을 조합),
    `strategy_executions.mode`가 실제로 LIVE인 이상 Executor의 mode 검사는
    adapter를 참조조차 하지 않으므로 여전히 차단된다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id, mode="LIVE")
    spoofed_adapter = FakeExchangeAdapter(is_paper_trading=True, is_sandboxed=True)

    with pytest.raises(FrozenZoneLiveModeBlockedError):
        await _execute(pool, execution_id, user_id, mode="LIVE", adapter=spoofed_adapter)

    assert spoofed_adapter.place_order_call_count == 0


async def test_path3_wiring_proof_same_spoofed_adapter_succeeds_when_mode_is_paper(pool):
    """배선증명 — 위조된 신호(is_paper_trading/is_sandboxed=True)는 그대로 두고
    mode만 "PAPER"로 바꾸면(정직한 실행이라면) 그대로 체결까지 간다. 즉 위
    테스트의 차단은 mode="LIVE" 검사에서 나온 것이지, adapter의 신고값과는
    무관하다 — 신고값이 정직(True/True)해도 mode가 LIVE면 막히고 정직한 mode
    (PAPER)에서는 신고값과 무관하게 통과한다는 것을 대조로 보여준다."""
    user_id = await create_test_tenant(pool)
    execution_id = await _seed_execution(pool, user_id, mode="PAPER")
    spoofed_adapter = FakeExchangeAdapter(is_paper_trading=True, is_sandboxed=True)

    result = await _execute(pool, execution_id, user_id, mode="PAPER", adapter=spoofed_adapter)

    assert spoofed_adapter.place_order_call_count == 1
    assert result.status is not None
