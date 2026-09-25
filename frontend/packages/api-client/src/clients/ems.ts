import type {
  AlgoProgressResponse,
  TcaResultResponse,
  ComputeTcaRequest,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// EM-18: 알고리즘 집행 진행률, TCA 리포트 API 클라이언트.
// src/api/routers/foundation/ems/algo.py와 ems/tca.py 기준.
// 전부 ApiResponse[...] envelope 응답이다(contracts/openapi/v1.json 확인).
// ":param" 치환이 있어 requestByRoute를 못 쓰므로 compliance.ts의
// getComplianceDecision과 동일한 resolveEnvelope(route) ? requestEnvelope(path) :
// request(path) 삼항을 쓴다(apiPaths.clientsScan.test.ts task-1160이 이 형태만 인정한다).

// task-6856: fills/bars가 빈 배열이면 백엔드가 TCA를 계산할 수 없으므로(분모 0),
// 요청을 보내기 전에 여기서 막는다 — foundation.ts의 requireIdempotencyKey와
// 동일하게 런타임에서 거부하는 관용.
function requireNonEmptyArray(value: unknown[], field: string): void {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error(`computeTca: "${field}"는 빈 배열일 수 없습니다.`);
  }
}

export function withEms<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getAlgoProgress(parentId: string): Promise<AlgoProgressResponse> {
      const path = resolvePath("ems.algo.progress").replace(":parentId", parentId);
      return resolveEnvelope("ems.algo.progress") ? this.requestEnvelope(path) : this.request(path);
    }

    async getLatestTca(parentId: string): Promise<TcaResultResponse> {
      const path = resolvePath("ems.tca.latest").replace(":parentId", parentId);
      return resolveEnvelope("ems.tca.latest") ? this.requestEnvelope(path) : this.request(path);
    }

    async getTcaRevision(
      parentId: string,
      revision: number,
    ): Promise<TcaResultResponse> {
      const path = resolvePath("ems.tca.revision")
        .replace(":parentId", parentId)
        .replace(":revision", revision.toString());
      return resolveEnvelope("ems.tca.revision") ? this.requestEnvelope(path) : this.request(path);
    }

    async computeTca(
      parentId: string,
      request: ComputeTcaRequest,
    ): Promise<TcaResultResponse> {
      requireNonEmptyArray(request.fills, "fills");
      requireNonEmptyArray(request.bars, "bars");
      const path = resolvePath("ems.tca.compute").replace(":parentId", parentId);
      return this.postEnvelope(path, request);
    }
  };
}
