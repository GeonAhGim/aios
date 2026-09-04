"""R-27 §3.5 각주 — ASSET_CLASS 노출 집계용 정적 심볼→자산군 화이트리스트.

거래소 API로 심볼의 실제 자산군을 조회하는 배선은 아직 없다(전수감사
§R9 미해결) — 이 화이트리스트에 없는 심볼을 만나면 CRYPTO 등 임의
버킷으로 조용히 뭉개지 않고 UNKNOWN으로 분리한다. exposure_snapshot.py가
그 UNKNOWN 총액을 그대로 상위(RiskEngine)에 넘겨 fail-closed 판단을
가능하게 한다(I-06). 미검증: 여기 CRYPTO로 분류된 심볼이 실제로 모든
거래소에서 거래 가능한지는 ExchangeCapability가 별도로 검증한다.
"""
from __future__ import annotations

from src.data.models.base import AssetClass

UNKNOWN_ASSET_CLASS = "UNKNOWN"

_CRYPTO_SYMBOLS = (
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "BNB/USDT", "DOGE/USDT",
    "ADA/USDT", "AVAX/USDT", "LINK/USDT", "LTC/USDT", "MATIC/USDT", "DOT/USDT",
    "TRX/USDT", "TON/USDT", "SHIB/USDT",
)
_SYMBOL_ASSET_CLASS: dict[str, AssetClass] = dict.fromkeys(_CRYPTO_SYMBOLS, AssetClass.CRYPTO)


def asset_class_for(symbol: str) -> str:
    """화이트리스트에 없으면 `UNKNOWN_ASSET_CLASS`(자산군 판정 불가, 0 아님)."""
    asset_class = _SYMBOL_ASSET_CLASS.get(symbol)
    return asset_class.value if asset_class is not None else UNKNOWN_ASSET_CLASS
