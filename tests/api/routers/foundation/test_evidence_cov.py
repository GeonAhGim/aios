"""Coverage tests for src/api/routers/foundation/evidence.py.

The router has no aggregation fallback or tolerance logic (task-21185 REJECT
points 1-2 do not apply to it); fail-closed behaviour is asserted instead:
broken chains and repository errors must propagate, never become a success.
Expected payload values are hard-coded literals; hashlib is never patched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.deps import get_current_admin, get_current_user
from src.api.foundation_deps import get_audit_event_repository
from src.api.routers.foundation.evidence import router
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import ChainIntegrityError, compute_event_hash
from src.services.auth_service import User

_USER_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
_AGG_ID = UUID("11111111-1111-1111-1111-111111111111")
_TRACE_ID = UUID("22222222-2222-2222-2222-222222222222")
_EVENT_ID = UUID("33333333-3333-3333-3333-333333333333")
_NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)


def _user(*, admin: bool) -> User:
    return User(
        user_id=_USER_ID,
        email="test@example.com",
        display_name="Test User",
        mfa_enabled=True,
        mfa_verified_at=_NOW,
        status="active",
        is_verifier=True,
        is_platform_admin=admin,
    )


def _event(**over: Any) -> AuditEvent:
    base: dict[str, Any] = dict(
        id=_EVENT_ID,
        tenant_id=_USER_ID,
        sequence_no=1,
        aggregate_type="mandate",
        aggregate_id=_AGG_ID,
        aggregate_revision=1,
        action="activate",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=_TRACE_ID,
        payload_hash="abc",
        payload={"k": "v", "n": 3},
        classification=Classification.INTERNAL,
        previous_hash=None,
        occurred_at=_NOW,
    )
    base.update(over)
    if "event_hash" not in base:
        base["event_hash"] = compute_event_hash(
            previous_hash=base["previous_hash"],
            tenant_id=base["tenant_id"],
            sequence_no=base["sequence_no"],
            aggregate_type=base["aggregate_type"],
            aggregate_id=base["aggregate_id"],
            action=base["action"],
            outcome=base["outcome"],
            payload_hash=base["payload_hash"],
            classification=base["classification"],
            occurred_at=base["occurred_at"],
        )
    return AuditEvent(**base)


class _FakeRepo:
    def __init__(
        self,
        events: list[AuditEvent],
        next_cursor: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.next_cursor = next_cursor
        self.error = error
        self.timeline_calls: list[dict[str, Any]] = []
        self.chain_tenants: list[UUID | None] = []

    async def list_timeline(
        self, tenant_id: UUID, **kw: Any
    ) -> tuple[list[AuditEvent], str | None]:
        self.timeline_calls.append({"tenant_id": tenant_id, **kw})
        if self.error:
            raise self.error
        return self.events, self.next_cursor

    async def list_chain_for_verification(self, tenant_id: UUID | None) -> list[AuditEvent]:
        self.chain_tenants.append(tenant_id)
        if self.error:
            raise self.error
        return self.events


def _app(repo: _FakeRepo, *, admin: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_audit_event_repository] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: _user(admin=admin)
    if admin:
        app.dependency_overrides[get_current_admin] = lambda: _user(admin=True)
    return app


def _client(repo: _FakeRepo, *, admin: bool) -> TestClient:
    return TestClient(_app(repo, admin=admin), raise_server_exceptions=True)


def test_timeline_returns_hardcoded_payload_and_scopes_to_user() -> None:
    repo = _FakeRepo([_event()], next_cursor="cur-2")
    r = _client(repo, admin=False).get(
        "/v1/foundation/evidence/timeline",
        params={"limit": 7, "aggregate_type": "mandate", "action": "activate", "cursor": "cur-1"},
    )
    assert r.status_code == 200
    page = r.json()["data"]
    assert page["next_cursor"] == "cur-2"
    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item["payload"] == {"k": "v", "n": 3}
    assert item["aggregate_type"] == "mandate"
    assert item["action"] == "activate"
    assert repo.timeline_calls == [
        {
            "tenant_id": _USER_ID,
            "cursor": "cur-1",
            "limit": 7,
            "aggregate_type": "mandate",
            "action": "activate",
        }
    ]


def test_timeline_default_limit_is_50_and_empty_page() -> None:
    repo = _FakeRepo([])
    r = _client(repo, admin=False).get("/v1/foundation/evidence/timeline")
    assert r.status_code == 200
    assert r.json()["data"]["items"] == []
    assert r.json()["data"]["next_cursor"] is None
    assert repo.timeline_calls[0]["limit"] == 50


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_timeline_limit_out_of_bounds_rejected_without_repo_call(limit: int) -> None:
    repo = _FakeRepo([_event()])
    r = _client(repo, admin=False).get("/v1/foundation/evidence/timeline", params={"limit": limit})
    assert r.status_code == 422
    assert repo.timeline_calls == []


@pytest.mark.parametrize("limit", [1, 100])
def test_timeline_limit_boundaries_accepted(limit: int) -> None:
    repo = _FakeRepo([])
    r = _client(repo, admin=False).get("/v1/foundation/evidence/timeline", params={"limit": limit})
    assert r.status_code == 200
    assert repo.timeline_calls[0]["limit"] == limit


def test_timeline_repo_failure_propagates_not_empty_success() -> None:
    repo = _FakeRepo([], error=RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):
        _client(repo, admin=False).get("/v1/foundation/evidence/timeline")


def test_verify_chain_valid_returns_verified_true_for_system_chain() -> None:
    first = _event(tenant_id=None)
    second = _event(tenant_id=None, sequence_no=2, previous_hash=first.event_hash)
    repo = _FakeRepo([first, second])
    r = _client(repo, admin=True).post("/v1/foundation/evidence/chain:verify")
    assert r.status_code == 200
    assert r.json()["data"] == {"verified": True}
    assert repo.chain_tenants == [None]


def test_verify_chain_passes_tenant_id() -> None:
    repo = _FakeRepo([])
    r = _client(repo, admin=True).post(
        "/v1/foundation/evidence/chain:verify", params={"tenant_id": str(_USER_ID)}
    )
    assert r.status_code == 200
    assert repo.chain_tenants == [_USER_ID]


def test_verify_chain_non_admin_forbidden_without_repo_call() -> None:
    repo = _FakeRepo([_event()])
    r = _client(repo, admin=False).post("/v1/foundation/evidence/chain:verify")
    assert r.status_code == 403
    assert repo.chain_tenants == []


def test_verify_chain_tampered_hash_raises_never_reports_verified() -> None:
    repo = _FakeRepo([_event(event_hash="deadbeef")])
    with pytest.raises(ChainIntegrityError):
        _client(repo, admin=True).post("/v1/foundation/evidence/chain:verify")


def test_verify_chain_broken_link_raises() -> None:
    repo = _FakeRepo([_event(), _event(sequence_no=2, previous_hash="not-the-prior-hash")])
    with pytest.raises(ChainIntegrityError):
        _client(repo, admin=True).post("/v1/foundation/evidence/chain:verify")


def test_verify_chain_repo_failure_propagates() -> None:
    repo = _FakeRepo([], error=RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):
        _client(repo, admin=True).post("/v1/foundation/evidence/chain:verify")
