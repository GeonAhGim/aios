"""L4-30b — canary.yaml 리스크 정책 번들 로더.

Spec: task-2750. `scripts/canary_bitget.py`에서 분리된 책임 단위 —
`config/risk_policy/canary.yaml`을 읽어 `CanaryLimits`로 정규화하는 부분만
담당한다(RATCHET-split task-10863, ADR-2026-09-10-C LOC 규율).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.core.loader.config_loader import load_config
from src.exchanges.bitget.symbols import to_canonical_symbol

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "risk_policy" / "canary.yaml"


class CanaryConfigError(Exception):
    """canary.yaml 스키마 위반 또는 필수 환경변수 누락(fail-closed)."""


class _CanaryConfigV1(BaseModel):
    """personal-conservative.yaml과 동일 원칙 — `extra="forbid"`로 오타·
    미지 키가 조용히 통과하는 것을 막는다(fail-closed)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = Field(gt=0)
    max_order_usdt: float = Field(gt=0)
    max_total_usdt: float = Field(gt=0)
    symbols: list[str] = Field(default_factory=list)
    max_fills: int = Field(gt=0)
    stop_on_daily_loss: bool = True


@dataclass(frozen=True)
class CanaryLimits:
    """`config/risk_policy/canary.yaml`의 순수 도메인 표현. `symbols`는
    정규 표기("BTC/USDT")로 정규화해 보관한다 — `Order.symbol`/게이트 평가가
    전부 정규 표기를 쓰기 때문에(거래소 네이티브 표기는 YAML 파일에서만
    쓴다)."""

    name: str
    max_order_usdt: Decimal
    max_total_usdt: Decimal
    symbols: frozenset[str]
    max_fills: int
    stop_on_daily_loss: bool


def load_canary_limits(path: Path | None = None) -> CanaryLimits:
    target = path if path is not None else DEFAULT_CONFIG_PATH
    raw = load_config(target)
    try:
        cfg = _CanaryConfigV1(**raw)
    except Exception as exc:  # noqa: BLE001 — 스키마 위반은 fail-closed로 재포장
        raise CanaryConfigError(f"{target} 스키마 위반: {exc}") from exc
    return CanaryLimits(
        name=cfg.name,
        max_order_usdt=Decimal(str(cfg.max_order_usdt)),
        max_total_usdt=Decimal(str(cfg.max_total_usdt)),
        symbols=frozenset(to_canonical_symbol(s) for s in cfg.symbols),
        max_fills=cfg.max_fills,
        stop_on_daily_loss=cfg.stop_on_daily_loss,
    )
