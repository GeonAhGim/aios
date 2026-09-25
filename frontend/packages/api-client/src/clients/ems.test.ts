import { describe, it, expect, vi, beforeEach } from "vitest";
import type { AnyConstructor } from "../http";
import { withEms } from "./ems";
import * as apiPaths from "../apiPaths";
import type {
  AlgoProgressResponse,
  TcaResultResponse,
  ComputeTcaRequest,
} from "@aios/shared-types";

class MockClient {
  requestEnvelope = vi.fn();
  postEnvelope = vi.fn();
}

describe("withEms", () => {
  let client: InstanceType<ReturnType<typeof withEms>>;

  beforeEach(() => {
    vi.clearAllMocks();
    const BaseWithEms = withEms(MockClient as unknown as AnyConstructor);
    client = new BaseWithEms() as any;
  });

  it("calls correct path for getAlgoProgress", async () => {
    const mockResponse: AlgoProgressResponse = {
      parentId: "550e8400-e29b-41d4-a716-446655440000",
      status: "PENDING",
      totalSlices: 10,
      submittedSlices: 5,
      pendingSlices: 3,
      remainingQty: "500.50",
      demotedToTwap: false,
      demotionReason: null,
    };

    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/algo/:parentId/progress",
    );
    client.requestEnvelope = vi.fn().mockResolvedValue(mockResponse);

    const result = await client.getAlgoProgress("550e8400-e29b-41d4-a716-446655440000");

    expect(apiPaths.resolvePath).toHaveBeenCalledWith("ems.algo.progress");
    expect(client.requestEnvelope).toHaveBeenCalledWith(
      "/v1/foundation/ems/algo/550e8400-e29b-41d4-a716-446655440000/progress",
    );
    expect(result).toEqual(mockResponse);
  });

  it("calls correct path for getLatestTca", async () => {
    const mockResponse: TcaResultResponse = {
      parentId: "550e8400-e29b-41d4-a716-446655440000",
      revision: 1,
      result: {
        arrivalBps: "15.50",
        vwapBps: "-5.25",
        impactBps: "8.75",
        feesBps: "2.00",
        opportunityBps: "10.00",
        schemaVersion: "v1",
      },
      computedAt: "2026-09-23T10:30:00Z",
    };

    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/tca/:parentId",
    );
    client.requestEnvelope = vi.fn().mockResolvedValue(mockResponse);

    const result = await client.getLatestTca("550e8400-e29b-41d4-a716-446655440000");

    expect(apiPaths.resolvePath).toHaveBeenCalledWith("ems.tca.latest");
    expect(client.requestEnvelope).toHaveBeenCalledWith(
      "/v1/foundation/ems/tca/550e8400-e29b-41d4-a716-446655440000",
    );
    expect(result).toEqual(mockResponse);
  });

  it("calls correct path for getTcaRevision", async () => {
    const mockResponse: TcaResultResponse = {
      parentId: "550e8400-e29b-41d4-a716-446655440000",
      revision: 2,
      result: {
        arrivalBps: "16.00",
        vwapBps: "-4.50",
        impactBps: "9.00",
        feesBps: "2.10",
        opportunityBps: "10.50",
        schemaVersion: "v1",
      },
      computedAt: "2026-09-23T11:00:00Z",
    };

    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/tca/:parentId/revisions/:revision",
    );
    client.requestEnvelope = vi.fn().mockResolvedValue(mockResponse);

    const result = await client.getTcaRevision("550e8400-e29b-41d4-a716-446655440000", 2);

    expect(apiPaths.resolvePath).toHaveBeenCalledWith("ems.tca.revision");
    expect(client.requestEnvelope).toHaveBeenCalledWith(
      "/v1/foundation/ems/tca/550e8400-e29b-41d4-a716-446655440000/revisions/2",
    );
    expect(result).toEqual(mockResponse);
  });

  it("calls postEnvelope for computeTca", async () => {
    const mockResponse: TcaResultResponse = {
      parentId: "550e8400-e29b-41d4-a716-446655440000",
      revision: 1,
      result: {
        arrivalBps: "15.50",
        vwapBps: "-5.25",
        impactBps: "8.75",
        feesBps: "2.00",
        opportunityBps: "10.00",
        schemaVersion: "v1",
      },
      computedAt: "2026-09-23T10:30:00Z",
    };

    const request: ComputeTcaRequest = {
      side: "BUY",
      fills: [{ price: "100.50", qty: "50" }],
      priceAtArrivalTs: "100",
      bars: [{ close: "101", volume: "1000" }],
      spreadCost: "0.5",
      fees: "2",
      totalCost: "2.5",
      computedAt: "2026-09-23T10:30:00Z",
    };

    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/tca/:parentId:compute",
    );
    client.postEnvelope = vi.fn().mockResolvedValue(mockResponse);

    const result = await client.computeTca("550e8400-e29b-41d4-a716-446655440000", request);

    expect(apiPaths.resolvePath).toHaveBeenCalledWith("ems.tca.compute");
    expect(client.postEnvelope).toHaveBeenCalledWith(
      "/v1/foundation/ems/tca/550e8400-e29b-41d4-a716-446655440000:compute",
      request,
    );
    expect(result).toEqual(mockResponse);
  });

  it("handles errors from getAlgoProgress", async () => {
    const error = new Error("Network error");
    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/algo/:parentId/progress",
    );
    client.requestEnvelope = vi.fn().mockRejectedValue(error);

    await expect(
      client.getAlgoProgress("550e8400-e29b-41d4-a716-446655440000"),
    ).rejects.toThrow("Network error");
  });

  it("handles errors from computeTca", async () => {
    const error = new Error("Compute failed");
    const request: ComputeTcaRequest = {
      side: "SELL",
      fills: [{ price: "100", qty: "10" }],
      priceAtArrivalTs: "100",
      bars: [{ close: "100", volume: "1000" }],
      spreadCost: "0",
      fees: "0",
      totalCost: "0",
      computedAt: "2026-09-23T10:30:00Z",
    };

    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/tca/:parentId:compute",
    );
    client.postEnvelope = vi.fn().mockRejectedValue(error);

    await expect(
      client.computeTca("550e8400-e29b-41d4-a716-446655440000", request),
    ).rejects.toThrow("Compute failed");
  });

  // task-6856: fills/bars가 빈 배열이면 서버가 0으로 나누는 등 계산 자체가
  // 성립하지 않는다(적색 게이트 재현) — 요청을 보내기 전에 클라이언트가 거부해야 한다.
  it("rejects computeTca before sending the request when fills is empty", async () => {
    const request: ComputeTcaRequest = {
      side: "BUY",
      fills: [],
      priceAtArrivalTs: "100",
      bars: [{ close: "100", volume: "1000" }],
      spreadCost: "0",
      fees: "0",
      totalCost: "0",
      computedAt: "2026-09-23T10:30:00Z",
    };
    client.postEnvelope = vi.fn();

    await expect(
      client.computeTca("550e8400-e29b-41d4-a716-446655440000", request),
    ).rejects.toThrow(/fills/);
    expect(client.postEnvelope).not.toHaveBeenCalled();
  });

  it("rejects computeTca before sending the request when bars is empty", async () => {
    const request: ComputeTcaRequest = {
      side: "BUY",
      fills: [{ price: "100", qty: "10" }],
      priceAtArrivalTs: "100",
      bars: [],
      spreadCost: "0",
      fees: "0",
      totalCost: "0",
      computedAt: "2026-09-23T10:30:00Z",
    };
    client.postEnvelope = vi.fn();

    await expect(
      client.computeTca("550e8400-e29b-41d4-a716-446655440000", request),
    ).rejects.toThrow(/bars/);
    expect(client.postEnvelope).not.toHaveBeenCalled();
  });

  // 수치 성능 단언(D2 하한): fills/bars가 큰 배열이어도 빈 배열 검증 자체는
  // O(1)에 가까워야 한다 — 검증 로직이 실수로 배열을 순회/복사하는 회귀를 잡는다.
  it("validates a large fills/bars payload well within budget", async () => {
    const request: ComputeTcaRequest = {
      side: "BUY",
      fills: Array.from({ length: 5000 }, () => ({ price: "100", qty: "1" })),
      priceAtArrivalTs: "100",
      bars: Array.from({ length: 5000 }, () => ({ close: "100", volume: "1" })),
      spreadCost: "0",
      fees: "0",
      totalCost: "0",
      computedAt: "2026-09-23T10:30:00Z",
    };

    vi.spyOn(apiPaths, "resolvePath").mockReturnValue(
      "/v1/foundation/ems/tca/:parentId:compute",
    );
    client.postEnvelope = vi.fn().mockResolvedValue({});

    const startedAt = performance.now();
    await client.computeTca("550e8400-e29b-41d4-a716-446655440000", request);
    const elapsedMs = performance.now() - startedAt;

    expect(elapsedMs).toBeLessThan(50);
  });
});
