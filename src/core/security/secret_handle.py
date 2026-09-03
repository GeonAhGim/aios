"""SecretHandle — 평문 생존기간 최소화.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32
(+ §3.6). `async with` 블록 안에서만 평문 bytes에 접근할 수 있고, 블록을
나가면 내부 bytearray를 즉시 0으로 덮어쓴다. Python `str`은 불변이라
zeroize가 불가능하므로 이 클래스는 처음부터 `bytes`/`bytearray`만 다룬다 —
정직한 한계는 §10-3 참고("필요한 시간만"은 참조 수명 기준이지 물리 메모리
기준이 아니다).

`ring`+`SealedRecord`를 생성 시점에 받아두고 `__aenter__`에서야 복호한다 —
핸들이 만들어지고 실제로 쓰이기까지 사이에 평문이 메모리에 떠 있는 시간을
없앤다. `resolver.open(ref)`(PLT-33 `credential_resolver.py`)가 이 핸들의
조립을 맡고, 이 리프는 핸들 자체의 계약만 구현한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType

from src.core.observability.metric_names import SECURITY_SECRET_DECRYPT_COUNT_TOTAL
from src.core.observability.metrics import MetricsPort
from src.core.observability.metrics import metrics as current_metrics
from src.core.security.envelope import SealedRecord, open_
from src.core.security.key_ring import KeyRing
from src.core.security.secret_ref import SecretRef


class SecretHandleClosedError(RuntimeError):
    """`__aenter__` 이전 또는 `__aexit__` 이후 평문 프로퍼티 접근(사용 오류)."""


class SecretHandle:
    """`async with handle as h: h.api_key` 형태로만 평문에 접근한다."""

    def __init__(
        self,
        ref: SecretRef,
        ring: KeyRing,
        *,
        api_key: SealedRecord,
        api_secret: SealedRecord | None = None,
        extra: Mapping[str, SealedRecord] | None = None,
        metrics_port: MetricsPort | None = None,
    ) -> None:
        self.ref = ref
        self._ring = ring
        self._sealed_api_key = api_key
        self._sealed_api_secret = api_secret
        self._sealed_extra = dict(extra) if extra else {}
        self._metrics = metrics_port if metrics_port is not None else current_metrics()

        self._api_key: bytearray | None = None
        self._api_secret: bytearray | None = None
        self._extra: dict[str, bytearray] = {}
        self._opened = False

    async def __aenter__(self) -> SecretHandle:
        self._api_key = bytearray(open_(self._sealed_api_key, self._ring))
        self._api_secret = (
            bytearray(open_(self._sealed_api_secret, self._ring))
            if self._sealed_api_secret is not None
            else None
        )
        self._extra = {
            name: bytearray(open_(rec, self._ring)) for name, rec in self._sealed_extra.items()
        }
        self._opened = True
        self._metrics.counter(
            SECURITY_SECRET_DECRYPT_COUNT_TOTAL,
            {"scope": self.ref.scope, "kind": self.ref.kind},
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        _zero(self._api_key)
        _zero(self._api_secret)
        for value in self._extra.values():
            _zero(value)
        self._api_key = None
        self._api_secret = None
        self._extra = {}
        self._opened = False

    @property
    def api_key(self) -> bytes:
        self._ensure_opened()
        assert self._api_key is not None  # 생성자 필수 인자 — opened면 항상 존재
        return bytes(self._api_key)

    @property
    def api_secret(self) -> bytes:
        self._ensure_opened()
        if self._api_secret is None:
            raise ValueError("이 SecretRef에는 api_secret이 없습니다.")
        return bytes(self._api_secret)

    @property
    def extra(self) -> Mapping[str, bytes]:
        self._ensure_opened()
        return {name: bytes(value) for name, value in self._extra.items()}

    def _ensure_opened(self) -> None:
        if not self._opened:
            raise SecretHandleClosedError(
                "async with 블록 밖에서 SecretHandle 평문에 접근했습니다."
            )


def _zero(buf: bytearray | None) -> None:
    if buf is None:
        return
    for i in range(len(buf)):
        buf[i] = 0
