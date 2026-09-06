"""DC-27 — source_contract: 소스 계약 등급·재배포 스코프(순수 판정).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D1/D2/D7. `entitlements`(9049e2b6b0b7:100, `domain/entitlement/policy.py`
DC-9)와 키링 핸들 패턴(`src/foundation/connections/domain/models.py`
`CredentialBinding.vault_secret_ref`)을 확장한다 — 새 컨텍스트를 만들지
않는다(D1 "신규 컨텍스트 아님").

`entitlements`가 "이 테넌트가 이 벤처를 볼 수 있는가"를 답한다면,
`source_contract`는 "우리가 이 소스를 어떤 등급으로, 어디까지 보여줘도
되는가"를 답한다 — 주체는 테넌트가 아니라 플랫폼이 맺은 소스별 계약
1건이다(D1 "등급을 행으로 교체"). 어댑터(예: 향후 OPENDART/ECOS
ingest_source, D6 "계약 전에는 등록하지 않는다")는 `source_id` 문자열만
알고 이 행을 모른다 — `authorize_source()`가 호출 전 게이트가 읽는
유일한 판정 함수다. 등급 승격은 이 행의 UPDATE 하나이고 어댑터 코드·
환경변수는 바뀌지 않는다(task-1764 DoD, 테스트로 증명).

이 모듈은 I/O가 없는 순수 규칙이다 — 저장(`source_contract` 테이블)은
`ports/source_contract_repository.py` + `adapters/postgres_source_contract.py`
소관이고, `source_id`만 아는 어댑터 앞에 이 게이트를 붙이는 배선은
`application/authorize_source_access.py` 소관이다.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import AwareDatetime, BaseModel, model_validator

__all__ = [
    "SourceContractTier",
    "RedistributionScope",
    "DataUse",
    "SourceCapability",
    "SourceContract",
    "SourceContractDenialReason",
    "SourceContractGrant",
    "authorize_source",
    "permits_use",
]


class SourceContractTier(str, Enum):
    FREE = "FREE"
    PERSONAL = "PERSONAL"
    BUSINESS = "BUSINESS"
    ENTERPRISE = "ENTERPRISE"


class RedistributionScope(str, Enum):
    """D2/D7. 미지정은 `NONE`으로 취급한다 — "기존 entitlement와 같은
    fail-closed 규율"(D2)."""

    NONE = "NONE"
    USER_SCOPED = "USER_SCOPED"
    INTERNAL = "INTERNAL"
    DISPLAY = "DISPLAY"
    REDISTRIBUTE = "REDISTRIBUTE"


class DataUse(str, Enum):
    """D2가 못박은 강제 지점(읽기 API·차트/백테스트·내보내기) + D7의
    "연결 소유자 본인 표시"를 판정 가능한 사용 목적으로 나눈 것.
    `permits_use()`의 입력이며, 강제 지점 코드는 이 값 중 하나로 자신의
    요청을 분류해 판정을 위임해야 한다(문자열 비교 재구현 금지)."""

    INTERNAL_CALC = "INTERNAL_CALC"
    USER_OWN_DISPLAY = "USER_OWN_DISPLAY"
    SHARED_DISPLAY = "SHARED_DISPLAY"
    EXPORT_OR_RESELL = "EXPORT_OR_RESELL"


_PERMITTED_USES: dict[RedistributionScope, frozenset[DataUse]] = {
    RedistributionScope.NONE: frozenset(),
    RedistributionScope.USER_SCOPED: frozenset({DataUse.USER_OWN_DISPLAY}),
    RedistributionScope.INTERNAL: frozenset({DataUse.INTERNAL_CALC}),
    RedistributionScope.DISPLAY: frozenset(
        {DataUse.INTERNAL_CALC, DataUse.USER_OWN_DISPLAY, DataUse.SHARED_DISPLAY}
    ),
    RedistributionScope.REDISTRIBUTE: frozenset(
        {
            DataUse.INTERNAL_CALC,
            DataUse.USER_OWN_DISPLAY,
            DataUse.SHARED_DISPLAY,
            DataUse.EXPORT_OR_RESELL,
        }
    ),
}


def permits_use(scope: RedistributionScope, use: DataUse) -> bool:
    """D2 "선언이 아니라 구조로 막는다"의 순수 판정 절반. `USER_SCOPED`는
    `USER_OWN_DISPLAY`만 허용한다 — 공유 캐시·스크리너·타 사용자 응답
    조립(`SHARED_DISPLAY`)에는 절대 쓰이지 않는다(D7)."""
    return use in _PERMITTED_USES[scope]


class SourceCapability(BaseModel, frozen=True):
    """D6 "능력 기술(capability: 자산군·해상도·기업행위 유무)". 자산군·
    해상도는 소스마다 어휘가 달라(예: ECOS는 매크로 지표, KRX는 자산군)
    trading `AssetClass`/`Timeframe` enum으로 좁히지 않고 문자열로 둔다."""

    asset_classes: frozenset[str]
    resolutions: frozenset[str]
    corporate_actions: bool = False


class SourceContract(BaseModel, frozen=True):
    """D1 표를 그대로 옮긴 계약 1행의 순수 표현. `credential_ref`는 키링
    핸들 문자열일 뿐(D1 "실제 키는 여기 없다") — 이 모델에는 원문 키가
    담길 필드 자체가 없으므로 직렬화해도 새어나올 수 없다."""

    source_id: str
    tier: SourceContractTier
    credential_ref: str
    redistribution_scope: RedistributionScope
    rate_limit: int
    quota: int
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    capability: SourceCapability

    @model_validator(mode="after")
    def _valid_range(self) -> SourceContract:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to는 valid_from보다 뒤여야 한다")
        return self


class SourceContractDenialReason(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    NOT_YET_VALID = "NOT_YET_VALID"
    EXPIRED = "EXPIRED"


class SourceContractGrant(BaseModel, frozen=True):
    """게이트 판정 결과. 허용/거부가 서로 배타적인 필드 조합만 만들 수
    있게 강제한다(`domain/entitlement/policy.py`의 `Entitlement`와 동일
    패턴)."""

    allowed: bool
    tier: SourceContractTier | None
    redistribution_scope: RedistributionScope | None
    rate_limit: int | None
    quota: int | None
    capability: SourceCapability | None
    denial_reason: SourceContractDenialReason | None

    @model_validator(mode="after")
    def _allow_deny_are_exclusive(self) -> SourceContractGrant:
        grant_fields = (
            self.tier,
            self.redistribution_scope,
            self.rate_limit,
            self.quota,
            self.capability,
        )
        if self.allowed:
            if self.denial_reason is not None or any(f is None for f in grant_fields):
                raise ValueError(
                    "allowed=True는 계약 필드를 전부 채우고 denial_reason은 비워야 한다"
                )
        else:
            if self.denial_reason is None or any(f is not None for f in grant_fields):
                raise ValueError("allowed=False는 denial_reason만 채우고 계약 필드는 비워야 한다")
        return self


def _deny(reason: SourceContractDenialReason) -> SourceContractGrant:
    return SourceContractGrant(
        allowed=False,
        tier=None,
        redistribution_scope=None,
        rate_limit=None,
        quota=None,
        capability=None,
        denial_reason=reason,
    )


def authorize_source(contract: SourceContract | None, as_of: datetime) -> SourceContractGrant:
    """`source_id`로 조회한 행(없을 수 있음) → 접근 가능 여부.

    fail-closed: 행이 없거나(`NOT_FOUND`), 아직 발효 전이거나
    (`NOT_YET_VALID`), 만료됐으면(`EXPIRED`) 전부 거부다 — "정보 결손은
    거부"(`domain/entitlement/policy.py`와 동일 원칙).

    `as_of`는 호출자가 넘기는 결정론적 시계 입력이다(순수 함수는 현재
    시각을 스스로 읽지 않는다).
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of는 tz-aware datetime만 받는다")

    if contract is None:
        return _deny(SourceContractDenialReason.NOT_FOUND)
    if as_of < contract.valid_from:
        return _deny(SourceContractDenialReason.NOT_YET_VALID)
    if contract.valid_to is not None and as_of >= contract.valid_to:
        return _deny(SourceContractDenialReason.EXPIRED)

    return SourceContractGrant(
        allowed=True,
        tier=contract.tier,
        redistribution_scope=contract.redistribution_scope,
        rate_limit=contract.rate_limit,
        quota=contract.quota,
        capability=contract.capability,
        denial_reason=None,
    )
