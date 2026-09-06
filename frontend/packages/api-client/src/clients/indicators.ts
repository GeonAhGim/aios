// CH-11 indicators 조회 클라이언트. 백엔드 SSOT: IND-12(task-1730, 934b8d1)
// src/api/routers/indicators.py `GET /v1/indicators` -> ApiResponse[IndicatorListView]
// (schemas/indicators.py IndicatorListItemView: name/tier/category/version/hash/
// inputs/outputs). 경로 문자열은 apiPaths.ts(indicators.list)에만 있다 — 이 파일은
// resolvePath로만 해석한다(하드코딩 금지, task-1731 decision).
//
// 커서는 마지막 항목 이름 그대로인 불투명 문자열(schemas/indicators.py decode_cursor)
// — positions.ts와 동일하게 meta.page.next_cursor를 해석하지 않고 그대로 되돌린다.
//
// 미지 필드 폴백 금지(charting.ts와 동일 관용): 항목 파싱은 각 필드 타입을
// 엄격히 검사하고, 서버 응답이 계약과 다르면 throw한다 — 빈 배열/기본값으로
// 뭉개지 않는다. 이 throw는 chart-engine `plugins/indicatorPlugin.ts`의
// `loadIndicatorCatalog`가 routeApiError로 분류해 표면화한다(이 파일은 에러를
// 잡거나 재분류하지 않는다).
import { resolveEnvelope, resolvePath } from "../apiPaths";
import { keysToSnake } from "../caseConvert";
import { ApiClientBase, type EnvelopeWithMeta } from "../http";

export type IndicatorTier = "core" | "oss" | "script";

export interface IndicatorCatalogItem {
  readonly name: string;
  readonly tier: IndicatorTier;
  readonly category: string;
  readonly version: string;
  readonly hash: string;
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
}

export interface ListIndicatorsParams {
  readonly q?: string;
  readonly category?: string;
  /** 이전 응답의 nextCursor를 그대로. 첫 페이지면 생략. */
  readonly cursor?: string;
  /** 서버 제약 1~200(기본 50, indicators.py `_PAGE_MAX`). */
  readonly limit?: number;
}

export interface ListIndicatorsResult {
  readonly items: readonly IndicatorCatalogItem[];
  /** meta.page.next_cursor 그대로(문자열). 마지막 페이지면 null. */
  readonly nextCursor: string | null;
}

const KNOWN_TIERS: readonly IndicatorTier[] = ["core", "oss", "script"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function requireString(record: Record<string, unknown>, field: string): string {
  const value = record[field];
  if (typeof value !== "string") throw new Error(`indicators client: "${field}" missing or not a string`);
  return value;
}

function requireStringArray(record: Record<string, unknown>, field: string): readonly string[] {
  const value = record[field];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`indicators client: "${field}" missing or not a string array`);
  }
  return value;
}

function requireTier(record: Record<string, unknown>, field: string): IndicatorTier {
  const value = record[field];
  if (typeof value !== "string" || !(KNOWN_TIERS as readonly string[]).includes(value)) {
    throw new Error(`indicators client: "${field}" is not a known tier`);
  }
  return value as IndicatorTier;
}

function toCatalogItem(raw: unknown): IndicatorCatalogItem {
  if (!isRecord(raw)) throw new Error("indicators client: item is not an object");
  return {
    name: requireString(raw, "name"),
    tier: requireTier(raw, "tier"),
    category: requireString(raw, "category"),
    version: requireString(raw, "version"),
    hash: requireString(raw, "hash"),
    inputs: requireStringArray(raw, "inputs"),
    outputs: requireStringArray(raw, "outputs"),
  };
}

class IndicatorsApiClient extends ApiClientBase {
  // positions.ts fetchByRoute와 동일한 if-fallthrough 관용(apiPaths.clientsScan.test.ts
  // task-1160 가드가 삼항 대신 이 형태도 "레지스트리 경유"로 인정한다) — resolveEnvelope
  // 값이 최종 분기를 결정하고, 이 파일이 스스로 봉투 여부를 단정하지 않는다.
  private async fetchList(path: string): Promise<EnvelopeWithMeta<unknown>> {
    if (resolveEnvelope("indicators.list")) {
      const result = await this.requestEnvelopeWithMeta<unknown>(path);
      return { data: keysToSnake(result.data), meta: result.meta };
    }
    return { data: keysToSnake(await this.request<unknown>(path)), meta: null };
  }

  async listIndicators(params: ListIndicatorsParams = {}): Promise<ListIndicatorsResult> {
    const path = this.withQuery(resolvePath("indicators.list"), {
      q: params.q,
      category: params.category,
      cursor: params.cursor,
      limit: params.limit,
    });
    const { data, meta } = await this.fetchList(path);
    const items = isRecord(data) && Array.isArray(data.items) ? data.items.map(toCatalogItem) : [];
    return { items, nextCursor: meta?.page?.next_cursor ?? null };
  }
}

export interface IndicatorsClient {
  listIndicators(params?: ListIndicatorsParams): Promise<ListIndicatorsResult>;
}

export function createIndicatorsClient(baseUrl: string, getToken: () => string | null): IndicatorsClient {
  const client = new IndicatorsApiClient(baseUrl, getToken);
  return { listIndicators: (params) => client.listIndicators(params) };
}
