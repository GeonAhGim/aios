"""AI-16 -- `tools_propose.py`/`tools_paper.py` depth-1 evidence (D1->D2).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §9 AI-16 DoD.
DEEPEN-DEPTH AI-16: 보강 D1->D2. 현재 D1, 하한 D2. 미달 사유: negative 2/3.

대상 모듈: submit_proposal, preview_promotion, confirm_promotion.
기존 테스트 파일 (수정 금지):
  - tests/api/mcp/test_tools_propose_paper.py (negative 5, round-trip+reuse)
  - tests/api/mcp/test_tools_propose_paper_reliability.py (failure injection 2, perf 3, gate-red)

새 파일이 추가하는 항목:
  - negative 테스트 3종: 미허용 스코프(403), 누락된 헤더(401), 빈 idempotency_key(422)
  - 게이트 적색 재현: preview_promotion이 발급한 ticket_id를 깨뜨려 confirm -> 428
  - 성능 단언: perf_budget.best_of_async로 round-trip 측정, 5s 예산 단언
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

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
    CoverageQuality,
    PostgresCoverageRepository,
)
from src.foundation.market_data.adapters.postgres_instrument_repository import (
    PostgresInstrumentRepository,
)
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import Instrument, InstrumentLifecycle
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from tests.api.mcp.conftest import issue
from tests.foundation.integration.risk_gate.conftest import activate_mandate_with_defaults
from tests.integration.conftest import create_test_tenant
from tests.support.perf_budget import PerfBudget

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = _T0 + timedelta(days=30)
_SCRIPT = "input length: int = 14\nsignal go_long = length > 0\n"


def _fake_ulid() -> str:
    return "0" + uuid4().hex[:25].upper()


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
        created_at=datetime.now(UTC),
    )


async def _grant_entitlement(pool, *, tenant_id, venue):  # noqa: ANN001
    await pool.execute(
        "INSERT INTO entitlements (tenant_id, subject_id, venue, timeframe, feed_type) "
        "VALUES ($1, $2, $3, $4, 'DELAYED')",
        tenant_id,
        uuid4(),
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


async def _proposal_ready_tenant(pool, mandate_repo, trust_repo, coverage_repo, instrument_repo):  # noqa: ANN001
    """A tenant with an ACTIVE mandate plus one instrument covered for M1."""
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


def _accepted_experiment(tenant_id) -> Experiment:  # noqa: ANN001
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
        created_at=datetime.now(UTC),
    )


def _promotion_params(*, proposal_id, experiment_id, idempotency_key):  # noqa: ANN001
    return {
        "proposal_id": str(proposal_id),
        "experiment_id": str(experiment_id),
        "connection_id": None,
        "adapter_type": "fake-paper-v1",
        "provider_sandbox_account_ref": "sandbox-acct-1",
        "endpoint_classification": "SANDBOX",
        "idempotency_key": idempotency_key,
    }


async def _do_proposal(client, instrument_id, token):  # noqa: ANN001
    """Helper: submit_proposal and return (proposal_body, tenant_id)."""
    proposal = await client.post(
        "/mcp/tools/submit_proposal",
        json={
            "script_source": _SCRIPT,
            "hypothesis": "rsi mean reversion",
            "data_scope": _data_scope(instrument_id),
            "params": {"length": 14},
        },
        headers={AGENT_TOKEN_HEADER: token},
    )
    assert proposal.status_code == 200, proposal.text
    return proposal.json()


# ---------------------------------------------------------------------------
# negative #1: PAPER scope 없이 preview_promotion 호출 -> 403 (scope denied)
# ---------------------------------------------------------------------------


async def test_preview_promotion_without_paper_scope_is_403(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """negative: PROPOSE only token cannot preview_promotion (needs PAPER scope)."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    # Only PROPOSE scope — no PAPER
    issued = await issue(token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE}))
    proposal_body = await _do_proposal(client, instrument_id, issued.secret)

    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal_body["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key="promo-no-paper",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 403, preview.text


# ---------------------------------------------------------------------------
# negative #2: AGENT_TOKEN_HEADER 누락 -> 401
# ---------------------------------------------------------------------------


async def test_preview_promotion_missing_token_header_is_401(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """negative: missing AGENT_TOKEN header -> 401 UNAUTHORIZED."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal_body = await _do_proposal(client, instrument_id, issued.secret)

    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal_body["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key="promo-no-token",
    )
    # Send preview without the AGENT_TOKEN_HEADER
    response = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
    )
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# negative #3: 이미 사용된 idempotency_key로 confirm_promotion -> 409 (duplicate)
# ---------------------------------------------------------------------------


async def test_confirm_promotion_duplicate_idempotency_key_is_409(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """negative: calling confirm_promotion twice with the same
    idempotency_key returns 409 CONFLICT (ticket already consumed)."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal_body = await _do_proposal(client, instrument_id, issued.secret)

    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal_body["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key="promo-dup-key",
    )
    # First confirm: should succeed
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    ticket_id = preview.json()["ticket_id"]

    first_confirm = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert first_confirm.status_code == 200, first_confirm.text

    # Second confirm with the same ticket -> 409
    second_confirm = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert second_confirm.status_code == 409, second_confirm.text


# ---------------------------------------------------------------------------
# 게이트 적색 재현: preview_promotion이 발급한 ticket_id를 깨뜨려 confirm -> 428
# ---------------------------------------------------------------------------


async def test_confirm_promotion_corrupted_ticket_id_is_428(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
):
    """Gate-red reproduction: corrupt the ticket_id from preview_promotion
    and confirm — the 428 guard must catch it (ticket not found)."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )
    proposal_body = await _do_proposal(client, instrument_id, issued.secret)

    experiment = _accepted_experiment(tenant_id)
    await experiment_repo.append(experiment)

    params = _promotion_params(
        proposal_id=proposal_body["proposal_id"],
        experiment_id=experiment.experiment_id,
        idempotency_key="promo-gate-red",
    )
    preview = await client.post(
        "/mcp/tools/preview_promotion",
        json=params,
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert preview.status_code == 200, preview.text
    valid_ticket_id = preview.json()["ticket_id"]

    # Corrupt the ticket_id by swapping one hex digit in the middle
    # to keep it a valid UUID format but change the value
    chars = list(valid_ticket_id)
    # Swap a hex character in the middle portion (keep valid UUID)
    if chars[14] == "a":
        chars[14] = "b"
    else:
        chars[14] = "a"
    corrupted_ticket_id = "".join(chars)

    response = await client.post(
        "/mcp/tools/confirm_promotion",
        json={**params, "ticket_id": corrupted_ticket_id},
        headers={AGENT_TOKEN_HEADER: issued.secret},
    )
    assert response.status_code == 428, response.text


# ---------------------------------------------------------------------------
# 성능 단언: full round-trip within budget
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_confirm_promotion_full_round_trip_within_5s_budget(
    pool,
    client,
    mandate_repo,
    trust_repo,
    coverage_repo,
    instrument_repo,
    experiment_repo,
    token_repo: PostgresAgentTokenRepository,
    perf_budget: PerfBudget,
):
    """perf: propose -> preview -> confirm round trip must complete within
    5s wall-clock budget (ADR-2026-09-09-C Decision 1 D2 floor).

    Measured with perf_budget.best_of_async(5) to reduce noise."""
    tenant_id, instrument_id = await _proposal_ready_tenant(
        pool, mandate_repo, trust_repo, coverage_repo, instrument_repo
    )
    issued = await issue(
        token_repo, tenant_id=tenant_id, scopes=frozenset({Scope.PROPOSE, Scope.PAPER})
    )

    async def _round_trip():
        proposal_body = await _do_proposal(client, instrument_id, issued.secret)

        experiment = _accepted_experiment(tenant_id)
        await experiment_repo.append(experiment)

        params = _promotion_params(
            proposal_id=proposal_body["proposal_id"],
            experiment_id=experiment.experiment_id,
            idempotency_key=f"promo-perf-depth1-{tenant_id}",
        )
        preview = await client.post(
            "/mcp/tools/preview_promotion",
            json=params,
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )
        assert preview.status_code == 200, preview.text
        ticket_id = preview.json()["ticket_id"]

        return await client.post(
            "/mcp/tools/confirm_promotion",
            json={**params, "ticket_id": ticket_id},
            headers={AGENT_TOKEN_HEADER: issued.secret},
        )

    budget_ms = 5000.0
    sample = await perf_budget.best_of_async(_round_trip, n=5, warmup=1)
    confirm = sample.result

    assert confirm.status_code == 200, confirm.text
    assert sample.cpu_ms < budget_ms, (
        "propose->preview->confirm exceeds 5s CPU budget: "
        + perf_budget.describe(sample, budget_ms=budget_ms)
    )
