# PERSONAL 모드 5분 안내 (U-15)

혼자 소액으로 직접 운용하는 1인 운영자를 위한 보수적 기본값 번들입니다.

> **task-3986 배선 상태 요약**: QA(task-3819)가 이 번들이 실주문 경로 어디
> 에도 배선되지 않아 "한도 초과 주문을 하나도 막지 못한다"는 결함을
> 지적했다. 지금은 실제 `PreSubmitGate`(`src/services/order_service/
> foundation_gate.py`) 4층으로 배선됐고, `.env`의 `PERSONAL_MODE_ACCOUNT_ID`
> 로 지정한 **그 계정에만** 적용된다(다른 계정은 전역으로 영향받지 않는다,
> §1-A). kill 스위치 차단은 즉시·자동이지만, 숫자 한도(포지션당/노출/소액
> 상한/화이트리스트) 4가지는 주문을 넣는 호출부가 그 순간의 계좌 자본·노출액
> 을 직접 계산해 넘겨줘야 평가된다 — 이 실시간 계산을 자동으로 해 주는 호출
> 부는 아직 없다(§1-B, 별도 리프).

## 1. 이게 뭘 해주나요?

`personal-conservative` 번들을 켜면 다음이 적용됩니다.

- **일일 손실 자동 중단(kill)**: 오늘 손실이 계좌의 **3%**에 닿으면(또는
  대시보드에서 수동으로) kill 스위치가 켜지고, 그 뒤로 스코프된 계정의
  신규 주문은 숫자 계산 없이도 **즉시** 전부 막힙니다(텔레그램 알림 동반).
  이 부분은 자동으로 이미 작동합니다.
- **포지션당 상한(계좌 자본의 2%)**·**최대 노출 상한(30%)**·**거래소별
  소액 상한(기본 KRW 100만)**·**신규 심볼 화이트리스트**(비어 있으면
  전부 거부, fail-closed): 이 4가지는 주문 제출부가 그 순간의 계좌 자본·
  노출액·주문 명목가(KRW 환산)를 `PersonalOrderRiskSnapshot`으로 직접
  계산해 `submit_order(personal_risk_snapshot=...)`에 넘겨야 평가됩니다
  (§1-B).

### 1-A. 이 계정에만 적용되게 켜기

`.env`의 `PERSONAL_MODE_ACCOUNT_ID`에 운영자 본인의 계정/테넌트 `user_id`
(UUID)를 넣으면, 그 계정으로 들어오는 주문에만 4층이 평가됩니다. 비어
있으면(기본값) 어떤 계정도 영향받지 않습니다 — 다른 MVP-1 계정의 기존
주문 흐름을 깨지 않기 위한 전역 강제 금지 설계입니다.

### 1-B. 알려진 한계 — 숫자 한도는 아직 자동이 아님

계좌 자본(KRW 환산 equity)·현재 노출액을 실시간으로 계산해 주는 소스가
코드베이스에 아직 없습니다(`positions.NavRepository`는 일별 배치 스냅샷,
`daily_report.py`의 실현손익도 호출자가 직접 넘기는 값 — 같은 종류의 기존
공백). 그래서 `submit_order()`를 부르는 실행 루프 쪽이 아직 이 스냅샷을
채워 넣지 않는 한, 위 4가지 숫자 한도는 평가되지 않고 통과합니다(kill
스위치 차단만 항상 작동). 실시간 소스 연결은 별도 리프로 남아 있습니다.

## 2. 설정 파일

`config/risk_policy/personal-conservative.yaml`에서 숫자를 조정할 수
있습니다. 특히 `symbol_whitelist`는 비워두면 아무 심볼도 거래되지
않으니, 실제 운용 전에 거래할 심볼을 반드시 추가하세요.

```yaml
symbol_whitelist: ["BTC/USDT", "ETH/USDT"]
exchange_notional_caps:
  bitget: 500000  # 이 거래소만 KRW 50만으로 더 낮게
```

## 3. 텔레그램 알림 켜기

1. 텔레그램에서 `@BotFather`에게 `/newbot`으로 봇을 만들고 토큰을
   받습니다.
2. 만든 봇을 알림 받을 채팅방(개인 대화 또는 그룹)에 추가합니다.
3. `.env`에 다음 두 값을 채웁니다.
   ```
   TELEGRAM_BOT_TOKEN=<봇토큰>
   TELEGRAM_CHAT_ID=<채팅방ID>
   ```
4. 비워두면 알림이 발송되지 않고 실패로만 기록됩니다(성공으로 위장하지
   않습니다) — 실수로 알림이 꺼진 채 운용하는 상황을 막기 위한 설계입니다.

체결·한도위반·kill, 세 가지 상황에서 알림이 옵니다.

## 4. 대시보드 kill 버튼

`POST /v1/foundation/personal/kill`을 호출하면(대시보드 버튼과 연결)
즉시 kill 스위치가 켜지고 텔레그램 알림이 갑니다. 현재 상태는
`GET /v1/foundation/personal/kill`로 확인합니다. 일일 손실 한도 초과 시에도
같은 스위치가 자동으로 켜집니다. `PERSONAL_MODE_ACCOUNT_ID`로 스코프된
계정이라면(§1-A), 켜지는 즉시 그 계정의 신규 주문이 `PreSubmitGate`
4층에서 실제로 거부됩니다(task-3986 이전에는 이 버튼이 상태만 기록할 뿐
실주문을 막지 못했습니다).

## 5. PAPER → LIVE 승격

소액이라도 실제 돈이 오가는 LIVE로 넘어가려면 아래 네 가지가 전부 필요
합니다(`GET /v1/foundation/personal/promotion-checklist`로 확인 가능,
하나라도 빠지면 `POST /v1/foundation/personal/promote`가 거부됩니다):

1. ADR-2026-08-29-E 조건 2(실계좌에 MFA + 이중승인 운영 적용) — 준비되면
   `.env`의 `PERSONAL_ADR_0829E_CONDITION2_MET=true`로 직접 켭니다.
2. `personal-conservative` 번들이 활성 상태일 것.
3. PAPER 모드로 **최소 7일** 운영했을 것.
4. 그 7일 동안 한도 위반이 **한 건도** 없었을 것.

넷 중 하나라도 빠지면 승격 요청은 403(`POLICY_LIVE_BLOCKED`)으로
거부됩니다 — 조건을 억지로 우회할 방법은 없습니다.
