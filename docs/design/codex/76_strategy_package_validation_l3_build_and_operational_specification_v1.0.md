# Strategy Package and Validation L3/L4 Build and Operational Specification v1.0

> Implements: 46 Strategy Package & Validation, FND-04. Scope: reproducible paper eligibility, never profit certification.

## 1. Aggregates and persistence

| Record | Required fields | Integrity rule |
|---|---|---|
| `strategy_draft` | id, tenant_id, author_id, CSM revision, parameter JSON, universe ref, state | editable only in DRAFT; schema validated |
| `strategy_artifact` | id, source hash, compiler version, build environment hash, artifact URI, created_at | artifact immutable/content-addressed |
| `validation_run` | id, tenant_id, artifact ref, input snapshot ref, config revision, environment/seed, state | input/config pinned before queue |
| `validation_result` | run ref, metrics, warnings, failure conditions, evidence refs, result hash | append result revision; no overwrite |
| `strategy_package` | id, tenant_id, artifact ref, validation bundle ref, mandate compatibility, lifecycle, package hash | immutable revision; unique hash/tenant |

`parameter JSON` is schema-owned/versioned and rejects unknown fields by default. Result metrics include units, annualization convention, period, source currency and estimate/final semantics; bare floating-point performance values are forbidden.

## 2. Lifecycle and asynchronous work

```text
Draft: DRAFT → COMPILED → VALIDATING → PACKAGE_DRAFT
ValidationRun: QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED
Package: PACKAGE_DRAFT → PAPER_ELIGIBLE → PAUSED | RETIRED
```

Only a successful required validation bundle can create `PAPER_ELIGIBLE`. `PAPER_ELIGIBLE` requires active mandate compatibility at decision time and an expiry/revalidation policy. Any artifact, input, compiler, validation or mandate compatibility change produces a new package revision. Retire prevents new deployments; it does not delete historical evidence.

Workers use durable operation IDs, queue retries with immutable input fingerprint, timeout/cancel signals, and output hash verification. A retry can resume only before externally visible result finalization; duplicate result writes deduplicate on run ID/result hash.

## 3. Validation policy

| Check | Required machine-readable output | Hard fail |
|---|---|---|
| Point-in-time data | source/mapping/as-of coverage and gap report | future timestamp/data lineage absence |
| Backtest | fills, fees, spread/slippage, benchmark, cashflow model | cost model absent |
| OOS/walk-forward | split boundaries, selection rule, window outcomes | test leakage/reused selection horizon |
| Robustness | parameter sensitivity, random seed/range, bias warnings | non-reproducible config |
| Stress/capacity | shock definition, liquidity/turnover/impact assumptions | required scenario missing |
| Failure conditions | observable pause/revalidate triggers | no operational invalidation criteria |

Validation policy rules are versioned. They output `PASS`, `FAIL`, or `PASS_WITH_OBLIGATIONS`; only explicit obligations may be carried to package state. A caller cannot downgrade a hard failure by editing a UI field.

## 4. Commands, events, APIs, errors

Commands: `CreateDraft`, `CompileArtifact`, `StartValidation`, `CancelValidation`, `BuildPackage`, `MarkPaperEligible`, `PausePackage`, `RetirePackage`. Events follow `strategy.<event>.v1` and include safe refs, aggregate revision, validation policy ref, trace/audit/evidence refs.

API routes use operation resources: `POST /v1/foundation/strategy-drafts`; `POST /{id}:compile`; `POST /v1/foundation/validation-runs`; `GET /validation-runs/{id}`; `POST /v1/foundation/strategy-packages`; `POST /{id}:mark-paper-eligible`; `GET /{id}/verification`. Long-running routes return `202 {operation_id,status_url}`.

Errors: `VALIDATION_CSM_SCHEMA`, `INTEGRITY_ARTIFACT_HASH_MISMATCH`, `STATE_PACKAGE_NOT_ELIGIBLE`, `POLICY_MANDATE_INCOMPATIBLE`, `INTEGRITY_FUTURE_DATA`, `VALIDATION_COST_MODEL_REQUIRED`, `DEPENDENCY_COMPUTE_UNAVAILABLE`, `RATE_VALIDATION_QUOTA`. All return safe reason, trace, remediation; artifact internals and provider credentials never appear.

## 5. Projections and operations

`VerificationView` is a read model keyed by package revision, with `as_of`, validation policy revision, run state, metrics, assumptions, limits, warnings, failure conditions, evidence links. It must display `PAPER_ELIGIBLE` as a deployment condition—not an investment recommendation, safety rating, or return guarantee.

SLIs: validation queue age, completion/error rate, reproducibility mismatch rate, stale package count, OOS/cost-policy failure rate. Alerts: queue age, compute/config version skew, result hash mismatch, package promoted without required evidence, abnormal validation cost. Rollout: fixtures → synthetic strategies → internal paper packages → limited paper cohort, behind feature flag.

## 6. Named tests

| ID | Scenario |
|---|---|
| STR-001 | same source/config/input/seed compiles and validates to same artifact/result hash |
| STR-002 | unknown parameter/type/range is rejected before compile |
| STR-003 | future data, missing source lineage, or absent cost model hard-fails validation |
| STR-004 | OOS boundary/selection leakage is detected and prevents eligibility |
| STR-005 | altered artifact/input after result cannot reuse previous package hash |
| STR-006 | mandate-forbidden universe/autonomy is incompatible despite passing backtest |
| STR-007 | duplicate StartValidation uses one operation; changed same key conflicts |
| STR-008 | worker timeout/cancel leaves auditable terminal state without partial package |
| STR-009 | retired/expired package cannot receive new deployment request |
| STR-010 | package/view/event isolation, no secret/raw dataset leakage, migration round trip |
