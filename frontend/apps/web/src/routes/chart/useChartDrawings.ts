// CH-6a 순수 이동(task-2011): ChartPage.tsx의 "그리기(도형) 상태" 소유 단위를 그대로
// 옮긴다 — 로직 변경 없음. 후속 task-2012(drawings 영속화)가 이 경계 위에 얹힌다.
import { type Dispatch, type SetStateAction, useEffect, useRef, useState } from "react";
import { addDrawing, removeDrawing } from "@aios/chart-engine/src/drawings/tools";
import type { DrawingCollection, DrawingKind } from "@aios/chart-engine/src/drawings/model";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { Timeframe, Venue } from "@aios/shared-types";
import { createDrawing } from "./chartPageHelpers";

export interface UseChartDrawingsResult {
  readonly drawingTool: DrawingKind | null;
  readonly setDrawingTool: Dispatch<SetStateAction<DrawingKind | null>>;
  readonly drawings: DrawingCollection;
  readonly handleAddDrawing: (latestCandle: StreamCandle | undefined) => void;
  readonly handleRemoveDrawing: (id: string) => void;
}

export function useChartDrawings(venue: Venue, instrumentId: string | null, timeframe: Timeframe): UseChartDrawingsResult {
  const [drawingTool, setDrawingTool] = useState<DrawingKind | null>(null);
  const [drawings, setDrawings] = useState<DrawingCollection>([]);
  const drawingSeq = useRef(0);

  // 심볼/거래소/타임프레임이 바뀔 때마다 그리기를 초기화한다 — 서로 다른
  // 키의 그리기를 한 화면에 섞지 않는다(candleStream.ts key_mismatch 규약과 동일 축).
  useEffect(() => {
    if (!instrumentId) return;
    setDrawings([]);
  }, [venue, instrumentId, timeframe]);

  function handleAddDrawing(latestCandle: StreamCandle | undefined): void {
    if (!drawingTool) return;
    if (!latestCandle) return;
    const time = Math.floor(latestCandle.openTimeMs / 1000);
    const price = Number(latestCandle.record.close);
    drawingSeq.current += 1;
    const drawing = createDrawing(`drawing-${drawingSeq.current}`, drawingTool, time, price);
    setDrawings((prev) => addDrawing(prev, drawing));
  }

  function handleRemoveDrawing(id: string): void {
    setDrawings((prev) => removeDrawing(prev, id));
  }

  return { drawingTool, setDrawingTool, drawings, handleAddDrawing, handleRemoveDrawing };
}
