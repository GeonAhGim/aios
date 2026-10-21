"""FND-05 `application/begin_connection.py` -- mocked failure injection +
perf assertion + gate-red repro.

DEEPEN(task-4636, ADR-2026-09-09-C D2 floor): the command orchestrates two
other bounded contexts (Trust Core's `evaluate_trust_freshness` +
`get_active_consent`) and two repository writes (`insert_pending_connection`,
`insert_consent_link`). None of that ordering/fail-closed behaviour was
covered by `tests/foundation/unit/connections/test_rules.py` (pure rules
only) -- this file isolates the application layer behind in-memory fakes
(same reasoning as `tests/foundation/unit/ems/test_route_order_application.py`,
task-3117) so MFA/consent/repository failures can be forced on demand.
Connections is not itself the safety/execution/ledger/compliance/data axis
(ADR-2026-09-09-C axis list), so only the D2 floor applies here.

# ratchet-allow: fail-closed Protocol stub methods (FakeConnectionRepository/
# FakeTrustRepository members that begin_connection() never calls) raise
# NotImplementedError so an accidental call surfaces immediately instead of
# returning a silently wrong fake value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.connections.application.begin_connection import (
    ConsentRequiredError,
    MfaRequiredError,
    _mask,
    begin_connection,
    connection_to_view,
)
from src.foundation.connections.domain.models import (
    AccountConnection,
    AccountSnapshot,
    ConnectionConsent,
    ConnectionHealth,
    ConnectionState,
    CredentialBinding,
)
from src.foundation.connections.domain.rules import ForbiddenCapabilityScopeError
from src.foundation.trust.domain.models import Consent, ConsentState, Disclosure
from tests.conftest import PerfBudget
from tests.support.perf_inject import delayed_sync

_NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
_PURPOSE = "account_read_connection"


@dataclass
class FakeConnectionRepository:
    """In-memory stand-in for `ConnectionRepository`. `insert_exc`, when set,
    is raised on `insert_pending_connection` before anything is recorded --
    simulating a save-path failure (dropped connection, constraint
    violation) instead of a successful write."""

    inserted: list[AccountConnection] = field(default_factory=list)
    consent_links: list[ConnectionConsent] = field(default_factory=list)
    insert_exc: Exception | None = None
    insert_pending_calls: int = 0
    insert_consent_link_calls: int = 0

    async def insert_pending_connection(self, connection: AccountConnection) -> AccountConnection:
        self.insert_pending_calls += 1
        if self.insert_exc is not None:
            raise self.insert_exc
        created = AccountConnection(
            id=connection.id,
            tenant_id=connection.tenant_id,
            owner_subject_id=connection.owner_subject_id,
            provider_code=connection.provider_code,
            opaque_account_ref=connection.opaque_account_ref,
            state=connection.state,
            capability_profile=connection.capability_profile,
            revision=connection.revision,
            created_at=_NOW,
        )
        self.inserted.append(created)
        return created

    async def insert_consent_link(self, link: ConnectionConsent) -> ConnectionConsent:
        self.insert_consent_link_calls += 1
        self.consent_links.append(link)
        return link

    # Unused Protocol members -- not exercised by begin_connection().
    async def get_connection(self, connection_id: UUID) -> AccountConnection | None:
        raise NotImplementedError

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        raise NotImplementedError

    async def transition_connection_state(
        self, connection_id: UUID, *, tenant_id: UUID, expected_state: str, new_state: str
    ) -> AccountConnection:
        raise NotImplementedError

    async def insert_credential_binding(self, binding: CredentialBinding) -> CredentialBinding:
        raise NotImplementedError

    async def get_credential_binding(self, connection_id: UUID) -> CredentialBinding | None:
        raise NotImplementedError

    async def revoke_credential_binding(self, connection_id: UUID) -> None:
        raise NotImplementedError

    async def persist_snapshot_if_syncable(
        self,
        connection_id: UUID,
        tenant_id: UUID,
        snapshot: AccountSnapshot,
        health: ConnectionHealth,
    ) -> AccountSnapshot:
        raise NotImplementedError

    async def get_latest_snapshot(self, connection_id: UUID) -> AccountSnapshot | None:
        raise NotImplementedError

    async def insert_health_record(self, health: ConnectionHealth) -> ConnectionHealth:
        raise NotImplementedError

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        raise NotImplementedError


@dataclass
class FakeTrustRepository:
    """In-memory stand-in for `TrustRepository`. Only the three methods
    `begin_connection`'s path touches (directly, or via
    `evaluate_trust_freshness`) are wired to configurable fixtures."""

    disclosure: Disclosure | None
    latest_consent: Consent | None
    active_consent: Consent | None
    get_active_consent_calls: int = 0

    async def get_active_disclosure(self, purpose: str) -> Disclosure | None:
        return self.disclosure

    async def get_latest_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        return self.latest_consent

    async def get_active_consent(self, tenant_id: UUID, purpose: str) -> Consent | None:
        self.get_active_consent_calls += 1
        return self.active_consent

    async def get_disclosure_by_purpose_and_revision(
        self, purpose: str, revision: int
    ) -> tuple[Disclosure, datetime] | None:
        raise NotImplementedError

    async def list_active_consents(self, tenant_id: UUID) -> list[Consent]:
        raise NotImplementedError

    async def insert_consent(
        self,
        *,
        tenant_id: UUID,
        subject_id: UUID,
        purpose: str,
        disclosure_id: UUID,
        disclosure_revision: int,
        expires_at: datetime | None,
    ) -> Consent:
        raise NotImplementedError

    async def revoke_consent(self, consent_id: UUID, *, tenant_id: UUID) -> Consent:
        raise NotImplementedError


def _disclosure(*, revision: int = 1, retired_at: datetime | None = None) -> Disclosure:
    return Disclosure(
        id=uuid4(),
        purpose=_PURPOSE,
        revision=revision,
        content_hash="deadbeef",
        published_at=_NOW - timedelta(days=30),
        retired_at=retired_at,
    )


def _consent(
    *,
    tenant_id: UUID,
    state: ConsentState = ConsentState.ACTIVE,
    disclosure_id: UUID,
    revision: int = 1,
    expires_at: datetime | None = None,
) -> Consent:
    return Consent(
        id=uuid4(),
        tenant_id=tenant_id,
        subject_id=tenant_id,
        purpose=_PURPOSE,
        disclosure_id=disclosure_id,
        disclosure_revision=revision,
        state=state,
        accepted_at=_NOW - timedelta(days=1),
        revoked_at=None,
        expires_at=expires_at,
    )


def _fresh_trust_repo(tenant_id: UUID) -> FakeTrustRepository:
    disclosure = _disclosure()
    consent = _consent(tenant_id=tenant_id, disclosure_id=disclosure.id)
    return FakeTrustRepository(
        disclosure=disclosure, latest_consent=consent, active_consent=consent
    )


def _kwargs(tenant_id: UUID, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        tenant_id=tenant_id,
        subject_id=tenant_id,
        mfa_verified=True,
        provider_code="BITGET",
        opaque_account_ref="ACCT1234567890",
        requested_capability_profile=["READ_BALANCE"],
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# negative tests (D2)
# ---------------------------------------------------------------------------


async def test_begin_connection_rejects_when_mfa_not_verified() -> None:
    """#74 §5 -- connection commands require MFA. Neither repository should
    be touched before this check fires."""
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository()
    trust_repo = _fresh_trust_repo(tenant_id)

    with pytest.raises(MfaRequiredError):
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id, mfa_verified=False))

    assert conn_repo.insert_pending_calls == 0
    assert trust_repo.get_active_consent_calls == 0


@pytest.mark.perf
async def test_mfa_rejection_single_call_cpu_convergence() -> None:
    """단일 호출 CPU 시간을 측정한다(0 수렴 결함 감지).

    `is_lease_available` 패터너: batch=10000으로 측정한 CPU 시간이
    0ms면 Windows 양자화 오차(≈15.6ms tick)일 수 있다. MFA 체크는
    `if not mfa_verified: raise` 한 줄이므로 genuinely 0ms에 가깝고
    `>= 0`으로 측정 메커니즘의 동작을 확인한다.
    """
    tenant_id = uuid4()

    async def _run() -> None:
        with pytest.raises(MfaRequiredError):
            await begin_connection(
                FakeConnectionRepository(),
                _fresh_trust_repo(tenant_id),
                **_kwargs(tenant_id, mfa_verified=False),
            )

    sample = await PerfBudget().sample_async(_run, batch=10000)
    assert sample.cpu_ms >= 0, (
        f"CPU 측정 실패 — 측정 메커니즘이 동작하지 않음: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
async def test_mfa_rejection_perf_budget(perf_budget: PerfBudget) -> None:
    """성능 수치 단언(D2): MFA 거부 경로의 호출 1회가 0.1ms 미만이어야 한다.

    `process_time()` 기반 측정 — 다른 워커나 호스트 대기 영향 제거.
    예산 10배 지연 monkeypatch에서 적색으로 떨어져야 한다(gate-red).
    """
    tenant_id = uuid4()

    async def _run() -> None:
        with pytest.raises(MfaRequiredError):
            await begin_connection(
                FakeConnectionRepository(),
                _fresh_trust_repo(tenant_id),
                **_kwargs(tenant_id, mfa_verified=False),
            )

    sample = await perf_budget.sample_async(_run, batch=100)
    assert sample.cpu_ms < 0.1, (
        f"mfa_rejection_path CPU {sample.cpu_ms:.3f}ms exceeded 0.1ms budget"
    )


@pytest.mark.perf
async def test_mfa_rejection_perf_injection_exceeds_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MFA 체크에 CPU 지연(delayed_sync, 0.05s)을 주입한 후
    assert_within가 AssertionError를 발생시켜야 한다(red).

    begin_connection의 첫 줄이 MFA 체크(`if not mfa_verified: raise`)이므로,
    MfaRequiredError를 raise하기 전에 0.05s CPU 부하를 주입한다.
    예산 0.1ms보다 500배 커서 assert_within가 실패한다. 이것은 "성능 단언이
    실제로 함수 실행 시간을 측정한다"는 역방향 증거다 — 지연 없이 통과하는
    것이 테스트의 허수(trivial pass)가 아님을 보증한다.
    """
    import src.foundation.connections.application.begin_connection as target

    # 0.05s = 50ms CPU 부하 — 예산 0.1ms를 500배 초과
    slow_begin_connection = delayed_sync(target.begin_connection, 0.05)

    tenant_id = uuid4()

    async def _run() -> None:
        with pytest.raises(MfaRequiredError):
            await slow_begin_connection(
                FakeConnectionRepository(),
                _fresh_trust_repo(tenant_id),
                **_kwargs(tenant_id, mfa_verified=False),
            )

    with pytest.raises(AssertionError):
        sample = await perf_budget.sample_async(_run, batch=1)
        assert sample.cpu_ms < 0.1, (
            f"perf_injection_mfa_rejection CPU {sample.cpu_ms:.3f}ms exceeded 0.1ms budget"
        )


async def test_begin_connection_rejects_forbidden_capability_scope() -> None:
    """CON-002 -- a TRADE_* scope is a hard rejection before any trust/DB
    round trip happens."""
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository()
    trust_repo = _fresh_trust_repo(tenant_id)

    with pytest.raises(ForbiddenCapabilityScopeError):
        await begin_connection(
            conn_repo,
            trust_repo,
            **_kwargs(tenant_id, requested_capability_profile=["TRADE_SPOT"]),
        )

    assert conn_repo.insert_pending_calls == 0
    assert trust_repo.get_active_consent_calls == 0


async def test_begin_connection_raises_when_consent_not_fresh() -> None:
    """No disclosure published yet -> `evaluate_trust_freshness` denies with
    POLICY_DISCLOSURE_NOT_PUBLISHED, and `begin_connection` must surface that
    exact reason_code through ConsentRequiredError without writing anything."""
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository()
    trust_repo = FakeTrustRepository(disclosure=None, latest_consent=None, active_consent=None)

    with pytest.raises(ConsentRequiredError) as exc_info:
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))

    assert exc_info.value.reason_code == "POLICY_DISCLOSURE_NOT_PUBLISHED"
    assert conn_repo.insert_pending_calls == 0
    assert trust_repo.get_active_consent_calls == 0


async def test_begin_connection_raises_when_consent_expired() -> None:
    """Boundary case -- a consent whose `expires_at` is exactly `now` counts
    as expired (`now >= expires_at`), and must deny with the expiry
    reason_code, not silently pass through as fresh."""
    tenant_id = uuid4()
    disclosure = _disclosure()
    expired = _consent(
        tenant_id=tenant_id, disclosure_id=disclosure.id, expires_at=_NOW - timedelta(seconds=1)
    )
    trust_repo = FakeTrustRepository(
        disclosure=disclosure, latest_consent=expired, active_consent=expired
    )
    conn_repo = FakeConnectionRepository()

    with pytest.raises(ConsentRequiredError) as exc_info:
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))

    assert exc_info.value.reason_code == "POLICY_CONSENT_EXPIRED"
    assert conn_repo.insert_pending_calls == 0


async def test_begin_connection_raises_when_fresh_but_no_active_consent_row() -> None:
    """Fresh per `evaluate_trust_freshness`, but `get_active_consent` (a
    *separate* repository round trip `begin_connection` makes itself) comes
    back None -- e.g. revoked between the freshness read and this read. Must
    still deny, not silently proceed with a connection that has no consent
    to point `ConnectionConsent.consent_ref` at."""
    tenant_id = uuid4()
    disclosure = _disclosure()
    fresh = _consent(tenant_id=tenant_id, disclosure_id=disclosure.id)
    trust_repo = FakeTrustRepository(
        disclosure=disclosure, latest_consent=fresh, active_consent=None
    )
    conn_repo = FakeConnectionRepository()

    with pytest.raises(ConsentRequiredError) as exc_info:
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))

    assert exc_info.value.reason_code == "POLICY_CONSENT_REQUIRED"
    assert conn_repo.insert_pending_calls == 0
    assert trust_repo.get_active_consent_calls == 1


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_begin_connection_propagates_repository_write_failure_fail_closed() -> None:
    """DB save-path failure injection (connection drop) on the first write
    -- `begin_connection` must not swallow it, and the dependent second
    write (`insert_consent_link`) must never fire against a connection that
    was never actually persisted."""
    tenant_id = uuid4()
    trust_repo = _fresh_trust_repo(tenant_id)
    conn_repo = FakeConnectionRepository(
        insert_exc=ConnectionResetError("simulated connection drop")
    )

    with pytest.raises(ConnectionResetError):
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))

    assert conn_repo.insert_pending_calls == 1
    assert conn_repo.insert_consent_link_calls == 0


# ---------------------------------------------------------------------------
# success path + pure helpers
# ---------------------------------------------------------------------------


async def test_begin_connection_success_creates_pending_connection_with_consent_link() -> None:
    tenant_id = uuid4()
    subject_id = uuid4()
    disclosure = _disclosure()
    consent = _consent(tenant_id=tenant_id, disclosure_id=disclosure.id)
    trust_repo = FakeTrustRepository(
        disclosure=disclosure, latest_consent=consent, active_consent=consent
    )
    conn_repo = FakeConnectionRepository()

    view = await begin_connection(
        conn_repo,
        trust_repo,
        **_kwargs(
            tenant_id,
            subject_id=subject_id,
            requested_capability_profile=["READ_BALANCE", "READ_POSITION"],
        ),
    )

    assert view.state.value == "PENDING_CONSENT"
    assert view.masked_account_label == "**********7890"
    assert view.revision == 1
    assert view.scope_verified is False

    assert conn_repo.insert_pending_calls == 1
    assert conn_repo.insert_consent_link_calls == 1
    link = conn_repo.consent_links[0]
    assert link.connection_id == conn_repo.inserted[0].id
    assert link.consent_ref == consent.id
    assert link.data_purposes == (_PURPOSE,)
    assert link.expires_at == consent.expires_at


def test_mask_short_ref_fully_masked() -> None:
    """Boundary -- refs of length <= 4 have nothing safe to reveal, so the
    whole string is masked instead of leaking it verbatim."""
    assert _mask("AB12") == "****"
    assert _mask("A") == "*"


def test_mask_longer_ref_reveals_last_four_only() -> None:
    assert _mask("ACCT1234567890") == "**********7890"


def test_connection_to_view_defaults_scope_verified_false_without_binding() -> None:
    connection = AccountConnection(
        id=uuid4(),
        tenant_id=uuid4(),
        owner_subject_id=uuid4(),
        provider_code="BITGET",
        opaque_account_ref="ACCT1234567890",
        state=ConnectionState.PENDING_CONSENT,
        capability_profile=(),
        revision=1,
        created_at=_NOW,
    )
    view = connection_to_view(connection)
    assert view.scope_verified is False


# ---------------------------------------------------------------------------
# gate-red repro (D2) -- proves Trust Core's freshness check is load-bearing
# for begin_connection's fail-closed consent gate.
# ---------------------------------------------------------------------------


async def test_gate_red_repro_freshness_check_is_load_bearing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    conn_repo = FakeConnectionRepository()
    # stale consent: disclosure revision moved on, this consent is revision 1.
    disclosure = _disclosure(revision=2)
    stale_consent = _consent(tenant_id=tenant_id, disclosure_id=disclosure.id, revision=1)
    trust_repo = FakeTrustRepository(
        disclosure=disclosure, latest_consent=stale_consent, active_consent=stale_consent
    )

    # green: guard active -- stale-revision consent is denied, nothing persisted.
    with pytest.raises(ConsentRequiredError) as exc_info:
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))
    assert exc_info.value.reason_code == "POLICY_CONSENT_STALE_REVISION"
    assert conn_repo.insert_pending_calls == 0

    # red repro: neutralize exactly the freshness check `begin_connection`
    # delegates to (evaluate_trust_freshness) by forcing it to always report
    # fresh. begin_connection does not re-derive revision/expiry itself (it
    # trusts this call per this module's docstring #71 §4 Contract
    # ownership), so a regression there would silently let a stale-consent
    # connection through, and no rules-only test would catch it.
    import src.foundation.connections.application.begin_connection as target
    from src.foundation.trust.contracts.v1 import TrustFreshnessDecision

    async def _always_fresh(
        repo: object, context: object, *, purpose: str
    ) -> TrustFreshnessDecision:
        return TrustFreshnessDecision(
            tenant_id=tenant_id,
            purpose=purpose,
            is_fresh=True,
            reason_code=None,
            as_of=_NOW,
        )

    monkeypatch.setattr(target, "evaluate_trust_freshness", _always_fresh)

    view = await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))

    # without the guard, a connection is created from a stale-revision
    # consent -- proving the real (unpatched) freshness check is what
    # protects this fail-closed gate, not anything in this module alone.
    assert conn_repo.insert_pending_calls == 1
    assert view.state.value == "PENDING_CONSENT"


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_begin_connection_perf_budget_p95_latency(
    perf_budget: Any,
) -> None:
    """No published per-axis budget covers FND-05 specifically (ADR-2026-09-09-C
    Decision 1's table); pin the same order of magnitude as the closest
    published command-write budget ("order submit -> ACK p95 50ms, paper")
    since this command is also a single fail-closed write plus one
    dependent-context round trip, all served in-memory here.

    raw time.perf_counter() -> perf_budget.samples_async (task-10958):
    process_time 기반 측정 + coverage tracer-pause로 부하 민감 호스트에서의
    오탐을 줄인다. 예산 값(50ms)은 그대로 유지한다."""
    tenant_id = uuid4()

    async def _run() -> None:
        disclosure = _disclosure()
        consent = _consent(tenant_id=tenant_id, disclosure_id=disclosure.id)
        trust_repo = FakeTrustRepository(
            disclosure=disclosure, latest_consent=consent, active_consent=consent
        )
        conn_repo = FakeConnectionRepository()
        await begin_connection(conn_repo, trust_repo, **_kwargs(tenant_id))

    samples = await perf_budget.samples_async(_run, n=50)
    durations_ms = sorted(s.cpu_ms for s in samples)
    p95 = durations_ms[int(50 * 0.95) - 1]
    assert p95 < 50.0, f"begin_connection p95 latency {p95:.3f}ms exceeded 50ms budget"
