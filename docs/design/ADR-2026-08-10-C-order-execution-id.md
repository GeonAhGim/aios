# ADR-2026-08-10-C: Order/Position 모델에 execution_id 필드 추가

## Status
Accepted (2026-08-10, "0번부터 재검토" 라운드에서 소급 기록)

## Context
FD-16(전략 실행 제어판) 신설로 `strategy_executions` 테이블이 생겼고, 04번
DB스키마에는 이미 `ALTER TABLE orders/positions ADD COLUMN execution_id
BIGINT REFERENCES strategy_executions(id)`를 반영했다. 이에 대응해 01번
데이터모델(Pydantic `Order`/`Position` 클래스)에도 `execution_id: Optional[int]`
필드를 추가하는 패치(`patch-01-data-models-execution-id.md`)를 만들었으나,
**공유접점문서(`AIOS_DevEngine_공유접점문서.md`) §4 변경관리 프로토콜을
거치지 않고 진행했다** — §2.3(Order State Machine, 개발명세서 §1.4 기준)이
동결 계약 대상으로 지정한 스키마의 필드 확장인데도 ADR 없이 이뤄진 것을
"0번부터 재검토" 라운드에서 발견해 소급 기록한다.

## Decision
`Order`, `Position` 모델에 `execution_id: Optional[int] = None` 필드를
추가한다(01번 데이터모델, `patch-01-data-models-execution-id.md` 내용
그대로 확정).

## Scope 확인 — 동결 계약 위반이 아님을 명시
공유접점문서 §2.3이 동결하는 것은 **Order State Machine의 상태 전이**
(`CREATED → VALIDATED → SUBMITTED → ... → FILLED`, 예외 상태 포함)이지
`Order` 객체의 전체 필드 목록이 아니다. `execution_id` 추가는:
- 상태 전이 규칙을 전혀 변경하지 않음
- 기존 필드를 제거·이름변경하지 않음(순수 추가, `Optional`이라 기존 코드
  호환성도 유지)
- DevEngine이 참조하는 핵심 계약(state machine 전이 로직)에 영향 없음

따라서 이 변경은 "계약 파괴"가 아니라 "계약의 하위 호환 확장"이며,
공유접점문서 §4의 "양쪽 프로젝트 동시 재업로드" 요건만 충족하면 된다 —
Zone 재분류나 State Machine 재정의 수준의 중대 변경은 아니다. 다만
**절차 자체(ADR 선기록)를 생략한 것은 원칙 위반**이므로 이번에 소급
보완한다.

## Rejected Alternatives
- **execution_id를 별도 매핑 테이블로 분리**(`order_executions` 등):
  FD-16.4(실행 모니터링)의 조인이 매 쿼리마다 추가 JOIN을 필요로 하게 돼
  불필요한 복잡도 증가 — 기각.
- **string 형태의 복합키로 대체**: `strategy_executions.id`가 이미
  BIGSERIAL로 확정돼 있어(04번) 정수 FK가 가장 단순 — 기각.

## Impact
- 01번 데이터모델(Pydantic) 갱신: `patch-01-data-models-execution-id.md`
  적용.
- 04번 DB스키마: 이미 반영됨(변경 없음).
- **공유접점문서 갱신 필요**: §2.3에 "Order/Position은 FD-16(2026-08-10
  신설) 이후 `execution_id`(Optional) 필드를 포함한다"는 각주 추가 권장 —
  이 ADR과 함께 양쪽 프로젝트(AIOS/DevEngine) 지식 저장소에 동시 반영해야
  공유접점문서 §4 절차가 완료된다(현재 이 ADR 자체는 AIOS 프로젝트에만
  기록됨 — DevEngine 프로젝트 측에도 이 문서를 전달해야 함).

## References
- FD-16(기능설계문서 v1.16), 04번 DB스키마 execution_id ALTER문
- `AIOS_DevEngine_공유접점문서.md` §2.3, §4
- `patch-01-data-models-execution-id.md`
