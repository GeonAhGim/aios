# L4 제품 경험·발견 기능 명세 v1.1

## 0. 문서 메타
- status: Accepted (2026-09-06) v1.0 — ADR-2026-09-06-D의 실행 명세. §U 추가 v1.1(2026-09-10) — ADR-2026-09-09-B Decision C의 실행 명세
- owner role: Chief Architect(원칙), PM(리프 배정)
- depends on: PLT §3(에러 봉투·테넌시), IND-12(지표 레지스트리), RD-7(리서치 조회), AI-10/11(실험 원장),
  FA-13~16(이벤트·재현), R(리스크 평가), CM-8(컴플라이언스 사전 판정), MP-1~3(리스팅), ADR-2026-09-09-B(§U)
- implemented by: `frontend/apps/web/**`, `frontend/packages/{ui-web,shared-hooks,api-client}/**`,
  `src/foundation/screener/**`, `src/foundation/whatif/**`, `src/foundation/follow/**`, `src/api/routers/{screener,whatif,follow}.py`
- 리프 접두: **UX**

## 1. 요구 (왜 지금인가)
| 항목 | 현재 | 문제 |
|---|---|---|
| 다국어 | `.tsx` 149개 중 **147개에 한국어 하드코딩**, i18n 없음 | 화면이 늘수록 재작성 비용이 선형 증가 |
| 디자인·접근성 | `packages/ui-web` 존재, 토큰·a11y 계약 없음 | 화면마다 제각각, 사후 통일 불가능 |
| 발견 | 스크리너 없음 | 경쟁 제품의 일상 진입점이 비어 있음 |
| 사전 분석 | 주문 전 영향 미리보기 없음 | 기관 제품의 핵심, 우리는 엔진이 이미 있음 |
| 팔로우 | 구매만 가능 | 리테일 최대 유입 경로 부재 |
| 모바일·알림·단축키·온보딩 | 없음 | 제품 완성도 |
| 결정 이력 | 데이터는 쌓이나 보는 화면 없음 | 최대 차별점이 사용자에게 안 보임 |

## 2. 모듈 분해
### 2.1 플랫폼 UX — `frontend/packages/{ui-web,shared-hooks}`
| 파일 | 책임 |
|---|---|
| `ui-web/src/i18n/{provider,catalog.ko,catalog.en,format}.ts` | 로케일 사전·포매터. **금액·수량은 Decimal 문자열 유지**(로케일 반올림 금지) |
| `ui-web/src/tokens/{color,space,type,state}.ts` + 다크 모드 | 디자인 토큰 |
| `ui-web/src/a11y/{focus,announcer}.ts` | 포커스 관리·스크린리더 알림 |
| `ui-web/src/shell/{CommandPalette,NotificationCenter,EmptyState,Onboarding}.tsx` | 셸 UX |
| `apps/web/src/pwa/{manifest,serviceWorker,push}.ts` | 설치·오프라인 셸·푸시(`device_tokens` 연동) |

### 2.2 스크리너 — `src/foundation/screener/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `ScreenDefinition{filters: tuple[Filter,...], universe, sort, columns}`, `Filter = IndicatorFilter\|FundamentalFilter\|ResearchFilter\|BacktestStatFilter` |
| `domain/query_plan.py` | 필터 → 실행 계획(순수). 지표는 컬럼 경로, 리서치는 `as_of` 강제 |
| `application/run_screen.py` | 실행(커서 페이지네이션·상한), 결과 캐시 |
| `application/{save_screen,share_screen,alert_on_screen}.py` | 저장·공유(마켓 규칙)·조건 알림 |

### 2.3 사전 영향·리밸런싱 — `src/foundation/whatif/`
| 파일 | 책임 |
|---|---|
| `application/preview_order.py` | **읽기 전용**: 가상 주문을 리스크·컴플라이언스·포트폴리오 엔진에 통과시켜 영향 산출. 주문·이벤트 생성 없음 |
| `domain/impact.py` | 노출·집중도·VaR·한도 여유 델타(순수) |
| `application/rebalance_plan.py` | 목표 비중 → 이탈 → 주문 초안(승인 전 상태) |

### 2.4 팔로우 — `src/foundation/follow/`
| 파일 | 책임 |
|---|---|
| `contracts/v1.py` | `FollowSubscription{follower_portfolio, source_listing, sizing_policy, max_notional, paper_only=True}` |
| `domain/mirror_rules.py` | 신호 복제 규칙(비중 환산·최소 단위·거부 조건, 순수) |
| `application/mirror_signal.py` | 원본 신호 → 팔로워 주문 의도(리스크·컴플라이언스 게이트 필수 통과) |

## 3. 계약 (요지)
- i18n 키는 `screen.section.element` 규칙. 누락 키는 개발 빌드에서 실패, 운영에서는 키 자체를 표시(빈 문자열 금지).
- 스크리너: `ResearchFilter`는 `as_of` 없이 실행 불가(누수 차단, RD-A1 상속). 결과 상한 1,000행·타임아웃 10초.
- What-if: 응답에 `would_be_denied_by: [risk|compliance]`와 근거 코드를 담되 **주문 생성 부작용 0**임을 계약으로 명시.
- 팔로우: `paper_only=True` 불변. LIVE 전환은 이 계약으로 불가(별도 ADR 필요).
- 에러: `UX_SCREEN_TIMEOUT`(408), `UX_SCREEN_LIMIT`(413), `UX_WHATIF_UNAVAILABLE`(503), `UX_FOLLOW_SOURCE_INACTIVE`(409).

## 4. 불변조건
- **UX-A1** 사용자 노출 문자열 리터럴 0건(CI 검사).
- **UX-A2** What-if 경로는 어떤 쓰기도 하지 않는다(정적 검사 + 적대적 테스트).
- **UX-A3** 팔로우 주문도 팔로워 자신의 리스크·컴플라이언스 게이트를 통과한다(원본 계정 권한 상속 금지).
- **UX-A4** 접근성 검사(키보드 도달·대비·레이블) 실패 시 빌드 실패.
- **UX-A5** 결정 이력 뷰어는 저장된 이벤트·판정만 표시한다(추론·요약 생성 금지).

## 5. 동시성·멱등성·트랜잭션 경계 (105번 표준)
- 화면 쓰기(워치리스트·레이아웃·알림 규칙·연결 설정)는 모두 서버 API의 멱등 키(클라이언트 생성 UUID)를 동반한다. 같은 키 재전송은 200과 동일 결과, 다른 본문은 409.
- 낙관적 갱신은 서버 응답으로 반드시 되돌림·확정된다. 되돌림 시 사용자에게 사유를 표시한다(무음 드롭 금지).
- 다중 탭·다중 기기 동시 편집은 버전 필드(ETag)로 충돌을 감지하고 마지막 쓰기 승리를 금지한다(409 + 새로고침 유도).
- 주문·연결·kill 같은 안전 행동은 프론트가 절대 재시도하지 않는다(서버 게이트가 유일한 권위).

## 6. 실패 모드와 복구
- API 5xx/타임아웃: 화면은 마지막 성공 상태를 "지연됨" 배지와 함께 유지하고, 쓰기 버튼은 비활성화한다.
- 실시간 채널 단절: 마지막 봉 이후 갭을 REST로 보충한 뒤 재구독(M2-1 계약). 보충 실패 시 커버리지 배지에 "미커버 구간" 표시.
- 인증 만료: 편집 중 데이터를 로컬에 보존하고 재로그인 후 복원한다(비밀값은 보존 대상에서 제외).
- 부분 실패(일괄 저장 중 일부 거부): 성공/거부 항목을 구분해 표시하고 거부분만 재시도 가능하게 한다.

## 7. 성능·SLO·관측성 (108번)
- 첫 화면 상호작용 가능 시간 p95 2.5s(로컬), 차트 5k봉 렌더 p95 200ms(CH 예산 재사용), 스크리너 결과 표시 p95 2s(U-1).
- 모든 화면 이벤트(진입·오류·되돌림·재시도)는 tenant·화면·요청 id를 붙여 관측 파이프라인(PLT 관측성)으로 보낸다. 개인정보·비밀값은 기록하지 않는다.
- 접근성: 핵심 흐름 axe 위반 0(M2-15), 키보드만으로 주문 취소·kill 도달 가능.

## 8. 테스트 계획
- 컴포넌트 테스트(vitest): 빈/오류/로딩/되돌림 상태 각 1건 이상.
- 계약 테스트: OpenAPI 경로 ↔ api-client 함수 양방향 대응(CONSIST-1 §7), 응답 스키마 변경 시 적색.
- e2e(Playwright, H-7b): 온보딩→차트→백테스트→룰 등록, 실패 주입(5xx·단절) 1건.
- 테넌트 격리: 교차 테넌트 404를 화면 레벨에서 재확인.

## 9. 리프 목록
| 리프 | 파일 | 선행 | DoD | 크기 |
|---|---|---|---|---|
| UX-1 | i18n 프레임워크 도입 + `catalog.ko` 골격 + 포매터(금액 Decimal 유지) + ESLint 리터럴 금지 규칙 | — | 규칙 위반 시 빌드 실패 | 400 |
| UX-2 | **기존 147개 파일 문자열 키 추출·치환**(기계적 변환 + 수동 검수) + `catalog.en` 초벌 | UX-1 | 리터럴 0건, 화면 회귀 통과 | 600 |
| UX-3 | 디자인 토큰 + 다크 모드 + 공용 컴포넌트 계약 | — | 토큰 외 하드코딩 색상 0건 | 460 |
| UX-4 | 접근성 baseline(포커스·레이블·대비) + CI 검사(axe) | UX-3 | 전 화면 위반 0 | 400 |
| UX-5 | `screener/contracts/v1.py` + `domain/query_plan.py` + test | IND-12 | 4종 필터 계획 생성, 리서치 `as_of` 강제 | 500 |
| UX-6 | `application/run_screen.py` + 인덱스·캐시 + 통합 | UX-5, DC-13 | 1,000행 ≤10초, 교차 테넌트 404 | 400 |
| UX-7 | `save/share/alert_on_screen` + 마이그레이션 + 통합 | UX-6, MP-3 | 저장·공유·조건 알림 | 400 |
| UX-8 | 프론트 `ScreenerPage.tsx`(필터 빌더·결과 표·차트/백테스트 연결) | UX-6, UX-1 | 화면·negative | 300 |
| UX-9 | `whatif/domain/impact.py` + test | R-14, R-27 | 델타 정확값 | 300 |
| UX-10 | `whatif/application/preview_order.py` + **쓰기 0 정적 검사** + 적대적 | UX-9, CM-8 | UX-A2 증명 | 400 |
| UX-11 | `whatif/application/rebalance_plan.py` + `core/portfolio` 연결 + test | UX-9, FA-6 | 목표 비중 → 주문 초안 | 400 |
| UX-12 | 프론트 `WhatIfPanel.tsx` + `RebalancePage.tsx` | UX-10, UX-11 | 화면·negative | 460 |
| UX-13 | `follow/contracts/v1.py` + `domain/mirror_rules.py` + test | MP-1 | 비중 환산·거부 조건 | 400 |
| UX-14 | `follow/application/mirror_signal.py` + 게이트 통과 적대적 테스트 | UX-13, CM-8 | UX-A3 증명, paper_only 불변 | 400 |
| UX-15 | 프론트 `FollowPage.tsx`(팔로우 관리·성과 비교) + 자문 오인 표현 금지 검수 | UX-14 | 화면·negative | 300 |
| UX-16 | PWA(매니페스트·서비스워커·설치) + 오프라인 셸 | UX-3 | 설치·오프라인 진입 | 300 |
| UX-17 | 푸시 알림 연동(`device_tokens`) + 권한 흐름 | UX-16 | 구독·해지·수신 | 300 |
| UX-18 | 알림 센터 + 일간 다이제스트(이메일·푸시) | UX-17 | 이력·요약 | 400 |
| UX-19 | 명령 팔레트 + 단축키 맵 + 도움말 | UX-3 | 키보드 전 경로 | 300 |
| UX-20 | 온보딩 흐름(거래소 연결 → 전략 → 페이퍼 실행) + 전 목록 빈 상태 | UX-3, UX-1 | 첫 실행 완주 | 400 |
| UX-21 | 반응형 브레이크포인트 전 화면 점검 + 모바일 레이아웃 회귀 | UX-3 | 주요 10화면 모바일 통과 | 400 |
| UX-22 | **결정 이력 뷰어** — 주문·판정·데이터 계보를 이벤트에서 역추적해 표시 | FA-15, CM-13 | 차단 사유 역추적 재현, UX-A5 | 460 |

## 10. 미확정·리스크
- i18n 라이브러리 선정(react-i18next·lingui 등)은 UX-1에서 라이선스·번들 크기 확인 후 결정한다.
- 번역 품질: `catalog.en`은 초벌이며 출시 전 검수 필요(기계 번역 금지 문구는 금융 용어 위주로 지정).
- 팔로우는 PAPER 전용이며 LIVE 확장은 투자자문·일임 규제 검토가 선행돼야 한다(사람 결정).

## §U. MVP-2 사용자 가치 리프 DoD — ADR-2026-09-09-B Decision C (U-1~U-14)
- 이 절의 `U-*` ID는 ADR-2026-09-09-B Decision C에서 온 사용자 가치 리프 식별자이며, §9의 `UX-*` 리프 번호와는 다른 이름공간이다(혼동 금지). 각 U 항목은 §9의 기존 UX 리프·타 스펙 모듈 위에 얹히며, U 항목 자체는 이 절에서 신규 파일·경로를 지정하지 않는다(구현 리프 분해는 후속 task에서 진행).
- **공통 DoD(전 U 항목에 적용, 아래 표에서는 반복하지 않음)**: (1) 실제 사용자 흐름 e2e 테스트 1건 이상, (2) 빈 상태·오류 상태·로딩 상태 각각의 화면/응답 검증, (3) 테넌트 격리 적대적 테스트(교차 테넌트 조회·쓰기 거부), (4) 기능 플래그로 단계 공개(플래그 OFF 시 완전 비활성·기존 화면 무변경 회귀 통과). 아래 표의 DoD 열은 이 4항에 더해 항목별로 측정 가능하고 CI로 증빙 가능한 추가 기준만 적는다.
- 우선순위는 ADR-2026-09-09-B Decision C를 그대로 따른다: 우선 1 = U-1·U-2·U-3·U-4·U-10(하드닝과 병행 착수), 우선 2 = U-5·U-6·U-7·U-8·U-9·U-11·U-16, 우선 3 = U-11·U-12·U-13·U-14(M2-HOLD 조건에서 발행).

| ID | 사용자 흐름(1문장) | API/화면/데이터 의존 | DoD(측정 가능·CI 증빙) | 기능 플래그 |
|---|---|---|---|---|
| U-1 | 사용자가 국내·해외·코인 심볼을 한 화면에서 가격·거래량·지표·재무·수급 조건으로 걸러 워치리스트로 저장하고, 결과 행에서 바로 차트·주문으로 이동한다 | §2.2 스크리너(UX-5~8, `src/foundation/screener/**`) 확장 + DC 심볼 마스터(DC-13 인덱스) + IND-12 지표 레지스트리 + 신규 워치리스트 저장 API/화면 | 3개 자산군(국내주식·해외주식·코인) 혼합 스크린이 UX-6의 1,000행 ≤10초 상한을 그대로 만족, 결과 행 클릭→차트 진입 e2e 1건, 워치리스트 저장 후 재로그인 시 동일 결과로 복원 | `ff_u1_unified_watchlist` |
| U-2 | 사용자가 증권사(KIS/NH)·거래소(Bitget) 전 계좌의 포지션·현금·손익·노출을 펀드/포트폴리오 스코프로 한 화면에서 보고 일·월·연 수익률·MDD를 확인한다 | FA-6(펀드/포트폴리오 스코프 응답) + FA-14 투영(`projections/{orders,positions,ledger}.py`) 재사용 + 신규 `GET /accounts/summary` 집계 API + 신규 대시보드 화면 | 계좌 3종 혼합 합산액이 개별 계좌 합과 소수점 오차 0으로 일치(반올림 규율 준수, Decimal), 일/월/연 수익률·MDD가 FA 투영 재계산과 바이트 동일, 계좌 일부 조회 실패 시 실패한 계좌만 표시하고 전체 화면 실패로 번지지 않음(부분 실패 테스트) | `ff_u2_portfolio_dashboard` |
| U-3 | 사용자가 자연어로 요청하면 AIOS Script 초안이 생성·설명되고, 백테스트 결과 해설과 공시·뉴스 요약(OpenDART)을 대화로 받는다 | 에이전트 게이트웨이(ADR-2026-09-05-A) + DSL 컴파일러(DSL-1~12) + BT 백테스트 결과 조회 + RD-7 리서치 조회(OpenDART) | 생성된 스크립트 100%가 DSL 컴파일·리소스 상한 검사를 통과하거나 실패 사유를 사용자에게 명시(무음 실패 0건, 회귀 CI 픽스처), 세션당 요청·토큰 상한 적용 및 초과 시 명확한 오류 응답(무한 재시도 없음) | `ff_u3_ai_assistant` |
| U-4 | 사용자가 조건(가격·지표·공시·시간)과 행동(알림·주문·헤지·kill)을 코드 없이 조합해 규칙을 등록하고, 과거 데이터로 발동 횟수를 미리 본다 | M2-5(alertcondition·조건 타입·웹훅/다채널 액션 엔진) + 신규 텔레그램 알림 어댑터(포트 위 구현체 1종) | 규칙 저장 후 과거 90일 데이터로 재실행한 발동 횟수가 저장 시점 미리보기 값과 일치(결정론), 실거래 행동(주문·헤지·kill)은 반드시 리스크·컴플라이언스 게이트를 통과해야만 실행됨(UX-A3와 동형의 적대적 테스트: 게이트 미통과 규칙 실행 0건), 텔레그램 알림 전달 성공/실패가 로그로 남음 | `ff_u4_rule_builder` |
| U-5 | 사용자가 자동 저널링된 체결 내역에 태그·메모를 남기고 승률·기대값·MAE/MFE·시간대별 성과와 실수 패턴 리포트를 본다 | UX-22 결정 이력 뷰어 + FA-14/15 투영·재생(`order_events`/`pos_journal` 원천) | 저널 항목이 체결 이벤트와 1:1 대응(누락·중복 0건, FA-16 무이벤트 상태변경 0건 불변식 재사용), MAE/MFE 계산값이 원본 바 데이터 재계산과 일치, 태그·메모는 교차 테넌트 조회 404 | `ff_u5_trade_journal` |
| U-6 | 사용자가 차트에서 투자자별 매매동향(KIS TR), 배당·공모주·실적 캘린더(OpenDART), 공매도·대차(KRX 공개) 데이터를 이벤트 마커로 확인한다 | RD 리서치 조회(KIS TR·OpenDART 연동) + CH 차트 이벤트 마커 플러그인 | 마커 클릭 시 원본 공시 수치·출처 링크만 표시하고 요약·추론을 생성하지 않음(UX-A5와 동형 정적 검사), 데이터 소스 장애 시 해당 마커만 숨겨지고 차트 나머지는 정상 렌더(부분 실패 테스트), 데이터 최신성(스테일니스) 배지 표시 | `ff_u6_kr_market_data` |
| U-7 | 사용자가 전략 템플릿 20종 중 하나로 원클릭 백테스트를 돌리고, 워크포워드·파라미터 그리드 탐색에서 과적합 경고(DSR/PBO)를 확인한다 | BT 백테스트 엔진 + DSL 컴파일러 + H-9(Deflated Sharpe/PBO 원문 검증 회귀) 결과 재사용 | 템플릿 20종 전부 컴파일·백테스트 성공(회귀 CI 픽스처), DSR/PBO 임계 초과 시 표시되는 경고 배지가 H-9 회귀 테스트의 판정 함수와 동일 결과, 파라미터 그리드 탐색이 DSL 리소스 상한·결정론 원칙(ADR-B Rejected 절)을 위반하지 않음 | `ff_u7_strategy_gallery` |
| U-8 | 사용자가 포지션 사이징 계산기, 한도 소진 예측, 시나리오·스트레스 테스트 화면에서 리스크 엔진 결과를 확인한다 | R 리스크 엔진(R-14·R-27, UX-9 `whatif/domain/impact.py`와 동일 계산 경로 재사용) | 화면 표시 결과가 R 엔진 배치 계산과 바이트 동일, 시나리오 실행은 주문·이벤트 생성 부작용 0(UX-A2와 동형의 정적 검사 + 적대적 테스트), 한도 소진 예측 시간창을 사용자가 조정 가능 | `ff_u8_risk_coach` |
| U-9 | 사용자가 국내·해외 주식·코인 양도차익 계산 초안과 월간 성과 PDF/이메일을 받는다 | FA-14 투영(원장) + 신규 PDF/이메일 발송 어댑터(포트 위 구현체 1종) | 계산 초안에 "세무 자문 아님" 고지 문구 포함(UX-15 자문 오인 방지 원칙과 동형 검수), 초안 합계가 FA 원장 합계와 일치(회귀 테스트), 월간 리포트 발송 성공/실패 로그 | `ff_u9_tax_report` |
| U-10 | 신규 사용자가 증권사/거래소 연결 마법사(권한 점검·읽기 전용 확인)를 거치거나 샘플 데이터 데모 모드로 5분 내 첫 백테스트를 완주한다 | UX-20 온보딩 흐름 확장 + HB-2(Bitget 데모 키)·HB-3(KIS 모의계좌) 연결 상태 조회 + 신규 데모 데이터셋(실계좌 미접촉) | ADR-B MVP-2 종료 기준과 동일 지표(사용성 테스트 3명 중 3명이 도움 없이 연결→스크리너→차트→백테스트→룰 등록까지 15분 내 완주), 데모 모드는 어떤 실계좌 API도 호출하지 않음(정적 검사로 증빙) | `ff_u10_onboarding_demo` |
| U-11 | 소규모 운용사 사용자가 고객별 성과·수수료·보고서를 GIPS 준거 표기로 확인한다 | FA-6 스코프 위 신규 고객 계정 모델 | 고객별 성과 계산이 GIPS 준거 공식 회귀 테스트를 통과, 고객 간 데이터가 교차 조회 404로 격리, 수수료 계산이 저장된 조건으로 재현 가능 | `ff_u11_multi_account` |
| U-12 | 사용자가 과거 바를 리플레이하며 주문을 연습하고 복기 채점을 받는다 | CH-7 리플레이 엔진 재사용 + PAPER 전용 연습 주문 경로 | 리플레이 중 발생한 주문은 실계좌·실이벤트에 영향 0(follow의 `paper_only` 불변과 동형의 적대적 테스트), 동일 리플레이를 재실행하면 채점 결과가 바이트 동일(결정론) | `ff_u12_replay_training` |
| U-13 | 사용자가 앱을 설치형 PWA로 쓰고 알림을 푸시로 받는다 | M2-15 반응형 브레이크포인트 위 UX-16/17(PWA·푸시) 확장 | 설치 후 오프라인 셸 진입 e2e, 푸시 구독·해지·수신 e2e, 브레이크포인트 3종 전부 통과(axe CI 포함) | `ff_u13_pwa_push`(UX-16/17과 동일 인프라 재사용, 신규 플래그는 노출 범위만 분리) |
| U-14 | 사용자가 마켓플레이스 공개 전략에서 검증 파이프라인 결과와 실계좌 추적 배지를 확인한다 | F-04 검증 파이프라인(hard-fail 판정) + M2-14(MP-1~10 마켓플레이스) | 배지 상태가 검증 파이프라인 최신 판정과 TTL 이내로 일치(스테일 배지 0건, 만료 시 배지 숨김), 실계좌 추적 수치는 저장된 이벤트만 표시하고 추론·요약을 생성하지 않음(UX-A5와 동형) | `ff_u14_marketplace_badge` |

## 11. 진행 현황 (자동 생성)

<!-- spec-status:begin (auto-generated by C:/aios/pm/spec_status.py — do not edit) -->
갱신 2026-09-12T17:04:32+00:00 · 총 22 · done 1 · inflight 21 · untouched 0 · hold 0 · 재오픈 1 · 깊이(done) {'D?': 1}

| ID | 리프 | 상태 | done task | 열린 task | depth | commit |
|---|---|---|---|---|---|---|
| UX-1 | i18n 프레임워크 도입 + catalog.ko 골격 + 포매터(금액 Decimal 유지) + ESLint 리터럴 금지 규칙 | done | 2845 | 2685 |  | bcee89e |
| UX-2 | 기존 147개 파일 문자열 키 추출·치환(기계적 변환 + 수동 검수) + catalog.en 초벌 | inflight |  | 2686 |  |  |
| UX-3 | 디자인 토큰 + 다크 모드 + 공용 컴포넌트 계약 | inflight |  | 2687 |  |  |
| UX-4 | 접근성 baseline(포커스·레이블·대비) + CI 검사(axe) | inflight |  | 2688 |  |  |
| UX-5 | screener/contracts/v1.py + domain/query_plan.py + test | inflight |  | 2689 |  |  |
| UX-6 | application/run_screen.py + 인덱스·캐시 + 통합 | inflight |  | 2690 |  |  |
| UX-7 | save/share/alert_on_screen + 마이그레이션 + 통합 | inflight |  | 2691 |  |  |
| UX-8 | 프론트 ScreenerPage.tsx(필터 빌더·결과 표·차트/백테스트 연결) | inflight |  | 2692 |  |  |
| UX-9 | whatif/domain/impact.py + test | inflight |  | 2693 |  |  |
| UX-10 | whatif/application/preview_order.py + 쓰기 0 정적 검사 + 적대적 | inflight |  | 2694 |  |  |
| UX-11 | whatif/application/rebalance_plan.py + core/portfolio 연결 + test | inflight |  | 2695 |  |  |
| UX-12 | 프론트 WhatIfPanel.tsx + RebalancePage.tsx | inflight |  | 2696 |  |  |
| UX-13 | follow/contracts/v1.py + domain/mirror_rules.py + test | inflight |  | 2697 |  |  |
| UX-14 | follow/application/mirror_signal.py + 게이트 통과 적대적 테스트 | inflight |  | 2698 |  |  |
| UX-15 | 프론트 FollowPage.tsx(팔로우 관리·성과 비교) + 자문 오인 표현 금지 검수 | inflight |  | 2699 |  |  |
| UX-16 | PWA(매니페스트·서비스워커·설치) + 오프라인 셸 | inflight |  | 2700 |  |  |
| UX-17 | 푸시 알림 연동(device_tokens) + 권한 흐름 | inflight |  | 2701 |  |  |
| UX-18 | 알림 센터 + 일간 다이제스트(이메일·푸시) | inflight |  | 2702 |  |  |
| UX-19 | 명령 팔레트 + 단축키 맵 + 도움말 | inflight |  | 2703 |  |  |
| UX-20 | 온보딩 흐름(거래소 연결 → 전략 → 페이퍼 실행) + 전 목록 빈 상태 | inflight |  | 2704 |  |  |
| UX-21 | 반응형 브레이크포인트 전 화면 점검 + 모바일 레이아웃 회귀 | inflight |  | 2705 |  |  |
| UX-22 | 결정 이력 뷰어 — 주문·판정·데이터 계보를 이벤트에서 역추적해 표시 | inflight |  | 2706 |  |  |
<!-- spec-status:end -->
