"""BR-2 — KIS 호출 한도 프로파일(exchanges/kis/rate_profile.py) 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.2, ADR-2026-09-06-I D4.
DoD: (1) 실전/모의 프로파일이 다른 값으로 적용된다, (2) 프로파일에 없는
TR/TR그룹은 가장 보수적 한도로 취급된다, (3) 한도 초과 시 예외(429 상당)
대신 대기로 흡수되고 그 사실이 `RateLimitWaitObserver`로 관측된다.
"""
from __future__ import annotations

import pytest

from src.exchanges.common.error_taxonomy import ExchangeError
from src.exchanges.common.transport import RateLimitWaitObserver
from src.exchanges.kis.rate_profile import (
    KisAccountType,
    RateLimitSpec,
    build_token_bucket,
    get_rate_limit,
    tr_group_for,
)

_KNOWN_TR_ID = "FHKST01010100"  # [국내주식] 주식현재가 시세 — kis_tr_reference.json에 존재
_UNKNOWN_TR_ID = "ZZZZZZZZZ_NOT_A_REAL_TR"


def test_real_and_paper_profiles_differ_for_same_group() -> None:
    real = get_rate_limit(KisAccountType.REAL, "domestic_stock")
    paper = get_rate_limit(KisAccountType.PAPER, "domestic_stock")
    assert (real.rate_per_sec, real.burst) != (paper.rate_per_sec, paper.burst)
    assert real.rate_per_sec > paper.rate_per_sec


@pytest.mark.parametrize("group", [None, "nonexistent_group", "made_up"])
def test_unknown_group_falls_back_to_most_conservative_limit(group: str | None) -> None:
    known_groups = ("domestic_stock", "domestic_bond", "overseas_stock")
    for account_type in (KisAccountType.REAL, KisAccountType.PAPER):
        fallback = get_rate_limit(account_type, group)
        known_specs = [get_rate_limit(account_type, g) for g in known_groups]
        assert all(fallback.rate_per_sec <= s.rate_per_sec for s in known_specs)
        assert all(fallback.burst <= s.burst for s in known_specs)
        assert fallback.verified == "ESTIMATED"


def test_tr_group_for_known_tr_id_matches_br1_domain() -> None:
    assert tr_group_for(_KNOWN_TR_ID) == "domestic_stock"


def test_tr_group_for_unknown_tr_id_returns_none() -> None:
    assert tr_group_for(_UNKNOWN_TR_ID) is None


def test_build_token_bucket_for_unknown_tr_uses_conservative_limit() -> None:
    fallback = get_rate_limit(KisAccountType.REAL, None)
    bucket = build_token_bucket(KisAccountType.REAL, _UNKNOWN_TR_ID)
    # 프로파일 미스가 관대한 기본값으로 새지 않았는지 내부 상태로 직접 확인
    assert bucket._burst == fallback.burst


async def test_build_token_bucket_real_vs_paper_accept_different_bursts() -> None:
    """DoD — 실전/모의가 실제로 다른 한도로 동작함을 TokenBucket 레벨에서 증명."""
    real_spec = get_rate_limit(KisAccountType.REAL, tr_group_for(_KNOWN_TR_ID))
    paper_spec = get_rate_limit(KisAccountType.PAPER, tr_group_for(_KNOWN_TR_ID))
    assert real_spec.burst > paper_spec.burst

    real_bucket = build_token_bucket(KisAccountType.REAL, _KNOWN_TR_ID)
    paper_bucket = build_token_bucket(KisAccountType.PAPER, _KNOWN_TR_ID)

    await real_bucket.acquire(real_spec.burst, timeout=0.01)  # 실전 한도 전량은 즉시 확보 가능
    with pytest.raises(ExchangeError):
        # negative test — 모의 버킷은 실전 한도만큼의 요청량을 burst 초과로 즉시 거부한다
        await paper_bucket.acquire(real_spec.burst, timeout=0.01)


async def test_rate_limit_wait_is_absorbed_client_side_not_raised_and_is_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """한도 초과 시 429가 아니라 클라이언트에서 대기·재시도로 흡수되고,
    그 사실이 지표(RateLimitWaitObserver)로 관측됨을 증명한다(DoD).
    실제 대기 없이 결정론적으로 검증한다(fake sleep, task-423 패턴)."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("src.exchanges.kis.rate_profile.asyncio.sleep", fake_sleep)

    observer = RateLimitWaitObserver()
    spec = RateLimitSpec(rate_per_sec=10.0, burst=1.0, verified="DOC_ONLY")
    bucket = spec.new_bucket(observer=observer)

    await bucket.acquire(1, timeout=1.0)  # 버스트 소진 — 대기 없음
    assert observer.waits == 0

    await bucket.acquire(1, timeout=1.0)  # 리필 필요 — 예외 대신 대기로 흡수됨
    assert observer.waits == 1
    # 실제 시계 경과분만큼 필요 대기시간이 줄어들 수 있어 정확히 0.1은 아니다 —
    # "대기가 한 번, 0보다 크고 0.1초 이하로 발생했다"만 결정론적으로 확인한다.
    assert len(sleep_calls) == 1
    assert 0.0 < sleep_calls[0] <= 0.1
