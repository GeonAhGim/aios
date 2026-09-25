"""AI-16 -- `tools_propose.py`/`tools_paper.py` end-to-end MCP round trip.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-16 DoD
("tools_propose/paper + confirmation token round trip + adversarial: ticket
reuse -> 409"), §1 "confirmation token", INVARIANTS.md I-11. ADR-2026-09-09-C
D2: negative >= 3 (met jointly with the digest-mismatch/cross-tenant/
unaccepted-evaluation cases below).

Setup helpers (`_instrument`/`_span`/`_grant_entitlement`) are copied from
`tests/integration/foundation/market_data/test_get_coverage.py` -- the same
DC-1/DC-8 rows `submit_proposal`'s coverage lookup reads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.mcp.server import AGENT_TOKEN_HEADER, create_mcp_app
from src.data.models.base import AssetClass
from src.foundation.ai.gateway.adapters.postgres_token_repository import (
    PostgresAgentTokenRepository,
)
from src.foundation.ai.gateway.domain.token_rules import Scope
from src.foundation.experiments.adapters.postgres_repository import PostgresExperimentRepository
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.market_data.adapters.postgres_coverage_repository import (
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentLifecycle
from src.foundation.market_data.ports.coverage_repository import CoverageQuality
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.api.mcp.conftest import issue
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(days=30)
_SCRIPT = "input length: int = 14\nsignal go_long = length > 0\n"


def _fake_ulid() -> str:
    return "0" + uuid.uuid4().hex[:25].upper()


def _instrument(instrument_id: str) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=InstrumentLifecycle.ACTIVE,
        created_at=datetime.now(timezone.utc),
    )


async def _grant_entitlement(pool: asyncpg.Pool, *, tenant_id: UUID, venue: Venue) -> None:
    await pool.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $2, $3, $4, 'DELAYED')",
        tenant_id,
        uuid.uuid4(),
        venue.value,
        Timeframe.M1.value,
    )


@pytest.fixture
def coverage_repo(pool):  # noqa: ANN001
    return PostgresCoverageRepository(pool)


@pytest.fixture
def instrument_repo(pool):  # noqa: ANN001
    return PostgresInstrumentRepository(pool)


@pytest.fixture
def mandate_repo(pool):  # noqa: ANN001
    return PostgresMandateRepository(pool)


@pytest.fixture
def trust_repo(pool):  # noqa: ANN001
    return PostgresTrustRepository(pool)


@pytest.fixture
def experiment_repo(pool):  # noqa: ANN001
    return PostgresExperimentRepository(pool)


@pytest.fixture
async def client(pool):  # noqa: ANN001
    app = create_mcp_app(pool)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://mcp.test") as c:
        yield c


async def _proposal_ready_tenant(
    pool: asyncpg.Pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
) -> tuple[UUID, str]:
    """A tenant with an ACTIVE mandate (needed for the risk gate to ALLOW)
    plus one instrument fully covered for `Timeframe.M1` over `[_T0, _T1)`
    on `Venue.BITGET` (needed for `submit_proposal`'s data-scope check)."""
    tenant_id = await create_test_tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)
    instrument_id = _fake_ulid()
    async with pool.acquire() as conn, conn.transaction():
        await instrument_repo.create(conn, _instrument(instrument_id))
    await _grant_entitlement(pool, tenant_id=tenant_id, venue=Venue.BITGET)
    async with pool.acquire() as conn, conn.transaction():
        await coverage_repo.upsert_span(
            conn,
            StoredCoverageSpan(
                instrument_id=instrument_id,
                venue=Venue.BITGET,
                timeframe=Timeframe.M1,
                quality=CoverageQuality.VALIDATED,
                start=_T0,
                end=_T1,
            ),
        )
    return tenant_id, instrument_id


def _data_scope(instrument_id: str) -> dict:
    return {
        "instruments": [instrument_id],
        "tf": "1m",
        "span": [_T0.isoformat(), _T1.isoformat()],
    }


async def _submit_proposal(client, secret: str, instrument_id: str) -> dict:
    response = await client.post(
        "/mcp/tools/submit_proposal",
        json={
            "script_source": _SCRIPT,
            "hypothesis": "rsi mean reversion",
            "data_scope": _data_scope(instrument_id),
            "params": {"length": 14},
        },
        headers={AGENT_TOKEN_HEADER: secret},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _accepted_experiment(tenant_id: UUID) -> Experiment:
    return Experiment(
        experiment_id=uuid4(),
        tenant_id=tenant_id,
        reproducibility_key="a" * 64,
        kind=ExperimentKind.BACKTEST,
        inputs_hash="b" * 64,
        metrics={
            "context": {
                "outcome": "PASS",
                "metrics": {},
                "warnings": [],
                "hard_fail_reasons": [],
                "obligations": [],
            }
        },
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
    )


def _rejected_experiment(tenant_id: UUID) -> Experiment:
    return Experiment(
        experiment_id=uuid4(),
        tenant_id=tenant_id,
        reproducibility_key="c" * 64,
        kind=ExperimentKind.BACKTEST,
        inputs_hash="d" * 64,
        metrics={
            "backtest": {
                "outcome": "FAIL",
                "metrics": {},
                "warnings": [],
                "hard_fail_reasons": ["BACKTEST_LOOKAHEAD_VIOLATION"],
                "obligations": [],
            }
        },
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
    )


def _promotion_params(*, proposal_id: str, experiment_id: UUID, idempotency_key: str) -> dict:
    return {
        "proposal_id": proposal_id,
        "experiment_id": str(experiment_id),
        "connection_id": None,
        "adapter_type": "fake-paper-v1",
        "provider_sandbox_account_ref": "sandbox-acct-1",
        "endpoint_classification": "SANDBOX",
        "idempotency_key": idempotency_key,
    }


# --- 정상 경로 + 적대적(재사용 409): propose -> preview -> confirm -> 재사용 거부 ---


async def test_confirm_promotion_round_trip_then_ticket_reuse_is_409(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal = await _submit_proposal(client, issued.secret, instrument_id)

    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key=f"promo-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion", json=params, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    confirm_body = {**params, "ticket_id": ticket_id}
    first = await client.post(
        "/mcp/tools/confirm_promotion",
        json=confirm_body,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert first.status_code == 200, first.text
    assert first.json()["package_ref"] == proposal["proposal_id"]

    # adversarial: replaying the exact same confirm body with the exact same
    # (already-consumed) ticket_id must be rejected, not silently re-promoted.
    second = await client.post(
        "/mcp/tools/confirm_promotion",
        json=confirm_body,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert second.status_code == 409, second.text


# --- negative #1: 존재하지 않는 ticket_id는 428(AI_CONFIRM_REQUIRED) ---


async def test_confirm_promotion_unknown_ticket_is_428(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal = await _submit_proposal(client, issued.secret, instrument_id)
    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    body = {
        **_promotion_params(
            proposal_id=proposal["proposal_id"],
            experiment_id=experiment.experiment_id,
            idempotency_key=f"promo-unknown-{tenant_id}",
        ),
        "ticket_id": str(uuid4()),
    }
    response = await client.post(
        "/mcp/tools/confirm_promotion", json=body, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert response.status_code == 428, response.text


# --- negative #2: preview 이후 파라미터를 바꿔 confirm하면 digest 불일치 -> 409 ---


async def test_confirm_promotion_digest_mismatch_is_409(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal = await _submit_proposal(client, issued.secret, instrument_id)
    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key=f"promo-mismatch-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion", json=params, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    ticket_id = preview.json()["ticket_id"]

    tampered = {**params, "ticket_id": ticket_id, "idempotency_key": "a-different-key"}
    response = await client.post(
        "/mcp/tools/confirm_promotion", json=tampered, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    assert response.status_code == 409, response.text


# --- negative #3 (교차 테넌트): 다른 테넌트의 proposal_id는 404로 위장 ---


async def test_confirm_promotion_cross_tenant_proposal_is_404(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    owner_tenant, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    owner_token = await issue(token_repo, tenant_id=owner_tenant, scopes=frozenset({Scope.PROPOSE}))
    proposal = await _submit_proposal(client, owner_token.secret, instrument_id)
    experiment = _accepted_experiment(owner_tenant)
    await experiment_repo.append(experiment)

    attacker_tenant = await create_test_tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=attacker_tenant)
    attacker_token = await issue(
        token_repo, tenant_id=attacker_tenant, scopes=frozenset({Scope.PAPER})
    )

    body = {
        **_promotion_params(
            proposal_id=proposal["proposal_id"],
            experiment_id=experiment.experiment_id,
            idempotency_key=f"promo-cross-{attacker_tenant}",
        ),
        "ticket_id": str(uuid4()),
    }
    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json=body,
        headers={AGENT_TOKEN_HEADER: attacker_token.secret},
    )
    assert response.status_code == 404, response.text


# --- negative #4 (risk gate bypass defense): a FAIL experiment is never promoted ---


async def test_confirm_promotion_rejects_when_backing_experiment_failed(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """`evaluation` is reconstructed server-side from `experiment_id`, never
    trusted from the request body -- if the backing experiment actually
    FAILed (carries `hard_fail_reasons`), sending otherwise-valid promotion
    parameters must still be rejected via `ProposalNotAcceptedError` (spec
    §8's "risk gate bypass attempt" adversarial case)."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal = await _submit_proposal(client, issued.secret, instrument_id)
    experiment = _rejected_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key=f"promo-failed-{tenant_id}",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion", json=params, headers={AGENT_TOKEN_HEADER: issued.secret}
    )
    ticket_id = preview.json()["ticket_id"]

    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 409, response.text


# --- negative #5: 커버리지 밖 데이터 범위 제안은 400(AI_PROPOSAL_SCHEMA) ---


async def test_submit_proposal_rejects_uncovered_data_scope(
    pool, client, token_repo: PostgresAgentTokenRepository
):
    tenant_id = await create_test_tenant(pool)
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE}))
    uncovered_instrument = _fake_ulid()  # never registered/covered for this tenant

    response = await client.post(
        "/mcp/tools/submit_proposal",
        json={
            "script_source": _SCRIPT,
            "hypothesis": "no coverage exists for this instrument",
            "data_scope": _data_scope(uncovered_instrument),
            "params": {"length": 14},
        },
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 400, response.text
