import { afterEach, describe, expect, it, vi } from "vitest";
import { resolvePath } from "../apiPaths";
import { ApiClientBase } from "../http";
import { withFoundation } from "./foundation";
import type { RequestPaperDeploymentBody } from "./foundation";

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

// task-1309: paper_control.py/trust.py 원본 확인 — 두 라우터 모두 처음부터
// `-> ApiResponse[T]`(ok())로만 응답한다. 이 테스트 파일의 fetch 스텁도 실제
// 서버가 보내는 {data, meta} 봉투 모양으로 맞춘다(이전엔 data를 그대로 top-level에
// 둬 응답 파싱 자체를 검증하지 못했다 — postIdempotent였다면 통과했겠지만
// postEnvelopeIdempotent로는 unwrap이 실패해 이 어긋남이 바로 드러난다).
function envelope<T>(data: T): { data: T; meta: { trace_id: string; as_of: string; page: null } } {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-04T00:00:00Z", page: null } };
}

// spec §3.7 적용 대상: POST /v1/foundation/paper-control/*(5개, 실제 마운트
// 경로는 /v1/foundation/paper-deployments — src/api/routers 원본 확인)와
// POST /v1/foundation/trust/consents.
describe("withFoundation: paper-deployments 5개 + trust/consents", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listPaperDeployments: GET으로 조회하고 봉투(data.deployments/asOf)를 풀어 camelCase로 반환한다", async () => {
    const asOf = "2026-09-04T00:00:00Z";
    stubFetch(envelope({ deployments: [deploymentView], as_of: asOf }));

    const result = await makeClient().listPaperDeployments();

    expect(result).toEqual({
      deployments: [
        {
          id: "d1",
          packageRef: "pkg-1",
          connectionId: null,
          state: "REQUESTED",
          fenceToken: 1,
          createdAt: null,
          updatedAt: null,
          schemaVersion: "v1",
        },
      ],
      asOf,
    });
  });

  it("requestPaperDeployment: postEnvelopeIdempotent로 헤더를 싣고, 전환기 규칙대로 body에도 idempotencyKey를 alias한다", async () => {
    const fetchMock = stubFetch(envelope(deploymentView), 201);

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
    const fetchMock = stubFetch(envelope(deploymentView));

    const client = makeClient() as unknown as Record<string, (...args: unknown[]) => Promise<unknown>>;
    await client[method]("dep-1", `caller-supplied-key-${action}`);

    const { url, init } = requestOf(fetchMock);
    expect(url).toBe(`https://api.example.test/v1/foundation/paper-deployments/dep-1:${action}`);
    expect(idempotencyKeyHeader(init)).toBe(`caller-supplied-key-${action}`);
    const body = JSON.parse(init.body as string);
    expect(body.idempotency_key).toBe(`caller-supplied-key-${action}`);
  });

  it("acceptTrustConsent: postEnvelopeIdempotent로 헤더만 싣는다(body에는 alias할 기존 idempotency 필드가 없음)", async () => {
    const fetchMock = stubFetch(envelope(consentView), 201);

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
    stubFetch(envelope(deploymentView));
    const client = makeClient();

    // @ts-expect-error idempotencyKey는 필수 인자다 — 누락 시 타입 에러.
    await expect(client.requestPaperDeployment({ packageRef: "p", adapterType: "a", providerSandboxAccountRef: "r" })).rejects.toThrow();
  });

  it("빈 문자열 키는 형식 검증에서 런타임 거부된다(서버 왕복 없음)", async () => {
    const fetchMock = stubFetch(envelope(deploymentView));
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
    const key = "key-same-mismatch-fixture";
    const first = stubFetch(envelope(deploymentView), 201);
    const client = makeClient();

    await client.requestPaperDeployment(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      key,
    );
    expect(first).toHaveBeenCalledTimes(1);

    const second = stubFetch(envelope(deploymentView), 201);
    await expect(
      client.requestPaperDeployment(
        { packageRef: "pkg-b", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
        key,
      ),
    ).rejects.toThrow(/이전과 다른 요청 본문/);
    expect(second).not.toHaveBeenCalled();
  });

  it("같은 키·같은 body 재전송(replay)은 서버 왕복을 허용한다", async () => {
    const key = "key-same-replay-fixture";
    const client = makeClient();
    const body = { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" };

    stubFetch(envelope(deploymentView), 201);
    await client.requestPaperDeployment(body, key);

    const second = stubFetch(envelope(deploymentView), 201);
    await expect(client.requestPaperDeployment(body, key)).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("acceptTrustConsent: 같은 키·다른 body는 서버 왕복 전에 차단한다", async () => {
    const key = "key-trust-mismatch-fixture";
    const client = makeClient();

    stubFetch(envelope(consentView), 201);
    await client.acceptTrustConsent({ purpose: "trading", disclosureRevision: 1 }, key);

    const second = stubFetch(envelope(consentView), 201);
    await expect(
      client.acceptTrustConsent({ purpose: "trading", disclosureRevision: 2 }, key),
    ).rejects.toThrow(/이전과 다른 요청 본문/);
    expect(second).not.toHaveBeenCalled();
  });

  it("paper-deployments와 trust/consents가 우연히 같은 키를 써도 라우트별로 독립적으로 취급한다", async () => {
    const key = "key-cross-route-shared-fixture";
    const client = makeClient();

    stubFetch(envelope(deploymentView), 201);
    await client.requestPaperDeployment(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      key,
    );

    const second = stubFetch(envelope(consentView), 201);
    await expect(client.acceptTrustConsent({ purpose: "trading", disclosureRevision: 1 }, key)).resolves.toBeDefined();
    expect(second).toHaveBeenCalledTimes(1);
  });
});

// DEEPEN task-3185(docs/audit/DEPTH_PLT.md #1309): 기존 스위트는 negative·replay
// 증거는 충분했지만(D3급 replay 증명 有) 수치 성능 단언과 게이트 적색 재현이
// 없어 D2 하한(ADR-2026-09-09-C, PLT축은 안전축 목록 밖이라 D3 불요)을
// 채우지 못했다. 이 블록에서 두 결함을 각각 보강한다.
describe("DEEPEN 3185: 수치 성능 단언 + 게이트 적색 재현", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // 수치 성능: guardIdempotentBody(digest 선검증, httpIdempotent.ts)는 같은
  // 키·다른 body를 fetch 호출 전에 거부한다. 그 "서버 왕복 전 차단"이 실제로
  // 네트워크 지연을 기다리지 않는지 카운트가 아닌 실측 wall-clock ms로
  // 증명한다 — 두 번째 fetch를 일부러 실제 네트워크 지연(200ms, real timer)으로
  // 스텁해도 거부가 그 지연의 절반 미만에서 끝나야 guard가 fetch 이전에
  // 단락(short-circuit)됐다고 볼 수 있다.
  it("수치 성능: 같은 키·다른 body 거부는 네트워크 왕복(200ms)을 기다리지 않고 그 절반 미만에서 즉시 실패한다", async () => {
    const NETWORK_LATENCY_MS = 200;
    const key = "key-perf-mismatch-fixture";
    const client = makeClient();

    stubFetch(envelope(deploymentView), 201);
    await client.requestPaperDeployment(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      key,
    );

    const slowFetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          setTimeout(() => resolve(jsonResponse(201, envelope(deploymentView))), NETWORK_LATENCY_MS);
        }),
    );
    vi.stubGlobal("fetch", slowFetch);

    const start = performance.now();
    await expect(
      client.requestPaperDeployment(
        { packageRef: "pkg-b", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
        key,
      ),
    ).rejects.toThrow(/이전과 다른 요청 본문/);
    const elapsedMs = performance.now() - start;

    expect(slowFetch).not.toHaveBeenCalled();
    expect(elapsedMs).toBeLessThan(NETWORK_LATENCY_MS / 2);
  });

  // 게이트 적색 재현: foundation.ts 101-105행 주석이 명시하는 실제 이력상
  // 버그 — "이전 리프가 postIdempotent를 잘못 골라 응답 봉투를 그대로
  // 반환"했다. postEnvelopeIdempotent 대신 postIdempotent를 쓰는 경로를
  // 로컬로 재현해, 그 결함이 있으면 camelCase 필드가 비고 봉투가 그대로
  // 새는지(적색)와 실제 구현은 정상 언랩되는지(녹색)를 대조한다.
  class BuggyFoundationClient extends ApiClientBase {
    async requestPaperDeploymentBuggy(body: RequestPaperDeploymentBody, idempotencyKey: string): Promise<unknown> {
      const outgoing = { ...body, idempotencyKey };
      return this.postIdempotent(resolvePath("foundation.paperDeployments.request"), outgoing, idempotencyKey);
    }
  }

  it("게이트 적색 재현: postIdempotent를 잘못 쓰면 응답 봉투가 그대로 새어나와 packageRef가 undefined다(적색) vs 실제 구현은 정상 언랩한다(녹색)", async () => {
    stubFetch(envelope(deploymentView), 201);
    const buggyClient = new BuggyFoundationClient("https://api.example.test", () => null);

    const buggyResult = (await buggyClient.requestPaperDeploymentBuggy(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      "buggy-key-0000000000001",
    )) as { data?: { packageRef?: string }; packageRef?: string };

    // 적색: 언랩되지 않은 봉투({data, meta})가 그대로 반환돼 최상위 packageRef가
    // 없다 — 값은 한 겹 안(data.packageRef, keysToCamel은 재귀 변환이라 여기도
    // camelCase다)에 숨어 있다.
    expect(buggyResult.packageRef).toBeUndefined();
    expect(buggyResult.data?.packageRef).toBe("pkg-1");

    stubFetch(envelope(deploymentView), 201);
    const goodResult = await makeClient().requestPaperDeployment(
      { packageRef: "pkg-a", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
      "good-key-00000000000001",
    );

    // 녹색: 실제 구현(postEnvelopeIdempotent)은 정상 언랩해 packageRef가 있다.
    expect(goodResult.packageRef).toBe("pkg-1");
  });
});

// DEPTH_PLT(task-3139)가 원 task-493(5f7c00b)를 D2 축 하한 미달(D1)로 판정 —
// stubFetch가 항상 정상 응답만 흉내내 네트워크 자체가 끊기거나 응답이 전송
// 중 깨지는 실제 결함 클래스를 다루지 않았다. scripts.test.ts/marketData.test.ts
// DEEPEN과 동일 기법을 그대로 따른다.
describe("failure-injection — 실 어댑터/네트워크 결함 시뮬레이션", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("negative: 네트워크 완전 단절(fetch 자체가 reject)이면 ApiError로 재분류하지 않고 원본 예외를 그대로 던진다", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .requestPaperDeployment(
        { packageRef: "pkg-1", adapterType: "bitget-sandbox", providerSandboxAccountRef: "acct-1" },
        "caller-supplied-key-neta",
      )
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(TypeError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("negative: 네트워크 타임아웃(AbortError)도 재시도 없이 원본 예외를 그대로 던진다(POST는 withGetRetry 대상이 아님)", async () => {
    const timeoutError = new Error("The operation was aborted due to timeout");
    timeoutError.name = "AbortError";
    const fetchMock = vi.fn().mockRejectedValue(timeoutError);
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .startPaperDeployment("dep-1", "caller-supplied-key-netb")
      .catch((e: unknown) => e as Error);

    expect(err.name).toBe("AbortError");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("negative: 응답 바디가 전송 중 잘린 깨진 JSON(실 어댑터 결함 — 에러 봉투가 아니라 파싱 자체가 불가)이면 SyntaxError를 그대로 던진다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('{"data": {"consent_id":', {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const err = await makeClient()
      .acceptTrustConsent({ purpose: "trading", disclosureRevision: 1 }, "caller-supplied-key-netc")
      .catch((e: unknown) => e);

    expect(err).toBeInstanceOf(SyntaxError);
  });

  it("negative: 응답 바디가 완전히 빈 문자열(연결이 중간에 끊긴 실 결함)이면 봉투 형식 위반으로 던지고 빈 값으로 뭉개지 않는다", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(makeClient().listPaperDeployments()).rejects.toThrow(/봉투 형식/);
  });
});
