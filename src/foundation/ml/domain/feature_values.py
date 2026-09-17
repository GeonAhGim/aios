"""AI-19 -- feature value shape rules (pure, no I/O).

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-19
(`ml ports + parquet_feature_store` ...), §9 AI-19 DoD ("lineage storage").

`adapters/parquet_feature_store.py::ParquetFeatureStore.write_batch` calls
this before writing a row into a partition -- `FeatureSpec.dtype` (AI-18's
`contracts/v1.py`) decides which string shapes are acceptable. Values are
stored as strings regardless of dtype (same discipline as DC-23's
`tick_parquet.py`), so a bad shape has to be rejected here, before the
value is durably published, rather than surfacing later as a silent NaN or
a caller-side cast crash at read time.
"""

from __future__ import annotations

import math

from src.foundation.ml.contracts.v1 import FeatureSpec

__all__ = ["InvalidFeatureValueError", "validate_feature_value"]


class InvalidFeatureValueError(ValueError):
    """`value`'s string shape does not match `feature_id`'s declared
    `dtype`."""

    def __init__(self, feature_id: str, dtype: str, value: str) -> None:
        self.feature_id = feature_id
        self.dtype = dtype
        self.value = value
        super().__init__(
            f"feature {feature_id!r} (dtype={dtype!r}): {value!r} is not a valid value"
        )


def validate_feature_value(spec: FeatureSpec, value: str) -> None:
    """Raises `InvalidFeatureValueError` unless `value` parses cleanly as
    `spec.dtype`. `float` additionally rejects `nan`/`inf` -- a stored
    non-finite value would poison any mean/std a caller later computes over
    the partition without ever raising there."""
    if spec.dtype == "float":
        try:
            parsed = float(value)
        except ValueError:
            raise InvalidFeatureValueError(spec.feature_id, spec.dtype, value) from None
        if not math.isfinite(parsed):
            raise InvalidFeatureValueError(spec.feature_id, spec.dtype, value)
    elif spec.dtype == "int":
        try:
            int(value)
        except ValueError:
            raise InvalidFeatureValueError(spec.feature_id, spec.dtype, value) from None
    elif spec.dtype == "bool":
        if value not in ("true", "false"):
            raise InvalidFeatureValueError(spec.feature_id, spec.dtype, value)
    elif spec.dtype == "category":
        if not value.strip():
            raise InvalidFeatureValueError(spec.feature_id, spec.dtype, value)
