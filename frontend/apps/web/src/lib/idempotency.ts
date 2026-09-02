// 금전 라우트(구매·충전·주문 등)는 Idempotency-Key 헤더 규격을 하나로 통일한다
// (docs/specs/L4_platform_observability_tenancy_api_v1.0.md §3.7, §9 PLT-14/PLT-15).
// 서버 검증 규칙: 16~128자, [A-Za-z0-9_-]. 이 모듈은 그 형식을 만족하는 키를
// "요청 1건당 1회" 생성하고, 재시도 시에는 만료 전까지 같은 키를 돌려주며,
// 성공/최종실패 후에는 discardKey로 폐기해 다음 요청이 새 키를 받게 한다.

const HEADER_KEY_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

// 서버 core/idempotency.py M2 마이그레이션의 idempotency_keys.expires_at 기본값(now()+24h)과 맞춘다.
export const DEFAULT_IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000;

export interface IdempotencyKeyEntry {
  key: string;
  createdAt: number;
  expiresAt: number;
}

export interface IdempotencyKeyManager {
  /** requestKey에 대해 유효한 키가 있으면 재사용하고, 없거나 만료됐으면 새로 발급한다. */
  getOrCreateKey(requestKey: string, now?: number): string;
  /** 성공/최종실패 후 호출 — 폐기하지 않으면 만료 전까지 같은 requestKey가 같은 키를 계속 재사용한다. */
  discardKey(requestKey: string): void;
  /** 발급 없이 현재 유효한 키만 조회한다. 없거나 만료면 null. */
  peekKey(requestKey: string, now?: number): string | null;
  /** 보관 중인 모든 키를 지운다(테스트/로그아웃 등 전체 초기화용). */
  clear(): void;
}

function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  const cryptoObj = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (cryptoObj && typeof cryptoObj.getRandomValues === "function") {
    cryptoObj.getRandomValues(bytes);
    return bytes;
  }
  for (let i = 0; i < length; i += 1) {
    bytes[i] = Math.floor(Math.random() * 256);
  }
  return bytes;
}

function fallbackUuidV4(): string {
  const bytes = randomBytes(16);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
  return [
    hex.slice(0, 4).join(""),
    hex.slice(4, 6).join(""),
    hex.slice(6, 8).join(""),
    hex.slice(8, 10).join(""),
    hex.slice(10, 16).join(""),
  ].join("-");
}

/** crypto.randomUUID가 없는 환경(구형 브라우저·일부 테스트 러너)을 위한 폴백 포함. */
export function generateIdempotencyKey(): string {
  const cryptoObj = typeof globalThis !== "undefined" ? globalThis.crypto : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  return fallbackUuidV4();
}

/** 서버가 요구하는 Idempotency-Key 헤더 형식(16~128자, [A-Za-z0-9_-])인지 검사한다. */
export function isValidIdempotencyKeyFormat(key: string): boolean {
  return HEADER_KEY_PATTERN.test(key);
}

function isExpired(entry: IdempotencyKeyEntry, now: number): boolean {
  return now >= entry.expiresAt;
}

/**
 * requestKey(라우트+대상을 식별하는 문자열, 예: "marketplace.purchase:listing-42")별로
 * Idempotency-Key를 관리한다. 전역 싱글턴을 두지 않고 팩토리로 제공해 화면/훅 단위로
 * 독립된 인스턴스를 쓸 수 있게 한다.
 */
export function createIdempotencyKeyManager(
  ttlMs: number = DEFAULT_IDEMPOTENCY_TTL_MS,
): IdempotencyKeyManager {
  const entries = new Map<string, IdempotencyKeyEntry>();

  function getOrCreateKey(requestKey: string, now: number = Date.now()): string {
    const existing = entries.get(requestKey);
    if (existing && !isExpired(existing, now)) {
      return existing.key;
    }
    const entry: IdempotencyKeyEntry = {
      key: generateIdempotencyKey(),
      createdAt: now,
      expiresAt: now + ttlMs,
    };
    entries.set(requestKey, entry);
    return entry.key;
  }

  function peekKey(requestKey: string, now: number = Date.now()): string | null {
    const existing = entries.get(requestKey);
    if (!existing || isExpired(existing, now)) return null;
    return existing.key;
  }

  function discardKey(requestKey: string): void {
    entries.delete(requestKey);
  }

  function clear(): void {
    entries.clear();
  }

  return { getOrCreateKey, discardKey, peekKey, clear };
}
