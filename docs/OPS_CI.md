# OPS_CI

로컬 CI(`pm/local_ci.py`, aios-meta의 push 가드)와 GitHub Actions(`.github/workflows/quality.yml`)가
공유하는 게이트 스크립트(`scripts/check_*.py`, `scripts/*_ratchet.py`)의 운영 메모. 이 파일 자체는
워커가 아니라 ops/PM이 로컬 CI·push 가드 배선을 갱신할 때 참조한다(§CLAUDE.md "라이브 클론 편집
금지" — `pm/`, `aios-meta`는 이 저장소에서 직접 고치지 않는다).

## code-language 기준선 회귀 사고 (task-5303)

`code-language-baseline.txt`(`scripts/check_code_language.py`가 관리하는 래칫)가 09-22~09-23
사이 두 번 0으로 커밋됐다(4acc2620, c2770645/task-4328) — 둘 다 LANG-en 리프 워커가 스캐너를
거치지 않고 파일을 직접 덮어쓴 경우였다. baseline=0인 동안 모든 push가 저장소에 이미 존재하는
1만 줄 이상의 한글 주석·독스트링을 "초과분"으로 잡아 게이트가 영구 적색이 됐다.

### 근본 조치 (완화가 아니라 근절)

1. **검사기 하한 가드** (`scripts/check_code_language.py`) — 측정치가 0이거나 직전 baseline의
   50% 미만이면 baseline을 쓰지 않고 rc=2로 종료한다. baseline 갱신은 `--update-baseline`
   플래그가 있을 때만 일어난다 — 평범한 실행은 항상 보고만 하고 파일을 건드리지 않는다.
2. **push 가드** (`scripts/check_baseline_raise.py`, 신규) — 커밋된 `code-language-baseline.txt`
   값이 독립적으로 재측정한 실측 총계보다 작으면 push를 거부한다(rc=2). (1)의 가드는 스크립트가
   *실행돼 낮은 값을 쓸 때만* 발동하므로, 스크립트를 아예 거치지 않고 파일을 직접 편집한
   커밋(4acc2620/c2770645의 경로)은 (1)로 잡히지 않는다 — 이 가드가 그 구멍을 막는다.
3. **LANG-en 리프 명세** — baseline 파일은 검사기가 갱신한 값만 커밋한다. 직접 편집·직접 커밋
   금지.

### 새 게이트 도입 상태

`scripts/check_baseline_raise.py`는 이 리프에서 스크립트·테스트만 추가했고, 로컬
CI(`pm/local_ci.py`)나 `.github/workflows/quality.yml`에는 아직 배선하지 않았다(둘 다
`pm/`·aios-meta 소유 — 이 저장소 워커가 직접 고칠 수 없다). 배선 시 OPS-42 절차대로 먼저
`STEP_MODE=warn`으로 등록하고 baseline을 확인한 뒤 hard fail로 올린다.

### 기준선 복원 기록

2026-09-22 20:50 사용자 승인으로 0 → 실측 복원(1451e60c, 12520). 이후 재발해 다시 0이 됐던
것을 이 리프(task-5303)에서 클린 체크아웃 실측값(11326, 2026-09-23 기준 `src/` 스캔)으로
재복원했다. 통상적인 리프 워커는 이 문서의 절차를 우회해 baseline 숫자를 손으로 올리지 않는다
— 필요하면 PM에게 BASELINE 승인을 요청한다.
