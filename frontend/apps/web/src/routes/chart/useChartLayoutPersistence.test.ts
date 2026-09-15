import { useRef } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createEmptyLayoutModel, encodeLayoutModel, type ChartLayoutModel } from "@aios/chart-engine/src/layout/layoutModel";
import type { ChartingLayoutRecord, ChartingPort } from "@aios/chart-engine/src/layout/persistence";
import {
  useChartLayoutPersistence,
  type UseChartLayoutPersistenceOptions,
} from "./useChartLayoutPersistence";
import type { ChartViewSnapshot } from "./chartLayoutView";

// useChartLayout.test.ts와 동일 관용: routeApiError는 statusCode/errorCode 덕타이핑으로
// 분류하므로(errorRouting.ts) api-client의 ApiError를 임포트할 필요가 없다.
function apiErrorLike(statusCode: number, errorCode: string): Error & { statusCode: number; errorCode: string } {
  return Object.assign(new Error(errorCode), { statusCode, errorCode });
}

function fakePort(overrides: Partial<ChartingPort> = {}): ChartingPort {
  return {
    createLayout: vi.fn(),
    listLayouts: vi.fn(async () => []),
    getLayout: vi.fn(),
    updateLayout: vi.fn(),
    deleteLayout: vi.fn(),
    getDrawings: vi.fn(),
    putDrawings: vi.fn(),
    ...overrides,
  };
}

function layoutRecord(model: ChartLayoutModel, overrides: Partial<ChartingLayoutRecord> = {}): ChartingLayoutRecord {
  return {
    id: "layout-1",
    name: "저장된 레이아웃",
    layoutState: encodeLayoutModel(model),
    revision: 1,
    updatedAt: "2026-09-06T00:00:00Z",
    ...overrides,
  };
}

const BASE_VIEW: ChartViewSnapshot = {
  instrumentId: "BTCUSDT",
  venue: "BITGET",
  timeframe: "1h",
  indicatorIds: [],
  compareSymbolIds: [],
};

function savedModelFor(view: ChartViewSnapshot): ChartLayoutModel {
  const panel = {
    id: "server-panel",
    instrument: { instrumentId: view.instrumentId, venue: view.venue, symbol: view.instrumentId },
    timeframe: view.timeframe,
    indicators: view.indicatorIds.map((id) => ({ id })),
    drawingSetId: "server-panel",
  };
  return { ...createEmptyLayoutModel(), panels: [panel], activePanelId: panel.id };
}

let idSeq = 0;
function genId(prefix: string): string {
  idSeq += 1;
  return `${prefix}-${idSeq}`;
}

type HarnessProps = Omit<UseChartLayoutPersistenceOptions, "onApplyViewRef" | "genId"> & {
  readonly onApplyView: (view: ChartViewSnapshot) => void;
};

// 훅 계약이 onApplyViewRef(MutableRefObject)를 요구한다 — useChartLayout.ts가
// 조립 시 하는 것과 동일하게, 이 얇은 하니스가 매 렌더 최신 콜백을 ref에 반영한다.
function setup(port: ChartingPort, view: ChartViewSnapshot = BASE_VIEW, onApplyView = vi.fn()) {
  idSeq = 0;
  return renderHook(
    (props: HarnessProps) => {
      const onApplyViewRef = useRef(props.onApplyView);
      onApplyViewRef.current = props.onApplyView;
      return useChartLayoutPersistence({ ...props, onApplyViewRef, genId });
    },
    { initialProps: { port, enabled: true, view, onApplyView } },
  );
}

describe("복원: 성공 경로", () => {
  it("저장된 레이아웃이 없으면 기본 1패널로 시작하고 현재 뷰를 화면에 반영한다", async () => {
    const onApplyView = vi.fn();
    const port = fakePort();
    const { result } = setup(port, BASE_VIEW, onApplyView);

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.model.panels).toHaveLength(1);
    expect(result.current.layoutId).toBeNull();
    expect(onApplyView).toHaveBeenCalledWith(BASE_VIEW);
  });
});

describe("negative: 409 STATE_CONCURRENCY_CONFLICT", () => {
  it("저장 중 충돌 응답이 오면 saveStatus가 conflict가 되고 모델은 서버 상태로 덮어써지지 않는다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW));
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.rename("이름변경"));
    expect(result.current.isDirty).toBe(true);

    let resolved: string | null = "unset";
    await act(async () => {
      resolved = await result.current.save();
    });

    expect(resolved).toBeNull();
    expect(result.current.saveStatus).toBe("conflict");
    expect(result.current.saveError).toBeNull();
    // 충돌 시 로컬 편집(이름변경)을 서버 응답으로 덮어쓰지 않는다 — applySaved가 호출되지 않았다.
    expect(result.current.layoutName).toBe("이름변경");
    expect(result.current.layoutId).toBe("layout-1");
  });
});

describe("negative: 생성 경로 저장 실패(5xx)", () => {
  it("layoutId가 없을 때 createLayout이 실패하면 saveStatus가 error가 되고 layoutId는 null로 남는다", async () => {
    const port = fakePort({
      createLayout: vi.fn().mockRejectedValue(apiErrorLike(500, "INTERNAL")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    let resolved: string | null = "unset" as unknown as string | null;
    await act(async () => {
      resolved = await result.current.save();
    });

    expect(resolved).toBeNull();
    expect(result.current.saveStatus).toBe("error");
    expect(result.current.saveError).not.toBeNull();
    expect(result.current.layoutId).toBeNull();
  });
});

describe("negative: 갱신 경로 404 NOT_FOUND(대상이 서버에서 이미 삭제됨)", () => {
  it("갱신 대상이 사라졌으면 saveStatus가 not_found가 되고 로컬 편집은 유지된다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW));
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(404, "RESOURCE_NOT_FOUND")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.rename("이름변경"));

    let resolved: string | null = "unset" as unknown as string | null;
    await act(async () => {
      resolved = await result.current.save();
    });

    expect(resolved).toBeNull();
    expect(result.current.saveStatus).toBe("not_found");
    expect(result.current.layoutName).toBe("이름변경");
  });
});

describe("negative: 초기 복원 실패(listLayouts 네트워크 오류)", () => {
  it("복원이 실패하면 status가 restore_failed가 되고, retryRestore로 재시도해 회복한다", async () => {
    let fail = true;
    const port = fakePort({
      listLayouts: vi.fn(async () => {
        if (fail) throw apiErrorLike(503, "EXCHANGE_UNAVAILABLE");
        return [];
      }),
    });
    const { result } = setup(port);

    await waitFor(() => expect(result.current.status).toBe("restore_failed"));
    expect(result.current.restoreError).not.toBeNull();
    // 실패한 복원은 fabricated 기본 모델을 만들지 않는다 — 초기 빈 모델 그대로.
    expect(result.current.model.panels).toHaveLength(0);

    fail = false;
    await act(async () => {
      result.current.retryRestore();
    });

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.restoreError).toBeNull();
  });
});

describe("negative: 삭제 실패(deleteLayout 네트워크 오류)", () => {
  it("삭제가 실패하면 saveStatus가 error가 되고 레이아웃은 로컬에 그대로 남는다(묵시적 초기화 금지)", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW));
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      deleteLayout: vi.fn().mockRejectedValue(apiErrorLike(500, "INTERNAL")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.layoutId).toBe("layout-1");

    await act(async () => {
      await result.current.remove();
    });

    expect(result.current.saveStatus).toBe("error");
    expect(result.current.saveError).not.toBeNull();
    // remove() 실패에도 applyDefault()가 불리지 않아 layoutId/model이 그대로 남는다.
    expect(result.current.layoutId).toBe("layout-1");
  });
});

describe("failure injection: save() 결과 코드 x 기존 레이아웃 유무 전수 퍼징", () => {
  // Seeded mulberry32 PRNG (no new dependency) — chart-engine
  // compare/paneLayout DEEPEN 퍼저(task-2729 audit 계열)와 동일 관용.
  function createRng(seed: number): () => number {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  type SaveOutcome = "ok" | "conflict" | "not_found" | "network_error";
  const OUTCOMES: readonly SaveOutcome[] = ["ok", "conflict", "not_found", "network_error"];
  const STATUS_CODE: Readonly<Record<Exclude<SaveOutcome, "ok">, number>> = {
    conflict: 409,
    not_found: 404,
    network_error: 500,
  };
  const ERROR_CODE: Readonly<Record<Exclude<SaveOutcome, "ok">, string>> = {
    conflict: "STATE_CONCURRENCY_CONFLICT",
    not_found: "RESOURCE_NOT_FOUND",
    network_error: "INTERNAL",
  };

  it("40개의 시드된 (기존 레이아웃 유무 x 서버 결과) 조합 전부에서 save()가 던지지 않고 saveStatus를 정의된 종단 상태로만 남긴다", async () => {
    const rng = createRng(0x5a4e);
    const exercised = new Set<string>();
    let cases = 0;
    for (let i = 0; i < 40; i++) {
      const hasExisting = rng() < 0.5;
      const outcome = OUTCOMES[Math.floor(rng() * OUTCOMES.length)]!;
      cases++;
      exercised.add(`${hasExisting}:${outcome}`);

      const record = layoutRecord(savedModelFor(BASE_VIEW));
      const port = fakePort({
        listLayouts: vi.fn(async () => (hasExisting ? [record] : [])),
        createLayout: vi.fn(async () => {
          if (outcome !== "ok") throw apiErrorLike(STATUS_CODE[outcome], ERROR_CODE[outcome]);
          return { ...record, id: "created-1" };
        }),
        updateLayout: vi.fn(async () => {
          if (outcome !== "ok") throw apiErrorLike(STATUS_CODE[outcome], ERROR_CODE[outcome]);
          return { ...record, revision: record.revision + 1 };
        }),
      });
      const { result, unmount } = setup(port);
      await waitFor(() => expect(result.current.status).toBe("ready"));

      let threw = false;
      await act(async () => {
        try {
          await result.current.save();
        } catch {
          threw = true;
        }
      });

      expect(threw, `case ${i} hasExisting=${hasExisting} outcome=${outcome}`).toBe(false);
      // createLayout(생성 경로)은 classify()를 거치지 않는다(persistence.ts) —
      // 기존 레이아웃이 없을 때는 어떤 실패든 conflict/not_found로 세분되지
      // 않고 뭉뚱그려 error가 된다. 기존 레이아웃이 있을 때(갱신 경로)만
      // classify()가 409/404를 conflict/not_found로 정밀 분류한다.
      const expectedStatus = outcome === "ok" ? "idle" : !hasExisting ? "error" : outcome === "network_error" ? "error" : outcome;
      expect(result.current.saveStatus, `case ${i} hasExisting=${hasExisting} outcome=${outcome}`).toBe(
        expectedStatus,
      );
      unmount();
    }
    expect(cases).toBe(40);
    expect(exercised.size).toBeGreaterThanOrEqual(6);
  });
});

describe("performance: 대량 저장 레이아웃 목록에서 최신본 선택", () => {
  it("2,000개의 저장된 레이아웃 중 최신본을 골라 ready 상태에 도달하기까지 고정 ms 예산 이내에 끝난다", async () => {
    const records: ChartingLayoutRecord[] = Array.from({ length: 2000 }, (_, i) =>
      layoutRecord(savedModelFor(BASE_VIEW), {
        id: `layout-${i}`,
        updatedAt: new Date(Date.UTC(2020, 0, 1, 0, 0, i)).toISOString(),
      }),
    );
    const port = fakePort({ listLayouts: vi.fn(async () => records) });

    const start = performance.now();
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));
    const elapsedMs = performance.now() - start;

    expect(result.current.layoutId).toBe("layout-1999"); // 가장 최신 updatedAt.
    // 넉넉한 고정 예산(상대 래칫 아님): 최신본 선택이 실수로 O(n^2)(예: 매
    // 후보마다 배열을 다시 스캔하는 최댓값 탐색)이 되면 2,000건에서 확실히
    // 넘긴다.
    expect(elapsedMs).toBeLessThan(5000);
  });
});

describe("gate red reproduction: 충돌 시 로컬 편집을 서버로 덮어쓰지 않는다", () => {
  /** Mimics a naive "resolve conflict by force-refetch" save() that, on 409,
   * fetches the current server record and adopts its name directly —
   * silently discarding whatever local edit (a pending rename) triggered the
   * conflict. A hand-written mutant of the hook's save(), not the shipped
   * hook. */
  async function legacySaveForceOverwriteOnConflict(
    port: ChartingPort,
    layoutId: string,
    pendingName: string,
  ): Promise<string> {
    try {
      await port.updateLayout(layoutId, { expectedRevision: 1, name: pendingName });
    } catch {
      const current = await port.getLayout(layoutId);
      return current.name;
    }
    return pendingName;
  }

  it("red: 강제 덮어쓰기 방식은 충돌 시 사용자가 방금 입력한 이름을 조용히 버린다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW), { name: "서버 원본 이름" });
    const port = fakePort({
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
      getLayout: vi.fn(async () => record),
    });

    const resultName = await legacySaveForceOverwriteOnConflict(port, "layout-1", "이름변경");

    expect(resultName).toBe("서버 원본 이름"); // 사용자가 입력한 "이름변경"이 사라졌다.
  });

  it("green: 실제 훅은 충돌 시 saveStatus만 conflict로 바꾸고 사용자의 편집(이름변경)은 그대로 남긴다", async () => {
    const record = layoutRecord(savedModelFor(BASE_VIEW), { name: "서버 원본 이름" });
    const port = fakePort({
      listLayouts: vi.fn(async () => [record]),
      updateLayout: vi.fn().mockRejectedValue(apiErrorLike(409, "STATE_CONCURRENCY_CONFLICT")),
    });
    const { result } = setup(port);
    await waitFor(() => expect(result.current.status).toBe("ready"));

    act(() => result.current.rename("이름변경"));
    await act(async () => {
      await result.current.save();
    });

    expect(result.current.saveStatus).toBe("conflict");
    expect(result.current.layoutName).toBe("이름변경");
  });
});
