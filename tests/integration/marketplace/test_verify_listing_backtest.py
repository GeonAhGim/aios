"""MP-11 `verify_listing_backtest.py` 테스트 — ADR-2026-09-06-G §9.

DoD: 리스팅이 주장한 `result_hash`와 플랫폼 재현 결과가 비트 단위로
일치해야 `verified` 평판 반영, 불일치 시 `MP_UNVERIFIED_RESULT`로 표시하고
평판 가산 차단, 위조 픽스처가 실제로 차단됨을 증명.

`run_backtest`(백테스트 모듈)로 실제 재현 결과를 만들고 마켓플레이스
모듈의 검증 로직과 대조한다 — 두 모듈 경계를 넘나드는 대조 자체가 맞는지
증명하는 것이 이 테스트의 목적이라 integration으로 분류한다(BT-19
`test_parity_harness.py`와 동일한 분류 근거).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, BacktestResult, CostModel
from src.foundation.marketplace.application.verify_listing_backtest import (
    MP_UNVERIFIED_RESULT,
    ListingBacktestClaim,
    ListingUnverifiedError,
    compute_result_hash,
    count_verified_backtests,
    verify_listing_backtest,
)
from src.services.condition_compiler import ORDER_FILLED

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


class _FakePriceIndicatorService:
    """"PRICE" 키를 창(window)의 마지막 종가로 되돌린다 — TA-Lib 의존 없음
    (test_run_backtest.py와 동일한 배선 분리 목적)."""

    def calculate(self, indicator: str, candles: list[Candle], **params: int) -> object:
        assert indicator == "PRICE"

        class _Result:
            values = [float(candles[-1].close)]

        return _Result()


def _bar(*, open_price: str, close_price: str, index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(open_price),
        high=max(Decimal(open_price), Decimal(close_price)),
        low=min(Decimal(open_price), Decimal(close_price)),
        close=Decimal(close_price),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="listed-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE,
            FSMState.BUY_ORDER_PENDING,
            FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 105",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition=ORDER_FILLED,
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.SELL_ORDER_PENDING,
                condition="PRICE < 95",
            ),
            FSMTransition(
                from_state=FSMState.SELL_ORDER_PENDING,
                to_state=FSMState.IDLE,
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    )


def _bars() -> list[Candle]:
    return [
        _bar(index=0, open_price="100", close_price="100"),
        _bar(index=1, open_price="100", close_price="110"),
        _bar(index=2, open_price="112", close_price="111"),
        _bar(index=3, open_price="111", close_price="90"),
        _bar(index=4, open_price="88", close_price="89"),
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_id="listed-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=252,
    )


def _platform_reproduction() -> BacktestResult:
    """플랫폼이 리스팅과 같은 아티팩트·구간으로 독립 재현한 결과(정직한 진실)."""
    return run_backtest(
        _config(), _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )


def test_claim_matching_platform_reproduction_is_verified() -> None:
    reproduced = _platform_reproduction()
    honest_claim = ListingBacktestClaim(
        listing_id=1, claimed_result_hash=compute_result_hash(reproduced)
    )

    outcome = verify_listing_backtest(honest_claim, reproduced)

    assert outcome.is_verified is True
    assert outcome.error_code is None
    outcome.raise_if_unverified()  # 통과 — 예외 없음


def test_forged_claim_is_blocked_and_flagged_unverified() -> None:
    """DoD: 위조 픽스처가 실제로 차단됨을 증명 — 판매자가 실제로 나온 결과가
    아니라 더 좋아 보이는 조작된 해시를 주장한다(예: 존재하지 않는 체결
    기록을 근거로 계산한 해시)."""
    reproduced = _platform_reproduction()
    forged_hash = compute_result_hash(reproduced) + "0"  # 실제로 나올 수 없는 해시 위조
    forged_claim = ListingBacktestClaim(listing_id=2, claimed_result_hash=forged_hash)

    outcome = verify_listing_backtest(forged_claim, reproduced)

    assert outcome.is_verified is False
    assert outcome.error_code == MP_UNVERIFIED_RESULT
    with pytest.raises(ListingUnverifiedError, match=MP_UNVERIFIED_RESULT):
        outcome.raise_if_unverified()


def test_forged_claim_excluded_from_reputation_count() -> None:
    """불일치 시 평판 가산 차단 — count_verified_backtests가 위조 건을 뺀다."""
    reproduced = _platform_reproduction()
    honest = verify_listing_backtest(
        ListingBacktestClaim(listing_id=1, claimed_result_hash=compute_result_hash(reproduced)),
        reproduced,
    )
    forged = verify_listing_backtest(
        ListingBacktestClaim(
            listing_id=2, claimed_result_hash=compute_result_hash(reproduced) + "0"
        ),
        reproduced,
    )

    assert count_verified_backtests([honest, forged]) == 1
    assert count_verified_backtests([forged, forged]) == 0
    assert count_verified_backtests([]) == 0


def test_different_reproduced_result_changes_hash() -> None:
    """엔진이 실제로 다른 체결을 냈다면(예: 다른 초기자본) 해시도 달라진다 —
    해시가 항상 같은 상수로 위장 통과되지 않음을 보장."""
    reproduced = _platform_reproduction()
    other_config = BacktestConfig(
        strategy_id="listed-strategy",
        strategy_version="v1",
        initial_equity=Decimal("5000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=252,
    )
    other_result = run_backtest(
        other_config, _fsm_config(), _bars(), indicator_service=_FakePriceIndicatorService()
    )

    assert compute_result_hash(reproduced) != compute_result_hash(other_result)


def test_empty_claimed_hash_rejected_fail_closed() -> None:
    with pytest.raises(ValueError, match="claimed_result_hash"):
        ListingBacktestClaim(listing_id=1, claimed_result_hash="")


def test_empty_equity_curve_rejected_fail_closed() -> None:
    reproduced = _platform_reproduction()
    empty_result = reproduced.model_copy(update={"equity_curve": []})
    with pytest.raises(ValueError, match="equity_curve"):
        compute_result_hash(empty_result)
