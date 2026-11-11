# 파일 범위 분할 제안: task-23334

## 분석

- 원본 files: `["scripts/check_consistency.py", "src/"]` — `src/`는 디렉터리라 파일 범위로 해석 불가(`unresolved_path_scope`).
- 원본 spec 요지: consistency 24h 5회 반복 조사. 부모 9083 커밋은 미확정. HEAD에서 `check_consistency.py`는 exit 0이고 `openapi_client_mismatch` 34건은 기준선 34와 같아 드리프트가 없다. 기준선을 완화한 곳도 없다.
- 실제로 남은 발견은 1건이다. 기준선이 감소해도 자동 갱신되지 않아 래칫 슬랙이 생긴다. `env_key_undocumented`는 `consistency-baseline.json`에 4로 남아 있으나 실측은 0이다.
- `scripts/check_consistency.py`는 167줄이다. 감소 시 기준선 유지는 의도된 동작이고 `--update`로 반영한다(155~159행). 구조 결함이 아니다.
- 따라서 `src/` 하위 수정이 필요한 실제 근거는 없다. 이 task는 코드 수정이 아니라 기준선 운영 문제다.

## 실제 변경 파일 (분할안)

| 순서 | 파일 | 내용 | 비고 |
|---|---|---|---|
| 1 | `consistency-baseline.json` | `python scripts/check_consistency.py --update`로 감소분만 반영(`env_key_undocumented` 4->0). 증가·완화는 금지. | 1파일 |
| 2 | `tests/unit/scripts/test_check_consistency.py` (경로는 `Glob`으로 확인 후 확정) | 감소 시 `--update` 없으면 유지, 있으면 갱신하는 음성/경계 테스트 1~2개 | 기존 테스트가 이미 있으면 생략 |

- `src/`는 범위에서 제외한다. `src/` 수정이 필요해 보이면 별도 task에서 구체 파일명으로 다시 제안한다.
- `scripts/check_consistency.py`와 `scripts/consistency/*.py`는 변경하지 않는다. 변경이 필요하면 `contracts.py`(351줄)나 `registry.py`(89줄) 중 해당 검사 그룹 파일 하나만 지정한다.
- 기준선 값은 올리지 않는다. 감소만 반영한다(baseline 규칙).

## 제약

원본 hold 해제, 재큐, 코드 수정은 하지 않았다. 이 파일은 제안서만 담는다.
