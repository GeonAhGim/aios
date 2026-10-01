"""scripts/closeout_check.py 동적 로더 — test_closeout_check*.py 공용.

`closeout_check.py`는 패키지가 아니라 `scripts/` 아래 단일 스크립트라 일반
import가 안 되므로, 여러 분할 테스트 파일이 같은 방식(importlib)으로 한 번씩
로드한다. 실제 검사 함수/테스트 헬퍼는 여기 없다 — 순수 로딩 배선만 담당한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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


cc = _load_module("closeout_check_under_test", SCRIPTS_DIR / "closeout_check.py")


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


TEST_DEF = "def test_x():\n    assert True\n"


def test_load_module_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """존재하지 않는 경로는 FileNotFoundError로 거부돼야 한다(조용한 빈 모듈 금지)."""
    missing = tmp_path / "does_not_exist.py"

    with pytest.raises(FileNotFoundError):
        _load_module("closeout_check_loader_missing", missing)


def test_load_module_syntax_error_propagates(tmp_path: Path) -> None:
    """문법 오류가 있는 스크립트는 예외를 삼키지 않고 SyntaxError로 전파돼야 한다."""
    bad = _write(tmp_path / "broken.py", "def broken(:\n    pass\n")

    with pytest.raises(SyntaxError):
        _load_module("closeout_check_loader_broken", bad)


def test_load_module_exec_error_propagates(tmp_path: Path) -> None:
    """모듈 최상위 실행 중 예외는 삼켜지지 않고 그대로 전파돼야 한다."""
    raising = _write(tmp_path / "raising.py", "raise RuntimeError('boom')\n")

    with pytest.raises(RuntimeError, match="boom"):
        _load_module("closeout_check_loader_raising", raising)


def test_load_module_spec_none_raises_assertion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """실패주입: spec_from_file_location이 None을 반환하면 assert로 즉시 중단돼야 한다."""
    target = _write(tmp_path / "ok.py", TEST_DEF)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **k: None)

    with pytest.raises(AssertionError):
        _load_module("closeout_check_loader_spec_none", target)


def test_load_module_loader_none_raises_assertion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """실패주입: spec.loader가 None이면(네임스페이스 패키지 등) assert로 거부돼야 한다."""
    target = _write(tmp_path / "ok2.py", TEST_DEF)
    fake_spec = SimpleNamespace(loader=None)
    monkeypatch.setattr(importlib.util, "spec_from_file_location", lambda *a, **k: fake_spec)

    with pytest.raises(AssertionError):
        _load_module("closeout_check_loader_loader_none", target)


def test_write_creates_missing_parent_directories(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "file.txt"

    result = _write(nested, "hello")

    assert result == nested
    assert nested.read_text(encoding="utf-8") == "hello"


def test_write_overwrites_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "overwrite.txt"
    _write(target, "first")

    _write(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
