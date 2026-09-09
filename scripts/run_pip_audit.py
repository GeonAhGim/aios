"""H-5(ADR-2026-09-09-B) 공급망 취약점 게이트 — pip-audit 실행 래퍼.

왜 pip-audit을 있는 그대로 CLI에서 --strict로 못 부르나: 이 저장소는 CI에서
`pip install -e ".[test,dev]"`로 스스로를 editable로 설치한다(quality.yml).
pip-audit --strict는 "감사 대상에서 스킵된 항목이 하나라도 있으면" 즉시
실패하는데(pip_audit._cli — SkippedDependency는 --strict 아래서 무조건 fatal),
`--skip-editable`을 줘도 그 자기 자신은 여전히 "skip_reason=distribution
marked as editable"인 SkippedDependency로 리포트된다 — 즉 --strict와
--skip-editable을 그대로 같이 쓰면 이 저장소에서는 항상 빨간불이다(자기 자신을
audit 대상에서 뺀 것 자체가 --strict가 보기엔 "수집 실패"이기 때문).

그래서 `--format json`으로 원시 결과를 받아 이 스크립트가 strict 판정을
대신한다: skip_reason이 정확히 "distribution marked as editable"인 항목(=
--skip-editable이 의도적으로 만든 항목)만 통과시키고, 그 외의 모든 skip
(PyPI 미등록·해석 실패 등 진짜 수집 실패)은 여전히 fatal로 취급한다. 나머지는
`pip-audit --ignore-vuln ID`로는 사유·만료일을 남길 수 없어 `.pip-audit-ignore`
(JSON)에 사유+만료일을 강제로 같이 적게 하고, 만료된 예외가 하나라도 있으면
pip-audit을 돌리기도 전에 실패한다(예외 방치 금지 — coverage_ratchet.py/
check_audit_regressions.py와 같은 래칫 원칙).

사용: `python scripts/run_pip_audit.py` (저장소 루트에서, 의존성이 이미
설치된 환경에서 — quality.yml/local_ci.py 둘 다 이 스텝 앞에 install이 있다).
종료코드 0=통과, 1=만료된 예외/진짜 수집 실패/미해결 취약점/pip-audit 실행
자체의 실패.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IGNORE_FILE = ROOT / ".pip-audit-ignore"
EXPECTED_SKIP_REASON = "distribution marked as editable"


class IgnoreFileError(ValueError):
    """`.pip-audit-ignore`의 형식이 잘못됐거나 예외가 만료됐다."""


class AuditFailure(ValueError):
    """pip-audit 실행 실패, 진짜 수집 실패, 또는 미해결 취약점 발견."""


def load_ignored_vuln_ids(ignore_file: Path, *, today: dt.date) -> list[str]:
    """`.pip-audit-ignore`를 읽어 아직 유효한 취약점 ID 목록을 돌려준다.

    형식: {"ignore": [{"id": "...", "reason": "...", "expires": "YYYY-MM-DD"}]}.
    파일이 없으면(예외 없음) 빈 목록. `expires`가 오늘보다 과거면 IgnoreFileError —
    유효기간이 지난 예외를 자동으로 계속 무시하면 게이트가 무력화된다.
    """
    if not ignore_file.exists():
        return []
    try:
        data = json.loads(ignore_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IgnoreFileError(f"{ignore_file}: JSON 파싱 실패: {exc}") from exc
    entries = data.get("ignore", [])
    ids: list[str] = []
    expired: list[str] = []
    for entry in entries:
        vuln_id = entry.get("id")
        reason = entry.get("reason")
        expires_raw = entry.get("expires")
        if not vuln_id or not reason or not expires_raw:
            raise IgnoreFileError(
                f"{ignore_file}: 각 항목은 id/reason/expires가 모두 있어야 한다: {entry!r}"
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
        ids.append(vuln_id)
    if expired:
        raise IgnoreFileError(
            f"{ignore_file}: 만료된 예외 {len(expired)}건 — 갱신하거나 지워라:\n"
            + "\n".join(f"  - {line}" for line in expired)
        )
    return ids


def build_pip_audit_command(python: str) -> list[str]:
    return [python, "-m", "pip_audit", "--skip-editable", "--format", "json"]


def evaluate_pip_audit_json(raw_stdout: str, ignored_ids: set[str]) -> list[str]:
    """pip-audit --format json 출력을 검사해 strict 판정을 대신한다.

    돌려주는 목록이 비어 있으면 통과. 각 항목은 사람이 읽을 실패 사유 한 줄이다.
    skip_reason이 EXPECTED_SKIP_REASON이 아닌 항목(진짜 수집 실패)과, ignored_ids에
    없는 취약점이 하나라도 남은 의존성을 실패로 센다.
    """
    try:
        data = json.loads(raw_stdout)
    except json.JSONDecodeError as exc:
        raise AuditFailure(f"pip-audit 출력이 JSON이 아니다: {exc}") from exc

    failures: list[str] = []
    for dep in data.get("dependencies", []):
        skip_reason = dep.get("skip_reason")
        if skip_reason is not None:
            if skip_reason != EXPECTED_SKIP_REASON:
                failures.append(f"{dep['name']}: 수집 실패 — {skip_reason}")
            continue
        vulns = [
            v for v in dep.get("vulns", [])
            if not (set(v.get("aliases", [])) | {v.get("id")}) & ignored_ids
        ]
        for v in vulns:
            fix = v.get("fix_versions") or ["no fix"]
            failures.append(
                f"{dep['name']} {dep['version']}: {v.get('id')} (fix: {', '.join(fix)})"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ignore-file", type=Path, default=DEFAULT_IGNORE_FILE)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)

    try:
        ignored_ids = load_ignored_vuln_ids(args.ignore_file, today=dt.date.today())
    except IgnoreFileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    cmd = build_pip_audit_command(args.python)
    proc = subprocess.run(cmd, capture_output=True, text=True)

    try:
        failures = evaluate_pip_audit_json(proc.stdout, set(ignored_ids))
    except AuditFailure as exc:
        print(str(exc), file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return 1

    if failures:
        print("pip-audit 게이트 실패:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"pip-audit 게이트 통과 (ignored={sorted(ignored_ids)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
