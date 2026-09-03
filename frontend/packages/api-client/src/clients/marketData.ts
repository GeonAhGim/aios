// market_data 조회 클라이언트(§3.1 (A)). 백엔드 계약 SSOT는 LA-17(task-624,
// 7ad6d15) `application/get_candles`·`application/replay_candles`와
// `src/foundation/market_data/contracts/v1.py`다. 이 파일은 그 두 조회를
// http.ts(ApiClientBase)의 request/requestEnvelope 계열로만 호출한다(자체
// fetch 금지) — 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다.
//
// 응답 파싱은 새 파서를 만들지 않고 shared-types의 parseCandleSeries를 그대로
// 재사용한다(candleSeries.ts, task-629). 파싱 실패는 throw나 빈 배열로 뭉개지
// 않고 그 판별 결과 객체(ParsedCandleSeries)를 그대로 호출부에 돌려준다.
//
// CandleSeries/ReplaySeries(contracts/v1.py)에는 QualityVerdict 필드가 없다 —
// 다만 107번 §8("필드 추가는 minor") 관례상 응답이 부가 `quality` 필드를 얹어
// CandleQualityBadge(task-629)에 바로 공급할 가능성을 배제하지 않으므로,
// 있으면 parseQualityVerdict로 함께 판별하고 없으면 null로 둔다 — 계약에 없는
// 필드를 강제로 요구하지는 않는다.
import {
  parseCandleSeries,
  parseQualityVerdict,
  type Adjustment,
  type ParsedCandleSeries,
  type ParsedQualityVerdict,
  type Timeframe,
  type Venue,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { keysToSnake } from "../caseConvert";
import { ApiClientBase } from "../http";

// contracts/v1.py Timeframe enum과 동일한 화이트리스트. shared-types의
// candleSeries.ts는 이 목록을 export하지 않으므로(내부 파서 전용) 여기서는
// 요청 전 거부용으로만 별도로 둔다 — 응답 판별은 여전히 parseCandleSeries가
// 전담한다(새 파서 아님, 입력 가드일 뿐).
const KNOWN_TIMEFRAMES: readonly Timeframe[] = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"];

function isKnownTimeframe(value: string): value is Timeframe {
  return (KNOWN_TIMEFRAMES as readonly string[]).includes(value);
}

export interface CandleQueryParams {
  venue: Venue;
  instrumentId: string;
  timeframe: Timeframe;
  /** ISO datetime 문자열(UTC). Date 변환은 하지 않는다 — candleSeries.ts와 동일 원칙. */
  start: string;
  end: string;
  /** 생략 시 서버가 "지금"으로 취급한다(get_candles.py `_effective_as_of`). */
  asOf?: string;
  /** 생략 시 RAW(계약 기본값). */
  adjustment?: Adjustment;
}

// ReplayRequest(contracts/v1.py)는 as_of를 필수로 재정의한다.
export interface ReplayQueryParams extends Omit<CandleQueryParams, "asOf"> {
  asOf: string;
}

export interface CandleQueryResult {
  series: ParsedCandleSeries;
  /** 응답에 `quality` 필드가 없으면 null(현재 계약 기준 — 위 파일 주석 참고). */
  quality: ParsedQualityVerdict | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

// candleSeries.ts의 unwrapEnvelope와 같은 관용(ApiResponse 봉투가 있으면 그
// 안을, 없으면 raw 자체를 본다)을 여기서는 `quality` 서브필드를 꺼내는
// 용도로만 별도로 쓴다 — parseCandleSeries/parseQualityVerdict 자체의 판별
// 로직은 재구현하지 않는다.
function unwrapDataEnvelope(raw: unknown): unknown {
  return isRecord(raw) && "data" in raw ? raw.data : raw;
}

function toCandleQueryResult(raw: unknown): CandleQueryResult {
  const series = parseCandleSeries(raw);
  const candidate = unwrapDataEnvelope(raw);
  const qualityRaw = isRecord(candidate) ? candidate.quality : undefined;
  const quality = qualityRaw === undefined ? null : parseQualityVerdict(qualityRaw);
  return { series, quality };
}

function requireKnownTimeframe(timeframe: Timeframe): void {
  if (!isKnownTimeframe(timeframe)) {
    throw new Error(`market_data: 알 수 없는 timeframe입니다("${timeframe}").`);
  }
}

// get_candles.py의 `QuarantinedViewUnsupportedError`가 include_quarantined=True를
// 명시적으로 거부한다 — 지원되지 않는 파라미터를 노출하지 않고 아예 뺀다
// (foundation.ts의 paper-control LIVE 파라미터 비노출과 같은 원칙).
function toQuery(params: CandleQueryParams): Record<string, string> {
  const query: Record<string, string> = {
    venue: params.venue,
    instrument_id: params.instrumentId,
    timeframe: params.timeframe,
    start: params.start,
    end: params.end,
    adjustment: params.adjustment ?? "RAW",
  };
  if (params.asOf !== undefined) query.as_of = params.asOf;
  return query;
}

class MarketDataApiClient extends ApiClientBase {
  private async fetchCandles(route: ApiRouteName, query: Record<string, string>): Promise<CandleQueryResult> {
    const path = this.withQuery(resolvePath(route), query);
    const raw = resolveEnvelope(route)
      ? await this.requestEnvelope<unknown>(path)
      : await this.request<unknown>(path);
    // request/requestEnvelope는 응답 키를 camelCase로 바꾼다(http.ts fetchJson).
    // parseCandleSeries/parseQualityVerdict는 contracts/v1.py 그대로의
    // snake_case를 기대하므로(memberships.ts와 동일 관용) keysToSnake로 되돌린다.
    return toCandleQueryResult(keysToSnake(raw));
  }

  async getCandles(params: CandleQueryParams): Promise<CandleQueryResult> {
    requireKnownTimeframe(params.timeframe);
    return this.fetchCandles("marketData.candles.get", toQuery(params));
  }

  async replayCandles(params: ReplayQueryParams): Promise<CandleQueryResult> {
    requireKnownTimeframe(params.timeframe);
    return this.fetchCandles("marketData.candles.replay", toQuery(params));
  }
}

export interface MarketDataClient {
  getCandles(params: CandleQueryParams): Promise<CandleQueryResult>;
  replayCandles(params: ReplayQueryParams): Promise<CandleQueryResult>;
}

export function createMarketDataClient(baseUrl: string, getToken: () => string | null): MarketDataClient {
  const client = new MarketDataApiClient(baseUrl, getToken);
  return {
    getCandles: (params) => client.getCandles(params),
    replayCandles: (params) => client.replayCandles(params),
  };
}
