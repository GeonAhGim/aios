from io import StringIO
from pathlib import Path

import pytest
import yaml

from src.core.loader.config_loader import load_config


def test_load_config_reads_yaml(tmp_path: Path):
    config_file = tmp_path / "risk_policy.yaml"
    config_file.write_text(
        "version: draft-1\ndaily_loss:\n  warning_pct: 3.0\n  halt_pct: 5.0\n",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config["version"] == "draft-1"
    assert config["daily_loss"]["warning_pct"] == 3.0


def test_load_config_empty_file_returns_empty_dict(tmp_path: Path):
    config_file = tmp_path / "empty.yaml"
    config_file.write_text("", encoding="utf-8")

    assert load_config(config_file) == {}


def test_load_config_non_mapping_root_raises(tmp_path: Path):
    config_file = tmp_path / "list.yaml"
    config_file.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(config_file)


@pytest.mark.parametrize("payload", ["false\n", "0\n", '""\n', "text\n"])
def test_load_config_negative_scalar_root_rejected(tmp_path: Path, payload: str):
    config_file = tmp_path / "scalar.yaml"
    config_file.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="dict"):
        load_config(config_file)


def test_load_config_negative_malformed_yaml_rejected(tmp_path: Path):
    config_file = tmp_path / "malformed.yaml"
    config_file.write_text("daily_loss: [1, 2\n", encoding="utf-8")

    with pytest.raises(yaml.parser.ParserError):
        load_config(config_file)


def test_load_config_negative_python_object_tag_rejected(tmp_path: Path):
    config_file = tmp_path / "unsafe.yaml"
    config_file.write_text("value: !!python/tuple [1, 2]\n", encoding="utf-8")

    with pytest.raises(yaml.constructor.ConstructorError, match="python/tuple"):
        load_config(config_file)


def test_load_config_negative_missing_file_rejected(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")


def test_load_config_failure_injection_read_error_closes_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    failure = OSError("injected configuration read failure")

    class BrokenStream(StringIO):
        def read(self, *args, **kwargs):
            raise failure

    stream = BrokenStream("version: draft-1\n")
    config_file = tmp_path / "unreadable.yaml"

    def open_stream(path, *, encoding):
        assert path == config_file
        assert encoding == "utf-8"
        return stream

    monkeypatch.setattr(Path, "open", open_stream)

    with pytest.raises(OSError, match="injected configuration read failure") as caught:
        load_config(config_file)

    assert caught.value is failure
    assert stream.closed
