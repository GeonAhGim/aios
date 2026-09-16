"""U-15 personal-conservative 정책 번들 YAML 로더.

`src/core/loader/risk_policy_loader.py`와 동일 원칙 — pydantic
`extra="forbid"`로 오타/미지 키를 로드 성공으로 넘기지 않는다(fail-closed).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from src.core.loader.config_loader import load_config
from src.foundation.risk.contracts.v1 import PersonalRiskBundleConfigV1
from src.foundation.risk.domain.models import PersonalRiskBundle

DEFAULT_PERSONAL_BUNDLE_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "risk_policy" / "personal-conservative.yaml"
)


def load_personal_bundle(path: Path | None = None) -> PersonalRiskBundle:
    target = path if path is not None else DEFAULT_PERSONAL_BUNDLE_PATH
    raw = load_config(target)
    config = PersonalRiskBundleConfigV1(**raw)
    return PersonalRiskBundle(
        name=config.name,
        position_pct_of_equity=Decimal(str(config.position_pct_of_equity)),
        daily_loss_kill_pct=Decimal(str(config.daily_loss_kill_pct)),
        max_exposure_pct=Decimal(str(config.max_exposure_pct)),
        default_notional_cap_krw=Decimal(str(config.default_notional_cap_krw)),
        symbol_whitelist=frozenset(config.symbol_whitelist),
        exchange_notional_caps={
            k: Decimal(str(v)) for k, v in config.exchange_notional_caps.items()
        },
    )
