# Implementation Specification Depth Standard v1.0

> 상태: Mandatory authoring standard for AIOS implementation documents.
> 판단: 42~71번은 L1/L2 구조·작업패키지다. 구현 착수 전 각 FND capability는 아래 L3/L4 명세를 가져야 한다.

## 1. 문서 성숙도

| Level | 목적 | 충분 조건 |
|---|---|---|
| L0 Vision | 제품 방향 | 사용자 가치·비목표 |
| L1 Architecture | 경계와 책임 | bounded context, ownership, dependency |
| L2 Work Package | 구현 순서 | PR slice, DoD, high-level contracts |
| L3 Build Specification | 한 기능을 안전하게 구현 | data/API/event/error/state/test/rollout 상세 |
| L4 Operational Specification | 실제 운영·감사 | SLO, alert, runbook, DR, access review, evidence |
| L5 Production Evidence | 출시 판단 | CI, security, load, rehearsal, owner sign-off |

L3 없이 코드 작성 금지, L4 없이 고위험 capability의 사용자 노출 금지, L5 없이 governed LIVE 승격 금지다.

## 2. 각 capability L3 필수 목차

1. Scope, owner, non-goals, assumptions, dependency graph
2. Ubiquitous language와 aggregate/entity/value-object 정의
3. field-level data dictionary: type, nullability, classification, source, retention, validation
4. relational/event persistence schema, unique/index/foreign-key/tenant isolation, migration/rollback
5. state machine: state, transition, actor, guard, side effect, idempotency behavior
6. command/query/event contracts: JSON schema, semantic version, compatibility/deprecation rule
7. API surface: auth, request/response, pagination/filter/sort, HTTP/error mapping, idempotency
8. deterministic domain rules와 reason/error code taxonomy
9. authorization, consent, privacy, secret, rate-limit, egress, audit requirements
10. workflow/concurrency/retry/timeout/outbox/dead-letter/compensation behavior
11. projections/cache consistency/as-of semantics and UI copy/state mapping
12. test catalogue: unit, contract, migration, integration, adversarial, property, load
13. metrics/log/trace/audit schema, SLI/SLO, alerts, dashboards, runbook
14. rollout, feature flag, backfill, migration, rollback, ownership/approval checklist

## 3. cross-cutting standard contracts

Every high-risk command envelope:

```json
{
  "command_id": "uuid", "idempotency_key": "string(1..128)",
  "tenant_id": "uuid", "actor_subject_id": "uuid", "trace_id": "uuid",
  "occurred_at": "RFC3339 UTC", "schema_version": "v1"
}
```

Every persisted high-risk record has `tenant_id`, `record_id`, `revision`, `created_at`, `created_by`, `updated_at` when mutable, `classification`, and audit correlation. Timestamps are UTC; UI formatting never becomes source of truth.

## 4. standard error taxonomy

| Family | HTTP | Retry | Example |
|---|---|---|---|
| `AUTH_*` | 401/403 | no | insufficient MFA, tenant mismatch |
| `VALIDATION_*` | 400/422 | no | malformed input, missing field |
| `STATE_*` | 409 | conditional | invalid transition, stale revision |
| `POLICY_*` | 403/409 | after user action | consent revoked, policy denied |
| `RISK_*` | 409/423 | only after condition changes | limit breached, kill switch |
| `DEPENDENCY_*` | 502/503 | safe/idempotent only | provider unavailable |
| `INTEGRITY_*` | 409/500 | operator review | hash mismatch, reconciliation mismatch |
| `RATE_*` | 429 | after retry-after | quota exceeded |

Error responses expose stable code, safe message, trace ID, remediation hint; never internal stack, secret, raw provider payload, or another tenant reference.

## 5. standard test depth

Each L3 document must enumerate named test cases, not generic “add tests.” Minimum: happy path; field boundary/property; state transition; duplicate/idempotent command; stale revision; authorization/tenant isolation; consent/policy; adapter failure; audit completeness; concurrency; migration; API contract compatibility. High-risk contexts add secret scanning, egress denial, replay, chaos/recovery and load tests.

## 6. writing sequence

Create L3/L4 documents in FND-01~10 order. Do not copy a generic template without deciding field types, error codes, transition guards, retention, and test fixtures for the actual context. A document is complete only after unresolved choices are either decided or explicitly assigned to an owner/ADR gate.
