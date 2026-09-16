# ADR Index

이 문서는 `scripts/gen_adr_index.py`가 `docs/design/ADR-*.md`에서 자동 생성한다(ADR-2026-09-09-F). 직접 수정하지 말 것 — 스크립트를 다시 실행해 갱신하고 커밋한다.

전체 35건. 상태 없음(`## Status` 절 없음 또는 파싱 불가) 2건: ADR-2026-08-28, ADR-2026-08-29.

| ID | Title | Status | Date | Supersedes | Superseded By | Amended | File |
|---|---|---|---|---|---|---|---|
| ADR-2026-08-10 | 플랫폼 레벨 Critical Risk 승인 — 1인 체제 임시 운영 방식 | Superseded | 2026-08-14 |  | ADR-2026-08-10-D-platform-approval-finalized |  | ADR-2026-08-10-platform-approval-solo.md |
| ADR-2026-08-10-B | 기술 스택 확정 (백엔드·DB·인증·프론트엔드) | Accepted | 2026-08-10 |  |  |  | ADR-2026-08-10-B-tech-stack.md |
| ADR-2026-08-10-C | Order/Position 모델에 execution_id 필드 추가 | Accepted | 2026-08-10 |  |  |  | ADR-2026-08-10-C-order-execution-id.md |
| ADR-2026-08-10-D | 플랫폼 레벨 Critical Risk 승인 게이트 — 정식 확정 | Accepted | 2026-08-14 | ADR-2026-08-10 |  |  | ADR-2026-08-10-D-platform-approval-finalized.md |
| ADR-2026-08-28 | 다자산군(Multi-Asset-Class) 지원 확장 | **MISSING** |  |  |  |  | ADR-2026-08-28-multi-asset-class-expansion.md |
| ADR-2026-08-29 | 마켓플레이스 내부 크레딧 지갑 + 판매자 이원화 + 고차원 전략 생성 | **MISSING** |  |  |  |  | ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md |
| ADR-2026-09-03-A | public 모노레포 `GeonAhGim/aios`로 통합 | Accepted | 2026-09-03 |  |  |  | ADR-2026-09-03-A-public-monorepo.md |
| ADR-2026-09-03-B | Meta-Control Plane과 개발 조직(Orchestrator·헤드리스 worker·PM·Guard) | Accepted | 2026-09-03 |  |  |  | ADR-2026-09-03-B-meta-control-plane-and-dev-org.md |
| ADR-2026-09-04-A | 시장데이터 리플레이 성능 계약과 lineage 해시의 증분화 | Accepted | 2026-09-04 |  |  |  | ADR-2026-09-04-A-market-data-replay-perf.md |
| ADR-2026-09-04-B | 분석·전략작성·백테스트·마켓플레이스의 TradingView 수준 상향과 글로벌 데이터 커버리지 아키텍처 | Accepted | 2026-09-04 |  |  |  | ADR-2026-09-04-B-tradingview-parity-and-global-data-coverage.md |
| ADR-2026-09-04-C | aios-brainstorm 연구 산출물의 채택·보류 결정 | Accepted | 2026-09-04 |  |  |  | ADR-2026-09-04-C-brainstorm-adoption.md |
| ADR-2026-09-04-D | 1차 MVP = 최소 T3(프로 퀀트 프레임워크) 수준 — 범위 절단과 종료 기준 | Accepted | 2026-09-04 |  |  | 2026-09-04, 2026-09-06, 2026-09-09 | ADR-2026-09-04-D-mvp1-t3-scope.md |
| ADR-2026-09-05-A | 지표·전략작성·백테스트의 오픈소스 기반 대규모 확장과 AI 연구·전략 생성 계층 | Accepted | 2026-09-05 |  |  |  | ADR-2026-09-05-A-indicator-scale-and-ai-strategy-factory.md |
| ADR-2026-09-06-A | T1급 데이터 모델 선반영, 리서치 데이터 플레인(비가격 데이터) 개방, 데이터 벤더 선정 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-A-t1-data-model-research-plane-and-vendors.md |
| ADR-2026-09-06-B | T1을 넘어서는 목표 아키텍처 — 초도 완성도를 위한 공격적 결정 | Accepted | 2026-09-06 |  |  | 2026-09-09 | ADR-2026-09-06-B-beyond-t1-target-architecture.md |
| ADR-2026-09-06-C | 고밀도 차트 — 한 화면에 수십 개 지표를 실사용 가능한 속도로 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-C-high-density-charting.md |
| ADR-2026-09-06-D | 제품 경험·발견 기능 — 경쟁 우위 확보와 UX 리팩터링 차단 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-D-product-experience-and-discovery.md |
| ADR-2026-09-06-E | 전면 재검토 결과와 정정 — "게이트는 있는데 조립선이 끊겨 있다" | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-E-full-audit-corrections.md |
| ADR-2026-09-06-F | 차용 우선 — 손으로 쓰던 리프를 자동 생성·벤더 노출로 대체 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-F-adopt-dont-rewrite.md |
| ADR-2026-09-06-G | 2차 전면 감사 — 품질 조립선, 벤더 중복, 수준 격차 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-G-second-audit-corrections.md |
| ADR-2026-09-06-H | 데이터 조달 — 자체 구축 범위, 계약 등급 교체 가능성, 재배포 스코프 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md |
| ADR-2026-09-06-I | 연결축 최우선 — 국내 증권사·코인거래소 먼저, KIS API 전수 구현 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-I-broker-first-and-full-kis-coverage.md |
| ADR-2026-09-06-J | 무인 운영 모델 — 되돌릴 수 있는 것은 자동으로, 아닌 것만 사람에게 | Accepted | 2026-09-06 |  |  |  | ADR-2026-09-06-J-autonomous-operation-model.md |
| ADR-2026-09-07-A | 코드 주석은 영문, 문서는 한글 — 그리고 소급 변환은 하지 않는다 | Accepted | 2026-09-07 |  |  |  | ADR-2026-09-07-A-code-comment-language.md |
| ADR-2026-09-08-A | 대리 기간 거버넌스 추인과 운영 결정 6건 | Accepted | 2026-09-08 |  |  |  | ADR-2026-09-08-A-governance-ratification-and-ops-decisions.md |
| ADR-2026-09-08-B | GitHub Copilot 코딩 에이전트를 별도 실행 레인으로 붙인다 | Accepted | 2026-09-08 |  |  | 2026-09-10 | ADR-2026-09-08-B-copilot-coding-agent-lane.md |
| ADR-2026-09-09-A | IND-11 대상 패키지를 pandas-ta에서 pandas-ta-classic으로 교체 | Accepted | 2026-09-09 |  |  |  | ADR-2026-09-09-A-ind11-pandas-ta-classic.md |
| ADR-2026-09-09-B | MVP-1 하드닝 리프와 MVP-2 범위 | Accepted | 2026-09-09 |  |  |  | ADR-2026-09-09-B-mvp1-hardening-and-mvp2-scope.md |
| ADR-2026-09-09-C | T2 깊이 기준(D2)과 명세 커버리지 기계 검증 | Accepted | 2026-09-09 |  |  | 2026-09-10 | ADR-2026-09-09-C-t2-depth-bar-and-spec-coverage.md |
| ADR-2026-09-09-D | 변곡점 프로토콜과 누수 방지 — 마일스톤 종결·대규모 리팩터링·외부 변화를 기계가 먼저 잡는다 | Accepted | 2026-09-09 |  |  |  | ADR-2026-09-09-D-inflection-point-protocol.md |
| ADR-2026-09-09-E | 작업 효율 — 위험도별 검증 깊이, 영향 범위 테스트, 증분 정적 검사 | Accepted | 2026-09-09 |  |  |  | ADR-2026-09-09-E-work-efficiency.md |
| ADR-2026-09-09-F | 최종 목표 = 프런티어 1등급(F) — 등급 정의, 단계 경로, ADR 갱신 권한 | Accepted | 2026-09-09 |  |  |  | ADR-2026-09-09-F-frontier-roadmap.md |
| ADR-2026-09-10-A | 상황 인식 검토 — 검토 컨텍스트 MCP 서버 + 영역별 검토 스킬 + 발견 기억 | Accepted | 2026-09-10 |  |  |  | ADR-2026-09-10-A-context-aware-review.md |
| ADR-2026-09-10-B | 워커 효율 커넥터 — 저장소 CLAUDE.md·결정론 훅을 먼저, MCP는 측정하며 붙인다 | Accepted | 2026-09-10 |  |  |  | ADR-2026-09-10-B-efficiency-connectors.md |
| ADR-2026-09-10-C | 개발정책 3단계 전환 — 파일 길이에서 도메인 응집·불변식 지역성으로, 변경 거버넌스는 closeout 시점에 | Accepted | 2026-09-10 |  |  |  | ADR-2026-09-10-C-development-policy-phase3.md |
