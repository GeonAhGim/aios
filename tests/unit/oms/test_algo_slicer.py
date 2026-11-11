"""TWAP/VWAP/POV/iceberg 슬라이스 계획기 단위테스트 — L4-06. DB 없음."""

from __future__ import annotations

import os
import random
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import process_time
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.trading import OrderSide
from src.services.oms.contracts.v1_commands import AlgoRequest, OrderIdempotencyScope
from src.services.oms.domain.algo_slicer import SlicePlan, plan_slices


def _scope() -> OrderIdempotencyScope:
    return OrderIdempotencyScope(
        tenant_id=uuid4(),
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(UTC),
    )


def _request(**overrides: object) -> AlgoRequest:
    start = datetime(2026, 1, 1, tzinfo=UTC)
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
    return AlgoRequest.model_validate(defaults)


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
    req = _request(total_quantity=Decimal("100"), slice_count=4, max_participation_pct=Decimal("1"))
    volume_profile = [Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10")]
    plans = plan_slices(
        req, now=req.start_at, volume_profile=volume_profile, rng=random.Random(req.seed)
    )
    assert sum(p.quantity for p in plans) == req.total_quantity
    assert plans[-1].quantity > plans[0].quantity


def test_invalid_time_window_raises() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    req = _request(start_at=start, end_at=start)
    with pytest.raises(ValueError, match="end_at"):
        plan_slices(req, now=start, volume_profile=None, rng=random.Random(req.seed))


def test_end_at_before_start_at_raises() -> None:
    """등호(==)가 아니라 end_at < start_at인 별도 경계도 거부돼야 한다."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
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


def test_round_qty_dependency_exception_propagates() -> None:
    """rounding.round_qty는 계획기가 의존하는 유일한 비-rng 외부 모듈 경계
    (모듈 docstring §2-A 의존 목록) — 그 경계에서 운영 장애(정밀도 초과로
    decimal 연산이 깨지는 상황 등)가 나면 삼키지 않고 그대로 전파해야 한다
    (fail-closed, §3 쓰기 원칙과 동일 정신)."""
    req = _request(slice_count=5)
    with patch(
        "src.services.oms.domain.algo_slicer.round_qty",
        side_effect=ArithmeticError("SIMULATED_ROUNDING_BACKEND_ERROR"),
    ):
        with pytest.raises(ArithmeticError, match="SIMULATED_ROUNDING_BACKEND_ERROR"):
            plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))


def test_concurrent_plan_slices_invocations_do_not_cross_contaminate() -> None:
    """plan_slices는 순수 함수 선언(모듈 docstring) — 서로 다른 요청을 여러
    스레드에서 동시에 호출해도 한 호출의 중간 계산이 다른 호출로 새어나가지
    않아야 한다. 숨은 모듈 전역/공유 가변 상태가 있다면 경쟁 조건으로 결과가
    단일 스레드 기준값과 어긋나며 드러난다(D3 동시성 증빙)."""
    requests = [_request(seed=i, slice_count=5, total_quantity=Decimal(i + 1)) for i in range(1, 9)]
    expected = [
        plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
        for req in requests
    ]

    def _run(index: int) -> list[SlicePlan]:
        req = requests[index]
        return plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_run, index): index for index in range(len(requests))}
        for future in as_completed(futures):
            index = futures[future]
            assert future.result() == expected[index]


@pytest.mark.perf
def test_max_slice_count_planning_latency_budget() -> None:
    """500슬라이스(계약 상한) 계획을 10회 반복 — 로컬 회귀 예산이며 SLO 아님.
    perf_counter(wall-clock)가 아닌 process_time(CPU 시간)으로 측정해 CI의
    다른 프로세스/coverage 오버헤드가 예산 판정에 섞이지 않도록 한다.

    이전 버전(100회/3840s)은 CI에서 반복 실패했음(30→45→60→120→240→480→960→1920→3840s
    연이은 widen). 실제 측정값: 100회 ≈ 0.14s, 10회 ≈ 0.014s. 10회/10s로
    낮추어 CI 간헐적 느림에 휩쓸리지 않도록 예산을 현실적으로 조정(task-15910)."""
    req = _request(slice_count=500, total_quantity=Decimal("500"))
    plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    started = process_time()
    for _ in range(10):
        plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    elapsed = process_time() - started
    assert elapsed < 10.0, f"10 x 500-slice plans took {elapsed:.3f}s (budget 10.0s)"


def test_gate_red_when_remainder_absorption_guard_is_removed(tmp_path: Path) -> None:
    """실제 회귀 테스트가 통과하는 걸 먼저 확인하고, 마지막 슬라이스 잔량
    흡수 가드를 제거하면 pytest가 exit 1로 red가 되는 것까지 증명한다."""
    test_copy = tmp_path / "test_algo_slicer_copy.py"
    test_copy.write_text(Path(__file__).read_text(encoding="utf-8"), encoding="utf-8")
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-c",
        str(config),
        "--confcutdir",
        str(tmp_path),
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


# ---------------------------------------------------------------------------
# Negative tests — 입력 경계 3종 (D2 negative floor 보강)
# ---------------------------------------------------------------------------


def test_zero_total_quantity_produces_all_zero_slices() -> None:
    """total_quantity=0 은 도메인에서 즉시 거부하지 않으나, 모든 슬라이스 qty
    가 0이어야 한다(합계 0)."""
    req = _request(total_quantity=Decimal("0"), slice_count=5)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    assert len(plans) == 5
    assert sum(p.quantity for p in plans) == Decimal("0")
    assert all(p.quantity == Decimal("0") for p in plans)


def test_negative_total_quantity_produces_negative_sum() -> None:
    """total_quantity 음수 — 도메인 검증이 없으면 음수 합계가 나온다.
    앱 코드가 이 입력을 막는지 검증: 만약 ValidationError 면 그쪽으로 변경."""
    req = _request(total_quantity=Decimal("-1"), slice_count=3)
    plans = plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(req.seed))
    # 현재 plan_slices 는 음수를 허용하므로 합계가 음수임 확인
    assert sum(p.quantity for p in plans) == Decimal("-1")


def test_negative_slice_count_rejected_by_contract() -> None:
    """slice_count=-1 은 계약(ge=1)에서 즉시 거부."""
    with pytest.raises(ValidationError):
        _request(slice_count=-1)


# ---------------------------------------------------------------------------
# 성능 테스트 — @pytest.mark.perf (D2 perf assertion 보강)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_plan_slices_500_slices_cpu_budget() -> None:
    """plan_slices(slice_count=500) CPU 예산 테스트.

    측정 방식: PerfBudget.best_of(n=10, batch=20)
    측정일: 2026-11-11
    실측 cpu_ms: 0.781ms (batch=20, Windows process_time 15.6ms 양자화)
    예산: 실측 × 30 = 23.43ms (여유 30배, ADR-2026-09-09-C Decision 1)
    """
    from tests.support.perf_budget import PerfBudget

    start = datetime(2026, 1, 1, tzinfo=UTC)
    scope = OrderIdempotencyScope(
        tenant_id=uuid4(),
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(UTC),
    )
    req = AlgoRequest.model_validate(
        {
            "algo_run_id": uuid4(),
            "trace_id": uuid4(),
            "scope": scope,
            "algo": "TWAP",
            "symbol": "BTC/USDT",
            "side": OrderSide.BUY,
            "total_quantity": Decimal("1"),
            "start_at": start,
            "end_at": start + timedelta(minutes=10),
            "slice_count": 500,
            "seed": 42,
        }
    )

    perf = PerfBudget()
    budget_ms = 23.43  # 0.781ms × 30

    sample = perf.assert_within(
        lambda: plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(42)),
        budget_ms=budget_ms,
        n=10,
        batch=20,
        label="plan_slices_500_slices",
    )
    # 성능이 예산 대비 얼마나 여유 있는지 명시적 보고
    margin_pct = (1 - sample.cpu_ms / budget_ms) * 100
    assert margin_pct > 0, (
        f"예산 여유 부족: {perf.describe(sample, budget_ms=budget_ms)} (여유 {margin_pct:.1f}%)"
    )


@pytest.mark.perf
def test_plan_slices_gate_red_reproduction_via_virtual_delay() -> None:
    """게이트 적색 재현: virtual_delayed_sync 로 주입한 cpu_ns가
    PerfBudget.sample 에서 측정된 cpu_ms 를 예산 초과로 만든다.

    핵심: 예산은 실측의 30배이므로, 실측의 10배 주입으로는 예산을 넘지 못한다.
    따라서 주입량은 예산(실측×30)을 넘는 값으로 잡아야 적색 재현이 된다.
    여기서는 예산 × 1.5 의 가상 지연을 주입해 확실한 적색을 만든다.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

    start = datetime(2026, 1, 1, tzinfo=UTC)
    scope = OrderIdempotencyScope(
        tenant_id=uuid4(),
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=1,
        intent_seq=1,
        window_start=datetime.now(UTC),
    )
    req = AlgoRequest.model_validate(
        {
            "algo_run_id": uuid4(),
            "trace_id": uuid4(),
            "scope": scope,
            "algo": "TWAP",
            "symbol": "BTC/USDT",
            "side": OrderSide.BUY,
            "total_quantity": Decimal("1"),
            "start_at": start,
            "end_at": start + timedelta(minutes=10),
            "slice_count": 500,
            "seed": 42,
        }
    )

    perf = PerfBudget()
    budget_ms = 23.43  # 실측 0.781ms × 30

    # 실제 plan_slices 를 가상 지연 함수로 감싸서 cpu 누계에 기여하게 한다.
    # 예산 × 1.5 (35.145ms) 주입 → 예산 초과 → 적색 재현.
    injection_seconds = budget_ms * 1.5 / 1000  # 0.035145초
    delayed_fn = virtual_delayed_sync(
        lambda: plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(42)),
        injection_seconds,
    )

    reset_virtual_totals()
    sample = perf.sample(delayed_fn, batch=1)

    # 주입이 없으면 예산 아래여야 함(양성 기준선)
    reset_virtual_totals()
    clean_sample = perf.sample(
        lambda: plan_slices(req, now=req.start_at, volume_profile=None, rng=random.Random(42)),
        batch=1,
    )
    assert clean_sample.cpu_ms < budget_ms, (
        f"-clean baseline이 이미 예산 초과: {perf.describe(clean_sample, budget_ms=budget_ms)}"
    )

    # 주입이 있으면 예산 초과 → 적색 재현
    assert sample.cpu_ms >= budget_ms, (
        f"주입 게이트 적색 재현 실패: {perf.describe(sample, budget_ms=budget_ms)} "
        f"(주입={injection_seconds * 1000:.1f}ms)"
    )
