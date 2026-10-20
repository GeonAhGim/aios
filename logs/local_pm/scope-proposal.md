# 파일 범위 분할 제안 — task-16647 (작성: task-16650)

## 배경

- task-16647 (`QA: [health:ci_red] CI no_bom 정정 — [no_bom] timeout 60s`)은
  `files: ["src/"]`로 배정됐고, `hold=true` / `hold_reason=local_file_scope` /
  `needs_split=true` 상태로 멈춰 있다.
- `src/` 전체를 가리키는 glob은 "한 리프 = 한 범위" 원칙에 맞지 않는다.
- 실제 원인 커밋(3129775e)은 `.github/workflows/quality.yml`의 CRLF→LF 줄바꿈 변경일 뿐,
  no_bom timeout 자체를 변경하지 않았다. timeout 문제는 기존에 존재하던 것이었다.
- spec: "해당 단계 스크립트를 로컬에서 실행해 녹색 증빙(수치)을 note에 남기고, 커밋 1개 push"
- spec 제약: "기준선/예산 상향, 규칙 완화, ignore 추가 금지(DECISION_GUIDELINES B-2)"

## 실제 영향 파일

task-16647의 실제 수정 대상은 `src/`가 아닌 다음 2개 파일이다:

| 파일 | 역할 |
|---|---|
| `scripts/check_no_bom.py` | BOM 스캔 스크립트. SCAN_WORKERS=16, shared ThreadPoolExecutor 적용됨 |
| `tests/unit/scripts/test_check_no_bom.py` | 해당 스크립트 단위 테스트 (182줄, 12 테스트) |

`.github/workflows/quality.yml`는 GitHub Actions 워크플로우로 `src/` 외부에 있으나,
No BOM 단계에 명시적 `timeout-minutes`가 없어 job 레벨 45분을 상속받는다. CI 에러
`[no_bom] timeout 60s`는 local_ci(py)의 단계별 timeout 설정에서 온 것으로 보이나,
`local_ci.py`는 `C:\aios\pm` 라이브 디렉터리에 있어 직접 수정 금지 대상이다.

## 분할안

task-16647은 **단일 파일**로 충분하다. `src/` glob을 쪼갤 필요가 없으며,
실제 영향 범위가 `scripts/check_no_bom.py` 하나에 국한된다:

| 분할 조각 | files | 근거 |
|---|---|---|
| no-bom-script | `scripts/check_no_bom.py`, `tests/unit/scripts/test_check_no_bom.py` | 스크립트 + 단위 테스트. timeout 문제를 진단하고 필요시 스크립트 내 상수/스레드 수 조정으로 해결 |

## 범위 밖 (이 task가 하지 않은 것)

- `C:\aios\pm\local_ci.py` 수정 — 라이브 pm 디렉터리, 직접 수정 금지
- `.github/workflows/quality.yml` 수정 — `src/` 외부, 그리고 spec이 "해당 단계
  스크립트"를 명시
- task-16647의 hold 해제, 재큐, status 변경 — 수행하지 않음
