import { afterEach, describe, expect, it, vi } from "vitest";
import { checkDigest, computeBodyDigest, createIdempotencyDigestStore } from "./idempotencyDigest";
import { ApiClientBase } from "./http";

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

// task-1024: checkDigest를 http.ts의 postIdempotent/postEnvelopeIdempotent
// 공통 경로(httpIdempotent.ts)에 배선했다 — 개별 클라이언트(foundation.ts 등)가
// 아니라 ApiClientBase 자체에서 막히는지 여기서 직접 검증한다.
class DigestTestClient extends ApiClientBase {
  postIdempotent<T>(path: string, body: unknown, idempotencyKey?: string): Promise<T> {
    return super.postIdempotent(path, body, idempotencyKey);
  }

  postEnvelopeIdempotent<T>(path: string, body: unknown, idempotencyKey?: string): Promise<T> {
    return super.postEnvelopeIdempotent(path, body, idempotencyKey);
  }
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient(): DigestTestClient {
  return new DigestTestClient("https://api.example.test", () => null);
}

describe("postIdempotent/postEnvelopeIdempotent 공통 경로의 digest 선검증(task-1024)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("같은 키·다른 body는 네트워크 왕복 전에 ApiError(409 INTEGRITY_IDEMPOTENCY_CONFLICT)로 막는다", async () => {
    const key = "http-digest-mismatch-test-0001";
    const client = makeClient();
    const first = stubFetch({ ok: true });

    await client.postIdempotent("/v1/money/action", { amount: 1 }, key);
    expect(first).toHaveBeenCalledTimes(1);

    const second = stubFetch({ ok: true });
    await expect(client.postIdempotent("/v1/money/action", { amount: 2 }, key)).rejects.toMatchObject({
      statusCode: 409,
      errorCode: "INTEGRITY_IDEMPOTENCY_CONFLICT",
    });
    expect(second).not.toHaveBeenCalled();
  });

  it("같은 키·같은 body 재전송(replay)은 서버 왕복을 허용한다", async () => {
    const key = "http-digest-replay-test-0001";
    const client = makeClient();
    const body = { amount: 1 };

    stubFetch({ ok: true });
    await client.postIdempotent("/v1/money/action", body, key);

    const second = stubFetch({ ok: true });
    await expect(client.postIdempotent("/v1/money/action", body, key)).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("postEnvelopeIdempotent도 같은 키·다른 body를 왕복 전에 막는다", async () => {
    const key = "http-digest-envelope-mismatch-0001";
    const client = makeClient();
    const envelope = { data: { ok: true }, meta: { trace_id: "t-1", as_of: "2026-09-03T00:00:00Z", page: null } };

    stubFetch(envelope);
    await client.postEnvelopeIdempotent("/v1/money/envelope-action", { amount: 1 }, key);

    const second = stubFetch(envelope);
    await expect(
      client.postEnvelopeIdempotent("/v1/money/envelope-action", { amount: 2 }, key),
    ).rejects.toMatchObject({ statusCode: 409, errorCode: "INTEGRITY_IDEMPOTENCY_CONFLICT" });
    expect(second).not.toHaveBeenCalled();
  });

  it("다른 경로는 같은 키·다른 body라도 서로 독립적이다(스토어 키가 path를 포함)", async () => {
    const key = "http-digest-cross-route-test-0001";
    const client = makeClient();

    stubFetch({ ok: true });
    await client.postIdempotent("/v1/money/route-a", { amount: 1 }, key);

    const second = stubFetch({ ok: true });
    await expect(client.postIdempotent("/v1/money/route-b", { amount: 2 }, key)).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("키를 넘기지 않으면 매 호출마다 새 키가 생성돼 mismatch가 발생하지 않는다", async () => {
    const client = makeClient();

    stubFetch({ ok: true });
    await client.postIdempotent("/v1/money/action", { amount: 1 });

    const second = stubFetch({ ok: true });
    await expect(client.postIdempotent("/v1/money/action", { amount: 2 })).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
