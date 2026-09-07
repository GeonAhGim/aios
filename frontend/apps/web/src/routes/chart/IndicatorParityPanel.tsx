// CH-18b/CH-18e — ChartPage 화면 배선: CH-18a `clientEngine.ts`(task-1953)의 클라이언트
// 계산값을 그대로 그리지 않고, `parityCheck.ts`(task-1954)로 서버 참조값과
// 대조한 뒤에만 그린다. 실제 IND-1 서버 지표 계산 엔드포인트는 아직 없다
// (IND-12 `GET /v1/indicators`는 카탈로그 목록만 제공한다) — 그래서
// `catalog`·`resolveServerSeries`는 둘 다 부모(ChartPage)가 주입하는 포트이고,
// 기본값은 "아직 배선되지 않음"을 정직하게 반영해 아무것도 검증되지 않은
// 상태(빈 카탈로그·server=null)로 떨어진다. 이 컴포넌트는 그 상태에서 아무
// 숫자도 그리지 않는다(fail-closed) — verifiedIndicators.ts의 빈 화이트리스트
// 원칙과 동일.
//
// CH-18e: 실제 지표 계산(`useIndicatorParityRows.ts`)은 이제 `workerPool.ts`를
// 통해 Web Worker에서 돈다 — 이 컴포넌트는 그 풀의 생명주기(자기가 만들었으면
// 언마운트 시 dispose, 테스트가 주입했으면 손대지 않음)만 책임진다.
import { useEffect, useMemo, useState } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import type { Bar } from "@aios/chart-engine/src/compute/clientEngine";
import type { WorkerPool } from "@aios/chart-engine/src/compute/workerPool";
import { createIndicatorComputePool } from "./indicatorComputePool";
import { useIndicatorParityRows, type ServerIndicatorSeriesPort } from "./useIndicatorParityRows";

export type { ServerIndicatorSeriesPort };

function toBar(candle: StreamCandle): Bar {
  return {
    open: Number(candle.record.open),
    high: Number(candle.record.high),
    low: Number(candle.record.low),
    close: Number(candle.record.close),
    volume: Number(candle.record.volume),
  };
}

export interface IndicatorParityPanelProps {
  readonly candles: readonly StreamCandle[];
  readonly overlays: readonly OverlayEntry[];
  /** IND-12 카탈로그 항목. 비어 있으면(기본값) 아무 지표도 검증 대상이 아니다(fail-closed). */
  readonly catalog: readonly IndicatorCatalogEntry[];
  /** 서버 참조 시리즈 포트. 기본값은 "아직 없음"(항상 null) — 실 배선 전까지 fail-closed. */
  readonly resolveServerSeries?: ServerIndicatorSeriesPort;
  /** 테스트 전용 오버라이드. 생략하면(기본값) 실제 브라우저 Worker 풀을 만들어 쓰고 언마운트 시 정리한다. */
  readonly computePool?: WorkerPool | null;
}

const NO_SERVER_SERIES: ServerIndicatorSeriesPort = () => null;

export function IndicatorParityPanel({
  candles,
  overlays,
  catalog,
  resolveServerSeries = NO_SERVER_SERIES,
  computePool,
}: IndicatorParityPanelProps) {
  const bars = useMemo(() => candles.map(toBar), [candles]);
  const ownsPool = computePool === undefined;
  const [pool] = useState<WorkerPool | null>(() => (ownsPool ? createIndicatorComputePool() : computePool));

  useEffect(() => {
    if (!ownsPool) return;
    return () => pool?.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `pool` is created once in useState above; `ownsPool` is derived from the same initial prop.
  }, []);

  const rows = useIndicatorParityRows(bars, overlays, catalog, resolveServerSeries, pool);

  if (rows.length === 0) return null;

  return (
    <section aria-label="지표 검증 상태" className="space-y-1 text-xs text-fg-secondary" data-testid="indicator-parity-panel">
      {rows.map((row) => (
        <p key={row.id} data-testid={`indicator-parity-${row.id}`}>
          <span>{row.id}: </span>
          <span data-testid={`indicator-parity-value-${row.id}`}>{row.value !== null ? row.value.toFixed(6) : "--"}</span>
          <span data-testid={`indicator-parity-source-${row.id}`}> ({row.source})</span>
          {row.fallbackReason && (
            <span data-testid={`indicator-parity-fallback-${row.id}`}>
              {" "}
              — 서버 값으로 대체됨({row.fallbackReason})
            </span>
          )}
          {row.computeNote && <span data-testid={`indicator-parity-note-${row.id}`}> ({row.computeNote})</span>}
        </p>
      ))}
    </section>
  );
}
