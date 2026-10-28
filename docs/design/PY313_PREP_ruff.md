# Python 3.13 대비 정적 분석 리포트 (ruff --target-version py313)

**생성일**: 2026-10-29
**명령어**: `python -m ruff check src tests scripts --target-version py313 --select UP,PLW,B --statistics --exit-zero`
**git grep 추가 검사**: `asyncio.get_event_loop(`, `asyncio.iscoroutinefunction`, `datetime.utcnow(`, `datetime.utcfromtimestamp(`, `pkg_resources`, `distutils`, `imp.`, `locale.getdefaultlocale(`, `unittest.makeSuite`, `typing.io`

---

## 요약

| 규칙 | 건수 | 상태 | 설명 |
|------|------|------|------|
| UP017 (datetime-timezone-utc) | **1,984** | ⚠️ 폐기(3.13) | `timezone.utc` → `datetime.UTC` 권장 |
| UP042 (replace-str-enum) | **155** | ⚠️ 폐기(3.13) | `str, Enum` → `StrEnum` 권장 |
| PLW0108 (unnecessary-lambda) | **57** | ℹ️ 스타일 | 불필요한 lambda |
| UP043 (unnecessary-default-type-args) | **38** | ⚠️ 폐기(3.13) | 제네릭 기본 타입 인수 제거 권장 |
| PLW1510 (subprocess-run-without-check) | **39** | ℹ️ 스타일 | `check` 명시 권장 |
| UP047 (non-pep695-generic-function) | **14** | ⚠️ 폐기(3.13) | PEP 695 타입 파라미터 권장 |
| UP046 (non-pep695-generic-class) | **4** | ⚠️ 폐기(3.13) | PEP 695 타입 파라미터 권장 |
| UP035 (deprecated-import) | **4** | ⚠️ 폐기(3.13) | `typing_extensions` → `typing` 이동 |
| UP041 (timeout-error-alias) | **3** | ⚠️ 폐기(3.13) | `asyncio.TimeoutError` → `TimeoutError` |
| PLW0603 (global-statement) | **10** | ℹ️ 스타일 | 전역 변수 사용 |
| PLW2901 (redefined-loop-name) | **7** | ℹ️ 스타일 | 루프 변수 재할당 |
| PLW0406 (import-self) | **2** | ⚠️ 경고(3.13) | self import |
| PLW1641 (eq-without-hash) | **1** | ℹ️ 스타일 | `__eq__`만 재정의 |
| **합계** | **2,318** | | |

---

## 1. UP017 — `datetime-timezone-utc` (1,984건) ⚠️ 3.13에서 경고

**3.13 영향**: `datetime.timezone.utc`는 `datetime.UTC`로 대체됨. 코드는 여전히 동작하지만 DeprecationWarning 발생.

**대표 사용처 (상위 10)**:

| 파일:줄 | 내용 |
|---------|------|
| `scripts/backup/base_backup.py:37` | `dt.datetime.now(dt.timezone.utc)` |
| `scripts/backup/restore_drill.py:57` | `dt.datetime.now(dt.timezone.utc)` |
| `scripts/bench/_common.py:60` | `datetime.now(timezone.utc).isoformat()` |
| `scripts/bench/backtest_instant_bench.py:48` | `datetime(2025, 1, 1, tzinfo=timezone.utc)` |
| `scripts/bench/instrument_lookup_bench.py:79` | `datetime.now(timezone.utc)` |
| `scripts/canary_bitget.py:175` | `datetime.now(timezone.utc).date().isoformat()` |
| `scripts/kis_tr_fetch.py:112` | `datetime.now(timezone.utc)` |
| `scripts/ledger_backfill.py:42` | `datetime.now(timezone.utc)` |
| `scripts/nh_openapi_fetch.py:146` | `datetime.now(timezone.utc).isoformat(timespec="seconds")` |
| `scripts/observability/notify_selftest.py:37` | `dt.datetime.now(dt.timezone.utc)` |

**분포**: `tests/unit/services/` 내 테스트 파일 45건, `src/api/routers/` 20건, `scripts/` 12건, 그 외 `src/` 전역.

---

## 2. UP042 — `replace-str-enum` (155건) ⚠️ 3.13에서 폐기

**3.13 영향**: `str, Enum` 조합은 `StrEnum`으로 대체해야 함. 3.11에서 `StrEnum` 도입됨.

**대표 클래스 (상위 10)**:

| 파일:줄 | 클래스명 |
|---------|---------|
| `scripts/canary_bitget_lib/gate.py:25` | `GateVerdict` |
| `src/api/contracts/error_codes.py:16` | `ErrorCode` |
| `src/core/event_bus/policy.py:10` | `HandlerCriticality` |
| `src/core/indicators/catalog/registry_tiers.py:68` | `Tier` |
| `src/core/notifications/channel_policy.py:16` | `NotificationChannel` |
| `src/core/portfolio/config.py:36` | `SizingMethod` |
| `src/core/risk/decision.py:25` | `RiskOutcome` |
| `src/core/risk/decision.py:33` | `GateKind` |
| `src/core/risk/limits.py:39` | `LimitScope` |
| `src/core/risk/limits.py:48` | `LimitMetric` |

**FROZEN_PAPER 영역**: `src/core/event_bus/policy.py`, `src/core/indicators/catalog/registry_tiers.py`, `src/core/notifications/channel_policy.py`, `src/core/portfolio/config.py`, `src/core/risk/decision.py`, `src/core/risk/limits.py`, `src/core/risk/policy_bundle.py` — 수정 시 FROZEN 승인 필요.

**분포**: `src/foundation/` 내 도메인 모델 80건, `src/core/` 20건, `src/api/` 5건, `src/exchanges/` 5건.

---

## 3. UP043 — `unnecessary-default-type-args` (38건) ⚠️ 3.13에서 폐기

**3.13 영향**: `AsyncGenerator[T, None]` → `AsyncGenerator[T]` (두 번째 타입 인수는 기본값이므로 불필요).

**대표 사용처 (상위 10)**:

| 파일:줄 | 내용 |
|---------|------|
| `tests/adversarial/risk/test_fence_race.py:61` | `AsyncGenerator[None, None]` |
| `tests/foundation/adversarial/validation/test_cross_tenant_isolation.py:44` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/foundation/integration/performance/conftest.py:33` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/api/test_envelope_everywhere_deepen_3164.py:61` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/api/test_envelope_everywhere_deepen_3167.py:73` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/api/test_envelope_handler_contracts_3156.py:53` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/api/test_middleware_trace_deepen_3156.py:42` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/api/test_rate_limit_storm.py:43` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/db/test_migration_tenant_membership.py:70` | `AsyncGenerator[asyncpg.Pool, None]` |
| `tests/integration/db_schema/conftest.py:35` | `AsyncGenerator[asyncpg.Pool, None]` |

---

## 4. UP047 — `non-pep695-generic-function` (14건) ⚠️ 3.13에서 폐기

**3.13 영향**: `def func[T](...)` 문법 사용 권장. `TypeVar` 기반은 여전히 동작하지만 스타일 경고.

**대표 함수**:

| 파일:줄 | 함수명 |
|---------|--------|
| `src/api/contracts/envelope.py:55` | `ok` |
| `src/core/bitemporal.py:132` | `check_no_overlap` |
| `src/core/bitemporal.py:148` | `as_of` |
| `src/core/bitemporal.py:162` | `current` |
| `src/core/bitemporal.py:167` | `as_of_valid_time` |
| `src/core/bitemporal.py:177` | `as_of_transaction_time` |
| `src/core/bitemporal.py:188` | `as_of_bitemporal` |
| `src/core/db/session_policy.py:94` | `enforce_session_policy` |
| `src/core/portfolio/sizing/__init__.py` | `require` |
| `tests/support/perf_inject.py:42` | `virtual_delayed_async` |
| `tests/support/perf_inject.py:54` | `virtual_delayed_sync` |
| `tests/support/perf_inject.py:64` | `delayed_async` |
| `tests/support/perf_inject.py:74` | `delayed_sync` |

---

## 5. UP046 — `non-pep695-generic-class` (4건) ⚠️ 3.13에서 폐기

| 파일:줄 | 클래스명 |
|---------|---------|
| `src/api/contracts/envelope.py:42` | `ApiResponse` |
| `src/core/bitemporal.py:81` | `BitemporalRecord` |

---

## 6. UP035 — `deprecated-import` (4건) ⚠️ 3.13에서 폐기

**3.13 영향**: `typing_extensions`에서 `typing`으로 이동된 타입.

| 파일:줄 | import | 대체 |
|---------|--------|------|
| `tests/foundation/unit/mandates/test_exclusion_rules.py:34` | `from typing_extensions import Unpack` | `from typing import Unpack` |
| `tests/foundation/unit/mandates/test_rules.py:7` | `from typing_extensions import Unpack` | `from typing import Unpack` |
| `tests/unit/foundation/market_data/domain/test_l2_orderbook.py:17` | `from typing_extensions import Unpack` | `from typing import Unpack` |
| `tests/unit/market_data/test_lifecycle.py:26` | `from typing_extensions import Never` | `from typing import Never` |

---

## 7. UP041 — `timeout-error-alias` (3건) ⚠️ 3.13에서 폐기

**3.13 영향**: `asyncio.TimeoutError`는 `TimeoutError`의 별명이며, 3.13에서 별명 제거됨.

| 파일:줄 | 내용 |
|---------|------|
| `src/core/event_bus/in_process.py:132` | `except asyncio.TimeoutError:` |
| `src/foundation/screener/application/run_screen.py:246` | `except asyncio.TimeoutError as exc:` |
| `tests/unit/services/test_risk_alerting.py:187` | `exception=asyncio.TimeoutError()` |

---

## 8. git grep 검사 결과

| 패턴 | 건수 | 상태 |
|------|------|------|
| `asyncio.get_event_loop(` | **1** | ⚠️ 3.13에서 제거됨 |
| `asyncio.iscoroutinefunction` | **0** | ✅ |
| `datetime.utcnow(` | **3** | ⚠️ 3.13에서 제거됨 (테스트용) |
| `datetime.utcfromtimestamp(` | **0** | ✅ |
| `pkg_resources` | **0** | ✅ |
| `distutils` | **0** | ✅ |
| `imp.` | **0** | ✅ |
| `locale.getdefaultlocale(` | **0** | ✅ |
| `unittest.makeSuite` | **0** | ✅ |
| `typing.io` | **0** | ✅ |

### `asyncio.get_event_loop()` 사용처 (1건)

| 파일:줄 | 내용 |
|---------|------|
| `tests/support/orders_residue.py:18` | `loop = asyncio.get_event_loop()` |

**3.13 영향**: 이벤트 루프가 없으면 `DeprecationWarning` 발생 → 3.14에서 `RuntimeError`로 변경 예정. `asyncio.get_running_loop()`으로 대체 권장.

### `datetime.utcnow()` 사용처 (3건)

| 파일:줄 | 내용 |
|---------|------|
| `tests/unit/scripts/test_check_consistency.py:54` | 테스트용 더미 코드 |
| `tests/unit/scripts/test_check_consistency.py:69` | 테스트용 더미 코드 |
| `tests/unit/scripts/test_check_consistency_time_money.py:58` | 테스트용 더미 코드 |

**3.13 영향**: 3.12에서 제거됨. 모든 3건은 테스트용 더미 코드(`_write`로 임시 파일 생성). 실제 프로덕션 코드에는 없음.

---

## 9. PLW 경고 (스타일/안티패턴)

### PLW0108 — `unnecessary-lambda` (57건) ℹ️

`_best_of()`, `assert_within()` 호출에서 lambda가 직접 함수 참조로 전달되는 경우. 3.13 영향 없음.

### PLW1510 — `subprocess-run-without-check` (39건) ℹ️

`subprocess.run()`에 명시적 `check=False` 누락. 대부분 `noqa`로 마킹됨. 3.13 영향 없음.

### PLW0603 — `global-statement` (10건) ℹ️
### PLW2901 — `redefined-loop-name` (7건) ℹ️
### PLW0406 — `import-self` (2건) ⚠️
### PLW1641 — `eq-without-hash` (1건) ℹ️

---

## 10. 3.13에서 실제로 깨지는 것 vs 경고만 나는 것

### 🔴 3.13에서 제거됨 (코드 수정 필수)

| 항목 | 건수 | 설명 |
|------|------|------|
| `datetime.utcnow()` | 3 | 테스트용 더미 코드만 (실제 프로덕션 코드 없음) |
| `asyncio.get_event_loop()` | 1 | 테스트 지원 코드 |
| `typing_extensions.Unpack` | 3 | → `typing.Unpack`으로 이동 |
| `typing_extensions.Never` | 1 | → `typing.Never`으로 이동 |

### 🟡 3.13에서 폐기됨 (DeprecationWarning, 수정 권장)

| 항목 | 건수 | 설명 |
|------|------|------|
| `timezone.utc` | 1,984 | `datetime.UTC`로 대체 |
| `str, Enum` | 155 | `StrEnum`으로 대체 |
| `asyncio.TimeoutError` | 3 | `TimeoutError`로 대체 |
| `AsyncGenerator[T, None]` | 38 | `AsyncGenerator[T]`로 단순화 |
| PEP 695 미적용 | 18 | 함수/클래스 타입 파라미터 |

### 🟢 3.13에서 영향 없음 (스타일)

| 항목 | 건수 |
|------|------|
| PLW0108 unnecessary-lambda | 57 |
| PLW1510 subprocess-run-without-check | 39 |
| PLW0603 global-statement | 10 |
| PLW2901 redefined-loop-name | 7 |
| PLW1641 eq-without-hash | 1 |

---

## 11. 미확인 항목

아래 항목은 `git grep`으로 검색했으나 저장소 내에 존재하지 않음:

- `datetime.utcfromtimestamp(` — 0건
- `pkg_resources` — 0건
- `distutils` — 0건
- `imp.` — 0건
- `locale.getdefaultlocale(` — 0건
- `unittest.makeSuite` — 0건
- `typing.io` — 0건
- `asyncio.iscoroutinefunction` — 0건

---

## 12. 권장 우선순위

1. **P0 (3.13에서 제거됨)**: `datetime.utcnow()` 3건, `asyncio.get_event_loop()` 1건 — 테스트용 코드이나 3.13에서 RuntimeError로 이어질 수 있음
2. **P1 (3.13에서 폐기됨)**: `typing_extensions` → `typing` 이동 4건 — ruff `--fix`로 자동 해결
3. **P2 (3.13에서 폐기됨)**: `asyncio.TimeoutError` → `TimeoutError` 3건 — ruff `--fix`로 자동 해결
4. **P3 (대규모)**: `timezone.utc` → `datetime.UTC` 1,984건 — ruff `--fix`로 자동 해결 가능하나 FROZEN_PAPER 영역 확인 필요
5. **P4 (대규모)**: `str, Enum` → `StrEnum` 155건 — ruff `--fix` 가능, FROZEN_PAPER 영역 확인 필요
6. **P5**: 나머지 스타일 경고 (PLW 계열) — 3.13 영향 없음

---

**코드·pyproject·ruff 설정은 수정하지 않음. 확인 못 한 항목은 '미확인'으로 표기.**
