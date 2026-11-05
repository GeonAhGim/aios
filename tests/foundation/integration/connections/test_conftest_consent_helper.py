"""conftest.grant_account_read_consent 헬퍼 증빙 (DB 불필요) — task-22232.

conftest.py 자체는 테스트를 수집하지 않으므로, 헬퍼의 negative·실패 주입·성능 단언은
이 sibling 파일에 둔다. DB/Trust 의존성은 fake로 대체한다.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.foundation.integration.connections import conftest as helper
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_POOL: Any = object()
_REPO: Any = object()


class _Recorder:
    def __init__(self) -> None:
        self.disclosures: list[tuple[str, int]] = []
        self.accepts: list[dict[str, Any]] = []

    async def create_disclosure(self, pool: object, *, purpose: str, revision: int) -> None:
        self.disclosures.append((purpose, revision))

    async def accept_disclosure(self, repo: object, ctx: Any, **kwargs: Any) -> None:
        self.accepts.append({"ctx": ctx, **kwargs})


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    r = _Recorder()
    monkeypatch.setattr(helper, "create_disclosure", r.create_disclosure)
    monkeypatch.setattr(helper, "accept_disclosure", r.accept_disclosure)
    return r


# --- negative -----------------------------------------------------------------------


def test_next_revision_is_strictly_monotonic_never_repeats() -> None:
    values = [helper._next_revision() for _ in range(50)]
    assert values == sorted(set(values))


async def test_each_grant_uses_a_fresh_revision_to_avoid_unique_collision(rec: _Recorder) -> None:
    tenant = uuid4()
    await helper.grant_account_read_consent(_POOL, _REPO, tenant_id=tenant)
    await helper.grant_account_read_consent(_POOL, _REPO, tenant_id=tenant)
    first, second = (rev for _, rev in rec.disclosures)
    assert first != second
    assert [a["disclosure_revision"] for a in rec.accepts] == [first, second]


async def test_consent_is_scoped_to_given_tenant_with_mfa_verified(rec: _Recorder) -> None:
    tenant = uuid4()
    await helper.grant_account_read_consent(_POOL, _REPO, tenant_id=tenant)
    ctx = rec.accepts[0]["ctx"]
    assert ctx.tenant_id == tenant and ctx.subject_id == tenant and ctx.mfa_verified is True
    assert rec.accepts[0]["purpose"] == helper.ACCOUNT_READ_CONSENT_PURPOSE


async def test_accept_is_not_attempted_when_disclosure_creation_fails(
    monkeypatch: pytest.MonkeyPatch, rec: _Recorder
) -> None:
    async def _boom(pool: object, *, purpose: str, revision: int) -> None:
        raise RuntimeError("disclosure insert failed")

    monkeypatch.setattr(helper, "create_disclosure", _boom)
    with pytest.raises(RuntimeError, match="disclosure insert failed"):
        await helper.grant_account_read_consent(_POOL, _REPO, tenant_id=uuid4())
    assert rec.accepts == []


# --- failure injection --------------------------------------------------------------


async def test_accept_failure_propagates_instead_of_silent_consent(
    monkeypatch: pytest.MonkeyPatch, rec: _Recorder
) -> None:
    async def _boom(repo: object, ctx: object, **kwargs: Any) -> None:
        raise RuntimeError("trust repo down")

    monkeypatch.setattr(helper, "accept_disclosure", _boom)
    with pytest.raises(RuntimeError, match="trust repo down"):
        await helper.grant_account_read_consent(_POOL, _REPO, tenant_id=uuid4())


# --- performance + gate-red ---------------------------------------------------------


@pytest.mark.perf
def test_next_revision_within_budget(perf_budget: PerfBudget) -> None:
    sample = perf_budget.best_of(helper._next_revision, n=5, batch=200)
    assert sample.cpu_ms < 1.0, f"_next_revision {sample.cpu_ms:.4f}ms exceeded 1ms budget"


@pytest.mark.perf
def test_next_revision_budget_catches_virtual_delay(perf_budget: PerfBudget) -> None:
    slow = virtual_delayed_sync(helper._next_revision, 0.010)
    sample = perf_budget.best_of(slow, n=5, batch=10)
    assert sample.cpu_ms >= 1.0, f"delay-injected {sample.cpu_ms:.4f}ms should exceed 1ms budget"
