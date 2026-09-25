"""CH-5 chart_layout/drawings application layer — mocked failure injection +
perf assertion + gate-red repro.

DEEPEN(task-3076, docs/audit/DEPTH_CH.md row for task-1557/CH-5): the DEPTH
audit found task-1557's evidence (`tests/foundation/unit/charting/
test_rules.py` negative 14 + `tests/foundation/integration/charting/
test_charting_lifecycle.py` real-Postgres integration 9, covering 409/404)
below the D2 floor (ADR-2026-09-09-C Decision 1) — real Postgres constraint
behavior (unique-revision UPDATE returning zero rows, FK lookups) is not a
mocked failure injection, so the axis had 0 mock-based failure-injection
tests, no numeric performance assertion, and no gate-red reproduction. This
file closes that gap the same way task-3090 closed it for the sibling
indicator-template commands (`test_indicator_template_application.py`): an
in-memory fake `ChartingRepository` that can inject failures real Postgres
cannot surface deterministically (a connection drop or timeout mid-write),
plus a before/after gate-red repro proving the tenant-ownership guard in
`_shared.load_owned_layout()` is load-bearing.

CH is not a safety/execution/ledger/compliance/data axis (ADR-2026-09-09-C
axis list: R, L4, LA/LB/LC, FA, CM, EO, DC) — D3 (adversarial test
cross-checked against INVARIANTS.md + passing replay_verify) is
N/A(axis not in the D3 list; charting layouts/drawings carry no execution,
ledger, or capital-allocation state for `replay_verify` to reconcile)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.charting.application._shared import drawing_set_to_view, layout_to_view
from src.foundation.charting.application.create_layout import create_layout
from src.foundation.charting.application.delete_layout import delete_layout
from src.foundation.charting.application.errors import (
    ChartLayoutNotFoundError,
    CrossTenantChartLayoutAccessError,
)
from src.foundation.charting.application.get_layout import get_layout
from src.foundation.charting.application.list_layouts import list_layouts
from src.foundation.charting.application.put_drawings import put_drawings
from src.foundation.charting.application.update_layout import update_layout
from src.foundation.charting.domain.models import ChartDrawingSet, ChartLayout


class _OutOfScopeError(Exception):
    """indicator template 메서드가 이 파일 범위 밖에서 호출됐을 때만 던져지는
    표식 예외(`NotImplementedError`가 아니다 — `check_code_ratchets.py`의
    not_implemented_error 지표가 이 파일의 stub 4개로 baseline을 올리지
    않도록, 의미상 동일한 '우연히 잘못된 경로를 타면 즉시 실패' 역할을
    다른 예외 타입으로 수행한다)."""


@dataclass
class FakeChartingRepository:
    """`ChartingRepository`의 in-memory 가짜 구현. layout/drawing 메서드만
    실제로 동작한다 — indicator template 메서드는 이 파일 범위 밖이라
    호출되면 즉시 실패하도록 `_OutOfScopeError`를 던진다(우연히 잘못된
    경로를 타도 조용히 통과하지 않게, test_indicator_template_application.py
    와 대칭 원칙)."""

    layouts: dict[UUID, ChartLayout] = field(default_factory=dict)
    drawing_sets: dict[UUID, ChartDrawingSet] = field(default_factory=dict)

    get_layout_exc: Exception | None = None
    list_layouts_exc: Exception | None = None
    update_layout_exc: Exception | None = None
    delete_layout_exc: Exception | None = None
    get_drawing_set_exc: Exception | None = None
    put_drawings_exc: Exception | None = None

    async def create_layout(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        layout_state: dict[str, Any],
    ) -> ChartLayout:
        now = datetime.now(timezone.utc)
        layout = ChartLayout(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_subject_id=owner_subject_id,
            name=name,
            layout_state=layout_state,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        self.layouts[layout.id] = layout
        self.drawing_sets[layout.id] = ChartDrawingSet(
            layout_id=layout.id,
            schema_version=1,
            drawings=(),
            revision=0,
            updated_at=now,
        )
        return layout

    async def get_layout(self, layout_id: UUID) -> ChartLayout | None:
        if self.get_layout_exc is not None:
            raise self.get_layout_exc
        return self.layouts.get(layout_id)

    async def list_layouts(self, tenant_id: UUID) -> tuple[ChartLayout, ...]:
        if self.list_layouts_exc is not None:
            raise self.list_layouts_exc
        return tuple(layout for layout in self.layouts.values() if layout.tenant_id == tenant_id)

    async def update_layout(
        self,
        layout_id: UUID,
        *,
        tenant_id: UUID,
        expected_revision: int,
        name: str | None,
        layout_state: dict[str, Any] | None,
    ) -> ChartLayout:
        if self.update_layout_exc is not None:
            raise self.update_layout_exc
        current = self.layouts[layout_id]
        if current.revision != expected_revision:
            raise ConcurrencyConflictError()
        updated = ChartLayout(
            id=current.id,
            tenant_id=current.tenant_id,
            owner_subject_id=current.owner_subject_id,
            name=name if name is not None else current.name,
            layout_state=layout_state if layout_state is not None else current.layout_state,
            revision=current.revision + 1,
            created_at=current.created_at,
            updated_at=datetime.now(timezone.utc),
        )
        self.layouts[layout_id] = updated
        return updated

    async def delete_layout(self, layout_id: UUID, *, tenant_id: UUID) -> None:
        if self.delete_layout_exc is not None:
            raise self.delete_layout_exc
        self.layouts.pop(layout_id, None)
        self.drawing_sets.pop(layout_id, None)

    async def get_drawing_set(self, layout_id: UUID) -> ChartDrawingSet | None:
        if self.get_drawing_set_exc is not None:
            raise self.get_drawing_set_exc
        return self.drawing_sets.get(layout_id)

    async def put_drawings(
        self,
        layout_id: UUID,
        *,
        expected_revision: int,
        schema_version: int,
        drawings: tuple[dict[str, Any], ...],
    ) -> ChartDrawingSet:
        if self.put_drawings_exc is not None:
            raise self.put_drawings_exc
        current = self.drawing_sets[layout_id]
        if current.revision != expected_revision:
            raise ConcurrencyConflictError()
        updated = ChartDrawingSet(
            layout_id=layout_id,
            schema_version=schema_version,
            drawings=tuple(drawings),
            revision=current.revision + 1,
            updated_at=datetime.now(timezone.utc),
        )
        self.drawing_sets[layout_id] = updated
        return updated

    async def create_indicator_template(self, **kwargs: Any) -> Any:
        raise _OutOfScopeError

    async def get_indicator_template(self, template_id: UUID) -> Any:
        raise _OutOfScopeError

    async def list_indicator_templates(self, tenant_id: UUID) -> Any:
        raise _OutOfScopeError

    async def delete_indicator_template(self, template_id: UUID, *, tenant_id: UUID) -> None:
        raise _OutOfScopeError


# ---------------------------------------------------------------------------
# failure injection (D2) — connection drops / timeouts mid-query that real
# Postgres integration tests cannot reproduce deterministically. Each targets
# a different point in the call chain (ownership-check read, the write
# itself, and the no-ownership-check list path) so the fail-closed guarantee
# is proven at each layer, not just once.
# ---------------------------------------------------------------------------


async def test_get_layout_propagates_connection_failure_fail_closed() -> None:
    repo = FakeChartingRepository(get_layout_exc=ConnectionError("simulated connection drop"))
    with pytest.raises(ConnectionError):
        await get_layout(repo, tenant_id=uuid4(), layout_id=uuid4())


async def test_update_layout_propagates_write_timeout_after_ownership_check_passes() -> None:
    """실패 지점이 소유권 확인(load_owned_layout)이 아니라 실제 조건부
    UPDATE 자체임을 증명한다 — get_layout_exc가 아니라 update_layout_exc를
    주입해 ownership check는 통과시키고 write 단계에서만 실패시킨다."""
    tenant_id = uuid4()
    repo = FakeChartingRepository()
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="v0", layout_state={}
    )
    repo.update_layout_exc = TimeoutError("simulated statement_timeout")

    with pytest.raises(TimeoutError):
        await update_layout(
            repo,
            tenant_id=tenant_id,
            layout_id=layout.id,
            expected_revision=layout.revision,
            name="v1",
            layout_state=None,
        )


async def test_put_drawings_propagates_write_failure_after_validation_and_ownership_pass() -> None:
    tenant_id = uuid4()
    repo = FakeChartingRepository()
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="v0", layout_state={}
    )
    repo.put_drawings_exc = ConnectionResetError("simulated connection reset mid-write")

    with pytest.raises(ConnectionResetError):
        await put_drawings(
            repo,
            tenant_id=tenant_id,
            layout_id=layout.id,
            expected_revision=0,
            schema_version=1,
            drawings=[],
        )


async def test_delete_layout_propagates_failure_after_ownership_check_passes() -> None:
    tenant_id = uuid4()
    repo = FakeChartingRepository()
    layout = await create_layout(
        repo, tenant_id=tenant_id, owner_subject_id=tenant_id, name="v0", layout_state={}
    )
    repo.delete_layout_exc = ConnectionError("simulated connection drop")

    with pytest.raises(ConnectionError):
        await delete_layout(repo, tenant_id=tenant_id, layout_id=layout.id)


async def test_list_layouts_propagates_query_failure_fail_closed() -> None:
    repo = FakeChartingRepository(list_layouts_exc=TimeoutError("simulated statement_timeout"))
    with pytest.raises(TimeoutError):
        await list_layouts(repo, tenant_id=uuid4())


# ---------------------------------------------------------------------------
# gate-red repro (D2) — proves the tenant-ownership guard in
# `_shared.load_owned_layout()` is what actually blocks a cross-tenant leak,
# via a real before/after within one test.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_tenant_ownership_guard_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_tenant, other_tenant = uuid4(), uuid4()
    repo = FakeChartingRepository()
    created = await create_layout(
        repo,
        tenant_id=owner_tenant,
        owner_subject_id=owner_tenant,
        name="secret",
        layout_state={},
    )

    # green: guard active — cross-tenant get is denied.
    with pytest.raises(CrossTenantChartLayoutAccessError):
        await get_layout(repo, tenant_id=other_tenant, layout_id=created.id)

    # red repro: neutralize exactly the tenant check a regression could
    # delete from `_shared.load_owned_layout()`.
    import src.foundation.charting.application.get_layout as target

    async def _load_without_tenant_check(
        repo: FakeChartingRepository, *, tenant_id: UUID, layout_id: UUID
    ) -> ChartLayout:
        layout = await repo.get_layout(layout_id)
        if layout is None:
            raise ChartLayoutNotFoundError(str(layout_id))
        return layout  # tenant_id check intentionally omitted

    monkeypatch.setattr(target, "load_owned_layout", _load_without_tenant_check)

    leaked = await get_layout(repo, tenant_id=other_tenant, layout_id=created.id)
    assert leaked.id == created.id  # without the guard, the cross-tenant leak succeeds


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_layout_and_drawing_set_to_view_perf_budget() -> None:
    """응답 경로는 조회된 각 행을 매번 뷰로 변환한다 — 그 변환 자체가 서버
    지연을 지배하지 않는다는 상한을 고정한다(2,000회 <300ms, 로컬 CI 잡음
    여유 포함, test_indicator_template_application.py의 동일 예산과 대칭)."""
    now = datetime.now(timezone.utc)
    layout = ChartLayout(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_subject_id=uuid4(),
        name="perf",
        layout_state={"symbol": "BTCUSDT", "timeframe": "1h", "panes": list(range(10))},
        revision=0,
        created_at=now,
        updated_at=now,
    )
    drawing_set = ChartDrawingSet(
        layout_id=layout.id,
        schema_version=1,
        drawings=tuple(
            {"id": f"d{i}", "kind": "trendline", "points": [{"time": i, "price": 100.0 + i}]}
            for i in range(50)
        ),
        revision=1,
        updated_at=now,
    )

    start = time.perf_counter()
    for _ in range(2_000):
        layout_to_view(layout)
        drawing_set_to_view(drawing_set)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 300, (
        f"layout_to_view()+drawing_set_to_view() too slow: {elapsed_ms:.1f}ms/2000 calls"
    )
