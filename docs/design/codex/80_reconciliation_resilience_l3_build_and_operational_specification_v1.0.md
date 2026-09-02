# Reconciliation and Resilience L3/L4 Build and Operational Specification v1.0

> Implements: 50 Reconciliation & Resilience, FND-08. Principle: unknown state blocks new execution.

## 1. Data model and classification

| Record | Required fields | Rule |
|---|---|---|
| `reconciliation_run` | id, tenant/account/deployment ref, scope, internal snapshot ref, provider snapshot ref, rule version, state | input snapshots pinned |
| `reconciliation_item` | run ref, entity type/key, internal value, provider value/ref, materiality, classification, status | immutable observation; correction separate |
| `reconciliation_state` | target ref, aggregate status, last healthy/checked time, blocking reason, revision | latest projection + history |
| `recovery_case` | target ref, incident/evidence refs, owner, state, required approvals | no auto-resume transition |

Classifications: `HEALTHY`, `PENDING`, `MINOR_DIFFERENCE`, `MATERIAL_MISMATCH`, `PROVIDER_UNAVAILABLE`, `INVESTIGATING`, `RESOLVED`. Materiality is typed/asset-aware policy, not UI configuration. `MATERIAL_MISMATCH` and `PROVIDER_UNAVAILABLE` create a SafetyControl request and block new submissions.

## 2. Workflow and concurrency

Scheduled/event reconciliation claims target via lease/fence, fetches pinned provider state with read/paper port, normalizes through mapping version, compares order/fill/position/cash, persists run/items/outbox atomically, then projects safety/notification. Multiple workers deduplicate by target/window/input hash. Provider timeout creates `PROVIDER_UNAVAILABLE`; it never assumes zero balance/fill.

Safe automated actions are only projection rebuild, read retry, idempotent fetch, or clearly defined ledger correction with evidence. Re-submit, auto-cancel without known provider state, balance overwrite, and resume are disallowed. `RESOLVED` opens recovery review; fresh trust/policy/risk approval is required to run.

## 3. API/errors/operations

Users see last checked/as-of, scope, safe difference category, execution impact, and next action; raw provider responses or foreign data are hidden. Operators can request recheck/investigate/resolve with reason/evidence and role separation. Errors: `INTEGRITY_RECONCILIATION_MISMATCH`, `DEPENDENCY_PROVIDER_SNAPSHOT_UNAVAILABLE`, `STATE_RECOVERY_APPROVAL_REQUIRED`, `INTEGRITY_SNAPSHOT_MAPPING_MISMATCH`.

SLIs: run completion, lag since last healthy reconciliation, mismatch rate, signal-to-pause time, recovery-case age, duplicate-run rate. Alerts: material mismatch, unavailable provider beyond threshold, unreconciled RUNNING deployment, backlog, any resume without closed case. DR drill verifies restored state is reconciled before enabling scheduling.

## 4. Named tests

| ID | Scenario |
|---|---|
| REC-001 | matching order/fill/position/cash creates HEALTHY evidence/projection |
| REC-002 | material fill/balance mismatch pauses target before new submission |
| REC-003 | provider timeout/partial payload marks unavailable, no zero-state assumption |
| REC-004 | concurrent scheduled/manual runs dedupe and preserve one semantic result |
| REC-005 | mapping/currency/corporate-action version mismatch is visible and blocks unsafe compare |
| REC-006 | safe retry does not duplicate order, fill, event, or notification |
| REC-007 | resolve alone cannot resume; fresh trust/policy/risk/recovery approval required |
| REC-008 | tenant/operator isolation and raw provider payload redaction hold |
| REC-009 | backup restore/drill detects missing audit/snapshot lineage and remains paused |
| REC-010 | load/provider chaos maintains fences, alerting, bounded retries, and trace correlation |
