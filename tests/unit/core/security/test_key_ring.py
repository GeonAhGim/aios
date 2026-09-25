"""KeyRing 단위 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §6 I7). 핵심: 레거시 단일 키 흡수, 버전 키 파싱, PAPER 런타임 LIVE 키
기동 거부(fail-closed).

DEEPEN(task-3138): task-458 DEPTH 감사가 원 리프(commit 401c16dd)에 실패
주입·수치 성능 단언·게이트 적색 재현이 없다고 지적함 — 새 기능 추가 없이
이 리프의 증빙만 보강한다. 실패 주입은 환경변수 백엔드(예: 시크릿 매니저
프록시)가 특정 키 조회에서 예외를 던졌을 때 `from_env`가 이를 "키 없음"
(KeyRingConfigError)으로 오판하지 않고 원 예외를 그대로 전파하는지
확인한다(fail-open 방지). 성능 단언은 ADR-2026-09-09-C Decision 1 예산표에
`KeyRing.from_env` 전용 항목이 없어(가장 가까운 항목은 "사전거래 게이트 p99
5ms") 이 리프가 그 값을 자체 예산으로 차용한다 — 순수 파이썬 문자열 파싱 +
hex 디코딩(I/O 없음)이라 로컬 실측 p95는 이보다 수백 배 낮다(2026-09-16
실측 ~0.003ms).
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping

import pytest

from src.core.exceptions import FrozenZoneLiveModeBlockedError
from src.core.security import key_ring as key_ring_module
from src.core.security.key_ring import KeyRing, KeyRingConfigError, UnknownKeyIdError

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32
KEY_LEGACY = "33" * 32


def test_from_env_parses_versioned_keys_and_active_kid() -> None:
    ring = KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v2:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v2",
        },
    )
    assert ring.active_kid == "v2"
    assert set(ring.kids()) == {"v1", "v2"}
    assert ring.key("v1") == bytes.fromhex(KEY_V1)
    assert ring.key("v2") == bytes.fromhex(KEY_V2)


def test_from_env_absorbs_legacy_single_key_as_legacy_kid() -> None:
    ring = KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEY": KEY_LEGACY,
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
        },
    )
    assert "legacy" in ring.kids()
    assert ring.key("legacy") == bytes.fromhex(KEY_LEGACY)
    assert ring.active_kid == "v1"


def test_from_env_legacy_only_environment_defaults_active_kid_to_legacy() -> None:
    """전환기: 아직 CREDENTIAL_ENCRYPTION_KEYS_PAPER를 안 쓰는 기존 배포."""
    ring = KeyRing.from_env("PAPER", env={"CREDENTIAL_ENCRYPTION_KEY": KEY_LEGACY})
    assert ring.active_kid == "legacy"
    assert ring.key("legacy") == bytes.fromhex(KEY_LEGACY)


def test_key_unknown_kid_raises() -> None:
    ring = KeyRing.from_env("PAPER", env={"CREDENTIAL_ENCRYPTION_KEY": KEY_LEGACY})
    with pytest.raises(UnknownKeyIdError):
        ring.key("v99")


def test_from_env_no_keys_at_all_raises_config_error() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env("PAPER", env={})


def test_from_env_versioned_keys_without_active_kid_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env("PAPER", env={"CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}"})


def test_from_env_active_kid_not_in_keys_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v2",
            },
        )


def test_from_env_bad_hex_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": "v1:not-hex",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )


def test_from_env_wrong_length_key_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": "v1:aabbcc",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )


def test_from_env_duplicate_kid_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v1:{KEY_V2}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )


def test_from_env_reserved_legacy_kid_in_versioned_var_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"legacy:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "legacy",
            },
        )


def test_from_env_rejects_live_keys_when_paper_runtime_explicit() -> None:
    """I7: PAPER 런타임 프로세스에 LIVE KEK가 있으면 기동 거부(fail-closed)."""
    with pytest.raises(FrozenZoneLiveModeBlockedError):
        KeyRing.from_env(
            "PAPER",
            env={
                "AIOS_RUNTIME_MODE": "PAPER",
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
                "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
            },
        )


def test_from_env_rejects_live_keys_when_runtime_mode_unset_defaults_paper() -> None:
    """AIOS_RUNTIME_MODE 미설정은 fail-closed 기본값 PAPER로 취급한다."""
    with pytest.raises(FrozenZoneLiveModeBlockedError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
            },
        )


def test_from_env_bad_format_error_message_does_not_leak_key_material() -> None:
    """리뷰 발견 ①: 예외 메시지에 kid:hex 원문(64자 키)이 그대로 노출되면 안 된다."""
    with pytest.raises(KeyRingConfigError) as exc_info:
        KeyRing.from_env(
            "PAPER",
            env={"CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"nocolonhere{KEY_V1}"},
        )
    assert KEY_V1 not in str(exc_info.value)


def test_from_env_empty_kid_error_message_does_not_leak_key_material() -> None:
    with pytest.raises(KeyRingConfigError) as exc_info:
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f":{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )
    assert KEY_V1 not in str(exc_info.value)


def test_from_env_rejects_live_keys_when_runtime_mode_is_lowercase_paper_typo() -> None:
    """리뷰 발견 ②: 'paper'(소문자)처럼 완전일치가 아닌 값도 fail-closed로 검사돼야 한다."""
    with pytest.raises(FrozenZoneLiveModeBlockedError):
        KeyRing.from_env(
            "PAPER",
            env={
                "AIOS_RUNTIME_MODE": "paper",
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
                "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
            },
        )


def test_from_env_rejects_live_keys_when_runtime_mode_is_misspelled() -> None:
    with pytest.raises(FrozenZoneLiveModeBlockedError):
        KeyRing.from_env(
            "PAPER",
            env={
                "AIOS_RUNTIME_MODE": "PAPR",
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
                "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
            },
        )


def test_from_env_allows_live_keys_when_runtime_mode_is_lowercase_live() -> None:
    ring = KeyRing.from_env(
        "LIVE",
        env={
            "AIOS_RUNTIME_MODE": "live",
            "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
        },
    )
    assert ring.active_kid == "v1"


def test_from_env_allows_live_keys_in_live_runtime() -> None:
    ring = KeyRing.from_env(
        "LIVE",
        env={
            "AIOS_RUNTIME_MODE": "LIVE",
            "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
        },
    )
    assert ring.active_kid == "v1"
    assert ring.key("v1") == bytes.fromhex(KEY_V2)


# --- DEEPEN(task-3138): 실패 주입 — 환경변수 백엔드 실패는 "키 없음"이 아니다


class _FlakyEnv(Mapping[str, str]):
    """특정 키 조회에서 예외를 던지는 환경(예: 시크릿 매니저 프록시 장애)을
    시뮬레이션 — `from_env`가 이 예외를 삼켜 `KeyRingConfigError`("키 없음")로
    오판(fail-open)하지 않고 원 예외를 그대로 전파해야 한다(fail-closed)."""

    def __init__(self, data: dict[str, str], boom_key: str) -> None:
        self._data = data
        self._boom_key = boom_key

    def __getitem__(self, key: str) -> str:
        if key == self._boom_key:
            raise RuntimeError("simulated env backend failure")
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def test_from_env_propagates_env_backend_failure_instead_of_treating_as_missing() -> None:
    env = _FlakyEnv(
        {
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
        },
        boom_key="CREDENTIAL_ENCRYPTION_KEYS_PAPER",
    )
    with pytest.raises(RuntimeError, match="simulated env backend failure"):
        KeyRing.from_env("PAPER", env=env)


# --- DEEPEN(task-3138): 수치 성능 단언 — from_env 버전 키 파싱 지연 --------

_FROM_ENV_ITERATIONS = 50
_FROM_ENV_BUDGET_MS = 5.0  # ADR-2026-09-09-C Decision 1의 "사전거래 게이트 p99
# 5ms"를 가장 가까운 유사 항목으로 차용(전용 예산 항목 없음) — from_env는
# 순수 파이썬 문자열 파싱 + hex 디코딩(I/O 없음)이라 로컬 실측 p95(2026-09-16
# ~0.003ms)는 이보다 수백 배 낮다.


def _from_env_latencies_ms(iterations: int = _FROM_ENV_ITERATIONS) -> list[float]:
    env = {
        "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v2:{KEY_V2}",
        "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v2",
    }
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        KeyRing.from_env("PAPER", env=env)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return samples


def _p95(samples: list[float]) -> float:
    return samples[min(int(len(samples) * 0.95), len(samples) - 1)]


def test_from_env_parsing_p95_latency_within_self_declared_budget() -> None:
    samples = _from_env_latencies_ms()
    p95_ms = _p95(samples)
    print(f"[PLT-31] KeyRing.from_env() p95={p95_ms:.4f}ms budget<{_FROM_ENV_BUDGET_MS:.0f}ms")
    assert p95_ms < _FROM_ENV_BUDGET_MS


def test_from_env_budget_gate_actually_fails_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 위 단언식이 실제 지연 주입에 대해 `AssertionError`를
    내는지 확인한다 — 이 테스트가 없으면 위 단언이 항상 통과하는
    tautology인지 아무도 검증하지 못한다."""
    original_decode = key_ring_module._decode_key

    def _slow_decode(hex_key: str, kid: str) -> bytes:
        time.sleep(_FROM_ENV_BUDGET_MS / 1000.0)
        return original_decode(hex_key, kid)

    monkeypatch.setattr(key_ring_module, "_decode_key", _slow_decode)

    samples = _from_env_latencies_ms(iterations=3)
    p95_ms = _p95(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _FROM_ENV_BUDGET_MS
