"""scripts/closeout_check.py 동적 로더 — test_closeout_check*.py 공용.

`closeout_check.py`는 패키지가 아니라 `scripts/` 아래 단일 스크립트라 일반
import가 안 되므로, 여러 분할 테스트 파일이 같은 방식(importlib)으로 한 번씩
로드한다. 실제 검사 함수/테스트 헬퍼는 여기 없다 — 순수 로딩 배선만 담당한다.
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


cc = _load_module("closeout_check_under_test", SCRIPTS_DIR / "closeout_check.py")


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


TEST_DEF = "def test_x():\n    assert True\n"
