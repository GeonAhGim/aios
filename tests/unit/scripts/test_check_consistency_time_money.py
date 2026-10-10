"""Timezone and monetary precision rules -- task-10846, CONSIST-1.

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
# 9. naive_datetime
# ---------------------------------------------------------------------------


def test_naive_datetime_flags_now_without_tz(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.now()\n")
    hits = cc.check_naive_datetime(tmp_path)
    assert hits == [("src/foo.py", 2)]


def test_naive_datetime_passes_with_tz(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "import datetime\nx = datetime.datetime.now(datetime.timezone.utc)\n",
    )
    assert cc.check_naive_datetime(tmp_path) == []


def test_naive_datetime_flags_utcnow_always(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
    assert cc.check_naive_datetime(tmp_path) == [("src/foo.py", 2)]


# ---------------------------------------------------------------------------
# 10. money_float
# ---------------------------------------------------------------------------


def test_money_float_flags_float_annotation(tmp_path: Path) -> None:
    _write(tmp_path, "src/foo.py", "def f(amount: float) -> None:\n    pass\n")
    hits = cc.check_money_float(tmp_path)
    assert hits == [("src/foo.py", 1)]


def test_money_float_passes_with_decimal(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/foo.py",
        "from decimal import Decimal\ndef f(amount: Decimal) -> None:\n    pass\n",
    )
    assert cc.check_money_float(tmp_path) == []


def test_money_float_regression_task_3828_sites_stay_clean() -> None:
    """task-3828 회귀 가드 -- CI(ci/48ec858bf68c) 적색의 실제 원인이었던 세 필드
    (`LiquidationPolicy.max_slice_notional`, `metrics_registry.Counter/Gauge`의
    `amount` 파라미터, `StrategyIntent.qty`)가 다시 float로 되돌아가면 실제
    저장소(tmp_path 합성이 아니라 ROOT)를 스캔하는 이 테스트가 즉시 잡는다."""
    hits = dict(cc.check_money_float(ROOT))
    assert "src/core/loader/risk_policy_loader.py" not in hits
    assert "src/core/observability/metrics_registry.py" not in hits
    assert "src/core/script/runtime/builtins_strategy.py" not in hits


def test_money_float_regression_task_4188_krx_data_stays_clean() -> None:
    """task-4188 회귀 가드 -- money_float이 10 -> 13으로 재발한 원인은 RD-12
    산출물 `src/foundation/market_data/adapters/krx_data.py`의 `IndexQuote.price`/
    `IndexPoint.price`/`ShortResistanceStock.price` 세 필드가 float로 선언된
    것이었다(mandates/contracts, risk/contracts의 나머지 10건은 OpenAPI 응답
    스키마라 task-3828 때부터 baseline 부채로 남겨둔 것과 별개). 세 필드를
    다시 float로 되돌리면 실제 저장소를 스캔하는 이 테스트가 즉시 잡는다."""
    hits = dict(cc.check_money_float(ROOT))
    assert "src/foundation/market_data/adapters/krx_data.py" not in hits


def test_money_float_wire_boundary_allow_suppresses_annotated_field(tmp_path: Path) -> None:
    """task-5762 -- a v1 wire contract may keep a money-shaped field `float`
    (compatibility surface, ADR-2026-09-10-C P5) if the line explicitly
    marks the reason. Without the marker the same field still flags."""
    _write(
        tmp_path,
        "src/contracts/v1.py",
        "class Foo:\n"
        "    # ratchet-allow: wire-boundary: v1 wire float, Decimal at boundary\n"
        "    amount: float\n",
    )
    assert cc.check_money_float(tmp_path) == []


def test_money_float_wire_boundary_allow_requires_explicit_marker(tmp_path: Path) -> None:
    """The marker text is not free-form -- an unrelated comment on the
    field's line or the line above must not suppress the hit (task-5762)."""
    _write(
        tmp_path,
        "src/contracts/v1.py",
        "class Foo:\n    # some unrelated comment\n    amount: float\n",
    )
    hits = cc.check_money_float(tmp_path)
    assert hits == [("src/contracts/v1.py", 3)]


def test_money_float_regression_task_5762_mandates_risk_contracts_stay_clean() -> None:
    """task-5762 회귀 가드 -- QA(task-5117)가 발견한 mandates/contracts/v1.py,
    risk/contracts/v1.py의 8개 S등급 필드가 다시 무표시 float로 돌아가면(즉
    `# ratchet-allow: wire-boundary:` 주석 없이) 실제 저장소를 스캔하는 이
    테스트가 즉시 잡는다. 계약 v1 타입 자체는 유지하고(CTO 결정,
    2026-09-23), Decimal 정정은 application 경계(evaluate_policy.py,
    create_draft_mandate.py, bundle_loader.py, personal.py)에 있다."""
    hits = dict(cc.check_money_float(ROOT))
    assert "src/foundation/mandates/contracts/v1.py" not in hits
    assert "src/foundation/risk/contracts/v1.py" not in hits
