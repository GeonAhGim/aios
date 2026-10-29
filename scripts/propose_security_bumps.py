"""Propose requirements-lock.txt bumps for pip-audit findings (dry-run only).

Reads `pip-audit --format json` output and prints a table of suggested bumps.
It never edits requirements-lock.txt or .pip-audit-ignore: exception drafts are
printed for a human to review and apply.

Usage: python scripts/propose_security_bumps.py AUDIT.json [--lock requirements-lock.txt]
Exit codes: 0 = proposal printed, 2 = bad input or a network failure (fail-closed).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPIRY_DAYS = 30
TARGET_PYTHON = "3.10"

AUTO = "auto-propose"
MANUAL = "manual-review"
EXCEPTION = "exception-candidate"

# Returns True if `name==version` is installable on Python 3.10, False if pip
# reports no matching distribution. Raises NetworkCheckError on any other failure.
InstallCheck = Callable[[str, str], bool]


class NetworkCheckError(RuntimeError):
    """The installability probe failed for a reason other than 'no such version'."""


@dataclass(frozen=True)
class Vulnerability:
    package: str
    current: str
    vuln_id: str
    fix_versions: tuple[str, ...]


@dataclass(frozen=True)
class Proposal:
    package: str
    current: str
    proposed: str | None
    classification: str
    reason: str
    vuln_ids: tuple[str, ...]


def _release_tuple(version: str) -> tuple[int, ...] | None:
    match = re.match(r"^v?(\d+(?:\.\d+)*)", version.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def version_sort_key(version: str) -> tuple[int, ...]:
    parsed = _release_tuple(version)
    return parsed if parsed is not None else (-1,)


def classify_bump(current: str, proposed: str | None) -> str:
    """Pure classification: patch/minor -> auto, major -> manual, none -> exception."""
    if proposed is None:
        return EXCEPTION
    cur, new = _release_tuple(current), _release_tuple(proposed)
    if cur is None or new is None:
        return MANUAL
    if new[0] != cur[0]:
        return MANUAL
    return AUTO


def pick_min_installable(
    fix_versions: Iterable[str], is_installable: InstallCheck, package: str
) -> str | None:
    """Smallest fix version for which `is_installable` is True, else None."""
    for version in sorted(set(fix_versions), key=version_sort_key):
        if is_installable(package, version):
            return version
    return None


def parse_audit_json(raw: str) -> list[Vulnerability]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"pip-audit JSON parse failure: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("pip-audit JSON must be an object with 'dependencies'")
    found: list[Vulnerability] = []
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            found.append(
                Vulnerability(
                    package=str(dep.get("name", "")),
                    current=str(dep.get("version", "")),
                    vuln_id=str(vuln.get("id", "")),
                    fix_versions=tuple(str(v) for v in vuln.get("fix_versions") or ()),
                )
            )
    return found


def build_proposals(vulns: Iterable[Vulnerability], is_installable: InstallCheck) -> list[Proposal]:
    """Group findings per package; propose the minimum version fixing all of them."""
    grouped: dict[tuple[str, str], list[Vulnerability]] = {}
    for vuln in vulns:
        grouped.setdefault((vuln.package, vuln.current), []).append(vuln)
    proposals: list[Proposal] = []
    for (package, current), items in sorted(grouped.items()):
        ids = tuple(sorted(v.vuln_id for v in items))
        candidates: set[str] | None = None
        for item in items:
            versions = set(item.fix_versions)
            candidates = versions if candidates is None else candidates & versions
        if not candidates:
            # No single version fixes every finding: be conservative, use the union.
            candidates = {fv for item in items for fv in item.fix_versions}
        proposed = pick_min_installable(candidates, is_installable, package)
        classification = classify_bump(current, proposed)
        if proposed is None:
            reason = (
                "no fix_versions published"
                if not candidates
                else f"no fix installable on Python {TARGET_PYTHON}"
            )
        elif classification == MANUAL:
            reason = f"major bump {current} -> {proposed}; needs manual review"
        else:
            reason = f"min fix installable on Python {TARGET_PYTHON}"
        proposals.append(Proposal(package, current, proposed, classification, reason, ids))
    return proposals


def draft_ignore_entries(proposals: Iterable[Proposal], today: dt.date) -> list[dict[str, str]]:
    expires = (today + dt.timedelta(days=EXPIRY_DAYS)).isoformat()
    return [
        {
            "id": vuln_id,
            "reason": f"{p.package} {p.current}: {p.reason}",
            "expires": expires,
        }
        for p in proposals
        if p.classification == EXCEPTION
        for vuln_id in p.vuln_ids
    ]


def render_table(proposals: Iterable[Proposal]) -> str:
    header = ("package", "current", "proposed", "class", "reason")
    rows = [header] + [
        (p.package, p.current, p.proposed or "-", p.classification, p.reason) for p in proposals
    ]
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = [" | ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows]
    lines.insert(1, "-+-".join("-" * w for w in widths))
    return "\n".join(lines)


def pip_download_check(package: str, version: str) -> bool:
    """Real probe: can `package==version` be downloaded as a wheel for Python 3.10?"""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            sys.executable,
            "-m",
            "pip",
            "download",
            f"{package}=={version}",
            "--python-version",
            TARGET_PYTHON,
            "--only-binary=:all:",
            "--no-deps",
            "--disable-pip-version-check",
            "-d",
            tmp,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NetworkCheckError(f"pip download failed to run: {exc}") from exc
    if proc.returncode == 0:
        return True
    err = proc.stderr
    if "No matching distribution" in err or "Could not find a version" in err:
        return False
    raise NetworkCheckError(f"pip download {package}=={version}: {err.strip()[-300:]}")


def main(
    argv: list[str] | None = None,
    *,
    is_installable: InstallCheck = pip_download_check,
    today: dt.date | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run: propose lock bumps for pip-audit JSON findings. "
        "Never modifies requirements-lock.txt or .pip-audit-ignore."
    )
    parser.add_argument(
        "audit_json", help="path to `pip-audit --format json` output (- reads stdin)"
    )
    parser.add_argument(
        "--lock",
        default=str(ROOT / "requirements-lock.txt"),
        help="lock file (never modified; informational)",
    )
    args = parser.parse_args(argv)
    try:
        if args.audit_json == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.audit_json).read_text(encoding="utf-8")
        proposals = build_proposals(parse_audit_json(raw), is_installable)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except NetworkCheckError as exc:
        print(
            f"ERROR: network check failed (fail-closed, not treated as pass): {exc}",
            file=sys.stderr,
        )
        return 2
    if not proposals:
        print("no vulnerabilities in input; nothing to propose")
        return 0
    print(render_table(proposals))
    drafts = draft_ignore_entries(proposals, today or dt.datetime.now(dt.UTC).date())
    if drafts:
        print("\n# DRAFT .pip-audit-ignore entries (NOT applied; human review required):")
        print(json.dumps({"ignore": drafts}, indent=2, ensure_ascii=False))
    print("\n(dry-run: no files were modified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
