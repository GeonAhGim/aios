import {
  CandlestickSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

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

// 검은색+골드 팔레트(apps/web/src/index.css)와 맞춘 리터럴 값 —
// lightweight-charts는 캔버스 렌더러라 CSS 커스텀 프로퍼티(var())를
// 직접 해석하지 못해 실제 색상값을 그대로 넣는다.
const CHART_COLORS = {
  background: "transparent",
  text: "#b5a98c", // --color-fg-secondary
  grid: "rgba(42, 38, 32, 0.6)", // --color-border, 반투명
  up: "#34d399", // --color-success
  down: "#f87171", // --color-danger
};

export function CandlestickChart({ data, height = 320 }: CandlestickChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      height,
      width: container.clientWidth,
      layout: {
        background: { color: CHART_COLORS.background },
        textColor: CHART_COLORS.text,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: CHART_COLORS.grid },
      },
      timeScale: { borderVisible: false, timeVisible: true },
      rightPriceScale: { borderVisible: false },
      crosshair: { mode: 0 },
    });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: CHART_COLORS.up,
      downColor: CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: CHART_COLORS.up,
      wickDownColor: CHART_COLORS.down,
    });

    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: container.clientWidth });
    });
    resizeObserver.observe(container);

    return () => {
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
