"""DROP(응답 유실) 주입 → UNKNOWN 종단 적대적 테스트(L4-23, §6 F17).

task-1605 note DoD(3) — 예외를 삼키지 않고 `SentUnknownError`로 종단하되,
venue측 원장(`paper_sim_orders`)은 실제 처리 결과를 그대로 보존해
`find_order_by_client_id`/`get_order`로 재조회 가능해야 한다
(L4-16 `unknown_resolver`가 나중에 쓸 경로). DoD(2) — LIVE 요청은 이
어댑터가 아니라 방어선(`require_paper_sandbox`)이 실제로 차단하는지도
여기서 증명한다(defense-in-depth, is_paper_trading/is_sandboxed는 상수라
정상 경로로는 절대 LIVE가 될 수 없으므로 서브클래스로 값을 뒤집어 확인).

DEPTH 감사(task-2722, docs/audit/DEPTH_L4_BR.md #1605)는 원 커밋(0db9a9b)을
D1로 판정했다 — negative=4, failure-injection·adversarial guard-bypass는
이미 이 파일에 있으나 게이트/CI 적색선 회귀 테스트가 없었다(D2 하한
미달). 파일 끝의 `test_pytest_gate_turns_red_when_drop_raise_branch_is_
removed`가 그 한 항목만 보강한다 — `test_submit_order_failure_
injection.py`(task-2765)와 동일 기법(자식 pytest 프로세스 안에서만 소스를
바꿔치기)으로, `place_order`의 `if outcome.kind == "DROP": raise
SentUnknownError(...)` 분기를 제거하면 위
`test_drop_injection_raises_but_ledger_holds_real_terminal_state`가
green(1 passed)에서 red(1 failed)로 뒤집힘을 증명한다 — 즉 그 테스트의
주장이 실제로 이 분기에 의존한다는 뜻이다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from src.core.exceptions import FrozenZonePaperAdapterBlockedError
from src.data.models.base import AssetClass, Currency
from src.data.models.trading import Order, OrderSide, OrderStatus, OrderType
from src.exchanges.paper.fee_model import FeeModel
from src.exchanges.paper.fill_model import FillModel
from src.exchanges.paper.latency_model import LatencyModel
from src.exchanges.paper.ledger_repository import PaperLedgerRepository
from src.exchanges.paper.simulator_adapter import PaperSimulatorAdapter, SentUnknownError
from tests.support.paper_sim_fakes import FakeReferenceAdapter, SeqRandom, fixed_adv, instant_sleep

_NO_SLIPPAGE_FILL = FillModel(
    spread_bps=Decimal("0"),
    impact_bps_per_pct_adv=Decimal("0"),
    partial_fill_prob=0.0,
    partial_min_pct=Decimal("100"),
)
_TAKER_FEE = FeeModel(maker_bps=Decimal("0"), taker_bps=Decimal("10"), fee_currency=Currency.USDT)
_ALWAYS_DROP_LATENCY = LatencyModel(ack_ms_p50=0, ack_ms_p99=0, drop_response_prob=1.0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_order(client_order_id: str) -> Order:
    return Order(
        client_order_id=client_order_id,
        strategy_id="strat",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="paper_sim",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        asset_class=AssetClass.CRYPTO,
    )


def _make_adapter(pool, account_id) -> PaperSimulatorAdapter:
    reference = FakeReferenceAdapter(bid=Decimal("29990"), ask=Decimal("30000"))
    return PaperSimulatorAdapter(
        reference,
        PaperLedgerRepository(),
        _NO_SLIPPAGE_FILL,
        _TAKER_FEE,
        _ALWAYS_DROP_LATENCY,
        clock=_now,
        rng=SeqRandom([0.0, 0.0]),  # u=0(지연 0), drop 판정 0.0 < 1.0 → 항상 DROP
        account_id=account_id,
        pool=pool,
        adv_provider=fixed_adv,
        sleeper=instant_sleep,
    )


async def test_drop_injection_raises_but_ledger_holds_real_terminal_state(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    async with pool.acquire() as conn:
        await PaperLedgerRepository().deposit(conn, account_id, "USDT", Decimal("100000"))

    order = _make_order("drop-1")
    with pytest.raises(SentUnknownError) as exc_info:
        await adapter.place_order(order)
    assert exc_info.value.client_order_id == "drop-1"

    # 응답은 못 받았지만 venue 진실은 이미 커밋돼 있다 — unknown_resolver 몫.
    resolved = await adapter.find_order_by_client_id("drop-1")
    assert resolved is not None
    assert resolved.status is OrderStatus.FILLED
    assert resolved.filled_quantity == Decimal("1")

    balances = {b.asset: b for b in await adapter.get_balance()}
    assert balances["BTC"].total == Decimal("1")
    assert balances["USDT"].available == Decimal("69970")


async def test_drop_then_resend_same_client_order_id_does_not_double_fill(pool) -> None:
    account_id = uuid4()
    adapter = _make_adapter(pool, account_id)
    async with pool.acquire() as conn:
        await PaperLedgerRepository().deposit(conn, account_id, "USDT", Decimal("100000"))

    order = _make_order("drop-2")
    with pytest.raises(SentUnknownError):
        await adapter.place_order(order)

    # OMS가 UNKNOWN을 해소하기 전에 순진하게 재전송해도(§6 F17) 원장은 1행 그대로.
    resend_result = await adapter.place_order(order)
    assert resend_result.status is OrderStatus.FILLED
    assert resend_result.filled_quantity == Decimal("1")

    balances = {b.asset: b for b in await adapter.get_balance()}
    assert balances["USDT"].available == Decimal("69970")  # 두 번 안 깎임


class _LiveConfiguredPaperAdapter(PaperSimulatorAdapter):
    """방어 심화 검증 전용 — 정상 경로로는 절대 만들어질 수 없는 상태
    (`is_paper_trading=False`)를 강제로 재현해 `require_paper_sandbox`가
    실제로 place_order를 막는지 확인한다."""

    @property
    def is_paper_trading(self) -> bool:
        return False


async def test_live_configured_subclass_is_blocked_by_defense_in_depth_guard(pool) -> None:
    account_id = uuid4()
    reference = FakeReferenceAdapter(bid=Decimal("29990"), ask=Decimal("30000"))
    adapter = _LiveConfiguredPaperAdapter(
        reference,
        PaperLedgerRepository(),
        _NO_SLIPPAGE_FILL,
        _TAKER_FEE,
        _ALWAYS_DROP_LATENCY,
        clock=_now,
        rng=SeqRandom([0.0, 0.0]),
        account_id=account_id,
        pool=pool,
        adv_provider=fixed_adv,
        sleeper=instant_sleep,
    )
    async with pool.acquire() as conn:
        await PaperLedgerRepository().deposit(conn, account_id, "USDT", Decimal("100000"))

    with pytest.raises(FrozenZonePaperAdapterBlockedError):
        await adapter.place_order(_make_order("blocked-1"))

    # 거부됐으므로 주문행 자체가 없어야 한다.
    assert await adapter.find_order_by_client_id("blocked-1") is None


# ---- D2: gate/CI red-line regression ---------------------------------------

_GUARD = (
    '        if outcome.kind == "DROP":\n'
    "            raise SentUnknownError(order.client_order_id, row.order_id)\n"
    "        return self._row_to_order(row)\n"
)
_MUTATED = "        return self._row_to_order(row)\n"


def _plugin_source() -> str:
    return f"""\
import importlib
from pathlib import Path


def pytest_configure(config):
    module = importlib.import_module("src.exchanges.paper.simulator_adapter")
    source = Path(module.__file__).read_text(encoding="utf-8")
    guard = {_GUARD!r}
    assert source.count(guard) == 1
    mutated_src = source.replace(guard, {_MUTATED!r})
    mutant = compile(mutated_src, module.__file__, "exec")
    exec(mutant, module.__dict__)
"""


def test_pytest_gate_turns_red_when_drop_raise_branch_is_removed(tmp_path: Path) -> None:
    """`place_order`의 `if outcome.kind == "DROP": raise SentUnknownError(...)`
    분기를 자식 pytest 프로세스 안에서만 제거하면(프로덕션 소스는 그대로),
    `test_drop_injection_raises_but_ledger_holds_real_terminal_state`가
    green(1 passed)에서 red(1 failed)로 뒤집힌다는 것을 증명한다 — 그 테스트가
    장식이 아니라 실제로 이 분기에 의존한다는 뜻이다."""
    target_test = (
        "tests/adversarial/exchanges/test_paper_drop_injection.py::"
        "test_drop_injection_raises_but_ledger_holds_real_terminal_state"
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target_test]
    repo_root = str(Path.cwd())
    env = dict(os.environ, PYTHONPATH=repo_root, PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8")

    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=120, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    plugin_module_name = "_mutate_paper_drop_raise_gate"
    plugin = tmp_path / f"{plugin_module_name}.py"
    plugin.write_text(_plugin_source(), encoding="utf-8")
    mutated_env = dict(env, PYTHONPATH=f"{repo_root}{os.pathsep}{tmp_path}")

    mutated = subprocess.run(
        [*command[:-1], "-p", plugin_module_name, command[-1]],
        capture_output=True, encoding="utf-8", errors="replace",
        env=mutated_env, timeout=120, check=False,
    )
    assert mutated.returncode != 0, mutated.stdout + mutated.stderr
    assert "1 passed" not in mutated.stdout
    assert "1 failed" in mutated.stdout
