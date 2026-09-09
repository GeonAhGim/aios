"""EM-17: transport-free FIX boundary; real adapters belong to MVP-2.

Only orders already approved through OMS submit_order may reach this port.
send_order rejects logged-out sessions and reused cl_ord_id values with
RuntimeError, without consuming a sequence number. It returns the accepted
order's session-local seq_num, starting at 1 and increasing by one.
reset_sequence makes the next accepted order number 1; it preserves duplicate
protection. Logout/logon preserves both sequence and duplicate protection.
These are local port semantics, not a claim about venue FIX wire behavior.
Known transport failure before acceptance must reject without advancing local
sequence or duplicate state. Ambiguous delivery requires adapter reconciliation
in MVP-2; production FIX disconnect/recovery behavior remains 미검증.
Execution reports reuse OMS ProviderOrderEvent because EM-1 defines none.
The registered synchronous callback receives normalized execution reports.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from src.foundation.ems.contracts.v1 import ChildOrder
from src.services.oms.contracts.v1_events import ProviderOrderEvent


@runtime_checkable
class FixSessionPort(Protocol):
    async def logon(self) -> None: ...

    async def logout(self) -> None: ...

    async def send_order(self, order: ChildOrder, *, cl_ord_id: str) -> int: ...

    async def reset_sequence(self) -> None: ...

    def register_execution_report_callback(
        self, callback: Callable[[ProviderOrderEvent], None]
    ) -> None: ...
