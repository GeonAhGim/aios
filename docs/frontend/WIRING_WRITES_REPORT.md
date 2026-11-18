# Write/param wiring report (generated)

> `scripts/uat/write_probe.py` against the loopback UAT API as a throw-away user.
> Empty JSON body and dummy UUIDs. 400/401/403/404/409/422 are healthy answers.

| Bucket | Count |
|---|---|
| accepted_empty | 1 |
| missing | 32 |
| needs_input | 66 |
| needs_role | 9 |
| server_error | 2 |

## Server errors and stubs (need attention)

| Method | Path | Status | Note |
|---|---|---|---|
| POST | `/v1/foundation/mandates/mandate:pause` | 500 | server_error |
| POST | `/v1/foundation/mandates/mandate:resume` | 500 | server_error |

## Operations that accepted an empty body (review)

- PUT `/notifications/preferences` -> 200
