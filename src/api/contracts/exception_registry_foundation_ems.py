"""EM-14 (task-5277) -- `src/api/routers/foundation/ems.py` domain exceptions
-> ErrorCode.

`exception_registry_foundation.py` is already close to the P6.line_cap
300-line guard and follows the same split convention as
`exception_registry_foundation_ai_gateway.py` (see that module's docstring).

Both `compute_tca.py` exceptions are client-input errors (a non-positive
arrival price or a `revision < 1`), not server faults -- `EmptyFillsError`/
`InvalidFillError` (EM-13 `domain/tca/decomposition.py`) fold into the same
`VALIDATION_INVALID_FIELD` code for the same reason.

task-5598 (PLT-21) adds the ems.py TCA/algo-progress *read* paths to the
same bucket -- `TcaResultNotFoundError`/`AlgoRunNotFoundError`, both 404.
"""

from __future__ import annotations

from src.api.contracts.error_codes import ErrorCode
from src.foundation.ems.application.compute_tca import (
    InvalidArrivalPriceError,
    InvalidRevisionError,
)
from src.foundation.ems.application.get_algo_progress import AlgoRunNotFoundError
from src.foundation.ems.domain.tca.benchmarks import EmptyBarsError
from src.foundation.ems.domain.tca.decomposition import EmptyFillsError, InvalidFillError
from src.foundation.ems.ports.tca_result_repository import TcaResultNotFoundError

EXCEPTION_MAP_EMS: list[tuple[type[Exception], ErrorCode]] = [
    (InvalidArrivalPriceError, ErrorCode.VALIDATION_INVALID_FIELD),
    (InvalidRevisionError, ErrorCode.VALIDATION_INVALID_FIELD),
    (EmptyFillsError, ErrorCode.VALIDATION_INVALID_FIELD),
    (InvalidFillError, ErrorCode.VALIDATION_INVALID_FIELD),
    (EmptyBarsError, ErrorCode.VALIDATION_INVALID_FIELD),
    (TcaResultNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
    (AlgoRunNotFoundError, ErrorCode.RESOURCE_NOT_FOUND),
]
