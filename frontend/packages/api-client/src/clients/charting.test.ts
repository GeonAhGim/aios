import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
import { keysToCamel } from "../caseConvert";
import { ApiError } from "../httpErrors";
import { createChartingClient } from "./charting";

function stubFetch(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function makeClient() {
  return createChartingClient("https://api.example.test", () => null);
}

function requestOf(fetchMock: ReturnType<typeof vi.fn>, callIndex = 0): [string, RequestInit] {
  return fetchMock.mock.calls[callIndex] as [string, RequestInit];
}

const layoutView = {
  id: "layout-1",
  tenant_id: "tenant-1",
  owner_subject_id: "owner-1",
  name: "My layout",
  layout_state: { schema_version: 1, panels: [], active_panel_id: null, watchlists: [] },
  revision: 0,
  created_at: "2026-09-06T00:00:00Z",
  updated_at: "2026-09-06T00:00:00Z",
  schema_version: "v1",
};

const drawingsView = {
  layout_id: "layout-1",
  schema_version: 1,
  drawings: [{ id: "t", kind: "horizontal-line", price: 100, style: { line_width: 2 } }],
  revision: 0,
  updated_at: "2026-09-06T00:00:00Z",
};

const indicatorTemplateView = {
  id: "template-1",
  tenant_id: "tenant-1",
  owner_subject_id: "owner-1",
  name: "My template",
  template: { schema_version: 1, panes: [], indicators: [] },
  revision: 0,
  created_at: "2026-09-07T00:00:00Z",
  updated_at: "2026-09-07T00:00:00Z",
  schema_version: "v1",
};

describe("charting apiPaths 레지스트리: envelope=true, v1Path 미배선", () => {
  it.each([
    "charting.layouts.base",
    "charting.layouts.item",
    "charting.layouts.drawings",
    "charting.indicatorTemplates.base",
    "charting.indicatorTemplates.item",
  ] as const)("%s", (route) => {
    expect(API_ROUTES[route].envelope).toBe(true);
    expect(API_ROUTES[route].v1Path).toBeUndefined();
  });

  it("legacyPath는 charting.py 원문(§9.6 CH-5)과 1:1이다", () => {
    expect(API_ROUTES["charting.layouts.base"].legacyPath).toBe("/v1/foundation/charting/layouts");
    expect(API_ROUTES["charting.layouts.item"].legacyPath).toBe("/v1/foundation/charting/layouts/:layoutId");
    expect(API_ROUTES["charting.layouts.drawings"].legacyPath).toBe(
      "/v1/foundation/charting/layouts/:layoutId/drawings",
    );
  });

  it("legacyPath는 charting.py 원문(CH-17b, task-1904)과 1:1이다", () => {
    expect(API_ROUTES["charting.indicatorTemplates.base"].legacyPath).toBe(
      "/v1/foundation/charting/indicator-templates",
    );
    expect(API_ROUTES["charting.indicatorTemplates.item"].legacyPath).toBe(
      "/v1/foundation/charting/indicator-templates/:templateId",
    );
  });
});

describe("createChartingClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("createLayout: POST /layouts, body가 snake_case로 나가고 응답은 camelCase로 돌아온다", async () => {
    const fetchMock = stubFetch({ data: layoutView, meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } }, 201);

    const result = await makeClient().createLayout({ name: "My layout", layoutState: { schemaVersion: 1 } });

    const [url, init] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/charting/layouts");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ name: "My layout", layout_state: { schema_version: 1 } });
    expect(result).toEqual({
      id: "layout-1",
      tenantId: "tenant-1",
      ownerSubjectId: "owner-1",
      name: "My layout",
      layoutState: { schemaVersion: 1, panels: [], activePanelId: null, watchlists: [] },
      revision: 0,
      createdAt: "2026-09-06T00:00:00Z",
      updatedAt: "2026-09-06T00:00:00Z",
    });
  });

  it("listLayouts: GET /layouts, 배열 그대로 매핑한다", async () => {
    const fetchMock = stubFetch({ data: [layoutView], meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } });
    const result = await makeClient().listLayouts();
    expect(requestOf(fetchMock)[0]).toBe("https://api.example.test/v1/foundation/charting/layouts");
    expect(result).toHaveLength(1);
    expect(result[0]?.id).toBe("layout-1");
  });

  it("getLayout: GET /layouts/:id 치환", async () => {
    const fetchMock = stubFetch({ data: layoutView, meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } });
    const result = await makeClient().getLayout("layout-1");
    expect(requestOf(fetchMock)[0]).toBe("https://api.example.test/v1/foundation/charting/layouts/layout-1");
    expect(result.id).toBe("layout-1");
  });

  it("updateLayout: PATCH, expectedRevision·layoutState가 snake_case로 나간다", async () => {
    const fetchMock = stubFetch({
      data: { ...layoutView, revision: 1 },
      meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" },
    });
    const result = await makeClient().updateLayout("layout-1", { expectedRevision: 0, name: "Renamed" });

    const [url, init] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/charting/layouts/layout-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ expected_revision: 0, name: "Renamed" });
    expect(result.revision).toBe(1);
  });

  it("deleteLayout: DELETE, 204는 몸체 없이 성공한다", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await expect(makeClient().deleteLayout("layout-1")).resolves.toBeUndefined();
    const [url, init] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/charting/layouts/layout-1");
    expect(init.method).toBe("DELETE");
  });

  it("getDrawings: schema_version(top-level)을 camelCase 자동변환에서 복원해 fromDrawingsDocument가 기대하는 리터럴 키로 돌려준다", async () => {
    const fetchMock = stubFetch({ data: drawingsView, meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } });
    const result = await makeClient().getDrawings("layout-1");
    expect(requestOf(fetchMock)[0]).toBe(
      "https://api.example.test/v1/foundation/charting/layouts/layout-1/drawings",
    );
    expect(result.document).toEqual({
      schema_version: 1,
      drawings: [{ id: "t", kind: "horizontal-line", price: 100, style: { lineWidth: 2 } }],
    });
    expect(result.revision).toBe(0);
  });

  it("putDrawings: PUT body가 schema_version/expected_revision(snake)로 나가고, nested lineWidth는 line_width로 왕복한다", async () => {
    const fetchMock = stubFetch({ data: drawingsView, meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } });
    await makeClient().putDrawings("layout-1", {
      expectedRevision: 0,
      schemaVersion: 1,
      drawings: [{ id: "t", kind: "horizontal-line", price: 100, style: { lineWidth: 2 } }],
    });

    const [, init] = requestOf(fetchMock);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: 0,
      schema_version: 1,
      drawings: [{ id: "t", kind: "horizontal-line", price: 100, style: { line_width: 2 } }],
    });
  });

  it("negative: 409 STATE_CONCURRENCY_CONFLICT는 그대로 ApiError로 던져진다(자동 재시도·덮어쓰기 없음)", async () => {
    const fetchMock = stubFetch(
      { error_code: "STATE_CONCURRENCY_CONFLICT", message: "revision mismatch", trace_id: "t-409" },
      409,
    );
    const err = await makeClient()
      .updateLayout("layout-1", { expectedRevision: 0, name: "x" })
      .catch((e: unknown) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(409);
    expect(err.errorCode).toBe("STATE_CONCURRENCY_CONFLICT");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("negative: 404 RESOURCE_NOT_FOUND(타 테넌트 포함, 서버가 동일 코드로 접는다)은 그대로 ApiError로 던져진다", async () => {
    const fetchMock = stubFetch(
      { error_code: "RESOURCE_NOT_FOUND", message: "not found", trace_id: "t-404" },
      404,
    );
    const err = await makeClient()
      .getLayout("someone-elses-layout")
      .catch((e: unknown) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.statusCode).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("negative: 응답에 필수 필드가 없으면 폴백하지 않고 throw한다(미지 필드 폴백 금지)", async () => {
    stubFetch({ data: { ...layoutView, revision: undefined }, meta: { trace_id: "t-1", as_of: null } });
    await expect(makeClient().getLayout("layout-1")).rejects.toThrow(/revision/);
  });

  // CH-17c(task-1905): CH-17b(task-1904) indicator-templates CRUD.
  it("createIndicatorTemplate: POST /indicator-templates, body가 snake_case로 나가고 응답은 camelCase로 돌아온다", async () => {
    const fetchMock = stubFetch(
      { data: indicatorTemplateView, meta: { trace_id: "t-1", as_of: "2026-09-07T00:00:00Z" } },
      201,
    );

    const result = await makeClient().createIndicatorTemplate({
      name: "My template",
      template: { schemaVersion: 1, panes: [], indicators: [] },
    });

    const [url, init] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/charting/indicator-templates");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      name: "My template",
      template: { schema_version: 1, panes: [], indicators: [] },
    });
    expect(result).toEqual({
      id: "template-1",
      tenantId: "tenant-1",
      ownerSubjectId: "owner-1",
      name: "My template",
      template: { schemaVersion: 1, panes: [], indicators: [] },
      revision: 0,
      createdAt: "2026-09-07T00:00:00Z",
      updatedAt: "2026-09-07T00:00:00Z",
    });
  });

  it("listIndicatorTemplates: GET /indicator-templates, 배열 그대로 매핑한다", async () => {
    const fetchMock = stubFetch({
      data: [indicatorTemplateView],
      meta: { trace_id: "t-1", as_of: "2026-09-07T00:00:00Z" },
    });
    const result = await makeClient().listIndicatorTemplates();
    expect(requestOf(fetchMock)[0]).toBe("https://api.example.test/v1/foundation/charting/indicator-templates");
    expect(result).toHaveLength(1);
    expect(result[0]?.id).toBe("template-1");
  });

  it("deleteIndicatorTemplate: DELETE /indicator-templates/:id, 204는 몸체 없이 성공한다", async () => {
    const fetchMock = stubFetch(undefined, 204);
    await expect(makeClient().deleteIndicatorTemplate("template-1")).resolves.toBeUndefined();
    const [url, init] = requestOf(fetchMock);
    expect(url).toBe("https://api.example.test/v1/foundation/charting/indicator-templates/template-1");
    expect(init.method).toBe("DELETE");
  });

  it("negative: 템플릿 응답에 필수 필드가 없으면 폴백하지 않고 throw한다(미지 필드 폴백 금지)", async () => {
    stubFetch({
      data: [{ ...indicatorTemplateView, template: undefined }],
      meta: { trace_id: "t-1", as_of: "2026-09-07T00:00:00Z" },
    });
    await expect(makeClient().listIndicatorTemplates()).rejects.toThrow(/template/);
  });
});

describe("numeric perf: listLayouts mapping large payloads", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("maps 500 layout records (fetch + camelCase + toLayoutRecord validation) within a 200ms budget", async () => {
    const many = Array.from({ length: 500 }, (_, i) => ({ ...layoutView, id: `layout-${i}` }));
    stubFetch({ data: many, meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } });

    const startedAt = performance.now();
    const result = await makeClient().listLayouts();
    const elapsedMs = performance.now() - startedAt;

    expect(result).toHaveLength(500);
    expect(elapsedMs).toBeLessThan(200);
  });
});

// toDrawingsRecord() special-cases the top-level schema_version key exactly
// because http.ts's keysToCamel is a blind deep-recursive rename — it can't
// tell CH-4's intentionally-snake `schema_version` from an ordinary
// server field. These two tests pin both sides of that boundary: the naive
// conversion this leaf works around (RED — what fromDrawingsDocument would
// see if the restoration line were deleted) and the client's actual,
// restored output (GREEN).
describe("gate-red reproduction: schema_version restoration in toDrawingsRecord", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("RED: naive keysToCamel on the raw drawings envelope renames schema_version to schemaVersion, which fromDrawingsDocument does not recognize", () => {
    const naive = keysToCamel<Record<string, unknown>>(drawingsView);
    expect(naive.schemaVersion).toBe(1);
    expect(naive.schema_version).toBeUndefined();
  });

  it("GREEN: the actual client restores the top-level schema_version key, undoing exactly that rename", async () => {
    stubFetch({ data: drawingsView, meta: { trace_id: "t-1", as_of: "2026-09-06T00:00:00Z" } });
    const result = await makeClient().getDrawings("layout-1");
    expect(result.document.schema_version).toBe(1);
    expect((result.document as Record<string, unknown>).schemaVersion).toBeUndefined();
  });
});

describe("D3: multi-instance independence under concurrency", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("two independently-constructed clients (different baseUrl/token) issue concurrent requests without cross-contaminating headers, URLs, or results", async () => {
    const fetchMock = vi.fn(async (url: unknown, init?: RequestInit) => {
      const isA = (url as string).startsWith("https://a.example.test");
      const body = { data: { ...layoutView, id: isA ? "layout-a" : "layout-b" }, meta: { trace_id: isA ? "t-a" : "t-b", as_of: "2026-09-06T00:00:00Z" } };
      void init;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    const clientA = createChartingClient("https://a.example.test", () => "token-a");
    const clientB = createChartingClient("https://b.example.test", () => "token-b");

    const [resultA, resultB] = await Promise.all([clientA.getLayout("x"), clientB.getLayout("y")]);

    expect(resultA.id).toBe("layout-a");
    expect(resultB.id).toBe("layout-b");

    const callA = fetchMock.mock.calls.find(([url]) => (url as string).startsWith("https://a.example.test"))!;
    const callB = fetchMock.mock.calls.find(([url]) => (url as string).startsWith("https://b.example.test"))!;
    expect((callA[1]?.headers as Headers).get("Authorization")).toBe("Bearer token-a");
    expect((callB[1]?.headers as Headers).get("Authorization")).toBe("Bearer token-b");
  });
});
