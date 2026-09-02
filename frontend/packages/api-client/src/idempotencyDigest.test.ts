import { describe, expect, it } from "vitest";
import { checkDigest, computeBodyDigest, createIdempotencyDigestStore } from "./idempotencyDigest";

describe("checkDigest", () => {
  it("같은 키의 첫 호출은 'new'다", async () => {
    const store = createIdempotencyDigestStore();
    expect(await checkDigest("key-1", { a: 1 }, store)).toBe("new");
  });

  it("같은 키·같은 body(같은 digest)는 'replay'다", async () => {
    const store = createIdempotencyDigestStore();
    await checkDigest("key-1", { a: 1 }, store);
    expect(await checkDigest("key-1", { a: 1 }, store)).toBe("replay");
  });

  it("같은 키·키 순서만 다른 동일 body는 'replay'다(canonical 동치)", async () => {
    const store = createIdempotencyDigestStore();
    await checkDigest("key-1", { a: 1, b: 2 }, store);
    expect(await checkDigest("key-1", { b: 2, a: 1 }, store)).toBe("replay");
  });

  it("같은 키·다른 body는 'mismatch'다", async () => {
    const store = createIdempotencyDigestStore();
    await checkDigest("key-1", { a: 1 }, store);
    expect(await checkDigest("key-1", { a: 2 }, store)).toBe("mismatch");
  });

  it("mismatch는 throw하지 않는다", async () => {
    const store = createIdempotencyDigestStore();
    await checkDigest("key-1", { a: 1 }, store);
    await expect(checkDigest("key-1", { a: 2 }, store)).resolves.toBe("mismatch");
  });

  it("mismatch 이후에도 원래 digest가 유지되어 재전송은 계속 mismatch다", async () => {
    const store = createIdempotencyDigestStore();
    await checkDigest("key-1", { a: 1 }, store);
    await checkDigest("key-1", { a: 2 }, store);
    expect(await checkDigest("key-1", { a: 2 }, store)).toBe("mismatch");
    expect(await checkDigest("key-1", { a: 1 }, store)).toBe("replay");
  });

  it("다른 키는 서로 독립적이다", async () => {
    const store = createIdempotencyDigestStore();
    await checkDigest("key-1", { a: 1 }, store);
    expect(await checkDigest("key-2", { a: 999 }, store)).toBe("new");
  });
});

describe("computeBodyDigest", () => {
  it("64자 소문자 hex 형식이다", async () => {
    expect(await computeBodyDigest({ a: 1 })).toMatch(/^[0-9a-f]{64}$/);
  });

  it("키 순서만 다른 body는 동일한 digest를 만든다", async () => {
    const a = await computeBodyDigest({ x: 1, y: 2 });
    const b = await computeBodyDigest({ y: 2, x: 1 });
    expect(a).toBe(b);
  });
});
