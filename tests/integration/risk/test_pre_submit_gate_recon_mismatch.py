"""R-35 `evaluate_pre_submit` RECON_MISMATCH 심볼단위 DENY — task-9224.

task-9066 audit F5 후속: `RECON_MISMATCH`가 `risk_signal`에 signal-only로
쌓이기만 하고 어떤 주문 경로도 읽지 않던 실배선 부재를 고친다. 5번째
규칙(`recon_mismatch`)이 이 심볼에 열린 RECON_MISMATCH 신호가 있으면
DENY하고, 다른 심볼에는 영향을 주지 않는다(심볼단위 격리).
`test_pre_submit_gate.py`에서 분리(loc_over_500, CLAUDE.md §7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from src.core.risk.decision import RiskOutcome
from src.foundation.risk_gate.adapters.postgres_signal_repository import (
    PostgresSignalRepository,
    dedupe_key_for,
)
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.foundation.risk_gate.domain.models import RiskSignalType
from tests.integration.conftest import create_test_tenant
from tests.integration.risk.conftest import (
    PROVIDER,
    QTY,
    SIDE,
    healthy_connection_repo,
    normal_risk_repo,
)


def _unique_symbol(prefix: str) -> str:
    # dedupe_key_for's 5-minute floor has no tenant_id in it (§6 row 453) --
    # a fixed symbol string would collide across repeated test runs within
    # the same window, silently no-opping `insert_if_new` for a later run.
    return f"{prefix}/{uuid4().hex[:8]}"


async def _open_recon_mismatch(pool, *, tenant_id, symbol: str) -> None:
    repo = PostgresSignalRepository(pool)
    as_of = datetime.now(timezone.utc)
    await repo.insert_if_new(
        signal_id=uuid4(),
        dedupe_key=dedupe_key_for(RiskSignalType.RECON_MISMATCH.value, symbol, as_of),
        tenant_id=tenant_id,
        signal_type=RiskSignalType.RECON_MISMATCH.value,
        severity="CRITICAL",
        as_of=as_of,
        source="test_pre_submit_gate_recon_mismatch",
    )


async def test_open_recon_mismatch_for_symbol_denies(pool, risk_repo, recorder):
    tenant_id = await create_test_tenant(pool)
    symbol = _unique_symbol("BTC/USDT")
    await _open_recon_mismatch(pool, tenant_id=tenant_id, symbol=symbol)
    signal_repo = PostgresSignalRepository(pool)

    decision, _ = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=symbol,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_RECON_MISMATCH_SYMBOL" in decision.reason_codes


async def test_open_recon_mismatch_does_not_deny_a_different_symbol(pool, risk_repo, recorder):
    """DoD 필수 negative test — 심볼단위 격리: 두 심볼 중 한쪽만 열린
    RECON_MISMATCH 신호가 있으면, 다른 심볼의 평가는 영향받지 않는다."""
    tenant_id = await create_test_tenant(pool)
    symbol_a = _unique_symbol("BTC/USDT")
    symbol_b = _unique_symbol("ETH/USDT")
    await _open_recon_mismatch(pool, tenant_id=tenant_id, symbol=symbol_a)
    signal_repo = PostgresSignalRepository(pool)

    decision, _ = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=symbol_b,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.ALLOW


async def test_other_tenants_recon_mismatch_does_not_leak(pool, risk_repo, recorder):
    """교차 tenant 격리 — 다른 tenant의 RECON_MISMATCH는 이 tenant의 같은
    심볼 평가에 영향을 주지 않는다(has_open_signal의 tenant_id 스코핑)."""
    tenant_id = await create_test_tenant(pool)
    other_tenant_id = await create_test_tenant(pool)
    symbol = _unique_symbol("BTC/USDT")
    await _open_recon_mismatch(pool, tenant_id=other_tenant_id, symbol=symbol)
    signal_repo = PostgresSignalRepository(pool)

    decision, _ = await evaluate_pre_submit(
        normal_risk_repo(risk_repo),
        healthy_connection_repo(tenant_id),
        signal_repo,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=PROVIDER,
        symbol=symbol,
        side=SIDE,
        quantity=QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.ALLOW
