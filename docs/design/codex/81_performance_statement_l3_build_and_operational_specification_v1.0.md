# Performance Statement L3/L4 Build and Operational Specification v1.0

> Implements: 51 Performance Statement, FND-09. Scope: transparent reporting, not a performance promise or tax finalization.

## 1. Statement model

| Record | Required fields | Rule |
|---|---|---|
| `valuation_snapshot` | scope, as_of, positions/cash refs, price/fx evidence, state | reconciled/estimated state explicit |
| `performance_methodology` | version, return formula, cashflow convention, cost/fx/tax assumptions, benchmark rule | immutable/versioned |
| `performance_statement` | tenant/scope/period, methodology ref, input refs, gross/net components, risk metrics, benchmark, state, revision | no overwrite; correction revision |
| `attribution_slice` | statement ref, dimension, contribution, confidence/limitation | values sum/reconcile by documented rules |

Every monetary/return value carries currency, precision, period/as-of, paper/live state, estimate/final state. Gross PnL, fees, spreads, slippage, FX, cashflow, estimated tax and net return are separate fields; null/unknown values cannot silently become zero.

## 2. Calculation and correction

Pipeline: select reconciled snapshots/fills/cashflows → apply methodology version → value positions with source evidence → compute costs/returns/risk/benchmark → validate accounting identities and missing inputs → persist immutable statement/evidence/audit → project. If upstream correction occurs, create `CORRECTED` revision with prior ref, delta, reason, and user notification policy; never rewrite statement history.

Benchmark is mandate/methodology bound at period start or documented revision; it cannot be swapped opportunistically after result. Paper and LIVE collections are physically separate query dimensions and cannot aggregate by default.

## 3. API/UI/errors/operations

`GET /v1/foundation/performance-statements` supports scope, period, methodology, state, cursor; `GET /{id}` returns components, attribution, evidence, assumptions and safe limitation text; export is asynchronous/audited. UI labels estimates, pending reconciliation, correction, paper/live, as-of, methodology clearly and never calls result ‘guaranteed’ or ‘AI predicted’.

Errors: `INTEGRITY_STATEMENT_INPUT_UNRECONCILED`, `VALIDATION_METHODOLOGY_REQUIRED`, `INTEGRITY_CURRENCY_PRECISION`, `STATE_STATEMENT_NOT_FINAL`, `AUTH_PERFORMANCE_SCOPE_DENIED`. SLI: statement calculation success, as-of lag, correction rate, reconciliation coverage, export completion. Alerts: methodology mismatch, unexplained component identity failure, paper/live mix attempt, stale statement for active user scope.

## 4. Named tests

| ID | Scenario |
|---|---|
| PRF-001 | reconciled input computes gross/net/cost/cashflow identity with evidence refs |
| PRF-002 | missing fee/slippage/cashflow/price marks pending or rejects, never zero-fills silently |
| PRF-003 | paper/live and estimated/final values cannot aggregate or be mislabeled |
| PRF-004 | correction creates immutable successor, delta/reason/audit, preserves original |
| PRF-005 | benchmark is pinned to methodology/period and cannot be post-hoc swapped |
| PRF-006 | currency rounding/precision and FX source/as-of pass deterministic tests |
| PRF-007 | tenant/scope access, export authorization, evidence filtering are enforced |
| PRF-008 | attribution reconciles or reports documented residual/limitation |
| PRF-009 | method version change produces new statement not silent recalculation |
| PRF-010 | load/export failure remains safe, auditable, and does not expose foreign data |
