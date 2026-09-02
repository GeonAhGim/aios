"""Zone 순수성 AST 검사 — L0-2.

DoD(docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-2, §8.4):
`src/**/domain/**`는 `asyncpg|httpx|sqlalchemy`를 import하지 않는다. 문자열 grep이 아니라
AST로 실제 import 문만 판정한다(주석·문자열 리터럴에 이 이름이 등장해도 오탐하지 않는다).
ruff `TID251`(banned-api, pyproject.toml)이 같은 규칙을 정적 분석 단계에서 병행 강제한다 —
이 테스트는 그 설정이 없거나 우회되어도 항상 실패를 재현하는 두 번째 방어선이다.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BANNED_MODULES = frozenset({"asyncpg", "httpx", "sqlalchemy"})


def _find_banned_imports(source: str, banned: frozenset[str]) -> list[str]:
    """`source`를 파싱해 최상위 모듈명이 `banned`에 속하는 import를 전부 반환한다."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in banned:
                    found.append(root)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:  # `from . import x` 같은 상대 import
                continue
            root = node.module.split(".")[0]
            if root in banned:
                found.append(root)
    return found


def _domain_dirs() -> list[Path]:
    return sorted(p for p in (ROOT / "src").rglob("domain") if p.is_dir())


def test_domain_dirs_exist() -> None:
    """검사 대상이 하나도 없으면 아래 테스트는 아무것도 검증하지 못한 채 항상 통과한다."""
    assert _domain_dirs(), "src/**/domain 디렉터리를 하나도 찾지 못함 — 검사가 무의미해짐"


def test_domain_zone_has_no_io_imports() -> None:
    violations: list[str] = []
    for domain_dir in _domain_dirs():
        for py_file in domain_dir.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            found = _find_banned_imports(source, BANNED_MODULES)
            if found:
                rel = py_file.relative_to(ROOT).as_posix()
                violations.append(f"{rel}: {sorted(set(found))}")

    assert not violations, (
        "domain/**에서 금지된 I/O 임포트 발견(순수 규칙 위반):\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "banned_source",
    [
        "import asyncpg\n",
        "import asyncpg.pool\n",
        "from asyncpg import connect\n",
        "import httpx\n",
        "from httpx import AsyncClient\n",
        "import sqlalchemy\n",
        "from sqlalchemy.orm import Session\n",
    ],
)
def test_negative_sample_is_detected(banned_source: str) -> None:
    """위반 샘플(실제 파일로 만들지 않고 문자열로만 구성)이 탐지되지 않으면 체커 결함 —
    이 테스트 자체가 실패해야 한다(DoD: "negative test로 위반 샘플이 통과하면 실패")."""
    found = _find_banned_imports(banned_source, BANNED_MODULES)
    assert found, f"위반 샘플이 탐지되지 않음(체커 결함): {banned_source!r}"


@pytest.mark.parametrize(
    "clean_source",
    [
        "from decimal import Decimal\n",
        "from dataclasses import dataclass\n",
        "import re\n",
        "from __future__ import annotations\n",
    ],
)
def test_clean_sample_is_not_flagged(clean_source: str) -> None:
    """오탐 방지 회귀: 허용된 import까지 금지 목록에 걸리면 domain 코드를 계속 못 쓰게 된다."""
    assert _find_banned_imports(clean_source, BANNED_MODULES) == []
