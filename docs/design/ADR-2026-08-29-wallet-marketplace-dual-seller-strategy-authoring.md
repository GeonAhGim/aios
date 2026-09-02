# ADR-2026-08-29: 마켓플레이스 내부 크레딧 지갑 + 판매자 이원화 + 고차원 전략 생성

> 상태: 구현 완료(2026-08-29~09-01). §1(지갑)·§2(판매자 이원화)·§3(마법사)
> 백엔드+프론트엔드 전부 반영, 브라우저 실증 완료. §3의 자연어 AI
> 프롬프트 축만 Anthropic API 크레딧 미충전으로 501 스텁 상태 —
> 라우터/스키마/프론트 UI는 이미 완성돼 있어 크레딧 충전 후
> strategy_prompt_service.py 내부만 교체하면 활성화된다.

## 배경

사용자가 세 가지를 동시에 요구했다(2026-08-29):

1. "거래할 때 통화는 뭘로 해야할지도 정해야지" — 마켓플레이스 거래 통화/결제
   모델이 미정이었다는 지적.
2. "마켓플레이스도 유저간 거래와 플랫폼에서 유저에게 파는 것을 지원해야해"
   — P2P(유저↔유저)뿐 아니라 B2C(플랫폼↔유저) 판매도 지원.
3. "조건식같은 아날로그적인 방법이 아니야 더욱 고차원적이지만 사용자는
   조건식보다 더 쉽게 쓸수있어야하는거야" — Strategy Builder의 조건식
   직접 조립(`ConditionRow`/`ConditionGroup`) UX를 대체할, 더 쉬운 고차원
   전략 생성 방법.

세 질문에 AskUserQuestion으로 확인한 결정:

| 질문 | 결정 |
|---|---|
| 결제 모델 | **플랫폼 내부 크레딧(포인트) 지갑** |
| 전략 생성 UX | **목표기반 마법사 + 자연어 AI 프롬프트, 두 방식 병행** |
| 판매자 구분 취급 | **동일 커미션 구조, `seller_type`만 구분** |

## §1. 결제 모델 — 플랫폼 내부 크레딧 지갑 (구현 완료)

### 기존 스펙과의 관계

`14_marketplace_detailed_v1.1.md` §14.1이 이미 명시한 원칙은 그대로 지킨다:

- 가격 통화는 `Money`(11번 §11.1) 타입 개념상 **원화(KRW) 단일 통화**로
  단순화한다(크립토 전략이든 KIS 전략이든 마켓플레이스 거래 자체는 자산
  손익과 무관하게 단일 통화).
- 자동 PG(결제대행) 미도입은 **의도적 설계**다 — 실제 결제대행 계약(전자
  금융업 등록 여부 포함)은 19장 법률검토 완료 후 진행하는 것이 안전하다는
  원문 판단을 그대로 존중한다.

이번 결정은 이 원칙을 어기지 않으면서 그 위에 지갑 계층 하나를 추가한
것뿐이다: **"충전(입금 확인)"과 "구매(지갑 차감)"를 분리**한다.

- 충전: 기존 `payment_confirmation_service.py`가 하던 것과 동일한 패턴
  (사용자가 실제 은행 계좌로 원화를 입금 → 관리자가 수동으로 확인 →
  지갑 잔액 증가)을 그대로 재사용한다. 자동 PG를 여전히 붙이지 않으므로
  19장 법률검토 전제를 어기지 않는다.
- 구매: 지갑 잔액은 이미 검증이 끝난 자금이므로, 구매 시점에 즉시
  차감하고 그 자리에서 결제를 확정한다. 유저간 거래에서 플랫폼이 실제
  은행송금을 건별로 중개하는 구조를 피할 수 있어(전자금융업/PG 등록
  이슈 회피), P2P 마켓플레이스 확장에도 안전하다.
- 1 크레딧 = 1원 고정. 별도 환전/발행 로직 없음 — 표시 단위만
  "크레딧"으로 부를 뿐 회계상 KRW 그대로다.

### 대체된 것

`payment_confirmation_service.py`(FD-18.5a/18.5b, 구매 건별 결제확인)는
완전히 삭제하고 `wallet_service.py`로 대체했다. 구매가 지갑 차감으로
즉시 정산되므로 `PENDING_PAYMENT` 중간 상태가 더는 발생하지 않는다 —
관리자 확인이 필요한 지점이 "구매 건"에서 "충전 요청"으로 옮겨갔다.

부수 효과: `dispute_resolution_service.py`가 "PG 미연동이라 환불 자금
이동은 스콥 밖"이라고 명시적으로 남겨뒀던 갭이 이번에 자연히 해소됐다
— `DELISTED_AND_REFUND` 결정 시 구매자 지갑으로 실제 환불 크레딧을
적립한다.

### 스키마

- `user_wallets(user_id PK, balance, updated_at)`
- `wallet_transactions(id, user_id, tx_type, amount, balance_after, related_purchase_id, created_at)`
  — `tx_type ∈ {TOPUP, PURCHASE_DEBIT, SALE_CREDIT, COMMISSION_CREDIT, REFUND}`
- `wallet_topup_requests(id, user_id, requested_amount, status, requested_at, confirmed_at, confirmed_by)`
- 예약 시스템 계정 `PLATFORM_HOUSE_USER_ID`(고정 UUID) — 커미션 수취 +
  §2의 PLATFORM 리스팅 판매자 역할을 겸한다.

구현: `src/services/wallet_service.py`, `src/db/migrations/versions/
e7f8a9b0c1d2_wallet_ledger.py`, `src/api/routers/wallet.py`. 상세 근거는
`wallet_service.py` 모듈 docstring 참조.

## §2. 판매자 이원화 — `seller_type`(다음 leaf)

`strategy_listings.seller_type ∈ {USER, PLATFORM}` 컬럼만 추가한다.
커미션 계산(`commission.py`)은 변경하지 않는다 — PLATFORM 리스팅도
`seller_user_id`에 `PLATFORM_HOUSE_USER_ID`를 그대로 채워 넣으므로,
`purchase_service.py`의 정산 로직(구매자 차감 → 판매자 정산 + 플랫폼
커미션 적립)이 분기 없이 동일하게 작동한다 — 판매대금 전액이 결과적으로
같은 하우스 지갑에 쌓일 뿐이다.

플랫폼이 직접 리스팅을 등록하는 경로(관리자 전용 API)는 기존
`ListingService.create_listing()`의 "본인 소유 전략만 리스팅 가능" 제약을
`seller_type == "PLATFORM"`일 때만 우회하도록 확장한다.

## §3. 고차원 전략 생성 — 마법사 + AI 프롬프트 병행(다음 leaf)

`condition_compiler.py`가 이미 담당하는 조건식 실행 엔진 자체는 건드리지
않는다 — 그 위에 "고차원 입력 → 조건식 JSON 자동 생성" 저작 계층만
추가한다. 결과물은 항상 기존 `StrategyDefinition`/조건 JSON이므로 실행
엔진·`ConditionRow`/`ConditionGroup` 프론트엔드 에디터는 그대로 재사용
(생성 결과를 검토·수정하는 화면으로 계속 쓰임 — 원래의 "직접 작성" 모드도
남겨둔다).

- **목표기반 마법사**: `POST /strategy-builder/wizard` — `{goal, riskTolerance,
  assetClasses}` 같은 몇 개 선택지를 코드로 사전 정의된 전략 템플릿
  테이블에 매핑해 조건식 JSON을 생성한다. AI 의존성이 없어 예측 가능하고
  구현이 먼저 가능하다.
- **자연어 AI 프롬프트**: `POST /strategy-builder/generate-from-prompt` —
  자유 텍스트를 Claude(Anthropic)에 전달해 구조화된 조건식 JSON을
  받는다(엄격한 스키마 강제 + Pydantic 검증 실패 시 재시도). **차단 요인**:
  DevEngine/AIOS 메모리 기준 Anthropic API 키는 유효하나 크레딧 $0 —
  크레딧 충전 전까지 이 엔드포인트는 501/명시적 "AI 생성 기능 일시
  비활성화"를 반환하도록 구현하고, 침묵 실패나 가짜 응답으로 위장하지
  않는다.

## 롤아웃 순서 (전부 완료)

1. ✅ §1 지갑 — `wallet_service.py`, `e7f8a9b0c1d2_wallet_ledger.py`
2. ✅ §2 `seller_type` + 플랫폼 리스팅 관리자 API —
   `f8a9b0c1d2e3_strategy_listings_seller_type.py`,
   `POST /admin/marketplace/platform-listings`
3. ✅ 프론트엔드 — `/wallet` 페이지, `WalletTopupsPage`(구
   `PendingPaymentsPage` 대체), `PlatformListingPage`, 마켓플레이스
   PLATFORM 배지·크레딧 단위 표시
4. ✅ §3 마법사 — `strategy_wizard_service.py`(3x3 템플릿, AI 없음),
   `StrategyWizardPanel.tsx`
5. ⏳ §3 자연어 프롬프트 — `strategy_prompt_service.py`가 501 스텁으로
   구현돼 있음(Anthropic 크레딧 충전 대기). 크레딧이 채워지면 이
   서비스 내부만 실제 Claude 호출로 교체하면 된다 — 라우터/스키마/
   프론트 UI는 이미 최종 형태.
