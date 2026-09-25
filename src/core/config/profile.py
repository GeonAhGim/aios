"""H-13 — config/{dev,staging,live}.yaml loader (ADR-2026-09-09-B).

Selects a profile via `AIOS_ENV` and loads its non-secret settings from
`config/<profile>.yaml`. Secrets are out of scope here and stay in .env /
KeyRing (src/core/loader/secret_loader.py, src/core/security/key_ring.py).

The `live` profile has one extra rule: booting it while any exchange adapter
is still configured for paper/demo trading (KIS_PAPER_TRADING,
NH_PAPER_TRADING, BITGET_PAPER_TRADING) is refused outright
(`LiveProfileContaminationError`). Unset or unrecognized values are treated
as "still paper" — fail-closed default posture (CLAUDE.md §3) — so an
incomplete live rollout cannot boot live by omission.

This module only decides whether the *profile* may load; it is not the
live-adapter creation gate (`AIOS_ALLOW_LIVE_ADAPTER`, src/exchanges/
factory.py) and does not replace it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_DIR = _PROJECT_ROOT / "config"

AIOS_ENV_VAR = "AIOS_ENV"
DEFAULT_PROFILE = "dev"
VALID_PROFILES = ("dev", "staging", "live")

# Read as "still paper/demo" unless explicitly turned off.
_PAPER_FLAG_ENV_VARS = ("KIS_PAPER_TRADING", "NH_PAPER_TRADING", "BITGET_PAPER_TRADING")
_FALSY = {"0", "false", "no", "off"}

_REQUIRED_KEYS = ("environment", "log_level")
_KNOWN_KEYS = frozenset({"environment", "log_level", "cors_allowed_origins"})


class ProfileNotFoundError(Exception):
    """No config/<profile>.yaml exists for the resolved profile name."""


class InvalidProfileNameError(Exception):
    """AIOS_ENV holds a value outside {dev, staging, live}."""


class ProfileSchemaError(Exception):
    """config/<profile>.yaml is not a valid profile document."""


class LiveProfileContaminationError(Exception):
    """The live profile was selected while a paper/demo trading flag is still active."""


@dataclass(frozen=True)
class ProfileConfig:
    """Parsed, validated contents of one config/<profile>.yaml."""

    name: str
    log_level: str
    cors_allowed_origins: tuple[str, ...]
    extra: Mapping[str, Any]


def resolve_profile_name(env: Mapping[str, str] | None = None) -> str:
    """Read AIOS_ENV (default: "dev") and validate it names a known profile."""
    source = env if env is not None else os.environ
    name = source.get(AIOS_ENV_VAR, DEFAULT_PROFILE).strip().lower()
    if name not in VALID_PROFILES:
        raise InvalidProfileNameError(f"{AIOS_ENV_VAR}={name!r} is not one of {VALID_PROFILES}")
    return name


def _is_still_paper(raw: str | None) -> bool:
    """Fail-closed: missing or unrecognized values count as "still paper"."""
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY


def assert_live_profile_safe(profile_name: str, env: Mapping[str, str] | None = None) -> None:
    """Refuse to boot the live profile while a paper/demo trading flag is on.

    No-op for any profile other than "live" — paper/demo trading is exactly
    what dev/staging are for.
    """
    if profile_name != "live":
        return
    source = env if env is not None else os.environ
    contaminated = [
        var_name for var_name in _PAPER_FLAG_ENV_VARS if _is_still_paper(source.get(var_name))
    ]
    if contaminated:
        raise LiveProfileContaminationError(
            "live profile refused to boot: paper/demo trading flag(s) still "
            f"active or unset: {', '.join(contaminated)} (fail-closed, H-13/"
            "ADR-2026-09-09-B). Set each to a falsy value (0/false/no/off) "
            "explicitly before starting the live profile."
        )


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ProfileNotFoundError(f"no config file for this profile: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileSchemaError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileSchemaError(f"{path} top level must be a mapping, got {type(raw).__name__}")
    return raw


def load_profile_config(
    profile_name: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config_dir: Path | None = None,
) -> ProfileConfig:
    """Load config/<profile>.yaml for the resolved profile.

    Resolution order: explicit `profile_name` argument, else `AIOS_ENV`
    (via `resolve_profile_name`). The live-contamination guard
    (`assert_live_profile_safe`) runs before the file is even read, so a
    malformed live.yaml cannot mask the fail-closed rejection.
    """
    source = env if env is not None else os.environ
    name = profile_name if profile_name is not None else resolve_profile_name(source)
    if name not in VALID_PROFILES:
        raise InvalidProfileNameError(f"unknown profile {name!r}, expected one of {VALID_PROFILES}")

    assert_live_profile_safe(name, source)

    directory = config_dir if config_dir is not None else _DEFAULT_CONFIG_DIR
    raw = _read_yaml_mapping(directory / f"{name}.yaml")

    for key in _REQUIRED_KEYS:
        if key not in raw:
            raise ProfileSchemaError(
                f"{directory / f'{name}.yaml'} is missing required key {key!r}"
            )

    declared_name = raw["environment"]
    if declared_name != name:
        raise ProfileSchemaError(
            f"{directory / f'{name}.yaml'} declares environment={declared_name!r}, "
            f"expected {name!r}"
        )

    cors = raw.get("cors_allowed_origins", [])
    if not isinstance(cors, list) or not all(isinstance(item, str) for item in cors):
        raise ProfileSchemaError(
            f"{directory / f'{name}.yaml'}: cors_allowed_origins must be a list of strings"
        )

    extra = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}

    return ProfileConfig(
        name=name,
        log_level=str(raw["log_level"]),
        cors_allowed_origins=tuple(cors),
        extra=extra,
    )
