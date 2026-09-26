# AIOS 불변조건 카탈로그 (I-01 ~ I-12)

출처: `docs/research/brainstorm_2026-09-03/AIOS_Registers_v1_Assumption_Contradiction_Invariant_Failure_2026-09-03.md` §3
(Codex·Fable·ChatGPT 교차검증, 코드 레벨 근거 포함). 채택: ADR-2026-09-04-C.
이 문서는 **모든 리프의 DoD와 QA·리뷰 체크리스트에 포함**된다. 위반은 리뷰 REJECT 사유다.

| ID | 불변조건 | 강제 지점 |
|---|---|---|
| I-01 | 주문 제출·승인 경로의 어떤 생성자도 안전 게이트 인자를 `Optional`/`None` 기본값으로 받지 않는다 | EO-03/EO-06 정적 검사(CI) |
| I-02 | 다중 프로세스가 읽는 실행-소유권 상태는 lease/fencing token을 가지며 소유자 변경 시에만 증가, 갱신 실패 시 즉시 로컬 중지 | EO-01~04 |
| I-03 | 멱등키는 (tenant, actor, route, content-hash) 4중 스코프, 같은 키·다른 payload = 409 | `src/api/contracts/idempotency.py` (PLT-14 완료) |
| I-04 | 전략 아티팩트는 버전 부여 시 콘텐츠 해시로 주소화·불변, DB 권한/트리거로 강제 | L4 strategy §3.0, MP-3 |
| I-05 | 백테스트와 라이브는 같은 컴파일 산출물·같은 도메인 로직(주문/포지션/비용)을 공유 | DSL-11, BT-1~9, ADR-B D4 |
| I-06 | 외부 AI/에이전트 capability는 인간 세션과 분리된 닫힌 스코프 enum·서버측 즉시 revoke 토큰으로만, 자기 권한 API 도달 불가 | (Agent Gateway 명세 시) |
| I-07 | 검증/승인 게이트의 hard-fail 조건은 도메인 코드가 계산하고 실제로 FAIL을 반환할 수 있어야 한다 | L36-a(F-04), L42 |
| I-08 | MCP/도구 서버는 REST/도메인 계층 이상의 인가·비즈니스 로직을 갖지 않는다 | (Agent Gateway 명세 시) |
| I-09 | 주문 최종 ALLOW/DENY는 **두 개의 독립 권위**(RiskEngine 합성점 ∩ Compliance 번들 평가)를 모두 통과해야 하며, 각각 조회 증거(`risk_decision_id`·`compliance_decision_id`)를 남긴다 (2026-09-06 ADR-B D4 반영 — 이전 "mandate ∩ RiskEngine 단일 합성점" 표현은 폐기) | R-16/R-35/R-36(리스크 합성), CM-3/CM-8(컴플라이언스), OMS §3.1-a submit_order 배선 |
| I-10 | "구현됨 ≠ 작동함": 안전/정책 컴포넌트는 배선 증명 테스트(정적 검사 또는 적대적 통합)가 있어야 완료 | 모든 리프 DoD, QA 프롬프트 |
| I-11 | 확인이 필요한 작업은 1회성 서버측 토큰으로 미리보기와 실행을 연결한다(클라이언트 플래그만으로 불가) | 승인 워크플로·Agent Gateway |
| I-12 | AI/에이전트는 LIVE 주문 경로에 도달할 수 없고, PAPER 승격·실행은 confirm ticket 필수(2026-09-26 ADR-2026-09-26-B Decision 3, AIS-1 신설) | `tests/adversarial/assistant/test_no_execution_access.py`(FORBIDDEN_MODULE_PREFIXES 임포트 그래프 정적 검사 + confirm ticket 없이 PAPER 승격 시도 시 거부 확인) |

**실패 시나리오 카탈로그(F-01~F-12)**는 같은 레지스터 §4에 있다. QA는 해당 영역 리프에서 관련 F 항목을
적대적 테스트로 재현·차단해야 한다. I-12는 기존 F-01~F-12 중 어느 것과도 직접 대응하지 않는다 — AI/에이전트
분리는 그 레지스터가 작성된 2026-09-03 당시 존재하지 않았던 경로(AI factory PAPER 승격)이므로, 새 F-xx
항목 없이 I-12 자신의 강제 지점(위 표)이 유일한 재현·차단 지점이다(ADR-2026-09-26-B Decision 3 실측 근거).
