# Portfolio Mandate L3/L4 Build and Operational Specification v1.0

> Implements: 45 Portfolio Mandate, FND-02. This context makes constraints executable; it does not generate orders.

## 1. Schema and invariant model

| Record | Required fields | Integrity rule |
|---|---|---|
| `portfolio_mandate` | id, tenant_id, subject_id, state, active_revision_id, created_at | one active mandate per subject/portfolio scope |
| `mandate_revision` | id, mandate_id, revision_no, objective, risk_budget, liquidity, universe, exposure, leverage, autonomy, tax_values, hash | immutable; unique `(mandate_id, revision_no)` |
| `policy_bundle` | id, mandate_revision_id, compiler_version, rule_hash, state | deterministic compiler output |
| `policy_decision` | id, tenant_id, bundle_id, command_fingerprint, outcome, reasons, obligations, evaluated_at, expires_at | no overwrite; fingerprint/index for dedupe |
| `approval_binding` | id, proposed_revision_id, required_roles, state, expires_at | material changes require distinct actor approval |

All numeric limits carry unit/currency/asset scope and inclusive/exclusive semantics. Ambiguous values such as ‘moderate risk’ cannot reach the rule compiler; they must map to explicit tier/range with disclosure evidence.

## 2. Material-change rules

Material changes are: increased maximum drawdown, leverage/short enablement, expanded asset/venue universe, reduced liquidity reserve, autonomy elevation, new delegated actor, or changed jurisdiction/tax profile. These create `PROPOSED` revision, require MFA, current consent/suitability, cooling-off timestamp, and where policy says so a distinct approver. No update mutates an active revision.

State: `DRAFT → PROPOSED → ACTIVE → SUPERSEDED`; `ACTIVE → PAUSED`; `PAUSED → ACTIVE` needs fresh policy evaluation; `DRAFT/PROPOSED → CANCELLED`. One transaction promotes a revision and supersedes the prior revision.

## 3. Compiler and decision API

`PolicyCompiler.compile(MandateRevision) -> PolicyBundle` is pure, versioned, and deterministic. It emits normalized rules such as `MAX_TOTAL_EXPOSURE`, `MAX_SINGLE_INSTRUMENT`, `MIN_CASH_BUFFER`, `ALLOWED_AUTONOMY`, `FORBIDDEN_ASSET`, `MAX_DAILY_LOSS`.

`EvaluatePolicy` accepts a typed `PolicyEvaluationSubject`, not arbitrary JSON. Output is:

```json
{"outcome":"ALLOW|DENY|REQUIRE_APPROVAL|REQUIRE_REASSESSMENT|PAUSE_REQUIRED",
 "reason_codes":["POLICY_MAX_DRAWDOWN"], "obligations":["REQUIRE_RISK_GATE"],
 "bundle_ref":"...", "expires_at":"...", "trace_id":"..."}
```

Denied decisions are cached only for their exact input fingerprint and short TTL; a policy decision never bypasses a later RiskDecision.

## 4. API, concurrency and audit

Routes: create draft; replace draft with `If-Match`; submit proposal; approve material change; activate; pause; fetch active/revision/history; evaluate (internal service API). User APIs return a human-readable summary generated from explicit policy fields, plus the machine decision/reason code. Every creation, activation, pause and decision emits evidence/audit with old/new revision refs.

Use serializable transaction or optimistic revision checks for activation. Concurrent proposed revisions are allowed, but only one can become active; a stale approver gets `409 STATE_STALE_REVISION`.

## 5. Operations and named tests

SLIs: compiler failures, policy evaluation latency, expired mandate rate, pause propagation lag, material change abandonment. Alert on compiler version skew, inability to evaluate active mandate, pause propagation >60s, unexpected ALLOW rate shift after release.

| ID | Scenario |
|---|---|
| MAN-001 | active mandate compiles to stable hash and deterministic decision |
| MAN-002 | no active mandate denies deployment/order precondition |
| MAN-003 | leverage/universe/risk increase cannot activate without MFA/fresh trust/cooling-off |
| MAN-004 | concurrent approval sees stale revision and cannot overwrite active mandate |
| MAN-005 | forbidden asset/insufficient cash/limit breach yields exact reason and obligation |
| MAN-006 | pause propagates to all linked deployments and blocks new decisions |
| MAN-007 | tenant/subject mismatch and foreign mandate ref are denied without existence leak |
| MAN-008 | compiler unknown enum/unit and numeric overflow fail closed |
| MAN-009 | audit includes previous/current revision, actor, reason, trace; no sensitive answer payload |
| MAN-010 | feature rollback leaves active immutable revisions readable and stops new activation |

Rollout follows pure compiler fixtures → internal drafts → paper-only mandate activation → limited cohort. No LIVE authorization is implied by an `ALLOW` decision.
