"""RD-19 — L2 orderbook raw record store (local files) + retention policy enforcement.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D5 "The cost is storage and operations (GB per day even with only a few
symbols) — build in a retention policy from the start." Full-depth
records are stored as files, not in Postgres (per the cost profile D5
acknowledges, we don't dump bulk volume data into the relational DB — the
DC-8 `coverage_spans` table is metadata that only records whether a span
is online; it is not the store for these files).

Each record's timestamp is determined by the `as_of` passed by the
caller at write time (the OS file mtime is not trusted) — this is so the
adapter can support the virtual-clock determinism tests required by
`domain/l2_retention.plan_retention`. The manifest (`manifest.json`) is
the SSOT for record metadata; the actual files hold only the payload.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.foundation.market_data.domain.l2_retention import (
    DepthRecordMeta,
    RecordKind,
    RetentionAction,
    RetentionPolicy,
    plan_retention,
)

logger = logging.getLogger(__name__)

__all__ = ["L2DepthStore", "RetentionResult"]

DownsampleFn = Callable[[bytes], bytes]


class RetentionResult:
    def __init__(
        self, *, deleted: int, downsampled: int, total_bytes: int, cap_still_exceeded: bool
    ) -> None:
        self.deleted = deleted
        self.downsampled = downsampled
        self.total_bytes = total_bytes
        self.cap_still_exceeded = cap_still_exceeded


class L2DepthStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._manifest_path = self._root / "manifest.json"
        self._manifest: dict[str, dict[str, str | int]] = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict[str, str | int]]:
        if not self._manifest_path.exists():
            return {}
        return dict(json.loads(self._manifest_path.read_text(encoding="utf-8")))

    def _save_manifest(self) -> None:
        self._manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def write_full_depth(self, as_of: datetime, payload: bytes) -> str:
        """Store one full-depth record and return its record_id."""
        record_id = uuid4().hex
        path = self._root / f"{record_id}.full.json"
        path.write_bytes(payload)
        self._manifest[record_id] = {
            "kind": RecordKind.FULL.value,
            "as_of": as_of.isoformat(),
            "size_bytes": len(payload),
        }
        self._save_manifest()
        return record_id

    def records(self) -> list[DepthRecordMeta]:
        return [
            DepthRecordMeta(
                record_id=record_id,
                kind=RecordKind(meta["kind"]),
                as_of=datetime.fromisoformat(str(meta["as_of"])),
                size_bytes=int(meta["size_bytes"]),
            )
            for record_id, meta in self._manifest.items()
        ]

    def total_bytes(self) -> int:
        return sum(int(meta["size_bytes"]) for meta in self._manifest.values())

    def _path_for(self, record_id: str, kind: RecordKind) -> Path:
        suffix = "full" if kind is RecordKind.FULL else "agg"
        return self._root / f"{record_id}.{suffix}.json"

    def enforce_retention(
        self, policy: RetentionPolicy, now: datetime, downsample_fn: DownsampleFn
    ) -> RetentionResult:
        """Applies the plan decided by `domain/l2_retention.plan_retention` to
        the actual files. `KEEP_FULL`/`KEEP_AGGREGATE` are no-ops."""
        plan = plan_retention(self.records(), now, policy)
        deleted = downsampled = 0
        for record_id, action in plan.actions.items():
            meta = self._manifest[record_id]
            kind = RecordKind(meta["kind"])
            if action is RetentionAction.DELETE:
                self._path_for(record_id, kind).unlink(missing_ok=True)
                del self._manifest[record_id]
                deleted += 1
            elif action is RetentionAction.DOWNSAMPLE:
                full_path = self._path_for(record_id, RecordKind.FULL)
                aggregate_payload = downsample_fn(full_path.read_bytes())
                full_path.unlink(missing_ok=True)
                self._path_for(record_id, RecordKind.AGGREGATE).write_bytes(aggregate_payload)
                self._manifest[record_id] = {
                    "kind": RecordKind.AGGREGATE.value,
                    "as_of": meta["as_of"],
                    "size_bytes": len(aggregate_payload),
                }
                downsampled += 1
        self._save_manifest()
        if plan.cap_still_exceeded:
            logger.warning(
                "L2 보존 정책 적용 후에도 디스크 상한 초과(projected_bytes=%d, cap=%d) — "
                "단기 전체 깊이 보존 구간(KEEP_FULL)은 이 정책이 스스로 지우지 않는다",
                plan.projected_bytes, policy.disk_cap_bytes,
            )
        return RetentionResult(
            deleted=deleted,
            downsampled=downsampled,
            total_bytes=self.total_bytes(),
            cap_still_exceeded=plan.cap_still_exceeded,
        )
