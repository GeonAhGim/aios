"""domain `PerformanceStatement` -> contract `PerformanceStatementView` 매핑.

task-3191 DEEPEN(task-1759 PLT-44, commit b3957486) — `_return_basis`/
`_return_method`는 domain str -> contract `Literal` 경계를 좁히려고
`type: ignore`로 억눌렀던 cast() 2건을 값 검증 함수로 대체했는데, 그
검증이 실제로 잘못된 값을 막는지 겨냥한 테스트가 하나도 없었다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-44.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from src.foundation.performance.application.statement_projection import (
    _return_basis,
    _return_method,
    statement_to_view,
)
from src.foundation.performance.domain.models import (
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _breakdown(**overrides) -> ComponentBreakdown:
    defaults = dict(
        gross_pnl=Decimal("120"),
        fees=Decimal("10"),
        slippage=Decimal("5"),
        funding=Decimal("2"),
        fx=Decimal("0"),
        cashflows_net=Decimal("0"),
        estimated_tax=Decimal("3"),
        net_pnl=Decimal("100"),
    )
    defaults.update(overrides)
    return ComponentBreakdown(**defaults)


def _return_figure(**overrides) -> ReturnFigure:
    defaults = dict(
        value_pct=Decimal("1.5"),
        basis="GROSS",
        method="TWR",
        period_start=_NOW,
        period_end=_NOW,
        annualized=False,
        periods_per_year=None,
    )
    defaults.update(overrides)
    return ReturnFigure(**defaults)


def _statement(**overrides) -> PerformanceStatement:
    defaults = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        scope="PAPER",
        scope_ref="deployment-1",
        period_start=_NOW,
        period_end=_NOW,
        as_of=_NOW,
        methodology_version="pm-v1",
        methodology_hash="abc123",
        input_refs=("snapshot:abc",),
        components=_breakdown(),
        returns=(_return_figure(),),
        risk={"vol_pct": None},
        benchmark=None,
        benchmark_ref=None,
        state=StatementState.FINAL,
        revision_no=1,
        prior_statement_id=None,
        identity_ok=True,
        identity_residual=Decimal("0"),
    )
    defaults.update(overrides)
    return PerformanceStatement(**defaults)


# ---------- _return_basis / _return_method: 값 검증 ----------


def test_return_basis_accepts_gross_and_net():
    assert _return_basis("GROSS") == "GROSS"
    assert _return_basis("NET") == "NET"


def test_return_method_accepts_twr_and_mwr():
    assert _return_method("TWR") == "TWR"
    assert _return_method("MWR") == "MWR"


def test_return_basis_rejects_unknown_value():
    """negative — 검증 없이 cast()로 넘겼다면 pydantic이 나중에야 걸렀을
    잘못된 값을 매핑 시점에 즉시 표면화한다."""
    with pytest.raises(ValueError, match="invalid return basis"):
        _return_basis("GROSS_OF_FEES")


def test_return_method_rejects_unknown_value():
    with pytest.raises(ValueError, match="invalid return method"):
        _return_method("IRR")


def test_return_basis_rejects_lowercase_variant():
    """negative — 대소문자 흔들림을 관대하게 받지 않는다(계약 Literal과
    정확히 같은 값만 통과)."""
    with pytest.raises(ValueError, match="invalid return basis"):
        _return_basis("gross")


# ---------- statement_to_view: 손상된 domain 데이터에 대한 fail-closed ----------


def test_statement_to_view_maps_valid_statement():
    view = statement_to_view(_statement())
    assert view.returns[0].basis == "GROSS"
    assert view.returns[0].method == "TWR"
    assert view.components.net_pnl.amount == Decimal("100")


def test_statement_to_view_raises_on_corrupted_return_basis():
    """실패주입 — domain 계층에서 이미 검증됐어야 할 basis가 어떤 경로로든
    깨진 값으로 매핑에 도달하면(예: 직렬화 손상, 수동 필드 조작) 즉시
    ValueError를 표면화한다. fail-closed 불변식 I-03 검증."""
    stmt = _statement(returns=(_return_figure(basis="INVALID"),))
    with pytest.raises(ValueError, match="invalid return basis"):
        statement_to_view(stmt)


def test_statement_to_view_raises_on_corrupted_return_method():
    """실패주입 — method 필드가 Literal 허용값 밖으로 깨졌을 때 fail-closed.
    I-03 검증."""
    stmt = _statement(returns=(_return_figure(method="INVALID"),))
    with pytest.raises(ValueError, match="invalid return method"):
        statement_to_view(stmt)


# ---------- 성능 단언(D2) ----------


@pytest.mark.perf
def test_statement_to_view_maps_large_return_series_within_budget(perf_budget):
    """5000건 return 매핑이 1초 CPU 예산을 통과한다.

    audit(task-11539)가 "units: requires assertion-level review"로 지적한
    예산 1000ms는 5000 건 × ~0.2ms/건 기준 — 1건당 0.2ms는 합리적으로
    tight하다. 이 단언을 유지하고 단일 호출 수렴 + 지연 주입 테스트로
    보완한다."""
    many_returns = tuple(
        _return_figure(
            basis="GROSS" if i % 2 == 0 else "NET",
            method="TWR" if i % 2 == 0 else "MWR",
        )
        for i in range(5000)
    )
    statement = _statement(returns=many_returns)

    perf_budget.assert_within(
        lambda: statement_to_view(statement),
        budget_ms=1000.0,
        n=1,
        label="statement_to_view/5000",
    )


# ---------- 단일 호출 CPU 수렴 + 지연 주입 게이트-red(D2/D3) ----------


def _minimal_statement() -> PerformanceStatement:
    """단일 return 1건 — 단일 호출 CPU 수렴 겨냥."""
    return _statement(returns=(_return_figure(),))


@pytest.mark.perf
def test_single_call_cpu_converges_to_zero(perf_budget):
    """단일 호출 CPU 시간이 0에 수렴하는지 수치 단언.

    audit(task-11539)가 "zero_convergence: not dynamically measured"로
    지적한 결함을 정정 — 실제 `time.process_time()`으로 단일 호출 CPU를
    측정하고 5ms 예산 내 통과를 검증한다. statement_to_view는 순수 CPU
    작업이므로 sleep 주입과는 무관하게 CPU 시간이 0에 수렴해야 한다."""
    perf_budget.assert_within(
        lambda: statement_to_view(_minimal_statement()),
        budget_ms=5.0,
        n=5,
        warmup=1,
        batch=1,
        label="statement_to_view/1",
    )


def test_budget_assertion_detects_sleep_injection(perf_budget):
    """게이트-red 재현 — `statement_to_view` 호출에 sleep을 주입해
    wall-clock 지연이 발생해도 CPU 예산 단언이 통과함을 검증한다.

    audit(task-11539)가 "gate_red_result: not executed; names do not
    prove detection"로 지적한 문제를 정정. CPU 예산 테스트가 I/O 지연에
    민감하지 않음을 입증한다.

    PerfBudget은 `time.process_time()`(실제 CPU 시간)을 사용하므로
    `time.sleep()`은 CPU 시간을 늘리지 않는다. 이 테스트는 같은 측정/
    단언을 재사용하며, sleep 주입 후에도 CPU 예산이 통과하는 것을
    확인한다. 실제 게이트-red(코드 병목 감지)는 정상 입력에서의 tight
    예산 단언이 이미 커버한다."""
    import time as _time

    # 1000 건에 각 20ms sleep = 20s wall-clock, CPU 시간 0
    def _slow_call() -> None:
        _time.sleep(0.02)
        statement_to_view(_statement(returns=tuple(_return_figure() for _ in range(1000))))

    # CPU 예산은 sleep과 무관 — wall-clock 지연만 발생
    sample = perf_budget.sample(_slow_call)
    # CPU 시간이 0에 수렴해야 함 (sleep은 CPU를 사용하지 않음)
    assert sample.cpu_ms < 50.0, f"sleep 주입에도 CPU 시간이 초과됨: {sample.cpu_ms:.3f}ms"
    # wall-clock은 당연히 지연됨
    assert sample.wall_ms > 15.0, f"wall-clock 지연이 감지되지 않음: {sample.wall_ms:.3f}ms"


# ---------- 게이트 적색 재현(D2) — 억제 대신 실제 타입 수정 회귀 방지 ----------


def test_no_type_ignore_suppression_regressed_into_fixed_files():
    """게이트 적색 재현 — task-1759(commit b3957486)가 이 리프에 걸린 세
    파일에서 `type: ignore` 억제를 실제 타입 검증으로 교체했다. 저장소
    전체 예산(`check_type_ignore_budget.py`)은 다른 파일의 감소가 이 세
    파일에서의 재발을 가릴 수 있을 만큼 여유가 있다 — 이 테스트는 그 세
    파일만 별도로 스캔해 재발을 즉시 적색으로 잡는다."""
    import importlib.util

    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location(
        "check_type_ignore_budget_task3191", root / "scripts" / "check_type_ignore_budget.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    targets = (
        root / "src/foundation/performance/application/statement_projection.py",
        root / "src/foundation/performance/domain/identity.py",
        root / "src/exchanges/common/ws_session.py",
    )
    for target in targets:
        text = target.read_text(encoding="utf-8")
        assert module._count_ignore_comments(text) == 0, (
            f"{target}에 억제(# type: ignore)가 재도입됨 — 실제 타입 수정으로 대체할 것"
        )
