"""RD-5 -- `domain/entity_link.py` unit tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 9 RD-5
DoD (a)-(d).
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)
from src.foundation.market_data.domain.instruments import symbol_master
from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import (
    EntityKey,
    EntityKeyKind,
    UnmappedReason,
    extract_entity_key,
    is_valid_isin,
    link_item,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_VALID_ISIN = "KR7005930003"  # 미검증: 삼성전자 보통주 ISIN(공개 문헌 인용, 거래소 원문 대조 없음)


def _item(*, instruments: tuple[str, ...] = (), item_id: UUID | None = None) -> ResearchItem:
    return ResearchItem(
        item_id=item_id or uuid4(),
        source_id="OPENDART",
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=instruments,
        title="title",
        body_ref=None,
        url="https://example.invalid/x",
        language="ko",
        hash="deadbeef",
        revision_of=None,
    )


def _instrument(instrument_id: str) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        asset_class=AssetClass.US_EQUITY,
        base=None,
        quote=None,
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("1"),
        calendar_id="XNYS",
        lifecycle_state=InstrumentLifecycle.ACTIVE,
        created_at=_NOW,
    )


def _listing(instrument_id: str, venue: Venue, symbol: str) -> VenueListing:
    return VenueListing(
        instrument_id=instrument_id,
        venue=venue,
        venue_symbol=symbol,
        listed_at=_NOW,
        delisted_at=None,
        is_primary=True,
    )


def test_extract_entity_key_krx_code() -> None:
    assert extract_entity_key("005930") == EntityKey(EntityKeyKind.KRX_CODE, "005930")


def test_extract_entity_key_corp_reg_no() -> None:
    assert extract_entity_key("1101110097494") == EntityKey(
        EntityKeyKind.CORP_REG_NO, "1101110097494"
    )


def test_extract_entity_key_isin() -> None:
    assert extract_entity_key(_VALID_ISIN) == EntityKey(EntityKeyKind.ISIN, _VALID_ISIN)


def test_extract_entity_key_ticker_venue() -> None:
    assert extract_entity_key("aapl:kis_us") == EntityKey(
        EntityKeyKind.TICKER_VENUE, "AAPL", venue=Venue.KIS_US
    )


def test_extract_entity_key_name_only_is_no_deterministic_key() -> None:
    assert extract_entity_key("삼성전자우 유상증자") is None


def test_is_valid_isin_accepts_correct_check_digit() -> None:
    assert is_valid_isin(_VALID_ISIN) is True


def test_is_valid_isin_rejects_tampered_check_digit() -> None:
    forged = _VALID_ISIN[:-1] + str((int(_VALID_ISIN[-1]) + 1) % 10)
    assert is_valid_isin(forged) is False


def test_link_item_with_name_only_instruments_is_unmapped_without_exception() -> None:
    item = _item(instruments=("삼성전자우 유상증자",))
    resolver = Mock()

    result = link_item(item, resolver)

    assert result.instrument_id is None
    assert result.reason is UnmappedReason.NO_DETERMINISTIC_KEY
    resolver.resolve.assert_not_called()


def test_link_item_maps_via_resolver() -> None:
    item = _item(instruments=("005930",))
    resolver = Mock()
    resolver.resolve.return_value = "INSTR-1"

    result = link_item(item, resolver)

    assert result.item_id == item.item_id
    assert result.instrument_id == "INSTR-1"
    assert result.reason is None
    resolver.resolve.assert_called_once_with(EntityKey(EntityKeyKind.KRX_CODE, "005930"))


def test_link_item_deterministic_key_not_found_is_unmapped_without_exception() -> None:
    item = _item(instruments=("005930",))
    resolver = Mock()
    resolver.resolve.return_value = None

    result = link_item(item, resolver)

    assert result.instrument_id is None
    assert result.reason is UnmappedReason.NOT_FOUND


def test_link_item_ticker_venue_lookup_delegates_to_symbol_master_resolve() -> None:
    """RD-5 DoD (b): removing the delegation call (e.g. hardcoding a result
    instead of calling `resolver.resolve`) makes this assertion fail."""
    instrument_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    instruments = [_instrument(instrument_id)]
    listings = [_listing(instrument_id, Venue.KIS_US, "AAPL")]

    def _real_resolve(key: EntityKey) -> str | None:
        try:
            ref = symbol_master.resolve(
                key.venue, key.value, instruments=instruments, listings=listings
            )
        except symbol_master.InstrumentNotFoundError:
            return None
        return str(ref.instrument.instrument_id)

    resolver = Mock(side_effect=None)
    resolver.resolve = Mock(side_effect=_real_resolve)

    item = _item(instruments=("AAPL:KIS_US",))
    result = link_item(item, resolver)

    resolver.resolve.assert_called_once_with(
        EntityKey(EntityKeyKind.TICKER_VENUE, "AAPL", venue=Venue.KIS_US)
    )
    assert result.instrument_id == instrument_id
    assert result.reason is None


_BANNED_FUZZY_MODULES = frozenset({"difflib", "rapidfuzz", "Levenshtein", "fuzzywuzzy"})


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module.split(".")[0])
    return found


def test_no_name_similarity_library_is_imported_by_rd5_leaf() -> None:
    """RD-5 DoD (a) mechanical enforcement of RD-A4: adding an import of any
    of these libraries to either file must make this test fail."""
    root = Path(__file__).resolve().parents[4]
    targets = [
        root / "src/foundation/research_data/domain/entity_link.py",
        root / "src/foundation/research_data/application/link_entities.py",
    ]
    for path in targets:
        found = _imported_top_level_modules(path) & _BANNED_FUZZY_MODULES
        assert not found, f"{path}: RD-A4 violation -- forbidden import found: {found}"
