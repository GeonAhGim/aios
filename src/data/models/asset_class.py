"""R-27 §3.5 footnote — static symbol-to-asset-class whitelist for ASSET_CLASS exposure aggregation.

No wiring exists yet to query the actual asset class of a symbol via exchange
API (full-audit §R9 unresolved) — when we encounter a symbol not in this
whitelist, we isolate it as UNKNOWN rather than quietly collapsing it into an
arbitrary bucket like CRYPTO. exposure_snapshot.py passes that UNKNOWN total
verbatim to the upstream (RiskEngine), enabling fail-closed judgment (I-06).
Unverified: ExchangeCapability separately validates whether symbols classified
as CRYPTO here are actually tradable on all exchanges.
"""

from __future__ import annotations

from src.data.models.base import AssetClass

UNKNOWN_ASSET_CLASS = "UNKNOWN"

_CRYPTO_SYMBOLS = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "MATIC/USDT",
    "DOT/USDT",
    "TRX/USDT",
    "TON/USDT",
    "SHIB/USDT",
)
_SYMBOL_ASSET_CLASS: dict[str, AssetClass] = dict.fromkeys(_CRYPTO_SYMBOLS, AssetClass.CRYPTO)


def asset_class_for(symbol: str) -> str:
    """Returns `UNKNOWN_ASSET_CLASS` if not in whitelist (asset class undetermined, not zero)."""
    asset_class = _SYMBOL_ASSET_CLASS.get(symbol)
    return asset_class.value if asset_class is not None else UNKNOWN_ASSET_CLASS
