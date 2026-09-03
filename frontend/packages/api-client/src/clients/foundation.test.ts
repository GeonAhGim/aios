import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClientBase } from "../http";
import { withFoundation } from "./foundation";

class FoundationTestClient extends withFoundation(ApiClientBase) {}

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

function makeClient(): FoundationTestClient {
  return new FoundationTestClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return { url, init };
}

function idempotencyKeyHeader(init: RequestInit): string | null {
  return new Headers(init.headers).get("Idempotency-Key");
}

const deploymentView = {
  id: "d1",
  package_ref: "pkg-1",
  connection_id: null,
  state: "REQUESTED",
  fence_token: 1,
  created_at: null,
  updated_at: null,
  schema_version: "v1",
};

const consentView = {
  consent_id: "c1",
  tenant_id: "t1",
  purpose: "trading",
  disclosure_id: "disc-1",
  disclosure_revision: 1,
  state: "ACTIVE",
  accepted_at: "2026-09-03T00:00:00Z",
  revoked_at: null,
  expires_at: null,
  schema_version: "v1",
};

// spec §3.7 적용 대상: POST /v1/foundation/paper-control/*(5개, 실제 마운트
// 경로는 /v1/foundation/paper-deployments — src/api/routers 원본 확인)와
// POST /v1/foundation/trust/consents.
describe("withFoundation: paper-deployments 5개 + trust/consents", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("requestPaperDeployment: postIdempotent로 헤더를 싣고, 전환기 규칙대로 body에도 idempotencyKey를 alias한다", async () => {
    const fetchMock = stubFetch(deploymentView, 201);

    await makeClient().requestPaperDeployment(
      {
        packageRef: "pkg-1",
        adapterType: "bitget-sandbox",
        providerSandboxAccountRef: "acct-1",
      },
      "caller-supplied-key-0001",
    );

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/paper-deployments");
    expect(idempotencyKeyHeader(init)).toBe("caller-supplied-key-0001");
    const body = JSON.parse(init.body as string);
    expect(body.idempotency_key).toBe("caller-supplied-key-0001");
    expect(body.package_ref).toBe("pkg-1");
  });

  it.each([
    ["startPaperDeployment", "start"],
    ["resumePaperDeployment", "resume"],
    ["pausePaperDeployment", "pause"],
    ["stopPaperDeployment", "stop"],
  ] as const)("%s: :%s 경로로 헤더+body(alias)를 함께 싣는다", async (method, action) => {
    const fetchMock = stubFetch(deploymentView);

    const client = makeClient() as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>;
    await client[method]("dep-1", `caller-supplied-key-${action}`);

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe(`https://api.example.test/v1/foundation/paper-deployments/dep-1:${action}`);
    expect(idempotencyKeyHeader(init)).toBe(`caller-supplied-key-${action}`);
    const body = JSON.parse(init.body as string);
    expect(body.idempotency_key).toBe(`caller-supplied-key-${action}`);
  });

  it("acceptTrustConsent: postIdempotent로 헤더만 싣는다(body에는 alias할 기존 idempotency 필드가 없음)", async () => {
    const fetchMock = stubFetch(consentView, 201);

    await makeClient().acceptTrustConsent(
      { purpose: "trading", disclosureRevision: 1 },
      "caller-supplied-key-0002",
    );

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/trust/consents");
    expect(idempotencyKeyHeader(init)).toBe("caller-supplied-key-0002");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ purpose: "trading", disclosure_revision: 1 });
  });

  it("키 없이 호출하면 런타임에서 거부한다(타입은 idempotencyKey를 필수 인자로 강제 — 컴파일 타임 방어)", async () => {
    stubFetch(deploymentView);
    const client = makeClient();

    // @ts-expect-error idempotencyKey는 필수 인자다 — 누락 시 타입 에러.
    await expect(client.requestPaperDeployment({ packageRef: "p", adapterType: "a", providerSandboxAccountRef: "r" })).rejects.toThrow();
  });

  it("빈 문자열 키는 형식 검증에서 런타임 거부된다(서버 왕복 없음)", async () => {
    const fetchMock = stubFetch(deploymentView);
    const client = makeClient();

    await expect(
      client.requestPaperDeployment(
        { packageRef: "p", adapterType: "a", providerSandboxAccountRef: "r" },
        "",
      ),
    ).rejects.toThrow(/Idempotency-Key/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("같은 키·다른 body면 서버 왕복 전에 차단한다(task-427 checkDigest 재사용)", async () => {
    const key = "same-key-mismatch-test-01";
    const first = stubFetch(deploymentView, 201);
    const client = makeClient();

    await client.requestPaperDeployment(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      key,
    );
    expect(first).toHaveBeenCalledTimes(1);

    const second = stubFetch(deploymentView, 201);
    await expect(
      client.requestPaperDeployment(
        { packageRef: "pkg-b", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
        key,
      ),
    ).rejects.toThrow(/이전과 다른 요청 본문/);
    expect(second).not.toHaveBeenCalled();
  });

  it("같은 키·같은 body 재전송(replay)은 서버 왕복을 허용한다", async () => {
    const key = "same-key-replay-test-01";
    const client = makeClient();
    const body = { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" };

    stubFetch(deploymentView, 201);
    await client.requestPaperDeployment(body, key);

    const second = stubFetch(deploymentView, 201);
    await expect(client.requestPaperDeployment(body, key)).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("acceptTrustConsent: 같은 키·다른 body는 서버 왕복 전에 차단한다", async () => {
    const key = "trust-consent-mismatch-test-01";
    const client = makeClient();

    stubFetch(consentView, 201);
    await client.acceptTrustConsent({ purpose: "trading", disclosureRevision: 1 }, key);

    const second = stubFetch(consentView, 201);
    await expect(
      client.acceptTrustConsent({ purpose: "trading", disclosureRevision: 2 }, key),
    ).rejects.toThrow(/이전과 다른 요청 본문/);
    expect(second).not.toHaveBeenCalled();
  });

  it("paper-deployments와 trust/consents가 우연히 같은 키를 써도 라우트별로 독립적으로 취급한다", async () => {
    const key = "cross-route-shared-key-0001-abcdefgh";
    const client = makeClient();

    stubFetch(deploymentView, 201);
    await client.requestPaperDeployment(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      key,
    );

    const second = stubFetch(consentView, 201);
    await expect(client.acceptTrustConsent({ purpose: "trading", disclosureRevision: 1 }, key)).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
