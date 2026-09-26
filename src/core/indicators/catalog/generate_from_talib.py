"""IND-2g -- TA-Lib catalog generator: writes a deterministic metadata snapshot.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-2g
(IND-10 is deprecated in favor of this leaf -- both walk `talib.get_functions()`
(161 types) x `talib_abstract.Function(name).info`; this leaf is the canonical
generator entry point and adds a callable-free, on-disk snapshot).

This module does not re-derive introspection/derivation rules (group, param
range, PlotSpec defaults) -- `src.core.indicators.generate_specs.generate_talib_specs`
is the single source of truth for that. Here we only reshape its output into a
deterministic, TA-Lib-free Python literal (`IndicatorSpec.lookback` is a runtime
callable and is intentionally dropped -- callers still need TA-Lib installed to
compute lookback; only the descriptive metadata is snapshotted) and render it to
`catalog/talib_generated.py`.

Fail-closed: `require_talib()` raises `TALibUnavailableError` instead of
silently emitting a partial/stale catalog when TA-Lib's C library binding is not
importable -- there is no skip path.
"""

from __future__ import annotations

import importlib.util
import logging
import pprint
from collections.abc import Iterable
from pathlib import Path
from textwrap import indent

from src.core.indicators.generate_specs import TALIB_GROUPS, generate_talib_specs
from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec

__all__ = [
    "OUTPUT_PATH",
    "TALibUnavailableError",
    "collect_records",
    "generate_file",
    "main",
    "render_module",
    "require_talib",
]

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path(__file__).with_name("talib_generated.py")

_HEADER = '''# loc-allow: IND-2g generated TA-Lib catalog table (161 records) -- deterministic
# rule matrix, ADR-2026-09-10-C Section 7.
"""IND-2g -- generated TA-Lib catalog snapshot. DO NOT EDIT BY HAND.

Regenerate with `python -m src.core.indicators.catalog.generate_from_talib`.
Requires TA-Lib installed -- without it the generator raises
`TALibUnavailableError` instead of silently writing a partial/stale file
(fail-closed). This file itself imports nothing beyond the stdlib so it can be
loaded/validated in environments without the TA-Lib C library (snapshot-only
verification).
"""
from __future__ import annotations

TALIB_GENERATED: tuple[dict[str, object], ...] = (
'''

_FOOTER = ")\n"

# repo ruff `line-length = 100`; reserve 4 columns for the `indent(..., "    ")`
# applied to every wrapped line below.
_LINE_WIDTH = 96


class TALibUnavailableError(RuntimeError):
    """Raised when TA-Lib's C library binding is not importable (fail-closed)."""


def require_talib() -> None:
    if importlib.util.find_spec("talib") is None:
        raise TALibUnavailableError(
            "TA-Lib is not installed -- generate_from_talib.py refuses to "
            "produce a partial/empty catalog snapshot (fail-closed, no skip)."
        )


def _param_dict(param: ParamSpec) -> dict[str, object]:
    return {"name": param.name, "min": param.min, "max": param.max, "default": param.default}


def _plot_dict(plot: PlotSpec) -> dict[str, object]:
    return {
        "kind": plot.kind,
        "scale": plot.scale,
        "default_pane": plot.default_pane,
        "fill_between": plot.fill_between,
        "color_rule": plot.color_rule,
        "precision": plot.precision,
        "legend_format": plot.legend_format,
    }


def _record(name: str, spec: IndicatorSpec) -> dict[str, object]:
    return {
        "name": name,
        "group": TALIB_GROUPS[name],
        "inputs": list(spec.inputs),
        "params": [_param_dict(p) for p in spec.params],
        "outputs": list(spec.outputs),
        "plots": [_plot_dict(p) for p in spec.plots],
    }


def collect_records(names: Iterable[str] | None = None) -> list[dict[str, object]]:
    """Collect deterministic, TA-Lib-free metadata records sorted by name.

    Requires TA-Lib (`require_talib()`); all introspection/derivation is
    delegated to `generate_talib_specs` (raises `ValueError` unchanged for
    unknown names, propagates any introspection failure unchanged -- this
    function reshapes, it does not swallow).
    """
    require_talib()
    specs = generate_talib_specs(names)
    return [_record(name, specs[name]) for name in sorted(specs)]


def render_module(names: Iterable[str] | None = None) -> str:
    """Render the full snapshot module source, deterministically.

    `pprint.pformat(..., sort_dicts=False)` on a `dict[str, object]` built
    with a fixed key order is stable across calls within one Python version
    (dict preserves insertion order; `pformat` does not reorder) -- re-running
    with the same TA-Lib version must produce byte-identical output (IND-2g
    DoD). `width=_LINE_WIDTH` wraps long records onto multiple lines so the
    snapshot stays within the repo's ruff `line-length` gate.
    """
    records = collect_records(names)
    body = ",\n".join(
        indent(pprint.pformat(record, width=_LINE_WIDTH, sort_dicts=False), "    ")
        for record in records
    )
    middle = f"{body}\n" if records else ""
    return _HEADER + middle + _FOOTER


def generate_file(path: Path = OUTPUT_PATH, names: Iterable[str] | None = None) -> str:
    source = render_module(names)
    path.write_text(source, encoding="utf-8", newline="\n")
    return source


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    source = generate_file()
    logger.info("[IND-2g] wrote %s (%d lines)", OUTPUT_PATH, source.count("\n"))


if __name__ == "__main__":
    main()
