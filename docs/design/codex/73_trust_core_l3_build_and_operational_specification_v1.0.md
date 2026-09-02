# Trust Core L3/L4 Build and Operational Specification v1.0

> Implements: 43 Trust Core, FND-01 from 71.
> Owner: Identity & Tenant Trust bounded context. Status: build-ready for PAPER-only Foundation.

## 1. Scope and dependencies

This capability issues trusted tenant context and evaluates consent/suitability freshness. It does not authenticate passwords, implement KYC, grant investment advice, or retain raw identity-provider tokens. Inputs: authenticated gateway identity, approved disclosure catalog, organization membership source. Consumers: Mandate, Connection, Strategy Package, Paper Control, Risk Gate.

```text
Gateway authentication → TenantContext issuer → command/query handlers
Disclosure catalog ──────→ Consent evaluator ──┐
Suitability answers ─────→ Suitability evaluator ├→ TrustStatusView
Membership/role source ──→ Authorization guard ─┘
```

## 2. Ubiquitous language and records

| Term | Definition |
|---|---|
| Subject | AIOS internal person/service identity; never a provider credential |
| Tenant | isolation boundary for personal account, household, or organization |
| Membership | subject's tenant role binding with revision and lifecycle |
| Disclosure | immutable published text/version for a declared purpose |
| Consent | subject's affirmative acceptance or revocation of one disclosure purpose/version |
| Suitability profile | user answers and deterministic tier result; not a recommendation |
| Fresh | record exists, active, unexpired, and required revision/rule matches |

### 2.1 Data dictionary

| Table/record | Field | Type | Null | Classification / rule |
|---|---|---|---|---|
| `trust_subject` | `id` | UUID | no | internal identifier; PK |
|  | `status` | enum ACTIVE/SUSPENDED/DELETED | no | state guarded |
|  | `created_at` | timestamptz | no | UTC |
| `tenant_membership` | `id`, `tenant_id`, `subject_id` | UUID | no | tenant-confidential; unique active `(tenant_id, subject_id)` |
|  | `role` | enum OWNER/ADMIN/MEMBER/AUDITOR/SERVICE | no | role is server-controlled |
|  | `state` | enum ACTIVE/SUSPENDED/REVOKED | no | no hard delete |
|  | `revision` | integer | no | optimistic concurrency |
| `disclosure` | `id`, `purpose`, `revision` | UUID/string/int | no | public/regulated; unique `(purpose, revision)` |
|  | `content_hash`, `published_at`, `retired_at` | text/timestamptz | no/yes | body lives in document store ref |
| `consent_record` | `id`, `tenant_id`, `subject_id`, `purpose` | UUID/string | no | restricted; unique active purpose per subject/tenant |
|  | `disclosure_id`, `state`, `accepted_at`, `revoked_at`, `expires_at` | UUID/enum/time | varies | append revision, never mutate accepted evidence |
| `suitability_profile` | `id`, `tenant_id`, `subject_id`, `revision` | UUID/int | no | highly restricted PII |
|  | `answers_ciphertext_ref` | opaque ref | no | raw answers encrypted outside event payload |
|  | `tier`, `rule_version`, `state`, `expires_at` | enum/string/enum/time | no | deterministic result |

All tenant-scoped tables have row-level repository predicate `tenant_id = context.tenant_id`; no caller-supplied tenant filter is trusted.

## 3. State machines

### 3.1 Membership

| From → To | Actor | Guard | Side effects |
|---|---|---|---|
| none → ACTIVE | tenant owner/system provisioning | trusted subject, role policy | `MEMBERSHIP_GRANTED` audit |
| ACTIVE → SUSPENDED | admin/risk | not self-only approval | invalidate sensitive sessions |
| ACTIVE/SUSPENDED → REVOKED | owner/admin | cannot remove last owner | cancel future delegated workflows |
| REVOKED → ACTIVE | owner with MFA | regrant policy | new revision, audit |

### 3.2 Consent and suitability

`Consent`: `NONE → ACTIVE → REVOKED`; a new disclosure revision requires a new ACTIVE record and never overwrites earlier evidence. `Suitability`: `DRAFT → ACTIVE → EXPIRED | SUPERSEDED → RETIRED`. Any material profile change creates a new revision. `ACTIVE` becomes unusable after `expires_at` even if background expiry processing is delayed.

## 4. Commands, events, reason codes

| Command | Event | Success result | Primary errors |
|---|---|---|---|
| `GrantMembership` | `trust.membership_granted.v1` | membership ref/revision | `AUTH_MFA_REQUIRED`, `AUTH_ROLE_FORBIDDEN`, `STATE_LAST_OWNER` |
| `SuspendMembership` | `trust.membership_suspended.v1` | new state | `AUTH_ROLE_FORBIDDEN`, `STATE_INVALID_TRANSITION` |
| `AcceptDisclosure` | `trust.consent_accepted.v1` | consent ref/expiry | `VALIDATION_DISCLOSURE_RETIRED`, `STATE_DUPLICATE_COMMAND` |
| `RevokeConsent` | `trust.consent_revoked.v1` | revocation ref | `AUTH_TENANT_MISMATCH` |
| `SubmitSuitability` | `trust.suitability_submitted.v1` | profile ref/tier/expiry | `VALIDATION_ANSWER_INCOMPLETE`, `POLICY_CONSENT_REQUIRED` |
| `EvaluateTrustFreshness` | none (query) | decision/reason/obligation | `POLICY_CONSENT_REVOKED`, `POLICY_SUITABILITY_EXPIRED` |

Event envelope uses the 72 standard plus `aggregate_type`, `aggregate_id`, `aggregate_revision`, `event_type`, `payload_ref`, `classification`. Payload has references and safe facts only; answers and disclosure body are not copied into events.

## 5. API contract

All routes require gateway-authenticated `TenantContext`; `tenant_id` is path-independent and never accepted as a body authority.

| Route | Request | Response | Auth |
|---|---|---|---|
| `GET /v1/trust/status` | none | active membership, consent/suitability freshness, next actions, as-of | member |
| `POST /v1/trust/consents` | purpose, disclosure_revision, idempotency key | consent ref/state/expiry | MFA verified |
| `POST /v1/trust/consents/{id}:revoke` | idempotency key | revoked state | owner subject or authorized admin |
| `POST /v1/trust/suitability-profiles` | typed answers, disclosure ref, idempotency key | profile ref/tier/expiry/reasons | MFA verified |
| `GET /v1/trust/memberships` | cursor, limit (1..100) | cursor page, `as_of` | admin/auditor |

HTTP mapping: malformed type `422 VALIDATION_*`; duplicate idempotency with different digest `409 INTEGRITY_IDEMPOTENCY_CONFLICT`; stale `If-Match` revision `409 STATE_STALE_REVISION`; expired consent `403 POLICY_CONSENT_EXPIRED`; MFA missing `403 AUTH_MFA_REQUIRED`; dependency unavailable `503 DEPENDENCY_IDP_UNAVAILABLE` with retry hint.

## 6. Deterministic rules

1. A command requires an ACTIVE membership for its tenant and purpose-specific role.
2. Sensitive commands require auth level `MFA_VERIFIED` issued within configured step-up window.
3. Required consent matches both purpose and active disclosure revision; a previous revision is insufficient if policy marks reacceptance required.
4. Required suitability is fresh only if state ACTIVE, `now < expires_at`, required answers complete, and rule version is supported.
5. Last ACTIVE OWNER cannot be suspended/revoked in the same transaction without a replacement owner.
6. Same `idempotency_key` + command fingerprint returns prior result; a different fingerprint is rejected.

## 7. Concurrency, persistence and migration

- Use transactionally persisted aggregate revision plus unique active membership/consent constraints.
- Write domain event through transactional outbox; projection worker is at-least-once and deduplicates by event ID.
- List/read models may lag. `TrustStatusView` carries `as_of` and `projection_lag_ms`; command handlers always query write model.
- Migration order: create enum/check constraints → tables/indexes → repositories behind disabled flag → backfill only verified legacy data → enable write routes. Rollback disables routes/flag; it does not drop accepted consent evidence.

## 8. Security, privacy and retention

Suitability answers use encrypted external payload reference; only derived tier/reason codes reach projections. Access to raw answers is limited to subject and explicitly authorized compliance role, audited at read time. Consent evidence retention follows applicable policy/legal hold; deletion creates a deletion-request workflow and does not silently destroy audit chain. Logs redact opaque refs where correlation is unnecessary.

Rate limits: status 120/min/subject; mutation 10/min/subject; membership admin 30/min/tenant. All mutations require request size limits, schema validation, CSRF/origin controls for browser session paths, and trace propagation.

## 9. Test catalogue

| ID | Test |
|---|---|
| TRU-001 | subject accepts current disclosure and receives ACTIVE consent with audit/event refs |
| TRU-002 | retired/wrong revision disclosure is rejected without a consent record |
| TRU-003 | revoke immediately causes freshness query and protected command precondition to deny |
| TRU-004 | expired profile denies deployment even if expiry worker has not run |
| TRU-005 | same idempotency key/fingerprint returns identical result; changed body returns conflict |
| TRU-006 | cross-tenant ID in route/body/query cannot read or mutate record |
| TRU-007 | last owner suspension/revocation is rejected atomically |
| TRU-008 | two concurrent membership grants yield one active unique binding/revision-safe outcome |
| TRU-009 | raw suitability answer, session token, secret-like value never appears in event/log/API projection |
| TRU-010 | outbox retry produces one projection/audit semantic event |
| TRU-011 | migration upgrade/backfill/rollback preserves valid consent evidence and isolation |
| TRU-012 | load test at rate limit returns stable 429 with no partial mutation |

## 10. Operations and rollout

SLIs: trust command success rate, authorization-deny rate by reason, projection lag, outbox backlog, consent/profile freshness coverage, cross-tenant access denials. Initial SLOs: 99.9% successful reads excluding caller denials; 99.5% mutation completion; p95 status read <300ms excluding IdP; projection lag <60s.

Alerts: outbox age >5 minutes; sharp rise in `AUTH_TENANT_MISMATCH`; freshness coverage decline; audit append failure; encryption/reference resolution failure. Runbook actions are pause protected deployments if trust decision service cannot safely evaluate, investigate trace, and never bypass with UI/admin direct DB edit.

Rollout: disabled feature flag → internal synthetic tenant → test tenant → limited paper cohort. Exit requires TRU-001~012 green, security review, migration rehearsal, dashboard/alert verification, owner sign-off, and rollback drill.
