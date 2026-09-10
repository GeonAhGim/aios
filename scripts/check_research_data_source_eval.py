"""CLI gate for RD-1 RESEARCH_DATA_SOURCE_EVAL.md (task-2904 deepen).

Reads the eval markdown and runs `assert_source_eval_gate`. Exit 0 = pass,
1 = DoD violation (gate red). Not yet wired as a hard CI step — OPS-42:
register as warn in fleet `ci_recheck.STEP_MODE` before promoting to gate.
Pytest covers the same pure function directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.foundation.research_data.domain.source_eval_gate import (  # noqa: E402
    SourceEvalGateError,
    assert_source_eval_gate,
)

_DEFAULT_PATH = _REPO_ROOT / "docs" / "design" / "RESEARCH_DATA_SOURCE_EVAL.md"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else _DEFAULT_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL: cannot read {path}: {exc}")
        return 1
    try:
        report = assert_source_eval_gate(text)
    except SourceEvalGateError as exc:
        print(f"FAIL: {exc}")
        return 1
    allow = [r.source_id for r in report.records if r.admission == "allow"]
    deny = [r.source_id for r in report.records if r.admission == "deny"]
    print(f"OK: LAYER_A={len(report.records)} allow={allow} deny={deny}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
