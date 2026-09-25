"""scripts/check_import_linter.py 단위 테스트 -- RATCHET-2, task-3256.

4종 계약(forbidden/forbidden_suffix/boundary/cycles)을 tmp_path에 합성한
`src/` 트리 + `.importlinter`로 각각 검증하고, baseline 래칫(증가=exit 2)을
재현한다. D2 DoD: negative test 3건 이상, 실패주입 1건(계약 파일 파싱 오류),
성능 단언 1건, 게이트 적색 재현 1건.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cil = _load_module("check_import_linter", SCRIPTS_DIR / "check_import_linter.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_contracts(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".importlinter"
    path.write_text(content, encoding="utf-8")
    return path


def _touch_pkg(tmp_path: Path, *parts: str) -> None:
    for i in range(1, len(parts) + 1):
        pkg_dir = tmp_path.joinpath(*parts[:i])
        pkg_dir.mkdir(parents=True, exist_ok=True)
        init = pkg_dir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_contracts
# ---------------------------------------------------------------------------


def test_parse_contracts_reads_all_four_kinds(tmp_path: Path) -> None:
    path = _write_contracts(
        tmp_path,
        "[forbidden:a]\nsource = src.core\nforbidden =\n    src.exchanges\n\n"
        "[forbidden_suffix:b]\nroot = src.foundation\nsource_suffix = domain\n"
        "forbidden_suffix = adapters\n\n"
        "[boundary:c]\nroot = src.foundation\ninternal_suffixes =\n    domain\n    adapters\n\n"
        "[cycles:d]\nroot = src\n",
    )
    contracts = cil.parse_contracts(path)
    assert [c["kind"] for c in contracts] == ["forbidden", "forbidden_suffix", "boundary", "cycles"]


def test_parse_contracts_missing_file_raises(tmp_path: Path) -> None:
    """실패주입: 계약 파일이 없으면 크래시 대신 ImportLinterError로 fail-closed."""
    with pytest.raises(cil.ImportLinterError):
        cil.parse_contracts(tmp_path / "nope.importlinter")


def test_parse_contracts_bad_section_name_raises(tmp_path: Path) -> None:
    path = _write_contracts(tmp_path, "[not_a_valid_section]\nx = 1\n")
    with pytest.raises(cil.ImportLinterError):
        cil.parse_contracts(path)


def test_parse_contracts_unknown_kind_raises(tmp_path: Path) -> None:
    path = _write_contracts(tmp_path, "[bogus:a]\nx = 1\n")
    with pytest.raises(cil.ImportLinterError):
        cil.parse_contracts(path)


# ---------------------------------------------------------------------------
# forbidden
# ---------------------------------------------------------------------------


def test_forbidden_flags_core_importing_forbidden_prefix(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src")
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/engine.py", "import src.exchanges.bitget\n")
    graph = cil.build_graph(tmp_path)
    contract = {"kind": "forbidden", "source": "src.core", "forbidden": ["src.exchanges"]}
    hits = cil._eval_forbidden(graph, contract)
    assert len(hits) == 1
    assert "src.core.engine" in hits[0][0]


def test_forbidden_passes_when_no_forbidden_import(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src")
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/engine.py", "import json\n")
    graph = cil.build_graph(tmp_path)
    contract = {"kind": "forbidden", "source": "src.core", "forbidden": ["src.exchanges"]}
    assert cil._eval_forbidden(graph, contract) == []


# ---------------------------------------------------------------------------
# forbidden_suffix (domain -> adapters 역의존)
# ---------------------------------------------------------------------------


def test_forbidden_suffix_flags_domain_importing_same_aggregate_adapters(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src", "foundation", "positions", "domain")
    _touch_pkg(tmp_path, "src", "foundation", "positions", "adapters")
    _write(
        tmp_path,
        "src/foundation/positions/domain/rules.py",
        "import src.foundation.positions.adapters.repo\n",
    )
    graph = cil.build_graph(tmp_path)
    contract = {
        "kind": "forbidden_suffix",
        "root": "src.foundation",
        "source_suffix": "domain",
        "forbidden_suffix": "adapters",
    }
    hits = cil._eval_forbidden_suffix(graph, contract)
    assert len(hits) == 1


def test_forbidden_suffix_ignores_cross_aggregate_adapters(tmp_path: Path) -> None:
    """다른 애그리게잇의 adapters는 이 계약(같은 애그리게잇 역의존) 대상이 아니다
    -- boundary 계약이 별도로 담당한다."""
    _touch_pkg(tmp_path, "src", "foundation", "positions", "domain")
    _touch_pkg(tmp_path, "src", "foundation", "ledger", "adapters")
    _write(
        tmp_path,
        "src/foundation/positions/domain/rules.py",
        "import src.foundation.ledger.adapters.repo\n",
    )
    graph = cil.build_graph(tmp_path)
    contract = {
        "kind": "forbidden_suffix",
        "root": "src.foundation",
        "source_suffix": "domain",
        "forbidden_suffix": "adapters",
    }
    assert cil._eval_forbidden_suffix(graph, contract) == []


# ---------------------------------------------------------------------------
# boundary (foundation 경계)
# ---------------------------------------------------------------------------


def test_boundary_flags_cross_aggregate_domain_import(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src", "foundation", "allocation", "application")
    _touch_pkg(tmp_path, "src", "foundation", "ledger", "domain")
    _write(
        tmp_path,
        "src/foundation/allocation/application/allocate.py",
        "import src.foundation.ledger.domain.chart_of_accounts\n",
    )
    graph = cil.build_graph(tmp_path)
    contract = {
        "kind": "boundary",
        "root": "src.foundation",
        "internal_suffixes": ["domain", "adapters"],
    }
    hits = cil._eval_boundary(graph, contract)
    assert len(hits) == 1


def test_boundary_allows_cross_aggregate_application_import(tmp_path: Path) -> None:
    """application/(공개 표면으로 취급)을 경유한 교차 애그리게잇 임포트는 허용."""
    _touch_pkg(tmp_path, "src", "foundation", "allocation", "application")
    _touch_pkg(tmp_path, "src", "foundation", "ledger", "application")
    _write(
        tmp_path,
        "src/foundation/allocation/application/allocate.py",
        "import src.foundation.ledger.application.post_entry\n",
    )
    graph = cil.build_graph(tmp_path)
    contract = {
        "kind": "boundary",
        "root": "src.foundation",
        "internal_suffixes": ["domain", "adapters"],
    }
    assert cil._eval_boundary(graph, contract) == []


# ---------------------------------------------------------------------------
# cycles
# ---------------------------------------------------------------------------


def test_cycles_detects_two_module_cycle(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/a.py", "import src.core.b\n")
    _write(tmp_path, "src/core/b.py", "import src.core.a\n")
    graph = cil.build_graph(tmp_path)
    contract = {"kind": "cycles", "root": "src"}
    hits = cil._eval_cycles(graph, contract)
    assert len(hits) == 1
    assert "순환" in hits[0][2]


def test_cycles_passes_on_acyclic_graph(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/a.py", "import src.core.b\n")
    _write(tmp_path, "src/core/b.py", "x = 1\n")
    graph = cil.build_graph(tmp_path)
    contract = {"kind": "cycles", "root": "src"}
    assert cil._eval_cycles(graph, contract) == []


def test_cycles_resolves_relative_imports(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/a.py", "from . import b\n")
    _write(tmp_path, "src/core/b.py", "from .a import x\n")
    graph = cil.build_graph(tmp_path)
    contract = {"kind": "cycles", "root": "src"}
    hits = cil._eval_cycles(graph, contract)
    assert len(hits) == 1


# ---------------------------------------------------------------------------
# main() -- 래칫 DoD 시나리오
# ---------------------------------------------------------------------------


def _scenario(tmp_path: Path) -> None:
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/engine.py", "x = 1\n")
    _write_contracts(tmp_path, "[forbidden:a]\nsource = src.core\nforbidden =\n    src.exchanges\n")


def test_first_run_initializes_baseline(tmp_path: Path) -> None:
    _scenario(tmp_path)
    baseline_path = tmp_path / "import-linter-baseline.json"

    exit_code = cil.main(
        [
            "--root",
            str(tmp_path),
            "--contracts",
            str(tmp_path / ".importlinter"),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == {"a": 0}


def test_increase_fails_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """게이트 적색 재현: 신규 forbidden import가 생기면 exit 2."""
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/engine.py", "import src.exchanges.bitget\n")
    _write_contracts(tmp_path, "[forbidden:a]\nsource = src.core\nforbidden =\n    src.exchanges\n")
    baseline_path = tmp_path / "import-linter-baseline.json"
    baseline_path.write_text(json.dumps({"a": 0}), encoding="utf-8")

    exit_code = cil.main(
        [
            "--root",
            str(tmp_path),
            "--contracts",
            str(tmp_path / ".importlinter"),
            "--baseline",
            str(baseline_path),
        ]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "0개 -> 1개" in out
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["a"] == 0


def test_malformed_contracts_file_fails_with_input_error(tmp_path: Path) -> None:
    """실패주입: .importlinter가 필수 키(source)를 빠뜨리면 exit 1(크래시 아님)."""
    _touch_pkg(tmp_path, "src", "core")
    _write(tmp_path, "src/core/engine.py", "x = 1\n")
    _write_contracts(tmp_path, "[forbidden:a]\nforbidden =\n    src.exchanges\n")

    exit_code = cil.main(
        [
            "--root",
            str(tmp_path),
            "--contracts",
            str(tmp_path / ".importlinter"),
            "--baseline",
            str(tmp_path / "import-linter-baseline.json"),
        ]
    )

    assert exit_code == 1


def test_decrease_without_update_leaves_baseline_unchanged(tmp_path: Path) -> None:
    _scenario(tmp_path)
    baseline_path = tmp_path / "import-linter-baseline.json"
    baseline_path.write_text(json.dumps({"a": 2}), encoding="utf-8")

    exit_code = cil.main(
        [
            "--root",
            str(tmp_path),
            "--contracts",
            str(tmp_path / ".importlinter"),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(baseline_path.read_text(encoding="utf-8"))["a"] == 2


# ---------------------------------------------------------------------------
# 성능 단언
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_build_graph_throughput_budget(tmp_path: Path) -> None:
    """300개 모듈 임포트 그래프 구성이 5초 예산 안에 끝난다(D2 DoD 성능 단언)."""
    _touch_pkg(tmp_path, "src", "foundation")
    for i in range(300):
        _write(
            tmp_path,
            f"src/foundation/mod_{i}.py",
            f"import json\nimport src.foundation.mod_{(i + 1) % 300}\n",
        )

    start = time.perf_counter()
    graph = cil.build_graph(tmp_path)
    elapsed = time.perf_counter() - start

    assert len(graph) >= 300
    assert elapsed < 5.0, f"build_graph took {elapsed:.2f}s for 300 modules (budget 5.0s)"
