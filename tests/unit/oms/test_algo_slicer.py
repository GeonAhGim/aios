"""TWAP/VWAP/POV/iceberg 슬라이스 계획기 단위테스트 — L4-06. DB 없음."""
from __future__ import annotations

import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.trading import OrderSide
from src.services.oms.contracts.v1_commands import AlgoRequest, OrderIdempotencyScope
from src.services.oms.domain.algo_slicer import plan_slices


def _scope() -> OrderIdempotencyScope:
    return OrderIdempotencyScope(
        tenant_id=uuid4(),
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )


def _request(**overrides: object) -> AlgoRequest:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    defaults: dict[str, object] = {
        "algo_run_id": uuid4(),
        "trace_id": uuid4(),
        "scope": _scope(),
        "algo": "TWAP",
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "total_quantity": Decimal("1"),
        "start_at": start,
        "end_at": start + timedelta(minutes=10),
        "slice_count": 5,
        "seed": 42,
    }
    defaults.update(overrides)
    return AlgoRequest(**defaults)  # type: ignore[arg-type]


def test_slices_sum_exactly_to_total_quantity() -> None:
    req = _request(total_quantity=Decimal("1"), slice_count=7)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert sum(p.quantity for p in plans) == req.total_quantity


def test_slice_count_matches_request() -> None:
    req = _request(slice_count=5)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert len(plans) == 5


def test_same_seed_produces_identical_plan() -> None:
    req = _request()
    plans_a = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    plans_b = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert plans_a == plans_b


def test_different_seed_produces_different_plan() -> None:
    req = _request()
    plans_a = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(1))
    plans_b = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(2))
    assert plans_a != plans_b


def test_all_scheduled_times_within_window() -> None:
    req = _request()
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    for plan in plans:
        assert req.start_at <= plan.scheduled_at <= req.end_at


def test_all_quantities_non_negative() -> None:
    req = _request(size_jitter_pct=Decimal("90"))
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert all(p.quantity >= 0 for p in plans)


def test_participation_cap_limits_non_final_slices() -> None:
    """참여율 상한(anti-front-running) — 슬라이스 qty는 그 구간 예상
    거래량 × max_participation_pct를 넘지 않는다(마지막 슬라이스는 잔량
    흡수라 예외)."""
    req = _request(
        total_quantity=Decimal("100"), slice_count=4, max_participation_pct=Decimal("10")
    )
    volume_profile = [Decimal("50"), Decimal("50"), Decimal("50"), Decimal("50")]
    plans = plan_slices(
        req, now=req.start_at, volume_profile=volume_profile, rng=random.Random(req.seed)
    )
    for plan in plans[:-1]:
        assert plan.quantity <= Decimal("5")  # 50 * 10%


def test_last_slice_absorbs_remainder_even_under_participation_cap() -> None:
    """참여율 상한 때문에 앞선 슬라이스들이 원래 몫보다 적게 나가도,
    마지막 슬라이스가 남은 전량을 흡수해 합계는 정확히 total과 같다."""
    req = _request(
        total_quantity=Decimal("100"), slice_count=4, max_participation_pct=Decimal("1")
    )
    volume_profile = [Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10")]
    plans = plan_slices(
        req, now=req.start_at, volume_profile=volume_profile, rng=random.Random(req.seed)
    )
    assert sum(p.quantity for p in plans) == req.total_quantity
    assert plans[-1].quantity > plans[0].quantity


def test_invalid_time_window_raises() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    req = _request(start_at=start, end_at=start)
    with pytest.raises(ValueError, match="end_at"):
        plan_slices(req, now=start, volume_profile=None, rng=random.Random(req.seed))


def test_end_at_before_start_at_raises() -> None:
    """등호(==)가 아니라 end_at < start_at인 별도 경계도 거부돼야 한다."""
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    req = _request(start_at=start, end_at=start - timedelta(minutes=1))
    with pytest.raises(ValueError, match="end_at"):
        plan_slices(req, now=start, volume_profile=None, rng=random.Random(req.seed))


def test_slice_count_zero_rejected_by_contract() -> None:
    """slice_count는 계약(§3.1)에서 ge=1 — 0은 도메인이 아니라 계약 경계에서 거부."""
    with pytest.raises(ValidationError):
        _request(slice_count=0)


def test_slice_count_over_max_rejected_by_contract() -> None:
    """slice_count 상한(le=500, §9 L4-06 DoD 경계)을 넘는 값은 계약에서 거부."""
    with pytest.raises(ValidationError):
        _request(slice_count=501)


class _CrashingRandom(random.Random):
    """rng는 계획기에 주입되는 유일한 외부 의존성(docstring §3.1 seed 재현성) —
    원격 무작위화 백엔드가 중간에 죽는 상황(크래시/네트워크 단절)을 흉내낸다.
    N번째 호출까지는 정상 응답하고, 그 다음 호출부터 강제로 예외를 던진다."""

    def __init__(self, seed: int, crash_after: int) -> None:
        super().__init__(seed)
        self._remaining = crash_after

    def uniform(self, a: float, b: float) -> float:
        if self._remaining <= 0:
            raise RuntimeError("SIMULATED_RNG_BACKEND_CRASH")
        self._remaining -= 1
        return super().uniform(a, b)


def test_rng_backend_crash_propagates_without_partial_plan() -> None:
    """주입된 rng가 크래시하면 오류를 삼키거나 절반만 채운 계획을 반환하지
    않고 그대로 전파해야 한다(fail-closed) — 부분 성공을 성공으로 위장 금지."""
    req = _request(slice_count=5)
    crashing_rng = _CrashingRandom(req.seed, crash_after=1)
    with pytest.raises(RuntimeError, match="SIMULATED_RNG_BACKEND_CRASH"):
        plan_slices(req, now=req.start_at, volume_profile=None, rng=crashing_rng)


def test_max_slice_count_planning_latency_budget() -> None:
    """500슬라이스(계약 상한) 계획을 100회 반복 — 로컬 회귀 예산이며 SLO 아님."""
    req = _request(slice_count=500, total_quantity=Decimal("500"))
    plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    started = perf_counter()
    for _ in range(100):
        plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    elapsed = perf_counter() - started
    assert elapsed < 20.0, f"100 x 500-slice plans took {elapsed:.3f}s (budget 20.0s)"


def test_pytest_gate_turns_red_when_remainder_absorption_is_removed(tmp_path: Path) -> None:
    """실제 회귀 테스트가 통과하는 걸 먼저 확인하고, 마지막 슬라이스 잔량
    흡수 가드를 제거하면 pytest가 exit 1로 red가 되는 것까지 증명한다."""
    test_copy = tmp_path / "test_algo_slicer_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "-c", str(config), "--confcutdir", str(tmp_path),
        f"{test_copy}::test_slices_sum_exactly_to_total_quantity",
    ]
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="")
    baseline = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert baseline.returncode == 0, baseline.stdout + baseline.stderr
    assert "1 passed" in baseline.stdout

    # 프로덕션 소스 파일은 그대로 두고, 이 자식 프로세스의 모듈 객체만 변조한다.
    (tmp_path / "conftest.py").write_text(
        "import importlib\nfrom pathlib import Path\n"
        "name = 'src.services.oms.domain.algo_slicer'\n"
        "module = importlib.import_module(name)\n"
        "source = Path(module.__file__).read_text(encoding='utf-8')\n"
        "guard = '        if is_last:'\n"
        "assert source.count(guard) == 1\n"
        "mutant = compile(source.replace(guard, '        if False:'), module.__file__, 'exec')\n"
        "exec(mutant, module.__dict__)\n",
        encoding="utf-8",
    )
    mutated = subprocess.run(
        command, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert mutated.returncode == 1, mutated.stdout + mutated.stderr
    assert "1 failed" in mutated.stdout
