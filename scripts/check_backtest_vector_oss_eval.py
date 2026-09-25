"""CLI gate for BT-14 BACKTEST_VECTOR_EVAL.md (task-3048 deepen).

Reads the eval markdown and pyproject.toml, runs `assert_vector_oss_eval_gate`.
Exit 0 = pass, 1 = DoD violation (gate red). Not yet wired as a hard CI step
-- OPS-42: register as warn in fleet `ci_recheck.STEP_MODE` before promoting
to gate. Pytest covers the same pure function directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.foundation.backtest.domain.vector_oss_eval_gate import (  # noqa: E402
    VectorOssEvalGateError,
    assert_vector_oss_eval_gate,
)

_DEFAULT_EVAL_PATH = _REPO_ROOT / "docs" / "design" / "BACKTEST_VECTOR_EVAL.md"
_DEFAULT_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = list(sys.argv[1:] if argv is None else argv)
    eval_path = Path(args[0]) if len(args) > 0 else _DEFAULT_EVAL_PATH
    pyproject_path = Path(args[1]) if len(args) > 1 else _DEFAULT_PYPROJECT_PATH
    try:
        eval_text = eval_path.read_text(encoding="utf-8")
        pyproject_text = pyproject_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL: cannot read input: {exc}")
        return 1
    try:
        report = assert_vector_oss_eval_gate(eval_text, pyproject_text)
    except VectorOssEvalGateError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(
        f"OK: excluded={sorted(report.excluded_packages)} "
        f"numpy_declared={report.numpy_declared} "
        f"numba_declared={report.numba_declared} "
        f"declared_deps={len(report.declared_dependencies)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
