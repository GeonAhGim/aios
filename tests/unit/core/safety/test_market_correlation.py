import decimal
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from src.core.safety.market_correlation import is_market_wide_move


def test_basket_below_min_symbols_is_undeterminable():
    result = is_market_wide_move(
        {"BTC/USDT": Decimal("-5"), "ETH/USDT": Decimal("-5")},
        account_loss_pct=Decimal("8"),
        min_symbols=3,
        move_threshold_pct=Decimal("3"),
    )
    assert result is None


def test_no_account_loss_is_never_correlated():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-9"), "C": Decimal("-9")},
        account_loss_pct=Decimal("0"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_majority_basket_decline_is_market_wide():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-4"), "C": Decimal("-3.5"), "D": Decimal("0.2")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is True


def test_single_symbol_decline_is_isolated_not_market_wide():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("0.1"), "C": Decimal("0.2"), "D": Decimal("-0.5")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_exactly_half_declined_is_not_majority():
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-9"), "C": Decimal("0"), "D": Decimal("0")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_decline_exactly_at_threshold_counts():
    result = is_market_wide_move(
        {"A": Decimal("-3"), "B": Decimal("-3"), "C": Decimal("0")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is True


# --- 경계값(negative) 테스트 ---------------------------------------------------


def test_basket_exactly_at_min_symbols_is_determinable():
    """negative — basket 크기가 min_symbols와 정확히 같으면(미만이 아니라)
    None이 아니라 실제 판정이 내려져야 한다."""
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("0"), "C": Decimal("0")},
        account_loss_pct=Decimal("8"),
        min_symbols=3,
        move_threshold_pct=Decimal("3"),
    )
    assert result is False  # 3개 중 1개만 하락 — 과반 아님


def test_unanimous_decline_is_market_wide():
    """negative — 전원 하락(만장일치)은 당연히 시장 전체 급변으로 판정돼야
    한다(과반 판정 로직의 상한 경계)."""
    result = is_market_wide_move(
        {"A": Decimal("-10"), "B": Decimal("-8"), "C": Decimal("-15")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is True


def test_decline_just_below_threshold_does_not_count():
    """negative — move_threshold_pct보다 살짝 못 미치는 하락은 "급변"으로
    카운트되지 않는다(<=가 아니라 그보다 얕은 하락)."""
    result = is_market_wide_move(
        {"A": Decimal("-2.999"), "B": Decimal("-2.999"), "C": Decimal("0")},
        account_loss_pct=Decimal("8"),
        move_threshold_pct=Decimal("3"),
    )
    assert result is False


def test_custom_min_symbols_lowers_the_undeterminable_floor():
    """negative — min_symbols를 낮추면 이전엔 None이었을 작은 basket도
    판정 가능해진다(기본값 3이 아니라 호출부가 넘긴 값을 실제로 쓰는지)."""
    result = is_market_wide_move(
        {"A": Decimal("-9"), "B": Decimal("-9")},
        account_loss_pct=Decimal("8"),
        min_symbols=2,
        move_threshold_pct=Decimal("3"),
    )
    assert result is True


# --- 실패 주입 ------------------------------------------------------------


def test_corrupted_nan_return_fails_loud_not_silently_wrong():
    """실패 주입 — 가격 피드가 손상돼 특정 심볼의 수익률이 Decimal('NaN')으로
    들어오면(예: 0으로 나눈 결과) 조용히 잘못된 판정을 내는 대신 예외로
    시끄럽게 실패해야 한다 — fail-closed(§R3, 손상 입력을 암묵 통과시키지
    않는다)."""
    with pytest.raises(decimal.InvalidOperation):
        is_market_wide_move(
            {"A": Decimal("NaN"), "B": Decimal("-9"), "C": Decimal("-9")},
            account_loss_pct=Decimal("8"),
            move_threshold_pct=Decimal("3"),
        )


# --- 성능 단언 --------------------------------------------------------------


def test_is_market_wide_move_perf_bound_for_large_basket():
    """성능 단언 — 이 함수는 basket을 한 번 순회하는 O(n) 순수 함수여야
    한다. 대형 합성 basket(5만 심볼)으로 이차식 회귀가 들어와도 CI가
    눈치채도록 시간 상한을 못박는다."""
    basket = {
        f"SYM{i}/USDT": Decimal("-5") if i % 3 == 0 else Decimal("0.1") for i in range(50_000)
    }
    start = time.perf_counter()
    result = is_market_wide_move(
        basket, account_loss_pct=Decimal("8"), move_threshold_pct=Decimal("3")
    )
    elapsed = time.perf_counter() - start
    assert result is False  # 1/3 하락 — 과반 아님
    assert elapsed < 1.0, (
        f"5만 심볼 basket 처리에 {elapsed:.2f}s — O(n) 순수 함수치고 과도하게 느림"
    )


# --- 다중 인스턴스(동시 실행) 증명 -------------------------------------------


def test_is_market_wide_move_consistent_across_concurrent_threads():
    """다중 인스턴스 증거 — 순수 함수라 공유 가변 상태가 없어야 한다. 여러
    watchdog 배포/스레드가 동시에 같은 basket으로 호출해도 항상 같은 결과가
    나와야 한다."""
    basket = {"A": Decimal("-9"), "B": Decimal("-4"), "C": Decimal("-3.5"), "D": Decimal("0.2")}

    def _call(_: int) -> bool | None:
        return is_market_wide_move(
            basket, account_loss_pct=Decimal("8"), move_threshold_pct=Decimal("3")
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_call, range(200)))

    assert all(r is True for r in results)
