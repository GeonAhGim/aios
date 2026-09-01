import pytest

from src.core.loader.secret_loader import load_env_secrets

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://user:password@localhost:5432/aios_dev",
    "JWT_SECRET_KEY": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "test-key",
    "BITGET_API_KEY": "bk",
    "BITGET_API_SECRET": "bs",
    "KIS_APP_KEY": "kk",
    "KIS_APP_SECRET": "ks",
}


def test_load_env_secrets_from_explicit_source():
    bundle = load_env_secrets(MINIMAL_ENV)

    assert bundle.database_url.get_secret_value() == MINIMAL_ENV["DATABASE_URL"]
    assert bundle.jwt_algorithm == "HS256"  # 기본값
    assert bundle.jwt_expire_minutes == 60  # 기본값
    assert bundle.smtp_host is None


def test_load_env_secrets_optional_fields_populated():
    env = {**MINIMAL_ENV, "SMTP_HOST": "smtp.example.com", "JWT_EXPIRE_MINUTES": "120"}
    bundle = load_env_secrets(env)

    assert bundle.smtp_host == "smtp.example.com"
    assert bundle.jwt_expire_minutes == 120


def test_load_env_secrets_missing_required_key_raises():
    incomplete = dict(MINIMAL_ENV)
    del incomplete["JWT_SECRET_KEY"]
    with pytest.raises(KeyError):
        load_env_secrets(incomplete)


def test_load_env_secrets_repr_never_leaks_values():
    bundle = load_env_secrets(MINIMAL_ENV)
    assert "test-secret" not in repr(bundle)


def test_load_env_secrets_model_dump_never_leaks_values():
    """docs/RED_TEAM_FINDINGS.md #10 회귀 — __repr__/__str__만 마스킹해서는
    FastAPI가 실제로 쓰는 model_dump()/model_dump_json() 경로를 그대로
    우회해 평문을 반환했다. SecretStr로 바꾼 뒤에는 이 경로도 마스킹돼야
    한다."""
    bundle = load_env_secrets(MINIMAL_ENV)

    dumped = bundle.model_dump()
    dumped_json = bundle.model_dump_json()

    for value in MINIMAL_ENV.values():
        assert value not in repr(dumped)
        assert value not in dumped_json
