"""UX-10 적대적 — UX-A2: what-if 경로(`preview_order.py`)는 어떤 쓰기도
하지 않는다(정적 검사 + 적대적 테스트).

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §4 UX-A2,
§9 UX-10 ("쓰기 0 정적 검사 + 적대적"). `tests/adversarial/risk/
test_no_llm_in_risk_path.py`(R-56 I9)·`tests/adversarial/compliance/
test_rule_purity.py`(CM-A2)와 동형 3계층 증명:

1. 정적 — `preview_order.py`의 AST에 `async def`/`await`/쓰기 동사 호출
   (`insert_*`/`update_*`/`delete_*`/`save`/`persist`/`upsert`/`commit`/
   `execute`)이 0건임을 직접 스캔으로 단언하고, `src.*` import를 추이적으로
   따라가며 DB 드라이버(`asyncpg`/`psycopg`/`psycopg2`/`sqlalchemy`/
   `aiosqlite`)에 닿는 경로가 0건임을 단언한다(R-56과 동형 BFS).
2. 런타임 — DB 드라이버 연결 시도를 몽키패치해 호출 시 예외를 던지게 만든
   다음 `preview_order()`를 실제 입력으로 실행해 트랩이 0회 발동함을
   확인한다(AST가 못 보는 경로의 2차 방어).
3. 위조 주입 negative — 임시 파일에 위반(async def·await·쓰기 동사 호출·
   금지 import)을 심어 체커가 반드시 잡는지 확인한다(체커 결함이면 이
   테스트 자체가 실패한다). 마지막으로 실제 `preview_order.py` 소스에
   쓰기 동사 호출 한 줄을 주입한 사본으로 게이트가 실제로 적색이 됨을
   재현한다(D2 "게이트 적색 재현").
"""

from __future__ import annotations

import ast
from collections import deque
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TARGET = ROOT / "src/foundation/whatif/application/preview_order.py"

BANNED_MODULES: frozenset[str] = frozenset(
    {"asyncpg", "psycopg", "psycopg2", "sqlalchemy", "aiosqlite"}
)
_DYNAMIC_IMPORT_CALLS = frozenset({"__import__", "import_module"})
_WRITE_VERB_PREFIXES = (
    "insert",
    "update",
    "delete",
    "save",
    "persist",
    "upsert",
    "commit",
    "write",
)
_WRITE_VERB_EXACT = frozenset({"execute", "executemany"})

Violation = tuple[str, str]  # (kind, detail)


# ---------------------------------------------------------------------------
# 1. Static AST checks — direct scan + transitive import graph
# ---------------------------------------------------------------------------


def _is_write_verb_call(name: str) -> bool:
    if name in _WRITE_VERB_EXACT:
        return True
    return any(name == verb or name.startswith(verb + "_") for verb in _WRITE_VERB_PREFIXES)


def find_direct_violations(tree: ast.AST) -> list[Violation]:
    """AST가 파일 하나 안에서 직접 잡을 수 있는 것: async def/await/쓰기
    동사 호출. 순수 함수(파일 읽기만) — I/O 없음."""
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            violations.append(("async_def", node.name))
        elif isinstance(node, ast.Await):
            violations.append(("await", ast.dump(node)[:80]))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name is not None and _is_write_verb_call(name):
                violations.append(("write_call", name))
    return violations


def _imported_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if callee in _DYNAMIC_IMPORT_CALLS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.append(first.value)
    return names


def _matches_banned(module: str, banned: frozenset[str]) -> str | None:
    for name in banned:
        if module == name or module.startswith(name + "."):
            return name
    return None


def _module_to_path(module: str, root: Path) -> Path | None:
    parts = module.split(".")
    while parts:
        candidate = root.joinpath(*parts)
        if candidate.is_dir() and (candidate / "__init__.py").exists():
            return candidate / "__init__.py"
        if candidate.with_suffix(".py").exists():
            return candidate.with_suffix(".py")
        parts.pop()
    return None


def scan_import_graph(
    start_file: Path, root: Path, banned: frozenset[str]
) -> tuple[list[tuple[str, str, tuple[str, ...]]], set[Path]]:
    """`start_file`에서 출발해 `src.*` import를 BFS로 추이적으로 따라가며
    `banned` 모듈 도달을 수집한다(R-56/CM-A2와 동형). 순수 함수."""
    parent: dict[Path, Path | None] = {start_file: None}
    queue: deque[Path] = deque([start_file])
    violations: list[tuple[str, str, tuple[str, ...]]] = []

    def chain(path: Path) -> tuple[str, ...]:
        links: list[str] = []
        cursor: Path | None = path
        while cursor is not None:
            links.append(cursor.relative_to(root).as_posix())
            cursor = parent[cursor]
        return tuple(reversed(links))

    while queue:
        path = queue.popleft()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in _imported_names(tree):
            hit = _matches_banned(module, banned)
            if hit is not None:
                violations.append((path.relative_to(root).as_posix(), module, chain(path)))
                continue
            if not module.startswith("src."):
                continue
            target = _module_to_path(module, root)
            if target is not None and target not in parent:
                parent[target] = path
                queue.append(target)
    return violations, set(parent)


def test_guarded_file_exists() -> None:
    assert TARGET.exists(), f"UX-A2 보호 대상이 없음(스펙 §9 UX-10 갱신 필요): {TARGET}"


def test_preview_order_has_zero_write_constructs_directly() -> None:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))
    violations = find_direct_violations(tree)
    assert not violations, f"UX-A2 위반 — preview_order.py에 쓰기 구성요소 발견: {violations}"


def test_preview_order_reaches_no_db_driver_transitively() -> None:
    violations, reached = scan_import_graph(TARGET, ROOT, BANNED_MODULES)
    assert not violations, "UX-A2 위반 — preview_order.py에서 DB 드라이버 도달:\n" + "\n".join(
        f"{f} imports {m} via {' -> '.join(c)}" for f, m, c in violations
    )
    # 추이 탐색이 실제로 간선을 따라갔는지(출발 파일만 보고 끝나지 않았는지) 확인.
    assert len(reached) > 1
    assert TARGET in reached


# ---------------------------------------------------------------------------
# 2. Runtime trap — actually run preview_order() and prove no DB primitive
#    was ever touched.
# ---------------------------------------------------------------------------


class _DbAccessError(RuntimeError):
    """UX-A2 위반 — preview_order 실행 중 DB 원시 함수가 호출됐다."""


def test_runtime_execution_touches_no_db_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from datetime import datetime, timezone
    from decimal import Decimal
    from uuid import UUID

    from src.core.portfolio.state_input import PortfolioAggregate
    from src.core.risk.inputs import (
        ActivityInputs,
        EquityInputs,
        ExposureSnapshot,
        OrderIntent,
        RiskInputs,
        SafetyInputs,
        StatsInputs,
    )
    from src.foundation.mandates.contracts.v1 import (
        Autonomy,
        MandateRevisionState,
        MandateRevisionView,
    )
    from src.foundation.whatif.application.preview_order import preview_order
    from src.foundation.whatif.domain.impact import ProposedTrade

    calls: list[str] = []

    def _trap(name: str):
        def _raise(*_args: object, **_kwargs: object) -> object:
            calls.append(name)
            raise _DbAccessError(f"UX-A2 violation: {name} invoked during preview_order")

        return _raise

    try:
        import asyncpg

        monkeypatch.setattr(asyncpg, "connect", _trap("asyncpg.connect"), raising=False)
        monkeypatch.setattr(asyncpg, "create_pool", _trap("asyncpg.create_pool"), raising=False)
    except ImportError:
        pass

    # asyncio.run/ensure_future would only be needed if this module ever
    # tried to schedule I/O — trap them too so a hidden `async def` sneaking
    # past the AST scan (e.g. added dynamically) would still be caught.
    monkeypatch.setattr(asyncio, "run", _trap("asyncio.run"))

    now = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
    before = PortfolioAggregate(
        total_equity=Decimal("1000000"),
        per_symbol_pct={"AAPL": Decimal("30")},
        per_strategy_pct={"STRAT_A": Decimal("30")},
        total_exposure_pct=Decimal("30"),
        cash_pct=Decimal("70"),
        as_of=now,
    )
    trade = ProposedTrade(
        symbol="AAPL", notional=Decimal("50000"), side="BUY", strategy_id="STRAT_A"
    )
    risk_inputs = RiskInputs(
        tenant_id=UUID(int=7),
        execution_ref="exec:1",
        certified_badge=None,
        allocated_capital=None,
        intent=OrderIntent(
            symbol="AAPL",
            asset_class="EQUITY",
            side="BUY",
            quantity=Decimal("100"),
            ref_price=Decimal("500"),
            notional=Decimal("50000"),
            reduce_only=False,
            strategy_id="STRAT_A",
            strategy_version="v1",
            capital_pct=Decimal("5"),
        ),
        equity=EquityInputs(as_of=now, total_equity=Decimal("1000000")),
        exposure=ExposureSnapshot(as_of=now),
        stats=StatsInputs(as_of=now),
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        as_of=now,
    )
    mandate = MandateRevisionView(
        id=UUID(int=1),
        mandate_id=UUID(int=2),
        revision_no=1,
        state=MandateRevisionState.ACTIVE,
        max_total_exposure_pct=100.0,
        max_single_instrument_pct=100.0,
        min_cash_buffer_pct=0.0,
        max_daily_loss_pct=100.0,
        allowed_autonomy=Autonomy.PAPER,
        forbidden_assets=[],
        revision_hash="a" * 64,
        cooling_off_started_at=None,
        created_at=None,
        activated_at=None,
    )

    result = preview_order(
        before=before,
        trade=trade,
        risk_inputs=risk_inputs,
        risk_limits=(),
        mandate=mandate,
        now=now,
    )

    assert calls == [], f"UX-A2 위반 — 실행 중 DB 원시 함수 호출됨: {calls}"
    assert result.would_be_denied_by == ()


# ---------------------------------------------------------------------------
# 3. 위조 주입 negative — 체커 결함 방지
# ---------------------------------------------------------------------------

_CLEAN_HELPER = "def compute():\n    return 1\n"
_DIRECT_INJECTIONS: dict[str, str] = {
    "async_def": "async def handler():\n    return 1\n",
    "await_expr": "async def handler():\n    x = await something()\n    return x\n",
    "insert_call": "def handler(repo):\n    repo.insert_policy_decision(1)\n",
    "update_call": "def handler(repo):\n    repo.update_mandate(1)\n",
    "save_call": "def handler(obj):\n    obj.save()\n",
    "commit_call": "def handler(session):\n    session.commit()\n",
    "execute_call": "def handler(conn):\n    conn.execute('INSERT INTO x VALUES (1)')\n",
}


@pytest.mark.parametrize("injection", sorted(_DIRECT_INJECTIONS))
def test_negative_direct_violation_is_detected(injection: str) -> None:
    tree = ast.parse(_DIRECT_INJECTIONS[injection])
    violations = find_direct_violations(tree)
    assert violations, f"체커 결함 — 위조 주입({injection})을 놓침"


def test_negative_control_clean_source_has_no_direct_violation() -> None:
    tree = ast.parse(_CLEAN_HELPER)
    assert find_direct_violations(tree) == []


_IMPORT_INJECTIONS: dict[str, str] = {
    "direct_asyncpg": "import asyncpg\n",
    "from_sqlalchemy": "from sqlalchemy import create_engine\n",
    "psycopg2_submodule": "import psycopg2.extras\n",
    "dynamic_import_module": "import importlib\nclient = importlib.import_module('asyncpg')\n",
    "dynamic_dunder_import": "mod = __import__('aiosqlite')\n",
}


def _fake_tree(tmp_path: Path, helper_source: str) -> tuple[Path, Path]:
    (tmp_path / "src/foundation/whatif/application").mkdir(parents=True)
    (tmp_path / "src/services").mkdir(parents=True)
    for pkg in (
        "src",
        "src/foundation",
        "src/foundation/whatif",
        "src/foundation/whatif/application",
        "src/services",
    ):
        (tmp_path / pkg / "__init__.py").write_text("", encoding="utf-8")
    target = tmp_path / "src/foundation/whatif/application/preview_order.py"
    target.write_text("from src.services.helper import compute\n", encoding="utf-8")
    (tmp_path / "src/services/helper.py").write_text(
        helper_source + "\ndef compute():\n    return 1\n", encoding="utf-8"
    )
    return target, tmp_path


@pytest.mark.parametrize("injection", sorted(_IMPORT_INJECTIONS))
def test_negative_injected_db_import_is_detected_through_intermediate_module(
    tmp_path: Path, injection: str
) -> None:
    target, root = _fake_tree(tmp_path, _IMPORT_INJECTIONS[injection])
    violations, _ = scan_import_graph(target, root, BANNED_MODULES)
    assert violations, f"체커 결함 — 위조 주입({injection})을 놓침"
    file, _module, chain = violations[0]
    assert file == "src/services/helper.py"
    assert chain == (
        "src/foundation/whatif/application/preview_order.py",
        "src/services/helper.py",
    )


def test_negative_control_clean_tree_has_no_import_violation(tmp_path: Path) -> None:
    target, root = _fake_tree(tmp_path, "import decimal\n")
    violations, reached = scan_import_graph(target, root, BANNED_MODULES)
    assert violations == []
    assert root / "src/services/helper.py" in reached


# ---------------------------------------------------------------------------
# D2 "게이트 적색 재현": 실제 preview_order.py 소스에 쓰기 동사 호출을 주입한
# 사본을 스캔하면 게이트가 실제로 적색(위반 검출)이 된다.
# ---------------------------------------------------------------------------


def test_gate_turns_red_when_a_write_call_is_injected_into_the_real_file(
    tmp_path: Path,
) -> None:
    real_source = TARGET.read_text(encoding="utf-8")
    poisoned = real_source + "\n\ndef _leak(repo):\n    repo.insert_policy_decision(1)\n"
    poisoned_file = tmp_path / "preview_order_poisoned.py"
    poisoned_file.write_text(poisoned, encoding="utf-8")

    # Sanity: the real, unmodified file is clean.
    assert find_direct_violations(ast.parse(real_source)) == []

    # The poisoned copy reproduces a red gate.
    violations = find_direct_violations(ast.parse(poisoned))
    assert violations, "게이트 적색 재현 실패 — 주입된 쓰기 호출을 체커가 놓침"
    assert ("write_call", "insert_policy_decision") in violations
