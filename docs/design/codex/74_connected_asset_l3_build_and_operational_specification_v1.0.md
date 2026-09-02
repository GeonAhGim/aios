# Connected Asset L3/L4 Build and Operational Specification v1.0

> Implements: 44 Connected Asset, FND-05. Scope is read-only connection only.

## 1. Data model and capabilities

| Record | Required fields | Database constraints |
|---|---|---|
| `account_connection` | id, tenant_id, owner_subject_id, provider_code, opaque_account_ref, state, capability_profile, revision, created_at | unique `(tenant_id, provider_code, opaque_account_ref)`; tenant index; no secret column |
| `credential_binding` | id, connection_id, vault_secret_ref, scope_fingerprint, credential_class, expires_at, rotation_state | credential class must be `READONLY`; no raw value |
| `connection_consent` | connection_id, consent_ref, data_purposes, expires_at | active consent must match connection purpose |
| `account_snapshot` | id, connection_id, captured_at, provider_as_of, freshness, currency, source_evidence_ref | unique `(connection_id, provider_as_of, source_evidence_ref)` |
| `connection_health` | connection_id, evaluated_at, state, error_code, retry_after, provider_trace_ref | one latest projection + append history |

P0 capability profile is a closed enum set: `READ_BALANCE`, `READ_POSITION`, `READ_ACTIVITY`. Any `TRADE_*`, `WITHDRAW`, `TRANSFER`, `SIGN_*`, unknown scope, or missing scope fingerprint is a hard rejection.

## 2. State machine and commands

| From → To | Command/actor | Guards | Side effects |
|---|---|---|---|
| none → PENDING_CONSENT | BeginConnection / owner | MFA, provider allowlist | audit, expiry timer |
| PENDING_CONSENT → CONNECTING | ConfirmConnection / owner | active data consent, anti-CSRF/OAuth verifier | short-lived handshake |
| CONNECTING → ACTIVE_READONLY | VerifyScope / service | official provider, exact readonly scope, vault write succeeds | binding + sync schedule |
| ACTIVE_READONLY → DEGRADED | HealthCheck / service | auth/freshness/provider failure | alert; block dependent fresh-data actions |
| ACTIVE/DEGRADED → REVOKED | RevokeConnection / owner/admin | ownership/role | vault revoke, cancel workflows |
| ACTIVE/DEGRADED → DISCONNECTED | ProviderDisconnect / service | verified signal | no new sync; alert |

Commands use 72 envelope and reason codes: `VALIDATION_PROVIDER_UNSUPPORTED`, `POLICY_CONNECTION_CONSENT_REQUIRED`, `AUTH_MFA_REQUIRED`, `INTEGRITY_FORBIDDEN_SCOPE`, `DEPENDENCY_PROVIDER_UNAVAILABLE`, `STATE_CONNECTION_REVOKED`.

## 3. Provider port and workflow

```python
class ReadonlyAccountProvider(Protocol):
    def verify_readonly_scope(self, lease: SecretLease) -> ScopeProof: ...
    def fetch_snapshot(self, account_ref: OpaqueRef, as_of: Instant) -> ProviderSnapshot: ...
```

No method for orders, transfers, withdrawal, signing, or raw credential retrieval belongs to this port. Sync flow: acquire short-lived vault lease → verify connection state/consent → call allowlisted provider endpoint → validate payload size/schema/time → normalize instrument/currency → persist source evidence + snapshot/outbox → release lease. Retries use exponential backoff with jitter; only idempotent fetch is retried.

## 4. API/read semantics

`POST /v1/foundation/connections` begins a connection; `POST /{id}:confirm` completes verified browser/provider flow; `POST /{id}:revoke` revokes; `GET /v1/foundation/connections` returns masked provider/account label, allowed capability, state, last successful sync, freshness, and safe remediation. Account balances/positions return `as_of`, provider timestamp, conversion/mapping version, and `ESTIMATED` vs `PROVIDER_CONFIRMED` semantics.

The API never returns provider access token, API key, secret fingerprint beyond non-sensitive display suffix, full provider error body, or an account reference usable by another tenant.

## 5. Integrity, privacy and operations

Connection commands require MFA and active `ACCOUNT_READ` consent. Connection revocation has priority over queued sync: workers re-read write state immediately before secret lease and before persistence. Raw provider payload retention is classified/restricted; normalized snapshot lineage retains hash/source/time. Alert on scope drift, repeated failures, unexpected provider country/account mismatch, stale freshness, vault revoke failure.

SLIs: verified-connection success, sync success, p95 sync latency, stale connection ratio, revoke completion time, scope-denial count. Initial objectives: 99% successful scheduled fetch excluding provider outages; revoke blocks new lease within 60 seconds; stale threshold is provider/asset-class policy, never a UI-only timer.

## 6. Test catalogue

| ID | Scenario |
|---|---|
| CON-001 | exact readonly OAuth/API scope activates connection and persists opaque refs only |
| CON-002 | trade/withdraw/unknown scope is rejected before vault binding |
| CON-003 | revoked consent or connection prevents lease, fetch, snapshot, and retry |
| CON-004 | concurrent revoke and sync cannot persist a post-revocation snapshot |
| CON-005 | provider timeout/rate-limit yields DEGRADED, retry policy, no credential/event leak |
| CON-006 | malformed/stale/duplicate provider response is classified and does not overwrite history |
| CON-007 | tenant A cannot access, label, revoke, or reference tenant B connection |
| CON-008 | vault lease expires/rotation fails safely and records audited remediation |
| CON-009 | adapter has no callable order/transfer/sign method by contract test |
| CON-010 | feature-flag rollback cancels schedules without deleting prior evidence |

## 7. Rollout gate

Use fake provider → isolated sandbox provider → internal read-only account → limited paper cohort. Before any real provider activation: security/threat review, consent UX test, vault/revoke rehearsal, provider failure drill, privacy/retention review, dashboard/alerts, CON-001~010 green. This specification does not authorize trading integration.
