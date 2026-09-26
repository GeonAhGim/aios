---
name: ci-red-triage
description: GitHub Actions Quality Gate 적색을 점검하고 근본 원인까지 수리해 녹색 PR로 마감하는 절차 — PM(대시보드 호출)·클라우드 세션 공용
paths:
  - ".github/workflows/**"
  - "tests/**"
  - "src/**"
tier: [S, M, L]
axis: ops
---

# ci-red-triage

## 적용 조건

PM이 대시보드에서 "CI 점검·수리"로 호출됐거나, main/PR의 Quality Gate(`quality.yml`)가
적색일 때 적용한다. 목표는 "재실행해서 녹색"이 아니라 **실패마다 근본 원인을 특정하고
결정적으로 재현한 뒤 고쳐서** 다음 run에서 같은 이유로 다시 빨개지지 않게 하는 것이다.
2026-09-25~26 세션에서 main 40 failed → 0 failed까지 간 실제 절차를 그대로 옮겼다.

## 절대 규칙

1. 테스트를 skip·xfail·삭제·예산 완화로 녹색을 만들지 않는다. 예산 수치(ADR-2026-09-09-C)는
   CTO 결정 사항이라 손대지 않는다(DECISION_GUIDELINES B-2).
2. 실패 1건 = 근본 원인 1건 = 리프 커밋 1건. 커밋 본문에 run 번호·관측값·재현 방법·검증
   결과를 적는다. `git add/commit -- <경로>`만 쓴다. `git stash` 금지(파일 복사로 대체).
3. 재현 없이 고치지 않는다. 단독 실행에서 통과하는 실패는 "순서 의존"이며, leftover
   상태를 직접 시드해 결정적으로 재현한 뒤 고친다(아래 §4).
4. src를 고칠 때는 스펙(`docs/specs/L4_*.md`)·INVARIANTS.md와 대조하고, 안전축(R/L4/FA/CM)은
   D3 증빙(적대 테스트 + 스펙 원문 인용)을 남긴다.
5. 전체 스위트는 CI 동일 조건(`-n auto --dist loadfile -m "not perf ..."`)으로 **백그라운드
   1회**만 돌린다. 나머지는 실패 파일 단위로 좁힌다.

## 절차

### 1. 실패 목록 확보

```
# 최신 run의 verify 잡 로그에서 요약만 뽑는다(GitHub 로그 API는 뒤쪽 Postgres 컨테이너
# 로그로 ~665KB가 채워져 트레이스백이 잘릴 수 있다 — 요약 줄은 항상 남는다)
python - <<'PY'
import json,re
s=json.load(open("<saved job log json>"))["logs_content"]
for l in s.split("\n"):
    if re.search(r"FAILED tests|ERROR tests|= \d+ failed|passed.*in \d+", l): print(l[29:300])
PY
```

- 트레이스백이 잘렸으면 `_ <test_name> _` 마커로 다시 찾고, 그래도 없으면 로컬 재현으로
  간다(Actions 페이지 raw log가 있으면 그것이 정본).
- `Test` 단계와 `Test (perf, serial)` 단계를 구분한다. perf 단계는 Actions에서 warn
  (`continue-on-error`)이며 로컬 게이트가 정본이다.

### 2. 분류 (실패 1건마다)

| 부류 | 판별 | 처방 |
|---|---|---|
| A. 결정적 | 단독 실행에서도 실패 | src 또는 테스트 결함. 스펙과 대조해 근본 수정 |
| B. 순서 의존 | 단독 통과, 전체 스위트에서만 실패 | 워커 DB 공유 leftover(§4) — 시드 재현 후 정리 픽스처/고유 id/일회용 클론 |
| C. perf 예산 | wall-clock/cpu 예산 초과 | 마커 누락이면 `@pytest.mark.perf`; 마커가 있고 base도 같은 값으로 실패면 러너 계통 차이 → PR 1회 코멘트, 예산 미변경 |
| D. 환경/설치 | 설치·ruff·mypy 단계 실패, 의존성 충돌 | lock/pyproject 대조(`docs/DEPENDENCIES.md` 절차: 3.10 스크래치 venv + freeze), dependabot 그룹화 |
| E. base 적색 | 같은 실패가 base(main)에서도 재현 | 기존 수정 PR을 포팅하거나 없으면 PR에 1회 코멘트 후 대기 |

### 3. 결정적(A) 처리

- 재현 → 원인 코드 확인 → 스펙 원문 대조(예: recovery_gate baseline은 §4.3 423행
  "warning 미만"인데 구현은 `<= 0` 요구) → 수정 → 회귀 테스트(원본 코드에서 red, 수정 후
  green을 둘 다 기록) → 대상 파일 pytest + `ruff check` + `mypy <src 파일>` +
  `scripts/check_code_language.py`.

### 4. 순서 의존(B) 처리 — 워커 DB 공유가 원인의 90%

xdist 워커는 파일 단위로 같은 DB를 이어 쓴다. 앞 파일이 남긴 상태가 뒤 파일의 전제를
깬다. 지금까지 확인된 유형과 처방:

| 남는 상태 | 증상 | 처방(선례) |
|---|---|---|
| `order_command_outbox` PENDING 행 | `claim_batch`가 남의 행을 먼저 선점 → `claimed == []`, 주문이 VALIDATED | 모듈 autouse 픽스처로 outbox 비우기(test_stale_worker_late_write, e2e-1) |
| `safety_control` STRATEGY_DEPLOYMENT ACTIVE (`exec:1`) | 무관한 게이트가 `RISK_KILL_SWITCH_ACTIVE_*` DENY | 테스트별 고유 execution_id(test_no_mandate_reject) |
| RUNNING `strategy_executions` 손실 | CB tick이 emergency | 일회용 클론 + 클론 안 RUNNING→RETIRED(test_circuit_breaker_loop) |
| 로거 `disabled=True` | caplog 비어 있음 | Alembic env.py `disable_existing_loggers=False` + 격리 픽스처가 `disabled` 복원 |
| 워커 DB에 세션 존재 | `CREATE DATABASE ... TEMPLATE` ObjectInUseError | `tests/support/db.template_database_url()`로 원본 템플릿에서 복제 |
| 모듈 상수 시각(`_FUTURE = now()+5m`) | 스위트 후반에 과거가 됨 | 호출 시점 계산 헬퍼 |
| 같은 heartbeat tmp 파일 | 두 프로세스 rename 경합 | pid/uuid 접미(이미 main) |
| ProcessPoolExecutor 워커 재사용 | PID 고유성 가정 깨짐 | `multiprocessing.Process` 명시 기동 |
| EXCLUDE 대기 사이클 | PG DeadlockDetectedError | 희생자를 비성공으로 집계(불변식 유지) |

재현 방법: 실패 파일 앞에 leftover를 시드하는 임시 테스트를 두고 `-p no:randomly`로 두
파일만 돌린다(예: PENDING 12행 시드 → 1 failed 확인 → 수정 → 같은 순서 green). 임시 파일은
커밋 전에 지운다.

### 5. 검증·PR

- 리프별 커밋, draft PR(`claude/<branch>`), 본문에 실패→원인→재현→수정→검증 표.
- PR 이벤트 구독 + 1시간 체크인. verify가 다시 빨개지면 §1부터 반복(라운드 제한 없음).
- main이 전진해 충돌하면 main을 머지(rebase 금지)하고 같은 결함을 로컬이 먼저 고쳤으면
  main 쪽을 채택한다(heartbeat 선례).
- 머지되면: main에 `workflow_dispatch`로 재검증 → dependabot PR은 브랜치 갱신(main 머지)으로
  재검증(`@dependabot rebase` 댓글은 API 경로에서 멘션이 변형돼 무효).

### 6. 보고 형식

| 실패 | 부류 | 근본 원인 | 재현 | 수정 커밋 | 검증 |
|---|---|---|---|---|---|

남은 미해결 건은 "왜 못 잡았는지(로그 잘림, 로컬 미재현 등)"와 다음 시도 방법을 적는다.

## 근거

- 2026-09-25 main run #824(40 failed) → #844(1 failed) → PR #91 head c55eedc(0 failed):
  PR #88, #89, #91의 커밋 본문이 각 실패의 run 번호·재현·수정을 담고 있다.
- `docs/DEPENDENCIES.md`(lock 절차), `tests/support/db.py`(워커 DB·템플릿 복제),
  `ADR-2026-09-09-C`(예산표), `.github/workflows/quality.yml`(단계 분할·perf warn).
