# 테스트 실행 가이드

## 왜 세션마다 DB를 따로 쓰는가

통합테스트의 55%가 실제 Postgres를 때리고, 격리는 TRUNCATE·롤백이 아니라
uuid 접미사 신규 행에 의존한다(`docs/FULL_AUDIT_2026-09-02.md` §9). 여러
세션이 하나의 `aios_dev`를 공유하면 (1) 다른 세션이 마이그레이션을 아직
적용하지 않았거나 (2) 벤치마크·싱글톤 행(`system_safety_state`)을 동시에
건드려 "N passed"가 실행마다 달라진다(§1 실측: 1차 0 failed, 2차 114
errors, 3차 7 failed — 전부 환경 간섭). CI는 세션당 새 Postgres를 쓰므로
로컬도 같은 조건으로 맞춘다.

## 세션별 DB 만들기

```bash
.venv/Scripts/python.exe scripts/setup_test_db.py <세션이름>
```

`aios_test_<세션이름>`을 만들고(없으면) `alembic upgrade head`까지 적용한 뒤
export할 `TEST_DATABASE_URL`을 출력한다. 서버 접속 정보는 `.env`의
`DATABASE_URL`에서 빌려 쓰고 DB 이름만 바꾼다. `--reset`은 DROP 후 재생성.

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/aios_test_<세션이름>
.venv/Scripts/python.exe -m pytest -q
```

`tests/conftest.py`는 `TEST_DATABASE_URL`이 없으면 수집 단계에서 중단하고,
`.env`의 `DATABASE_URL`을 읽는 레거시 픽스처도 이 값으로 강제 치환한다.

## 백그라운드 루프 플래그

라우터 통합테스트는 `src/main.py` lifespan을 통째로 띄운다. 실행 루프
스케줄러와 기동 시 재시작 복구는 DB에 남은 RUNNING 실행·미결 주문을
실거래소로 보낼 수 있어 `tests/conftest.py`가 아래 두 플래그를 `0`으로
둔다. 스케줄러·복구 자체는 `test_execution_scheduler.py`,
`test_restart_recovery.py`가 직접 호출해 검증한다.

| 플래그 | 운영 기본 | 테스트 |
|---|---|---|
| `AIOS_EXECUTION_LOOP_ENABLED` | 1 | 0 |
| `AIOS_STARTUP_RECOVERY_ENABLED` | 1 | 0 |

## 게이트 (CI와 동일)

```bash
.venv/Scripts/python.exe -m ruff check src tests scripts
.venv/Scripts/python.exe -m mypy src
.venv/Scripts/python.exe scripts/check_zone_manifest.py
.venv/Scripts/python.exe -m pytest -q --cov=src --cov-report=term
```

## 공유 작업트리 규칙 (여러 세션이 같은 clone을 쓸 때)

- 편집 전 파일 경로를 PM 세션에 공지하고, 다른 세션이 공지한 파일은 건드리지 않는다.
- `git add <자기 파일>`만. `git add -A`·stash·rebase 금지.
- **커밋은 반드시 `git commit -F - -- <자기 경로들>` 형태로 경로를 명시한다.** 작업트리뿐
  아니라 **인덱스(staging)도 공유**되므로, 다른 세션이 `git add`만 해둔 파일이 있으면
  경로 없는 `git commit`은 그 파일까지 함께 커밋한다(실제 사고: `a77e9e4`에 다른
  세션이 스테이징해 둔 paper_control 파일 10개가 함께 실림). 커밋 전
  `git diff --cached --stat`으로 인덱스 상태를 본다.
- 같은 파일에 다른 세션의 미커밋 hunk가 섞여 있으면 그 세션에 먼저 커밋을 요청한다.
  (실제 사고: `c017525`가 다른 세션의 미커밋 훅을 함께 커밋해 그 훅이 import하는
  파일이 없는 상태가 origin에 올라갔고 `2d6c71a`로 보충됐다.)
- 커밋마다 즉시 push. 마이그레이션을 만들면 리비전 id를 공지하고 체인을 직렬화한다.
- 승인·확인 요청은 사용자가 아니라 PM 세션에 보낸다. 승인 대기 중에도 다른 배정 작업을 이어간다.
