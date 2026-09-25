"""domain `PerformanceStatement` -> contract `PerformanceStatementView` 매핑.

task-3191 DEEPEN(task-1759 PLT-44, commit b3957486) — `_return_basis`/
`_return_method`는 domain str -> contract `Literal` 경계를 좁히려고
`type: ignore`로 억눌렀던 cast() 2건을 값 검증 함수로 대체했는데, 그
검증이 실제로 잘못된 값을 막는지 겨냥한 테스트가 하나도 없었다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-44.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
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

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


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
    깨진 값으로 매핑에 도달하면(예: 백필/마이그레이션 중 잘못 채워진 리비전
    데이터), 잘못된 값을 조용히 통과시키지 않고 즉시 실패한다."""
    corrupted = _statement(returns=(_return_figure(basis="INVALID"),))
    with pytest.raises(ValueError, match="invalid return basis"):
        statement_to_view(corrupted)


def test_statement_to_view_raises_on_corrupted_return_method():
    corrupted = _statement(returns=(_return_figure(method="INVALID"),))
    with pytest.raises(ValueError, match="invalid return method"):
        statement_to_view(corrupted)


# ---------- 성능 단언(D2) ----------


@pytest.mark.perf
def test_statement_to_view_maps_large_return_series_within_budget():
    many_returns = tuple(
        _return_figure(
            basis="GROSS" if i % 2 == 0 else "NET",
            method="TWR" if i % 2 == 0 else "MWR",
        )
        for i in range(5000)
    )
    statement = _statement(returns=many_returns)

    started = time.perf_counter()
    view = statement_to_view(statement)
    elapsed = time.perf_counter() - started

    assert len(view.returns) == 5000
    assert elapsed <= 1.0, f"5000건 매핑에 {elapsed:.3f}s — 예산(1.0s) 초과"


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
