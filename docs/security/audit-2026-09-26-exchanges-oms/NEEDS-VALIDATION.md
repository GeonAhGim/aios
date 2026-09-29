# Needs validation — exchanges + OMS security audit (task-7982)

One record from `findings.json` is blocked on an external fact this audit could not
resolve from source, cached design docs, or this session's git history alone (no network
access to third-party documentation was available or used).

## KIS-WS-TLS-AVAILABILITY-NEEDS-VALIDATION-01

**Title:** Whether KIS's platform offers a TLS (`wss://`) variant of its realtime
WebSocket endpoint.

**Why it's blocked:** `KIS-WS-PLAINTEXT-01` (in `findings.json`, medium severity,
confirmed) establishes that this codebase's KIS adapter connects over plaintext `ws://`
for every channel, including the private order-notification feed that carries a
bearer-style `approval_key` and the AES-256-CBC key/iv protecting real account order-fill
data. The direct remediation — switching to `wss://` — depends entirely on one external
fact this audit cannot establish: **does KIS's official API even offer a TLS-protected
variant of this endpoint?** Every other venue adapter in this repo (Bitget, NH, Kiwoom)
uses `wss://`; only KIS's `WS_REAL_URL`/`WS_PAPER_URL` (`src/exchanges/kis/
websocket_mixin.py:110-111`) are hardcoded to `ws://ops.koreainvestment.com:21000`/`:31000`,
sourced from KIS's official example code per this module's own research trail. A
repo-wide grep of both code and `docs/design/02d_kis_api_full_spec_v1.md` found no
`wss://` reference anywhere in this codebase's KIS research history — but that only
establishes the absence of evidence *for* a TLS variant within this repository, not the
absence of one on KIS's live platform.

**Blocker:** Requires checking KIS's live, authoritative API documentation (or contacting
KIS support/account management) to determine whether a `wss://` variant of the
`ops.koreainvestment.com` realtime WS endpoint exists. This audit had no network access
to KIS's external documentation and could not resolve this from the repository's shallow
git clone or cached design docs alone.

**Validation plan:**
- **Local:** Search this repository's full (non-shallow) git history and any archived
  KIS API reference material for a TLS/`wss://` port or endpoint mentioned alongside the
  documented `ws://` ports 21000/31000.
- **Deployment/operational:** Have a team member with access to KIS's current official
  developer portal confirm whether a `wss://` endpoint is offered for the realtime WS
  feed.
  - **If yes:** migrate `WS_REAL_URL`/`WS_PAPER_URL` to the TLS variant — this directly
    closes `KIS-WS-PLAINTEXT-01`.
  - **If no (a genuine platform constraint):** document the residual risk explicitly in
    `docs/design/02d_kis_api_full_spec_v1.md` and require an operational mitigation
    (e.g. tunneling the KIS WS connection through a VPN/stunnel) rather than leaving the
    exposure undocumented, since a code-level fix isn't available in that case.

No other candidates from this audit's hunting pass required a `needs_validation` verdict
after independent re-verification — every other candidate reached a `confirmed` or
`rejected` verdict on source evidence alone (see `findings.json` and `REPORT.md`).
