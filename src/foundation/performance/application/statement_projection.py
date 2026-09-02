"""도메인 `PerformanceStatement` → 계약 `PerformanceStatementView` 매핑.

compute_statement/correct_statement/get_statement이 전부 같은 변환을 쓴다
(71번 §4 "domain은 순수 계산, contracts는 소비자 대상 표현"의 경계를 한
지점에 모은다).

`MoneyValue`가 요구하는 currency/precision은 domain에 없다 — `pm-v1` 방법론
스콥에서 statement 전체가 단일 통화(KRW)라고 가정한다(strategy_executions.
currency의 기본값과 동일, ADR-2026-08-28 다자산군 확장 이전 전제). 여러
통화를 섞어 계산하는 시나리오는 이 리프의 스콥이 아니다(§10 미확인 항목).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.performance.contracts import v1
from src.foundation.performance.domain.models import ComponentBreakdown, PerformanceStatement

STATEMENT_CURRENCY = "KRW"
STATEMENT_PRECISION = 2

_BREAKDOWN_FIELDS = (
    "gross_pnl",
    "fees",
    "slippage",
    "funding",
    "fx",
    "cashflows_net",
    "estimated_tax",
    "net_pnl",
)


def _money(amount: Decimal | None, *, as_of: datetime, final: bool) -> v1.MoneyValue:
    return v1.MoneyValue(
        amount=amount,
        currency=STATEMENT_CURRENCY,
        precision=STATEMENT_PRECISION,
        as_of=as_of,
        state="FINAL" if final else "ESTIMATED",
    )


def _components_view(
    b: ComponentBreakdown, *, as_of: datetime, final: bool
) -> v1.ComponentBreakdown:
    return v1.ComponentBreakdown(
        **{f: _money(getattr(b, f), as_of=as_of, final=final) for f in _BREAKDOWN_FIELDS}
    )


def statement_to_view(s: PerformanceStatement) -> v1.PerformanceStatementView:
    final = s.state != s.state.ESTIMATED
    return v1.PerformanceStatementView(
        id=s.id,
        tenant_id=s.tenant_id,
        scope=v1.StatementScope(s.scope),
        scope_ref=s.scope_ref,
        period_start=s.period_start,
        period_end=s.period_end,
        as_of=s.as_of,
        methodology_version=s.methodology_version,
        methodology_hash=s.methodology_hash,
        input_refs=list(s.input_refs),
        components=_components_view(s.components, as_of=s.as_of, final=final),
        returns=[
            v1.ReturnValue(
                value_pct=r.value_pct,
                basis=r.basis,  # type: ignore[arg-type]
                method=r.method,  # type: ignore[arg-type]
                period_start=r.period_start,
                period_end=r.period_end,
                annualized=r.annualized,
                periods_per_year=r.periods_per_year,
            )
            for r in s.returns
        ],
        risk=s.risk,
        benchmark=s.benchmark,
        benchmark_ref=s.benchmark_ref,
        state=v1.StatementState(s.state.value),
        revision_no=s.revision_no,
        prior_statement_id=s.prior_statement_id,
        identity_ok=s.identity_ok,
        identity_residual=s.identity_residual,
        limitations=list(s.limitations),
        evidence_refs=list(s.evidence_refs),
    )
