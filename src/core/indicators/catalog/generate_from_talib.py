"""IND-2g -- TA-Lib catalog generator: writes a deterministic metadata snapshot.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 IND-2g
(IND-10 is deprecated in favor of this leaf -- both walk `talib.get_functions()`
(161 types) x `talib_abstract.Function(name).info`; this leaf is the canonical
generator entry point and adds a callable-free, on-disk snapshot).

This module does not re-derive introspection/derivation rules (group, param
range, PlotSpec defaults) -- `src.core.indicators.generate_specs.generate_talib_specs`
is the single source of truth for that. Here we only reshape its output into
deterministic, TA-Lib-free Python literals (`IndicatorSpec.lookback` is a
runtime callable and is intentionally dropped -- callers still need TA-Lib
installed to compute lookback; only the descriptive metadata is snapshotted).

161 records don't fit in one `src/**/*.py` file under the repo's architecture
guard line cap (300 lines, no `loc-allow` exemption there unlike
`check_code_ratchets.py`'s 1000-line cap) -- so the snapshot is written as
several `talib_generated_partNN.py` data files (each packed to stay well under
budget) plus a small `talib_generated.py` aggregator that concatenates them.
`catalog/talib_*.py` (task file glob) covers both.

Fail-closed: `require_talib()` raises `TALibUnavailableError` instead of
silently emitting a partial/stale catalog when TA-Lib's C library binding is
not importable -- there is no skip path.
"""

from __future__ import annotations

import importlib.util
import logging
import pprint
from collections.abc import Iterable
from pathlib import Path

from src.core.indicators.generate_specs import TALIB_GROUPS, generate_talib_specs
from src.core.indicators.spec import IndicatorSpec, ParamSpec, PlotSpec

__all__ = [
    "AGGREGATOR_PATH",
    "OUTPUT_DIR",
    "TALibUnavailableError",
    "collect_records",
    "generate_files",
    "main",
    "render_all",
    "require_talib",
]

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent
AGGREGATOR_PATH = OUTPUT_DIR / "talib_generated.py"
_PART_STEM = "talib_generated_part"

# repo ruff `line-length = 100` and the architecture guard's 300-line cap on
# every `src/**/*.py` file -- 220 data lines plus the ~12-line header/footer
# stays comfortably under 300 even for the single largest record (MACDEXT,
# ~31 lines once wrapped).
_PART_LINE_BUDGET = 220
_LINE_WIDTH = 96

_PART_HEADER_TEMPLATE = '''"""IND-2g -- generated TA-Lib catalog snapshot, part {index} of {total}.

DO NOT EDIT BY HAND -- regenerate with
`python -m src.core.indicators.catalog.generate_from_talib`. Imports nothing
beyond the stdlib so it loads without TA-Lib installed (snapshot-only
verification, see `generate_from_talib.py` module docstring).
"""
from __future__ import annotations

TALIB_GENERATED_PART: tuple[dict[str, object], ...] = (
'''
_PART_FOOTER = ")\n"

_AGGREGATOR_HEADER = '''"""IND-2g -- generated TA-Lib catalog snapshot (aggregator).

DO NOT EDIT BY HAND. Regenerate with
`python -m src.core.indicators.catalog.generate_from_talib`.
Concatenates `talib_generated_partNN.py` (each kept under the architecture
guard's per-file line cap) into one `TALIB_GENERATED` tuple. Requires TA-Lib
installed to *regenerate* -- without it the generator raises
`TALibUnavailableError` instead of silently writing a partial/stale snapshot
(fail-closed). This file and its parts import nothing beyond the stdlib, so
they load without TA-Lib installed (CI-without-TA-Lib snapshot verification).
"""
from __future__ import annotations

'''


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


def _render_record(record: dict[str, object]) -> str:
    return pprint.pformat(record, width=_LINE_WIDTH, sort_dicts=False)


def _chunk_records(records: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Greedily pack sorted records into line-budget-bounded chunks.

    Deterministic: input order is fixed (`collect_records` sorts by name) and
    packing is a single left-to-right pass with no randomness or hashing.
    """
    chunks: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    current_lines = 0
    for record in records:
        record_lines = _render_record(record).count("\n") + 1
        if current and current_lines + record_lines > _PART_LINE_BUDGET:
            chunks.append(current)
            current = []
            current_lines = 0
        current.append(record)
        current_lines += record_lines
    if current:
        chunks.append(current)
    return chunks


def _part_filename(index: int) -> str:
    return f"{_PART_STEM}{index:02d}.py"


def _render_part(records: list[dict[str, object]], index: int, total: int) -> str:
    body = ",\n".join(f"    {_render_record(r)}" for r in records)
    return _PART_HEADER_TEMPLATE.format(index=index, total=total) + body + "\n" + _PART_FOOTER


def _render_aggregator(part_count: int) -> str:
    lines = [_AGGREGATOR_HEADER.rstrip("\n") + "\n", "\n"]
    for i in range(part_count):
        module = _part_filename(i)[: -len(".py")]
        alias = f"_PART_{i:02d}"
        lines.append(f"from src.core.indicators.catalog.{module} import (\n")
        lines.append(f"    TALIB_GENERATED_PART as {alias},\n")
        lines.append(")\n")
    lines.append("\n")
    parts = [f"_PART_{i:02d}" for i in range(part_count)]
    declaration = "TALIB_GENERATED: tuple[dict[str, object], ...] = "
    if not parts:
        lines.append(declaration + "()\n")
    else:
        lines.append(declaration + "(\n")
        for index, part in enumerate(parts):
            suffix = " +" if index < len(parts) - 1 else ""
            lines.append(f"    {part}{suffix}\n")
        lines.append(")\n")
    return "".join(lines)


def render_all(names: Iterable[str] | None = None) -> dict[str, str]:
    """Render every generated file's source, deterministically, keyed by filename.

    Byte-identical across calls for the same TA-Lib version/install (IND-2g
    DoD) -- `dict` insertion order here is fixed (parts in ascending index
    order, aggregator last).
    """
    records = collect_records(names)
    chunks = _chunk_records(records)
    files: dict[str, str] = {
        _part_filename(i): _render_part(chunk, i, len(chunks)) for i, chunk in enumerate(chunks)
    }
    files[AGGREGATOR_PATH.name] = _render_aggregator(len(chunks))
    return files


def generate_files(
    directory: Path = OUTPUT_DIR, names: Iterable[str] | None = None
) -> dict[str, str]:
    """Write the full generated snapshot (parts + aggregator) to `directory`.

    Stale parts from a previous run (e.g. TA-Lib upgrade changed chunk count)
    are removed first so no orphaned `talib_generated_partNN.py` lingers.
    """
    for stale in directory.glob(f"{_PART_STEM}*.py"):
        stale.unlink()
    files = render_all(names)
    for filename, source in files.items():
        (directory / filename).write_text(source, encoding="utf-8", newline="\n")
    return files


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    files = generate_files()
    logger.info("[IND-2g] wrote %d file(s) under %s", len(files), OUTPUT_DIR)


if __name__ == "__main__":
    main()
