# Python 3.13 호환성 — ruff 정적 점검 결과

> **목적:** Python 3.13 이전에 걸릴 폐기·제거 API 사용처를 정적으로 찾는다.
> **실행:** `python -m ruff check src tests scripts --target-version py313 --select UP,PLW,B --statistics --exit-zero`
> **보조:** `git grep`으로 `asyncio.get_event_loop(`, `asyncio.iscoroutinefunction`, `datetime.utcnow(`, `datetime.utcfromtimestamp(`, `pkg_resources`, `distutils`, `imp`, `locale.getdefaultlocale`, `unittest.makeSuite`, `typing.io` 센다.
> **판단 기준:** 3.13에서 **실제로 깨지는 것(제거됨)** 과 **경고만 나는 것(폐기)** 을 구분한다. 코드·pyproject·ruff 설정은 고치지 않는다.

---

## 1. ruff 규칙별 요약

| 규칙 | 건수 | 분류 | 설명 |
|------|------|------|------|
| **UP017** | 1,984 | 폐기(경고) | `datetime.timezone.utc` → `datetime.UTC` |
| **UP042** | 155 | 폐기(경고) | `str`+`enum` 병렬 상속 → `StrEnum` 사용 권장 |
| **PLW0108** | 57 | 폐기(경고) | `lambda` 대신 함수명 직접 사용 가능 |
| **UP043** | 38 | 폐기(경고) | 제네릭 함수/클래스의 불필요한 기본 타입 인수 |
| **UP047** | 16 | 폐기(경고) | PEP 695 타입 파라미터 문법 권장 |
| **PLW1510** | 39 | 폐기(경고) | `subprocess.run(check=True)` 권장 |
| **UP035** | 4 | **제거됨** | `typing_extensions` → `typing`으로 이동된 import |
| **UP041** | 3 | **제거됨** | `asyncio.TimeoutError` → 내장 `TimeoutError` |
| **PLW0603** | 10 | 폐기(경고) | 전역 변수 직접 수정 |
| **PLW2901** | 7 | 폐기(경고) | 중첩 루프에서 루프 변수 재할당 |
| **PLW0406** | 2 | 폐기(경고) | 같은 패키지의 모듈을 상대 임포트 |
| **PLW1641** | 1 | 폐기(경고) | `__eq__`만 정의하고 `__hash__` 없음 |
| **UP046** | 2 | 폐기(경고) | PEP 695 제네릭 클래스 문법 권장 |

**총 2,318개 오류** (fixable: 2,029개 `--fix` 가능)

---

## 2. 3.13에서 실제로 깨지는 것 (제거됨) — UP035, UP041

### UP035 deprecated-import (4건) — 3.13에서 import 실패

`typing_extensions`에서 `typing`으로 이동된 심볼을 여전히 `typing_extensions`에서 임포트하고 있음.

| # | 파일:줄 | 심볼 |
|---|---------|------|
| 1 | `tests/foundation/unit/mandates/test_exclusion_rules.py:34` | `Unpack` |
| 2 | `tests/foundation/unit/mandates/test_rules.py:7` | `Unpack` |
| 3 | `tests/unit/foundation/market_data/domain/test_l2_orderbook.py:17` | `Unpack` |
| 4 | `tests/unit/market_data/test_lifecycle.py:26` | `Never` |

**영향:** `Unpack`과 `Never`는 Python 3.11+에서 `typing` 모듈로 이동됨. 3.13에서도 `typing_extensions`에 여전히 있지만, 향후 제거될 가능성이 높으며 표준 위치에서 임포트하는 것이 올바른 방향.

### UP041 timeout-error-alias (3건) — 3.13에서 `asyncio.TimeoutError` 제거

| # | 파일:줄 | 변경 |
|---|---------|------|
| 1 | `src/core/event_bus/in_process.py:132` | `asyncio.TimeoutError` → `TimeoutError` |
| 2 | `src/foundation/screener/application/run_screen.py:246` | `asyncio.TimeoutError` → `TimeoutError` |
| 3 | `tests/unit/services/test_risk_alerting.py:187` | `asyncio.TimeoutError` → `TimeoutError` |

**영향:** `asyncio.TimeoutError`는 3.11에서 `TimeoutError`의 별칭으로 표시되었고, 3.13에서 완전히 제거됨. 코드 수정 필수.

---

## 3. 3.13에서 경고만 나는 것 (폐기) — 미실행 시 향후 문제

### UP017 datetime-timezone-utc (1,984건) — 가장 다수

`datetime.timezone.utc` → `datetime.UTC` (3.11에서 도입된 더 짧은 별칭).

**분포:**
- `tests/`: 1,707건
- `src/`: 264건
- `scripts/`: 13건

**대표 사용처 (src/):**

| 파일:줄 | 코드 |
|---------|------|
| `src/core/event_bus/envelope.py:49` | `datetime.now(timezone.utc)` |
| `src/core/executor/executor.py:92` | `datetime.fromtimestamp(floored, tz=timezone.utc)` |
| `src/api/routers/ai.py:100` | `datetime.now(timezone.utc)` |
| `src/api/routers/backtests.py:118` | `datetime.now(timezone.utc)` |
| `src/api/routers/health.py:96` | `datetime.now(timezone.utc)` |
| `src/api/routers/foundation/ledger_admin.py:52` | `datetime.now(timezone.utc)` |
| `src/api/mcp/tools_research_data.py:86` | `datetime.now(timezone.utc)` |
| `src/core/logging/fields.py:92` | `datetime.now(timezone.utc)` |
| `src/core/logging/schema.py:74` | `datetime.now(timezone.utc)` |

**대표 사용처 (scripts/):**

| 파일:줄 | 코드 |
|---------|------|
| `scripts/backup/base_backup.py:37` | `dt.datetime.now(dt.timezone.utc)` |
| `scripts/bench/_common.py:60` | `datetime.now(timezone.utc)` |
| `scripts/ledger_backfill.py:42` | `datetime.now(timezone.utc)` |

**영향:** 3.13에서도 `timezone.utc`는 작동하지만, `datetime.UTC`가 선호되는 새로운 표준. 자동 수정 가능(`--fix`).

### UP042 replace-str-enum (155건) — `str`+`enum` 병렬 상속

`class Foo(str, Enum)` → `class Foo(StrEnum)`으로 변경 권장.

**상위 파일:**
- `src/foundation/market_data/contracts/v1_enums.py`: 7건
- `src/foundation/risk_gate/domain/models.py`: 10건 (UP042 + UP04)
- `src/foundation/trust/domain/models.py`: 5건
- `src/foundation/ledger/contracts/v1.py`: 5건

**영향:** 3.13에서도 작동하지만, `StrEnum` (3.11 도입)이 더 명시적이고 안전한 방식.

### UP043 unnecessary-default-type-args (38건)

제네릭에 타입 인수가 이미 기본값과 같으면 제거 권장.

**대표:**
- `tests/adversarial/risk/test_fence_race.py:61`: `AsyncGenerator[None, None]` → `AsyncGenerator[None]`
- `tests/foundation/adversarial/validation/test_cross_tenant_isolation.py:44`: `AsyncGenerator[asyncpg.Pool, None]` → `AsyncGenerator[asyncpg.Pool]`
- `tests/unit/exchanges/kis/test_trading_mixin_precheck.py:64`: `Generator[None, None, None]` → `Generator[None]`

**영향:** 3.13에서 경고 발생. 자동 수정 가능.

### UP047 non-pep695-generic-function (16건)

제네릭 함수를 PEP 695 문법(`def fn[T](...)`)으로 변경 권장.

**상위 파일:**
- `src/core/bitemporal.py`: 5개 함수 (`check_no_overlap`, `as_of`, `current`, `as_of_valid_time`, `as_of_transaction_time`, `as_of_bitemporal`)
- `tests/support/perf_inject.py`: 4개 함수 (`virtual_delayed_async`, `virtual_delayed_sync`, `delayed_async`, `delayed_sync`)
- `src/api/contracts/envelope.py:55`: `ok`
- `src/core/db/session_policy.py:94`: `enforce_session_policy`

**영향:** 3.13에서도 구문으로 작동하지만, 3.12+에서 PEP 695가 안정화됨. 자동 수정 불가(`--unsafe-fixes` 필요).

### UP041과 유사 — asyncio.TimeoutError 별칭 (3건)

위 §2에서 이미 다룸.

### 기타 PLW 규칙 (121건 합계)

| 규칙 | 건수 | 설명 |
|------|------|------|
| PLW0108 | 57 | `lambda x: foo(x)` → `foo` |
| PLW1510 | 39 | `subprocess.run(..., check=True)` 권장 |
| PLW0603 | 10 | 전역 변수 직접 수정 |
| PLW2901 | 7 | 중첩 루프 변수 재할당 |
| PLW0406 | 2 | 같은 패키지 상대 임포트 |
| PLW1641 | 1 | `__eq__`만 정의 |

---

## 4. git grep 결과 — 명시적 폐기 API 검색

| API | 건수 | 상태 | 비고 |
|-----|------|------|------|
| `asyncio.get_event_loop(` | **1** | **위험** | `tests/support/orders_residue.py:18` |
| `asyncio.iscoroutinefunction` | 0 | 양호 | — |
| `datetime.utcnow(` | 0 | 양호 | 테스트 내 문자열 리터럴만 확인됨 |
| `datetime.utcfromtimestamp(` | 0 | 양호 | — |
| `pkg_resources` | 0 | 양호 | — |
| `distutils` | 0 | 양호 | — |
| `imp` 모듈 | 0 | 양호 | — |
| `locale.getdefaultlocale` | 0 | 양호 | — |
| `unittest.makeSuite` | 0 | 양호 | — |
| `typing.io` | 0 | 양호 | — |

### asyncio.get_event_loop() — 주의 필요

**`tests/support/orders_residue.py:18`**:
```python
loop = asyncio.get_event_loop()
```

Python 3.10+: 이벤트 루프가 없으면 `DeprecationWarning` 발생.
Python 3.13+: 이벤트 루프 정책 변경으로 `RuntimeError` 발생 가능.
3.14에서 완전히 제거 예정.

**권장:** `asyncio.get_running_loop()` (이미 루프가运行的 경우) 또는 `asyncio.run()` 사용.

---

## 5. 분류 요약

### 실제로 3.13에서 깨지는 것 (제거됨) — 수정 필수

| 항목 | 건수 | 우선순위 |
|------|------|----------|
| UP041 `asyncio.TimeoutError` | 3 | **높음** — 3.11에서 이미 제거됨 (3.13 확인) |
| UP035 `typing_extensions` 이동 import | 4 | **중간** — 3.11+ `typing`에서 import |
| `asyncio.get_event_loop()` | 1 | **높음** — 3.10+ Deprecation, 3.14에서 제거 예정 |

### 3.13에서도 작동하지만 향후 제거 예정 (폐기) — 계획적 수정

| 항목 | 건수 | 권장 |
|------|------|------|
| UP017 `timezone.utc` → `UTC` | 1,984 | `--fix`로 자동 수정 가능 |
| UP042 `str`+`Enum` → `StrEnum` | 155 | 점진적 교체 |
| UP043 불필요한 제네릭 기본 인수 | 38 | `--fix`로 자동 수정 가능 |
| UP047 PEP 695 타입 파라미터 | 16 | 수동 리팩토링 권장 |
| UP046 PEP 695 제네릭 클래스 | 2 | 수동 리팩토링 권장 |
| PLW 규칙 합계 | 121 | 코드 품질 개선 |

### 미확인

- `pyproject.toml` 또는 `ruff.toml`에 `target-version` 설정이 이미 존재하는지 확인했으나, 명령행 옵션 `--target-version py313`이 세팅을 덮어쓰므로 설정 변경은 불필요.
- `typing.io`, `typing.re`는 3.11에서 이미 제거됨 — 본 저장소에서는 사용 안 함 확인.
- `importlib.metadata`로 `pkg_resources`를 대체한 이력이 없음 (0건 확인).

---

## 6. 권장 조치 순서

1. **즉시 (3.13 릴리스 전):** UP041 3건 + `asyncio.get_event_loop()` 1건 수정
2. **중기:** UP035 4건 (`typing_extensions` → `typing`)
3. **계획적:** UP017 1,984건 (`ruff --fix UP017`)
4. **선택적:** UP042, UP043, UP047, PLW 규칙 — 코드 가독성 개선 차원에서 점진적 적용

---

## 7. 실행 명령 재현

```bash
# ruff 전체 점검
python -m ruff check src tests scripts --target-version py313 --select UP,PLW,B --statistics --exit-zero

# git grep 보조 검색
git grep -c 'asyncio\.get_event_loop(' -- src/ tests/ scripts/
git grep -c 'asyncio\.iscoroutinefunction' -- src/ tests/ scripts/
git grep -c 'datetime\.utcnow(' -- src/ tests/ scripts/
git grep -c 'datetime\.utcfromtimestamp(' -- src/ tests/ scripts/
git grep -c 'pkg_resources' -- src/ tests/ scripts/
git grep -c 'distutils' -- src/ tests/ scripts/
git grep -c 'import imp$\|from imp ' -- src/ tests/ scripts/
git grep -c 'locale\.getdefaultlocale' -- src/ tests/ scripts/
git grep -c 'unittest\.makeSuite' -- src/ tests/ scripts/
git grep -c 'typing\.io' -- src/ tests/ scripts/
```

---

*생성일: 2026-10-29 | 워커: claude-local-2 | task-20673*
