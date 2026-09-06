"""DC-27 — domain/entitlement/source_contract 단위 테스트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1/D2/D7. task-1764 DoD 3종을 여기서 증명한다:
1) 등급 승격이 행 갱신만으로 반영되고 어댑터 코드·환경변수가 바뀌지 않음.
2) `valid_to` 경과 계약은 fail-closed 거부.
3) `credential_ref`로 키 원문이 새어나오지 않음(직렬화 스냅샷).
docs/design/INVARIANTS.md I-10 — 이 테스트가 그 증명이다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractDenialReason,
    SourceContractTier,
    authorize_source,
    permits_use,
)

_NOW = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)


def _contract(
    *,
    source_id: str = "OPENDART",
    tier: SourceContractTier = SourceContractTier.PERSONAL,
    credential_ref: str = "vault:opendart:v1",
    redistribution_scope: RedistributionScope = RedistributionScope.INTERNAL,
    rate_limit: int = 100,
    quota: int = 1000,
    valid_from: datetime = _NOW - timedelta(days=30),
    valid_to: datetime | None = None,
    capability: SourceCapability | None = None,
) -> SourceContract:
    return SourceContract(
        source_id=source_id,
        tier=tier,
        credential_ref=credential_ref,
        redistribution_scope=redistribution_scope,
        rate_limit=rate_limit,
        quota=quota,
        valid_from=valid_from,
        valid_to=valid_to,
        capability=capability
        or SourceCapability(
            asset_classes=frozenset({"EQUITY_KR"}),
            resolutions=frozenset({"1d"}),
            corporate_actions=False,
        ),
    )


def _adapter_capability(source_id: str, row: SourceContract | None) -> frozenset[str] | None:
    """"어댑터는 source_id만 알고 등급을 모른다"를 흉내낸 헬퍼 — 시그니처가
    `source_id`와 게이트 판정 결과만 받고, 등급 자체는 갖고 있지 않다.
    아래 테스트에서 이 함수의 코드는 승격 전/후 단 한 번도 바뀌지 않는다."""
    grant = authorize_source(row, _NOW)
    if not grant.allowed:
        return None
    assert grant.capability is not None
    return grant.capability.asset_classes


def test_tier_upgrade_via_row_update_only_changes_capability_not_adapter_code() -> None:
    personal_row = _contract(
        tier=SourceContractTier.PERSONAL,
        rate_limit=10,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY_KR"}), resolutions=frozenset({"1d"})
        ),
    )
    # 승격은 "새 행"이 아니라 같은 source_id 행의 필드 갱신이다(D1).
    enterprise_row = _contract(
        tier=SourceContractTier.ENTERPRISE,
        rate_limit=10_000,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY_KR", "EQUITY_US"}), resolutions=frozenset({"1d", "1m"})
        ),
    )

    before = _adapter_capability("OPENDART", personal_row)
    after = _adapter_capability("OPENDART", enterprise_row)

    assert before == frozenset({"EQUITY_KR"})
    assert after == frozenset({"EQUITY_KR", "EQUITY_US"})
    assert before != after


def test_expired_contract_denied_fail_closed() -> None:
    expired = _contract(valid_from=_NOW - timedelta(days=60), valid_to=_NOW - timedelta(days=1))

    grant = authorize_source(expired, _NOW)

    assert grant.allowed is False
    assert grant.denial_reason == SourceContractDenialReason.EXPIRED
    assert grant.tier is None
    assert grant.capability is None


def test_not_yet_effective_contract_denied() -> None:
    future = _contract(valid_from=_NOW + timedelta(days=1), valid_to=None)

    grant = authorize_source(future, _NOW)

    assert grant.allowed is False
    assert grant.denial_reason == SourceContractDenialReason.NOT_YET_VALID


def test_missing_contract_denied_not_found() -> None:
    grant = authorize_source(None, _NOW)

    assert grant.allowed is False
    assert grant.denial_reason == SourceContractDenialReason.NOT_FOUND


def test_active_contract_at_valid_to_boundary_is_expired() -> None:
    """negative: `valid_to`는 배타적 상한이다 — 그 시각과 정확히 같은
    `as_of`는 이미 만료로 취급한다(0-폭 유예 없음, fail-closed)."""
    boundary = _contract(valid_from=_NOW - timedelta(days=10), valid_to=_NOW)

    grant = authorize_source(boundary, _NOW)

    assert grant.allowed is False
    assert grant.denial_reason == SourceContractDenialReason.EXPIRED


def test_authorize_source_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError):
        authorize_source(_contract(), datetime(2026, 9, 7))


def test_valid_to_before_valid_from_rejected_at_construction() -> None:
    """negative: 잘못된 계약 기간은 판정 이전에 생성 자체가 막힌다."""
    with pytest.raises(ValidationError):
        _contract(
            valid_from=_NOW,
            valid_to=_NOW - timedelta(days=1),
        )


def test_credential_ref_snapshot_does_not_leak_raw_secret() -> None:
    contract = _contract(credential_ref="vault:opendart:v3")

    dumped = contract.model_dump()

    # 스냅샷: 이 모델이 노출하는 필드는 이 집합뿐이어야 한다 — 새 필드가
    # 몰래 추가돼(예: 원문 키를 담는 필드) 이 목록과 어긋나면 실패한다.
    assert set(dumped) == {
        "source_id",
        "tier",
        "credential_ref",
        "redistribution_scope",
        "rate_limit",
        "quota",
        "valid_from",
        "valid_to",
        "capability",
    }
    # credential_ref는 키링 핸들 문자열 그대로만 나온다 — 복호된 키가
    # 아니다(이 모듈은 애초에 복호 기능을 갖지 않는다).
    assert dumped["credential_ref"] == "vault:opendart:v3"
    assert "api_key" not in dumped
    assert "api_secret" not in dumped


@pytest.mark.parametrize(
    ("scope", "use", "expected"),
    [
        (RedistributionScope.NONE, DataUse.INTERNAL_CALC, False),
        (RedistributionScope.NONE, DataUse.USER_OWN_DISPLAY, False),
        (RedistributionScope.USER_SCOPED, DataUse.USER_OWN_DISPLAY, True),
        (RedistributionScope.USER_SCOPED, DataUse.SHARED_DISPLAY, False),
        (RedistributionScope.INTERNAL, DataUse.INTERNAL_CALC, True),
        (RedistributionScope.INTERNAL, DataUse.SHARED_DISPLAY, False),
        (RedistributionScope.DISPLAY, DataUse.SHARED_DISPLAY, True),
        (RedistributionScope.DISPLAY, DataUse.EXPORT_OR_RESELL, False),
        (RedistributionScope.REDISTRIBUTE, DataUse.EXPORT_OR_RESELL, True),
    ],
)
def test_permits_use_matrix(scope: RedistributionScope, use: DataUse, expected: bool) -> None:
    assert permits_use(scope, use) is expected
