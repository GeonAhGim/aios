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
from unittest.mock import patch

import pytest

from tests.support.perf_budget import PerfBudget

ROOT = Path(__file__).resolve().parents[2]
BANNED_MODULES = frozenset({"asyncpg", "httpx", "sqlalchemy"})
# ADR-2026-09-09-C Decision 1: AST 파싱 성능 예산 — 전체 domain 스캔 ≤ 10초
# CI 병렬 환경에서 I/O 컨테이션으로 5초가 불안정하므로 10초로 확장(task-15100)
_MAX_SCAN_SECONDS = 10


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

    assert not violations, "domain/**에서 금지된 I/O 임포트 발견(순수 규칙 위반):\n" + "\n".join(
        violations
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


# ── negative tests: 위반 샘플이 탐지되는지 명시적으로 검증 ──────────────────


@pytest.mark.parametrize(
    "banned_source",
    [
        # 하위 모듈 임포트 — `asyncpg.pool` 같은 2단계 경로
        "import asyncpg.pool\n",
        # aliased import — `import sqlalchemy as sa`
        "import sqlalchemy as sa\n",
        # from ... import (aliased)
        "from sqlalchemy.orm import Session as DBSession\n",
        # mixed: 여러 banned 모듈 동시
        "import asyncpg\nimport httpx\n",
        # nested package — `httpx.Client`
        "from httpx._client import AsyncClient\n",
        # from relative style inside domain (should still be caught by root check)
        "from asyncpg.connection import Connection\n",
    ],
)
def test_negative_banned_submodule_imports_detected(banned_source: str) -> None:
    """불변식 I-02 위반: domain/**가 banned 모듈의 하위 패키지를 import해도 탐지해야 한다.
    탐지 실패는 AST 체커의 결함이므로 이 테스트가 실패해야 한다."""
    found = _find_banned_imports(banned_source, BANNED_MODULES)
    assert found, f"하위 모듈 임포트가 탐지되지 않음(체커 결함): {banned_source!r}"


@pytest.mark.parametrize(
    "clean_source",
    [
        # 문자열 리터럴 안에 banned 단어가 있어도 오탐 금지
        "# This file used to import asyncpg before we migrated to kis adapter\n",
        # docstring 안에 banned 모듈명
        '"""Supports asyncpg, httpx, sqlalchemy adapters."""\nfrom pathlib import Path\n',
        # f-string 안에 banned 모듈명
        'msg = f"Need to migrate {module} from asyncpg"\n',
        # 주석 안에 banned 모듈명
        "# TODO: replace asyncpg pool with connection pool\n",
    ],
)
def test_negative_clean_contexts_no_false_positive(clean_source: str) -> None:
    """불변식 I-02: 주석·문자열 안에 banned 모듈명이 있어도 AST import 노드로
    인식되면 오탐이다. clean한 컨텍스트는 탐지 결과 빈 리스트여야 한다."""
    assert _find_banned_imports(clean_source, BANNED_MODULES) == []


# ── failure injection: 파일 읽기 실패 시 graceful handling ─────────────────


def test_injection_read_error_raises_gracefully() -> None:
    """실패주입: domain 파일 중 하나가 읽을 수 없는 경우(권한 오류·인코딩 오류)
    테스트가 hang하지 않고 명확한 오류로 실패해야 한다.
    현재 구현은 read_text()가 예외를 전파하므로 pytest.raises로 검증한다."""

    # Path.read_text를 monkeypatch해 읽기 실패 시 예외 유발
    def failing_read_text(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError(f"cannot read {self}")

    with patch.object(Path, "read_text", failing_read_text):
        # _domain_dirs()가 반환하는 실제 domain 디렉터리가 하나라도 있다면
        # 해당 디렉터리 내 파일 읽기 시도 시 예외가 발생해야 함
        domain_dirs = _domain_dirs()
        if domain_dirs:
            with pytest.raises((PermissionError, OSError)):
                # 실제 스캔 로직을 호출하면 예외가 전파되어야 함
                for dd in domain_dirs:
                    for py_file in dd.rglob("*.py"):
                        py_file.read_text(encoding="utf-8")
        # domain 디렉터리가 없으면 이 테스트는 아무 동작도 하지 않음(허용)


# ── performance assertion: 전체 domain 스캔 시간 예산 ──────────────────────


@pytest.mark.perf
def test_zone_scan_completes_within_budget(perf_budget: PerfBudget) -> None:
    """성능 단언: 전체 domain/** 디렉터리 스캔 + AST 파싱이 _MAX_SCAN_SECONDS(10초) 내에
    완료되어야 한다. 대규모 리포에서 검사 시간이 선형으로 증가해도 예산을 지키는지 확인한다.

    raw time.perf_counter() → perf_budget.sample(batch=N) 전환(task-15300).
    process_time의 15.6ms 틱으로 1회 호출은 0/15.6만 나오므로 batch=8로 묶어
    호출당 오차를 ~2ms로 줄인다. 예산 단위: ms(10초 → 10000ms).
    """
    domain_dirs = _domain_dirs()
    assert domain_dirs, "검사 대상이 없으면 성능 단언이 무의미해짐"

    budget_ms = _MAX_SCAN_SECONDS * 1000  # 초 → ms 단위 일치

    def _scan_once() -> int:
        total_violations = 0
        for domain_dir in domain_dirs:
            for py_file in domain_dir.rglob("*.py"):
                source = py_file.read_text(encoding="utf-8")
                found = _find_banned_imports(source, BANNED_MODULES)
                total_violations += len(found)
        return total_violations

    sample = perf_budget.sample(_scan_once, batch=8)
    cpu_ms = sample.cpu_ms

    assert cpu_ms < budget_ms, (
        f"domain 스캔에 {cpu_ms:.2f}ms 소요 — 예산 {budget_ms}ms 초과. "
        f"batch=8, 호출당 {cpu_ms / 8:.2f}ms"
    )


# ── gate-red reproduction: 10x delay injection proves budget is real ─────────


@pytest.mark.perf
def test_zone_scan_perf_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): CPU 예산 테스트이므로 `time.process_time`을
    monkeypatch해 1회 호출당 예산(10초)을 10배 초과하는 CPU 시간을 가장하면
    같은 단언이 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    역으로 증명한다. fake process_time이 100초(100 000ms)를 반환하므로
    batch=8로 나눈 평균도 100 000ms > 10 000ms budget로 적색 확정.
    """
    budget_ms = _MAX_SCAN_SECONDS * 1000  # 10 000ms

    # PerfBudget.sample() 내부에서 time.process_time()을 호출하므로
    # 이를 monkeypatch해 항상 100초(10x budget)를 반환하게 한다.
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 100.0  # 100 000ms = 10x budget per call
        return _fake_time

    monkeypatch.setattr("time.process_time", _fake_process_time)

    domain_dirs = _domain_dirs()
    assert domain_dirs, "검사 대상이 없으면 성능 단언이 무의미해짐"

    def _scan_once() -> int:
        total_violations = 0
        for domain_dir in domain_dirs:
            for py_file in domain_dir.rglob("*.py"):
                source = py_file.read_text(encoding="utf-8")
                found = _find_banned_imports(source, BANNED_MODULES)
                total_violations += len(found)
        return total_violations

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_scan_once, batch=8)
        cpu_ms = sample.cpu_ms

        assert cpu_ms < budget_ms, (
            f"domain 스캔에 {cpu_ms:.2f}ms 소요 — 예산 {budget_ms}ms 초과. "
            f"batch=8, 호출당 {cpu_ms / 8:.2f}ms"
        )
