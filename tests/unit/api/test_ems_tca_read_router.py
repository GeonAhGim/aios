"""PLT-21(task-5598) -- unit tests for `GET /v1/foundation/ems/tca/{parent_id}`
and `GET /v1/foundation/ems/tca/{parent_id}/revisions/{revision}`
(`src/api/routers/foundation/ems.py::get_latest_tca` / `get_tca_revision`).

No FastAPI TestClient/DB -- mirrors `test_ems_algo_progress_router.py`'s
convention: a fake `TcaResultRepository` exercises the same read path
without I/O. Covers the raw-`HTTPException` -> domain-exception migration
(PLT-21, `tests/unit/api/test_no_raw_http_exception.py`) -- both 404 paths
must raise `TcaResultNotFoundError`, translated to 404 by the central
`exception_registry_foundation.py` mapping, not by the router itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.api.routers.foundation.ems import get_latest_tca, get_tca_revision
from src.foundation.ems.contracts.v1 import TcaResult
from src.foundation.ems.ports.tca_result_repository import TcaResultNotFoundError, TcaResultRecord
from src.services.auth_service import User

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _user() -> User:
    user_id = uuid4()
    return User(
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _record(parent_id: UUID, revision: int) -> TcaResultRecord:
    return TcaResultRecord(
        tca_id=uuid4(),
        parent_id=parent_id,
        revision=revision,
        result=TcaResult(
            arrival_bps=Decimal("0"),
            vwap_bps=Decimal("0"),
            impact_bps=Decimal("0"),
            fees_bps=Decimal("0"),
            opportunity_bps=Decimal("0"),
        ),
        computed_at=_T0,
        created_at=_T0,
    )


@dataclass
class _FakeTcaResultRepository:
    records: dict[tuple[UUID, int], TcaResultRecord] = field(default_factory=dict)

    async def insert_or_get(self, **_kwargs: object) -> TcaResultRecord:
        raise NotImplementedError("not exercised by these tests")

    async def get_by_revision(self, parent_id: UUID, revision: int) -> TcaResultRecord | None:
        return self.records.get((parent_id, revision))

    async def get_latest(self, parent_id: UUID) -> TcaResultRecord | None:
        matches = [r for (pid, _rev), r in self.records.items() if pid == parent_id]
        return max(matches, key=lambda r: r.revision) if matches else None


async def test_get_latest_tca_returns_the_highest_revision() -> None:
    parent_id = uuid4()
    repo = _FakeTcaResultRepository(
        records={(parent_id, 1): _record(parent_id, 1), (parent_id, 2): _record(parent_id, 2)}
    )

    response = await get_latest_tca(parent_id, _user=_user(), repo=repo)

    assert response.data.parent_id == parent_id
    assert response.data.revision == 2


async def test_get_latest_tca_raises_not_found_for_unknown_parent_id() -> None:
    """negative -- a parent_id with no computed TCA must raise the domain
    NotFoundError (translated to 404 by exception_registry_foundation.py),
    not an empty 200 or an unmapped 500."""
    repo = _FakeTcaResultRepository()

    with pytest.raises(TcaResultNotFoundError):
        await get_latest_tca(uuid4(), _user=_user(), repo=repo)


async def test_get_tca_revision_returns_the_requested_revision() -> None:
    parent_id = uuid4()
    repo = _FakeTcaResultRepository(records={(parent_id, 1): _record(parent_id, 1)})

    response = await get_tca_revision(parent_id, 1, _user=_user(), repo=repo)

    assert response.data.revision == 1


async def test_get_tca_revision_raises_not_found_for_unknown_revision() -> None:
    """negative -- a real parent_id but a revision that was never computed
    must still 404 (not fall back to the latest revision)."""
    parent_id = uuid4()
    repo = _FakeTcaResultRepository(records={(parent_id, 1): _record(parent_id, 1)})

    with pytest.raises(TcaResultNotFoundError):
        await get_tca_revision(parent_id, 2, _user=_user(), repo=repo)
