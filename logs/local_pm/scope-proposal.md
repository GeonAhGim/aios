# 파일 범위 분할 제안: task-23113

## 분석

- 원본 files: `["src/", "type-ignore-budget.txt"]` — `src/`는 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 원본 spec(리뷰어 소견): 원인 커밋 `deacc374`는 `scripts/check_zone_manifest.py`만 변경, type_ignore와 무관.
- 실측(2026-11-10, origin/main 기준): `scripts/check_type_ignore_budget.py` → `OK: type: ignore 109개 (budget 109개 이내)`. 예산 `type-ignore-budget.txt` = 109. 게이트는 이미 녹색이며 timeout 사유도 재현되지 않음.
- `src/`의 `type: ignore` 실제 보유 파일은 5개뿐(총 8건):
  - `src/core/indicators/generate_specs.py` (4)
  - `src/exchanges/nh/websocket_mixin.py` (1)
  - `src/exchanges/kis/websocket_connection.py` (1)
  - `src/exchanges/kis/generated/_protocols.py` (1)
  - `src/exchanges/common/http_client.py` (1)
  (나머지 합계는 tests/scripts 쪽이다.)

## 제안 (파일별 분할)

수정할 코드가 확인되지 않으므로 코드 분할은 불필요하다. 권고:

1. **task-23113 폐기(또는 noop done)** — 근거: 게이트 녹색(109/109), 원인 커밋 무관. 재발행 중단.
2. 굳이 유지한다면 files를 아래로 한정:
   - `type-ignore-budget.txt` (예산 값 확인/동기화만)
   - 필요 시 위 5개 `src/` 파일 중 실제로 제거할 파일만 개별 지정(한 task당 1~2개).
3. 간헐적 timeout이 실제 문제라면 별도 ops task로 `scripts/check_type_ignore_budget.py` 실행시간 계측(현재 약 2초)을 요청.

원본 hold 해제·재큐·코드 수정은 하지 않았다.
