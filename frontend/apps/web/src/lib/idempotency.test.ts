import { describe, expect, it } from "vitest";
import {
  createIdempotencyKeyManager,
  generateIdempotencyKey,
  isValidIdempotencyKeyFormat,
} from "./idempotency";

describe("generateIdempotencyKey", () => {
  it("서버 헤더 형식(16~128자, [A-Za-z0-9_-])을 만족하는 키를 만든다", () => {
    const key = generateIdempotencyKey();
    expect(isValidIdempotencyKeyFormat(key)).toBe(true);
  });

  it("호출할 때마다 서로 다른 키를 만든다", () => {
    const a = generateIdempotencyKey();
    const b = generateIdempotencyKey();
    expect(a).not.toBe(b);
  });
});

describe("isValidIdempotencyKeyFormat", () => {
  it("15자 이하는 거부한다", () => {
    expect(isValidIdempotencyKeyFormat("a".repeat(15))).toBe(false);
  });

  it("128자 초과는 거부한다", () => {
    expect(isValidIdempotencyKeyFormat("a".repeat(129))).toBe(false);
  });

  it("허용되지 않은 문자(콜론 등)가 있으면 거부한다", () => {
    expect(isValidIdempotencyKeyFormat("abcdefgh:ijklmnop")).toBe(false);
  });

  it("16~128자의 영문/숫자/_/- 조합은 허용한다", () => {
    expect(isValidIdempotencyKeyFormat("abcdefgh-ijklmnop_123")).toBe(true);
  });
});

describe("createIdempotencyKeyManager", () => {
  it("같은 requestKey로 재시도하면 동일한 키를 재사용한다", () => {
    const manager = createIdempotencyKeyManager();
    const now = 1_000_000;

    const first = manager.getOrCreateKey("wallet.topup:req-1", now);
    const retry = manager.getOrCreateKey("wallet.topup:req-1", now + 1_000);

    expect(retry).toBe(first);
  });

  it("negative: 서로 다른 요청은 같은 키를 받지 않는다", () => {
    const manager = createIdempotencyKeyManager();
    const now = 1_000_000;

    const a = manager.getOrCreateKey("wallet.topup:req-1", now);
    const b = manager.getOrCreateKey("wallet.topup:req-2", now);

    expect(a).not.toBe(b);
  });

  it("negative: 만료된 키는 재사용되지 않고 새 키가 발급된다", () => {
    const ttlMs = 24 * 60 * 60 * 1000;
    const manager = createIdempotencyKeyManager(ttlMs);
    const now = 1_000_000;

    const original = manager.getOrCreateKey("marketplace.purchase:listing-42", now);
    const afterExpiry = manager.getOrCreateKey(
      "marketplace.purchase:listing-42",
      now + ttlMs + 1,
    );

    expect(afterExpiry).not.toBe(original);
  });

  it("만료 시각 정각(now === expiresAt)에는 만료된 것으로 취급한다", () => {
    const ttlMs = 1_000;
    const manager = createIdempotencyKeyManager(ttlMs);
    const now = 0;

    const original = manager.getOrCreateKey("executions.start:exec-1", now);
    const atExpiry = manager.getOrCreateKey("executions.start:exec-1", now + ttlMs);

    expect(atExpiry).not.toBe(original);
  });

  it("성공/최종실패 후 discardKey를 호출하면 다음 요청은 새 키를 받는다", () => {
    const manager = createIdempotencyKeyManager();
    const now = 1_000_000;

    const first = manager.getOrCreateKey("portfolio.rebalance:acct-1", now);
    manager.discardKey("portfolio.rebalance:acct-1");
    const afterDiscard = manager.getOrCreateKey("portfolio.rebalance:acct-1", now + 1);

    expect(afterDiscard).not.toBe(first);
  });

  it("peekKey는 발급 없이 조회만 하고, 없으면 null을 반환한다", () => {
    const manager = createIdempotencyKeyManager();
    const now = 1_000_000;

    expect(manager.peekKey("executions.create:new", now)).toBeNull();

    const issued = manager.getOrCreateKey("executions.create:new", now);
    expect(manager.peekKey("executions.create:new", now)).toBe(issued);
  });

  it("clear는 보관 중인 모든 키를 지운다", () => {
    const manager = createIdempotencyKeyManager();
    const now = 1_000_000;

    const before = manager.getOrCreateKey("wallet.topup:req-1", now);
    manager.clear();
    const after = manager.getOrCreateKey("wallet.topup:req-1", now);

    expect(after).not.toBe(before);
  });
});
