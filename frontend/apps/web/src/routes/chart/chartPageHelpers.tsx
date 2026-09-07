// CH-6a 순수 이동(task-2011): ChartPage.tsx에서 상태를 갖지 않는 순수 함수/컴포넌트만
// 그대로 옮긴다 — 동작 변경 없음.
import {
  createFibonacci,
  createHorizontalLine,
  createRectangle,
  createTrendLine,
  createVerticalLine,
} from "@aios/chart-engine/src/drawings/tools";
import type { Drawing, DrawingKind } from "@aios/chart-engine/src/drawings/model";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { ReplayClock, ReplayState } from "@aios/chart-engine/src/replay/replayController";
import type { Timeframe, Venue } from "@aios/shared-types";
import { type CandlestickPoint, EmptyState, PageHeader } from "@aios/ui-web";
import { Link } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import type { CompareSymbolRef } from "./CompareSymbols";

// CH-13b: compareSymbolIds(useChartLayout.ts)와 동일한 "VENUE:instrumentId" 인코딩을
// 이 화면 경계에서만 구조체로 풀고 다시 만든다 — 훅은 문자열만 안다(파일 범위 제한).
export function encodeCompareSymbol(ref: CompareSymbolRef): string {
  return `${ref.venue}:${ref.instrumentId}`;
}

export function decodeCompareSymbol(id: string): CompareSymbolRef | null {
  const sep = id.indexOf(":");
  if (sep < 0) return null;
  return { venue: id.slice(0, sep) as Venue, instrumentId: id.slice(sep + 1) };
}

export const TIMEFRAME_MS: Record<Timeframe, number> = {
  "1m": 60_000,
  "5m": 5 * 60_000,
  "15m": 15 * 60_000,
  "30m": 30 * 60_000,
  "1h": 60 * 60_000,
  "4h": 4 * 60 * 60_000,
  "1d": 24 * 60 * 60_000,
};

export function toChartPoints(candles: readonly StreamCandle[]): CandlestickPoint[] {
  return candles.map((c) => ({
    time: Math.floor(c.openTimeMs / 1000),
    open: Number(c.record.open),
    high: Number(c.record.high),
    low: Number(c.record.low),
    close: Number(c.record.close),
  }));
}

export function drawingLabel(d: Drawing): string {
  switch (d.kind) {
    case "trendline":
      return `추세선 (${d.points[0].time}→${d.points[1].time})`;
    case "horizontal-line":
      return `수평선 @${d.price}`;
    case "vertical-line":
      return `수직선 @${d.time}`;
    case "rectangle":
      return `사각형 (${d.points[0].time}→${d.points[1].time})`;
    case "fibonacci":
      return `피보나치 (${d.points[0].time}→${d.points[1].time})`;
  }
}

export function createDrawing(id: string, kind: DrawingKind, time: number, price: number): Drawing {
  switch (kind) {
    case "horizontal-line":
      return createHorizontalLine(id, price);
    case "vertical-line":
      return createVerticalLine(id, time);
    case "trendline":
      return createTrendLine(id, { time: time - 1, price }, { time, price });
    case "rectangle":
      return createRectangle(id, { time: time - 1, price: price * 0.99 }, { time, price: price * 1.01 });
    case "fibonacci":
      return createFibonacci(id, { time: time - 1, price: price * 0.99 }, { time, price });
  }
}

export function NoInstrumentSelected() {
  return (
    <AppShell>
      <div className="max-w-5xl space-y-4">
        <PageHeader title="차트" />
        <EmptyState>
          심볼을 먼저 선택하세요.{" "}
          <Link to="/market/instruments" className="underline">
            심볼 목록으로 이동
          </Link>
        </EmptyState>
      </div>
    </AppShell>
  );
}

export const REAL_CLOCK: ReplayClock = {
  setTimeout: (cb, ms) => window.setTimeout(cb, ms),
  clearTimeout: (t) => window.clearTimeout(t as number),
};

// replayController가 아직 한 번도 프레임을 내보내기 전(마운트 직후·스트림
// 교체 직후)의 표시용 기본값 — ref.current를 렌더 중에 읽지 않기 위한 정적 값.
export const IDLE_REPLAY_STATE: ReplayState = {
  status: "paused",
  speed: 1,
  cursorTs: null,
  visibleCount: 0,
  totalCount: 0,
  atEnd: true,
};
