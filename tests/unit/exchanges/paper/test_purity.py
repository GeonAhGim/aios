"""I-10 배선 증명 — paper 모델 4종은 전역 난수·전역 시계·네트워크에 닿지 않는다.

정적(소스 검사) + 동적(전역을 폭탄으로 바꿔도 동작) 두 겹으로 증명한다.
"""
from __future__ import annotations

import asyncio
import random
import re
import time
from decimal import Decimal
from pathlib import Path

import pytest

from src.exchanges.paper.fill_model import FillModel
from src.exchanges.paper.latency_model import LatencyModel
from tests.unit.exchanges.paper.helpers import FixedRng, make_book, make_order

_PAPER_DIR = Path(__file__).resolve().parents[4] / "src" / "exchanges" / "paper"
_FORBIDDEN = [
    r"^\s*import\s+(random|time|httpx|aiohttp|requests|asyncio)\b",
    r"^\s*from\s+(random|time|httpx|aiohttp|requests|asyncio)\b",
    r"datetime\.now\(",
    r"asyncio\.sleep\(",
    r"random\.random\(",
]


@pytest.mark.parametrize("path", sorted(_PAPER_DIR.glob("*.py")), ids=lambda p: p.name)
def test_no_io_or_global_randomness_imports(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    for pattern in _FORBIDDEN:
        assert re.search(pattern, source, re.MULTILINE) is None, f"{path.name}: {pattern}"


def test_fill_model_survives_poisoned_global_random(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> float:
        raise AssertionError("전역 random 사용")

    monkeypatch.setattr(random, "random", _boom)
    model = FillModel(
        spread_bps=Decimal("1"),
        impact_bps_per_pct_adv=Decimal("1"),
        partial_fill_prob=1.0,
        partial_min_pct=Decimal("50"),
    )
    fills = model.simulate(make_order(), make_book(), Decimal("1000"), FixedRng([0.0, 0.0]))
    assert len(fills) == 1


async def test_latency_model_survives_poisoned_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom_sleep(_: float) -> None:
        raise AssertionError("전역 asyncio.sleep 사용")

    def _boom_time() -> float:
        raise AssertionError("전역 time 사용")

    # time.monotonic은 이벤트 루프 자체가 쓰므로 독을 넣지 않는다(정적 검사가
    # `import time` 자체를 금지하므로 time.time만으로 충분).
    monkeypatch.setattr(asyncio, "sleep", _boom_sleep)
    monkeypatch.setattr(time, "time", _boom_time)
    monkeypatch.setattr(random, "random", lambda: (_ for _ in ()).throw(AssertionError("random")))

    calls: list[float] = []

    async def sleeper(seconds: float) -> None:
        calls.append(seconds)

    out = await LatencyModel(10.0, 20.0, 0.0).apply(FixedRng([0.5]), sleeper=sleeper)
    assert out.kind == "ACK" and calls == [0.01]


def test_models_are_deterministic_for_same_rng_sequence() -> None:
    model = FillModel(Decimal("3"), Decimal("1"), 0.5, Decimal("20"))
    a = model.simulate(make_order(quantity="5"), make_book(), Decimal("100"), FixedRng([0.1, 0.7]))
    b = model.simulate(make_order(quantity="5"), make_book(), Decimal("100"), FixedRng([0.1, 0.7]))
    assert a == b
