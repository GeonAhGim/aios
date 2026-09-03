"""PLT-17/PLT-20 — 이관된 라우터에 raw `HTTPException` raise가 없는지 정적 검사.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-17~21

AST 기반 — 문자열 grep이 아니라 `raise` 문이 실제로 호출하는 대상이
`HTTPException`(그대로 import) 또는 `fastapi.HTTPException`(qualified
attribute)인지 판정한다. 도메인 예외는 전역 핸들러(src/api/contracts/
handlers.py)가 EXCEPTION_MAP을 통해 상태코드·error_code·trace_id를
채우므로 라우터가 직접 HTTPException을 만들 이유가 없다.

MIGRATED_ROUTERS는 허용목록이다 — 아직 이관되지 않은 라우터(foundation/
executions/admin 등)는 여기 없다. 후속 리프(PLT-19/21)가 이관을
끝낼 때마다 이 목록에 파일을 추가해 검사 범위를 넓힌다.
"""
from __future__ import annotations

import ast
from pathlib import Path

MIGRATED_ROUTERS: list[Path] = [
    Path("src/api/routers/auth.py"),
    Path("src/api/routers/users.py"),
    Path("src/api/routers/exchange_credentials.py"),
    Path("src/api/routers/marketplace.py"),
    Path("src/api/routers/strategy_builder.py"),
    Path("src/api/routers/suitability.py"),
    Path("src/api/routers/notifications.py"),
    Path("src/api/routers/alerts.py"),
    Path("src/api/routers/device_tokens.py"),
    Path("src/api/routers/wallet.py"),
]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _raw_http_exception_raise_lines(source_path: Path) -> list[int]:
    """`source_path` 안에서 `raise HTTPException(...)` 형태의 라인 번호 목록.

    `raise SomeOtherError(...) from exc`처럼 원인만 HTTPException인 경우는
    대상이 아니다 — 검사 대상은 어디까지나 라우터가 직접 만들어 던지는
    HTTPException 그 자체다."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(target, ast.Name) and target.id == "HTTPException":
            violations.append(node.lineno)
        elif isinstance(target, ast.Attribute) and target.attr == "HTTPException":
            violations.append(node.lineno)
    return violations


def test_migrated_routers_have_zero_raw_http_exception_raises():
    offenders = {
        str(rel_path): lines
        for rel_path in MIGRATED_ROUTERS
        if (lines := _raw_http_exception_raise_lines(_REPO_ROOT / rel_path))
    }
    assert offenders == {}, f"raw HTTPException raise found (도메인 예외로 이관 필요): {offenders}"


def test_detector_actually_flags_a_raw_http_exception_raise(tmp_path):
    """검사기 자체의 negative test — 대상 파일이 전부 통과라서 위 테스트가
    그냥 항상 초록불인 동어반복이 아님을 고정한다. 일부러 위반 코드를 담은
    파일을 만들어 `_raw_http_exception_raise_lines`가 실제로 잡아내는지
    확인한다."""
    offending_file = tmp_path / "sample_router.py"
    offending_file.write_text(
        "from fastapi import HTTPException\n"
        "\n"
        "def handler():\n"
        "    raise HTTPException(400, 'bad request')\n",
        encoding="utf-8",
    )

    assert _raw_http_exception_raise_lines(offending_file) == [4]


def test_detector_ignores_domain_exception_raised_from_http_exception_cause(tmp_path):
    """`raise DomainError(...) from some_http_exception`처럼 HTTPException이
    원인(cause)으로만 등장하는 경우는 위반이 아니다 — 실제로 던져지는
    예외 타입만 검사 대상이어야 한다."""
    clean_file = tmp_path / "clean_router.py"
    clean_file.write_text(
        "class DomainError(Exception):\n"
        "    pass\n"
        "\n"
        "def handler(exc):\n"
        "    raise DomainError('mapped') from exc\n",
        encoding="utf-8",
    )

    assert _raw_http_exception_raise_lines(clean_file) == []
