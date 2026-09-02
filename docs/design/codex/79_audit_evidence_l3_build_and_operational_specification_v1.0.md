# Audit, Evidence and Explainability L3/L4 Build and Operational Specification v1.0

> Implements: 49 Audit & Evidence, FND-03. Scope: tamper-evident operational history and safe explanation references.

## 1. Records and integrity

| Record | Required fields | Rule |
|---|---|---|
| `audit_event` | event_id, tenant_id, aggregate type/id/revision, action/outcome, actor, trace, occurred_at, payload hash/ref, previous hash | append-only; per-aggregate sequence unique |
| `evidence_object` | id, tenant_id, type, content hash, source URI/ref, as-of, classification, retention, access policy | immutable content/version |
| `evidence_edge` | from/to object refs, relation type, created_at | graph is additive; corrections are new edges |
| `explanation_record` | subject ref, template/version, evidence refs, as-of, safe text, state | no raw chain-of-thought/secret |

Tenant audit streams use hash chaining and periodic signed checkpoint. This detects mutation; it does not replace database backup, access control, or legal records policy.

## 2. Event/outbox behavior

High-risk command transaction persists aggregate change, audit event, evidence relation and outbox message together. Consumers are at-least-once; they deduplicate event ID and never change original event meaning. Corrections use `SUPERSEDES` relation and correction event with reason; deletion requests add controlled tombstone/access restriction under retention policy.

Standard event fields are per 72 plus `classification`, `correlation_id`, `causation_id`, `payload_schema`, `payload_hash`, `evidence_refs`. Payload schema uses allowlisted fields; serializer rejects key names matching secret/token/password/private-key patterns and oversized/raw provider blobs.

## 3. Read/API semantics

Timeline query is tenant/authorization scoped with opaque cursor, time range, aggregate/action filter and maximum bounded page. It returns event summary, actor display policy, outcome, safe reasons, trace, evidence links, `as_of`; it never returns hidden internal prompt, raw answer, secret, unrestricted foreign reference.

`ExplainDecision` derives response from authoritative decision/evidence records and uses template revision. If evidence is missing/stale, API returns an explicit unavailable/limited explanation, not invented rationale. Export is asynchronous, access logged, retention-filtered, and content-hashed.

## 4. Retention, operations and errors

Classifications: PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, SECRET_REFERENCE. `SECRET_REFERENCE` holds opaque ID only. Retention policy is data type/jurisdiction/hold aware; legal hold wins over ordinary deletion, with outcome explained/audited. Errors: `AUTH_EVIDENCE_ACCESS_DENIED`, `INTEGRITY_AUDIT_CHAIN_BROKEN`, `INTEGRITY_EVIDENCE_HASH_MISMATCH`, `STATE_LEGAL_HOLD_ACTIVE`, `RATE_EXPORT_QUOTA`.

SLIs: audit append success, outbox age, chain verification success, explanation evidence coverage, export completion, access-denied anomalies. Alerts: failed append, checkpoint mismatch, evidence hash mismatch, sudden restricted export volume, outbox lag. A failed audit append makes high-risk command fail closed.

## 5. Named tests

| ID | Scenario |
|---|---|
| AUD-001 | high-risk command transaction creates aggregate/audit/outbox atomically |
| AUD-002 | event mutation/delete/update attempt is blocked; correction uses new event/edge |
| AUD-003 | chain/checkpoint verification detects altered/missing sequence |
| AUD-004 | secret/token/raw provider payload serializer rejection and log scan |
| AUD-005 | tenant/access role cannot infer foreign event/evidence existence |
| AUD-006 | at-least-once consumer duplicates do not duplicate projection semantics |
| AUD-007 | explanation with missing evidence reports limitation, never hallucinated justification |
| AUD-008 | retention/deletion/legal hold conflict produces auditable controlled outcome |
| AUD-009 | paginated/export view preserves filtering, hash, authorization and as-of |
| AUD-010 | append outage fails high-risk command closed and triggers alert/runbook |
