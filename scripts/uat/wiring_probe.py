"""Live wiring probe: call every parameter-free GET operation of a running API as a fresh user.

Why: mock-based tests cannot tell a real endpoint from a missing one. This probe asks the running
server (default: the UAT stack) and classifies each answer, so "is this screen really wired?" has an
evidence-based answer. It only issues GETs (no writes), registers one throw-away user, and never
prints tokens or passwords.

Usage:
    python scripts/uat/wiring_probe.py --base-url http://127.0.0.1:18000 \
        --out-json /tmp/wiring.json --out-md docs/frontend/WIRING_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STUB_WORDS = ("not implemented", "stub", "placeholder", "mock", "todo", "coming soon")
RATE_LIMIT_RETRIES = 3

# Status buckets. "wired" means the server answered like a real implementation would.
WIRED = "wired"  # 2xx
NEEDS_INPUT = "needs_input"  # 400/422: reachable, wants query parameters
NEEDS_ROLE = "needs_role"  # 401/403: reachable but gated (MFA, role, mandate)
MISSING = "missing"  # 404/405: route or resource not there
UNIMPLEMENTED = "unimplemented"  # 501 or a stub marker in a 2xx body
SERVER_ERROR = "server_error"  # 5xx
RATE_LIMITED = "rate_limited"  # 429 after retries
UNREACHABLE = "unreachable"


@dataclass
class ProbeResult:
    path: str
    status: int
    bucket: str
    ms: int
    shape: str  # top-level JSON keys or a short note, never values
    stub_hint: bool


def classify(status: int, body_text: str) -> tuple[str, bool]:
    """Map an HTTP status (+ body for stub markers) to a bucket; second value = stub hint."""
    lowered = body_text.lower()
    stub = any(word in lowered for word in STUB_WORDS)
    if 200 <= status < 300:
        return (UNIMPLEMENTED if stub else WIRED), stub
    if status == 501:
        return UNIMPLEMENTED, True
    if status in (400, 422):
        return NEEDS_INPUT, False
    if status in (401, 403):
        return NEEDS_ROLE, False
    if status in (404, 405):
        return MISSING, False
    if status == 429:
        return RATE_LIMITED, False
    if status >= 500:
        return SERVER_ERROR, False
    return UNREACHABLE, False


def shape_of(body_text: str) -> str:
    """Describe the JSON shape without leaking values: top-level keys, or the list length."""
    try:
        data: Any = json.loads(body_text)
    except (ValueError, TypeError):
        return "non-json" if body_text else "empty"
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, list):
            return f"data[list:{len(inner)}]"
        if isinstance(inner, dict):
            return "data{" + ",".join(sorted(inner)[:6]) + "}"
        return "{" + ",".join(sorted(data)[:6]) + "}"
    if isinstance(data, list):
        return f"list:{len(data)}"
    return type(data).__name__


def _request(
    url: str, *, method: str = "GET", token: str | None = None, body: dict[str, Any] | None = None
) -> tuple[int, str]:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def register_user(base: str) -> str:
    """Create a throw-away user and return its access token (never printed)."""
    password = "Aa1!" + secrets.token_urlsafe(16)
    email = f"probe-{secrets.token_hex(4)}@demo.uat"
    status, text = _request(
        f"{base}/auth/register", method="POST", body={"email": email, "password": password}
    )
    if status != 201:
        raise SystemExit(f"register failed: HTTP {status}")
    payload = json.loads(text)
    token = (payload.get("data") or payload).get("access_token")
    if not token:
        raise SystemExit("register response had no access_token")
    return str(token)


def parameter_free_gets(openapi: dict[str, Any]) -> list[str]:
    paths = []
    for path, ops in openapi.get("paths", {}).items():
        if "get" in ops and "{" not in path:
            paths.append(path)
    return sorted(paths)


def probe_one(base: str, path: str, token: str) -> ProbeResult:
    start = time.perf_counter()
    status, text = 0, ""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        status, text = _request(f"{base}{path}", token=token)
        if status != 429:
            break
        time.sleep(2.0 * (attempt + 1))
    bucket, stub = classify(status, text)
    if status == 0:
        bucket = UNREACHABLE
    return ProbeResult(
        path=path,
        status=status,
        bucket=bucket,
        ms=int((time.perf_counter() - start) * 1000),
        shape=shape_of(text),
        stub_hint=stub,
    )


def render_markdown(
    results: list[ProbeResult], skipped_params: int, static: dict[str, list[str]]
) -> str:
    counts: dict[str, int] = {}
    for item in results:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    lines = [
        "# Wiring report (generated)",
        "",
        "> Generated by `scripts/uat/wiring_probe.py` against a running API as a fresh user.",
        "> GET-only, parameter-free operations. Path-param operations and writes are not probed.",
        "",
        "## Summary",
        "",
        "| Bucket | Meaning | Count |",
        "|---|---|---|",
    ]
    meanings = {
        WIRED: "2xx, real answer",
        NEEDS_INPUT: "400/422, reachable but wants parameters",
        NEEDS_ROLE: "401/403, reachable but gated (MFA/role/mandate)",
        MISSING: "404/405, route or resource missing",
        UNIMPLEMENTED: "501 or stub marker in body",
        SERVER_ERROR: "5xx, server failure",
        RATE_LIMITED: "429 after retries",
        UNREACHABLE: "no answer",
    }
    for bucket in meanings:
        lines.append(f"| {bucket} | {meanings[bucket]} | {counts.get(bucket, 0)} |")
    lines += [
        "",
        f"Probed {len(results)} parameter-free GETs; skipped {skipped_params} with path params.",
        "",
    ]
    problem = [
        r for r in results if r.bucket in (MISSING, UNIMPLEMENTED, SERVER_ERROR, UNREACHABLE)
    ]
    lines += ["## Problems (need attention)", ""]
    if problem:
        lines += ["| Path | Status | Bucket | Shape |", "|---|---|---|---|"]
        lines += [f"| `{r.path}` | {r.status} | {r.bucket} | {r.shape} |" for r in problem]
    else:
        lines.append("None.")
    lines += [
        "",
        "## All probed operations",
        "",
        "| Path | Status | Bucket | ms | Shape |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| `{r.path}` | {r.status} | {r.bucket} | {r.ms} | {r.shape} |" for r in results]
    for title, items in static.items():
        lines += ["", f"## {title}", ""]
        lines += [f"- `{item}`" for item in items] or ["None."]
    return "\n".join(lines) + "\n"


def static_findings(root: Path) -> dict[str, list[str]]:
    """Known client/contract mismatches from the repo's own consistency check (best effort)."""
    try:
        sys.path.insert(0, str(root))
        from scripts.consistency.registry import METRICS  # noqa: PLC0415

        hits = METRICS["openapi_client_mismatch"](root)
    except Exception as exc:  # noqa: BLE001 - report generation must not fail on this
        return {"Static client/contract mismatches": [f"unavailable: {type(exc).__name__}"]}
    client = [str(h[0]).split("#", 1)[1] for h in hits if "apiRoutes" in str(h[0])]
    contract = [str(h[0]).split("#", 1)[1] for h in hits if "contracts/openapi" in str(h[0])]
    return {
        "Frontend client routes with no backend route (will fail at runtime)": sorted(client),
        "Contract entries with no implementation": sorted(contract),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    status, text = _request(f"{base}/openapi.json")
    if status != 200:
        print(f"cannot read openapi.json: HTTP {status}", file=sys.stderr)
        return 2
    openapi = json.loads(text)
    token = register_user(base)
    paths = parameter_free_gets(openapi)
    total_get = sum(1 for ops in openapi.get("paths", {}).values() if "get" in ops)
    results = [probe_one(base, path, token) for path in paths]
    if args.out_json:
        args.out_json.write_text(
            json.dumps([asdict(r) for r in results], indent=1), encoding="utf-8"
        )
    root = Path(__file__).resolve().parents[2]
    report = render_markdown(results, total_get - len(paths), static_findings(root))
    if args.out_md:
        args.out_md.write_text(report, encoding="utf-8")
    counts: dict[str, int] = {}
    for item in results:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
