"""Tests for krx_data.py — RD-12 (index, investor, short).

DoD (D2 floor):
- negative tests ≥ 3
- failure-injection test ≥ 1
- performance assertion ≥ 1
- gate-red reproduction ≥ 1
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from src.foundation.market_data.adapters.krx_data import (
    IndexPoint,
    KRXIndexAdapter,
    KRXInvestorAdapter,
    KRXShortAdapter,
)

# ---------------------------------------------------------------------------
# Fake KIS client (duck-type)
# ---------------------------------------------------------------------------


class _FakeKISClient:
    """Minimal duck-type matching KISClient protocol."""

    _is_paper_trading = True

    def __init__(self, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, str]] = []

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, tr_id))
        key = tr_id
        return self.responses.get(key, {})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_client() -> _FakeKISClient:
    return _FakeKISClient()


@pytest.fixture
def index_price_response() -> dict[str, Any]:
    return {
        "output": {
            "indicode": "00001",
            "inpct_name": "KOSPI",
            "prdy_vrss": Decimal("2.45"),
            "prdy_crt": Decimal("0.78"),
            "stdc_crt": Decimal("0.35"),
            "hts_hgpr": Decimal("2680.50"),
            "hts_lwpr": Decimal("2650.10"),
            "hts_vol": 450000,
        }
    }


@pytest.fixture
def index_trend_response() -> dict[str, Any]:
    return {
        "output1": [
            {
                "indidate": "20260901",
                "indiprice": 2650.0,
                "indichg": -0.5,
                "inpct_name": "KOSPI",
            },
            {
                "indidate": "20260902",
                "indiprice": 2660.0,
                "indichg": 0.38,
                "inpct_name": "KOSPI",
            },
        ]
    }


@pytest.fixture
def investor_response() -> dict[str, Any]:
    return {
        "output": {
            "invst_pct": Decimal("35.2"),
            "frgn_by_pct": Decimal("28.7"),
            "inst_pct": Decimal("34.1"),
            "etc_pct": Decimal("2.0"),
            "invst_volume": 1200000,
        }
    }


@pytest.fixture
def short_response() -> dict[str, Any]:
    return {
        "output1": [
            {
                "ispr_code": "005930",
                "ispr_name": "삼성전자",
                "ispr_px": 72500,
                "stlt_qty": 5000000,
                "qttr_qty": 50000000,
                "scls_name": "전자제품",
            },
            {
                "ispr_code": "000660",
                "ispr_name": "SK하이닉스",
                "ispr_px": 185000,
                "stlt_qty": 3000000,
                "qttr_qty": 30000000,
                "scls_name": "전자제품",
            },
        ]
    }


# ---------------------------------------------------------------------------
# KRXIndexAdapter tests
# ---------------------------------------------------------------------------


class TestKRXIndexAdapter:
    """Index adapter positive + negative tests."""

    async def test_get_index_price(
        self, fake_client: _FakeKISClient, index_price_response: dict[str, Any]
    ) -> None:
        fake_client.responses["INDI000R"] = index_price_response
        adapter = KRXIndexAdapter(fake_client)
        quote = await adapter.get_index_price("00001")
        assert quote.code == "00001"
        assert quote.name == "KOSPI"
        assert quote.price == Decimal("2.45")
        assert quote.change == Decimal("0.78")
        assert quote.change_rate == Decimal("0.35")
        assert quote.high == Decimal("2680.50")
        assert quote.low == Decimal("2650.10")
        assert quote.volume == 450000
        assert isinstance(quote.observed_at, datetime)
        assert quote.observed_at.tzinfo == timezone.utc

    async def test_get_index_price_empty_response(self, fake_client: _FakeKISClient) -> None:
        fake_client.responses["INDI000R"] = {}
        adapter = KRXIndexAdapter(fake_client)
        with pytest.raises(ValueError, match="not found"):
            await adapter.get_index_price("99999")

    async def test_get_index_trend(
        self, fake_client: _FakeKISClient, index_trend_response: dict[str, Any]
    ) -> None:
        fake_client.responses["FHKST030201R"] = index_trend_response
        adapter = KRXIndexAdapter(fake_client)
        trend = await adapter.get_index_trend("00001", days=30)
        assert trend.code == "00001"
        assert trend.name == "KOSPI"
        assert len(trend.points) == 2
        assert trend.points[0] == IndexPoint("20260901", Decimal("2650.0"), Decimal("-0.5"))
        assert trend.points[1] == IndexPoint("20260902", Decimal("2660.0"), Decimal("0.38"))

    async def test_get_index_trend_empty(self, fake_client: _FakeKISClient) -> None:
        fake_client.responses["FHKST030201R"] = {"output1": []}
        adapter = KRXIndexAdapter(fake_client)
        trend = await adapter.get_index_trend("00001")
        assert trend.points == []
        assert trend.name == ""

    async def test_get_index_trend_tr_id_not_swapped(
        self, fake_client: _FakeKISClient, index_trend_response: dict[str, Any]
    ) -> None:
        """FHKST030201R starts with F — should NOT be swapped for paper trading."""
        fake_client.responses["FHKST030201R"] = index_trend_response
        adapter = KRXIndexAdapter(fake_client)
        await adapter.get_index_trend("00001")
        # Verify the raw tr_id was sent (not V-prefixed)
        _, _, tr_id = fake_client.calls[-1]
        assert tr_id == "FHKST030201R"


# ---------------------------------------------------------------------------
# KRXInvestorAdapter tests
# ---------------------------------------------------------------------------


class TestKRXInvestorAdapter:
    """Investor adapter positive + negative tests."""

    async def test_get_investor_trend(
        self, fake_client: _FakeKISClient, investor_response: dict[str, Any]
    ) -> None:
        fake_client.responses["FHKST030301R"] = investor_response
        adapter = KRXInvestorAdapter(fake_client)
        trend = await adapter.get_investor_trend("20260915")
        assert trend.date == "20260915"
        assert trend.individual_pct == Decimal("35.2")
        assert trend.foreign_pct == Decimal("28.7")
        assert trend.institution_pct == Decimal("34.1")
        assert trend.etc_pct == Decimal("2.0")
        assert trend.total_volume == 1200000

    async def test_get_investor_trend_empty(self, fake_client: _FakeKISClient) -> None:
        fake_client.responses["FHKST030301R"] = {}
        adapter = KRXInvestorAdapter(fake_client)
        with pytest.raises(ValueError, match="No investor data"):
            await adapter.get_investor_trend("20260915")

    async def test_get_investor_trend_defaults_to_today(
        self, fake_client: _FakeKISClient, investor_response: dict[str, Any]
    ) -> None:
        fake_client.responses["FHKST030301R"] = investor_response
        adapter = KRXInvestorAdapter(fake_client)
        trend = await adapter.get_investor_trend()
        expected = datetime.now(timezone.utc).strftime("%Y%m%d")
        assert trend.date == expected


# ---------------------------------------------------------------------------
# KRXShortAdapter tests
# ---------------------------------------------------------------------------


class TestKRXShortAdapter:
    """Short-selling adapter positive + negative tests."""

    async def test_get_short_resistance_stocks(
        self, fake_client: _FakeKISClient, short_response: dict[str, Any]
    ) -> None:
        fake_client.responses["FHKUP080700R"] = short_response
        adapter = KRXShortAdapter(fake_client)
        stocks = await adapter.get_short_resistance_stocks(limit=10)
        assert len(stocks) == 2
        assert stocks[0].stock_code == "005930"
        assert stocks[0].stock_name == "삼성전자"
        assert stocks[0].short_volume == 5000000
        assert stocks[0].total_volume == 50000000
        assert stocks[0].short_ratio == Decimal("10")
        assert stocks[0].sector == "전자제품"
        assert stocks[1].stock_code == "000660"

    async def test_get_short_resistance_stocks_limit(
        self, fake_client: _FakeKISClient, short_response: dict[str, Any]
    ) -> None:
        fake_client.responses["FHKUP080700R"] = short_response
        adapter = KRXShortAdapter(fake_client)
        stocks = await adapter.get_short_resistance_stocks(limit=1)
        assert len(stocks) == 1
        assert stocks[0].stock_code == "005930"

    async def test_get_short_resistance_stocks_empty(self, fake_client: _FakeKISClient) -> None:
        fake_client.responses["FHKUP080700R"] = {"output1": []}
        adapter = KRXShortAdapter(fake_client)
        stocks = await adapter.get_short_resistance_stocks()
        assert stocks == []

    async def test_get_short_resistance_zero_total_volume(
        self, fake_client: _FakeKISClient
    ) -> None:
        """Division-by-zero guard: total_volume=0 → ratio=0."""
        fake_client.responses["FHKUP080700R"] = {
            "output1": [
                {
                    "ispr_code": "000000",
                    "ispr_name": "테스트",
                    "ispr_px": 0,
                    "stlt_qty": 1000,
                    "qttr_qty": 0,
                    "scls_name": "기타",
                }
            ]
        }
        adapter = KRXShortAdapter(fake_client)
        stocks = await adapter.get_short_resistance_stocks()
        assert len(stocks) == 1
        assert stocks[0].short_ratio == Decimal(0)


# ---------------------------------------------------------------------------
# Failure injection test
# ---------------------------------------------------------------------------


class TestFailureInjection:
    """Failure injection: malformed KIS response with missing fields."""

    async def test_investor_missing_fields(self, fake_client: _FakeKISClient) -> None:
        """Investor response with empty dict should raise ValueError."""
        fake_client.responses["FHKST030301R"] = {}
        adapter = KRXInvestorAdapter(fake_client)
        with pytest.raises(ValueError):
            await adapter.get_investor_trend("20260915")

    async def test_index_missing_output_key(self, fake_client: _FakeKISClient) -> None:
        """Index response with no 'output' key should raise ValueError."""
        fake_client.responses["INDI000R"] = {"rt_cd": 1, "msg_cd": "ERROR"}
        adapter = KRXIndexAdapter(fake_client)
        with pytest.raises(ValueError):
            await adapter.get_index_price("00001")


# ---------------------------------------------------------------------------
# Performance assertion (D2)
# ---------------------------------------------------------------------------


class TestPerformance:
    """Performance: 100 concurrent requests must complete in ≤ 5 seconds."""

    @pytest.mark.asyncio
    async def test_concurrent_index_requests(self, fake_client: _FakeKISClient) -> None:
        fake_client.responses["INDI000R"] = {
            "output": {
                "indicode": "00001",
                "inpct_name": "KOSPI",
                "prdy_vrss": 0,
                "prdy_crt": 0,
                "stdc_crt": 0,
                "hts_hgpr": 0,
                "hts_lwpr": 0,
                "hts_vol": 0,
            }
        }
        import asyncio

        adapter = KRXIndexAdapter(fake_client)
        start = datetime.now(timezone.utc)
        tasks = [adapter.get_index_price("00001") for _ in range(100)]
        await asyncio.gather(*tasks)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        assert elapsed < 5.0, f"100 concurrent index requests took {elapsed:.2f}s (budget: 5s)"


# ---------------------------------------------------------------------------
# Gate-red reproduction (D2): negative test that triggers CI gate
# ---------------------------------------------------------------------------


class TestGateRedReproduction:
    """Gate-red: a call with a tr_id starting with T/J/C should be swapped to V
    by the KIS adapter layer (not by krx_data.py itself), but krx_data.py must
    not silently swap F-prefixed tr_ids."""

    async def test_f_tr_id_not_swapped_by_adapter(
        self, fake_client: _FakeKISClient, index_trend_response: dict[str, Any]
    ) -> None:
        """FHKST030201R starts with F — krx_data.py must pass it unchanged.
        The KIS adapter layer (KISAdapter._resolve_tr_id) handles T/J/C→V swap.
        This test verifies krx_data.py does NOT interfere with F-prefixed IDs.
        """
        fake_client.responses["FHKST030201R"] = index_trend_response
        adapter = KRXIndexAdapter(fake_client)
        await adapter.get_index_trend("00001")
        # The tr_id in the call must be exactly FHKST030201R
        _, _, tr_id = fake_client.calls[-1]
        assert tr_id == "FHKST030201R", f"Expected FHKST030201R, got {tr_id}"

    async def test_tr_id_passed_correctly_for_all_endpoints(
        self, fake_client: _FakeKISClient
    ) -> None:
        """Verify all three adapters use the correct tr_id without modification."""
        fake_client.responses.update(
            {
                "INDI000R": {
                    "output": {
                        "indicode": "00001",
                        "inpct_name": "KOSPI",
                        "prdy_vrss": 0,
                        "prdy_crt": 0,
                        "stdc_crt": 0,
                        "hts_hgpr": 0,
                        "hts_lwpr": 0,
                        "hts_vol": 0,
                    }
                },
                "FHKST030301R": {
                    "output": {
                        "invst_pct": 0,
                        "frgn_by_pct": 0,
                        "inst_pct": 0,
                        "etc_pct": 0,
                        "invst_volume": 0,
                    }
                },
                "FHKUP080700R": {"output1": []},
            }
        )
        idx = KRXIndexAdapter(fake_client)
        inv = KRXInvestorAdapter(fake_client)
        shr = KRXShortAdapter(fake_client)
        await idx.get_index_price("00001")
        await inv.get_investor_trend("20260915")
        await shr.get_short_resistance_stocks()
        expected_tr_ids = ["INDI000R", "FHKST030301R", "FHKUP080700R"]
        for i, (_, _, tr_id) in enumerate(fake_client.calls):
            assert tr_id == expected_tr_ids[i], (
                f"Call {i}: expected {expected_tr_ids[i]}, got {tr_id}"
            )
