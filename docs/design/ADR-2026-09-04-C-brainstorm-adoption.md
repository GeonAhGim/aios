# ADR-2026-09-04-C: aios-brainstorm 연구 산출물의 채택·보류 결정

## Status
Accepted (2026-09-04, Chief Architect). 사용자 지시 "aios-brainstorm 내용도 확인해 쓸만한 것 고민".

## Context
`C:\aios\brainstorm`(Codex 광역조사 3종 + Fable 코드 레벨 검증 v2/v3/v4 + 레지스터 v1 + Target Architecture
Freeze v0.1 + 연구 결정 기록)을 전부 읽고 2026-09-04 현재 코드와 대조했다. 원본은
`docs/research/brainstorm_2026-09-03/`로 이관했다. 당시 결정 기록은 "프로덕션 구현 보류(연구 우선)"였으나,
지금은 Orchestrator·Guard·PM 조직이 가동 중이라 "이미 있는 코드를 배선한다"는 Tier 0 항목을 미룰 이유가 없다.

**아직 사실인 결함(2026-09-04 코드 재확인)**
- F-01 실행 소유권/lease 없음 — `scheduler.list_runnable`에 소유권 조건 없음, 마이그레이션 없음.
- F-02/F-03 `ExecutionLoopScheduler(...)`가 `pre_submit_gate`·`distrust_monitor` 없이 생성됨
  (`src/services/background_loops.py:212`) → kill switch·DataDistrust가 실행 루프에서 무효.
- F-04 `src/foundation/validation/application/start_validation.py:180` `hard_fail_reasons=()` 상수 → 검증 FAIL 불가.
- F-05 `src/contracts/enterprise.py` StrategyPackage가 라우터·테이블 어디에도 배선되지 않음.

**이미 해소된 것**: I-03 멱등 4중 스코프(PLT-14), OMS 도메인·outbox 펜싱(L4-01~09, task-150), 거래소 잔고 대사
(LB-6/LB-16), DataDistrust 도메인(R-48, 단 배선은 F-03로 미해결).

## Decision
1. **채택·즉시 착수(P0)**: `L4_execution_ownership_and_safety_gate_wiring_v1.0.md`를 Approved로 올리고 EO-01~06을
   모든 리프보다 앞에 배정한다(F-01/F-02/F-03 해소, I-01/I-02/I-10 강제). EO-03/EO-04의 FROZEN_PAPER_ONLY 접점은
   이 ADR로 사전 승인한다. F-04는 단독 리프로 즉시 수정한다.
2. **채택·명세 편입**: 불변조건 I-01~I-11을 `docs/design/INVARIANTS.md`로 고정하고 QA·리뷰 프롬프트 체크리스트에
   넣는다. in-toto Statement/Predicate 데이터 모델(서명은 AIOS KeyRing, 공개 Rekor 미사용)을 L4 strategy §3.0
   아티팩트 해시의 봉투로 채택하고 MP-3(버전·해시)와 함께 구현한다. Freqtrade식 lookahead/recursive 자동 검출은
   DSL-5·BT-9에 이미 반영돼 있다. LEAN `IBrokerageModel`(제약+비용모델 번들)은 BT-2~6 체결 모델 계약의 형태로
   채택한다. QuantDinger의 notional 예약·6단 런타임 게이트는 LIVE 개통 시 내곽 벽(레지스터 C-04 결정)으로
   R 리프에 반영한다.
3. **채택·후속 명세(지금 착수 안 함)**: Agent Gateway Plane(QuantDinger 스코프 enum·opaque 토큰, I-06/I-08/I-11)은
   ADR-B(TradingView 수준 상향)와 R/L4 잔여가 끝난 뒤 별도 L4 명세로 작성한다. Strategy Factory(NL → 스키마 강제 IR)는
   AIOS Script(DSL)가 IR 역할을 하므로 DSL 완료 후 "NL → AIOS Script 생성" 리프로 축소 편입한다.
4. **보류(패턴만 차용)**: Temporal·OPA·Sigstore 도구·gVisor/Firecracker — v3 검증 결론 그대로. 12-plane Freeze v0.1은
   참고 문서로만 두고 plane 이름을 코드 구조에 도입하지 않는다(현 hexagonal 컨텍스트 구조가 같은 경계를 이미 가짐).
5. **기각**: Phase 2 15종 중 v4가 REJECTED로 마감한 저장소의 재조사, Freqtrade/Lumibot(GPL) 코드 차용, Pine 호환 실행기.

## Consequences
- 최우선 순서: EO-01~06 + F-04 → DC → CH∥IND → DSL → BT → MP → R/L4 잔여(단 R 중 Policy 합성 I-09는 EO 직후) → SIG.
- QA 프롬프트에 INVARIANTS 체크와 F-카탈로그 재현 의무를 추가한다.
- 연구 트랙(brainstorm)은 계속 `C:\aios\brainstorm`에서 진행하되, 채택 여부는 이 ADR과 같은 형식으로 기록한다.
