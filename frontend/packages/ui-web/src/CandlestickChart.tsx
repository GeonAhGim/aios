import {
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import { CANVAS_COLOR_FALLBACK } from "./canvasColorFallbacks";
import { themeStore } from "./theme";

export interface CandlestickPoint {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
}

interface CandlestickChartProps {
  data: CandlestickPoint[];
  height?: number;
}

// UX-3: lightweight-charts는 캔버스 렌더러라 CSS 커스텀 프로퍼티(var())를 직접
// 해석하지 못한다 -- 그래서 리터럴 hex를 박아두는 대신, 실제 적용된 토큰 값을
// getComputedStyle로 읽어온다. 다크/라이트 전환(theme.ts) 때마다 다시 읽어야
// 화면이 맞는 팔레트를 따라가므로, 이 함수는 호출 시점마다 새로 계산한다.
function readColorToken(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function resolveChartColors() {
  return {
    background: "transparent",
    text: readColorToken("--color-fg-secondary", CANVAS_COLOR_FALLBACK.text),
    grid: readColorToken("--color-border", CANVAS_COLOR_FALLBACK.grid),
    up: readColorToken("--color-success", CANVAS_COLOR_FALLBACK.up),
    down: readColorToken("--color-danger", CANVAS_COLOR_FALLBACK.down),
  };
}

export function CandlestickChart({ data, height = 320 }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const initialColors = resolveChartColors();
    const chart = createChart(container, {
      height,
      width: container.clientWidth,
      layout: {
        background: { color: initialColors.background },
        textColor: initialColors.text,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: initialColors.grid },
      },
      timeScale: { borderVisible: false, timeVisible: true },
      rightPriceScale: { borderVisible: false },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: initialColors.up,
      downColor: initialColors.down,
      borderVisible: false,
      wickUpColor: initialColors.up,
      wickDownColor: initialColors.down,
    });

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth });
    });
    resizeObserver.observe(container);

    // UX-3: 다크/라이트 전환 시 캔버스 색을 다시 읽어 반영한다 -- CSS만으로는
    // canvas 렌더러(lightweight-charts)가 토큰을 따라가지 못한다.
    const unsubscribeTheme = themeStore.subscribe(() => {
      const colors = resolveChartColors();
      chart.applyOptions({
        layout: { background: { color: colors.background }, textColor: colors.text },
        grid: { horzLines: { color: colors.grid } },
      });
      seriesRef.current?.applyOptions({
        upColor: colors.up,
        downColor: colors.down,
        wickUpColor: colors.up,
        wickDownColor: colors.down,
      });
    });

    return () => {
      unsubscribeTheme();
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    seriesRef.current.setData(
      data.map((d) => ({
        time: d.time as UTCTimestamp,
        open: d.open,
        high: d.high,
        low: d.low,
        close: d.close,
      })),
    );
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
