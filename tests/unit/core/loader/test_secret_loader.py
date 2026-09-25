import time

import pytest

import src.core.loader.secret_loader as secret_loader_module
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


def test_load_env_secrets_missing_bitget_secret_raises():
    """negative — 경계값: 다른 필수 키(BITGET_API_SECRET)가 빠진 경우도
    동일하게 KeyError로 fail-closed 해야 한다 (단일 키 누락 케이스에만
    의존하지 않기 위한 추가 경계 검증)."""
    incomplete = dict(MINIMAL_ENV)
    del incomplete["BITGET_API_SECRET"]
    with pytest.raises(KeyError):
        load_env_secrets(incomplete)


def test_load_env_secrets_non_numeric_jwt_expire_minutes_raises():
    """negative — 잘못된 형식: JWT_EXPIRE_MINUTES가 정수로 파싱 불가능한
    문자열이면 조용히 기본값으로 폴백하지 않고 ValueError로 실패해야
    한다(fail-closed)."""
    env = {**MINIMAL_ENV, "JWT_EXPIRE_MINUTES": "not-a-number"}
    with pytest.raises(ValueError):
        load_env_secrets(env)


def test_load_env_secrets_non_numeric_smtp_port_raises():
    """negative — 잘못된 형식: SMTP_PORT가 정수로 파싱 불가능하면
    ValueError로 실패해야 한다."""
    env = {**MINIMAL_ENV, "SMTP_PORT": "not-a-port"}
    with pytest.raises(ValueError):
        load_env_secrets(env)


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


def test_load_env_secrets_merges_dotenv_and_os_environ(monkeypatch: pytest.MonkeyPatch):
    """source=None 경로(_merged_environment) — os.environ이 .env 파일 값을
    덮어써야 한다(실제 배포 환경변수가 로컬 .env보다 우선)."""
    monkeypatch.setattr(
        secret_loader_module,
        "dotenv_values",
        lambda path: {**MINIMAL_ENV, "JWT_ALGORITHM": "from-dotenv"},
    )
    monkeypatch.setattr(
        secret_loader_module.os,
        "environ",
        {"JWT_ALGORITHM": "from-os-environ"},
    )

    bundle = load_env_secrets()

    assert bundle.jwt_algorithm == "from-os-environ"


def test_load_env_secrets_propagates_dotenv_read_failure(monkeypatch: pytest.MonkeyPatch):
    """failure injection — 의존성(dotenv_values)이 파일 I/O 오류 등으로
    예외를 던지면 삼키거나 빈 설정으로 폴백하지 않고 그대로 전파해야
    한다(fail-closed)."""

    def _boom(path):
        raise OSError("dotenv read failed")

    monkeypatch.setattr(secret_loader_module, "dotenv_values", _boom)

    with pytest.raises(OSError):
        load_env_secrets()


@pytest.mark.perf
def test_load_env_secrets_repeated_calls_stay_within_perf_budget():
    """성능 단언 — load_env_secrets는 부팅 경로에서 반복 호출될 수 있으니
    (헬스체크·워커 재기동 등) 단일 호출 비용이 병적으로 커지면 안 된다."""
    start = time.perf_counter()
    for _ in range(500):
        load_env_secrets(MINIMAL_ENV)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0
