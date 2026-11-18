# 경쟁 제품 비교와 설계 결정 (FE-9 근거)

> 작성 2026-11-18, CTO. 기준(사용자 지정): **사람이 쓰기에 편하고, 많은 정보를 받을 수 있는가.**
> 방법: 공개 자료(제품 공식 문서·도움말·교육 자료) 웹 검색 5건. **한계**: 제품을 직접 써 보지 않았고, 일부 자료는 오래됐거나 제3자 글이다. 출처 등급을 표에 적었다.
> 표기: [출처] 아래 링크로 확인한 사실 / [추정] 검색에서 확인하지 못한 것(결정의 근거로 쓰지 않음).

## 1. 비교 대상과 확인된 사실

| 제품 | 핵심 방식 | 확인된 사실 | 출처 등급 |
|---|---|---|---|
| TradingView | 멀티 차트 레이아웃 + 동기화 | 심볼·크로스헤어·간격·시간·날짜 범위를 차트 간 동기화(레이아웃 메뉴에서 항목 선택). 심볼·간격을 **그룹**으로 나눠 부분 연결 가능. 도형은 같은 심볼 차트끼리만 동기화. 크로스헤어 동기화는 데스크톱 앱 한정. 레이아웃당 차트 수는 요금제로 제한(2/4/8). 다른 심볼 간 크로스헤어 시 가격이 다른 차트의 가시 범위 밖일 수 있다는 사용자 지적 | 공식 도움말·블로그 [1][2] |
| Bloomberg Terminal | Launchpad 컴포넌트 그룹 | 컴포넌트를 **이름 붙은 그룹**으로 묶고, 한 컴포넌트에서 종목을 바꾸면 그룹 전체가 갱신. 그룹은 여러 개 가능. 뉴스 패널을 워크시트에 연결해 클릭한 종목으로 갱신 | 교육기관 가이드·도움말 대화록(공식 최신 문서는 미확인) [3] |
| Interactive Brokers TWS (Mosaic) | 색상 연결 창 | 창 제목줄의 색 블록/사슬 아이콘으로 **색상 그룹**(API 기준 1~7) 지정, 같은 색은 함께 갱신. 창마다 **소스/목적지** 역할(소스는 종목을 보내고, 목적지는 받기만). 창은 한 그룹에만 속함. Mosaic은 **신규 사용자에게 권하는 사전 구성 작업공간**이고 기본으로 잠겨 실수로 닫히지 않게 함 | 공식 문서·IBKR Campus [4] |
| thinkorswim | 가젯 + 유연한 그리드 | 가젯을 **9색**으로 연결(같은 색은 심볼 공유). 유연 그리드는 행·열 개수를 사용자가 정함(차트 그리드는 32칸 상한). 레이아웃 저장·공유 링크. **다른 기기로 로그인하면 워치리스트 등은 복원되지만 작업공간 배치는 복원되지 않을 수 있음**(기기 저장 설정). 워치리스트는 그리드 칸 안이 아니라 사이드바에만 둘 수 있다는 제약 | 공식 도움말·Schwab [5] |
| Coinigy / Altrady (다중 거래소 터미널) | 여러 거래소 API 통합 | Altrady는 약 18~19개 거래소를 API로 연결, 스마트 주문·봇·스캐너 제공(자사 블로그 중심이라 마케팅 성격). Coinigy는 차트·워치리스트·알림 중심, 자동화 제한적. 통합 터미널은 거래소 고유 파생·마진·주문 유형을 완전히 대체하지 못한다는 리뷰. **거래소 간 호가 통합·최적 경로 선택의 방식은 확인된 자료 없음** | 자사 블로그·제3자 리뷰 [6] |

## 2. 읽어낸 것 (사용자 편의·정보량 기준)

1. **연결(링크)은 업계 표준이고 사용자가 이미 익숙하다.** TradingView(그룹), Bloomberg(컴포넌트 그룹), TWS(색상 그룹), thinkorswim(9색) 모두 "한 곳에서 종목을 바꾸면 연결된 패널이 함께 바뀐다". 우리 맥락 버스는 단일 전역이 아니라 **여러 개의 이름·색 그룹**이어야 사용자의 기대와 맞는다. (초안의 단일 맥락 버스를 수정한다.)
2. **소스/목적지 구분**이 있어야 "의도치 않게 다른 패널이 바뀌는" 혼란이 줄어든다(TWS).
3. **신규 사용자용 사전 구성**이 학습 비용을 낮춘다(TWS Mosaic). 고밀도 도구의 진입 장벽은 빈 캔버스다.
4. **잠금 기본값**이 실수로 닫는 사고를 막는다(TWS). 금전이 걸린 화면이라 우리에게는 더 중요하다.
5. **기기 간 작업공간 유실**은 실제 약점이다(thinkorswim). 서버 저장·동기화는 우리가 확실히 앞설 수 있는 지점이다.
6. **동기화에는 함정이 있다**: 심볼이 다르면 크로스헤어 가격이 다른 차트 범위를 벗어나고(TradingView 사용자 지적), 도형은 같은 심볼에서만 의미가 있다. 동기화 대상별 규칙을 명시해야 한다.
7. **차트·패널 수를 요금제로 제한하는 제품이 있다(TradingView).** 우리는 인위적 제한 대신 **성능 예산**으로 상한을 정하고 초과 시 경고한다.
8. **다중 거래소 통합 터미널은 거래소 고유 기능을 잃는다.** 따라서 "통합 + 거래소별 고유 기능 접근"을 함께 줘야 한다. 호가 통합·최적 경로는 시중 자료로 검증되지 않아 **차별화 후보이자 위험**이다(데이터 라이선스·지연·정확성).

## 3. 결정 (PRO_WORKSPACE_DESIGN에 반영)

| # | 결정 | 근거 |
|---|---|---|
| D1 | 연결 모델을 **다중 이름·색 그룹**으로 한다(기본 그룹 1개 제공, 창은 한 그룹에만 속함). 패널은 소스/목적지 역할을 갖고 UI에 표시한다 | 읽은 것 1·2 |
| D2 | 연결 항목별로 켜고 끈다: 종목 / 크로스헤어(시각 커서) / 간격 / 시간 범위 / 계정 범위. 도형은 같은 종목끼리만 동기화한다. 다른 종목 크로스헤어는 "범위 밖" 표시를 한다 | 읽은 것 6 |
| D3 | **사전 구성 레이아웃**(신규 사용자 기본, "시작 작업공간")을 제공하고 레이아웃은 **기본 잠금**, 잠금 해제는 명시 동작이다 | 읽은 것 3·4 |
| D4 | 레이아웃·그룹 설정은 **서버 저장 + 기기 간 동기화**가 기본이다(기기 저장 설정과 구분해 명시) | 읽은 것 5 |
| D5 | 패널 수는 인위 제한 없이 **성능 예산**으로 경고한다(8패널 기준 INP ≤ 200ms) | 읽은 것 7 |
| D6 | 통합 뷰에는 **거래소별 고유 기능 접근 경로**(거래소 능력 표)를 항상 둔다. 호가 통합·최적 경로는 **어댑터 지원·라이선스 조사(Phase 0 갭 조사) 후**에 범위를 확정한다 | 읽은 것 8 |
| D7 | 시그니처 장면 기본값: **A(두 거래소 호가 비교 후 한 번 주문)** — 단, D6 조사 결과에 따라 B(차트 구간 → 백테스트 → 주문 초안)를 먼저 완성하고 A를 뒤따를 수 있다 | 읽은 것 8: A는 검증된 선례가 없어 위험도가 높다 |
| D8 | 1차 로케일 ko·en, 1차 자산군 가상자산(+ 기존 KIS 등은 기존 범위 유지), 다중 모니터·부유 패널은 Phase 2, 모바일은 같은 작업공간 모델의 축소판 | 사용자 위임(기본값 승인) |

## 4. 아직 모르는 것 (결정에 쓰지 않음)

- 위 제품들의 실제 사용감(직접 사용 평가). 가능하면 Phase 0에서 무료 체험 계정으로 핵심 장면을 직접 비교한다.
- Bloomberg의 최신 공식 문서, 거래소 간 호가 통합 방식.
- 한국 사용자 기준 선호(국내 증권사 HTS: 키움 영웅문, 미래에셋 등)와 비교 — **다음 조사 대상**(우리는 KIS·키움·NH 어댑터가 있어 국내 HTS 사용 경험이 기준선이 될 수 있다).

## 출처

1. TradingView, "How to sync the charts of my layout?" — https://in.tradingview.com/support/solutions/43000629992
2. TradingView blog, "Crosshair syncing now available in TradingView Desktop app" — https://www.tradingview.com/blog/crosshair-syncing-in-tradingview-desktop-app-30157
3. Bloomberg Launchpad 그룹 설명 (University of Delaware 가이드, Bloomberg 교육 자료 등) — https://my.lerner.udel.edu/wp-content/uploads/BB-Getting-Started-in-Launchpad.pdf , https://professional.content.cirrus.bloomberg.com/professional2023/insights/technology/bloomberg-terminal-essentials-ib-worksheets-launchpad
4. Interactive Brokers, "Color Grouping Linked Panels" — https://ibkrguides.com/traderworkstation/color-grouping-linked-panels.htm ; Mosaic 사용자 가이드 — https://www.interactivebrokers.co.uk/en/software/tws.bak/usersguidebook/mosaic/group_windows.htm
5. thinkorswim, "Flexible Grid" — https://toslc.thinkorswim.com/center/howToTos/thinkManual/charts/Flexible-Grid ; "Server-Side and Device-Stored Settings" — https://toslc.thinkorswim.com/center/howToTos/thinkManual/Miscellaneous/Server-Side-and-Device-Stored-Settings
6. Altrady 비교 글·리뷰 — https://altrady.com/coinigy , https://www.bitdegree.org/crypto/altrady-review (자사/제3자, 마케팅 성격 주의)
