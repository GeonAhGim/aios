# Security audit — exchanges + OMS (task-7982)

Date: 2026-09-26. Scope: `src/exchanges/**` and `src/services/oms/**`. Profile: `quick`
(one hunter wave across six independent surfaces, one fresh verifier per candidate).
Methodology adapted from the `cloudflare/security-audit-skill` recon → hunt → refute →
schema-validate → independent re-verify → report pipeline (SKILL.md, report-schema.json,
`validate-findings.cjs`); the full multi-file write-isolation/sandbox-execution machinery
in that skill targets an autonomous multi-process runner and was not literally reproduced
here — this run used source-level static analysis plus reading existing tests, no target
code execution and no network calls, consistent with the task's constraints.

This audit deliberately hunted for classes rule-based checks miss (auth/signature/nonce
reuse, order idempotency bypass, input validation, secret handling, log exposure) rather
than re-running `review-exchange`/`review-safety`/`scripts/check_exchange_spi.py`, and
excludes the order-path findings already merged in
`docs/audits/AUDIT_2026-09-26_order_path.md` (task-7978, F1–F8).

## 0. Summary

| # | Severity | Verdict | Title |
|---|---|---|---|
| EMS-TCA-BOLA-01 | **high** | confirmed | Cross-tenant read + forged write of TCA/algo-progress via unscoped `/v1/foundation/ems` endpoints (BOLA) |
| NH-OAUTH-URL-SECRET-01 | **high** | confirmed | NH OAuth2 sends api_key/api_secret as URL query params (httpx `params=` bug) |
| KIS-WS-PLAINTEXT-01 | medium | confirmed | KIS WebSocket uses plaintext `ws://` for all channels incl. private order-notification |
| EXCHANGE-EXC-MSG-REDACTION-01 | medium | confirmed | Exchange exceptions embed raw response bodies via f-strings, bypassing structured redaction |
| COMPOSITE-ORDER-ID-DELIMITER-01 | low | confirmed | Colon-delimited composite order id has no delimiter validation (Binance/Kiwoom/OKX) |
| OMS-CANCEL-MODIFY-NO-IDEMPOTENCY-01 | low | confirmed | `cancel_order`/`modify_order` have no command-level idempotency |
| OMS-COLLISION-DIGEST-SKIP-01 | low | confirmed | `resolve_after_collision` digest-mismatch check silently skipped on legacy/new split-brain |
| ORDER-SERVICE-TAUTOLOGICAL-TENANT-01 | low | confirmed | `order_service` cancel/modify wrappers make the OMS tenant check tautological |
| KIS-WS-ACK-NOT-VALIDATED-01 | low | confirmed | KIS WS subscribe/login ack never validated (fail-open) |
| WATCHDOG-NO-REDACTION-01 | low | confirmed | `watchdog_process.py` never attaches `RedactionFilter` |
| KIS-ASCII-GUARD-REGRESSION-CHECK-01 | — | **rejected** | Historical KIS non-ASCII guard is a build-time codegen guard, not a runtime gap — not a regression |
| KIS-WS-TLS-AVAILABILITY-NEEDS-VALIDATION-01 | — | needs_validation | Whether KIS's platform offers a `wss://` variant at all |

**Confirmed: 10** (2 high, 2 medium, 6 low). **Rejected: 1. Needs validation: 1.**
Agent calls used: **17** of the 40 hard cap (6 hunters + 11 independent verifiers; every
confirmed/rejected/needs_validation record above was independently re-verified by a
second agent that did not produce the original candidate, per the methodology's
refutation-by-a-different-agent requirement).

## 1. Cross-cutting observation: much of the OMS command layer is unwired today

A recurring, independently-confirmed fact materially changed severity across several
candidates: **the new OMS `submit_order()`/`cancel_order()`/`modify_order()` application
layer has zero live production callers anywhere in this codebase snapshot** (no router,
background loop, or scheduler constructs `SubmitOrderCommand` or calls these functions
outside of tests — the legacy `Executor`/`fenced_submit` path still drives
`execution_loop/tick.py` in production). This is why
`OMS-CANCEL-MODIFY-NO-IDEMPOTENCY-01`, `OMS-COLLISION-DIGEST-SKIP-01`,
`ORDER-SERVICE-TAUTOLOGICAL-TENANT-01`, `KIS-WS-ACK-NOT-VALIDATED-01`, and
`COMPOSITE-ORDER-ID-DELIMITER-01` (Binance/OKX/Kiwoom have no factory/`SymbolRegistry`
wiring at all) are real, demonstrated code defects but score **low** rather than
medium/high: the trigger — a live caller — does not exist yet. Each is a "fix before
wiring" item, not a live incident. This should not be read as "the OMS layer is safe" —
it should be read as "the OMS command layer's real exposure has not started yet," and
each of these five items should be re-verified for reachability the moment any router,
worker, or EMS algo-start path is wired to `submit_order()`/`cancel_order()`/`modify_order()`.

By contrast, the two **high**-severity findings (`EMS-TCA-BOLA-01`, `NH-OAUTH-URL-SECRET-01`)
and the two **medium** findings are all in code that is demonstrably live today (mounted
FastAPI routers, or a routine, automatic credential-refresh call path).

## 2. task-7978 cross-check

Per task instruction, the order-path adversarial audit (task-7978, merged at
`docs/audits/AUDIT_2026-09-26_order_path.md`) was read first and its findings (F1–F8,
covering venue-cancel-confirmation wiring, Binance/OKX `get_order`, partial-fill ledger
gaps, tick/lot pre-validation, KIS/Binance symbol normalization, KIS client_order_id,
`fill_seq` hardcoding, and Bitget `find_order_by_client_id`) were excluded from hunting
scope and are not re-reported here. This audit independently re-confirmed none of F1–F8
directly (different attack-surface focus per the task brief), but two of this audit's
findings intersect with F1–F8's territory without duplicating them:

- `OMS-CANCEL-MODIFY-NO-IDEMPOTENCY-01` is adjacent to F1 (both concern the cancel path)
  but is a distinct defect: F1 is about a venue-confirmed cancel never updating internal
  state; this finding is about the *cancel command itself* having no dedup at the
  application-command layer, independent of whether the venue confirms it.
- `KIS-WS-ACK-NOT-VALIDATED-01` and `KIS-WS-PLAINTEXT-01` extend F2's "get_order missing
  for some venues breaks reconciliation" theme into KIS's WebSocket layer specifically,
  which F1–F8 did not examine (that audit scoped to REST trading_mixin.py paths).

## 3. review-exchange checklist / `check_exchange_spi.py` cross-reference

| Finding | Already covered by existing static gate? |
|---|---|
| EMS-TCA-BOLA-01 (cross-tenant BOLA) | **No** — outside `review-exchange`/`review-safety`'s path scope (`src/exchanges/**`, `src/services/oms/**`, `src/core/safety/**` etc.); no `foundation/ems` axis checklist exists in `.claude/skills/`. New gap class. |
| NH-OAUTH-URL-SECRET-01 (secret in URL) | **No** — `review-exchange` checklist item 11 covers credentials logged in plaintext, not credentials placed in a URL query string by the client itself; `check_exchange_spi.py` only checks SPI method/capability wiring, not request construction. New gap class. |
| KIS-WS-PLAINTEXT-01 (`ws://` not `wss://`) | **No** — no checklist item addresses WS transport-layer TLS. New gap class. |
| EXCHANGE-EXC-MSG-REDACTION-01 (unredactable exception messages) | **Partially** — `review-exchange` item 11 ("credentials not logged in plaintext") is adjacent but targets credential logging specifically, not the general redaction-shape mismatch between f-string messages and `RedactionFilter`'s payload-only design. |
| OMS-CANCEL-MODIFY-NO-IDEMPOTENCY-01 | **Partially** — `review-exchange` item 5 covers idempotency key *scope* for submit; nothing explicitly requires cancel/modify command-level dedup, only outbox-dispatch-level retry safety (item 2 covers outbox/inbox durability, not application-entry-point dedup). |
| OMS-COLLISION-DIGEST-SKIP-01 | **No** — no checklist item addresses cross-path (legacy vs. OMS) idempotency-scope consistency. |
| ORDER-SERVICE-TAUTOLOGICAL-TENANT-01 | **No** — no checklist item addresses tenant-check tautology from same-row double-reads. |
| COMPOSITE-ORDER-ID-DELIMITER-01 | **No** — `check_exchange_spi.py` verifies SPI method/capability presence, not composite-id encoding safety. |
| KIS-WS-ACK-NOT-VALIDATED-01 | **Yes, in spirit** — `review-exchange` item 1 ("UNKNOWN response is reconfirmed via clientOid, not assumed failed") and item 10 ("scheduled reconfirmation commands aren't missing from the wiring") are the closest existing checklist coverage for this class; this specific WS-ack instance was missed because the checklist's examples are REST-oriented. |
| WATCHDOG-NO-REDACTION-01 | **No** — no checklist item covers logging configuration parity across separate OS processes. |
| KIS-ASCII-GUARD-REGRESSION-CHECK-01 (rejected) | **N/A** — resolved as not a live gap; the actual guard (`scripts/kis_rendering.py`) is unrelated to `review-exchange`'s item 9, which documents the same historical incident. |

**Net: 9 of 10 confirmed findings are in a gap class the existing static
checklists/gates do not cover at all; 1 (`KIS-WS-ACK-NOT-VALIDATED-01`) is the same
*class* of issue the `review-exchange` checklist already asks reviewers to look for
(UNKNOWN/ack reconfirmation), just not this specific WS instance.**

## 4. Findings detail

Full structured records — trace, evidence, reproduction, remediation, severity
reasoning — are in `findings.json` (validated against `report-schema.json` via
`validate-findings.cjs`, `PASS: 12 findings valid`). Each confirmed/rejected/
needs_validation record was independently re-verified by a second agent that did not
produce the original candidate; several verifiers *downgraded* the original finder's
severity after establishing non-reachability (documented per-record in `findings.json`'s
`severity` fields and this report's §1).

See `NEEDS-VALIDATION.md` for the one blocked item.
