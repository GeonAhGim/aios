"""`performance_statement`의 Decimal 필드 JSONB 직렬화/역직렬화 및 행 매핑.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.7 M5.

`components`/`returns`/`risk`/`benchmark`는 JSONB에 넣어야 하므로 Decimal을
문자열로 직렬화한다(부동소수 표현 오차로 값이 흔들리지 않게 — 이 세션
전반의 관례, reconciliation의 `compute_input_hash`와 같은 이유).
postgres_repository.py의 `PostgresPerformanceRepository`가 이 모듈의
함수들을 호출해 statement 행을 읽고 쓴다(P6: 300줄 초과 분할)."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

import asyncpg

from src.foundation.performance.domain.models import (
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)

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


def _decimal_or_none_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _str_to_decimal_or_none(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def breakdown_to_json(b: ComponentBreakdown) -> str:
    return json.dumps({f: _decimal_or_none_to_str(getattr(b, f)) for f in _BREAKDOWN_FIELDS})


def _breakdown_from_json(raw: str) -> ComponentBreakdown:
    data = json.loads(raw)
    return ComponentBreakdown(**{f: _str_to_decimal_or_none(data[f]) for f in _BREAKDOWN_FIELDS})


def returns_to_json(returns: tuple[ReturnFigure, ...]) -> str:
    return json.dumps(
        [
            {
                "value_pct": _decimal_or_none_to_str(r.value_pct),
                "basis": r.basis,
                "method": r.method,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "annualized": r.annualized,
                "periods_per_year": r.periods_per_year,
            }
            for r in returns
        ]
    )


def _returns_from_json(raw: str) -> tuple[ReturnFigure, ...]:
    return tuple(
        ReturnFigure(
            value_pct=_str_to_decimal_or_none(r["value_pct"]),
            basis=r["basis"],
            method=r["method"],
            period_start=datetime.fromisoformat(r["period_start"]),
            period_end=datetime.fromisoformat(r["period_end"]),
            annualized=r["annualized"],
            periods_per_year=r["periods_per_year"],
        )
        for r in json.loads(raw)
    )


def decimal_dict_to_json(d: dict[str, Decimal | None] | None) -> str | None:
    if d is None:
        return None
    return json.dumps({k: _decimal_or_none_to_str(v) for k, v in d.items()})


def _decimal_dict_from_json(raw: str | None) -> dict[str, Decimal | None] | None:
    if raw is None:
        return None
    return {k: _str_to_decimal_or_none(v) for k, v in json.loads(raw).items()}


def row_to_statement(row: asyncpg.Record) -> PerformanceStatement:
    return PerformanceStatement(
        id=row["id"],
        tenant_id=row["tenant_id"],
        scope=row["scope"],
        scope_ref=row["scope_ref"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        as_of=row["as_of"],
        methodology_version=row["methodology_version"],
        methodology_hash=row["methodology_hash"],
        input_refs=tuple(json.loads(row["input_refs"])),
        components=_breakdown_from_json(row["components"]),
        returns=_returns_from_json(row["returns"]),
        risk=_decimal_dict_from_json(row["risk"]) or {},
        benchmark=_decimal_dict_from_json(row["benchmark"]),
        benchmark_ref=row["benchmark_ref"],
        state=StatementState(row["state"]),
        revision_no=row["revision_no"],
        prior_statement_id=row["prior_statement_id"],
        identity_ok=row["identity_ok"],
        identity_residual=row["identity_residual"],
        limitations=tuple(row["limitations"]),
        evidence_refs=tuple(json.loads(row["evidence_refs"])),
    )
