# Paper Execution and Control L3/L4 Build and Operational Specification v1.0

> Implements: 47 Paper Execution & Control Center, FND-07. Scope is paper execution only; no LIVE switch exists in this context.

## 1. Deployment schema and invariant

| Record | Required fields | Constraint |
|---|---|---|
| `paper_deployment` | id, tenant_id, connection ref, package ref/revision/hash, mandate ref/revision, policy/risk refs, adapter provenance, state, limits snapshot | `mode = PAPER` database check; refs immutable after READY |
| `deployment_command` | command id, deployment id, idempotency key/digest, actor, requested state, outcome | unique `(deployment_id,idempotency_key)` |
| `paper_order_intent` | deployment ref, sequence, target/order plan, risk decision ref, expiry, state | no provider credential field |
| `deployment_health` | deployment id, data/provider/risk/reconciliation state, evaluated_at | latest projection + history |

Adapter provenance is a structured record: adapter type/version, `credential_class=PAPER`, endpoint classification, egress policy ref, provider sandbox account ref. It is validated at prepare and again before every submit. A boolean `is_paper` alone is insufficient.

## 2. State machine and priority

```text
REQUESTED → PREPARING → READY → RUNNING → PAUSED → RUNNING
                         │          └────────────→ STOPPED
                         └────────→ FAILED
READY/RUNNING/PAUSED → DEGRADED → STOPPED | RECOVERY_REVIEW
```

`STOP` and risk/emergency `PAUSE` outrank `START/RESUME`; command serialization is per deployment. `RUNNING` requires current trust, active mandate, paper-eligible non-expired package, fresh policy/risk decision, healthy connection/data/reconciliation, and verified paper provenance. `RECOVERY_REVIEW` cannot transition to RUNNING automatically.

## 3. Command handler behavior

| Command | Preconditions | Atomic effects |
|---|---|---|
| Request | package/mandate/trust refs valid | create REQUESTED + audit/outbox |
| Prepare | paper provenance and initial risk pass | pin refs/limits → READY |
| Start | READY + fresh all gates | RUNNING, schedule tick workflow |
| Pause | owner/risk/incident | fence token increments, cancel future ticks/intents |
| Stop | owner/risk | terminal stop, cancel work, enqueue reconciliation |
| Resume | PAUSED only + complete reevaluation | RUNNING or deny |

Every tick receives deployment ID, expected revision, fence token and mode. It verifies current state/fence immediately before intent and immediately before adapter call. Superseded fence token means no-op/audit, never late order submission.

## 4. Interface and safety boundary

The paper adapter port exposes `submit_paper_intent`, `cancel_paper_order`, `fetch_paper_state`; its input includes `PaperExecutionContext` with provenance proof and never a generic/live `ExchangeAdapter`. Build/dependency injection rules forbid loading live endpoint/credential provider in `foundation.paper_control` process configuration.

API: request/start/pause/stop/resume/status/timeline routes, all using idempotency keys and command status read. Pause/stop return accepted operation with final state/status URL, never falsely report completed cancellation before workers converge.

Errors: `INTEGRITY_PAPER_PROVENANCE_MISMATCH`, `POLICY_DEPLOYMENT_NOT_ALLOWED`, `RISK_DEPLOYMENT_PAUSED`, `STATE_DEPLOYMENT_FENCED`, `INTEGRITY_RECONCILIATION_REQUIRED`, `DEPENDENCY_PAPER_PROVIDER_UNAVAILABLE`.

## 5. UX, telemetry and recovery

Control Center displays mode=`PAPER` prominently, package/mandate/policy/risk revisions, simulated capital, active limits, last tick/order/fill, data/provider/reconciliation as-of, fence/stop reason, and command progress. It never calls a provider directly.

SLIs: start/pause/stop convergence time, provenance denial count, fenced tick count, order intent→paper fill latency, tick failure, recovery review age. Alerts: any live endpoint/credential classification seen, stop convergence breach, post-pause intent, unhealthy RUNNING deployment, audit append failure. Rollback disables starts and schedules; pause/stop remains available.

## 6. Named tests

| ID | Scenario |
|---|---|
| PAP-001 | valid paper refs/provenance reaches READY/RUNNING and emits audited transitions |
| PAP-002 | live endpoint, live credential, unknown adapter, or missing egress proof rejects before adapter call |
| PAP-003 | simultaneous start/stop results in STOPPED and no post-stop intent/submission |
| PAP-004 | pause fence invalidates already queued tick between planning and submit |
| PAP-005 | stale consent/mandate/package/risk/reconciliation denies start/resume |
| PAP-006 | duplicate command is idempotent; digest mismatch conflicts |
| PAP-007 | provider timeout produces DEGRADED/retry policy and never switches modes |
| PAP-008 | recovery needs fresh policy/risk/reconciliation decision; no auto-resume |
| PAP-009 | API/UI projection cannot forge state or direct-call adapter |
| PAP-010 | trace/audit includes all pinned refs and contains no credential/secret |
