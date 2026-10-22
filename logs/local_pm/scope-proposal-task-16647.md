# 파일 범위 분할 제안: task-16647

## 원본 작업 분석

- **task-16647**: QA(묶음 1건) — CI no_bom 정정 — [no_bom] timeout 60s
- **원본 files**: `["src/"]` — 전체 소스 디렉토리 (너무 광범위)
- **문제**: CI no_bom 단계가 60s timeout 발생 (esc-ci-no_bom)
- **원인 커밋**: 3129775e18e43bb60fc2a3243c40cf235c9998d1 (LF line endings 복원)
- **현재 상태**: 게이트 녹색 (rc=0, 1.58s)

## 실제 게이트 대상 파일

### no_bom 게이트 (task-16647)

`scripts/check_no_bom.py`가 스캔하는 대상:
1. `src/` — 소스 디렉토리 전체 스캔 (읽기 전용)
2. `tests/` — 테스트 디렉토리 전체 스캔 (읽기 전용)
3. `scripts/` — 스크립트 디렉토리 전체 스캔 (읽기 전용)
4. `docs/` — 문서 디렉토리 전체 스캔 (읽기 전용)
5. `frontend/` 아래 각 패키지의 `src/` 디렉토리 (읽기 전용)

**실제 수정 대상 파일** (게이트 스크립트 + 테스트):
| 파일 | LOC | 설명 |
|------|-----|------|
| `scripts/check_no_bom.py` | 223 | BOM 유입 차단 게이트 스크립트 |
| `tests/unit/scripts/test_check_no_bom.py` | 181 | 게이트 단위 테스트 (15건) |
| `.github/workflows/quality.yml` | 120-121 | CI no_bom 단계 (읽기 전용 확인) |

## 분할안

task-16647은 **단일 리프**로 충분함. `files`를 다음 3개로 좁힘:

```json
["scripts/check_no_bom.py", "tests/unit/scripts/test_check_no_bom.py", ".github/workflows/quality.yml"]
```

### 이유

1. 게이트 스크립트(223줄)와 테스트(181줄)는 하나의 책임(BOM 감지)을 공유
2. `src/` 전체를 스캔하는 것은 읽기 전용 운영 — 게이트 자체를 수정하는 것이 아님
3. CI 워크플로우의 no_bom 단계(2줄)만 관련 — 전체 quality.yml 아님
4. 게이트 현재 녹색(rc=0, 1.58s) — 회귀 정정 완료 상태

## 권장

`files`를 `["src/"]` → `["scripts/check_no_bom.py", "tests/unit/scripts/test_check_no_bom.py", ".github/workflows/quality.yml"]`로 좁힌 후 QA 단계 재실행.
