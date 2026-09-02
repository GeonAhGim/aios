# Risk and Safety L3/L4 Build and Operational Specification v1.0

> Implements: 48 Risk & Safety Gate, FND-06. Risk is deterministic final veto; it does not advise or execute.

## 1. Decision record and rule inputs

| Record | Required fields | Integrity |
|---|---|---|
| `risk_rule_bundle` | id, scope, version, rule hash, effective window, state | immutable published version |
| `risk_evaluation` | id, tenant/deployment ref, subject fingerprint, input evidence refs, outcome, reasons, obligations, expiry | exact input fingerprint + rule version |
| `safety_control` | id, scope/global-tenant-account-deployment-provider, state, reason, actor, fence token | active controls compose by most restrictive outcome |
| `risk_signal` | type, severity, as-of, source/evidence, state | dedupe/source validity policy |

Inputs are typed snapshots: mandate policy, connection health, account snapshot freshness, package lifecycle, validation expiry, market/data health, reconciliation state, safety controls. Missing/unreadable input yields `DENY` or `PAUSE`, never implicit allow.

## 2. Outcome and rule semantics

`RiskDecision.outcome = ALLOW | DENY | REDUCE | PAUSE | ESCALATE`. Each decision includes `reason_codes`, `obligations`, input refs, evaluated time/TTL, rule version/hash and trace. `ALLOW` expires rapidly and is bound to one subject fingerprint; it is not transferable between intents.

Rules have ordered severity: global/provider stop > tenant/account/deployment stop > reconciliation/material data failure > mandate hard limit > market/model constraints > advisory reduce/escalate. New restrictive rules apply immediately; a rule removal never resumes a deployment automatically.

## 3. Gates and kill switch workflow

1. deployment gate verifies all static refs and current safety control.
2. pre-intent gate verifies allocation/order plan limits.
3. pre-submit gate re-reads safety fence, health and fresh risk decision.
4. intraday monitor creates signals from drawdown, stale data, provider/reconciliation failures.
5. recovery gate requires resolved evidence + fresh trust/policy/risk + authorized approval.

`ActivateSafetyControl` transaction increments target fence token, publishes control event/outbox, and schedules cancel/pause handlers. Workers check fence before every side effect. `DeactivateSafetyControl` only makes recovery review possible and cannot create RUNNING state.

## 4. API, errors and observability

Only authorized operator/risk policy routes may create scoped safety controls; user Control Center can invoke permitted pause scope. Risk evaluation is internal service API, not a client-supplied approval. Errors include `RISK_LIMIT_BREACH`, `RISK_INPUT_STALE`, `RISK_KILL_SWITCH_ACTIVE`, `INTEGRITY_RISK_FINGERPRINT_MISMATCH`, `STATE_RECOVERY_REVIEW_REQUIRED`.

SLIs: gate latency, deny/pause rate by rule, post-fence side-effect count (must be zero), signal-to-pause latency, recovery review age. Alerts: any post-fence submit; rule evaluation failure; rule-version skew; high stale input rate; global safety control active. Every safety action writes audit/evidence and user/operations notification as policy permits.

## 5. Named tests

| ID | Scenario |
|---|---|
| RSK-001 | pinned input/rule produces stable decision/fingerprint |
| RSK-002 | missing/stale input is DENY/PAUSE and adapter is never called |
| RSK-003 | hard mandate/position/market limit emits exact reason/obligation |
| RSK-004 | global/provider/tenant controls compose to most restrictive result |
| RSK-005 | activate control races with submit and fence prevents post-control side effect |
| RSK-006 | agent/router cannot construct an ALLOW or bypass final evaluator |
| RSK-007 | recovery without resolved evidence/fresh all gates is rejected |
| RSK-008 | duplicate signal/control command is idempotent and audited once semantically |
| RSK-009 | rule release with changed threshold is versioned/replayable; no silent decision drift |
| RSK-010 | load/chaos failure degrades to deny/pause, preserving trace and alert |
