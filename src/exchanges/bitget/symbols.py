"""FULL_AUDIT_2026-09-02.md §2-B ④ — Bitget 심볼 정규화 단일 소스.

이전에는 market_data_mixin.py/futures_market_mixin.py/grid_mixin.py/
strategy_mixin.py 4곳에 `_to_bitget_symbol()`이 각자 독립적으로
복제돼 있었다(내용은 우연히 동일했음) — 하나만 고치고 나머지를
깜빡하면 조용히 서로 어긋날 위험이 있어 단일 모듈로 통합한다.
`to_canonical_symbol()`은 futures_market_mixin.py에만 있던 역방향
변환(Phase 1 스콥, 06번 §6.1 — USDT 마켓만 지원)도 함께 통합.
"""
from __future__ import annotations

_KNOWN_QUOTE_SUFFIXES = ("USDT",)


def to_bitget_symbol(canonical_symbol: str) -> str:
    """"BTC/USDT" -> "BTCUSDT" """
    return canonical_symbol.replace("/", "")


def to_canonical_symbol(bitget_symbol: str) -> str:
    """"BTCUSDT" -> "BTC/USDT". Phase 1 스콥(06번 §6.1)은 USDT 마켓만
    대상이라 그 접미사만 처리한다 — 매칭 안 되면 원문 그대로 반환."""
    for quote in _KNOWN_QUOTE_SUFFIXES:
        if bitget_symbol.endswith(quote):
            base = bitget_symbol[: -len(quote)]
            return f"{base}/{quote}"
    return bitget_symbol
