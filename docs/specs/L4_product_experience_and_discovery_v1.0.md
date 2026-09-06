# L4 제품 경험·발견 기능 명세 v1.0

## 0. 문서 메타
- status: Accepted (2026-09-06) — ADR-2026-09-06-D의 실행 명세
- owner role: Chief Architect(원칙), PM(리프 배정)
- depends on: PLT §3(에러 봉투·테넌시), IND-12(지표 레지스트리), RD-7(리서치 조회), AI-10/11(실험 원장),
  FA-13~16(이벤트·재현), R(리스크 평가), CM-8(컴플라이언스 사전 판정), MP-1~3(리스팅)
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

## 5. 리프 목록
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

## 6. 미확정·리스크
- i18n 라이브러리 선정(react-i18next·lingui 등)은 UX-1에서 라이선스·번들 크기 확인 후 결정한다.
- 번역 품질: `catalog.en`은 초벌이며 출시 전 검수 필요(기계 번역 금지 문구는 금융 용어 위주로 지정).
- 팔로우는 PAPER 전용이며 LIVE 확장은 투자자문·일임 규제 검토가 선행돼야 한다(사람 결정).
