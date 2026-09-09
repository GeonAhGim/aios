"""BR-2 — KIS 호출 한도 프로파일(exchanges/kis/rate_profile.py) 단위 테스트.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.2, ADR-2026-09-06-I D4.
DoD: (1) 실전/모의 프로파일이 다른 값으로 적용된다, (2) 프로파일에 없는
TR/TR그룹은 가장 보수적 한도로 취급된다, (3) 한도 초과 시 예외(429 상당)
대신 대기로 흡수되고 그 사실이 `RateLimitWaitObserver`로 관측된다.

DEEPEN(task-2774, docs/audit/DEPTH_L4_BR.md #1780, D1 실측 -> D2 하한): 원
커밋(b5fbf87)은 negative test 1개뿐이었고 failure-injection·gate/CI
red-line 회귀 테스트가 없었다. 아래에 (a) rejection/negative 테스트를
4개로 늘리고, (b) sleep 실패를 주입해 fail-closed(무음 성공 금지)를
증명하는 테스트, (c) fallback이 "가장 보수적" 불변식을 잃으면 실제로
pytest가 red가 되는 것까지 증명하는 gate/CI red-line 회귀 테스트를 추가한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind
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


async def test_conservative_fallback_bucket_rejects_request_exceeding_its_own_burst() -> None:
    """negative test 2/4 — 프로파일 미스(fallback) 버킷도 자기 burst보다
    큰 요청은 즉시 거부해야 한다(fallback이라고 검증을 느슨히 하면 안 됨)."""
    fallback = get_rate_limit(KisAccountType.REAL, None)
    bucket = build_token_bucket(KisAccountType.REAL, _UNKNOWN_TR_ID)
    with pytest.raises(ExchangeError) as exc_info:
        await bucket.acquire(fallback.burst + 1, timeout=0.01)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED


async def test_paper_bucket_for_known_group_rejects_when_wait_exceeds_timeout() -> None:
    """negative test 3/4 — 모의(PAPER) 버킷도 리필 대기시간이 timeout을
    초과하면 429 상당 예외로 fail-fast해야 한다(무한 대기로 새지 않음)."""
    spec = get_rate_limit(KisAccountType.PAPER, tr_group_for(_KNOWN_TR_ID))
    bucket = build_token_bucket(KisAccountType.PAPER, _KNOWN_TR_ID)
    await bucket.acquire(spec.burst, timeout=0.01)  # 버스트 소진
    with pytest.raises(ExchangeError) as exc_info:
        # rate_per_sec(2/s)로는 timeout 0.001s 내에 1토큰도 리필 불가능
        await bucket.acquire(1, timeout=0.001)
    assert exc_info.value.kind == ExchangeErrorKind.RATE_LIMITED
    assert exc_info.value.retryable is True


def test_rate_limit_spec_with_non_positive_rate_rejected_by_underlying_bucket() -> None:
    """negative test 4/4 — 프로파일 표에 실수로 0/음수 한도가 들어가면
    `TokenBucket`의 fail-closed 검증(ValueError)이 그대로 표면화돼야
    한다(BR-2가 이를 삼켜 무제한 버킷을 만들면 안 됨)."""
    broken_spec = RateLimitSpec(rate_per_sec=0.0, burst=1.0, verified="ESTIMATED")
    with pytest.raises(ValueError):
        broken_spec.new_bucket()


async def test_injected_sleep_failure_propagates_and_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """failure-injection test — 대기 중 이벤트 루프/타이머가 실패하는
    상황(예: 프로세스 리소스 고갈)을 주입한다. `RateLimitSpec.new_bucket`의
    관측 래퍼(`_observed_sleep`)가 이 실패를 삼켜 "대기했다고 치고" 토큰을
    내주면 한도 초과 호출이 조용히 통과하는 사고가 된다 — 실패가 그대로
    전파돼야 fail-closed다. `observer.record_wait()`는 실패 이전에 이미
    호출돼 있어야 한다(대기 시도 자체는 관측값에 남아야 함)."""

    class _InjectedSleepFailure(OSError):
        pass

    async def failing_sleep(seconds: float) -> None:
        raise _InjectedSleepFailure("simulated event-loop timer failure")

    monkeypatch.setattr("src.exchanges.kis.rate_profile.asyncio.sleep", failing_sleep)

    observer = RateLimitWaitObserver()
    spec = RateLimitSpec(rate_per_sec=10.0, burst=1.0, verified="DOC_ONLY")
    bucket = spec.new_bucket(observer=observer)

    await bucket.acquire(1, timeout=1.0)  # 버스트 소진 — 아직 sleep 호출 없음
    assert observer.waits == 0

    with pytest.raises(_InjectedSleepFailure):
        await bucket.acquire(1, timeout=1.0)  # 리필 대기 필요 -> 주입된 실패 전파
    assert observer.waits == 1  # 대기 시도는 실패 전에 이미 관측됨


def test_pytest_gate_turns_red_when_fallback_stops_being_conservative(
    tmp_path: Path,
) -> None:
    """gate/CI red-line regression test — fallback이 "가장 보수적 한도"
    불변식을 잃도록 소스를 변조하면(fallback을 실전 한도보다 관대하게)
    `test_unknown_group_falls_back_to_most_conservative_limit`이 실제로
    green -> red로 뒤집힘을 증명한다(DEPTH_L4_BR task-1780 D2 미달 사유
    해소). 프로덕션 파일은 그대로 두고 자식 프로세스의 모듈 객체만
    변조한다(test_rate_limiter.py의 동일 기법 재사용)."""
    test_copy = tmp_path / "test_kis_rate_profile_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\nasyncio_mode = auto\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_unknown_group_falls_back_to_most_conservative_limit",
    ]
    env = dict(
        os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="", PYTHONIOENCODING="utf-8"
    )
    baseline = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "3 passed" in baseline.stdout

    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.exchanges.kis.rate_profile'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = 'RateLimitSpec(1.0, 1.0, \"ESTIMATED\")'\n"
        "assert source.count(guard) == 2\n"
        "mutated_src = source.replace(guard, 'RateLimitSpec(100.0, 100.0, \"ESTIMATED\")')\n"
        "mutant = compile(mutated_src, module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, encoding="utf-8", errors="replace",
        env=env, timeout=60, check=False,
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "3 failed" in mutated.stdout
