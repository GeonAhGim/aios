"""task-4614 TEST-cov — src/api/schemas/strategy_builder.py coverage.

Pure-pydantic module (no I/O), so the negative/failure-injection tests
target construction-time validation and the two mapper functions
(`to_candle_response`, `to_strategy_response`, `to_strategy_detail_response`)
rather than DB/network failure modes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.strategy_builder import (
    CandleResponse,
    IndicatorComputeResponse,
    IndicatorListResponse,
    PreviewRequest,
    PreviewResponse,
    PromptGenerateRequest,
    StrategyCreateRequest,
    StrategyDetailResponse,
    StrategyResponse,
    WizardGenerateRequest,
    to_candle_response,
    to_strategy_detail_response,
    to_strategy_response,
)
from src.data.models.market_data import Candle
from src.services.preview_service import PreviewCondition
from src.services.strategy_builder_service import SavedStrategy, StrategyDetail


def _candle(**overrides: object) -> Candle:
    fields: dict[str, object] = dict(
        symbol="BTCUSDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("1"),
        close=Decimal("1.5"),
        volume=Decimal("10"),
        open_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        close_time=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )
    fields.update(overrides)
    return Candle(**fields)


def _condition(**overrides: object) -> PreviewCondition:
    fields: dict[str, object] = dict(indicator="rsi", operator=">", threshold=30.0)
    fields.update(overrides)
    return PreviewCondition(**fields)


class TestToCandleResponse:
    def test_maps_all_fields(self) -> None:
        candle = _candle()
        result = to_candle_response(candle)
        assert isinstance(result, CandleResponse)
        assert result.open_time == candle.open_time
        assert result.open == candle.open
        assert result.high == candle.high
        assert result.low == candle.low
        assert result.close == candle.close
        assert result.volume == candle.volume

    def test_drops_symbol_and_exchange_not_present_on_response(self) -> None:
        candle = _candle(symbol="ETHUSDT", exchange="kis")
        result = to_candle_response(candle)
        assert not hasattr(result, "symbol")
        assert not hasattr(result, "exchange")


class TestToStrategyResponse:
    def test_maps_lifecycle_status_to_status(self) -> None:
        saved = SavedStrategy(
            strategy_id="s1",
            version="v1",
            lifecycle_status="draft",
            risk_warning=None,
        )
        result = to_strategy_response(saved, fsm_definition={"states": []})
        assert isinstance(result, StrategyResponse)
        assert result.strategy_id == "s1"
        assert result.version == "v1"
        assert result.status == "draft"
        assert result.fsm_definition == {"states": []}


class TestToStrategyDetailResponse:
    def test_maps_all_fields(self) -> None:
        detail = StrategyDetail(
            strategy_id="s1",
            version="v1",
            owner_user_id=uuid4(),
            target_asset="BTC",
            market="spot",
            exchange="bitget",
            lifecycle_status="active",
            fsm_definition={"a": 1},
        )
        result = to_strategy_detail_response(detail)
        assert isinstance(result, StrategyDetailResponse)
        assert result.strategy_id == detail.strategy_id
        assert result.version == detail.version
        assert result.target_asset == detail.target_asset
        assert result.market == detail.market
        assert result.exchange == detail.exchange
        assert result.status == detail.lifecycle_status
        assert result.fsm_definition == detail.fsm_definition


class TestStrategyCreateRequest:
    def test_valid_request_defaults_combine_to_and(self) -> None:
        req = StrategyCreateRequest(
            strategy_id="s1",
            version="v1",
            target_asset="BTC",
            market="spot",
            exchange="bitget",
            entry_conditions=[_condition()],
            exit_conditions=[],
            stop_loss_conditions=[],
        )
        assert req.entry_combine == "AND"
        assert req.exit_combine == "AND"
        assert req.stop_loss_combine == "AND"

    def test_missing_required_field_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            StrategyCreateRequest(
                version="v1",
                target_asset="BTC",
                market="spot",
                exchange="bitget",
                entry_conditions=[],
                exit_conditions=[],
                stop_loss_conditions=[],
            )

    def test_invalid_condition_type_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            StrategyCreateRequest(
                strategy_id="s1",
                version="v1",
                target_asset="BTC",
                market="spot",
                exchange="bitget",
                entry_conditions=["not-a-condition"],
                exit_conditions=[],
                stop_loss_conditions=[],
            )


class TestPreviewRequest:
    def test_defaults(self) -> None:
        req = PreviewRequest(
            exchange="bitget",
            symbol="BTCUSDT",
            conditions=[_condition()],
        )
        assert req.timeframe == "1h"
        assert req.limit == 200
        assert req.combine == "AND"

    def test_invalid_combine_literal_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PreviewRequest(
                exchange="bitget",
                symbol="BTCUSDT",
                conditions=[_condition()],
                combine="XOR",
            )

    def test_limit_wrong_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PreviewRequest(
                exchange="bitget",
                symbol="BTCUSDT",
                conditions=[_condition()],
                limit="not-an-int",
            )


class TestPreviewResponse:
    def test_message_optional(self) -> None:
        resp = PreviewResponse(signal_indices=[1, 2], signal_times=["t1", "t2"], disclaimer="d")
        assert resp.message is None


class TestIndicatorSchemas:
    def test_indicator_list_response(self) -> None:
        resp = IndicatorListResponse(indicators=["rsi", "sma"])
        assert resp.indicators == ["rsi", "sma"]

    def test_indicator_compute_response_optional_fields(self) -> None:
        resp = IndicatorComputeResponse(indicator="rsi", values=[1.0, None], params={"period": 14})
        assert resp.series is None
        assert resp.message is None

    def test_indicator_compute_response_rejects_bad_values_type(self) -> None:
        with pytest.raises(ValidationError):
            IndicatorComputeResponse(indicator="rsi", values="not-a-list", params={"period": 14})


class TestWizardAndPromptRequests:
    def test_wizard_generate_request_requires_both_fields(self) -> None:
        with pytest.raises(ValidationError):
            WizardGenerateRequest(goal="grow capital")

    def test_prompt_generate_request_valid(self) -> None:
        req = PromptGenerateRequest(prompt="build me a momentum strategy")
        assert req.prompt == "build me a momentum strategy"

    def test_prompt_generate_request_missing_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PromptGenerateRequest()


class TestFailureInjection:
    def test_to_candle_response_propagates_attribute_error_on_broken_candle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate an upstream dependency (Candle) losing a field it should
        always have — the mapper must not silently swallow the failure."""
        candle = _candle()
        monkeypatch.delattr(candle, "volume", raising=True)
        with pytest.raises(AttributeError):
            to_candle_response(candle)
