"""H-5(ADR-2026-09-09-B) 프런트엔드 공급망 게이트 -- npm audit 실행 래퍼.

왜 `npm audit --audit-level=high`를 그대로 못 부르나: 패치 버전이 아직 게시되지 않은
취약점(예: picomatch ReDoS GHSA-fc5q-39xc-vjpf, 영향 범위 `<=4.0.7`인데 최신판이 4.0.7)은
업그레이드로 고칠 수 없는데, 그 하나가 의존 사슬(vite·vitest·tinyglobby·fdir)을 타고 번져
high 6건으로 잡힌다. 그렇다고 `--audit-level`을 올리면 다른 high까지 전부 가려진다.

그래서 `npm audit --json`을 읽어 이 스크립트가 판정한다 -- backend의
`scripts/run_pip_audit.py` + `.pip-audit-ignore`와 같은 원칙이다:

- 예외는 `frontend/.npm-audit-ignore`(JSON)에 **advisory ID(GHSA) 단위**로만 둔다.
  id/reason/expires가 모두 필수이고, expires가 지났으면 게이트가 실패한다(예외 방치 금지).
- 예외로 적은 advisory가 감사 결과에 더 이상 나오지 않으면(=lock이 이미 고쳐짐) 실패한다
  -- 쓸모없어진 예외를 지우게 만든다.
- 예외 advisory의 취약 패키지가 **호환 범위 안에서 고칠 수 있게 되면**(`fixAvailable`이 true)
  실패한다 -- 패치가 나왔는데 예외로 덮어두지 않게 한다.
- 예외에 없는 high 이상 advisory가 하나라도 있으면 실패한다.

사용: `python scripts/run_npm_audit.py [--frontend-dir frontend]` (저장소 루트에서, `npm ci` 후).
종료코드 0=통과, 1=미해결 취약점/만료·불필요 예외, 2=npm 실행·출력 해석 실패,
3=레지스트리 연결 실패(취약점 판정이 아니라 판정 불가 -- 호출 측이 구분해 다룬다).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONTEND_DIR = ROOT / "frontend"
IGNORE_FILE_NAME = ".npm-audit-ignore"
SEVERITY_ORDER = {"info": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}
FAIL_LEVEL = "high"
NETWORK_ERROR_MARKERS = ("ENOTFOUND", "EAI_AGAIN", "ECONNREFUSED", "ETIMEDOUT", "network")
NETWORK_ERROR_RC = 3
_GHSA_RE = re.compile(r"(GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4})", re.IGNORECASE)


class IgnoreFileError(ValueError):
    """`.npm-audit-ignore`의 형식이 잘못됐거나 예외가 만료됐다."""


def load_ignored_ids(ignore_file: Path, *, today: dt.date) -> dict[str, str]:
    """유효한 예외를 {GHSA id(소문자): 사유}로 돌려준다. 파일이 없으면 빈 dict."""
    if not ignore_file.exists():
        return {}
    try:
        data = json.loads(ignore_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IgnoreFileError(f"{ignore_file}: JSON 파싱 실패: {exc}") from exc
    ids: dict[str, str] = {}
    expired: list[str] = []
    for entry in data.get("ignore", []):
        vuln_id = entry.get("id")
        reason = entry.get("reason")
        expires_raw = entry.get("expires")
        if not vuln_id or not reason or not expires_raw:
            raise IgnoreFileError(
                f"{ignore_file}: 각 항목은 id/reason/expires가 모두 있어야 한다: {entry!r}"
            )
        if not _GHSA_RE.fullmatch(str(vuln_id)):
            raise IgnoreFileError(
                f"{ignore_file}: id는 GHSA-xxxx-xxxx-xxxx 형식이어야 한다: {vuln_id!r}"
            )
        try:
            expires = dt.date.fromisoformat(expires_raw)
        except ValueError as exc:
            raise IgnoreFileError(
                f"{ignore_file}: {vuln_id} expires 형식 오류(YYYY-MM-DD 필요): {expires_raw!r}"
            ) from exc
        if expires < today:
            expired.append(f"{vuln_id} (expired {expires_raw}): {reason}")
            continue
        ids[str(vuln_id).lower()] = str(reason)
    if expired:
        raise IgnoreFileError(
            f"{ignore_file}: 만료된 예외 {len(expired)}건 -- 갱신하거나 지워라:\n"
            + "\n".join(f"  - {line}" for line in expired)
        )
    return ids


def _advisory_id(via: dict[str, Any]) -> str | None:
    match = _GHSA_RE.search(str(via.get("url", "")))
    return match.group(1).lower() if match else None


def collect_blocking(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """high 이상 advisory를 {GHSA id: {title, package, fixable}}로 모은다.

    npm audit의 vulnerabilities 맵에서 `via`가 dict인 항목이 실제 advisory이고, 문자열인 항목은
    다른 패키지를 통해 번진(파생) 취약점이라 원인 advisory 쪽에서 이미 잡힌다.
    """
    found: dict[str, dict[str, Any]] = {}
    vulns = report.get("vulnerabilities", {})
    for name, vuln in vulns.items():
        for via in vuln.get("via", []):
            if not isinstance(via, dict):
                continue
            if SEVERITY_ORDER.get(str(via.get("severity")), 0) < SEVERITY_ORDER[FAIL_LEVEL]:
                continue
            advisory = _advisory_id(via) or f"npm-{via.get('source')}"
            fix = vulns.get(via.get("name", name), vuln).get("fixAvailable")
            found[advisory] = {
                "title": str(via.get("title", "")),
                "package": str(via.get("name", name)),
                "fixable": fix is True,
            }
    return found


def evaluate(report: dict[str, Any], ignored: dict[str, str]) -> list[str]:
    """실패 사유 목록(빈 목록이면 통과)."""
    blocking = collect_blocking(report)
    failures: list[str] = []
    for advisory, info in sorted(blocking.items()):
        label = f"{advisory} [{info['package']}] {info['title']}"
        if advisory not in ignored:
            failures.append(f"미해결 high 이상 취약점: {label}")
        elif info["fixable"]:
            failures.append(
                f"예외 advisory에 호환 범위 패치가 생겼다 -- 업그레이드하고 예외를 지워라: {label}"
            )
    for advisory, reason in sorted(ignored.items()):
        if advisory not in blocking:
            failures.append(f"쓸모없어진 예외(감사 결과에 없음) -- 지워라: {advisory} ({reason})")
    return failures


def run_npm_audit(frontend_dir: Path) -> tuple[int, str]:
    npm = shutil.which("npm")
    if npm is None:
        return 2, "npm이 설치돼 있지 않다"
    proc = subprocess.run(  # noqa: S603 -- 고정 인자, 셸 미사용
        [npm, "audit", "--json"],
        cwd=frontend_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return proc.returncode, proc.stdout or proc.stderr or ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-dir", type=Path, default=DEFAULT_FRONTEND_DIR)
    parser.add_argument("--ignore-file", type=Path, default=None)
    args = parser.parse_args(argv)
    ignore_file = args.ignore_file or args.frontend_dir / IGNORE_FILE_NAME
    try:
        ignored = load_ignored_ids(ignore_file, today=dt.date.today())
    except IgnoreFileError as exc:
        print(f"npm audit 게이트 실패: {exc}")
        return 1
    rc, out = run_npm_audit(args.frontend_dir)
    try:
        report = json.loads(out)
    except json.JSONDecodeError:
        print("npm audit 출력을 JSON으로 읽지 못했다:\n" + out[:2000])
        return NETWORK_ERROR_RC if any(m in out for m in NETWORK_ERROR_MARKERS) else 2
    if "error" in report and "vulnerabilities" not in report:
        text = json.dumps(report["error"], ensure_ascii=False)
        print("npm audit 실행 오류: " + text[:2000])
        return NETWORK_ERROR_RC if any(m in text for m in NETWORK_ERROR_MARKERS) else 2
    failures = evaluate(report, ignored)
    if failures:
        print("npm audit 게이트 실패:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"npm audit 게이트 통과 (예외 {len(ignored)}건: {', '.join(sorted(ignored)) or '없음'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
