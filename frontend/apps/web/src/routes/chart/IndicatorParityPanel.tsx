// CH-18b — ChartPage 화면 배선: CH-18a `clientEngine.ts`(task-1953)의 클라이언트
// 계산값을 그대로 그리지 않고, `parityCheck.ts`(task-1954)로 서버 참조값과
// 대조한 뒤에만 그린다. 실제 IND-1 서버 지표 계산 엔드포인트는 아직 없다
// (IND-12 `GET /v1/indicators`는 카탈로그 목록만 제공한다) — 그래서
// `catalog`·`resolveServerSeries`는 둘 다 부모(ChartPage)가 주입하는 포트이고,
// 기본값은 "아직 배선되지 않음"을 정직하게 반영해 아무것도 검증되지 않은
// 상태(빈 카탈로그·server=null)로 떨어진다. 이 컴포넌트는 그 상태에서 아무
// 숫자도 그리지 않는다(fail-closed) — verifiedIndicators.ts의 빈 화이트리스트
// 원칙과 동일.
//
// 지표별 기본 파라미터 값(DEFAULT_PARAMS)은 overlayRegistry.ts가 갖지 않는
// 정보(그쪽은 파라미터 "이름"만 안다) — TA-Lib 기본값이자
// `verify_all.py reference_vector()`가 스냅샷을 만들 때 쓰는 값과 동일하다
// (parityCheck.test.ts fixtures/referenceVectors.ts 참고, 새로 지어낸 값 아님).
import { useMemo } from "react";
import type { StreamCandle } from "@aios/chart-engine/src/data/candleStream";
import type { OverlayEntry } from "@aios/chart-engine/src/indicators/overlayRegistry";
import type { IndicatorCatalogEntry } from "@aios/chart-engine/src/plugins/indicatorPlugin";
import {
  ClientEngineError,
  computeIndicatorSeries,
  type Bar,
  type IndicatorParams,
  type IndicatorSeriesResult,
} from "@aios/chart-engine/src/compute/clientEngine";
import { resolveIndicatorSeries, type IndicatorSeriesSource } from "@aios/chart-engine/src/compute/parityCheck";
import { isVerifiedIndicator } from "@aios/chart-engine/src/compute/verifiedIndicators";

export type ServerIndicatorSeriesPort = (args: {
  readonly name: string;
  readonly params: IndicatorParams;
  readonly bars: readonly Bar[];
}) => IndicatorSeriesResult | null;

const DEFAULT_PARAMS: Readonly<Record<string, IndicatorParams>> = {
  SMA: { timeperiod: 20 },
  EMA: { timeperiod: 20 },
  RSI: { timeperiod: 14 },
  ATR: { timeperiod: 14 },
  CCI: { timeperiod: 14 },
  WILLR: { timeperiod: 14 },
  MFI: { timeperiod: 14 },
  MACD: { fastperiod: 12, slowperiod: 26, signalperiod: 9 },
  STOCH: { fastk_period: 5, slowk_period: 3, slowd_period: 3 },
  OBV: {},
};

function toBar(candle: StreamCandle): Bar {
  return {
    open: Number(candle.record.open),
    high: Number(candle.record.high),
    low: Number(candle.record.low),
    close: Number(candle.record.close),
    volume: Number(candle.record.volume),
  };
}

function lastNonNull(values: ReadonlyArray<number | null> | undefined): number | null {
  if (!values) return null;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null) return values[index]!;
  }
  return null;
}

interface IndicatorParityRow {
  readonly id: string;
  readonly source: IndicatorSeriesSource;
  readonly value: number | null;
  /**
   * Human-readable fallback reason, set whenever the rendered value is NOT
   * the client-computed one the user would otherwise expect: either a
   * client/server value mismatch (`ParityVerdict`), or the client engine
   * refusing this indicator outright (e.g. BBANDS — not in
   * `verifiedIndicators.ts`'s whitelist for ANY catalog, per
   * `clientEngine.ts`'s module docstring). `null` only when the client value
   * was used as-is or nothing could be verified at all (DoD: a fallback must
   * always surface, but "nothing to fall back to" is not a fallback).
   */
  readonly fallbackReason: string | null;
}

function buildRow(
  overlay: OverlayEntry,
  primaryOutput: string,
  bars: readonly Bar[],
  catalog: readonly IndicatorCatalogEntry[],
  resolveServerSeries: ServerIndicatorSeriesPort,
): IndicatorParityRow {
  const params = DEFAULT_PARAMS[overlay.id] ?? {};
  // CH-18d: check the whitelist gate before ever calling computeIndicatorSeries — an
  // unverified name (or a pinned name whose live catalog entry drifted off its pin)
  // must never reach the client compute path at all, not merely throw inside it.
  if (!isVerifiedIndicator(overlay.id, catalog)) {
    const server = resolveServerSeries({ name: overlay.id, params, bars });
    if (server === null) {
      return { id: overlay.id, source: "unverified", value: null, fallbackReason: null };
    }
    // eslint-disable-next-line no-console -- DoD: a whitelist-rejected indicator must
    // still fall back to the server observably, never silently.
    console.warn(`CH-18d indicator not in verified whitelist, falling back to server: ${overlay.id}`);
    return {
      id: overlay.id,
      source: "server",
      value: lastNonNull(server[primaryOutput]),
      fallbackReason: "클라이언트 미검증: 화이트리스트 불일치",
    };
  }
  let client: IndicatorSeriesResult;
  try {
    client = computeIndicatorSeries({ name: overlay.id, params, bars, catalog });
  } catch (err) {
    if (!(err instanceof ClientEngineError)) throw err;
    const server = resolveServerSeries({ name: overlay.id, params, bars });
    if (server === null) {
      return { id: overlay.id, source: "unverified", value: null, fallbackReason: null };
    }
    // eslint-disable-next-line no-console -- DoD: an indicator the client engine refuses must still
    // fall back to the server observably, never silently (same rule as the parity-mismatch branch below).
    console.warn(`CH-18c indicator not client-verified, falling back to server: ${overlay.id} (${err.code})`);
    return {
      id: overlay.id,
      source: "server",
      value: lastNonNull(server[primaryOutput]),
      fallbackReason: `클라이언트 미검증: ${err.code}`,
    };
  }
  const server = resolveServerSeries({ name: overlay.id, params, bars });
  const resolved = resolveIndicatorSeries({
    name: overlay.id,
    client,
    server,
    onMismatch: (verdict) => {
      // eslint-disable-next-line no-console -- DoD: fallback must surface observably, never silently.
      console.warn(`CH-18b indicator parity fallback: ${verdict.name} maxAbsError=${verdict.maxAbsError}`);
    },
  });
  return {
    id: overlay.id,
    source: resolved.source,
    value: lastNonNull(resolved.series?.[primaryOutput]),
    fallbackReason:
      resolved.verdict && !resolved.verdict.ok ? `최대오차 ${resolved.verdict.maxAbsError.toExponential(3)}` : null,
  };
}

export interface IndicatorParityPanelProps {
  readonly candles: readonly StreamCandle[];
  readonly overlays: readonly OverlayEntry[];
  /** IND-12 카탈로그 항목. 비어 있으면(기본값) 아무 지표도 검증 대상이 아니다(fail-closed). */
  readonly catalog: readonly IndicatorCatalogEntry[];
  /** 서버 참조 시리즈 포트. 기본값은 "아직 없음"(항상 null) — 실 배선 전까지 fail-closed. */
  readonly resolveServerSeries?: ServerIndicatorSeriesPort;
}

const NO_SERVER_SERIES: ServerIndicatorSeriesPort = () => null;

export function IndicatorParityPanel({
  candles,
  overlays,
  catalog,
  resolveServerSeries = NO_SERVER_SERIES,
}: IndicatorParityPanelProps) {
  const bars = useMemo(() => candles.map(toBar), [candles]);

  // Every selected overlay gets a row, whitelisted or not — a rejected
  // indicator (BBANDS, or any name the live catalog no longer pins) must
  // still surface as unverified/server-fallback, never disappear silently
  // (see buildRow; this used to pre-filter on the whitelist and dropped the
  // overlay before it ever got a chance to fall back — CH-18c).
  const rows = overlays.map((overlay) => buildRow(overlay, overlay.outputs[0]!.name, bars, catalog, resolveServerSeries));

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
        </p>
      ))}
    </section>
  );
}
