"""AI-20 -- TrainerPort: training-time step port for
`application/train_job.py`.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-20
(`ports/{feature_store,model_registry,trainer}.py`,
`adapters/{...,local_trainer}.py`), §9 AI-20 DoD ("resumable").

Deliberately separate from the inference-time `ports/predictor.py`
(AI-21): `train_job` only needs to advance a model `num_boost_round`
rounds from an optional prior checkpoint and get a new checkpoint back --
it never scores a feature vector. `adapters/local_trainer.py`'s
`LocalTrainer` satisfies both this Protocol and `ModelPredictorPort`
(`ports/predictor.py`'s docstring already names this), but the two ports
stay split so `application/train_job.py` never imports anything
inference-only, and `application/serve_signal.py` (AI-21) never imports
anything training-only.

`checkpoint` is an opaque string -- `application/train_job.py` and
`ports/train_job_repository.py` only ever pass it through, never parse
it. `LocalTrainer` happens to store LightGBM's own
`Booster.model_to_string()` there, but no caller outside
`adapters/local_trainer.py` may depend on that representation (a future
second `TrainerPort` implementation, e.g. a torch-backed one, is free to
put anything serializable in the same slot).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import NamedTuple, Protocol

__all__ = ["TrainingSample", "TrainerPort"]


class TrainingSample(NamedTuple):
    """One labeled training row. `features` keys are `FeatureSpec.feature_id`
    values (AI-18's `contracts/v1.py`); `label` is the regression/ranking
    target `train_step` fits against."""

    features: Mapping[str, float]
    label: float


class TrainerPort(Protocol):
    def train_step(
        self,
        checkpoint: str | None,
        samples: Sequence[TrainingSample],
        *,
        num_boost_round: int,
        params: Mapping[str, object],
    ) -> str:
        """Train `num_boost_round` additional rounds starting from
        `checkpoint` (`None` means cold start -- no prior rounds). Returns
        the new checkpoint, which already includes every round the input
        `checkpoint` held plus the `num_boost_round` just trained.

        Must be resumable: `train_step(train_step(None, s, num_boost_round=3,
        params=p), s, num_boost_round=2, params=p)` produces an equivalent
        model to one `train_step(None, s, num_boost_round=5, params=p)` call
        (AI-20 DoD "resumable") -- same data, same params, same total round
        count, split across two calls instead of one.
        """
        ...

    def evaluate(self, checkpoint: str, samples: Sequence[TrainingSample]) -> dict[str, float]:
        """Score `checkpoint` against `samples` and return metrics (e.g.
        `{"rmse": ...}`) -- pure, no I/O, no artifact lookup (unlike
        `ModelPredictorPort.predict`, this never touches `save_artifact`'s
        on-disk store; `checkpoint` is passed by value straight from the
        last `train_step` call). `application/train_job.py` calls this once
        a job reaches its `target_rounds`, before handing the checkpoint to
        `application/register_model.py`."""
        ...
