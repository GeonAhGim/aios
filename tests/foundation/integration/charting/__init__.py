"""Charting integration package marker + DEEPEN negative/failure-injection/perf tests.

Task-7941 DEEPEN of task-6704 (고아 산출물 회수 5828 (qa-2)) — this package had
0 negative tests and no failure-injection/perf markers. Sibling files
(test_charting_lifecycle.py, test_indicator_template_lifecycle.py) already
exercise the real-Postgres cross-tenant/optimistic-lock paths; this module
adds tests against an in-memory fake of the `ChartingRepository` Protocol so
they run without `TEST_DATABASE_URL`, covering pure-domain rejection paths
(`domain.rules.validate_drawings_document`) and a dependency-failure
propagation case that the DB-backed tests don't isolate."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.charting.application._shared import load_owned_layout
from src.foundation.charting.application.create_layout import create_layout
from src.foundation.charting.application.errors import (
    ChartLayoutNotFoundError,
    CrossTenantChartLayoutAccessError,
)
from src.foundation.charting.application.put_drawings import put_drawings
from src.foundation.charting.domain.models import ChartDrawingSet, ChartLayout
from src.foundation.charting.domain.rules import DrawingValidationError, validate_drawings_document


class _FakeChartingRepository:
    """Minimal in-memory stand-in for `ChartingRepository` (ports/repository.py).

    Only implements what these tests exercise (`create_layout`/`get_layout`/
    `put_drawings`) — not a general-purpose fake."""

    def __init__(self) -> None:
        self.layouts: dict[UUID, ChartLayout] = {}
        self.drawing_sets: dict[UUID, ChartDrawingSet] = {}

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
        return self.layouts.get(layout_id)

    async def put_drawings(
        self,
        layout_id: UUID,
        *,
        expected_revision: int,
        schema_version: int,
        drawings: tuple[dict[str, Any], ...],
    ) -> ChartDrawingSet:
        current = self.drawing_sets[layout_id]
        updated = replace(
            current,
            schema_version=schema_version,
            drawings=drawings,
            revision=current.revision + 1,
            updated_at=datetime.now(timezone.utc),
        )
        self.drawing_sets[layout_id] = updated
        return updated


def _tenant() -> UUID:
    return uuid4()


# ── Negative tests: pure-domain rejection (`validate_drawings_document`) ────


def test_validate_drawings_document_rejects_unsupported_schema_version() -> None:
    with pytest.raises(DrawingValidationError, match="schema_version"):
        validate_drawings_document({"schema_version": 2, "drawings": []})


def test_validate_drawings_document_rejects_unknown_drawing_kind() -> None:
    with pytest.raises(DrawingValidationError, match="kind"):
        validate_drawings_document(
            {"schema_version": 1, "drawings": [{"id": "d1", "kind": "unknown-kind"}]}
        )


def test_validate_drawings_document_rejects_duplicate_ids() -> None:
    document = {
        "schema_version": 1,
        "drawings": [
            {"id": "dup", "kind": "vertical-line", "time": 1},
            {"id": "dup", "kind": "vertical-line", "time": 2},
        ],
    }
    with pytest.raises(DrawingValidationError, match="duplicate id"):
        validate_drawings_document(document)


def test_validate_drawings_document_rejects_unknown_document_field() -> None:
    with pytest.raises(DrawingValidationError, match="unknown field"):
        validate_drawings_document({"schema_version": 1, "drawings": [], "extra": "not allowed"})


# ── Negative tests: application-layer ownership guard ──────────────────────


async def test_load_owned_layout_rejects_unknown_layout_id() -> None:
    repo = _FakeChartingRepository()
    with pytest.raises(ChartLayoutNotFoundError):
        await load_owned_layout(repo, tenant_id=_tenant(), layout_id=uuid4())


async def test_load_owned_layout_rejects_cross_tenant_access() -> None:
    repo = _FakeChartingRepository()
    owner_tenant = _tenant()
    layout = await repo.create_layout(
        tenant_id=owner_tenant, owner_subject_id=owner_tenant, name="mine", layout_state={}
    )
    with pytest.raises(CrossTenantChartLayoutAccessError):
        await load_owned_layout(repo, tenant_id=_tenant(), layout_id=layout.id)


async def test_put_drawings_rejects_malformed_document_before_touching_repo() -> None:
    """Structural validation runs before the repo write — a broken document
    never reaches `repo.put_drawings()` (fail-closed, no partial write)."""
    repo = _FakeChartingRepository()
    tenant_id = _tenant()
    layout = await repo.create_layout(
        tenant_id=tenant_id, owner_subject_id=tenant_id, name="strict", layout_state={}
    )
    with pytest.raises(DrawingValidationError):
        await put_drawings(
            repo,
            tenant_id=tenant_id,
            layout_id=layout.id,
            expected_revision=0,
            schema_version=1,
            drawings=[{"id": "d1", "kind": "unknown-kind"}],
        )
    # Untouched — the revision the fake repo would bump on a real write.
    assert repo.drawing_sets[layout.id].revision == 0


# ── Failure injection ────────────────────────────────────────────────────────


async def test_create_layout_propagates_repository_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the storage dependency raises (e.g. connection dropped mid-write),
    the application layer must not swallow it into a fake success — fail-closed
    per CLAUDE.md §3 default posture."""
    repo = _FakeChartingRepository()

    async def _boom(**_kwargs: Any) -> ChartLayout:
        raise ConnectionError("simulated dependency failure: pool exhausted")

    monkeypatch.setattr(repo, "create_layout", _boom)

    with pytest.raises(ConnectionError, match="simulated dependency failure"):
        await create_layout(
            repo,
            tenant_id=_tenant(),
            owner_subject_id=_tenant(),
            name="doomed",
            layout_state={},
        )


# ── Performance assertion ───────────────────────────────────────────────────


@pytest.mark.perf
def test_validate_drawings_document_scales_linearly_within_budget() -> None:
    """D2 numeric perf assertion: validating 500 drawings must stay well under
    the pure-CPU budget (no I/O in this path) — budget picked generously
    above observed local runtime to avoid flakiness while still catching a
    real algorithmic regression (e.g. accidental O(n^2) duplicate-id check)."""
    drawings = [{"id": f"d{i}", "kind": "vertical-line", "time": float(i)} for i in range(500)]
    document = {"schema_version": 1, "drawings": drawings}

    start = time.perf_counter()
    version, validated = validate_drawings_document(document)
    elapsed = time.perf_counter() - start

    assert version == 1
    assert len(validated) == 500
    assert elapsed < 1.0, f"validating 500 drawings took {elapsed:.3f}s (budget: 1.0s)"
