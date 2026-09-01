// 거래소 코드(백엔드 식별자, "kis"/"bitget")는 그대로 두고 화면에 보여주는
// 이름만 사용자 친화적으로 바꾼다 — "kis"는 한국투자증권의 내부 코드일
// 뿐이라 화면에 그대로 노출하면 알아보기 어렵다.
const EXCHANGE_LABELS: Record<string, string> = {
  bitget: "Bitget",
  kis: "한국투자증권(KIS)",
};

export function exchangeLabel(exchange: string): string {
  return EXCHANGE_LABELS[exchange] ?? exchange;
}
