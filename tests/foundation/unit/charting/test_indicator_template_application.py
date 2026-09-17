"""CH-17b indicator template application layer — mocked failure injection +
perf assertion + gate-red repro.

DEEPEN(task-3090, ADR-2026-09-09-C D2 floor for CH axis; docs/audit/
DEPTH_CH.md row 1904 missing-evidence): the existing
`tests/foundation/integration/charting/test_indicator_template_lifecycle.py`
only exercises real Postgres (constraint violations are genuine system
behavior, not injected failures — same reasoning DEPTH_CH.md applied to
task-1557). This file exercises the application layer against an in-memory
fake repository so it can inject failures real Postgres cannot surface
deterministically (a connection drop or timeout mid-query), and to prove
the tenant-ownership guard is load-bearing via a before/after gate-red
repro. CH is not a safety axis (ADR-2026-09-09-C axis list), so only the
D2 floor applies here — D3 (adversarial/replay/multi-instance) is out of
scope for this leaf."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.charting.application._shared import indicator_template_to_view
from src.foundation.charting.application.create_indicator_template import (
    create_indicator_template,
)
from src.foundation.charting.application.delete_indicator_template import (
    delete_indicator_template,
)
from src.foundation.charting.application.errors import (
    ChartIndicatorTemplateNotFoundError,
    CrossTenantChartIndicatorTemplateAccessError,
)
from src.foundation.charting.application.get_indicator_template import get_indicator_template
from src.foundation.charting.domain.models import ChartIndicatorTemplate


@dataclass
class FakeChartingRepository:
    """`ChartingRepository`의 in-memory 가짜 구현. indicator template
    메서드만 실제로 동작한다 — layout/drawing 메서드는 이 파일 범위 밖이라
    호출되지 않는다(실제 호출 시 None 반환으로 조용히 통과)."""

    templates: dict[UUID, ChartIndicatorTemplate] = field(default_factory=dict)
    get_exc: Exception | None = None
    create_exc: Exception | None = None

    async def create_layout(self, **kwargs: Any) -> Any:
        return None

    async def get_layout(self, layout_id: UUID) -> Any:
        return None

    async def list_layouts(self, tenant_id: UUID) -> Any:
        return ()

    async def update_layout(self, layout_id: UUID, **kwargs: Any) -> Any:
        return None

    async def delete_layout(self, layout_id: UUID, *, tenant_id: UUID) -> None:
        pass

    async def get_drawing_set(self, layout_id: UUID) -> Any:
        return None

    async def put_drawings(self, layout_id: UUID, **kwargs: Any) -> Any:
        return None

    async def create_indicator_template(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        template: dict[str, Any],
    ) -> ChartIndicatorTemplate:
        if self.create_exc is not None:
            raise self.create_exc
        now = datetime.now(timezone.utc)
        created = ChartIndicatorTemplate(
            id=uuid4(),
            tenant_id=tenant_id,
            owner_subject_id=owner_subject_id,
            name=name,
            template=template,
            revision=0,
            created_at=now,
            updated_at=now,
        )
        self.templates[created.id] = created
        return created

    async def get_indicator_template(self, template_id: UUID) -> ChartIndicatorTemplate | None:
        if self.get_exc is not None:
            raise self.get_exc
        return self.templates.get(template_id)

    async def list_indicator_templates(self, tenant_id: UUID) -> tuple[ChartIndicatorTemplate, ...]:
        return tuple(t for t in self.templates.values() if t.tenant_id == tenant_id)

    async def delete_indicator_template(self, template_id: UUID, *, tenant_id: UUID) -> None:
        existing = self.templates.get(template_id)
        if existing is not None and existing.tenant_id == tenant_id:
            self.templates.pop(template_id, None)


# ---------------------------------------------------------------------------
# failure injection (D2) — a dropped connection / timeout mid-query, which
# real Postgres integration tests cannot reproduce deterministically.
# ---------------------------------------------------------------------------


async def test_get_indicator_template_propagates_connection_failure_fail_closed() -> None:
    repo = FakeChartingRepository(get_exc=ConnectionError("simulated connection drop"))
    with pytest.raises(ConnectionError):
        await get_indicator_template(repo, tenant_id=uuid4(), template_id=uuid4())


async def test_delete_indicator_template_propagates_connection_failure_fail_closed() -> None:
    repo = FakeChartingRepository(get_exc=ConnectionError("simulated connection drop"))
    with pytest.raises(ConnectionError):
        await delete_indicator_template(repo, tenant_id=uuid4(), template_id=uuid4())


async def test_create_indicator_template_propagates_timeout_fail_closed() -> None:
    repo = FakeChartingRepository(create_exc=TimeoutError("simulated statement_timeout"))
    with pytest.raises(TimeoutError):
        await create_indicator_template(
            repo,
            tenant_id=uuid4(),
            owner_subject_id=uuid4(),
            name="x",
            template={},
        )


# ---------------------------------------------------------------------------
# gate-red repro (D2) — proves the tenant-ownership guard in
# `_shared.load_owned_indicator_template()` is what actually blocks a
# cross-tenant leak, via a real before/after within one test.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_tenant_ownership_guard_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_tenant, other_tenant = uuid4(), uuid4()
    repo = FakeChartingRepository()
    created = await create_indicator_template(
        repo,
        tenant_id=owner_tenant,
        owner_subject_id=owner_tenant,
        name="secret",
        template={},
    )

    # green: guard active — cross-tenant get is denied.
    with pytest.raises(CrossTenantChartIndicatorTemplateAccessError):
        await get_indicator_template(repo, tenant_id=other_tenant, template_id=created.id)

    # red repro: neutralize exactly the tenant check a regression could
    # delete from `_shared.load_owned_indicator_template()`.
    import src.foundation.charting.application.get_indicator_template as target

    async def _load_without_tenant_check(
        repo: FakeChartingRepository, *, tenant_id: UUID, template_id: UUID
    ) -> ChartIndicatorTemplate:
        template = await repo.get_indicator_template(template_id)
        if template is None:
            raise ChartIndicatorTemplateNotFoundError(str(template_id))
        return template  # tenant_id check intentionally omitted

    monkeypatch.setattr(target, "load_owned_indicator_template", _load_without_tenant_check)

    leaked = await get_indicator_template(repo, tenant_id=other_tenant, template_id=created.id)
    assert leaked.id == created.id  # without the guard, the cross-tenant leak succeeds


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


def test_indicator_template_to_view_perf_budget() -> None:
    """`list_indicator_templates`의 응답 경로는 조회된 각 행을 매번 뷰로
    변환한다 — 그 변환 자체가 서버 지연을 지배하지 않는다는 상한을
    고정한다(2,000회 <300ms, 로컬 CI 잡음 여유 포함)."""
    now = datetime.now(timezone.utc)
    template = ChartIndicatorTemplate(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_subject_id=uuid4(),
        name="perf",
        template={
            "schemaVersion": 1,
            "panes": [{"id": f"p{i}", "kind": "sub", "heightRatio": 0.1} for i in range(10)],
            "indicators": [
                {"id": f"ind{i}", "paneId": f"p{i % 10}", "params": {"period": i}}
                for i in range(50)
            ],
        },
        revision=0,
        created_at=now,
        updated_at=now,
    )

    start = time.perf_counter()
    for _ in range(2_000):
        indicator_template_to_view(template)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 300, f"indicator_template_to_view() too slow: {elapsed_ms:.1f}ms/2000 calls"
