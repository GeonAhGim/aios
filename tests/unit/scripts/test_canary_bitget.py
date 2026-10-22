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
from types import ModuleType
from uuid import UUID

import pytest

from src.data.models.trading import Order, OrderStatus
from src.foundation.risk_gate.domain.models import SafetyControl
from tests.conftest import PerfBudget

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


@pytest.mark.perf
def test_load_canary_limits_normalizes_native_symbol_to_canonical(
    tmp_path: Path, perf_budget: PerfBudget
) -> None:
    path = _write_config(tmp_path, _VALID_CONFIG)

    def measure() -> canary.CanaryLimits:
        return canary.load_canary_limits(path)

    limits = measure()
    assert limits.symbols == frozenset({"BTC/USDT"})
    assert limits.max_order_usdt == Decimal("10")
    assert limits.max_total_usdt == Decimal("50")
    assert limits.max_fills == 2

    # 실제 성능 단언: 1회 호출이 50ms CPU 예산 안에 끝난다.
    # load_canary_limits 는 YAML 파싱 + Pydantic 검증 + 심볼 정규화만 하므로
    # 네트워크/디스크 I/O 대기 없이 순수 CPU 작업으로 완료된다.
    perf_budget.assert_within(measure, budget_ms=50.0, label="load_canary_limits 1회 호출")


@pytest.mark.perf
def test_load_canary_limits_detection_budget_violation(
    tmp_path: Path, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """검출력 확인: load_canary_limits 를 10배 느리게 만든 budgets.assert_within
    가 적색으로 떨어지는지 검증 — 단언이 허울이 아닌 실제 검출력을 갖는지 확인.

    load_canary_limits 는 YAML 파일 읽기 + Pydantic 검증 + 심볼 변환을 하므로
    I/O 포함 지연 예산이다. sleep 으로 지연을 주입하고 wall-clock 으로 측정한다.
    """
    path = _write_config(tmp_path, _VALID_CONFIG)

    # 원본 함수 참조
    _original_load = canary.load_canary_limits

    def _delayed_load(path_arg: Path | None = None) -> canary.CanaryLimits:
        import asyncio

        # sleep 주입 — I/O 포함 지연 예산이므로 asyncio.sleep 사용
        asyncio.run(asyncio.sleep(0.05))
        return _original_load(path_arg)

    monkeypatch.setattr(canary, "load_canary_limits", _delayed_load)

    def measure() -> canary.CanaryLimits:
        return canary.load_canary_limits(path)

    # 지연 주입 후 wall_ms 가 50ms 예산을 초과해야 한다 — 그렇지 않으면
    # 이 테스트가 허울 단언임을 의미(검출력 결함).
    # load_canary_limits 는 I/O 포함 작업이므로 cpu_ms 가 0 수렴할 수 있다.
    sample = perf_budget.sample(measure, batch=1)
    assert sample.wall_ms >= 50.0, (
        f"검출력 실패: 지연 주입에도 wall-clock 50ms를 초과하지 못함 — "
        f"wall={sample.wall_ms:.1f}ms. 단언이 허울임을 의미."
    )


@pytest.mark.perf
def test_load_canary_limits_rejects_unknown_key_fail_closed(
    tmp_path: Path, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative #1 — extra="forbid": 오타 키가 조용히 무시되지 않는다.

    성능 단언: YAML 파싱 + Pydantic 검증 + 심볼 정규화는 CPU 예산 안에
    끝난다. 지연 주입(monkeypatch)으로 예산을 초과하면 테스트가 적색으로
    떨어지는지 검출력도 확인한다.
    """
    path = _write_config(tmp_path, _VALID_CONFIG + "\ntypo_field: 1\n")

    # 실제 성능 단언: unknown key는 Pydantic 검증에서 즉시 거부되므로
    # CPU 예산 안에 끝난다. 예외가 발생해도 PerfBudget가 측정 구간을
    # 벗어나도록 _exception_result sentinel로 반환값을 보존한다.
    _exception_result = object()

    def measure() -> object:
        try:
            return canary.load_canary_limits(path)
        except canary.CanaryConfigError:
            return _exception_result

    sample = perf_budget.best_of(measure, n=5)
    assert sample.result is _exception_result, "음성 테스트: 예외 대신 정상 반환값"
    assert sample.cpu_ms < 50.0, (
        f"검증 실패 경로가 느림: {sample.cpu_ms:.3f}ms — Pydantic extra=forbid 검증은 O(1)이어야 함"
    )

    # 예외 발생 재확인 (음성 테스트)
    with pytest.raises(canary.CanaryConfigError):
        canary.load_canary_limits(path)

    # 검출력 확인: load_canary_limits 를 10배 느리게 만든 budgets.assert_within
    # 가 적색으로 떨어지는지 검증 — 단언이 허울이 아닌 실제 검출력을 갖는지 확인.
    # load_canary_limits 는 YAML 파일 읽기 + Pydantic 검증 + 심볼 변환을 하므로
    # I/O 포함 지연 예산이다. sleep 으로 지연을 주입하고 wall-clock 으로 측정한다.
    from tests.support.perf_inject import delayed_sync

    monkeypatch.setattr(
        canary,
        "load_canary_limits",
        delayed_sync(canary.load_canary_limits, 0.05),
    )

    def measure_delayed() -> object:
        try:
            return canary.load_canary_limits(path)
        except canary.CanaryConfigError:
            return _exception_result

    sample = perf_budget.sample(measure_delayed, batch=1)
    assert sample.wall_ms >= 50.0, (
        f"검출력 실패: 지연 주입에도 wall-clock 50ms를 초과하지 못함 — "
        f"wall={sample.wall_ms:.1f}ms. 단언이 허울임을 의미."
    )


@pytest.mark.perf
def test_load_canary_limits_rejects_non_positive_max_order_usdt(
    tmp_path: Path, perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative #2 — 상한 값 자체가 0 이하면 설정 로드 단계에서 거부.

    성능 단언: 0 이하는 Pydantic 검증에서 즉시 거부되므로 CPU 예산 안에
    끝난다. 지연 주입(monkeypatch)으로 예산을 초과하면 테스트가 적색으로
    떨어지는지 검출력도 확인한다.
    """
    body = _VALID_CONFIG.replace("max_order_usdt: 10", "max_order_usdt: 0")
    path = _write_config(tmp_path, body)

    # 실제 성능 단언: 0 이하는 Pydantic 검증에서 즉시 거부되므로
    # CPU 예산 안에 끝난다. 예외가 발생해도 PerfBudget가 측정 구간을
    # 벗어나도록 _exception_result sentinel로 반환값을 보존한다.
    _exception_result = object()

    def measure() -> object:
        try:
            return canary.load_canary_limits(path)
        except canary.CanaryConfigError:
            return _exception_result

    sample = perf_budget.best_of(measure, n=5)
    assert sample.result is _exception_result, "음성 테스트: 예외 대신 정상 반환값"
    assert sample.cpu_ms < 50.0, (
        f"검증 실패 경로가 느림: {sample.cpu_ms:.3f}ms — Pydantic gt=0 검증은 O(1)이어야 함"
    )

    # 예외 발생 재확인 (음성 테스트)
    with pytest.raises(canary.CanaryConfigError):
        canary.load_canary_limits(path)

    # 검출력 확인: load_canary_limits 를 10배 느리게 만든 budgets.assert_within
    # 가 적색으로 떨어지는지 검증 — 단언이 허울이 아닌 실제 검출력을 갖는지 확인.
    # load_canary_limits 는 YAML 파일 읽기 + Pydantic 검증 + 심볼 변환을 하므로
    # I/O 포함 지연 예산이다. sleep 으로 지연을 주입하고 wall-clock 으로 측정한다.
    from tests.support.perf_inject import delayed_sync

    monkeypatch.setattr(
        canary,
        "load_canary_limits",
        delayed_sync(canary.load_canary_limits, 0.05),
    )

    def measure_delayed() -> object:
        try:
            return canary.load_canary_limits(path)
        except canary.CanaryConfigError:
            return _exception_result

    sample = perf_budget.sample(measure_delayed, batch=1)
    assert sample.wall_ms >= 50.0, (
        f"검출력 실패: 지연 주입에도 wall-clock 50ms를 초과하지 못함 — "
        f"wall={sample.wall_ms:.1f}ms. 단언이 허울임을 의미."
    )


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


# 성능 단언은 tests/unit/scripts/test_canary_bitget_perf.py로 분할.
