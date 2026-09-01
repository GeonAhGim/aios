from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.services.paper_strategy_projection import (
    PaperStrategyProjectionInput,
    project_paper_eligible_strategy,
)
from src.services.strategy_builder_service import StrategyDetail, StrategyLifecycleError

_HASH = "a" * 64


def _strategy(lifecycle_status: str = "PAPER_TRADING") -> StrategyDetail:
    return StrategyDetail(
        strategy_id="legacy-strategy-1",
        version="1.2.3",
        owner_user_id=UUID("11111111-1111-4111-8111-111111111111"),
        target_asset="KRX:005930",
        market="kr_equity",
        exchange="KRX",
        lifecycle_status=lifecycle_status,
        fsm_definition={"strategy_id": "legacy-strategy-1"},
    )


def _projection() -> PaperStrategyProjectionInput:
    return PaperStrategyProjectionInput(
        contract_id=uuid4(),
        contract_strategy_id=uuid4(),
        tenant_id="tenant-demo",
        trace_id="0123456789abcdef",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        markets=["kr_equity"],
        asset_classes=["equity"],
        time_horizons=["medium"],
        hypothesis_statement="A documented, paper-only hypothesis.",
        evidence_refs=[uuid4()],
        uncertainty="Not approved for live trading.",
        artifact_ref="artifact:strategy:legacy-strategy-1",
        artifact_hash=f"sha256:{_HASH}",
        build_environment_ref="build:paper:1",
        license_ref="license:internal",
    )


def test_projection_requires_a_paper_trading_legacy_strategy():
    package = project_paper_eligible_strategy(_strategy(), _projection())

    assert package.lifecycle == "PAPER_ELIGIBLE"
    assert package.dependencies.capabilities == []
    assert package.risk_envelope.prohibited_actions == [
        "direct_broker_access",
        "live_order_submission",
    ]


@pytest.mark.parametrize("status", ["GENERATED", "APPROVED", "DEPLOYED"])
def test_projection_refuses_non_paper_or_live_legacy_states(status: str):
    with pytest.raises(StrategyLifecycleError, match="PAPER_TRADING"):
        project_paper_eligible_strategy(_strategy(status), _projection())
