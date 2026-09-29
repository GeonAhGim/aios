"""L4_risk_and_safety_v1.0.md#9 R-01 — hashing.py canonical JSON 결정론 테스트."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.core.risk.hashing import canonical_json, sha256_hex


def test_key_order_does_not_change_hash():
    a = {"b": 1, "a": 2}
    b = {"a": 2, "b": 1}
    assert sha256_hex(canonical_json(a)) == sha256_hex(canonical_json(b))


def test_decimal_trailing_zero_normalized_to_same_hash():
    a = {"x": Decimal("1.0")}
    b = {"x": Decimal("1.00")}
    assert canonical_json(a) == canonical_json(b)


def test_decimal_zero_variants_normalized_to_same_hash():
    assert canonical_json({"x": Decimal("0")}) == canonical_json({"x": Decimal("0.00")})


def test_decimal_never_serialized_in_exponential_notation():
    payload = canonical_json({"x": Decimal("100")})
    assert b"E" not in payload and b"e" not in payload
    assert b'"100"' in payload


def test_different_values_hash_differently():
    a = canonical_json({"x": Decimal("1.0")})
    b = canonical_json({"x": Decimal("1.01")})
    assert sha256_hex(a) != sha256_hex(b)


def test_aware_datetime_normalized_to_utc_iso():
    a = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)
    payload = canonical_json({"t": a})
    assert b"2026-09-03T00:00:00+00:00" in payload


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        canonical_json({"t": datetime(2026, 9, 3, 0, 0)})


def test_non_serializable_type_raises_type_error():
    class _Unsupported:
        pass

    with pytest.raises(TypeError):
        canonical_json({"x": _Unsupported()})


def test_naive_datetime_rejected_when_nested_in_list():
    with pytest.raises(ValueError):
        canonical_json({"ts": [datetime(2026, 9, 3, 0, 0)]})


def test_uuid_normalized_to_str():
    u = UUID("12345678-1234-5678-1234-567812345678")
    payload = canonical_json({"id": u})
    assert str(u).encode() in payload


def test_nested_list_and_tuple_normalized_identically():
    a = canonical_json({"xs": [Decimal("1.0"), Decimal("2.00")]})
    b = canonical_json({"xs": (Decimal("1.00"), Decimal("2.0"))})
    assert a == b


def test_sha256_hex_is_64_char_hex():
    digest = sha256_hex(canonical_json({"a": 1}))
    assert len(digest) == 64
    int(digest, 16)  # ValueError면 hex가 아님


# ---------------------------------------------------------------------------
# D3 — 리뷰 task-8044(REJECT) 후속: INVARIANTS.md 교차검증 adversarial 테스트 +
# replay_verify 경로(risk_replay.py/replay_decision.py)의 결정론적 재현 일치 검증.
#
# `RiskInputs.inputs_hash()`/`compute_rule_hash()`(R-15)는 이 모듈의
# `canonical_json`/`sha256_hex`를 그대로 재사용해 I-09("주문 최종 ALLOW/DENY는
# 두 개의 독립 권위를 모두 통과해야 하며, 각각 조회 증거를 남긴다")의 조회
# 증거 무결성을 뒷받친다 — `src/foundation/risk_gate/application/replay_decision.py`
# 의 nightly replay(`src/tools/risk_replay.py`)는 WORM에 저장된 `inputs_snapshot`을
# 재구성해 다시 해시하고 저장된 값과 비교하는데, 그 비교가 성립하려면 이
# 모듈의 정규화가 (a) 논리적으로 다른 입력은 반드시 다른 해시를 내고, (b) 같은
# 논리적 입력은 구성 경로가 달라도 항상 같은 해시를 내야 한다. 두 성질 중 하나만
# 무너져도 replay는 조용히 통과하거나(위조 탐지 실패) 조용히 실패한다(정상
# 재현을 오탐 거부).
# ---------------------------------------------------------------------------


def test_adversarial_reordered_evidence_sequence_changes_hash():
    """I-09 조회 증거 무결성 — `RuleResult`/`rule_bundle.limits`처럼 순서가
    의미를 갖는 시퀀스(list/tuple)를 재배열해 같은 항목 집합으로 원래 해시를
    재현하려는 시도(예: 오래된 rule_results 순서를 다른 평가 순서에 재사용해
    replay 불일치를 감추려는 변조)는 canonical_json이 리스트 순서를 정렬하지
    않으므로(딕셔너리 키만 정렬) 반드시 다른 해시를 내야 한다 — 그렇지 않으면
    두 개의 독립 권위(risk/compliance)가 남긴 조회 증거 순서를 조용히 바꿔도
    replay_verify가 위조를 놓친다."""
    evidence = [
        {"rule_id": "exposure_limits", "outcome": "ALLOW"},
        {"rule_id": "max_drawdown", "outcome": "DENY"},
    ]
    tampered = list(reversed(evidence))

    original_hash = sha256_hex(canonical_json({"rule_results": evidence}))
    tampered_hash = sha256_hex(canonical_json({"rule_results": tampered}))

    assert original_hash != tampered_hash


def test_adversarial_float_amount_diverges_from_equal_decimal_hash():
    """monetary amount는 항상 `Decimal`이어야 한다(CLAUDE.md §3) — 버그로
    `float`가 섞여 들어오면(예: 레거시 dict 조립 경로가 `Decimal`화를 빠뜸)
    canonical_json은 float에 Decimal 정규화를 적용하지 않고 표준 json 인코딩을
    그대로 통과시킨다. 이 테스트는 그 침투가 조용히 넘어가지 않고 해시 자체가
    갈라진다는 것을 고정한다 — replay_verify가 저장 시점 해시(Decimal 경로)와
    재현 시점 해시(float가 섞인 경로)를 비교하면 반드시 불일치로 드러나
    I-09/R-01(RSK-001/009) 결정론 위반을 잡아낸다."""
    decimal_hash = sha256_hex(canonical_json({"notional": Decimal("100.10")}))
    float_hash = sha256_hex(canonical_json({"notional": 100.10}))

    assert decimal_hash != float_hash


def test_replay_verify_recompute_matches_stored_hash_across_construction_paths():
    """`risk_replay.py`(R-54 nightly replay)가 실제로 의존하는 성질 —
    WORM에 저장된 `inputs_snapshot`을 다시 읽어 재구성한 payload가 저장 당시와
    "구성 경로"만 다를 뿐 논리적으로 같다면(키 삽입 순서·Decimal 표현 다름),
    재해시가 저장된 해시와 반드시 일치해야 replay가 오탐 불일치(MISMATCH)를
    내지 않는다. `replay_decision.replay()`의 비교 로직(`_COMPARED_FIELDS`)과
    동일한 "저장 시점 해시 vs 재현 시점 해시" 대조를 이 모듈 수준에서 재현한다."""
    stored_payload = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "intent": {"symbol": "BTC/USDT", "notional": Decimal("5000.00")},
        "as_of": datetime(2026, 9, 3, tzinfo=timezone.utc),
    }
    stored_hash = sha256_hex(canonical_json(stored_payload))

    # replay 시점: 같은 논리값을 다른 삽입 순서·다른 Decimal 표현으로 재구성.
    replayed_payload = {
        "as_of": datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc),
        "intent": {"notional": Decimal("5000"), "symbol": "BTC/USDT"},
        "tenant_id": "11111111-1111-1111-1111-111111111111",
    }
    replayed_hash = sha256_hex(canonical_json(replayed_payload))

    assert replayed_hash == stored_hash


def test_replay_verify_detects_tampered_snapshot_as_mismatch():
    """위와 같은 replay 경로에서 `inputs_snapshot`의 실제 값 하나가 저장 시점과
    달라지면(예: 재현 중 다른 값으로 손상/변조) `risk_replay.py`가 exit 2로
    보고하는 `INTEGRITY_RISK_REPLAY_MISMATCH`에 대응하는 해시 불일치가 반드시
    발생해야 한다 — 조용한 재현 실패는 감사 로그에서 손상된 결정이 사라지는
    것과 같다(task-2174, risk_replay.py 모듈 docstring)."""
    stored_payload = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "intent": {"symbol": "BTC/USDT", "notional": Decimal("5000.00")},
        "as_of": datetime(2026, 9, 3, tzinfo=timezone.utc),
    }
    stored_hash = sha256_hex(canonical_json(stored_payload))

    tampered_payload = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "intent": {"symbol": "BTC/USDT", "notional": Decimal("5000.01")},
        "as_of": datetime(2026, 9, 3, tzinfo=timezone.utc),
    }
    tampered_hash = sha256_hex(canonical_json(tampered_payload))

    assert tampered_hash != stored_hash
