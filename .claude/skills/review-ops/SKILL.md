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
5. 이 저장소의 운영 스크립트가 fleet(pm) 저장소에 이미 있는 책임(예: 공유 Postgres
   서버의 세션별 활성 DB 정리)을 다른 안전 가정으로 중복 구현하지 않는다 — 같은 책임의
   로직은 한 곳에만 있어야 다른 세션이 쓰던 DB를 엉뚱한 스크립트가 지우는 사고가 없다
   [근거: 6926609d] [검사: 후보]
6. 검사 스크립트의 화이트리스트/allowlist 예외가 정확히 스코프되어 있다(경로·사유가
   근거와 함께 명시) — 예외가 넓어 원래 막으려던 유령 경로까지 함께 삼키지 않는지
   확인하는 negative 테스트가 있다 [근거: ccf4dcc7] [검사: 후보]
7. healthcheck/정적 경로 오탐 판정 로직이 정상 케이스를 오탐하지 않는다 — 오탐 수정은
   검사를 완화하는 게 아니라 참조 경로를 실제 배치와 맞추는 방식이어야 한다
   [근거: 67abd236] [검사: 후보]
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

- 이 저장소의 `scripts/cleanup_orphan_test_dbs.py`·`scripts/nightly_pipeline.py`가
  fleet(pm) 저장소의 DB 정리 로직을 다른(더 좁은) 활성 세션 판정으로 중복 구현해,
  다른 세션이 쓰던 `aios_test_ci`를 orphan으로 오판해 DROP — local_ci prepare가
  `InvalidCatalogNameError`로 적색이 되어 두 파일을 삭제(원본은 fleet 쪽에만
  존재) (git:6926609d)
- `check_consistency.py`의 openapi 화이트리스트가 실제로는 등록된 15개 경로를
  유령 경로로 오탐 — 프론트 스캐너와 동일한 규칙·근거로 allowlist를 정확히
  스코프해 해소, 다른 경로까지 삼키지 않는 negative 테스트 추가 (git:ccf4dcc7)
- `closeout_check`의 H-2/3/4/6/8/10이 검사가 참조하는 경로가 실제 배치와
  어긋나 있던 오탐이었다 — 검사를 완화하지 않고 참조 경로만 실제와 맞춰 정정
  (git:67abd236)
- `setup_test_db.py`의 advisory lock이 CREATE 구간만 감싸고 이후 migrate 호출은
  락 밖에서 실행되어, 그 창에서 동시 `--reset` 호출이 방금 만든 DB를 다시 DROP —
  락을 쥔 채로 migrate까지 마치도록 수정해 재발을 막음 (git:8f30a00d)
- 전체 스위트(`pytest tests/`)가 공유 `TEST_DATABASE_URL` 오염으로 실행마다 다른 결과를
  내던 3300초대 적색을 원인 정리로 해소 — 전체 스위트를 습관적으로 돌리면 재현 안 되는
  적색까지 쫓게 된다는 근거 (git:677b76f8)
- 경로 없는 `git commit`이 다른 세션이 스테이징해 둔 paper_control 파일 10개를 함께
  실었던 사고 (git:a77e9e4)
