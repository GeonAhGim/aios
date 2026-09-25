"""In-memory test doubles for `test_promote_to_paper.py` -- split out of the
test module itself (file policy ADR-2026-09-10-C §7) since these are shared
fixtures/builders, not test cases.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from src.foundation.ai.factory.contracts.v1 import (
    DataScope,
    ProposalEvaluation,
    ProviderRef,
    StrategyProposal,
)
from src.foundation.ai.gateway.domain.confirm import ConfirmTicket
from src.foundation.mandates.domain.models import (
    Autonomy,
    MandateRevision,
    MandateRevisionState,
    PolicyBundle,
    PolicyDecision,
    PortfolioMandate,
)
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.paper_control.domain.models import (
    CommandOutcome,
    CommandType,
    DeploymentCommand,
    PaperDeployment,
)
from src.foundation.risk_gate.domain.models import RiskEvaluation, SafetyControl

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = START + timedelta(days=30)
INSTRUMENT = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
DIGEST = "a" * 64


class FakeConfirmTicketRepository:
    def __init__(self) -> None:
        self._tickets: dict[UUID, ConfirmTicket] = {}

    def seed(self, ticket: ConfirmTicket) -> None:
        self._tickets[ticket.ticket_id] = ticket

    def has(self, ticket_id: UUID) -> bool:
        return ticket_id in self._tickets

    async def get(self, ticket_id: UUID) -> ConfirmTicket | None:
        return self._tickets.get(ticket_id)

    async def mark_consumed(
        self, ticket_id: UUID, *, execute_digest: str, now: datetime
    ) -> ConfirmTicket | None:
        ticket = self._tickets.get(ticket_id)
        if (
            ticket is None
            or ticket.consumed_at is not None
            or ticket.action_digest != execute_digest
            or now >= ticket.expires_at
        ):
            return None
        consumed = replace(ticket, consumed_at=now)
        self._tickets[ticket_id] = consumed
        return consumed


class FakePaperControlRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, PaperDeployment] = {}
        self._by_request_key: dict[tuple[UUID, str], UUID] = {}
        self._commands: dict[tuple[UUID, str], DeploymentCommand] = {}
        self.insert_deployment_calls = 0
        self.fail_insert_deployment: BaseException | None = None

    async def get_deployment_by_request_key(
        self, tenant_id: UUID, request_idempotency_key: str
    ) -> PaperDeployment | None:
        deployment_id = self._by_request_key.get((tenant_id, request_idempotency_key))
        return None if deployment_id is None else self._by_id.get(deployment_id)

    async def insert_deployment(self, deployment: PaperDeployment) -> PaperDeployment:
        self.insert_deployment_calls += 1
        if self.fail_insert_deployment is not None:
            raise self.fail_insert_deployment
        self._by_id[deployment.id] = deployment
        if deployment.request_idempotency_key is not None:
            key = (deployment.tenant_id, deployment.request_idempotency_key)
            self._by_request_key[key] = deployment.id
        return deployment

    async def get_command_by_idempotency_key(
        self, deployment_id: UUID, idempotency_key: str
    ) -> DeploymentCommand | None:
        return self._commands.get((deployment_id, idempotency_key))

    async def insert_command(
        self,
        *,
        deployment_id: UUID,
        idempotency_key: str,
        command_type: CommandType,
        actor_subject_id: UUID,
        outcome: CommandOutcome,
        detail: str | None,
    ) -> DeploymentCommand:
        command = DeploymentCommand(
            id=uuid4(),
            deployment_id=deployment_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            actor_subject_id=actor_subject_id,
            outcome=outcome,
            detail=detail,
        )
        self._commands[(deployment_id, idempotency_key)] = command
        return command


class FakeRiskGateRepository:
    def __init__(self, *, active_controls: tuple[SafetyControl, ...] = ()) -> None:
        self.active_controls = active_controls
        self.list_active_controls_calls = 0
        self.insert_evaluation_calls = 0

    async def get_cached_evaluation(
        self, tenant_id: UUID, fingerprint: str
    ) -> RiskEvaluation | None:
        return None

    async def list_active_controls(
        self,
        *,
        tenant_id: UUID,
        provider_code: str | None = None,
        include_all_providers: bool = False,
    ) -> tuple[SafetyControl, ...]:
        self.list_active_controls_calls += 1
        return self.active_controls

    async def insert_evaluation(self, evaluation: RiskEvaluation) -> RiskEvaluation:
        self.insert_evaluation_calls += 1
        return evaluation


class FakeMandateRepository:
    """One ACTIVE mandate revision with permissive limits -- the default
    `PolicyEvaluationSubject` `evaluate_risk_gate` builds when no `plan` is
    passed carries no exposure/loss fields, so `evaluate_policy` always
    returns ALLOW against any revision limits here (see
    `mandates/domain/rules/__init__.py::evaluate_policy`)."""

    def __init__(self) -> None:
        self.mandate_id = uuid4()
        self.revision_id = uuid4()
        self._revision = MandateRevision(
            id=self.revision_id,
            mandate_id=self.mandate_id,
            revision_no=1,
            state=MandateRevisionState.ACTIVE,
            max_total_exposure_pct=100.0,
            max_single_instrument_pct=100.0,
            min_cash_buffer_pct=0.0,
            max_daily_loss_pct=100.0,
            allowed_autonomy=Autonomy.PAPER,
        )
        self._bundle: PolicyBundle | None = None
        self._decisions: dict[tuple[UUID, str], PolicyDecision] = {}

    async def get_mandate(
        self, tenant_id: UUID, portfolio_id: UUID | None = None
    ) -> PortfolioMandate:
        return PortfolioMandate(
            id=self.mandate_id,
            tenant_id=tenant_id,
            subject_id=uuid4(),
            portfolio_id=uuid4(),
            active_revision_id=self.revision_id,
            created_at=NOW,
        )

    async def get_revision(self, revision_id: UUID) -> MandateRevision | None:
        return self._revision if revision_id == self.revision_id else None

    async def get_bundle_for_revision(self, revision_id: UUID) -> PolicyBundle | None:
        return self._bundle

    async def insert_policy_bundle(self, bundle: PolicyBundle) -> PolicyBundle:
        self._bundle = bundle
        return bundle

    async def get_cached_decision(
        self, tenant_id: UUID, command_fingerprint: str
    ) -> PolicyDecision | None:
        return self._decisions.get((tenant_id, command_fingerprint))

    async def insert_policy_decision(self, decision: PolicyDecision) -> PolicyDecision:
        self._decisions[(decision.tenant_id, decision.command_fingerprint)] = decision
        return decision


class UnusedConnectionRepository:
    """`connection_id=None` in every test here -- `evaluate_risk_gate` never
    touches this repository in that case. Any call would mean this leaf
    started passing a connection it was not asked to check."""

    async def get_connection(self, connection_id: UUID) -> Any:
        raise AssertionError("connection_repo must not be called when connection_id is None")

    async def get_latest_health(self, connection_id: UUID) -> Any:
        raise AssertionError("connection_repo must not be called when connection_id is None")


def make_proposal(**overrides: Any) -> StrategyProposal:
    base: dict[str, Any] = dict(
        proposal_id=uuid4(),
        script_source="input length: int = 14\nsignal go_long = length > 0\n",
        hypothesis="rsi mean reversion",
        data_scope=DataScope(
            instruments=frozenset({INSTRUMENT}), tf=Timeframe.H1, span=(START, END)
        ),
        params={"length": 14},
        provider_ref=ProviderRef.ANTHROPIC,
        prompt_hash="0" * 64,
        created_by_token=uuid4(),
    )
    base.update(overrides)
    return StrategyProposal(**base)


def make_evaluation(proposal: StrategyProposal, *, accepted: bool = True) -> ProposalEvaluation:
    return ProposalEvaluation(
        proposal_id=proposal.proposal_id,
        experiment_id=uuid4(),
        accepted=accepted,
        hard_fail_reasons=() if accepted else ("BACKTEST_LOOKAHEAD_VIOLATION",),
    )


def make_ticket(*, digest: str = DIGEST, expires_at: datetime | None = None) -> ConfirmTicket:
    return ConfirmTicket(
        ticket_id=uuid4(),
        action_digest=digest,
        expires_at=expires_at if expires_at is not None else NOW + timedelta(minutes=5),
    )


class Deps:
    """Bundles one full set of fresh fakes -- every test/perf-iteration gets
    its own tenant/idempotency scope so runs never collide."""

    def __init__(self) -> None:
        self.confirm_repo = FakeConfirmTicketRepository()
        self.paper_repo = FakePaperControlRepository()
        self.risk_repo = FakeRiskGateRepository()
        self.mandate_repo = FakeMandateRepository()
        self.connection_repo = UnusedConnectionRepository()
        self.tenant_id = uuid4()
