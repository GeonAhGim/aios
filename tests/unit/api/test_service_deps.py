"""src/api/service_deps.py — 16번대 서비스 팩토리 Depends 커버리지.

전부 순수 조립 함수(생성자만 호출, I/O 없음)라 실DB 없이 단위테스트 가능.
KeyRing.from_legacy_hex 경로(get_exchange_credential_service)만 잘못된 hex
입력에 fail-closed로 반응하는지 검증한다."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api import service_deps
from src.core.loader.risk_policy_loader import RiskPolicy
from src.core.safety.circuit_breaker import CircuitBreakerService
from src.core.security.key_ring import KeyRingConfigError
from src.services.account_deletion_service import AccountDeletionService
from src.services.alert_service import AlertService
from src.services.approval_settings_service import ApprovalSettingsService
from src.services.credential_resolver import CredentialResolver
from src.services.exchange_credential_service import ExchangeCredentialService
from src.services.wallet_service import WalletService
from src.services.withdrawal_whitelist_service import WithdrawalWhitelistService

_VALID_HEX_KEY = "ab" * 32  # 32 bytes = 64 hex chars, required by KeyRing._decode_key


def _pool() -> MagicMock:
    return MagicMock(name="pool")


def _request_with_secret(hex_key: str) -> MagicMock:
    request = MagicMock(name="request")
    request.app.state.secrets.credential_encryption_key.get_secret_value.return_value = hex_key
    return request


def test_get_risk_policy_loads_real_policy_file():
    policy = service_deps.get_risk_policy()
    assert isinstance(policy, RiskPolicy)


def test_get_risk_policy_propagates_loader_failure(monkeypatch: pytest.MonkeyPatch):
    """failure-injection: load_risk_policy가 스키마 위반으로 실패하면
    get_risk_policy는 조용히 기본값으로 대체하지 않고 그대로 전파해야 한다."""

    def _boom() -> RiskPolicy:
        raise ValueError("bad risk policy config")

    monkeypatch.setattr(service_deps, "load_risk_policy", _boom)

    with pytest.raises(ValueError, match="bad risk policy config"):
        service_deps.get_risk_policy()


def test_get_circuit_breaker_service_wires_pool_and_policy():
    pool = _pool()
    policy = service_deps.get_risk_policy()

    service = service_deps.get_circuit_breaker_service(pool, policy)

    assert isinstance(service, CircuitBreakerService)
    assert service._pool is pool
    assert service._policy is policy.circuit_breaker


def test_get_approval_settings_service_wires_pool():
    pool = _pool()
    service = service_deps.get_approval_settings_service(pool)
    assert isinstance(service, ApprovalSettingsService)
    assert service._pool is pool


def test_get_account_deletion_service_wires_pool():
    pool = _pool()
    service = service_deps.get_account_deletion_service(pool)
    assert isinstance(service, AccountDeletionService)
    assert service._pool is pool


def test_get_wallet_service_wires_pool():
    pool = _pool()
    service = service_deps.get_wallet_service(pool)
    assert isinstance(service, WalletService)
    assert service._pool is pool


def test_get_withdrawal_whitelist_service_wires_secret_and_publish():
    pool = _pool()
    request = _request_with_secret(_VALID_HEX_KEY)
    circuit_breaker = MagicMock(spec=CircuitBreakerService)
    event_bus = MagicMock()

    service = service_deps.get_withdrawal_whitelist_service(
        request, pool, circuit_breaker, event_bus
    )

    assert isinstance(service, WithdrawalWhitelistService)
    assert service._encryption_key == _VALID_HEX_KEY
    assert service._publish is event_bus.publish


def test_get_withdrawal_whitelist_service_missing_secrets_state_raises():
    """negative: main.py lifespan이 아직 app.state.secrets를 채우지 않은 상태
    (예: 테스트 클라이언트 오구성)에서는 명확한 예외로 fail-closed해야 한다."""
    pool = _pool()
    request = MagicMock(spec=["app"])
    request.app = MagicMock(spec=["state"])
    request.app.state = MagicMock(spec=[])
    circuit_breaker = MagicMock(spec=CircuitBreakerService)
    event_bus = MagicMock()

    with pytest.raises(AttributeError):
        service_deps.get_withdrawal_whitelist_service(request, pool, circuit_breaker, event_bus)


def test_get_exchange_credential_service_wires_key_ring():
    pool = _pool()
    request = _request_with_secret(_VALID_HEX_KEY)

    service = service_deps.get_exchange_credential_service(request, pool)

    assert isinstance(service, ExchangeCredentialService)


def test_get_exchange_credential_service_invalid_hex_length_raises():
    """negative: 32바이트(64자)가 아닌 hex 키는 KeyRingConfigError로 거부되어야
    한다 — 조용히 잘린 키로 진행하지 않는다."""
    pool = _pool()
    request = _request_with_secret("ab" * 10)

    with pytest.raises(KeyRingConfigError):
        service_deps.get_exchange_credential_service(request, pool)


def test_get_exchange_credential_service_non_hex_value_raises():
    """negative: hex로 디코드 불가능한 값은 KeyRingConfigError로 거부되어야 한다."""
    pool = _pool()
    request = _request_with_secret("not-hex-at-all")

    with pytest.raises(KeyRingConfigError):
        service_deps.get_exchange_credential_service(request, pool)


def test_get_credential_resolver_returns_app_state_singleton():
    """docstring이 명시하는 계약: 매 요청 새로 만들지 않고 lifespan이 만든
    싱글턴을 그대로 반환해야 5분 TTL 캐시가 재사용된다."""
    request = MagicMock(name="request")
    resolver = MagicMock(spec=CredentialResolver)
    request.app.state.credential_resolver = resolver

    assert service_deps.get_credential_resolver(request) is resolver


def test_get_alert_service_wires_resolver_and_publish():
    pool = _pool()
    resolver = MagicMock(spec=CredentialResolver)
    event_bus = MagicMock()

    service = service_deps.get_alert_service(pool, resolver, event_bus)

    assert isinstance(service, AlertService)
    assert service._resolver is resolver
    assert service._publish is event_bus.publish
