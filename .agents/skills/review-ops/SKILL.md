---
name: review-ops
description: 자동화 스크립트·워크플로 — 라이브 파일 직접 편집 금지, task 파일 원자적 갱신, 테스트 격리, 새 게이트 도입 절차 축 검토 체크리스트
paths:
  - "scripts/**"
  - ".github/workflows/**"
  - "tests/unit/scripts/**"
tier: [L]
axis: ops
---

# review-ops

## 적용 조건

로컬 CI 게이트 스크립트(`scripts/check_*.py`, `scripts/*_ratchet.*`), GitHub Actions
워크플로(`.github/workflows/`), 오케스트레이션/자동화 스크립트를 건드릴 때 적용한다. 이
저장소 안에서 fleet 제어영역(pm 저장소) 코드를 직접 다루지는 않지만, 같은 계열의 사고
패턴(라이브 상태 직접 편집, task/상태 파일 손상, 전체 스위트 오남용)이 이 저장소의
`scripts/`·CI 자동화에도 그대로 적용된다.

## 체크리스트

1. 새 검사 스크립트를 로컬 CI(`ci_recheck.build_steps`, `.github/workflows/quality.yml`류)에
   연결할 때 곧바로 게이트(실패=적색)로 켜지 않는다 — main이 이미 위반 상태면 CI가 그
   자리에서 고착된다(supply_chain/compliance_gate/position_key_central 선례)
   [근거: OPS-42] [검사: 후보]
2. 새 게이트 도입 시 baseline 파일(`*-baseline.json`, `*-baseline.txt`류)이 함께 커밋되어
   기존 위반을 인정하고 신규 위반만 잡는다(래칫 방식) — baseline 없이 전면 강제하지 않는다
   [근거: OPS-42] [검사: scripts/check_code_ratchets.py]
3. 상태 파일(JSON)은 손으로 편집하지 않고 `json.dump`류 직렬화 경로로만 갱신한다 — 자유
   텍스트 필드(note 등)에 따옴표·줄바꿈이 그대로 들어가 JSON 파싱이 깨지는 경로가 없다
   [근거: 11_implementation_rules_v1.2.md] [검사: 후보]
4. 공유 작업트리/저장소에 대한 쓰기(`git add`, `git commit`)가 항상 경로를 명시한다
   (`git commit -F - -- <경로>`) — 경로 없는 커밋이 다른 세션이 스테이징해 둔 파일까지
   함께 실어가지 않는다 [근거: TESTING.md] [검사: 후보]
5. 자동화 스크립트가 실행 중인 세션(`AIOS_TASK_ID`/`AIOS_WORKTREE` 등 워커 컨텍스트) 안에서
   재귀적으로 자기 자신(오케스트레이터류)을 기동하지 않는다
   [근거: c593b7cd] [검사: 후보]
6. env 플래그/bash 패턴 가드로 라이브 상태 직접 쓰기를 차단하는 로직에 예외 경로를 추가할
   때, 그 예외가 특정 안전한 호출 패턴(예: `python -c` task-report 호출)으로 정확히
   스코프되어 있다 — 예외가 넓어 원래 막으려던 경로까지 함께 열리지 않는다
   [근거: d509050a] [검사: 후보]
7. healthcheck/오탐 판정 로직(예: 오배정 탐지)이 정상적인 정정 케이스(title 동일·role만
   변경 등)를 오탐하지 않는다 — 새 판정 규칙을 추가하면 그 규칙의 회귀 테스트가 있다
   [근거: 8ec38848] [검사: 후보]
8. 스크립트가 전체 테스트 스위트(`pytest tests/`)를 자동으로 반복 실행하지 않는다 —
   영향받은 파일/디렉터리로 범위를 좁힌 실행 경로를 기본으로 한다(턴/시간 예산 소진 방지,
   공유 TEST_DATABASE_URL 오염으로 실행마다 결과가 달라진 선례) [근거: OPS-29]
   [검사: 후보]
9. DB 연결이 필요한 검사는 CI worktree에 DB가 없을 때도 동작하도록(정적 AST 분석 등)
   설계되어 있거나, 명시적으로 DB 필요를 선언하고 없으면 스킵한다(무음 실패가 아니다)
   [근거: scripts/check_migration_chain.py] [검사: 후보]
10. 여러 세션이 동시에 실행되는 스크립트는 자기 것이 아닌 stash/브랜치/task 파일을 건드릴
    가능성이 있는 명령(`git stash pop`, 인덱스 전체 add)을 쓰지 않는다
    [근거: TESTING.md] [검사: 후보]
11. 코드 언어 래칫(주석/문자열이 영어여야 하는 파일)이나 type-ignore 예산 같은 품질
    래칫은 새 위반이 baseline보다 늘어나면 실패하되, 기존 위반을 이번 변경과 무관하게
    한꺼번에 강제하지 않는다 [근거: scripts/check_code_ratchets.py] [검사: scripts/check_code_ratchets.py]
12. CI 워크플로 변경이 시크릿 노출 없이 이뤄진다 — `.env`, 자격증명 파일을 워크플로 로그에
    echo하거나 아티팩트로 업로드하지 않는다 [근거: .gitleaks.toml] [검사: 후보]

## 반례

### 반례 1 — 경로 없는 커밋으로 다른 세션의 스테이징 파일까지 실림

```bash
# BAD: 인덱스에 다른 세션이 add해 둔 파일이 섞여 있어도 전부 커밋된다.
git commit -m "fix: my change"

# GOOD: 자기 파일 경로를 명시해 그 파일들만 커밋한다.
git commit -F - -- src/services/oms/domain/state_machine.py <<'EOF'
fix(oms): state_machine 전이 조건 보정
EOF
```

### 반례 2 — 새 게이트를 baseline 없이 즉시 강제

```python
# BAD: main이 이미 위반 상태인 새 검사를 곧바로 gate(실패=적색)로 등록한다.
STEP_MODE = {"new_check": "gate"}

# GOOD: warn으로 등록하고 baseline을 함께 커밋한 뒤, 위반이 0이 된 시점에 gate로 승격.
STEP_MODE = {"new_check": "warn"}
# warn_baselines/new_check.json 에 현재 위반 목록을 스냅샷으로 남긴다.
```

## 이 축에서 났던 사고

- `AIOS_TASK_ID`/`AIOS_WORKTREE`가 설정된 워커 세션 안에서 오케스트레이터가 재귀적으로
  기동되지 않도록 시작 시점에 거부하는 가드를 추가 (git:c593b7cd)
- 라이브 상태 직접 쓰기를 막는 bash 패턴 가드가 `python -c` task-report 호출까지
  막아버려 정상 보고 경로가 차단되던 문제를 정확히 스코프된 예외로 수정 (git:d509050a)
- `task_clobbered` 오탐 판정이 title 동일·role만 정정된 정상 케이스를 오배정으로
  잘못 플래그하던 결함을 수정 (git:8ec38848)
- 결정론적 훅(포맷 온 에딧, bash 패턴 가드, precommit 요약, 라이브-pm 쓰기 차단)을
  도입해 위 사고들의 재발 방지 기반을 마련 (git:5ceebd89)
- 전체 스위트(`pytest tests/`)가 공유 `TEST_DATABASE_URL` 오염으로 실행마다 다른 결과를
  내던 3300초대 적색을 원인 정리로 해소 — 전체 스위트를 습관적으로 돌리면 재현 안 되는
  적색까지 쫓게 된다는 근거 (git:677b76f8)
- 경로 없는 `git commit`이 다른 세션이 스테이징해 둔 paper_control 파일 10개를 함께
  실었던 사고 (git:a77e9e4)
