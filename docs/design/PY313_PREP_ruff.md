# Python 3.13 준비 — ruff 비호환·폐기 API 점검 결과

> 태스크: task-20673 | 실행일: 2026-10-28

## 1. ruff 규칙별 건수

`python -m ruff check src tests scripts --target-version py313 --select UP,PLW,B --statistics --exit-zero`

| 규칙 | 건수 | 자동 수정 | 의미 |
|------|------|-----------|------|
| UP017 | 1982 | ○ | `datetime.timezone.utc` → `datetime.UTC` |
| UP042 | 155 | ○ | `str` enum → `str, Enum` |
| PLW0108 | 57 | × | `lambda` → 직접 함수 참조 |
| UP043 | 38 | ○ | `List[T]` → `list[T]` 등 |
| PLW1510 | 39 | ○ | `subprocess.run` check=True 누락 |
| UP047 | 14 | ○ | PEP 695 제네릭 함수 |
| PLW0603 | 8 | × | global 문 |
| PLW2901 | 7 | ○ | 재정의된 루프 변수 |
| UP035 | 4 | ○ | deprecated-import (typing_extensions → typing) |
| UP041 | 3 | ○ | asyncio.TimeoutError → TimeoutError |
| PLW0406 | 2 | × | import-self |
| UP046 | 2 | ○ | PEP 695 제네릭 클래스 |
| PLW1641 | 1 | × | __eq__ without __hash__ |
| **합계** | **2312** | | |

## 2. 10개 폐기 API 패턴별 건수

`git grep -n -E "asyncio\.get_event_loop\(|asyncio\.iscoroutinefunction|datetime\.utcnow\(|datetime\.utcfromtimestamp\(|pkg_resources|distutils|^\s*import imp\b|locale\.getdefaultlocale|unittest\.makeSuite|typing\.io"`

| 패턴 | 건수 | 코드 히트 | 3.13 상태 |
|------|------|-----------|-----------|
| `datetime.utcnow(` | 3 | `tests/unit/scripts/test_check_consistency.py:54, 69`<br>`tests/unit/scripts/test_check_consistency_time_money.py:58` | **제거됨** |
| `asyncio.get_event_loop(` | 1 | `tests/support/orders_residue.py:18` | **경고** |
| `asyncio.iscoroutinefunction` | 0 | — | 경고 |
| `datetime.utcfromtimestamp(` | 0 | — | **제거됨** |
| `pkg_resources` | 0 | — | 제거됨 |
| `distutils` | 0 | — | 제거됨 |
| `import imp` | 0 | — | **제거됨** |
| `locale.getdefaultlocale` | 0 | — | **제거됨** |
| `unittest.makeSuite` | 0 | — | 제거됨 |
| `typing.io` | 0 | — | 제거됨 |

### 3.13에서 제거된 API (실행 시 ImportError / AttributeError)

| API | 상태 | 참고 |
|-----|------|------|
| `datetime.utcnow()` | **제거됨** | Python 3.13 릴리스 노트 — `datetime.now(timezone.utc)` 사용 |
| `datetime.utcfromtimestamp()` | **제거됨** | Python 3.13 릴리스 노트 |
| `import imp` | **제거됨** | Python 3.13 릴리스 노트 — `importlib` 사용 |
| `locale.getdefaultlocale()` | **제거됨** | Python 3.13 릴리스 노트 |
| `pkg_resources` | 제거됨 | setuptools에서 제거 (미확인) |
| `distutils` | 제거됨 | Python 3.12에서 제거, 3.13에도 없음 (미확인) |
| `unittest.makeSuite()` | 제거됨 | 미확인 |
| `typing.io` | 제거됨 | 미확인 |

### 경고만 나는 것 (폐기, 동작은 하지만 향후 제거 예정)

| API | 상태 | 참고 |
|-----|------|------|
| `asyncio.get_event_loop()` | **경고** | Python 3.14에서 제거 예정 — `asyncio.get_running_loop()` 또는 `asyncio.Runner()` 사용 |
| `asyncio.iscoroutinefunction()` | 경고 | 미확인 |

## 3. 대표 사용처 상세

### datetime.utcnow() — 3건 (모두 문자열 리터럴, 픽스처 코드)

```
tests/unit/scripts/test_check_consistency.py:54    _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
tests/unit/scripts/test_check_consistency.py:69    "import datetime\nx = datetime.datetime.utcnow()\ny = datetime.datetime.utcnow()\n",
tests/unit/scripts/test_check_consistency_time_money.py:58  _write(tmp_path, "src/foo.py", "import datetime\nx = datetime.datetime.utcnow()\n")
```

**판단**: 모두 `check_consistency.py` 게이트의 테스트 픽스처에서 `utcnow()` 감지 기능을 검증하기 위한
가짜 소스 코드 문자열이다. 실제 호출이 아니므로 코드 수정 대상이 아니다.

### asyncio.get_event_loop() — 1건 (실제 호출)

```
tests/support/orders_residue.py:18    loop = asyncio.get_event_loop()
```

**판단**: 실제 호출. Python 3.14에서 제거 예정. `asyncio.get_running_loop()` 또는 `asyncio.Runner()`로 교체 필요.
테스트 지원 코드이므로 우선순위 낮음.

## 4. 확인하지 못한 항목

아래 항목들에 대해 Python 3.13 공식 릴리스 노트에서 제거/경고 여부를 확인하지 못했다:

- `pkg_resources` — setuptools 의존, 3.13과 무관할 가능성 높음
- `distutils` — Python 3.12에서 이미 제거
- `unittest.makeSuite()` — unittest 모듈에서 제거 여부 미확인
- `typing.io` — typing 하위 모듈 제거 여부 미확인
- `asyncio.iscoroutinefunction()` — 3.13에서 변경 여부 미확인

## 5. ruff UP017 (1982건) 특이사항

`UP017`은 `dt.timezone.utc` → `dt.UTC` 변환을 권고하는 규칙이다.
`datetime.UTC`는 Python 3.11에서 도입된 별칭이며, 3.13에서 `timezone.utc`가 제거되는 것은 아니다.
따라서 3.13 호환성에 직접적인 영향을 주지 않으나, 코드를 현대화하는 방향으로 권장한다.
