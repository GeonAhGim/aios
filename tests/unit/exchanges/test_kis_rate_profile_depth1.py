"""BR-2 D2 보강(task-24820): registry 조회 성능과 적색 재현.

기존 음성/실패 주입 증거는 유지하고 생성 실패 후 registry 복구도 검증한다.
로컬 Windows Python 3.13에서 warm registry 조회 best-of-5, batch=10000
벽시계 실측 0.000186 ms/회(CPU는 타이머 눈금 아래 0 ms).
새 조회 예산 0.01 ms/회는 실측의 53배 이상이며 기존 acquire 예산과 별개다.
10배 예산 가상 지연을 실제 build_token_bucket 경로에 주입해 같은 단언의
적색을 재현한다. 네트워크/실제 대기 없이 최소 100000회/초 조회를 요구한다.
"""

from collections.abc import Iterator

import pytest

from src.exchanges.kis import rate_profile
from src.exchanges.kis.rate_profile import (
    KisAccountType,
    RateLimitSpec,
    build_token_bucket,
    reset_token_bucket_registry_for_test,
)
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_LOOKUP_BUDGET_MS = 0.01
_TR_ID = "FHKST01010100"


@pytest.fixture(autouse=True)
def _reset_bucket_registry() -> Iterator[None]:
    reset_token_bucket_registry_for_test()
    reset_virtual_totals()
    yield
    reset_token_bucket_registry_for_test()
    reset_virtual_totals()


@pytest.mark.parametrize("rate,burst", [(0.0, 1.0), (-1.0, 1.0), (1.0, 0.0)])
def test_invalid_profile_rejected_without_caching(
    monkeypatch: pytest.MonkeyPatch, rate: float, burst: float
) -> None:
    """음성 3종: 잘못된 프로파일은 생성 실패하고 registry에 남지 않는다."""
    key = (KisAccountType.PAPER, "domestic_stock")
    with monkeypatch.context() as patch:
        patch.setitem(
            rate_profile._PROFILE[KisAccountType.PAPER],
            "domestic_stock",
            RateLimitSpec(rate, burst, "ESTIMATED"),
        )
        with pytest.raises(ValueError, match="rate_per_sec|burst"):
            build_token_bucket(KisAccountType.PAPER, _TR_ID)
        assert key not in rate_profile._BUCKET_REGISTRY
    bucket = build_token_bucket(KisAccountType.PAPER, _TR_ID)
    assert build_token_bucket(KisAccountType.PAPER, _TR_ID) is bucket


def test_bucket_creation_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 주입: 생성자 예외 이후 재시도가 정상 싱글턴을 만든다."""
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected bucket creation failure")

    with monkeypatch.context() as patch:
        patch.setattr(RateLimitSpec, "new_bucket", fail)
        with pytest.raises(OSError, match="injected bucket creation failure"):
            build_token_bucket(KisAccountType.PAPER, _TR_ID)
        assert not rate_profile._BUCKET_REGISTRY
    bucket = build_token_bucket(KisAccountType.PAPER, _TR_ID)
    assert build_token_bucket(KisAccountType.PAPER, _TR_ID) is bucket


def _assert_lookup_budget(perf_budget: PerfBudget) -> None:
    expected = rate_profile.build_token_bucket(KisAccountType.PAPER, _TR_ID)
    sample = perf_budget.best_of(
        lambda: rate_profile.build_token_bucket(KisAccountType.PAPER, _TR_ID),
        n=5,
        batch=10000,
    )
    assert sample.result is expected
    assert sample.cpu_ms < _LOOKUP_BUDGET_MS, "registry lookup budget: " + perf_budget.describe(
        sample, budget_ms=_LOOKUP_BUDGET_MS
    )


@pytest.mark.perf
def test_registry_lookup_throughput_budget(perf_budget: PerfBudget) -> None:
    _assert_lookup_budget(perf_budget)


@pytest.mark.perf
def test_registry_lookup_gate_red_with_10x_virtual_delay(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    """정상 경로 green -> 같은 경로 10배 지연 red -> 복구 green."""
    _assert_lookup_budget(perf_budget)
    with monkeypatch.context() as patch:
        patch.setattr(
            rate_profile,
            "build_token_bucket",
            virtual_delayed_sync(
                rate_profile.build_token_bucket, seconds=_LOOKUP_BUDGET_MS * 10 / 1000
            ),
        )
        with pytest.raises(AssertionError, match="registry lookup budget"):
            _assert_lookup_budget(perf_budget)
    _assert_lookup_budget(perf_budget)
