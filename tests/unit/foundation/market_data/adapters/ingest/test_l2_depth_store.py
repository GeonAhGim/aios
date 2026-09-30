"""RD-19 — `adapters/ingest/l2_depth_store.py` 단위테스트(로컬 파일, 실 DB 불필요).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D5. DoD: "보존 정책이 디스크 증가를 상한 안에 유지" — 여러 날치 전체
깊이 레코드가 쌓여도 `enforce_retention` 후 실제 디스크 사용량(파일
크기 합)이 상한을 넘지 않음을 실제 파일로 증명한다(가상 시계로 age
결정, wall clock 의존 없음).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.foundation.market_data.adapters.ingest.l2_depth_store import L2DepthStore
from src.foundation.market_data.domain.l2_retention import RecordKind, RetentionPolicy

_NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
_POLICY = RetentionPolicy(
    full_depth_ttl=timedelta(days=2),
    aggregate_ttl=timedelta(days=14),
    top_n=5,
    disk_cap_bytes=5_000,
    aggregate_size_bytes_estimate=200,
)


def _downsample(full_payload: bytes) -> bytes:
    data = json.loads(full_payload)
    return json.dumps({"top": data["bids"][:5]}).encode("utf-8")


def _full_payload(n_levels: int) -> bytes:
    payload = {"bids": [[str(i), "1"] for i in range(n_levels)], "asks": []}
    return json.dumps(payload).encode("utf-8")


def test_write_then_records_round_trips(tmp_path: Path):
    store = L2DepthStore(tmp_path)
    record_id = store.write_full_depth(_NOW, _full_payload(50))
    records = store.records()
    assert len(records) == 1
    assert records[0].record_id == record_id
    assert records[0].kind is RecordKind.FULL
    assert records[0].size_bytes == len(_full_payload(50))


def test_enforce_retention_downsamples_aged_records_and_deletes_expired(tmp_path: Path):
    store = L2DepthStore(tmp_path)
    recent_id = store.write_full_depth(_NOW - timedelta(hours=1), _full_payload(50))
    aged_id = store.write_full_depth(_NOW - timedelta(days=5), _full_payload(50))
    expired_id = store.write_full_depth(_NOW - timedelta(days=20), _full_payload(50))

    result = store.enforce_retention(_POLICY, _NOW, _downsample)

    assert result.downsampled == 1
    assert result.deleted == 1
    remaining_ids = {r.record_id for r in store.records()}
    assert recent_id in remaining_ids
    assert aged_id in remaining_ids
    assert expired_id not in remaining_ids
    aged_record = next(r for r in store.records() if r.record_id == aged_id)
    assert aged_record.kind is RecordKind.AGGREGATE


def test_disk_usage_stays_under_cap_as_data_grows_over_many_days(tmp_path: Path):
    """DoD: 보존 정책이 디스크 증가를 상한 안에 유지한다 — 30일치 매일
    수집 결과를 순차로 적재하고 매번 정책을 적용해도 실제 온디스크 바이트
    합이 한 번도 상한을 넘지 않는다."""
    store = L2DepthStore(tmp_path)
    for day in range(30):
        as_of = _NOW - timedelta(days=29 - day)
        store.write_full_depth(as_of, _full_payload(50))  # 하루 1건
        result = store.enforce_retention(_POLICY, as_of, _downsample)
        assert result.total_bytes <= _POLICY.disk_cap_bytes
        assert result.cap_still_exceeded is False

    data_files = [p for p in tmp_path.glob("*.json") if p.name != "manifest.json"]
    actual_disk_bytes = sum(p.stat().st_size for p in data_files)
    assert actual_disk_bytes <= _POLICY.disk_cap_bytes


def test_full_depth_records_within_ttl_survive_retention(tmp_path: Path):
    """negative — 단기 보존 약속을 정책이 스스로 깨지 않는다: 상한을
    넘겨도 full_depth_ttl 이내 레코드는 절대 삭제/축소되지 않는다."""
    tight_policy = RetentionPolicy(
        full_depth_ttl=timedelta(days=2),
        aggregate_ttl=timedelta(days=14),
        top_n=5,
        disk_cap_bytes=1,  # 의도적으로 극단적으로 작은 상한
        aggregate_size_bytes_estimate=1,
    )
    store = L2DepthStore(tmp_path)
    recent_id = store.write_full_depth(_NOW, _full_payload(50))

    result = store.enforce_retention(tight_policy, _NOW, _downsample)

    remaining = {r.record_id: r for r in store.records()}
    assert recent_id in remaining
    assert remaining[recent_id].kind is RecordKind.FULL
    assert result.cap_still_exceeded is True  # 조용히 숨기지 않고 표면화


def test_corrupted_manifest_json_raises_instead_of_silently_resetting(tmp_path: Path):
    """negative — manifest.json이 손상되면(디스크 오류/동시쓰기 등) 빈
    manifest로 조용히 대체해 기존 레코드 메타데이터를 잃지 않는다. 손상은
    fail-closed로 드러나야 한다."""
    (tmp_path / "manifest.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        L2DepthStore(tmp_path)


def test_records_rejects_manifest_entry_with_unknown_kind(tmp_path: Path):
    """negative — manifest 항목의 kind가 RecordKind 열거값이 아니면
    (수동 편집/버전 불일치) records()는 잘못된 값을 넘기지 않고 명시적으로
    거부한다."""
    store = L2DepthStore(tmp_path)
    record_id = store.write_full_depth(_NOW, _full_payload(5))
    store._manifest[record_id]["kind"] = "bogus_kind"
    store._save_manifest()

    with pytest.raises(ValueError):
        store.records()


def test_enforce_retention_missing_full_file_raises_instead_of_fabricating_aggregate(
    tmp_path: Path,
):
    """negative — 다운샘플 대상 레코드의 실제 파일이 디스크에서 사라진
    상태(외부 삭제/손상)라면 enforce_retention은 존재하지 않는 페이로드로
    빈 aggregate를 조작해내지 않고 그대로 실패한다."""
    store = L2DepthStore(tmp_path)
    aged_id = store.write_full_depth(_NOW - timedelta(days=5), _full_payload(50))
    (tmp_path / f"{aged_id}.full.json").unlink()

    with pytest.raises(FileNotFoundError):
        store.enforce_retention(_POLICY, _NOW, _downsample)


def test_enforce_retention_propagates_downsample_failure_without_partial_writes(
    tmp_path: Path,
):
    """실패주입 — downsample_fn이 예외를 던지면(예: 외부 라이브러리 오류)
    enforce_retention은 그 예외를 그대로 전파하고, 대상 레코드는 원본
    FULL 상태로 남아 반쪽짜리 AGGREGATE로 바뀌지 않는다."""

    def _boom(_payload: bytes) -> bytes:
        raise RuntimeError("downsample backend unavailable")

    store = L2DepthStore(tmp_path)
    aged_id = store.write_full_depth(_NOW - timedelta(days=5), _full_payload(50))

    with pytest.raises(RuntimeError, match="downsample backend unavailable"):
        store.enforce_retention(_POLICY, _NOW, _boom)

    remaining = {r.record_id: r for r in store.records()}
    assert remaining[aged_id].kind is RecordKind.FULL
    assert (tmp_path / f"{aged_id}.full.json").exists()
