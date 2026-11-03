# 파일 범위 분할 제안: task-21796

## 오배정 분석

### spec vs 실제 변경 파일 불일치

| 항목 | spec / 원본 files | 실제 변경 (commit 91454fc1d) |
|------|-------------------|------------------------------|
| files | `["frontend/"]` | `tests/foundation/unit/evidence/application/` |
| spec 제목 | "CI frontend 정정" | "test(evidence): add comprehensive tests for record_command_event" |
| 변경 파일 | 없음 (frontend/에서 해당 커밋 없음) | conftest.py(+68), test_record_command_event.py(+351), test_record_command_event_advanced.py(+276) |
| 총 추가 줄 | — | 695 |

### root cause

task-21796의 `files: ["frontend/"]`는 **오입력**이다. 원본 리뷰는 `tests/foundation/unit/evidence/application/`의 3파일을 대상으로 했지만, files 필드에 `frontend/`가 잘못 기록되었다. spec의 "CI frontend 정정" 제목도 이 오입력에서 비롯된 것으로 보인다.

### AccountDeletionPage.test.tsx와의 관계

- QA(21875)는 "spec은 frontend/AccountDeletionPage.test.tsx 언급"이라고 했으나, 실제 spec 텍스트에 이 파일명은 등장하지 않는다.
- `frontend/apps/web/src/routes/settings/AccountDeletionPage.test.tsx`는 현재 저장소에 존재하지만, **커밋 91454fc1d와 무관**하다. 이 파일의 마지막 관련 커밋은 `7bcc040bb` (hide details for unknown api errors)이다.
- QA의 "referenced commit 91454fc1d는 evidence/application 테스트 3파일 변경"이라는 지적이 정확하다.

## 실제 작업 범위

### 수정 대상 파일 (3개, 총 695줄)

| 파일 | 줄 수 | 책임 |
|------|-------|------|
| `tests/foundation/unit/evidence/application/conftest.py` | 68 | MockAuditEventRepository, make_audit_event 팩토리 |
| `tests/foundation/unit/evidence/application/test_record_command_event.py` | 351 | happy path + boundary tests (main) |
| `tests/foundation/unit/evidence/application/test_record_command_event_advanced.py` | 276 | failure injection + trace_id + enum coverage |

### DoD 누락 항목 (task-21796 spec의 지적)

1. **D2 perf 단언 없음**: 단위 테스트이므로 p95/p99 성능 단언은 적합하지 않음. 처리량 테스트는 integration/e2e 범위. → **N/A(단위 테스트)**
2. **negative 명시 2건으로 D2 최소 3 미달**: 현재 negative 2건 (repository error, unsafe payload). 3번째 negative 추가 필요.
3. **trace_id dedup 주장 미검증**: mock이 돌려준 trace_id 값을 비교할 뿐, 실제 dedup 로직이 호출되었는지 검증 없음.
4. **대부분 단언이 mock 반환값 동어반반복**: `assert event.payload == payload` 등 mock이 그대로 반환한 값을 확인하는 단언이 많음.

## 분할안

### 권장: 분할 불필요 (3파일, 695줄, 같은 도메인)

3개 파일 모두 `tests/foundation/unit/evidence/application/`에 속하며, 같은 `record_command_event` 함수를 테스트한다. 파일당 하나의 책임으로 명확히 구분되어 있음:

- `conftest.py` (68줄): 테스트 헬퍼 — 독립적, 수정 시 다른 파일에 영향 없음
- `test_record_command_event.py` (351줄): happy path — 테스트 클래스 1개
- `test_record_command_event_advanced.py` (276줄): failure/trace/enum — 테스트 클래스 2개

**분할 권장 사항**: 파일 분할보다는 **DoD 누락 항목 정정**을 우선한다.

### DoD 정정 작업 계획

1. **3번째 negative 테스트 추가**: `test_record_command_event_invalid_classification.py` 또는 기존 파일에 `test_record_command_event_none_aggregate_id` 추가
2. **trace_id dedup 검증 강화**: mock이 받은 trace_id가 실제 repository에 전달되었는지 `assert`로 확인
3. **동어반복 단언 개선**: mock 반환값 비교 대신, repository의 `last_call_kwargs`로 실제 인자 검증

## 원본 task-21796 재배정 시 files 수정 제안

```json
"files": [
  "tests/foundation/unit/evidence/application/conftest.py",
  "tests/foundation/unit/evidence/application/test_record_command_event.py",
  "tests/foundation/unit/evidence/application/test_record_command_event_advanced.py"
]
```

## 결론

- task-21796은 **오배정** (files: frontend/ → 실제: tests/foundation/unit/evidence/application/)
- 분할은 불필요 (3파일, 같은 도메인, 파일 경계가 명확)
- 우선순위: (1) files 필드 정정, (2) DoD 누락 항목 정정, (3) 게이트 녹색
- 원본 hold 해제·재큐·코드 수정은 금지 (task-21798 spec 준수)
