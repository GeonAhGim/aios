"""task-2791 DEEPEN of task-1990 (BR-12 generator language template,
ADR-2026-09-07-A, docs/audit/DEPTH_L4_BR.md #1990).

DEPTH audit found the original leaf's suite
(tests/unit/scripts/test_kis_generate_adapters.py) below the D2 floor: only
one regression test (full regen of the real, already-clean reference must
produce zero Hangul comment/docstring lines) -- fewer than the required 3
negative-input tests, no failure-injection test, and no numeric performance
assertion.

The real reference (docs/design/kis_tr_reference.json) is machine-extracted
by BR-11 from example scripts. `_summary_line` interpolates several of its
fields (method, path, param names, response containers/fields) straight into
the generated docstring text with no Hangul check of its own -- it relies
entirely on those fields staying ASCII. If a future BR-11 extraction bug ever
lets Hangul leak into one of those fields (e.g. a label mis-mapped onto a
field name), the generator would silently re-emit Hangul under `src/` and
reopen exactly the CI-red scenario task-1990 fixed -- undetected by
generation itself, only caught later by a separate CI step
(check_code_language.py) with no pointer back to the offending row.

This leaf (1) adds a fail-closed Hangul guard directly in
`_docstring_lines` (scripts/kis_generate_adapters.py), (2) proves it with
negative-input tests injecting Hangul into each of the dynamic fields that
feed the docstring, (3) a failure-injection test showing the guard
propagates through the full `generate_files` pipeline (one poisoned row
aborts the whole batch rather than silently emitting partial bad output),
and (4) a numeric performance assertion on `generate_files` over a
synthetic reference an order of magnitude larger than the real one (pure
in-memory string building, no I/O -- an absolute time budget is safe here,
unlike the ratio-based budgets used for HTTP-bearing tests elsewhere in this
suite).
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

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


gen = _load_module("kis_generate_adapters", SCRIPTS_DIR / "kis_generate_adapters.py")
_LANG_CHECK_MODULE = _load_module("check_code_language", SCRIPTS_DIR / "check_code_language.py")


def _row(
    tr_id: str,
    *,
    domain: str = "domestic_stock",
    method: str = "GET",
    path: str | None = "/uapi/domestic-stock/v1/quotations/inquire-price",
    params: list[dict[str, Any]] | None = None,
    containers: list[str] | None = None,
    fields: list[str] | None = None,
    label: str = "test label",
) -> dict[str, Any]:
    return {
        "tr_id": tr_id,
        "domain": domain,
        "label": label,
        "source_path": "examples_llm/domestic_stock/inquire_price/inquire_price.py",
        "method": method,
        "path": path,
        "params": params if params is not None else [{"name": "FID_INPUT_ISCD", "required": True}],
        "response": {
            "kind": "ws" if method == "WS" else "rest",
            "containers": containers if containers is not None else ["output"],
            "fields": fields if fields is not None else [],
        },
        "extraction_error": None,
    }


# ---------------------------------------------------------------------------
# 1) negative-input tests -- Hangul injected into each dynamic field that
#    `_summary_line` interpolates into the docstring. All must fail closed.
# ---------------------------------------------------------------------------


def test_hangul_in_required_param_name_raises() -> None:
    row = _row("ZDOC0001", params=[{"name": "종목코드", "required": True}])
    with pytest.raises(gen.KisGenerateError, match="ZDOC0001"):
        gen.render_method(row)


def test_hangul_in_optional_param_name_raises() -> None:
    row = _row(
        "ZDOC0002",
        params=[
            {"name": "FID_INPUT_ISCD", "required": True},
            {"name": "옵션필드", "required": False},
        ],
    )
    with pytest.raises(gen.KisGenerateError, match="ZDOC0002"):
        gen.render_method(row)


def test_hangul_in_response_container_name_raises() -> None:
    row = _row("ZDOC0003", containers=["출력"])
    with pytest.raises(gen.KisGenerateError, match="ZDOC0003"):
        gen.render_method(row)


def test_hangul_in_ws_field_name_raises() -> None:
    row = _row(
        "ZDOC0004",
        method="WS",
        path=None,
        params=[],
        containers=[],
        fields=["체결가"],
    )
    with pytest.raises(gen.KisGenerateError, match="ZDOC0004"):
        gen.render_method(row)


def test_hangul_in_path_raises() -> None:
    row = _row("ZDOC0005", path="/uapi/국내주식/v1/quotations/inquire-price")
    with pytest.raises(gen.KisGenerateError, match="ZDOC0005"):
        gen.render_method(row)


def test_clean_ascii_row_is_unaffected_by_the_guard() -> None:
    """Sanity check the guard is Hangul-specific, not a false-positive trap --
    a normal ASCII row (identical shape to real reference rows) must still
    render without raising."""
    row = _row("ZDOC0006")
    lines = gen.render_method(row)
    assert any("async def" in line for line in lines)
    assert _LANG_CHECK_MODULE.HANGUL.search("\n".join(lines)) is None


# ---------------------------------------------------------------------------
# 2) failure-injection -- one poisoned row mixed among otherwise-valid rows
#    must abort the *entire* generate_files() batch, not just skip itself or
#    emit a partially-poisoned file set.
# ---------------------------------------------------------------------------


def test_generate_files_aborts_whole_batch_on_one_poisoned_row() -> None:
    reference = {
        "trs": [
            _row("ZBATCH0001"),
            _row("ZBATCH0002", params=[{"name": "종목코드", "required": True}]),
            _row("ZBATCH0003"),
        ]
    }
    with pytest.raises(gen.KisGenerateError, match="ZBATCH0002"):
        gen.generate_files(reference, handwritten_source="")


# ---------------------------------------------------------------------------
# 3) numeric performance assertion -- generate_files is pure in-memory string
#    building (no I/O), so an absolute wall-clock budget is safe (unlike the
#    ratio-based budgets used elsewhere in this suite for HTTP-bearing code).
# ---------------------------------------------------------------------------


def test_generate_files_throughput_at_10x_real_reference_scale() -> None:
    """The real reference currently generates ~290 methods across a handful
    of domains in well under a second. This synthesizes ~3,000 rows (~10x)
    spread across 30 domains (small enough per domain to stay under the
    300-line file cap on tr_labels.py) and asserts the whole batch still
    completes inside a generous budget -- catching an accidental O(n^2) in
    future edits to chunking/wrapping, not just "did it hang forever"."""
    rows_per_domain = 100
    domain_count = 30
    trs = [
        _row(f"ZPERF{d:02d}{i:04d}", domain=f"zperfdomain{d:02d}")
        for d in range(domain_count)
        for i in range(rows_per_domain)
    ]
    reference = {"trs": trs}
    n = len(trs)
    assert n == rows_per_domain * domain_count

    started = time.perf_counter()
    files = gen.generate_files(reference, handwritten_source="")
    elapsed = time.perf_counter() - started

    budget_seconds = 30.0  # observed ~1.7-6s depending on machine/CI load; generous headroom
    throughput = n / elapsed if elapsed > 0 else float("inf")
    print(
        f"\ngenerate_files throughput: n={n} elapsed={elapsed * 1000:.1f}ms "
        f"throughput={throughput:.0f} rows/s (budget={budget_seconds}s)"
    )
    assert elapsed < budget_seconds, (
        f"generate_files({n} rows) took {elapsed:.2f}s, over the {budget_seconds}s budget -- "
        "check for an accidental quadratic blowup in chunking/wrapping."
    )

    # correctness alongside the timing, not just raw speed: every domain got a
    # labels file plus at least one mixin chunk, and none of it re-leaks Hangul.
    for d in range(domain_count):
        assert f"zperfdomain{d:02d}_tr_labels.py" in files
    mixin_files = [name for name in files if name.endswith("_mixin.py")]
    assert len(mixin_files) >= domain_count
    total_hangul = sum(
        len(_LANG_CHECK_MODULE.HANGUL.findall(content))
        for name, content in files.items()
        if name.endswith("_mixin.py")
    )
    assert total_hangul == 0
