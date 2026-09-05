// LB-19 positions 조회 클라이언트(§3.2 (B) 계약, §9 LB-19 라우트). 백엔드 SSOT는
// src/api/routers/positions.py(task-1377 d83d79c) + src/api/schemas/positions.py다:
//   GET /v1/positions?account_id&instrument_id          -> ApiResponse[PositionListResponse]
//   GET /v1/positions/nav?account_id&start_date&end_date -> ApiResponse[NavSeriesResponse]
//   GET /v1/positions/{position_key}/journal?cursor&limit -> ApiResponse[PositionJournalResponse]
// 쓰기 엔드포인트는 없다(저널 append는 application 커맨드 전용) — 이 파일도 GET만 있다.
// 경로 문자열은 apiPaths.ts 레지스트리에만 있고(positions.*), 봉투 여부도 거기서 읽는다.
//
// 응답 파싱은 shared-types positionView.ts의 parsePositionSnapshot/parseNavSnapshot을
// 항목마다 그대로 재사용한다(새 파서 금지, task-1524 decision). 파싱 실패는 throw하지
// 않고 판별 결과 객체를 그대로 돌려준다(marketData.ts와 동일 관용). 저널 항목
// (PositionJournalEntryView)은 shared-types에 파서가 아직 없어 raw(snake_case 복원)로
// 돌려주고 표시 컴포넌트가 방어적으로 그린다 — 여기서 파서를 새로 만들지 않는다.
//
// 저널 커서는 불투명 문자열이다(schemas/positions.py encode_cursor = str(sequence_no)).
// 숫자로 바꾸거나 해석하지 않고 meta.page.next_cursor를 받은 그대로 다음 요청의
// cursor 쿼리에 실어 보낸다 — useCursorPage(CursorNavigatorMeta.next_cursor)와 동일 계약.
//
// 404/403/5xx는 http.ts가 buildApiError로 ApiError를 던지고, 화면이 routeApiError로
// 분류한다 — 이 파일은 에러를 잡거나 재분류하지 않는다.
import {
  parseNavSnapshot,
  parsePositionSnapshot,
  type ParsedNavSnapshot,
  type ParsedPositionSnapshot,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { keysToSnake } from "../caseConvert";
import { ApiClientBase, type EnvelopeWithMeta } from "../http";

export interface PositionListParams {
  /** UUID 문자열. 생략하면 테넌트 전체. */
  accountId?: string;
  instrumentId?: string;
}

export interface PositionListResult {
  items: ParsedPositionSnapshot[];
  /** 봉투 meta.as_of(§3.3). 봉투가 없으면 null — Date.now()로 대체하지 않는다(task-936). */
  asOf: string | null;
}

export interface PositionJournalParams {
  positionKey: string;
  /** 이전 응답의 nextCursor를 그대로. 첫 페이지면 생략. */
  cursor?: string;
  /** 서버 제약 1~200(기본 50). 검증은 서버가 한다(VALIDATION_INVALID_FIELD). */
  limit?: number;
}

export interface PositionJournalResult {
  positionKey: string;
  /** PositionJournalEntryView(contracts/v1.py) raw 항목 — snake_case 키. */
  items: unknown[];
  /** meta.page.next_cursor 그대로(문자열). 마지막 페이지면 null. */
  nextCursor: string | null;
  asOf: string | null;
}

export interface NavSeriesParams {
  accountId: string;
  /** ISO date(YYYY-MM-DD). */
  startDate: string;
  endDate: string;
}

export interface NavSeriesResult {
  accountId: string;
  startDate: string;
  endDate: string;
  items: ParsedNavSnapshot[];
  /** 범위 안에서 NAV가 산출되지 않은 날(YYYY-MM-DD). 0으로 채우지 않는다(FD-3.3). */
  missingDates: string[];
  asOf: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asStringArray(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === "string");
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

class PositionsApiClient extends ApiClientBase {
  // request/requestEnvelopeWithMeta는 data 키를 camelCase로 바꾼다(http.ts). 파서는
  // contracts/v1.py 그대로의 snake_case를 기대하므로 keysToSnake로 되돌린다(marketData.ts).
  private async fetchByRoute(route: ApiRouteName, path: string): Promise<EnvelopeWithMeta<unknown>> {
    if (resolveEnvelope(route)) {
      const result = await this.requestEnvelopeWithMeta<unknown>(path);
      return { data: keysToSnake(result.data), meta: result.meta };
    }
    return { data: keysToSnake(await this.request<unknown>(path)), meta: null };
  }

  async listPositions(params: PositionListParams = {}): Promise<PositionListResult> {
    const path = this.withQuery(resolvePath("positions.list"), {
      account_id: params.accountId,
      instrument_id: params.instrumentId,
    });
    const { data, meta } = await this.fetchByRoute("positions.list", path);
    const items = isRecord(data) ? asArray(data.items) : [];
    return { items: items.map((item) => parsePositionSnapshot(item)), asOf: meta?.as_of ?? null };
  }

  async getPositionJournal(params: PositionJournalParams): Promise<PositionJournalResult> {
    const base = resolvePath("positions.journal").replace(
      ":positionKey",
      encodeURIComponent(params.positionKey),
    );
    const path = this.withQuery(base, { cursor: params.cursor, limit: params.limit });
    const { data, meta } = await this.fetchByRoute("positions.journal", path);
    const record = isRecord(data) ? data : {};
    return {
      positionKey: asString(record.position_key) || params.positionKey,
      items: asArray(record.items),
      nextCursor: meta?.page?.next_cursor ?? null,
      asOf: meta?.as_of ?? null,
    };
  }

  async getNavSeries(params: NavSeriesParams): Promise<NavSeriesResult> {
    const path = this.withQuery(resolvePath("positions.nav"), {
      account_id: params.accountId,
      start_date: params.startDate,
      end_date: params.endDate,
    });
    const { data, meta } = await this.fetchByRoute("positions.nav", path);
    const record = isRecord(data) ? data : {};
    return {
      accountId: asString(record.account_id) || params.accountId,
      startDate: asString(record.start_date) || params.startDate,
      endDate: asString(record.end_date) || params.endDate,
      items: asArray(record.items).map((item) => parseNavSnapshot(item)),
      missingDates: asStringArray(record.missing_dates),
      asOf: meta?.as_of ?? null,
    };
  }
}

export interface PositionsClient {
  listPositions(params?: PositionListParams): Promise<PositionListResult>;
  getPositionJournal(params: PositionJournalParams): Promise<PositionJournalResult>;
  getNavSeries(params: NavSeriesParams): Promise<NavSeriesResult>;
}

export function createPositionsClient(baseUrl: string, getToken: () => string | null): PositionsClient {
  const client = new PositionsApiClient(baseUrl, getToken);
  return {
    listPositions: (params) => client.listPositions(params),
    getPositionJournal: (params) => client.getPositionJournal(params),
    getNavSeries: (params) => client.getNavSeries(params),
  };
}
