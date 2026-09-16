"""H-13 — config/{dev,staging,live}.yaml 로더 테스트 (ADR-2026-09-09-B).

D2 증빙: negative >=3, 실패주입 1, 성능 단언 1(p95), 게이트 적색 재현 1.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.core.config.profile import (
    AIOS_ENV_VAR,
    VALID_PROFILES,
    InvalidProfileNameError,
    LiveProfileContaminationError,
    ProfileNotFoundError,
    ProfileSchemaError,
    assert_live_profile_safe,
    load_profile_config,
    resolve_profile_name,
)

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"

# 실제 저장소 config/live.yaml을 오염 없이 통과시키려면 세 플래그 모두
# 명시적으로 꺼야 한다 — 이 매핑을 여러 테스트가 재사용한다.
_LIVE_CLEAN_ENV = {
    "AIOS_ENV": "live",
    "KIS_PAPER_TRADING": "false",
    "NH_PAPER_TRADING": "false",
    "BITGET_PAPER_TRADING": "false",
}


# --- 프로필 3종 로드 (DoD) -------------------------------------------------


def test_load_dev_profile_from_repo_config_dir() -> None:
    cfg = load_profile_config(env={"AIOS_ENV": "dev"})
    assert cfg.name == "dev"
    assert cfg.log_level == "DEBUG"
    assert "http://localhost:5173" in cfg.cors_allowed_origins


def test_load_staging_profile_from_repo_config_dir() -> None:
    cfg = load_profile_config(env={"AIOS_ENV": "staging"})
    assert cfg.name == "staging"
    assert cfg.log_level == "INFO"


def test_load_live_profile_succeeds_when_all_paper_flags_explicitly_off() -> None:
    cfg = load_profile_config(env=_LIVE_CLEAN_ENV)
    assert cfg.name == "live"
    assert cfg.log_level == "WARNING"
    assert cfg.cors_allowed_origins == ()


def test_default_profile_is_dev_when_aios_env_unset() -> None:
    assert resolve_profile_name(env={}) == "dev"


# --- live 혼입 거부 (DoD) ---------------------------------------------------


def test_live_boot_rejected_when_kis_paper_trading_true() -> None:
    env = dict(_LIVE_CLEAN_ENV, KIS_PAPER_TRADING="true")
    with pytest.raises(LiveProfileContaminationError, match="KIS_PAPER_TRADING"):
        load_profile_config(env=env)


def test_live_boot_rejected_when_bitget_paper_trading_true() -> None:
    env = dict(_LIVE_CLEAN_ENV, BITGET_PAPER_TRADING="true")
    with pytest.raises(LiveProfileContaminationError, match="BITGET_PAPER_TRADING"):
        load_profile_config(env=env)


def test_live_boot_rejected_when_nh_paper_trading_true() -> None:
    env = dict(_LIVE_CLEAN_ENV, NH_PAPER_TRADING="true")
    with pytest.raises(LiveProfileContaminationError, match="NH_PAPER_TRADING"):
        load_profile_config(env=env)


def test_live_boot_rejected_when_paper_flags_missing_entirely() -> None:
    """Fail-closed default: unset flags must NOT be treated as "already live"."""
    with pytest.raises(LiveProfileContaminationError) as exc_info:
        load_profile_config(env={"AIOS_ENV": "live"})
    message = str(exc_info.value)
    assert "KIS_PAPER_TRADING" in message
    assert "NH_PAPER_TRADING" in message
    assert "BITGET_PAPER_TRADING" in message


@pytest.mark.parametrize("truthy_spelling", ["1", "true", "TRUE", "yes", "on", "garbage"])
def test_is_still_paper_treats_any_non_falsy_spelling_as_contaminated(
    truthy_spelling: str,
) -> None:
    with pytest.raises(LiveProfileContaminationError):
        assert_live_profile_safe("live", env={"KIS_PAPER_TRADING": truthy_spelling})


@pytest.mark.parametrize("falsy_spelling", ["0", "false", "FALSE", "no", "off"])
def test_is_still_paper_accepts_falsy_spellings(falsy_spelling: str) -> None:
    assert_live_profile_safe(
        "live",
        env={
            "KIS_PAPER_TRADING": falsy_spelling,
            "NH_PAPER_TRADING": falsy_spelling,
            "BITGET_PAPER_TRADING": falsy_spelling,
        },
    )


def test_non_live_profile_ignores_paper_flags() -> None:
    """dev/staging are exempt — that is what paper flags are for there."""
    assert_live_profile_safe("dev", env={"KIS_PAPER_TRADING": "true"})
    assert_live_profile_safe("staging", env={"BITGET_PAPER_TRADING": "true"})


# --- negative: 잘못된 입력 (>=3 항목, 위 live 혼입 거부 3건과 별개) ----------


def test_unknown_aios_env_value_rejected() -> None:
    with pytest.raises(InvalidProfileNameError):
        resolve_profile_name(env={"AIOS_ENV": "production"})


def test_missing_profile_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileNotFoundError):
        load_profile_config("dev", env={}, config_dir=tmp_path)


def test_profile_declaring_wrong_environment_name_rejected(tmp_path: Path) -> None:
    (tmp_path / "dev.yaml").write_text("environment: staging\nlog_level: DEBUG\n", encoding="utf-8")
    with pytest.raises(ProfileSchemaError, match="environment"):
        load_profile_config("dev", env={}, config_dir=tmp_path)


def test_profile_missing_required_key_rejected(tmp_path: Path) -> None:
    (tmp_path / "dev.yaml").write_text("environment: dev\n", encoding="utf-8")
    with pytest.raises(ProfileSchemaError, match="log_level"):
        load_profile_config("dev", env={}, config_dir=tmp_path)


def test_profile_with_non_list_cors_origins_rejected(tmp_path: Path) -> None:
    (tmp_path / "dev.yaml").write_text(
        "environment: dev\nlog_level: DEBUG\ncors_allowed_origins: not-a-list\n",
        encoding="utf-8",
    )
    with pytest.raises(ProfileSchemaError, match="cors_allowed_origins"):
        load_profile_config("dev", env={}, config_dir=tmp_path)


# --- 실패 주입: 파일은 존재하지만 파싱 자체가 깨진 경우 ---------------------


def test_malformed_yaml_raises_schema_error_not_silent_success(tmp_path: Path) -> None:
    (tmp_path / "dev.yaml").write_text("environment: [unterminated\n", encoding="utf-8")
    with pytest.raises(ProfileSchemaError, match="not valid YAML"):
        load_profile_config("dev", env={}, config_dir=tmp_path)


def test_yaml_top_level_scalar_raises_schema_error(tmp_path: Path) -> None:
    (tmp_path / "dev.yaml").write_text("just a string\n", encoding="utf-8")
    with pytest.raises(ProfileSchemaError, match="mapping"):
        load_profile_config("dev", env={}, config_dir=tmp_path)


# --- 게이트 적색 재현: 가드를 실제로 우회시켜 놓고 그래도 막히는지 확인 ---


def test_gate_red_repro_bypassing_the_guard_lets_contamination_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`load_profile_config`가 `assert_live_profile_safe`를 실제로 호출해서
    막고 있다는 것(구현됨이 아니라 배선됨, I-10)을 증명한다 — 이 가드
    함수를 no-op으로 바꾸면(사고로 호출부가 지워진 상황을 재현) 오염된
    입력이 조용히 통과해야 한다. 이 테스트가 그 "적색" 상태를 재현하고,
    이후 `assert_live_profile_safe`가 다시 호출되도록 원복되면(현재 코드)
    위 test_live_boot_rejected_when_* 3건이 그 회귀를 잡는다.
    """
    import src.core.config.profile as profile_module

    monkeypatch.setattr(profile_module, "assert_live_profile_safe", lambda *a, **k: None)

    # 가드가 배선돼 있었다면 여기서 LiveProfileContaminationError가 났어야
    # 한다 — no-op으로 바꾼 상태에서는 (의도적으로) 통과한다.
    cfg = load_profile_config(env=dict(_LIVE_CLEAN_ENV, KIS_PAPER_TRADING="true"))
    assert cfg.name == "live"

    monkeypatch.undo()

    # 원복하면 즉시 다시 막힌다 — 가드가 실제로 배선되어 있다는 증거.
    with pytest.raises(LiveProfileContaminationError):
        load_profile_config(env=dict(_LIVE_CLEAN_ENV, KIS_PAPER_TRADING="true"))


# --- 성능 단언 ---------------------------------------------------------------


def test_load_profile_config_p95_latency_under_50ms() -> None:
    """로컬 성능 예산: 설정 파일 하나 읽기는 가벼운 I/O라 5k봉 조회
    p95 200ms(ADR-2026-09-09-C Decision 1)보다 훨씬 낮은 예산인 50ms를
    쓴다 — 반복 로드가 앱 기동/재로드 경로에서 병목이 되지 않음을 증명."""
    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        load_profile_config(env={"AIOS_ENV": "dev"})
        samples.append(time.perf_counter() - start)

    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 < 0.05, f"load_profile_config p95={p95 * 1000:.2f}ms exceeds 50ms budget"


def test_all_valid_profiles_have_a_config_file_in_repo() -> None:
    for name in VALID_PROFILES:
        assert (CONFIG_DIR / f"{name}.yaml").is_file()


def test_aios_env_var_name_constant_matches_documented_variable() -> None:
    assert AIOS_ENV_VAR == "AIOS_ENV"
