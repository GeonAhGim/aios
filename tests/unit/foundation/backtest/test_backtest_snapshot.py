"""L25 -- `cost_model_hash`/`config_hash`/`compute_bar_snapshot_hash` 안정성
+ `BarSnapshotRef` 필수 필드 거부 + `models.py`/`snapshot.py`/`events.py`
순수성(AST) 정적 검사 + 성능 예산 단언 + `.importlinter` 게이트 적색 재현
(D2 하한, ADR-2026-09-09-C Decision 1).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md#§9 L25.
"""

from __future__ import annotations

import ast
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from scripts.check_import_linter import ROOT as LINTER_ROOT
from scripts.check_import_linter import _eval_forbidden_suffix, _imports_of, parse_contracts
from src.data.models.market_data import Candle
from src.foundation.backtest.domain.models import CostModel
from src.foundation.backtest.domain.snapshot import (
    BarSnapshotRef,
    compute_bar_snapshot_hash,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

_DOMAIN_DIR = Path(__file__).resolve().parents[4] / "src" / "foundation" / "backtest" / "domain"
_CHECKED_FILES = ("models.py", "snapshot.py", "events.py")
_FORBIDDEN_IO_MODULES = {"asyncpg", "httpx", "openai", "aiohttp", "requests"}


def _bar(*, index: int, volume: Decimal) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=volume,
        open_time=ts,
        close_time=ts + timedelta(hours=1),
    )


# --------------------------------------------------------------------------
# cost_model_hash stability (DoD c)
# --------------------------------------------------------------------------


def test_cost_model_hash_same_input_is_stable() -> None:
    cm1 = CostModel(fee_bps=Decimal("0.001"), slippage_bps=Decimal("0.002"))
    cm2 = CostModel(fee_bps=Decimal("0.001"), slippage_bps=Decimal("0.002"))
    assert cm1.cost_model_hash() == cm2.cost_model_hash()


def test_cost_model_hash_changes_when_fee_bps_changes() -> None:
    cm1 = CostModel(fee_bps=Decimal("0.001"), slippage_bps=Decimal("0.002"))
    cm2 = CostModel(fee_bps=Decimal("0.0011"), slippage_bps=Decimal("0.002"))
    assert cm1.cost_model_hash() != cm2.cost_model_hash()


def test_cost_model_hash_is_independent_of_kwarg_order() -> None:
    cm1 = CostModel(fee_bps=Decimal("0.001"), slippage_bps=Decimal("0.002"))
    cm2 = CostModel(slippage_bps=Decimal("0.002"), fee_bps=Decimal("0.001"))
    assert cm1.cost_model_hash() == cm2.cost_model_hash()


# --------------------------------------------------------------------------
# bar snapshot hash stability (DoD d)
# --------------------------------------------------------------------------


def test_bar_snapshot_hash_same_input_is_stable() -> None:
    bars = [_bar(index=0, volume=Decimal("10"))]
    h1 = compute_bar_snapshot_hash(bars, source="binance", as_of=_T0)
    h2 = compute_bar_snapshot_hash(bars, source="binance", as_of=_T0)
    assert h1 == h2


def test_bar_snapshot_hash_changes_with_one_unit_volume_diff() -> None:
    bars_a = [_bar(index=0, volume=Decimal("10"))]
    bars_b = [_bar(index=0, volume=Decimal("11"))]
    h_a = compute_bar_snapshot_hash(bars_a, source="binance", as_of=_T0)
    h_b = compute_bar_snapshot_hash(bars_b, source="binance", as_of=_T0)
    assert h_a != h_b


def test_bar_snapshot_hash_stable_across_decimal_trailing_zero_forms() -> None:
    bars_a = [_bar(index=0, volume=Decimal("1.10"))]
    bars_b = [_bar(index=0, volume=Decimal("1.1"))]
    h_a = compute_bar_snapshot_hash(bars_a, source="binance", as_of=_T0)
    h_b = compute_bar_snapshot_hash(bars_b, source="binance", as_of=_T0)
    assert h_a == h_b


# --------------------------------------------------------------------------
# BarSnapshotRef required-field rejection (DoD f)
# --------------------------------------------------------------------------


def test_bar_snapshot_ref_missing_snapshot_hash_is_rejected() -> None:
    fields: dict[str, Any] = dict(
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        from_time=_T0,
        to_time=_T0,
        bar_count=1,
        source="binance",
        as_of=_T0,
    )
    with pytest.raises(ValidationError):
        BarSnapshotRef(**fields)


def test_bar_snapshot_ref_missing_bar_count_is_rejected() -> None:
    fields: dict[str, Any] = dict(
        snapshot_hash="a" * 64,
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        from_time=_T0,
        to_time=_T0,
        source="binance",
        as_of=_T0,
    )
    with pytest.raises(ValidationError):
        BarSnapshotRef(**fields)


# --------------------------------------------------------------------------
# purity (DoD g) -- AST assertion, not a text/regex search
# --------------------------------------------------------------------------


def _imported_module_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _has_now_attribute_access(tree: ast.Module) -> bool:
    return any(isinstance(node, ast.Attribute) and node.attr == "now" for node in ast.walk(tree))


@pytest.mark.parametrize("filename", _CHECKED_FILES)
def test_domain_file_has_no_io_or_nondeterministic_imports(filename: str) -> None:
    tree = ast.parse((_DOMAIN_DIR / filename).read_text(encoding="utf-8"))
    modules = _imported_module_roots(tree)
    assert not modules & _FORBIDDEN_IO_MODULES, f"{filename}: {modules & _FORBIDDEN_IO_MODULES}"
    assert "random" not in modules, f"{filename}: imports random"
    assert not _has_now_attribute_access(tree), f"{filename}: calls datetime.now"


# --------------------------------------------------------------------------
# D2 성능 단언 -- ADR-2026-09-09-C Decision 1 예산표 "백테스트 1개월 M1 1심볼 3초"
# --------------------------------------------------------------------------


def test_bar_snapshot_hash_one_month_m1_single_symbol_within_backtest_budget() -> None:
    """스냅샷 해싱은 백테스트 파이프라인의 한 단계일 뿐이므로, 예산 전체(3초)가
    아니라 그 상당한 여유(1초)만 쓴다고 단언한다 -- 1개월치 M1(1분봉) 단일 심볼은
    43,200개 bar이며, `canonical_json`의 정규화(Decimal.normalize 등)가 병리적으로
    느려지는 회귀가 생기면 이 예산을 넘는다."""
    bars = [
        _bar(index=i, volume=Decimal("10"))
        for i in range(30 * 24 * 60)  # 30일 * 24시간 * 60분 = 43,200 M1 bar
    ]
    start = time.perf_counter()
    compute_bar_snapshot_hash(bars, source="binance", as_of=_T0)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, (
        f"43,200-bar(1개월 M1 1심볼) snapshot hash took {elapsed * 1000:.2f}ms, "
        "budget 1000ms(ADR-2026-09-09-C 3초 예산의 여유분)"
    )


# --------------------------------------------------------------------------
# D2 게이트 적색 재현 -- .importlinter domain-no-adapters
# --------------------------------------------------------------------------


def test_import_linter_domain_no_adapters_catches_backtest_domain_regression() -> None:
    """`models.py`/`snapshot.py`/`events.py`는 `src/foundation/backtest/domain/`
    아래에 있고, `.importlinter`의 `domain-no-adapters` 계약(§ domain/은 같은
    애그리게잇의 adapters/를 임포트할 수 없다)이 그 순수성(DoD g, 위 AST 검사)의
    실제 CI 집행자다. 그 평가기(`scripts/check_import_linter.py._eval_forbidden_suffix`)에
    이 leaf가 `adapters/`를 참조하는 회귀 모양을 합성 그래프로 주입해 적색으로
    잡히는지, 그리고 이 leaf의 실제 현재 import는 녹색인지 대조 증명한다(합성
    그래프를 쓰므로 회귀가 실제 트리에 존재할 필요가 없다)."""
    contracts = parse_contracts(LINTER_ROOT / ".importlinter")
    domain_no_adapters = next(c for c in contracts if c["id"] == "domain-no-adapters")

    regressed_graph = {
        "src.foundation.backtest.domain.snapshot": {
            "src.foundation.backtest.adapters.bar_fill_simulator"
        }
    }
    hits = _eval_forbidden_suffix(regressed_graph, domain_no_adapters)
    assert len(hits) == 1
    assert hits[0][0] == "src.foundation.backtest.domain.snapshot"

    real_graph = {
        f"src.foundation.backtest.domain.{filename[:-3]}": _imports_of(
            _DOMAIN_DIR / filename,
            f"src.foundation.backtest.domain.{filename[:-3]}",
            is_package=False,
        )
        for filename in _CHECKED_FILES
    }
    assert _eval_forbidden_suffix(real_graph, domain_no_adapters) == []
