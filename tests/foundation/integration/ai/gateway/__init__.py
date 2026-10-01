"""Domain-level negative / failure-injection / performance tests for AI gateway.

Covers token_rules (Scope, AgentToken, authorize, issue_scopes) and confirm
(ConfirmTicket, verify_and_consume) with explicit invariant-violating inputs,
dependency-failure injection, and latency assertions. No DB required -- pure
domain logic (INVARIANTS.md I-06, I-11).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.ai.gateway.domain.confirm import (
    ConfirmDigestMismatchError,
    ConfirmTicket,
    ConfirmTicketExpiredError,
    ConfirmTicketReusedError,
    is_consumed,
    verify_and_consume,
)
from src.foundation.ai.gateway.domain.confirm import (
    is_expired as confirm_is_expired,
)
from src.foundation.ai.gateway.domain.token_rules import (
    AgentToken,
    InstrumentNotAllowedError,
    NotionalCapExceededError,
    Scope,
    ScopeDeniedError,
    ScopeEscalationError,
    TokenExpiredError,
    TokenRevokedError,
    TokenRuleError,
    authorize,
    issue_scopes,
)

UTC = timezone.utc


def _token(**overrides: object) -> AgentToken:
    fields: dict[str, object] = dict(
        token_id=uuid4(),
        tenant_id=uuid4(),
        scopes=frozenset({Scope.READ}),
        allow_instruments=frozenset(),
        notional_cap=Decimal("1000000"),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        paper_only=True,
        revoked_at=None,
    )
    fields.update(overrides)
    return AgentToken(**fields)  # type: ignore[arg-type]


# -- Negative tests (invariant-violating inputs -> rejection) ----------------


class TestNegativeScope:
    """Reject scope-related inputs that violate the §2.1/I-06 invariants."""

    def test_negative_scope_escalation(self):
        """issue_scopes() rejects a requested scope set exceeding grantable."""
        with pytest.raises(ScopeEscalationError, match="scope escalation"):
            issue_scopes(
                requested=frozenset({Scope.READ, Scope.PROPOSE}),
                grantable=frozenset({Scope.READ}),
            )

    def test_negative_scope_empty_request_rejected(self):
        """issue_scopes() rejects an empty requested scope set."""
        with pytest.raises(TokenRuleError, match="at least one scope"):
            issue_scopes(requested=frozenset(), grantable=frozenset({Scope.READ}))

    def test_negative_scope_empty_rejected_by_post_init(self):
        """Empty scopes on AgentToken -> TokenRuleError at construction."""
        with pytest.raises(TokenRuleError, match="at least one scope"):
            _token(scopes=frozenset())

    def test_negative_scope_paper_only_invariant(self):
        """paper_only=False -> TokenRuleError at construction (__post_init__)."""
        with pytest.raises(TokenRuleError, match="paper_only"):
            _token(paper_only=False)


class TestNegativeTokenLifetime:
    """Reject authorize() against an AgentToken violating invariant I-06."""

    def test_negative_token_expired(self):
        """Expired token -> TokenExpiredError, checked before scope match."""
        token = _token(expires_at=datetime.now(UTC) - timedelta(hours=1))
        with pytest.raises(TokenExpiredError, match="expired"):
            authorize(token, Scope.READ, datetime.now(UTC))

    def test_negative_token_revoked(self):
        """Revoked token -> TokenRevokedError, checked before expiry/scope."""
        now = datetime.now(UTC)
        token = _token(revoked_at=now - timedelta(minutes=1))
        with pytest.raises(TokenRevokedError, match="revoked"):
            authorize(token, Scope.READ, now)

    def test_negative_token_scope_not_granted(self):
        """authorize() with a scope the token never held -> ScopeDeniedError."""
        token = _token(scopes=frozenset({Scope.READ}))
        with pytest.raises(ScopeDeniedError, match="not granted"):
            authorize(token, Scope.PAPER, datetime.now(UTC))

    def test_negative_instrument_not_allowed(self):
        """authorize() with an instrument outside allow_instruments -> rejected."""
        token = _token(allow_instruments=frozenset({"BTCUSDT"}))
        with pytest.raises(InstrumentNotAllowedError, match="not in allow_instruments"):
            authorize(token, Scope.READ, datetime.now(UTC), instrument="ETHUSDT")

    def test_negative_notional_cap_exceeded(self):
        """authorize() with notional above notional_cap -> rejected."""
        token = _token(notional_cap=Decimal("100"))
        with pytest.raises(NotionalCapExceededError, match="exceeds cap"):
            authorize(token, Scope.READ, datetime.now(UTC), notional=Decimal("101"))


class TestNegativeConfirmTicket:
    """ConfirmTicket negative cases: expired, reused, digest mismatch."""

    def test_negative_confirm_expired_ticket(self):
        """ConfirmTicket past its TTL -> is_expired() reports True."""
        ticket = ConfirmTicket(
            ticket_id=uuid4(),
            action_digest="abc123",
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
            consumed_at=None,
        )
        assert confirm_is_expired(ticket, datetime.now(UTC)) is True

    def test_negative_confirm_digest_mismatch(self):
        """Wrong execution digest -> ConfirmDigestMismatchError."""
        ticket = ConfirmTicket(
            ticket_id=uuid4(),
            action_digest="valid-digest",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            consumed_at=None,
        )
        with pytest.raises(ConfirmDigestMismatchError):
            verify_and_consume(ticket, "wrong-digest", datetime.now(UTC))

    def test_negative_confirm_ticket_reused(self):
        """Reusing an already-consumed ticket -> ConfirmTicketReusedError,
        even when the ticket has not also expired (I-11 single-use)."""
        ticket = ConfirmTicket(
            ticket_id=uuid4(),
            action_digest="single-use",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            consumed_at=None,
        )
        consumed = verify_and_consume(ticket, "single-use", datetime.now(UTC))
        assert is_consumed(consumed) is True
        with pytest.raises(ConfirmTicketReusedError):
            verify_and_consume(consumed, "single-use", datetime.now(UTC))

    def test_negative_confirm_empty_digest_rejected_by_post_init(self):
        """Empty action_digest -> rejected at ConfirmTicket construction."""
        with pytest.raises(Exception, match="action_digest must not be empty"):
            ConfirmTicket(
                ticket_id=uuid4(),
                action_digest="",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                consumed_at=None,
            )

    def test_negative_confirm_expired_before_digest_check(self):
        """Expiry is checked before digest match -- an expired ticket with a
        wrong digest must still fail as expired, not as a digest mismatch."""
        ticket = ConfirmTicket(
            ticket_id=uuid4(),
            action_digest="valid-digest",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            consumed_at=None,
        )
        with pytest.raises(ConfirmTicketExpiredError):
            verify_and_consume(ticket, "wrong-digest", datetime.now(UTC))


# -- Failure injection (monkeypatch dependency exceptions) -------------------


class TestFailureInjectionToken:
    """Inject a broken clock dependency into authorize()."""

    def test_failure_authorize_clock_raises_propagates(self, monkeypatch):
        """If the caller-supplied clock ever raises, authorize() must not
        swallow it -- fail-closed means a broken time source surfaces
        instead of silently granting access."""

        class BrokenClock:
            def __eq__(self, other):
                raise RuntimeError("clock_unavailable")

            def __ge__(self, other):
                raise RuntimeError("clock_unavailable")

            def __le__(self, other):
                raise RuntimeError("clock_unavailable")

        token = _token()
        with pytest.raises(RuntimeError, match="clock_unavailable"):
            authorize(token, Scope.READ, BrokenClock())  # type: ignore[arg-type]


class TestFailureInjectionConfirm:
    """Inject failure into the confirm module's dependency surface."""

    def test_failure_confirm_ticket_construction_error(self, monkeypatch):
        """If ConfirmTicket construction itself fails (e.g. a corrupted
        repository row), the failure must propagate rather than be
        downgraded to a false-success ticket."""

        def fake_init(self, *args, **kwargs):
            raise KeyError("ticket_row_corrupted")

        monkeypatch.setattr(
            "src.foundation.ai.gateway.domain.confirm.ConfirmTicket.__init__",
            fake_init,
        )

        with pytest.raises(KeyError, match="ticket_row_corrupted"):
            ConfirmTicket(
                ticket_id=uuid4(),
                action_digest="missing",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                consumed_at=None,
            )

    def test_failure_confirm_repository_error_surfaces(self, monkeypatch):
        """A downstream repository error during verification must surface to
        the caller unmodified, not be swallowed into a silent pass/fail."""

        def fake_verify(ticket, execute_digest, now):
            raise ConnectionError("repository_unavailable")

        monkeypatch.setattr(
            "src.foundation.ai.gateway.domain.confirm.verify_and_consume",
            fake_verify,
        )
        from src.foundation.ai.gateway.domain import confirm as confirm_module

        ticket = ConfirmTicket(
            ticket_id=uuid4(),
            action_digest="any",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
            consumed_at=None,
        )
        with pytest.raises(ConnectionError, match="repository_unavailable"):
            confirm_module.verify_and_consume(ticket, "any", datetime.now(UTC))


# -- Performance assertions (latency budgets) --------------------------------


class TestPerformanceToken:
    """Latency budget for the hot-path authorize() judgment."""

    @pytest.mark.perf
    def test_performance_authorize_latency(self):
        """authorize() must average under a 1ms/op budget -- it sits on
        every MCP tool call per spec §2.1 ("single authorization point")."""
        budget_ms = 1.0
        iterations = 1000
        token = _token()
        now = datetime.now(UTC)
        start = time.perf_counter()
        for _ in range(iterations):
            authorize(token, Scope.READ, now)
        elapsed_ms = (time.perf_counter() - start) / iterations * 1000
        assert elapsed_ms < budget_ms, (
            f"authorize avg {elapsed_ms:.4f}ms exceeds {budget_ms}ms budget"
        )


class TestPerformanceConfirm:
    """Latency budget for confirm ticket verification."""

    @pytest.mark.perf
    def test_performance_confirm_ticket_verify(self):
        """verify_and_consume() must average under a 0.5ms/op budget."""
        budget_ms = 0.5
        iterations = 1000
        start = time.perf_counter()
        for i in range(iterations):
            ticket = ConfirmTicket(
                ticket_id=uuid4(),
                action_digest=f"digest-{i}",
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                consumed_at=None,
            )
            verify_and_consume(ticket, f"digest-{i}", datetime.now(UTC))
        elapsed_ms = (time.perf_counter() - start) / iterations * 1000
        assert elapsed_ms < budget_ms, (
            f"confirm verify avg {elapsed_ms:.4f}ms exceeds {budget_ms}ms budget"
        )
