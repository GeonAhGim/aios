// CH-6a 순수 이동(task-2011): ChartPage.tsx의 "리플레이 제어"(CH-2 candleStream +
// CH-7 replayController) 소유 단위를 그대로 옮긴다 — 로직 변경 없음.
import { type RefObject, useEffect, useRef, useState } from "react";
import {
  createCandleStream,
  type CandleStream,
  type CandleStreamSnapshot,
  type StreamCandle,
} from "@aios/chart-engine/src/data/candleStream";
import {
  createReplayController,
  type ReplayController,
  type ReplayFrame,
  type ReplayState,
} from "@aios/chart-engine/src/replay/replayController";
import type { CandleQueryResult } from "@aios/api-client";
import type { SeriesKey, Timeframe, Venue } from "@aios/shared-types";
import type { CandlestickPoint } from "@aios/ui-web";
import { IDLE_REPLAY_STATE, REAL_CLOCK, toChartPoints } from "./chartPageHelpers";

export interface UseChartReplaySessionOptions {
  readonly venue: Venue;
  readonly instrumentId: string | null;
  readonly timeframe: Timeframe;
  readonly queryData: CandleQueryResult | undefined;
}

export interface UseChartReplaySessionResult {
  readonly replayRef: RefObject<ReplayController | null>;
  readonly snapshot: CandleStreamSnapshot | null;
  readonly replayFrame: ReplayFrame | null;
  readonly replayState: ReplayState;
  readonly replayEngaged: boolean;
  readonly displayCandles: readonly StreamCandle[];
  readonly points: CandlestickPoint[];
  readonly replayDisabled: boolean;
}

export function useChartReplaySession({
  venue,
  instrumentId,
  timeframe,
  queryData,
}: UseChartReplaySessionOptions): UseChartReplaySessionResult {
  const [snapshot, setSnapshot] = useState<CandleStreamSnapshot | null>(null);
  const [replayFrame, setReplayFrame] = useState<ReplayFrame | null>(null);
  const streamRef = useRef<CandleStream | null>(null);
  const replayRef = useRef<ReplayController | null>(null);

  // 심볼/거래소/타임프레임이 바뀔 때마다 CH-2 candleStream과 CH-7
  // replayController를 새로 만든다 — 서로 다른 키의 봉을 한 스트림에
  // 섞지 않는다(candleStream.ts key_mismatch 규약).
  useEffect(() => {
    if (!instrumentId) return undefined;
    const key: SeriesKey = { venue, instrument_id: instrumentId, timeframe };
    const stream = createCandleStream({ key });
    const replay = createReplayController(stream, { clock: REAL_CLOCK });
    streamRef.current = stream;
    replayRef.current = replay;
    setSnapshot(stream.snapshot());
    setReplayFrame(null);
    const unsubStream = stream.subscribe(setSnapshot);
    const unsubReplay = replay.subscribe(setReplayFrame);
    return () => {
      unsubStream();
      unsubReplay();
      replay.dispose();
      stream.dispose();
      streamRef.current = null;
      replayRef.current = null;
    };
  }, [venue, instrumentId, timeframe]);

  // 서버에서 새 페이지가 도착할 때마다 같은 스트림에 병합한다(applyPage가
  // 중복·역행·gap 판정을 전담 — 여기서는 결과를 재정렬하지 않는다).
  useEffect(() => {
    const series = queryData?.series;
    if (streamRef.current && series) streamRef.current.applyPage(series);
  }, [queryData]);

  const replayState = replayFrame?.state ?? IDLE_REPLAY_STATE;
  const replayEngaged = replayState.status === "playing" || replayState.cursorTs !== null;
  const displayCandles = replayEngaged ? (replayFrame?.visible ?? []) : (snapshot?.candles ?? []);
  const points = toChartPoints(displayCandles);
  const replayDisabled = (snapshot?.candles.length ?? 0) === 0;

  return { replayRef, snapshot, replayFrame, replayState, replayEngaged, displayCandles, points, replayDisabled };
}
