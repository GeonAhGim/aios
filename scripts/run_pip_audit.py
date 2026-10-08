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
종료코드 0=통과, 1=만료된 예외/진짜 수집 실패/미해결 취약점, 3=pypi.org DNS/연결
실패(OPS-44, task-3386 — 취약점 판정이 아니라 판정 불가 상태이므로 1과 구분해
local_ci.py가 skipped(network)로 다루게 한다).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IGNORE_FILE = ROOT / ".pip-audit-ignore"
DEFAULT_LOCK_FILE = ROOT / "requirements-lock.txt"
EXPECTED_SKIP_REASON = "distribution marked as editable"

# task-11447: GitHub Actions는 `.[test,dev]`만 설치하지만, 로컬 CI는 mutation 테스트
# 도구(cosmic-ray/mutmut 등, `.[mutation]`)까지 깐 상주 venv(C:\aios\mihwa-aios\.venv)를
# 재사용한다 -- 그 extras의 전이 의존(예: cosmic-ray -> aiohttp -> multidict)은
# requirements-lock.txt에 핀이 없어 Actions가 절대 보지 못하는 영역이고, 로컬에서만
# CVE가 터질 수 있다(2026-10-07, multidict 6.8.0 CVE-2026-104874). lock 안 패키지의
# 취약점은 사람이 lock을 바꿔야 고칠 문제라 숫자 집계만 하고 그대로 fail 시키되,
# lock 밖(local-only) 패키지의 취약점은 "Actions parity 밖 로컬 전용 환경 문제"임을
# 보고서 tail에 명시해 두 원인을 혼동하지 않게 한다(허용 목록이 아니다 -- 여전히
# 게이트는 실패한다, 진단만 분리한다).
_FAILURE_LINE_RE = re.compile(r"^(\S+) \S+: \S+ \(fix: ")


class IgnoreFileError(ValueError):
    """`.pip-audit-ignore`의 형식이 잘못됐거나 예외가 만료됐다."""


class AuditFailure(ValueError):
    """pip-audit 실행 실패, 진짜 수집 실패, 또는 미해결 취약점 발견."""


# OPS-44(task-3386): pypi.org DNS/연결 실패는 취약점 판정이 불가능한 상태이지, 취약점
# 자체가 아니다. 이 마커가 rc=1 출력에 섞여 나오면 local_ci.py 쪽(ci_recheck.
# is_supply_chain_network_error)이 "취약점 발견"과 구분해 skipped(network)로 다루도록
# rc=3(구분 코드)을 대신 돌려준다.
NETWORK_ERROR_MARKERS = ("ENOTFOUND", "NameResolutionError", "ConnectionError", "Max retries")
NETWORK_ERROR_RC = 3


def is_network_error(text: str | None) -> bool:
    """`text`가 None이어도(예: subprocess 캡처 실패) TypeError 없이 False를 낸다."""
    normalized = text or ""
    return any(marker in normalized for marker in NETWORK_ERROR_MARKERS)


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


def _normalize_pkg_name(name: str) -> str:
    """'PyJWT' -> 'pyjwt', 'pydantic_settings' -> 'pydantic-settings' -- local_ci.py의
    _normalize_pkg_name과 같은 정규화(대소문자·구분자 차이를 다른 패키지로 보지 않는다)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock_names(lock_text: str) -> set[str]:
    """requirements-lock.txt에서 핀 줄의 패키지명만 정규화해 집합으로 돌려준다(주석·
    빈 줄·환경 마커 뒤는 무시, 버전은 보지 않는다 -- '이 이름이 사람이 고정한 적
    있는가'만 안다)."""
    names: set[str] = set()
    for raw in lock_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        line = line.split(";", 1)[0].strip()  # PEP 508 환경 마커 제거
        m = re.match(r"^([A-Za-z0-9_.\-]+)", line)
        if m:
            names.add(_normalize_pkg_name(m.group(1)))
    return names


def failure_package_name(failure_line: str) -> str | None:
    """`evaluate_pip_audit_json`이 낸 실패 한 줄에서 패키지명만 뽑는다. 취약점 실패
    (`"{name} {version}: {id} (fix: ...)"`) 형태만 매치한다 -- skip_reason 실패
    (`"{name}: 수집 실패 — ..."`)는 pip install로 자동 복구할 대상이 아니라서
    None을 돌려준다."""
    m = _FAILURE_LINE_RE.match(failure_line)
    return m.group(1) if m else None


def split_failures_by_lock(
    failures: list[str], lock_names: set[str]
) -> tuple[list[str], list[str]]:
    """실패 목록을 (locked, local_only)로 나눈다. locked는 requirements-lock.txt에 핀이
    있어 사람이 lock을 바꿔야 고칠 수 있는 패키지, local_only는 lock 밖 전이 의존이라
    pip install --upgrade로 그 자리에서 복구를 시도할 수 있는 패키지다."""
    locked: list[str] = []
    local_only: list[str] = []
    for line in failures:
        name = failure_package_name(line)
        if name is not None and _normalize_pkg_name(name) not in lock_names:
            local_only.append(line)
        else:
            locked.append(line)
    return locked, local_only


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
            v
            for v in dep.get("vulns", [])
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
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)

    try:
        ignored_ids = load_ignored_vuln_ids(args.ignore_file, today=dt.date.today())
    except IgnoreFileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    cmd = build_pip_audit_command(args.python)
    # encoding을 명시하지 않으면 Windows에서 locale.getpreferredencoding()(cp949 등)로
    # 디코딩되는데, pip-audit --format json은 UTF-8을 낸다 — 로케일이 cp949인 CI
    # 러너에서 UnicodeDecodeError로 죽는다(task-8362). pip-audit 출력은 항상 UTF-8이므로
    # 고정한다.
    #
    # 이것만으로는 부족하다: pip-audit(자식 python 프로세스) 자신도 venv 부트스트랩용
    # pip를 별도 subprocess로 띄우는데, 그 내부 호출은 text 모드이면서 encoding을
    # 지정하지 않는다 — 이 프로세스가 cp949 로케일로 뜨면 그 안쪽 subprocess의
    # reader thread가 UTF-8 바이트를 cp949로 디코딩하다 죽는다(task-8662, 이 스크립트의
    # encoding="utf-8" 한 겹 아래에서 발생하므로 여기서 고쳐도 안 잡혔다). PYTHONUTF8=1을
    # 자식 프로세스 환경에 넣어 그 안의 모든 text-mode subprocess가 로케일과 무관하게
    # UTF-8을 기본값으로 쓰게 한다(PEP 540).
    child_env = dict(os.environ)
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=child_env
    )
    # capture_output=True/text=True는 정상 경로에서 stdout/stderr를 항상 str로 채우지만,
    # subprocess가 캡처 자체에 실패하는 경로(예: 예외적인 파이프 처리)에서는 None이 나올 수
    # 있다 -- is_network_error가 None을 받아도 죽지 않게 여기서도 빈 문자열로 정규화해
    # 아래 판정 전체가 이 값 하나만 신뢰하면 되게 한다.
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if is_network_error(stdout) or is_network_error(stderr):
        print(
            "pip-audit 네트워크 오류(pypi.org DNS/연결 실패) -- 취약점 판정 불가:", file=sys.stderr
        )
        print(stderr, file=sys.stderr)
        return NETWORK_ERROR_RC

    if not stdout.strip():
        # 네트워크 오류 마커도 없이 stdout이 비어 있으면 취약점 판정이 아니라 감사 실행
        # 자체가 실패한 것(예: capture 실패, pip_audit 프로세스가 조기 종료) -- "취약점
        # 발견"으로 오판하지 않도록 별도 사유로 fail-closed 처리한다.
        print(
            "pip-audit 감사 실행 자체가 실패했다(stdout 비어있음, 네트워크 오류 아님):",
            file=sys.stderr,
        )
        print(stderr, file=sys.stderr)
        return 1

    try:
        failures = evaluate_pip_audit_json(stdout, set(ignored_ids))
    except AuditFailure as exc:
        print(str(exc), file=sys.stderr)
        print(stderr, file=sys.stderr)
        return 1

    if failures:
        print("pip-audit 게이트 실패:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        # task-11447: lock 밖(local-only) 패키지의 취약점은 GitHub Actions가 절대
        # 보지 않는 영역(parity 밖)이라는 걸 tail에 명시해 "어디서 왜 적색인지"를
        # 혼동하지 않게 한다 -- 여전히 게이트는 fail한다, 진단만 분리한다.
        lock_text = args.lock_file.read_text(encoding="utf-8") if args.lock_file.exists() else ""
        locked, local_only = split_failures_by_lock(failures, parse_lock_names(lock_text))
        if local_only:
            print(
                f"\n참고: 위 {len(local_only)}건은 {args.lock_file.name}에 핀이 없는 "
                "로컬 전용 패키지다(예: mutation extras의 전이 의존) -- GitHub Actions는 "
                "이 패키지를 설치하지 않아 같은 취약점을 보지 못한다(Actions parity 밖). "
                "`pip install --upgrade <package>`로 로컬 venv에서 직접 고친다:",
                file=sys.stderr,
            )
            for line in local_only:
                print(f"  - {line}", file=sys.stderr)
        if locked:
            print(
                f"\n참고: 위 {len(locked)}건은 {args.lock_file.name}에 핀이 있다 -- "
                "requirements-lock.txt 갱신이 필요하다(사람 결정).",
                file=sys.stderr,
            )
        return 1

    print(f"pip-audit 게이트 통과 (ignored={sorted(ignored_ids)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
