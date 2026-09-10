"""R-29 — execution_loop/correlation_service.py 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-29.
`correlated_exposure`가 상관행렬 계산을 재구현하지 않고
risk_stats.correlation_matrix로 위임하는지, 미지 페어·최소 중첩 미달을
0.0(무상관)으로 암묵 치환하지 않고 None을 반환하는지(레거시
`correlation.py`의 하드코딩 표 결함 재발 방지, R3 fail-closed)를
검증한다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.rules.correlation import correlation as correlation_rule
from src.data.models.market_data import Candle
from src.services.execution_loop.correlation_service import correlated_exposure

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_THRESHOLD = 0.7
_MIN_OVERLAP = 10
_RISK_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _risk_inputs(stats: StatsInputs) -> RiskInputs:
    """`rules/correlation.py`가 소비하는 최소 `RiskInputs` — 이 테스트가
    검증하려는 필드(stats)만 격리해서 게이트 체인을 재현하기 위한 헬퍼."""
    return RiskInputs(
        tenant_id=uuid4(),
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=OrderIntent(
            symbol="BTC/USDT", asset_class="CRYPTO_SPOT", side="BUY", quantity=Decimal("0.1"),
            ref_price=Decimal("50000"), notional=Decimal("5000"), reduce_only=False,
            strategy_id="strat-1", strategy_version="1.0", capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(as_of=_RISK_NOW),
        exposure=ExposureSnapshot(as_of=_RISK_NOW),
        stats=stats,
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        limits=(),
        as_of=_RISK_NOW,
    )


def _make_candles(closes: list[Decimal], *, symbol: str) -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            exchange="bitget",
            timeframe="1d",
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1"),
            open_time=_BASE + timedelta(days=i),
            close_time=_BASE + timedelta(days=i + 1),
        )
        for i, close in enumerate(closes)
    ]


def _proportional_closes(n: int, *, start: Decimal, factor: Decimal) -> list[Decimal]:
    """`factor`가 같은 부호면 원본과 완전 양의 상관(+1.0), 반대 부호면
    완전 음의 상관(-1.0)이 되는 종가열 — 실제 수익률 기반 상관 계산이
    올바로 위임되는지 결정론적으로 증명하기 위한 헬퍼."""
    shocks = [Decimal("0.01") if i % 2 == 0 else Decimal("-0.006") for i in range(n)]
    closes = [start]
    for shock in shocks:
        closes.append(closes[-1] * (Decimal("1") + shock * factor))
    return closes


def test_no_positions_returns_zero_exposure_and_zero_correlation():
    exposure, max_corr = correlated_exposure(
        {}, [], "BTC/USDT", threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP
    )
    assert exposure == Decimal("0")
    assert max_corr == 0.0


def test_self_symbol_position_needs_no_history():
    """대상 심볼 자신에 대한 기존 포지션은 상관 1.0이 자명하므로 histories가
    비어 있어도(fail-closed로 None이 되지 않고) 그대로 노출로 잡혀야 한다."""
    exposure, max_corr = correlated_exposure(
        {}, [("BTC/USDT", Decimal("500"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )
    assert exposure == Decimal("500")
    assert max_corr == 1.0


def test_correlated_position_above_threshold_is_summed_via_risk_stats_delegation():
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("1"))
    histories = {
        "BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert max_corr is not None and max_corr > 0.99  # 비례 수익률 → 상관 ≈ 1.0
    assert exposure == Decimal("300")


def test_anti_correlated_position_below_threshold_excluded_from_exposure():
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("-1"))
    histories = {
        "BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert max_corr is not None and max_corr < -0.99  # 완전 반비례 → 상관 ≈ -1.0
    assert exposure == Decimal("0")  # threshold(0.7) 미만이라 노출 합산 제외


def test_missing_history_for_other_symbol_returns_none_not_zero():
    """레거시 `correlation_with()`는 표에 없는 페어를 0.0(무상관)으로 조용히
    치환해 통과시켰다 — 여기선 히스토리가 아예 없는 심볼(XRP/USDT)은
    "판단 불가"이지 "무상관"이 아니므로 반드시 None을 반환해야 한다."""
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    histories = {"BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT")}

    exposure, max_corr = correlated_exposure(
        histories, [("XRP/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_missing_target_history_with_other_positions_returns_none_not_zero():
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("1"))
    histories = {"ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT")}

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_overlap_below_min_overlap_returns_none_not_zero():
    """중첩 표본이 `min_overlap` 미만이면(짧은 히스토리) 상관을 신뢰할 수
    없으므로 0.0이 아니라 None — 호출자가 DENY로 처리해야 한다."""
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    short_other_closes = _proportional_closes(3, start=Decimal("50"), factor=Decimal("1"))
    histories = {
        "BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(short_other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_multiple_positions_aggregate_same_symbol_before_correlation_lookup():
    exposure, max_corr = correlated_exposure(
        {}, [("BTC/USDT", Decimal("200")), ("BTC/USDT", Decimal("100"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )
    assert exposure == Decimal("300")
    assert max_corr == 1.0


def test_flat_price_history_returns_none_when_correlation_is_mathematically_undefined():
    """실패 주입 — 완전히 변동 없는(거래정지·유동성 소실) 종가열은 분산이
    0이라 Pearson 상관 자체가 정의되지 않는다(`risk_stats.correlation_matrix`
    가 0으로 나누기를 피해 None을 반환). 이 결손도 0.0(무상관)으로 암묵
    치환하지 않고 전체를 (None, None)으로 fail-closed 해야 한다."""
    flat_closes = [Decimal("100")] * 30  # factor=0과 동일 — 완전 무변동.
    other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("1"))
    histories = {
        "BTC/USDT": _make_candles(flat_closes, symbol="BTC/USDT"),
        "ETH/USDT": _make_candles(other_closes, symbol="ETH/USDT"),
    }

    exposure, max_corr = correlated_exposure(
        histories, [("ETH/USDT", Decimal("300"))], "BTC/USDT",
        threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
    )

    assert exposure is None
    assert max_corr is None


def test_repeated_calls_over_many_positions_has_no_performance_regression():
    """성능 단언 — 10개 포지션·30봉 히스토리에 대한 상관 노출 계산 30회가
    느슨한 상한(10000ms, 이 headless worker 환경의 CPU 경합을 감안한 회귀
    감지용 — 절대 성능목표 아님) 안에 끝나야 한다."""
    target = "BTC/USDT"
    symbols = [f"SYM{i}/USDT" for i in range(10)]
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    histories = {target: _make_candles(target_closes, symbol=target)}
    for i, sym in enumerate(symbols):
        factor = Decimal("1") if i % 2 == 0 else Decimal("-1")
        histories[sym] = _make_candles(
            _proportional_closes(30, start=Decimal("50"), factor=factor), symbol=sym
        )
    positions = [(sym, Decimal("100")) for sym in symbols]

    started = time.perf_counter()
    for _ in range(30):
        exposure, max_corr = correlated_exposure(
            histories, positions, target, threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
        )
        assert exposure is not None
        assert max_corr is not None
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert elapsed_ms < 10000.0, f"상관 노출 계산 30회 지연 회귀 의심: {elapsed_ms:.2f}ms"


def test_none_result_denies_via_correlation_gate_chain():
    """게이트 적색 재현 — 다른 심볼 히스토리 결손으로 `correlated_exposure`가
    실제로 반환하는 None이, 그대로 `rules/correlation.py` 게이트에 들어가면
    조용히 ALLOW로 새지 않고 DENY함을 체인 전체로 증명한다(이 리프의
    docstring이 주장하는 fail-closed 계약, R3)."""
    policy = load_risk_policy()
    target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
    histories = {"BTC/USDT": _make_candles(target_closes, symbol="BTC/USDT")}

    corr_policy = policy.correlation_risk
    exposure, max_corr = correlated_exposure(
        histories, [("XRP/USDT", Decimal("300"))], "BTC/USDT",
        threshold=corr_policy.threshold, min_overlap=corr_policy.min_overlap,
    )
    assert exposure is None
    assert max_corr is None

    missing_pairs = ("target:insufficient_history",)
    stats = StatsInputs(
        as_of=_RISK_NOW, correlated_exposure_pct=None, max_correlation=None,
        missing_pairs=missing_pairs,
    )
    decision = correlation_rule(_risk_inputs(stats), policy)

    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RISK_INPUT_MISSING:stats.missing_pairs"


def test_deterministic_across_independently_constructed_inputs_for_replay():
    """리플레이 증명(D3) — 감사 재생(R2)은 나중에 독립적으로 재구성된 입력
    (별도 리스트/딕셔너리 객체, 같은 논리적 값)으로도 최초 평가와 정확히
    같은 노출·상관을 재현해야 한다. 객체 동일성이 아니라 값 동일성에
    의존하는 숨은 상태가 없는지 증명한다."""

    def _fresh_call() -> tuple[Decimal | None, float | None]:
        target_closes = _proportional_closes(30, start=Decimal("100"), factor=Decimal("1"))
        other_closes = _proportional_closes(30, start=Decimal("50"), factor=Decimal("1"))
        histories = {
            "BTC/USDT": _make_candles(list(target_closes), symbol="BTC/USDT"),
            "ETH/USDT": _make_candles(list(other_closes), symbol="ETH/USDT"),
        }
        positions = [("ETH/USDT", Decimal("300"))]
        return correlated_exposure(
            histories, positions, "BTC/USDT", threshold=_THRESHOLD, min_overlap=_MIN_OVERLAP,
        )

    first_exposure, first_corr = _fresh_call()
    second_exposure, second_corr = _fresh_call()

    assert first_exposure is not None and second_exposure is not None
    assert first_exposure == second_exposure
    assert first_corr == second_corr
