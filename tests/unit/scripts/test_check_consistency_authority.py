"""Identifier assembly and authority ownership -- task-10846, CONSIST-1.

Tests stay grouped by the consistency checker responsibility.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_consistency", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path



# ---------------------------------------------------------------------------
# 11. symbol_id_assembly
# ---------------------------------------------------------------------------


def test_symbol_assembly_flags_fstring(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        'def build(venue, code):\n    symbol = f"{venue}:{code}"\n    return symbol\n',
    )
    hits = cc.check_symbol_id_assembly(tmp_path)
    assert hits == [("src/foo.py", 2)]


def test_symbol_assembly_passes_on_passthrough(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "def build(record):\n    symbol = record.symbol\n    return symbol\n",
    )
    assert cc.check_symbol_id_assembly(tmp_path) == []


def test_symbol_assembly_exempts_migrations_dir(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/db/migrations/versions/0001_a.py",
        'symbol = venue + ":" + code\n',
    )
    assert cc.check_symbol_id_assembly(tmp_path) == []


# ---------------------------------------------------------------------------
# 13. authority_duplication (RATCHET-2, task-3256)
# ---------------------------------------------------------------------------


def test_authority_duplication_flags_same_context_duplicate_assembly(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foundation/positions/domain/journal_rules.py",
        "def fill_entry(order_id, fill_seq):\n"
        '    idempotency_key = f"fill:{order_id}:{fill_seq}"\n'
        "    return idempotency_key\n",
    )
    _write(
        tmp_path,
        "src/foundation/positions/application/record_fill.py",
        "def check_exists(command):\n"
        '    idempotency_key = f"fill:{command.order_id}:{command.fill_seq}"\n'
        "    return idempotency_key\n",
    )
    hits = cc.check_authority_duplication(tmp_path)
    assert hits == [
        ("src/foundation/positions/application/record_fill.py", 2),
        ("src/foundation/positions/domain/journal_rules.py", 2),
    ]


def test_authority_duplication_passes_on_single_builder_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foundation/ledger/domain/idempotency.py",
        "def idempotency_key(event):\n"
        '    key = f"{event.event_type}:{event.event_ref}"\n'
        "    return key\n",
    )
    assert cc.check_authority_duplication(tmp_path) == []


def test_authority_duplication_passes_across_different_bounded_contexts(tmp_path: Path) -> None:
    """다른 bounded context(서로 다른 애그리게잇)에서 같은 이름을 각자 조립하는
    것은 위반이 아니다 -- 의도적으로 분리된 authority(연구 결과: risk_gate/
    mandates/risk는 3개의 독립된 bounded context)."""
    _write(
        tmp_path,
        "src/foundation/positions/domain/journal_rules.py",
        'def fill_entry(order_id):\n    idempotency_key = f"fill:{order_id}"\n'
        "    return idempotency_key\n",
    )
    _write(
        tmp_path,
        "src/foundation/paper_control/application/apply_safety_control.py",
        "def apply(control_id):\n"
        '    idempotency_key = f"risk-pause:{control_id}"\n'
        "    return idempotency_key\n",
    )
    assert cc.check_authority_duplication(tmp_path) == []


def test_authority_duplication_passes_on_delegation_not_raw_assembly(tmp_path: Path) -> None:
    """캔노니컬 빌더를 호출해 전달받는 것(위임)은 위반이 아니다 -- raw assembly만 잡는다."""
    _write(
        tmp_path,
        "src/foundation/positions/domain/journal_rules.py",
        'def fill_entry(order_id):\n    idempotency_key = f"fill:{order_id}"\n'
        "    return idempotency_key\n",
    )
    _write(
        tmp_path,
        "src/foundation/positions/application/record_fill.py",
        "from src.foundation.positions.domain.journal_rules import fill_entry\n"
        "def check_exists(command):\n"
        "    entry = fill_entry(command.order_id)\n"
        "    idempotency_key = entry.idempotency_key\n"
        "    return idempotency_key\n",
    )
    assert cc.check_authority_duplication(tmp_path) == []


def test_authority_duplication_exempts_migrations_dir(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/db/migrations/versions/0001_a.py",
        'idempotency_key = f"fill:{x}"\n',
    )
    _write(
        tmp_path,
        "src/db/migrations/versions/0002_b.py",
        'idempotency_key = f"fill:{y}"\n',
    )
    assert cc.check_authority_duplication(tmp_path) == []
