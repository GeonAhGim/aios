import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROUTES } from "../apiPaths";
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

describe("charting apiPaths 레지스트리: envelope=true, v1Path 미배선", () => {
  it.each(["charting.layouts.base", "charting.layouts.item", "charting.layouts.drawings"] as const)(
    "%s",
    (route) => {
      expect(API_ROUTES[route].envelope).toBe(true);
      expect(API_ROUTES[route].v1Path).toBeUndefined();
    },
  );

  it("legacyPath는 charting.py 원문(§9.6 CH-5)과 1:1이다", () => {
    expect(API_ROUTES["charting.layouts.base"].legacyPath).toBe("/v1/foundation/charting/layouts");
    expect(API_ROUTES["charting.layouts.item"].legacyPath).toBe("/v1/foundation/charting/layouts/:layoutId");
    expect(API_ROUTES["charting.layouts.drawings"].legacyPath).toBe(
      "/v1/foundation/charting/layouts/:layoutId/drawings",
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
});
