# ADR-2026-09-09-B: MVP-1 하드닝 리프와 MVP-2 범위

## Status
Accepted (2026-09-09, Chief Architect, 사용자 전권 위임). ADR-2026-09-04-D(MVP-1 범위·종료조건)와 ADR-2026-09-06-B("포트만 지금, 구현은 MVP-2")를 잇는다.

## Context
2026-09-09 기준 backend 구현 리프 486건 전부 완료, QA 221건 대기. 세 갈래 독립 감사(백엔드 깊이·제품층·엔지니어링 품질)를 돌려
"높은 수준" 기준으로 미비점 35건을 찾았다. 강점(Decimal 규율, WORM·outbox·멱등성·kill switch 실배선, 실제 DB 픽스처 테스트,
DSL 리소스 상한·결정론 강제, LOD 렌더링·리플레이)은 그대로 두고, 미비점을 두 묶음으로 나눈다.

- **A. MVP-1 하드닝(H-)**: 라이브 자금 개통(HB-5) 전에 반드시 닫아야 하는 것. MVP-1 종료조건 11항으로 추가한다.
- **B. MVP-2(M2-)**: 경쟁 제품 대비 격차. QA 대기 소진 후 착수.

## Decision A — MVP-1 하드닝 리프 (라이브 전 필수)
| ID | 문제(증거) | 리프 | 역할 |
|---|---|---|---|
| H-1 | 프로덕션 조립부 3곳 require_mandate=False, mandate_revision_id 항상 None → 컴플라이언스가 감사로그만 남기고 ALLOW (order_service/foundation_gate.py:215-235, submit_order.py:223) | mandate 바인딩을 조립부에 연결하고 require_mandate=True로 전환, 적대 테스트(무 mandate 주문 = REJECT), audit-baseline require_mandate_false 항목 닫기 | backend |
| H-2 | CM-11/15/16/17/20 미구현(사후거래 배치·거래보고·컴플라이언스 API·프론트·적대 스위트) | 스펙 L4_compliance §9 순서대로 5리프 | backend, frontend |
| H-3 | NH get_order NotImplementedError(trading_mixin.py:204), WS 미지원(adapter.py:246) → 장애 시 체결 재확인 불가 | 일별체결조회 매핑으로 REST 재조회 경로 확보(불가 시 fail-closed 정책 문서화), WS 프레임 스키마 확정 후 구독 | backend |
| H-4 | 백업·PITR·복구 리허설 스크립트 0건 | scripts/backup/{pg_basebackup,wal_archive,restore_drill}.py + healthcheck 일 1회 복구 리허설 finding | ops, backend |
| H-5 | 공급망 취약점 게이트 없음(quality.yml에 pip-audit/dependabot 0) | pip-audit + npm audit CI 게이트, dependabot.yml, local_ci 단계 추가 | ops |
| H-6 | Dockerfile 0건, compose.dev는 postgres만 | api/worker/frontend Dockerfile + compose.prod.yml + 헬스 엔드포인트 스모크 | ops |
| H-7 | e2e 파일 1개, Playwright 없음 | 백엔드 e2e 3건(주문→체결→정산, kill switch 중 거부, 재시작 복구) + Playwright 스모크 3건(주문·차트·백테스트) | backend, frontend |
| H-8 | property 테스트 0건 | hypothesis 도입: ledger 복식부기 불변식, 리스크 한도 단조성, 포지션 키 왕복 | backend |
| H-9 | Deflated Sharpe/PBO 수식 원문 미검증(overfitting.py:9) | Bailey and Lopez de Prado 원문 수치 예제로 회귀 테스트, 편차 시 수정 | backend |
| H-10 | alert_rules 11건이 사람을 호출하지 않음(pager/slack 연동 0) | Alertmanager 라우팅 + 웹훅(Slack/PagerDuty 포트, 어댑터 1종) + 무음 알림 감지 healthcheck | ops |
| H-11 | mandate 캐시 TTL 30초 stale ALLOW 결함 이력(0598fdab) | 상태변경 이벤트 기반 즉시 무효화 + 경합 테스트 | backend |
| H-12 | legacy_wallet_bridge pool placeholder 암묵 전제 | 주입 경로 정식화 또는 접근 시 즉시 실패하는 sentinel | backend |
| H-13 | env별 설정 분리 없음(.env 단일) | config/{dev,staging,live}.yaml + 로더, live에서 paper 플래그 강제 검증 | backend |

MVP-1 종료조건 11항 추가: "H-1~H-13 전부 CI 증빙으로 닫힘". 기존 10항과 함께 HB-5(실자금 승인)의 선행조건이다.

## Decision B — MVP-2 범위 (경쟁 제품 격차)
| ID | 격차 | 리프 | 우선 |
|---|---|---|---|
| M2-1 | 프론트-서버 실시간 채널 없음(RealtimeCandleSource 구현체 0) | 시장데이터 WS 게이트웨이(테넌트 인증·구독 상한·백프레셔) + 프론트 구독 훅 + 재접속 시 갭 보충 | 1 |
| M2-2 | DSL에 MTF/타 심볼 요청 없음 | 정적 분석 가능한 bounded request(symbol, timeframe, expr) 프리미티브 — 미래참조 금지·리소스 상한 유지 | 1 |
| M2-3 | DSL 타입 5종뿐 | array<float>, string 상수 단계 도입(리소스 상한에 배열 길이 포함) | 2 |
| M2-4 | strategy.* 가 side+qty만 | limit/stop 가격, bracket exit(profit/loss/trail), OCA — 백테스트·PAPER 패리티 테스트 | 1 |
| M2-5 | 알림 단일 조건, 웹훅 없음 | alertcondition() + 조건 타입(가격교차·드로잉 터치) + 웹훅/다채널 액션 엔진 | 2 |
| M2-6 | 드로잉 5종 | 채널·텍스트·화살표·측정·피치포크 등 10종 + 직렬화 왕복 property | 2 |
| M2-7 | OSS 지표 계층 비어 있음 | IND-11 이후 ta 어댑터, 큐레이션 세트 + 3자 교차검증 | 2 |
| M2-8 | 이벤트버스 단일 프로세스 큐 | Phase2 영속 백엔드(Redis Streams) + 재시작 재생 + 다중 인스턴스 | 1 |
| M2-9 | i18n 없음 | i18next + 문자열 추출 + ko/en | 3 |
| M2-10 | EMS venue 가중치 placeholder | TCA 산출물로 주기 재보정 루프 | 2 |
| M2-11 | FIX 회선(ADR-B 이연) | FIX 세션 어댑터(EM-17 포트 위) + 시뮬레이터 대조 | 3 |
| M2-12 | 규제 보고 제출(ADR-B 이연) | 국내 보고 포맷 어댑터 1종 + 제출 시뮬레이션 | 3 |
| M2-13 | 무중단 배포·HA 실배포 없음 | blue/green 롤아웃 + 마이그레이션 expand/contract 규칙 + 자동 롤백 | 2 |
| M2-14 | 마켓플레이스·신호 유입 | MP-1~10, SIG-1~6(기존 스펙) | 3 |
| M2-15 | 반응형·접근성 | 브레이크포인트 3종 + axe CI | 3 |
| M2-16 | 테넌트 격리 실증 부족 | tenant 경계 적대 테스트 확충(교차 조회·권한 상승·레이트리밋) | 2 |
| M2-17 | ADR 색인 없음 | docs/ADR_INDEX.md 생성 스크립트 + CI 최신성 검사 | 3 |
| M2-18 | 미국 정밀 데이터(HB-6 이후) | 유료 벤더 어댑터 1종(포트 위) | 보류 |

## Sequencing
1. QA 221건 소진과 병행해 H-1·H-5·H-9·H-11·H-12(작고 위험 큰 것) 즉시 배정.
2. H-2·H-3·H-4·H-6·H-7·H-8·H-10·H-13 순차. 여기까지가 MVP-1 완료.
3. MVP-2는 우선 1 → 2 → 3 순. 우선 1 네 건(M2-1·2·4·8)은 하드닝과 겹치지 않는 파일이라 병행 가능.

## MVP-2 종료 기준(초안)
실시간 차트 지연 p95 500ms 이하(로컬), DSL MTF 전략의 백테스트=PAPER 패리티, bracket 주문 왕복(데모), 이벤트버스 강제 종료 후 재생 무손실,
드로잉 15종 왕복 property, 알림 웹훅 전달 증빙, 무중단 배포 리허설 1회, tenant 적대 테스트 위반 0.

## Rejected
- 초저지연 EMS 경쟁(ADR-B 유지). 모바일 네이티브 앱(반응형으로 대체). DSL 무제한 루프·재귀(결정론 원칙 유지, bounded 프리미티브로 대체).
