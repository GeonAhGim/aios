# task-22842: DEEPEN 검증 복원

원본은 `b15746a8c^`의 91개 `tests/**/__init__.py`다. 삭제된 내용에
실제 부정·실패주입·성능·불변식 검증이 있어 삭제를 유지하지 않는다.
패키지 초기화 부작용 없이 pytest가 수집하도록 같은 디렉터리의
`test_restored_deepen.py` 89개로 복원했다.

이미 이동된 두 파일은 중복 복원하지 않았다.

- `tests/unit/api/middleware/__init__.py` → `test_context_deepen.py`
- `tests/unit/core/logging/__init__.py` → `test_boundary_regressions.py`

원본 테스트 737개 함수 중 위 두 파일의 8개를 제외한 729개를 보존했다.
매개변수 확장 후 복원 파일의 수집 건수는 761건이다. 이름이 바뀐 후속
테스트의 존재만으로 원본 검증이 대체됐다고 추정하지 않았다.

복원 시 현재 코드에 맞춘 수정:

- Python 3.13 import/형식 정리(Ruff).
- AST 스캔 상대 성능 측정은 연산·보정 모두 8회 배치해 Windows CPU 타이머
  해상도를 넘기며 기존 5배 예산을 유지.
- MFA 경계는 주입한 동일 시계로 비교하고 유효한 토큰은 미래 만료로 구성.
- break-glass 감사 실패 주입은 최대 TTL 경계 대신 10분 TTL로 실행.
- raw 주문을 만드는 EMS 테스트는 기존 일회용 DB 격리 픽스처 적용.
- follow의 컴플라이언스 예외 매핑 테스트는 선행 risk gate를 ALLOW로 고정.
- paper-control의 상태 변경에 tenant_id를 검증하고 reconciliation의
  멱등 안전 제어 진입점 `ensure_safety_control_active`에 실패를 주입.

RD-8/RD-17의 입력 오류는 AwareDatetime과 표준 400 봉투로 검증한다.
내부 ValueError는 원인을 숨기는 INTERNAL_ERROR(500)로 실패하며,
잘못된 요청으로 오분류하지 않는다. OpenAPI는 생성된 research 경로와
ApiError 정의를 반영했다. 로컬 Pydantic 버전 차이에서 생긴 다른 도메인의
Decimal pattern 변경은 이 작업에 포함하지 않았다.

FROZEN_PAPER_ONLY 구현은 변경하지 않았다. 복원 테스트는 기존 불변식의
실행 증거이며, 새로운 도메인 동작이나 안전 정책을 도입하지 않는다.
