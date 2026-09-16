"""scripts/canary_bitget.py 단위 테스트 — task-2750 L4-30b.

D2 증빙:
- negative >= 3: below_min_notional / exceeds_max_order_usdt /
  exceeds_max_total_usdt(누적) / max_fills_reached / symbol_outside_whitelist /
  하드가드 미해제 / kill switch ACTIVE / canary.yaml 스키마 위반, 8건.
- 실패주입 1건: `assert_hard_guard_released`가 마일스톤 파일 없을 때 예외.
- 성능 단언 1건: `evaluate_pretrade_gate` 10,000회 호출이 예산 안에 끝남
  (순수 함수 — 결정론적 규칙만 평가하므로 상수 예산이면 충분, ADR-2026-08-29-E
  설계 제약 1).
- 레드게이트 재현 1건: `submit_with_gate`가 REJECT일 때 mock 어댑터의
  `place_order`가 **한 번도 호출되지 않음**을 스파이로 증명 — 이 어설션이
  깨지면(즉 게이트를 없애거나 우회하면) 상한 초과 주문이 거래소로 나간다는
  뜻이므로, 이 테스트 자체가 "게이트가 빨간불일 때를 재현"하는 회귀 방지선.

전부 합성 데이터 + mock 어댑터로 검증한다 — 네트워크 접근 없음, 실계좌 키
불필요(모듈 docstring의 redaction 원칙 그대로 유지).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from types import ModuleType
from uuid import UUID, uuid4

import pytest

from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.domain.models import SafetyControl, SafetyControlState, SafetyScope

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


canary = _load_module("canary_bitget", SCRIPTS_DIR / "canary_bitget.py")


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "canary.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_VALID_CONFIG = """
name: canary-bitget-v1
version: 1
max_order_usdt: 10
max_total_usdt: 50
symbols: [BTCUSDT]
max_fills: 2
stop_on_daily_loss: true
"""


# ---------------------------------------------------------------------------
# load_canary_limits — 스키마/정규화
# ---------------------------------------------------------------------------


def test_load_canary_limits_normalizes_native_symbol_to_canonical(tmp_path: Path) -> None:
    path = _write_config(tmp_path, _VALID_CONFIG)
    limits = canary.load_canary_limits(path)
    assert limits.symbols == frozenset({"BTC/USDT"})
    assert limits.max_order_usdt == Decimal("10")
    assert limits.max_total_usdt == Decimal("50")
    assert limits.max_fills == 2


def test_load_canary_limits_rejects_unknown_key_fail_closed(tmp_path: Path) -> None:
    """negative #1 — extra="forbid": 오타 키가 조용히 무시되지 않는다."""
    path = _write_config(tmp_path, _VALID_CONFIG + "\ntypo_field: 1\n")
    with pytest.raises(canary.CanaryConfigError):
        canary.load_canary_limits(path)


def test_load_canary_limits_rejects_non_positive_max_order_usdt(tmp_path: Path) -> None:
    """negative #2 — 상한 값 자체가 0 이하면 설정 로드 단계에서 거부."""
    body = _VALID_CONFIG.replace("max_order_usdt: 10", "max_order_usdt: 0")
    path = _write_config(tmp_path, body)
    with pytest.raises(canary.CanaryConfigError):
        canary.load_canary_limits(path)


# ---------------------------------------------------------------------------
# evaluate_pretrade_gate — DoD 4 거부 케이스
# ---------------------------------------------------------------------------


def _limits(**overrides: object) -> canary.CanaryLimits:
    defaults = dict(
        name="canary-bitget-v1",
        max_order_usdt=Decimal("10"),
        max_total_usdt=Decimal("50"),
        symbols=frozenset({"BTC/USDT"}),
        max_fills=2,
        stop_on_daily_loss=True,
    )
    defaults.update(overrides)
    return canary.CanaryLimits(**defaults)


def test_gate_rejects_symbol_outside_whitelist() -> None:
    """negative #3 — DoD 4: 화이트리스트 밖 심볼."""
    decision = canary.evaluate_pretrade_gate(
        symbol="ETH/USDT",
        notional_usdt=Decimal("5"),
        limits=_limits(),
        state=canary.CanarySessionState(),
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "whitelist" in decision.reason


def test_gate_rejects_below_min_notional() -> None:
    """negative #4 — DoD 4: 최소 수량(명목가) 미만."""
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("0.1"),
        limits=_limits(),
        state=canary.CanarySessionState(),
        min_notional_usdt=Decimal("1"),
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "min_notional" in decision.reason


def test_gate_rejects_order_exceeding_max_order_usdt() -> None:
    """negative #5 — DoD 4: 주문 1건 상한 초과."""
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("11"),
        limits=_limits(max_order_usdt=Decimal("10")),
        state=canary.CanarySessionState(),
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "max_order_usdt" in decision.reason


def test_gate_rejects_when_cumulative_exceeds_max_total_usdt() -> None:
    """negative #6 — 누적 상한(세션 전체 50 USDT) 초과."""
    state = canary.CanarySessionState(cumulative_usdt=Decimal("48"))
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("5"),
        limits=_limits(max_total_usdt=Decimal("50")),
        state=state,
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "max_total_usdt" in decision.reason


def test_gate_rejects_when_max_fills_reached() -> None:
    """negative #7 — 세션 최대 체결 횟수 도달."""
    state = canary.CanarySessionState(fills=2)
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("5"),
        limits=_limits(max_fills=2),
        state=state,
    )
    assert decision.verdict is canary.GateVerdict.REJECT
    assert "max_fills" in decision.reason


def test_gate_allows_when_within_all_limits() -> None:
    decision = canary.evaluate_pretrade_gate(
        symbol="BTC/USDT",
        notional_usdt=Decimal("5"),
        limits=_limits(),
        state=canary.CanarySessionState(),
    )
    assert decision.verdict is canary.GateVerdict.ALLOW


def test_build_rejection_fixtures_all_reject(tmp_path: Path) -> None:
    """DoD 4 — 리포트/오케스트레이터가 쓰는 3종 거부 픽스처가 실제로 전부
    REJECT 판정을 받는지 자체 검증(문서-코드 드리프트 방지)."""
    limits = _limits()
    state = canary.CanarySessionState()
    for label, symbol, notional in canary.build_rejection_fixtures(limits):
        decision = canary.evaluate_pretrade_gate(
            symbol=symbol, notional_usdt=notional, limits=limits, state=state
        )
        assert decision.verdict is canary.GateVerdict.REJECT, f"{label} expected REJECT"


# ---------------------------------------------------------------------------
# submit_with_gate — 레드게이트 재현: REJECT면 어댑터를 절대 호출하지 않는다
# ---------------------------------------------------------------------------


@dataclass
class _SpyAdapter:
    """CanaryAdapter Protocol의 최소 구현 + 호출 스파이."""

    account_mode: str = "classic"
    place_order_calls: list[Order] = field(default_factory=list)
    _next_order_id: int = 0

    async def get_balance(self, asset: str | None = None) -> list[object]:
        return [object(), object()]

    async def get_ticker(self, symbol: str) -> object:
        return type("Ticker", (), {"price": Decimal("50000")})()

    async def place_order(self, order: Order) -> Order:
        self.place_order_calls.append(order)
        self._next_order_id += 1
        return order.model_copy(
            update={
                "exchange_order_id": f"ex-{self._next_order_id}",
                "status": OrderStatus.FILLED,
                "filled_quantity": order.quantity,
            }
        )

    async def get_order(self, order_id: str) -> Order:
        raise AssertionError("이 테스트에서는 호출되지 않아야 함")

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]:
        return []

    async def cancel_order(self, order_id: str) -> bool:
        return True


async def test_submit_with_gate_never_calls_adapter_when_rejected() -> None:
    """레드게이트 재현 — 상한 초과 주문이 게이트를 통과해 거래소로 나가면
    이 assert가 실패한다(place_order_calls가 비어있지 않게 됨)."""
    adapter = _SpyAdapter()
    limits = _limits(max_order_usdt=Decimal("10"))
    state = canary.CanarySessionState()
    order = Order(
        client_order_id="probe",
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.001"),
        asset_class="CRYPTO",
    )

    placed, decision = await canary.submit_with_gate(
        adapter, order, notional_usdt=Decimal("999"), limits=limits, state=state
    )

    assert decision.verdict is canary.GateVerdict.REJECT
    assert placed is None
    assert adapter.place_order_calls == []
    assert state.cumulative_usdt == Decimal("0")
    assert state.fills == 0


async def test_submit_with_gate_calls_adapter_and_updates_state_when_allowed() -> None:
    adapter = _SpyAdapter()
    limits = _limits()
    state = canary.CanarySessionState()
    order = Order(
        client_order_id="probe-2",
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.0001"),
        asset_class="CRYPTO",
    )

    placed, decision = await canary.submit_with_gate(
        adapter, order, notional_usdt=Decimal("5"), limits=limits, state=state
    )

    assert decision.verdict is canary.GateVerdict.ALLOW
    assert placed is not None
    assert len(adapter.place_order_calls) == 1
    assert state.cumulative_usdt == Decimal("5")
    assert state.fills == 1


# ---------------------------------------------------------------------------
# assert_hard_guard_released — 실패주입
# ---------------------------------------------------------------------------


def test_assert_hard_guard_released_raises_when_milestone_missing(tmp_path: Path) -> None:
    """실패주입 — MVP-1_CLOSEOUT.md가 없는 상태(현재 저장소의 실제 상태)를
    합성 경로로 재현. 이 예외가 나지 않으면 하드가드가 우회된 것."""
    missing = tmp_path / "does-not-exist" / "MVP-1_CLOSEOUT.md"
    with pytest.raises(canary.CanaryHardGuardBlockedError):
        canary.assert_hard_guard_released(missing)


def test_assert_hard_guard_released_passes_when_milestone_present(tmp_path: Path) -> None:
    present = tmp_path / "MVP-1_CLOSEOUT.md"
    present.write_text("closeout", encoding="utf-8")
    canary.assert_hard_guard_released(present)  # raises 없으면 통과


def test_repo_hard_guard_is_currently_not_released() -> None:
    """현재 저장소 실측 — task-2750 decision이 비어 있고
    docs/milestones/MVP-1_CLOSEOUT.md도 없다. 이 테스트는 그 사실을
    고정한다: 이 테스트가 실패하면(=마일스톤 파일이 생기면) 비로소 실계좌
    실행을 시도할 조건 중 하나가 충족된 것이므로, 워커는 이 시점에도
    task decision 필드의 명시적 승인 없이는 실행하지 않는다."""
    with pytest.raises(canary.CanaryHardGuardBlockedError):
        canary.assert_hard_guard_released()


# ---------------------------------------------------------------------------
# assert_kill_switch_inactive
# ---------------------------------------------------------------------------


@dataclass
class _FakeRiskGateRepo:
    controls: tuple[SafetyControl, ...] = ()

    async def list_active_controls(
        self,
        *,
        tenant_id: UUID,
        provider_code: str | None = None,
        include_all_providers: bool = False,
    ) -> tuple[SafetyControl, ...]:
        return self.controls


async def test_assert_kill_switch_inactive_raises_when_control_active() -> None:
    """negative #8 — kill switch ACTIVE면 즉시 중단."""
    control = SafetyControl(
        id=uuid4(),
        scope=SafetyScope.GLOBAL,
        scope_ref="*",
        state=SafetyControlState.ACTIVE,
        reason="daily_loss_breach",
        actor_subject_id=uuid4(),
        fence_token=1,
    )
    repo = _FakeRiskGateRepo(controls=(control,))
    with pytest.raises(canary.KillSwitchActiveError):
        await canary.assert_kill_switch_inactive(repo, tenant_id=uuid4())


async def test_assert_kill_switch_inactive_passes_when_no_controls() -> None:
    repo = _FakeRiskGateRepo(controls=())
    await canary.assert_kill_switch_inactive(repo, tenant_id=uuid4())  # raises 없으면 통과


# ---------------------------------------------------------------------------
# far_limit_roundtrip / reconcile_three_way / market_order_roundtrip
# ---------------------------------------------------------------------------


@dataclass
class _RoundtripAdapter:
    account_mode: str = "classic"
    _orders: dict[str, Order] = field(default_factory=dict)
    _history: list[Order] = field(default_factory=list)
    _seq: int = 0

    async def get_balance(self, asset: str | None = None) -> list[object]:
        return [object()]

    async def get_ticker(self, symbol: str) -> object:
        return type("Ticker", (), {"price": Decimal("50000")})()

    async def place_order(self, order: Order) -> Order:
        self._seq += 1
        order_id = f"ex-{self._seq}"
        status = OrderStatus.FILLED if order.order_type == "MARKET" else OrderStatus.ACKNOWLEDGED
        filled = order.quantity if status is OrderStatus.FILLED else Decimal("0")
        placed = order.model_copy(
            update={"exchange_order_id": order_id, "status": status, "filled_quantity": filled}
        )
        self._orders[order_id] = placed
        if status is OrderStatus.FILLED:
            self._history.append(placed)
        return placed

    async def get_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]:
        return list(self._history)

    async def cancel_order(self, order_id: str) -> bool:
        cancelled = self._orders[order_id].model_copy(update={"status": OrderStatus.CANCELLED})
        self._orders[order_id] = cancelled
        return True


async def test_far_limit_roundtrip_places_gets_cancels_and_confirms() -> None:
    adapter = _RoundtripAdapter()
    result = await canary.far_limit_roundtrip(
        adapter, symbol="BTC/USDT", market_price=Decimal("50000"), quantity=Decimal("0.0001")
    )
    assert result.cancelled is True
    assert result.final_status == OrderStatus.CANCELLED.value


async def test_market_order_roundtrip_reconciles_three_way_match() -> None:
    adapter = _RoundtripAdapter()
    result = await canary.market_order_roundtrip(
        adapter, symbol="BTC/USDT", quantity=Decimal("0.0001")
    )
    assert result.buy_reconciliation.matched is True
    assert result.sell_reconciliation.matched is True


def test_reconcile_three_way_reports_pending_when_history_not_yet_propagated() -> None:
    live = Order(
        client_order_id="c1",
        strategy_id="s",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.0001"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.0001"),
        exchange_order_id="ex-1",
        asset_class="CRYPTO",
    )
    record = canary.reconcile_three_way(
        live_order=live, history_order=None, local_position_qty=Decimal("0.0001")
    )
    assert record.pending is True
    assert record.matched is False


def test_reconcile_three_way_reports_mismatch_when_quantities_diverge() -> None:
    """negative #9 (여분) — 거래소/이력/로컬 셋 중 하나라도 어긋나면
    MATCH로 위장하지 않는다."""
    live = Order(
        client_order_id="c1",
        strategy_id="s",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.0001"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.0001"),
        exchange_order_id="ex-1",
        asset_class="CRYPTO",
    )
    history = live.model_copy(update={"filled_quantity": Decimal("0.00005")})
    record = canary.reconcile_three_way(
        live_order=live, history_order=history, local_position_qty=Decimal("0.0001")
    )
    assert record.pending is False
    assert record.matched is False
    assert "MISMATCH" in record.notes


# ---------------------------------------------------------------------------
# render_report / write_report — 리포트 생성 테스트
# ---------------------------------------------------------------------------


def test_render_report_contains_rejected_cases_and_roundtrip_sections() -> None:
    report = canary.CanaryReport(
        run_date="2026-09-16",
        account_mode="classic",
        rejected_cases=(
            canary.GateDecision(canary.GateVerdict.REJECT, "symbol outside whitelist"),
        ),
        limit_roundtrip=canary.LimitRoundtripResult(
            exchange_order_id="ex-1",
            placed_status="ACKNOWLEDGED",
            fetched_status="ACKNOWLEDGED",
            cancelled=True,
            final_status="CANCELLED",
        ),
        market_roundtrip=None,
    )
    text = canary.render_report(report)
    assert "2026-09-16" in text
    assert "REJECT" in text
    assert "ex-1" in text
    assert "CANCELLED" in text


def test_write_report_creates_file_under_out_dir(tmp_path: Path) -> None:
    report = canary.CanaryReport(run_date="2026-09-16", account_mode="unified", rejected_cases=())
    path = canary.write_report(report, out_dir=tmp_path)
    assert path == tmp_path / "CANARY_2026-09-16.md"
    assert path.exists()
    assert "unified" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 성능 단언 — evaluate_pretrade_gate는 결정론적 규칙만 평가하는 순수 함수라
# I/O가 전혀 없다. 10,000회 호출이 1초를 넘으면 회귀(예: 실수로 I/O나 O(n^2)
# 스캔이 섞여 들어간 경우)로 본다.
# ---------------------------------------------------------------------------


def test_evaluate_pretrade_gate_throughput_budget() -> None:
    limits = _limits()
    state = canary.CanarySessionState()
    started = perf_counter()
    for _ in range(10_000):
        canary.evaluate_pretrade_gate(
            symbol="BTC/USDT", notional_usdt=Decimal("1"), limits=limits, state=state
        )
    elapsed = perf_counter() - started
    assert elapsed < 1.0, f"evaluate_pretrade_gate 10,000회가 {elapsed:.3f}s (예산 1.0s 초과)"
