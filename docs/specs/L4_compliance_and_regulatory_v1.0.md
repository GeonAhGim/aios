# L4 컴플라이언스 권위·규제 보고 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-06) — ADR-2026-09-06-B D4의 실행 명세
- owner role: Chief Architect(권위 경계 승인), PM(리프 배정)
- depends on: FA-1~6(엔티티 계층), R-01~45(리스크 권위), L4-09(주문 제출), EO-03~05(게이트 배선), L0-3(WORM)
- implemented by: `src/foundation/compliance/**`, `src/api/routers/compliance.py`, `frontend/apps/web/src/compliance/**`
- 리프 접두: **CM**
- **감사 2026-09-06 재정의 — 신설이 아니라 확장이다.** 저장소에 이미 있다: `src/foundation/mandates/`(위임장 개정 상태기계·냉각기간·
  개정 해시·`evaluate_policy`), `policy_bundle`·`policy_decision` 테이블(`d8e8e4ba2365`, outcome·expires_at·command_fingerprint),
  그리고 `src/services/order_service/foundation_gate.py::make_foundation_pre_submit_gate`가 **이미 리스크 fence/control과 mandate 정책을
  함께 평가**한다. 따라서 `src/foundation/compliance/` 신설 대신 **mandates 확장 + GateDecision에 `policy_decision_id` 추가 +
  세 조립 지점의 `require_mandate=True` 전환**으로 구현한다. 새 규칙(금지종목·집중도·유동성·공매도·자전거래·시장질서)은 mandates의
  규칙 집합에 파일 단위로 추가하고, "규칙 번들 거버넌스"는 mandate revision 승인 흐름을 그대로 쓴다.
- **권위 원칙**: 리스크와 컴플라이언스는 **분리된 두 권위**다. 주문은 `Risk.ALLOW ∧ Compliance.ALLOW`일 때만 나간다.
  둘의 규칙·승인 주체·감사 경로가 다르므로 코드에서도 합치지 않는다.

## 1. 기관급 요구
| 요구 | 내용 | 강제 지점 |
|---|---|---|
| 사전 통제 | 주문 제출 전 규정·위임장·금지종목 판정, DENY는 주문 생성 자체를 막는다 | CM-6~8 |
| 사후 감시 | 체결·포지션 기준 한도 이탈, 시장질서 교란 패턴 탐지 | CM-9~12 |
| 규칙 거버넌스 | 규칙은 버전이 찍힌 번들, 활성화는 승인 워크플로 경유(작성자≠승인자) | CM-4~5 |
| 설명 가능성 | 모든 판정에 근거 규칙 ID·입력 스냅샷·판정 시각을 남겨 사후 재현 | CM-3, CM-13 |
| 위임장(mandate) | 펀드별 투자 제한(자산군·국가·집중도·레버리지·ESG 배제)을 데이터로 관리 | CM-2 |
| 규제 보고 | 최선집행·거래 보고 산출물을 데이터로 생성(제출 어댑터는 포트) | CM-14~18 |

## 2. 모듈 분해 (파일 ≤300줄)
### 2.1 계약·규칙
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `ComplianceDecision{decision_id, verdict: ALLOW\|WARN\|DENY, rule_hits, inputs_hash, bundle_version, evaluated_at}`, `RuleHit{rule_id, severity, message, evidence}` |
| `contracts/mandate.py` | `Mandate{mandate_id, fund_id, constraints: tuple[Constraint,...], effective_from, approved_by}` — 자산군·국가·통화·집중도·레버리지·유동성·배제목록 |
| `domain/rules/{restricted_list,concentration,leverage,liquidity,exclusion,short_sale,wash_trade,position_limit}.py` | 규칙 1종=1파일(순수 함수). 입력은 스냅샷, 출력은 `RuleHit \| None` |
| `domain/rule_bundle.py` | 규칙 번들 구성·해시·버전(리스크 정책 번들과 동일 패턴) |
| `domain/evaluator.py` | 번들 평가기(결정론, 규칙 순서 무관, 최악 판정 채택) |
| `domain/market_abuse.py` | 시장질서 패턴(자전거래·종가 관여·호가 조작 의심) 사후 탐지 규칙 |

### 2.2 저장·응용
| 파일 | 책임 |
|---|---|
| `adapters/postgres_{decision,bundle,mandate}_repository.py` + 마이그레이션 | 결정 WORM 저장, 번들·위임장 버전 |
| `application/evaluate_pre_trade.py` | 주문 제출 경로에서 호출되는 유일한 사전 판정 진입 |
| `application/evaluate_post_trade.py` | 체결·일마감 배치 판정 |
| `application/{activate_bundle,approve_mandate}.py` | 승인 워크플로(작성자≠승인자, 기존 승인 시스템 재사용) |
| `application/explain.py` | 판정 재현(입력 스냅샷 + 번들 버전으로 동일 결과 증명) |
| `src/api/routers/compliance.py` | 판정 조회·번들·위임장 관리·예외 승인 |

### 2.3 규제 보고 — `src/foundation/compliance/reporting/`
| 파일 | 책임 |
|---|---|
| `domain/best_execution.py` | 최선집행 근거 산출(체결가 vs 벤치마크, 벤처 선택 사유) |
| `domain/trade_report.py` | 거래 보고 레코드 정규화(국내·해외 공통 필드) |
| `ports/report_submitter.py` | 제출 포트(어댑터는 MVP-2: 국내 보고·MiFID RTS 27/28) |
| `application/generate_report.py` | 기간별 보고서 생성(불변 저장 + 해시) |

## 3. 계약 (요지)
- **주문 경로 계약**: `submit_order`는 `risk_decision_id`와 `compliance_decision_id`를 **둘 다** 필수로 받는다(둘 중 하나라도 없으면 거부).
- 판정 입력은 스냅샷으로 고정(포지션·노출·위임장·금지목록 시점 값) → `inputs_hash`로 재현.
- `WARN`은 통과시키되 기록하고 대시보드에 노출한다. `DENY`는 fail-closed.
- **예외 승인**: 운영자가 특정 주문에 대해 사전 예외를 승인할 수 있으나, 1회성 서버측 토큰(I-11) + 사유 필수 + 별도 감사.
- 에러: `CM_DENIED`(403, rule_hits 포함), `CM_BUNDLE_INACTIVE`(409), `CM_MANDATE_MISSING`(409), `CM_EXCEPTION_REQUIRED`(428).

## 4. 불변조건
- **CM-A1** 컴플라이언스 판정 없이 주문이 제출되는 경로는 존재하지 않는다(정적 검사 + 적대적 테스트, I-10).
- **CM-A2** 규칙 평가는 순수·결정론이며 LLM·네트워크 호출을 포함하지 않는다(적대적 테스트로 증명, R-56과 동형).
- **CM-A3** 번들 활성화는 작성자와 다른 승인자를 요구한다.
- **CM-A4** 판정 레코드는 append-only이며 `explain()`이 언제나 같은 결과를 재현한다.
- **CM-A5** 위임장 제약을 위반하는 주문은 리스크가 ALLOW여도 통과하지 못한다(권위 분리 증명 테스트).

## 5. 동시성·멱등성
- 판정: `(order_intent_hash, bundle_version)` 멱등. 번들 활성화: 조건부 UPDATE(단일 활성). 사후 배치: 일자별 유일.

## 6. 실패 모드
| 실패 | 조치 |
|---|---|
| 번들 미활성 | 409 fail-closed(무규칙 통과 금지) |
| 금지목록 소스 지연 | 마지막 유효 목록 사용 + 신선도 경고, 임계 초과 시 DENY |
| 사후 위반 탐지 | 알림 + 해당 펀드 신규 주문 차단(운영자 해제) |
| 예외 남용 | 예외 발급 횟수 임계 초과 시 자동 차단 + 보고 |

## 7. SLO
- 사전 판정 p99 30ms(주문 경로 지연 예산의 일부), 사후 배치 ≤ 10분/일, 보고서 생성 ≤ 5분/월.

## 8. 테스트
- 적대적: 컴플라이언스 우회 경로 탐색, 리스크만 ALLOW인 주문, 번들 미활성 통과 시도, 예외 토큰 재사용, 판정 레코드 변조.
- 계약: 규칙별 경계값 표, `explain()` 재현 동일성, 위임장 위반 케이스 전수.

## 9. 리프 목록
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| CM-1 | 기존 `mandates/contracts/v1.py` 확장(`ComplianceDecision`은 기존 `policy_decision`에 매핑, 신규 테이블 금지) + 스냅샷 | FA-1 | 스키마, 기존 계약 호환 | 260 |
| CM-2 | 기존 `MandateRuleInput`에 제약 확장(자산군·국가·통화·유동성·ESG 배제) + `mandates/domain/rules/exclusion.py` + test | CM-1 | 제약 7종 표현, 경계값 | 460 |
| CM-3 | `domain/rule_bundle.py` + `domain/evaluator.py` + test | CM-1 | 순서 무관·최악 판정, 번들 해시 | 460 |
| CM-4 | 기존 `policy_bundle`·`policy_decision`에 부족분만 추가(WORM 트리거 확인 포함) + 어댑터 확장 + 통합 | CM-3 | append-only 증명 | 560 |
| CM-5 | 기존 `mandates/application/{activate_revision,propose_amendment}`를 규칙 번들 거버넌스로 승격(작성자≠승인자 강제 추가) | CM-4 | 작성자≠승인자 강제 | 400 |
| CM-6 | `domain/rules/{restricted_list,concentration}.py` + test | CM-3 | 정확값·경계 | 400 |
| CM-7 | `domain/rules/{leverage,liquidity,position_limit}.py` + test | CM-3 | 정확값·경계 | 460 |
| CM-8 | `application/evaluate_pre_trade.py` + **주문 경로 배선**(`submit_order` 시그니처 확장) + 적대적(우회 0) | CM-6, CM-7, L4-09 | CM-A1·A5 증명 | 460 |
| CM-9 | `domain/rules/{short_sale,wash_trade}.py` + test | CM-3 | 국내 공매도 규정 케이스 포함 | 400 |
| CM-10 | `domain/market_abuse.py` + test | CM-3 | 3패턴 탐지·오탐 경계 | 300 |
| CM-11 | `application/evaluate_post_trade.py` + 배치 스케줄 + 통합 | CM-9, CM-10 | 일마감 판정, 위반 시 차단 | 400 |
| CM-12 | 위반 알림·차단 해제 워크플로 + 통합 | CM-11 | 차단·해제 감사 | 300 |
| CM-13 | `application/explain.py` + 재현 테스트 | CM-4 | 동일 입력 → 동일 판정 | 260 |
| CM-14 | `reporting/domain/best_execution.py` + test | CM-4, EM-1 | 벤치마크 대비 산출 | 300 |
| CM-15 | `reporting/domain/trade_report.py` + test | CM-4 | 국내·해외 공통 필드 정규화 | 300 |
| CM-16 | `reporting/ports/report_submitter.py` + `application/generate_report.py` + 불변 저장 | CM-15 | 보고서 해시·재생성 동일 | 300 |
| CM-17 | `src/api/routers/compliance.py` + 통합·교차 테넌트 | CM-8, CM-11 | 404 동형, 예외 토큰 흐름 | 400 |
| CM-18 | 프론트 `CompliancePage.tsx`(판정 조회·규칙 히트·예외 승인) | CM-17 | 화면·negative | 300 |
| CM-19 | 프론트 `MandatePage.tsx`(위임장 편집·승인 흐름) | CM-17 | 화면·negative | 300 |
| CM-20 | 적대적 스위트 `tests/adversarial/compliance/*`(우회·변조·권위 분리) | CM-8, CM-13 | 전 케이스 차단 | 300 |

## 10. 미확정·리스크
- 국내 규제 세부(공매도 업틱룰·5% 대량보유 보고·시장질서 교란 기준)는 CM-9/CM-10 리프에서 **법령 원문을 인용해** 확정한다. 추측 구현 금지.
- 실제 규제 제출 어댑터(국내 보고 시스템·MiFID)는 MVP-2. MVP-1은 보고서 산출까지.
- PAPER 전용 단계에서는 규제 보고 의무가 없으나, LIVE 개통 전 필수 선행으로 둔다.

## 11. 진행 현황 (자동 생성)

<!-- spec-status:begin (auto-generated by C:/aios/pm/spec_status.py — do not edit) -->
갱신 2026-09-10T17:02:14+00:00 · 총 20 · done 13 · inflight 6 · untouched 0 · hold 1 · 재오픈 3 · 깊이(done) {'D2': 3, 'D3': 8, 'D?': 2}

| ID | 리프 | 상태 | done task | 열린 task | depth | commit |
|---|---|---|---|---|---|---|
| CM-1 | 기존 mandates/contracts/v1.py 확장(ComplianceDecision은 기존 policy_decision에 | done | 2105,2118,2854 |  | D3 | 2be7cca3 |
| CM-2 | 기존 MandateRuleInput에 제약 확장(자산군·국가·통화·유동성·ESG 배제) + mandates/domain/rul | done | 2036,2855 |  | D2 | 10468248 |
| CM-3 | domain/rule_bundle.py + domain/evaluator.py + test | done | 2065,2066,2856 |  | D3 | 70c9fe20 |
| CM-4 | 기존 policy_bundle·policy_decision에 부족분만 추가(WORM 트리거 확인 포함) + 어댑터 확장 + 통 | done | 2857,2860,3023 |  | D2 | c6242a80 |
| CM-5 | 기존 mandates/application/{activate_revision,propose_amendment}를 규칙 번들 거 | done | 2118,2853,2862 | 3192 |  | 70509c75 |
| CM-6 | domain/rules/{restricted_list,concentration}.py + test | done | 2065,2105,2858 |  | D3 | c0a3f971 |
| CM-7 | domain/rules/{leverage,liquidity,position_limit}.py + test | done | 2066,2105,2859 |  | D3 | b00478bf |
| CM-8 | application/evaluate_pre_trade.py + 주문 경로 배선(submit_order 시그니처 확장) + 적 | done | 2105,2121 | 3126 | D3 | f553385a |
| CM-9 | domain/rules/{short_sale,wash_trade}.py + test | done | 2458,2863 |  | D3 | 833ae08a |
| CM-10 | domain/market_abuse.py + test | done | 2460,2864 |  | D3 | 90b124a8 |
| CM-11 | application/evaluate_post_trade.py + 배치 스케줄 + 통합 | done | 2509,2865 | 2616 |  | 24dede91 |
| CM-12 | 위반 알림·차단 해제 워크플로 + 통합 | inflight |  | 2667 |  |  |
| CM-13 | application/explain.py + 재현 테스트 | done | 2510,2866 |  | D3 | fb3d580c |
| CM-14 | reporting/domain/best_execution.py + test | done | 2526,2867 |  | D2 | cd1a00de |
| CM-15 | reporting/domain/trade_report.py + test | inflight |  | 2617 |  |  |
| CM-16 | reporting/ports/report_submitter.py + application/generate_report.py + | inflight |  | 2618,2620,2668 |  |  |
| CM-17 | src/api/routers/compliance.py + 통합·교차 테넌트 | inflight |  | 2620 |  |  |
| CM-18 | 프론트 CompliancePage.tsx(판정 조회·규칙 히트·예외 승인) | inflight |  | 2668 |  |  |
| CM-19 | 프론트 MandatePage.tsx(위임장 편집·승인 흐름) | hold |  |  |  |  |
| CM-20 | 적대적 스위트 tests/adversarial/compliance/(우회·변조·권위 분리) | inflight |  | 2619 |  |  |
<!-- spec-status:end -->
