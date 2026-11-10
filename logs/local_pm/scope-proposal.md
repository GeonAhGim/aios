# task-23246 파일 범위 분할 제안

## 개요
task-23246 spec: "Fix review findings: consistency 24h 5회 반복 — 단계 규칙/기준선 설계 결함 조사"
현재 실패: `openapi_client_mismatch` 34→35 증가 (단일 게이트)

## 실제 영향 파일

### 1. `scripts/consistency/contracts.py` (주요 수정)
- `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST` 딕셔너리에 19개 OpenAPI 전용 백엔드 경로 추가
- 19개 경로 모두 `check_openapi_frontend()`에서 allowlist 누락으로 오탐
- 예: `/executions/{execution_id}/decision-trail`, `/risk-coach/position-size`, `/v1/accounts/summary` 등

### 2. `frontend/packages/api-client/src/apiRoutes.ts` (선택적 수정)
- 16개 프론트엔드 전용 경로가 OpenAPI에 없음
- 이 경로들이 실제 백엔드 라우터에 매핑되어 있다면 `contracts/openapi/v1.json`에 추가 필요
- 아니면 프론트엔드 전용 경로이므로 allowlist에 추가 가능

### 3. `contracts/openapi/v1.json` (선택적 수정)
- 16개 프론트엔드 전용 경로를 OpenAPI에 추가하거나, 프론트엔드 allowlist에 추가

## 분할안

### 단일 파일 작업 권장 (추천)
task-23246의 실제 변경 범위는 **`scripts/consistency/contracts.py` 1파일**의 allowlist 보강에 집중됨.
16개 프론트엔드 경로는 백엔드 라우터에 존재하면 OpenAPI 추가, 존재하지 않으면 프론트엔드 allowlist 추가.
모두 `contracts.py`의 `_OPENAPI_NO_FRONTEND_UI_ALLOWLIST`에 추가하는 것으로 해결 가능.

### 분할안 (필요시)
| 작업 | 파일 | 설명 |
|------|------|------|
| A | `scripts/consistency/contracts.py` | 19개 OpenAPI 전용 경로 allowlist 추가 |
| B | `scripts/consistency/contracts.py` | 16개 프론트엔드 전용 경로 allowlist 추가 |
| C | `contracts/openapi/v1.json` | 프론트엔드 전용 경로가 실제 API라면 추가 (선택) |

**A+B는 같은 파일이므로 단일 커밋 권장. C는 B와 독립적.**

## 결론
- **권장: 단일 파일 `scripts/consistency/contracts.py` 수정 1건**
- allowlist 추가만 하면 35개→0개 해결 (기존 baseline 34는 유지)
- 프론트엔드 경로 16개도 allowlist에 추가하면 해결
- `src/` 전체 범위는 불필요하게 넓음 — 실제 변경은 contracts.py 1파일
