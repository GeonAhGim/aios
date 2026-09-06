// CH-8 charting 클라이언트 — 서버 SSOT: src/api/routers/charting.py(CH-5, task-1557
// 06e5560) + src/foundation/charting/contracts/v1.py. 7개 엔드포인트 전부
// `-> ApiResponse[...]`(ok())라 envelope=true(apiPaths.ts charting.layouts.*).
// 경로 문자열은 apiPaths.ts 레지스트리에만 있다(scripts.ts와 동일 관용).
//
// `layoutState`(=CH-8 layout/layoutModel.ts가 인코딩한 값)는 그냥 오갈 뿐, 이
// 클라이언트는 그 내부 스키마를 모른다(디코딩은 chart-engine persistence.ts 몫) —
// http.ts의 keysToCamel/keysToSnake가 깊은 재귀 변환이라 아무 특수 처리 없이도
// camelCase 왕복이 그대로 보존된다.
//
// 드로잉 문서만 예외다: CH-4 serialize.ts의 `DrawingsDocument`는 최상위 키가
// 의도적으로 스네이크(`schema_version`) — 자동 camelCase 변환이 이를
// `schemaVersion`으로 바꿔 버리므로, fromDrawingsDocument가 기대하는 리터럴 키를
// 여기서 한 번 복원한다(중첩 `lineWidth` 등은 애초에 camelCase라 원본 그대로
// 왕복되므로 손대지 않는다).
//
// 404/409는 http.ts가 buildApiError로 ApiError를 던지고, persistence.ts가
// routeApiError로 분류한다 — 이 파일은 에러를 잡거나 재분류하지 않는다(positions.ts와
// 동일 관용). 서버 필드가 없거나 타입이 다르면 폴백하지 않고 그대로 throw한다(decision:
// "미지 필드 폴백 금지").
import { resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { keysToSnake } from "../caseConvert";
import { ApiClientBase } from "../http";

export interface ChartLayoutRecord {
  readonly id: string;
  readonly tenantId: string;
  readonly ownerSubjectId: string;
  readonly name: string;
  /** Opaque — encode/decode with `@aios/chart-engine` `layout/layoutModel.ts`. */
  readonly layoutState: Record<string, unknown>;
  readonly revision: number;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DrawingsDocumentRecord {
  readonly layoutId: string;
  /** Raw CH-4 document shape (`{schema_version, drawings}`) — decode with `fromDrawingsDocument`. */
  readonly document: { readonly schema_version: unknown; readonly drawings: unknown };
  readonly revision: number;
  readonly updatedAt: string;
}

export interface CreateChartLayoutInput {
  readonly name: string;
  readonly layoutState: Record<string, unknown>;
}

export interface UpdateChartLayoutInput {
  readonly expectedRevision: number;
  readonly name?: string;
  readonly layoutState?: Record<string, unknown>;
}

export interface PutDrawingsInput {
  readonly expectedRevision: number;
  readonly schemaVersion: number;
  readonly drawings: readonly unknown[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(data: unknown, field: string): Record<string, unknown> {
  if (!isRecord(data)) throw new Error(`charting client: response is not an object (expected "${field}")`);
  const value = (data as Record<string, unknown>)[field];
  if (!isRecord(value)) throw new Error(`charting client: "${field}" missing or not an object`);
  return value;
}

function requireString(data: Record<string, unknown>, field: string): string {
  const value = data[field];
  if (typeof value !== "string") throw new Error(`charting client: "${field}" missing or not a string`);
  return value;
}

function requireNumber(data: Record<string, unknown>, field: string): number {
  const value = data[field];
  if (typeof value !== "number") throw new Error(`charting client: "${field}" missing or not a number`);
  return value;
}

function toLayoutRecord(raw: unknown): ChartLayoutRecord {
  if (!isRecord(raw)) throw new Error("charting client: layout response is not an object");
  return {
    id: requireString(raw, "id"),
    tenantId: requireString(raw, "tenantId"),
    ownerSubjectId: requireString(raw, "ownerSubjectId"),
    name: requireString(raw, "name"),
    layoutState: requireRecord(raw, "layoutState"),
    revision: requireNumber(raw, "revision"),
    createdAt: requireString(raw, "createdAt"),
    updatedAt: requireString(raw, "updatedAt"),
  };
}

// schema_version만 복원한다 — drawings[].lineWidth 등은 애초에 camelCase라
// keysToSnake(전송)→keysToCamel(수신) 왕복으로 원형 그대로다.
function toDrawingsRecord(raw: unknown): DrawingsDocumentRecord {
  if (!isRecord(raw)) throw new Error("charting client: drawings response is not an object");
  return {
    layoutId: requireString(raw, "layoutId"),
    document: { schema_version: raw.schemaVersion, drawings: raw.drawings },
    revision: requireNumber(raw, "revision"),
    updatedAt: requireString(raw, "updatedAt"),
  };
}

class ChartingApiClient extends ApiClientBase {
  // 경로 치환(:layoutId)이 있어 requestByRoute(치환 미지원)를 못 쓰는 조회/쓰기
  // 공통 분기 — marketplace.ts/exchange.ts와 동일한 resolveEnvelope 삼항 관용을
  // 한 곳에 모은 것뿐, resolvePath(...)를 request/requestEnvelope에 곧바로 넘기지
  // 않는다(apiPaths.clientsScan.test.ts task-1160 가드).
  private async requestItem<T>(route: ApiRouteName, path: string, init?: RequestInit): Promise<T> {
    return resolveEnvelope(route) ? this.requestEnvelope<T>(path, init) : this.request<T>(path, init);
  }

  private itemPath(layoutId: string): string {
    return resolvePath("charting.layouts.item").replace(":layoutId", encodeURIComponent(layoutId));
  }

  private drawingsPath(layoutId: string): string {
    return resolvePath("charting.layouts.drawings").replace(":layoutId", encodeURIComponent(layoutId));
  }

  async createLayout(input: CreateChartLayoutInput): Promise<ChartLayoutRecord> {
    const data = await this.requestByRoute<unknown>("charting.layouts.base", {
      method: "POST",
      body: JSON.stringify(keysToSnake({ name: input.name, layoutState: input.layoutState })),
    });
    return toLayoutRecord(data);
  }

  async listLayouts(): Promise<readonly ChartLayoutRecord[]> {
    const data = await this.requestByRoute<unknown[]>("charting.layouts.base");
    return data.map(toLayoutRecord);
  }

  async getLayout(layoutId: string): Promise<ChartLayoutRecord> {
    const data = await this.requestItem<unknown>("charting.layouts.item", this.itemPath(layoutId));
    return toLayoutRecord(data);
  }

  async updateLayout(layoutId: string, input: UpdateChartLayoutInput): Promise<ChartLayoutRecord> {
    const data = await this.requestItem<unknown>("charting.layouts.item", this.itemPath(layoutId), {
      method: "PATCH",
      body: JSON.stringify(
        keysToSnake({ expectedRevision: input.expectedRevision, name: input.name, layoutState: input.layoutState }),
      ),
    });
    return toLayoutRecord(data);
  }

  async deleteLayout(layoutId: string): Promise<void> {
    await this.requestItem<void>("charting.layouts.item", this.itemPath(layoutId), { method: "DELETE" });
  }

  async getDrawings(layoutId: string): Promise<DrawingsDocumentRecord> {
    const data = await this.requestItem<unknown>("charting.layouts.drawings", this.drawingsPath(layoutId));
    return toDrawingsRecord(data);
  }

  async putDrawings(layoutId: string, input: PutDrawingsInput): Promise<DrawingsDocumentRecord> {
    const data = await this.requestItem<unknown>("charting.layouts.drawings", this.drawingsPath(layoutId), {
      method: "PUT",
      body: JSON.stringify(
        keysToSnake({
          expectedRevision: input.expectedRevision,
          schemaVersion: input.schemaVersion,
          drawings: input.drawings,
        }),
      ),
    });
    return toDrawingsRecord(data);
  }
}

export interface ChartingClient {
  createLayout(input: CreateChartLayoutInput): Promise<ChartLayoutRecord>;
  listLayouts(): Promise<readonly ChartLayoutRecord[]>;
  getLayout(layoutId: string): Promise<ChartLayoutRecord>;
  updateLayout(layoutId: string, input: UpdateChartLayoutInput): Promise<ChartLayoutRecord>;
  deleteLayout(layoutId: string): Promise<void>;
  getDrawings(layoutId: string): Promise<DrawingsDocumentRecord>;
  putDrawings(layoutId: string, input: PutDrawingsInput): Promise<DrawingsDocumentRecord>;
}

export function createChartingClient(baseUrl: string, getToken: () => string | null): ChartingClient {
  const client = new ChartingApiClient(baseUrl, getToken);
  return {
    createLayout: (input) => client.createLayout(input),
    listLayouts: () => client.listLayouts(),
    getLayout: (layoutId) => client.getLayout(layoutId),
    updateLayout: (layoutId, input) => client.updateLayout(layoutId, input),
    deleteLayout: (layoutId) => client.deleteLayout(layoutId),
    getDrawings: (layoutId) => client.getDrawings(layoutId),
    putDrawings: (layoutId, input) => client.putDrawings(layoutId, input),
  };
}
