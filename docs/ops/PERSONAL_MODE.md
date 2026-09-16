# PERSONAL 모드 5분 안내 (U-15)

혼자 소액으로 직접 운용하는 1인 운영자를 위한 보수적 기본값 번들입니다.
설정 하나만 켜면 계좌를 지켜주는 안전장치들이 자동으로 작동합니다.

## 1. 이게 뭘 해주나요?

`personal-conservative` 번들을 켜면 다음이 자동으로 적용됩니다.

- **포지션당 상한**: 계좌 자본의 **2%**를 넘는 주문은 자동 거부됩니다.
- **일일 손실 자동 중단**: 오늘 손실이 계좌의 **3%**에 닿으면 모든 신규
  주문이 막히고(kill), 텔레그램으로 즉시 알림이 옵니다.
- **최대 노출 상한**: 보유 중인 모든 포지션 합이 계좌의 **30%**를 넘지
  않습니다.
- **거래소별 소액 상한**: 거래소당 1회 주문은 기본 **KRW 100만** 상당을
  넘지 못합니다(거래소마다 다르게 설정 가능).
- **신규 심볼 화이트리스트**: 처음 보는 심볼은 기본적으로 전부 거부됩니다
  (fail-closed) — 직접 거래할 심볼을 먼저 등록해야 합니다.

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
같은 스위치가 자동으로 켜집니다.

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
