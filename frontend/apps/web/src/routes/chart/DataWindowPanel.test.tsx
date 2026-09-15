import "../../i18n";
import "@testing-library/jest-dom/vitest";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import { DataWindowError, computeDataWindowRows, type DataWindowRow } from "@aios/chart-engine/src/legend/dataWindow";
import type { PlotSeriesPoint } from "@aios/chart-engine/src/render/plotRenderers";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { OverlaySeriesByOutput } from "./ChartPlotLayer";
import { DataWindowPanel, buildIndicatorSnapshots, resolveDataIndex, type DataWindowPanelProps } from "./DataWindowPanel";

afterEach(cleanup);

function candle(hourOffset: number): StreamCandle {
  const open = new Date(Date.UTC(2026, 8, 3, hourOffset, 0, 0));
  const close = new Date(Date.UTC(2026, 8, 3, hourOffset + 1, 0, 0));
  return {
    openTimeMs: open.getTime(),
    confirmed: true,
    record: {
      key: { venue: "BITGET", instrument_id: "BTCUSDT", timeframe: "1h" },
      open_time: open.toISOString(),
      close_time: close.toISOString(),
      open: "50000.00",
      high: "50500.00",
      low: "49800.00",
      close: "50200.00",
      volume: "12.5",
      quote_volume: "628500.00",
    },
  };
}

function overlay(id: string): OverlayEntry {
  return { id, placement: "sub-pane", params: [], outputs: [{ name: "value", series: "line" }], paneIndex: 1 };
}

function pointsAt(candles: readonly StreamCandle[], value: number): readonly PlotSeriesPoint[] {
  return candles.map((c) => ({ time: c.openTimeMs, value }));
}

describe("DataWindowPanel — CH-16d 지표 30종 값 동시 표시", () => {
  it("renders one row per figure at the crosshair-resolved bar", () => {
    const candles = [candle(0), candle(1)];
    const overlays = [overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 101.5)]])]]);

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={candles[0]!.openTimeMs} />);

    const rows = screen.getAllByTestId("data-window-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("101.5000");
  });

  it("DoD: 30 indicators render 30 rows simultaneously in the DOM (no truncation, no virtualization)", () => {
    const candles = [candle(0)];
    const overlays = Array.from({ length: 30 }, (_, i) => overlay(`IND_${i}`));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>(
      overlays.map((o, i) => [o.id, new Map([["value", pointsAt(candles, i + 0.5)]])] as const),
    );

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={null} />);

    expect(screen.getAllByTestId("data-window-row")).toHaveLength(30);
  });

  it("negative: duplicate indicator id is surfaced as an explicit error, not an empty panel or a stale last value", () => {
    const candles = [candle(0)];
    const overlays = [overlay("SMA"), overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 1)]])]]);

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={null} />);

    expect(screen.getByTestId("data-window-error")).toHaveTextContent("같은 지표 id가 중복되어");
    expect(screen.queryByTestId("data-window-row")).not.toBeInTheDocument();
  });
});

// DEPTH_CH(task-2729) 감사(task-3105): task-2044 CH-16d의 원 증빙은 negative 1건
// (중복 id)뿐이었다 — 아래는 스크린 레벨에서 놓쳤던 axis들(빈 overlays, overlaySeries
// 누락, 빈 candles)을 보강한다. 순수 로직(누락 포인트/범위밖 dataIndex)은 이미
// chart-engine legend/__tests__/dataWindow.test.ts(task-3085 DEEPEN 1711)가 덮지만,
// 그 보강이 실제로 DataWindowPanel 화면 배선까지 관통하는지는 여기서만 확인된다.
describe("DataWindowPanel — negative axes 보강 (DEEPEN task-3105)", () => {
  it("negative: overlays가 비어 있으면 표시할 지표 없음 placeholder를 보이고 크래시하지 않는다", () => {
    render(<DataWindowPanel overlays={[]} overlaySeries={new Map()} candles={[candle(0)]} crosshairTimeMs={null} />);

    expect(screen.queryByTestId("data-window-row")).not.toBeInTheDocument();
    expect(screen.getByTestId("data-window-panel")).toHaveTextContent("표시할 지표가 없습니다.");
  });

  it("negative: overlaySeries에 항목이 없는 overlay는 크래시 대신 defaultValue(n/a)로 렌더링된다", () => {
    const candles = [candle(0), candle(1)];
    const overlays = [overlay("ORPHAN")];

    render(<DataWindowPanel overlays={overlays} overlaySeries={new Map()} candles={candles} crosshairTimeMs={candles[0]!.openTimeMs} />);

    const rows = screen.getAllByTestId("data-window-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("n/a");
  });

  it("negative: candles가 비어 있으면 resolveDataIndex가 -1을 반환해도 각 지표 행이 defaultValue로 채워진 채 유지된다(빈 패널로 무음 폴백하지 않는다)", () => {
    const overlays = [overlay("SMA"), overlay("RSI")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([
      ["SMA", new Map([["value", []]])],
      ["RSI", new Map([["value", []]])],
    ]);

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={[]} crosshairTimeMs={null} />);

    const rows = screen.getAllByTestId("data-window-row");
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.textContent?.includes("n/a"))).toBe(true);
  });

  it("negative: crosshairTimeMs가 첫 캔들보다 이전이어도 가장 가까운(첫) 캔들 값으로 수렴하고 out-of-range로 빈 패널이 되지 않는다", () => {
    const candles = [candle(5), candle(6)];
    const overlays = [overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 42)]])]]);

    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={0} />);

    const rows = screen.getAllByTestId("data-window-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("42.0000");
  });
});

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const NON_FINITE_VALUES = [NaN, Infinity, -Infinity] as const;

describe("DataWindowPanel — 실패주입 (DEEPEN task-3105)", () => {
  it("200회 시드된 NaN/Infinity overlaySeries 포인트 주입에도 어떤 행도 'NaN'/'Infinity' 텍스트를 화면에 노출하지 않는다", () => {
    const rand = mulberry32(0x3105);

    for (let i = 0; i < 200; i++) {
      const candles = [candle(i), candle(i + 1)];
      const corrupted = NON_FINITE_VALUES[Math.floor(rand() * NON_FINITE_VALUES.length)]!;
      const overlays = [overlay(`IND_${i}`)];
      const overlaySeries = new Map<string, OverlaySeriesByOutput>([
        [`IND_${i}`, new Map([["value", [{ time: candles[0]!.openTimeMs, value: corrupted }, { time: candles[1]!.openTimeMs, value: rand() * 100 }]]])],
      ]);

      const { unmount } = render(
        <DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={candles[0]!.openTimeMs} />,
      );

      for (const row of screen.getAllByTestId("data-window-row")) {
        expect(row.textContent, `iteration ${i}, corrupted=${corrupted}`).not.toMatch(/NaN|Infinity/);
      }
      unmount();
    }
  });
});

describe("DataWindowPanel — 수치 성능 단언 (DEEPEN task-3105)", () => {
  it("지표 30종 x 2,000봉 시리즈 마운트가 1,000ms 예산 안에 끝난다", () => {
    const candles = Array.from({ length: 2000 }, (_, i) => candle(i));
    const overlays = Array.from({ length: 30 }, (_, i) => overlay(`IND_${i}`));
    const overlaySeries = new Map<string, OverlaySeriesByOutput>(
      overlays.map((o, i) => [o.id, new Map([["value", pointsAt(candles, i + 0.5)]])] as const),
    );

    const start = performance.now();
    render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={candles[1000]!.openTimeMs} />);
    const elapsedMs = performance.now() - start;

    expect(screen.getAllByTestId("data-window-row")).toHaveLength(30);
    expect(elapsedMs).toBeLessThan(1000);
  });
});

/**
 * DataWindowPanel의 try/catch(DataWindowError) 배선 없이 computeDataWindowRows를
 * 직접 렌더 경로에 노출한 legacy 목업 — 실제 모듈이 아니다. task-2044 이전 상태를
 * 흉내낸다: 중복 id를 만나면 무음 폴백 대신 렌더 중 예외를 그대로 던진다.
 */
function NaiveDataWindowPanel({ overlays, overlaySeries, candles, crosshairTimeMs }: DataWindowPanelProps) {
  const snapshots = buildIndicatorSnapshots(overlays, overlaySeries, candles);
  const dataIndex = resolveDataIndex(candles, crosshairTimeMs);
  const rows: readonly DataWindowRow[] = computeDataWindowRows(snapshots, dataIndex);
  return (
    <table>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.indicatorId}:${row.outputKey}`} data-testid="data-window-row">
            <td>{row.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

describe("DataWindowPanel — 게이트 적색 재현 (DEEPEN task-3105): 중복 id 에러 배선", () => {
  it("red: try/catch(DataWindowError) 배선이 없는 legacy 목업은 중복 id에서 렌더 중 예외로 크래시한다", () => {
    const candles = [candle(0)];
    const overlays = [overlay("SMA"), overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 1)]])]]);

    expect(() => render(<NaiveDataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={null} />)).toThrow(
      DataWindowError,
    );
  });

  it("green: 실 DataWindowPanel은 같은 입력을 잡아 에러 배너로 표면화하며 크래시하지 않는다(legacy와 대조)", () => {
    const candles = [candle(0)];
    const overlays = [overlay("SMA"), overlay("SMA")];
    const overlaySeries = new Map<string, OverlaySeriesByOutput>([["SMA", new Map([["value", pointsAt(candles, 1)]])]]);

    expect(() =>
      render(<DataWindowPanel overlays={overlays} overlaySeries={overlaySeries} candles={candles} crosshairTimeMs={null} />),
    ).not.toThrow();
    expect(screen.getByTestId("data-window-error")).toBeInTheDocument();
  });
});
