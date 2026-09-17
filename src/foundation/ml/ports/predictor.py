"""AI-21 -- ModelPredictorPort: inference-time scoring port for
`application/serve_signal.py` and `src/core/indicators/catalog/ml.py`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-21
(`application/serve_signal.py`, `indicators/catalog/ml.py`).

Deliberately separate from the training-time `ports/trainer.py` (AI-20,
not yet implemented, §2.5 "ports/{feature_store,model_registry,trainer}.py"):
`serve_signal` only needs to turn a trained `ModelCard` plus a feature
vector into a score, not to know how the model was trained or where its
weights live -- same "define the port before the concrete adapter exists"
sequencing `ports/feature_store.py`/`ports/model_registry.py` (AI-19) used
ahead of AI-20's adapters. AI-20's `local_trainer` adapter is expected to
also satisfy this Protocol once it lands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from src.foundation.ml.contracts.v1 import ModelCard

__all__ = ["ModelPredictorPort"]


class ModelPredictorPort(Protocol):
    def predict(self, card: ModelCard, features: Mapping[str, float]) -> float:
        """Score one feature vector against `card`. Must be a pure function
        of `(card, features)` -- deterministic, no hidden state, no I/O --
        so that scoring one bar at a time (streaming/replay) and scoring a
        whole span in one bulk pass (backtest) always agree (AI-21
        "incremental=batch equivalence" DoD, proved by
        `serve_signal.check_signal_equivalence` and `catalog/ml.py::
        check_ml_signal_equivalence`)."""
        ...
