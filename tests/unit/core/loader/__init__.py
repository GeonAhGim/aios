"""DEEPEN(task-9982): negative/failure-injection coverage for src/core/loader.

pytest collects this module when invoked with an explicit path
(`pytest tests/unit/core/loader/__init__.py`) even though it is not matched
by the default `test_*.py` discovery glob — see task-9982 spec.

Scope: src/core/loader/config_loader.load_config (principle 7.3 — this
loader only reads YAML, it never validates schema/ranges; that is the
consumer's job, so these tests only assert fail-closed behavior on
malformed/missing input, not on semantically-invalid-but-well-formed data).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.core.loader.config_loader import load_config


def test_load_config_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_load_config_malformed_yaml_raises_yaml_error(tmp_path: Path) -> None:
    config_file = tmp_path / "malformed.yaml"
    config_file.write_text("daily_loss: [unclosed\n  warning_pct: 3.0\n", encoding="utf-8")

    with pytest.raises(yaml.YAMLError):
        load_config(config_file)


def test_load_config_scalar_root_raises_value_error(tmp_path: Path) -> None:
    config_file = tmp_path / "scalar.yaml"
    config_file.write_text("just-a-string\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(config_file)


def test_load_config_int_root_raises_value_error(tmp_path: Path) -> None:
    config_file = tmp_path / "int_root.yaml"
    config_file.write_text("42\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(config_file)


def test_load_config_propagates_yaml_loader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure injection: a dependency (yaml.safe_load) exception must not be
    swallowed — fail-closed default posture (CLAUDE.md §3)."""
    config_file = tmp_path / "risk_policy.yaml"
    config_file.write_text("version: draft-1\n", encoding="utf-8")

    def _boom(_stream: object) -> None:
        raise RuntimeError("injected yaml backend failure")

    monkeypatch.setattr(yaml, "safe_load", _boom)

    with pytest.raises(RuntimeError, match="injected yaml backend failure"):
        load_config(config_file)
