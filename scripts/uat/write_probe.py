"""Write/param wiring probe for the LOCAL UAT stack: does every operation answer sanely?

For each OpenAPI operation that wiring_probe skips (writes and path-parameter GETs) this sends an
empty JSON body (and a dummy UUID for path parameters) as a throw-away user. Validation errors
(400/422), auth gates (401/403), conflicts and not-found are all *fine* -- they prove the route and
its validation exist. What it hunts for: 5xx (unhandled error), 501/stub markers (not
implemented), and operations that accept an empty body (2xx), which deserve a look.

Safety: refuses any base URL that is not loopback, skips /admin and /auth, never prints tokens.

    python -m scripts.uat.write_probe --out-md docs/frontend/WIRING_WRITES_REPORT.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.uat import wiring_probe as wp

DUMMY_UUID = "00000000-0000-4000-8000-000000000000"
WRITE_METHODS = ("post", "put", "patch", "delete")
SKIP_PREFIXES = ("/admin", "/auth")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PARAM_RE = re.compile(r"\{[^}]+\}")
ACCEPTED_EMPTY = "accepted_empty"
SLEEP_BETWEEN = 0.15


@dataclass
class WriteResult:
    method: str
    path: str
    status: int
    bucket: str
    note: str


def is_loopback(base_url: str) -> bool:
    return urlparse(base_url).hostname in LOOPBACK


def fill_path(path: str) -> str:
    return PARAM_RE.sub(DUMMY_UUID, path)


def operations(openapi: dict[str, Any]) -> list[tuple[str, str]]:
    """(METHOD, path) for writes and for GETs that have path parameters, minus skipped prefixes."""
    found: list[tuple[str, str]] = []
    for path, ops in openapi.get("paths", {}).items():
        if path.startswith(SKIP_PREFIXES):
            continue
        for method in WRITE_METHODS:
            if method in ops:
                found.append((method.upper(), path))
        if "get" in ops and "{" in path:
            found.append(("GET", path))
    return sorted(found, key=lambda item: (item[1], item[0]))


def classify_write(method: str, status: int, body_text: str) -> tuple[str, str]:
    bucket, stub = wp.classify(status, body_text)
    if 200 <= status < 300 and method != "GET" and not stub:
        return ACCEPTED_EMPTY, "accepted an empty body"
    note = "stub marker in body" if stub else ""
    return bucket, note


def probe_write(base: str, method: str, path: str, token: str) -> WriteResult:
    url = f"{base}{fill_path(path)}"
    body: dict[str, Any] | None = None if method == "GET" else {}
    status, text = 0, ""
    for attempt in range(wp.RATE_LIMIT_RETRIES + 1):
        status, text = wp._request(url, method=method, token=token, body=body)
        if status != 429:
            break
        time.sleep(2.0 * (attempt + 1))
    bucket, note = classify_write(method, status, text)
    if status == 0:
        bucket = wp.UNREACHABLE
    return WriteResult(method, path, status, bucket, note)


def render(results: list[WriteResult]) -> str:
    counts: dict[str, int] = {}
    for item in results:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    lines = [
        "# Write/param wiring report (generated)",
        "",
        "> `scripts/uat/write_probe.py` against the loopback UAT API as a throw-away user.",
        "> Empty JSON body and dummy UUIDs. 400/401/403/404/409/422 are healthy answers.",
        "",
        "| Bucket | Count |",
        "|---|---|",
    ]
    lines += [f"| {bucket} | {count} |" for bucket, count in sorted(counts.items())]
    attention = [
        r for r in results if r.bucket in (wp.SERVER_ERROR, wp.UNIMPLEMENTED, wp.UNREACHABLE)
    ]
    lines += ["", "## Server errors and stubs (need attention)", ""]
    if attention:
        lines += ["| Method | Path | Status | Note |", "|---|---|---|---|"]
        lines += [
            f"| {r.method} | `{r.path}` | {r.status} | {r.note or r.bucket} |" for r in attention
        ]
    else:
        lines.append("None.")
    accepted = [r for r in results if r.bucket == ACCEPTED_EMPTY]
    lines += ["", "## Operations that accepted an empty body (review)", ""]
    lines += [f"- {r.method} `{r.path}` -> {r.status}" for r in accepted] or ["None."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18000")
    parser.add_argument("--out-md", type=Path, default=None)
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    if not is_loopback(base):
        print("refusing: write_probe only runs against a loopback (local UAT) API", file=sys.stderr)
        return 2
    status, text = wp._request(f"{base}/openapi.json")
    if status != 200:
        print(f"cannot read openapi.json: HTTP {status}", file=sys.stderr)
        return 2
    token = wp.register_user(base)
    results = []
    for method, path in operations(json.loads(text)):
        results.append(probe_write(base, method, path, token))
        time.sleep(SLEEP_BETWEEN)
    report = render(results)
    if args.out_md:
        args.out_md.write_text(report, encoding="utf-8")
    counts: dict[str, int] = {}
    for item in results:
        counts[item.bucket] = counts.get(item.bucket, 0) + 1
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
