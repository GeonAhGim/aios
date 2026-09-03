// spec §9 LC-16 — `GET /wallet/balance`가 기존 `balance`(레거시 투영)에
// `available`/`held`/`pendingPayout`(원장 진실)을 추가했다(MINOR, 프론트 무변경
// 통과 가능). 이 모듈은 그 응답을 소비하는 쪽의 판정 로직만 담당한다 — 구버전
// 서버(세 필드 없음)와 신버전 서버 양쪽에서 예외 없이 동작해야 한다.
//
// 금액은 어디서도 Number/parseFloat을 거치지 않는다 — Decimal 문자열을 그대로
// 들고 다니고, 비교·합산·표시 포맷팅 전부 문자열/BigInt 연산으로 처리한다
// (부동소수점 반올림으로 인한 잔액 오표시를 막기 위함).

export type WalletBalanceWarning = "NEGATIVE_AMOUNT" | "SUM_MISMATCH";

export interface WalletBalanceFull {
  mode: "full";
  userId: string;
  balance: string;
  available: string;
  held: string;
  pendingPayout: string;
  /** held > 0 — "주문 보류" 안내 노출 여부 */
  hasHold: boolean;
  /** available > 0 — 구매 가능 여부는 balance가 아니라 available로 판정한다 */
  canPurchase: boolean;
  warnings: WalletBalanceWarning[];
}

export interface WalletBalanceLegacy {
  mode: "legacy";
  userId: string;
  balance: string;
  canPurchase: boolean;
  warnings: WalletBalanceWarning[];
}

export interface WalletBalanceInvalid {
  mode: "invalid";
  reason: string;
}

export type ParsedWalletBalance = WalletBalanceFull | WalletBalanceLegacy | WalletBalanceInvalid;

interface DecimalParts {
  negative: boolean;
  intPart: string;
  fracPart: string;
}

const DECIMAL_STRING_RE = /^(-)?(\d+)(?:\.(\d+))?$/;

function parseDecimalParts(value: unknown): DecimalParts | null {
  if (typeof value !== "string") return null;
  const match = DECIMAL_STRING_RE.exec(value);
  if (!match) return null;
  return { negative: match[1] === "-", intPart: match[2], fracPart: match[3] ?? "" };
}

function isZeroParts(parts: DecimalParts): boolean {
  return /^0+$/.test(parts.intPart) && (parts.fracPart === "" || /^0+$/.test(parts.fracPart));
}

function isNegativeParts(parts: DecimalParts): boolean {
  return parts.negative && !isZeroParts(parts);
}

function isPositiveParts(parts: DecimalParts): boolean {
  return !parts.negative && !isZeroParts(parts);
}

function toScaledBigInt(parts: DecimalParts, scale: number): bigint {
  const magnitude = BigInt(parts.intPart + parts.fracPart.padEnd(scale, "0"));
  return parts.negative ? -magnitude : magnitude;
}

// balance === available + held + pendingPayout 를 부동소수점 없이 정확히 비교한다.
// 넷 중 하나라도 형식이 잘못됐으면 호출부(parseWalletBalance)가 이미 걸러낸 뒤이므로
// null을 반환하지 않고 항상 boolean을 낸다.
function decimalSumEquals(total: DecimalParts, addends: DecimalParts[]): boolean {
  const scale = Math.max(total.fracPart.length, ...addends.map((p) => p.fracPart.length));
  const totalScaled = toScaledBigInt(total, scale);
  const sumScaled = addends.reduce((acc, p) => acc + toScaledBigInt(p, scale), 0n);
  return totalScaled === sumScaled;
}

// 문자열 그대로 천단위 구분자만 붙인다(Number 변환 없음 — 정수부 자릿수가 아무리
// 커도, 소수부가 아무리 길어도 원본 자릿수가 그대로 보존된다).
export function formatCreditAmount(value: string): string {
  const parts = parseDecimalParts(value);
  if (!parts) return value;
  const grouped = parts.intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const sign = isNegativeParts(parts) ? "-" : "";
  return parts.fracPart ? `${sign}${grouped}.${parts.fracPart}` : `${sign}${grouped}`;
}

export function parseWalletBalance(raw: unknown): ParsedWalletBalance {
  if (typeof raw !== "object" || raw === null) {
    return { mode: "invalid", reason: "응답이 객체가 아닙니다" };
  }
  const obj = raw as Record<string, unknown>;

  const userId = obj.userId;
  if (typeof userId !== "string" || userId.length === 0) {
    return { mode: "invalid", reason: "userId 필드가 없습니다" };
  }

  const balanceParts = parseDecimalParts(obj.balance);
  if (!balanceParts) {
    return { mode: "invalid", reason: "balance 필드가 올바른 금액 형식이 아닙니다" };
  }
  const balance = obj.balance as string;

  const hasAvailable = obj.available !== undefined;
  const hasHeld = obj.held !== undefined;
  const hasPendingPayout = obj.pendingPayout !== undefined;

  if (!hasAvailable && !hasHeld && !hasPendingPayout) {
    // 구버전 서버 폴백 — LC-16 이전 응답 그대로. balance만으로 동작한다.
    const warnings: WalletBalanceWarning[] = [];
    if (isNegativeParts(balanceParts)) warnings.push("NEGATIVE_AMOUNT");
    return { mode: "legacy", userId, balance, canPurchase: isPositiveParts(balanceParts), warnings };
  }

  if (!hasAvailable || !hasHeld || !hasPendingPayout) {
    return { mode: "invalid", reason: "available/held/pendingPayout 중 일부만 존재합니다" };
  }

  const availableParts = parseDecimalParts(obj.available);
  const heldParts = parseDecimalParts(obj.held);
  const pendingParts = parseDecimalParts(obj.pendingPayout);
  if (!availableParts || !heldParts || !pendingParts) {
    return { mode: "invalid", reason: "available/held/pendingPayout 금액 형식이 올바르지 않습니다" };
  }

  const warnings: WalletBalanceWarning[] = [];
  if (
    isNegativeParts(balanceParts) ||
    isNegativeParts(availableParts) ||
    isNegativeParts(heldParts) ||
    isNegativeParts(pendingParts)
  ) {
    warnings.push("NEGATIVE_AMOUNT");
  }
  // 불일치는 조용히 감추지 않는다 — 서버가 이미 드리프트를 409로 막지만(§9 LC-16),
  // 프론트는 그 계약을 신뢰하지 않고 스스로도 검증해 경고 상태로 표기한다.
  if (!decimalSumEquals(balanceParts, [availableParts, heldParts, pendingParts])) {
    warnings.push("SUM_MISMATCH");
  }

  return {
    mode: "full",
    userId,
    balance,
    available: obj.available as string,
    held: obj.held as string,
    pendingPayout: obj.pendingPayout as string,
    hasHold: isPositiveParts(heldParts),
    canPurchase: isPositiveParts(availableParts),
    warnings,
  };
}
