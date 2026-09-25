"""Backtest domain/universe.py 단위테스트 -- DB 없이 순수 함수만 검증.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L29 DoD.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.foundation.backtest.domain.universe import (
    SurvivorshipUnknownError,
    UniverseMember,
    UniverseSnapshot,
    compute_snapshot_hash,
    is_member,
)

_UTC = timezone.utc
_AS_OF = datetime(2026, 3, 1, tzinfo=_UTC)
_LISTED_FROM = datetime(2026, 1, 1, tzinfo=_UTC)
_DELISTED_AT = datetime(2026, 2, 15, tzinfo=_UTC)


def _snapshot(members: list[UniverseMember], *, as_of: datetime = _AS_OF) -> UniverseSnapshot:
    return UniverseSnapshot(
        as_of=as_of, members=members, snapshot_hash=compute_snapshot_hash(as_of, members)
    )


def _member(symbol: str = "ABC", delisted_at: datetime | None = _DELISTED_AT) -> UniverseMember:
    return UniverseMember(symbol=symbol, listed_from=_LISTED_FROM, delisted_at=delisted_at)


# -- (a) naive datetime rejection --------------------------------------------


def test_member_rejects_naive_listed_from() -> None:
    with pytest.raises(ValidationError):
        UniverseMember(symbol="ABC", listed_from=datetime(2026, 1, 1), delisted_at=None)


def test_member_rejects_naive_delisted_at() -> None:
    with pytest.raises(ValidationError):
        UniverseMember(symbol="ABC", listed_from=_LISTED_FROM, delisted_at=datetime(2026, 2, 15))


def test_snapshot_rejects_naive_as_of() -> None:
    with pytest.raises(ValidationError):
        UniverseSnapshot(as_of=datetime(2026, 3, 1), members=[], snapshot_hash="x")


def test_is_member_rejects_naive_at() -> None:
    snap = _snapshot([_member()])
    with pytest.raises(ValueError, match="tz-aware"):
        is_member(snap, "ABC", datetime(2026, 1, 15))


# -- (b) boundary correctness (5 cases) --------------------------------------


def test_is_member_before_listing_is_false() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", datetime(2025, 12, 31, 23, 59, tzinfo=_UTC)) is False


def test_is_member_on_listing_instant_is_true_inclusive() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", _LISTED_FROM) is True


def test_is_member_just_before_delisting_is_true() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", datetime(2026, 2, 14, 23, 59, tzinfo=_UTC)) is True


def test_is_member_on_delisting_instant_is_false_exclusive() -> None:
    snap = _snapshot([_member()])
    assert is_member(snap, "ABC", _DELISTED_AT) is False


def test_is_member_never_delisted_is_true_far_future() -> None:
    far_future = datetime(2099, 1, 1, tzinfo=_UTC)
    snap = _snapshot([_member(delisted_at=None)], as_of=far_future)
    assert is_member(snap, "ABC", far_future) is True


# -- (c) fail-closed on unknown survivorship ----------------------------------


def test_is_member_unknown_symbol_raises_not_false() -> None:
    snap = _snapshot([_member(symbol="ABC")])
    with pytest.raises(SurvivorshipUnknownError) as exc_info:
        is_member(snap, "ZZZ", _LISTED_FROM)
    assert exc_info.value.error_code == "INTEGRITY_SURVIVORSHIP_UNKNOWN"


def test_is_member_query_after_as_of_raises() -> None:
    snap = _snapshot([_member()])
    with pytest.raises(SurvivorshipUnknownError):
        is_member(snap, "ABC", _AS_OF + timedelta(days=1))


# -- (d) snapshot_hash stability -----------------------------------------------


def test_snapshot_hash_is_order_independent() -> None:
    m1 = _member(symbol="AAA")
    m2 = _member(symbol="BBB")
    hash_forward = compute_snapshot_hash(_AS_OF, [m1, m2])
    hash_reversed = compute_snapshot_hash(_AS_OF, [m2, m1])
    assert hash_forward == hash_reversed


def test_snapshot_hash_changes_when_delisted_at_shifts_by_one_day() -> None:
    original = _member(symbol="AAA", delisted_at=_DELISTED_AT)
    shifted = _member(symbol="AAA", delisted_at=_DELISTED_AT + timedelta(days=1))
    hash_original = compute_snapshot_hash(_AS_OF, [original])
    hash_shifted = compute_snapshot_hash(_AS_OF, [shifted])
    assert hash_original != hash_shifted


# -- (e) D2 보강: failure injection --------------------------------------------


def test_is_member_corrupted_window_defaults_false_not_crash_or_true() -> None:
    """실패 주입: 상류 ETL 결함으로 `delisted_at`이 `listed_from`보다 앞선
    손상된 창(window)이 스냅샷에 섞여 들어온 경우, `UniverseMember`에는
    교차필드 검증이 없어 그대로 통과한다 -- `is_member`가 이 손상된 데이터를
    예외로 터뜨리거나(예상 못 한 크래시) "상장 중"으로 잘못 판정하지 않고
    모든 시각에 보수적으로 `False`를 반환하는지 확인한다."""
    corrupted = UniverseMember(symbol="BROKEN", listed_from=_AS_OF, delisted_at=_LISTED_FROM)
    snap = _snapshot([corrupted])

    # listed_from 이전
    assert is_member(snap, "BROKEN", _LISTED_FROM - timedelta(days=1)) is False
    # listed_from과 delisted_at 사이(역전 구간)
    assert is_member(snap, "BROKEN", _LISTED_FROM) is False
    # listed_from 이후(정상이라면 상장 중이었을 구간)
    assert is_member(snap, "BROKEN", _AS_OF) is False


# -- (f) D2 보강: 수치 성능 단언 ------------------------------------------------


@pytest.mark.perf
def test_is_member_p95_latency_within_budget_for_large_snapshot() -> None:
    """수치 성능 단언: 5,000개 심볼을 담은 스냅샷에서도 `is_member`(선형 탐색)
    단일 조회가 실사용 규모(거래소 상장 종목 수 상한)에서 예산 안에 든다
    (ADR-2026-09-09-C 축 차용 -- 순수 함수 조회 경로, p95 예산 10ms/call)."""
    members = [
        UniverseMember(symbol=f"SYM{i:05d}", listed_from=_LISTED_FROM, delisted_at=None)
        for i in range(5000)
    ]
    snap = _snapshot(members)
    target_symbol = "SYM04999"  # 선형 탐색 최악 경로(리스트 끝)

    durations_ms: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        is_member(snap, target_symbol, _LISTED_FROM)
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(len(durations_ms) * 0.95)]
    assert p95 < 10.0, f"p95={p95:.3f}ms exceeds 10ms budget for 5,000-member snapshot scan"


# -- (g) D2 보강: 게이트 적색 재현 ----------------------------------------------


def test_gate_red_repro_missing_as_of_guard_would_leak_future_state() -> None:
    """게이트 적색 재현: `if at > u.as_of: raise ...` 가드를 지운 회귀본을
    테스트 안에서 직접 재현한다. 가드가 없으면 스냅샷이 알지 못하는 미래
    시각 조회를 그대로 판정해 "상장 중"이라는 근거 없는 `True`를 반환한다
    (look-ahead 정보 누출, I-03 위반) -- 실제 구현은 이 가드로 fail-closed
    하게 `SurvivorshipUnknownError`를 던진다."""

    def _regressed_is_member(u: UniverseSnapshot, symbol: str, at: datetime) -> bool:
        # as_of 가드 제거 회귀 재현 -- 실제 구현과 달리 미래 조회를 그대로 판정
        member = next((m for m in u.members if m.symbol == symbol), None)
        if member is None:
            raise SurvivorshipUnknownError(symbol=symbol, at=at)
        if at < member.listed_from:
            return False
        if member.delisted_at is not None and at >= member.delisted_at:
            return False
        return True

    snap = _snapshot([_member(delisted_at=None)])
    future = _AS_OF + timedelta(days=365)

    # 회귀본: 가드가 없으니 스냅샷이 모르는 미래도 조용히 True -- 적색
    assert _regressed_is_member(snap, "ABC", future) is True

    # 실제 구현: fail-closed로 거부 -- 녹색
    with pytest.raises(SurvivorshipUnknownError):
        is_member(snap, "ABC", future)
