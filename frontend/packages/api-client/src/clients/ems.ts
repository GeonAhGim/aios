import type {
  AlgoProgressResponse,
  TcaResultResponse,
  ComputeTcaRequest,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// EM-18: 알고리즘 집행 진행률, TCA 리포트 API 클라이언트.
// src/api/routers/foundation/ems/algo.py와 ems/tca.py 기준.
// 전부 ApiResponse[...] envelope 응답이다(contracts/openapi/v1.json 확인).
export function withEms<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async getAlgoProgress(parentId: string): Promise<AlgoProgressResponse> {
      const path = resolvePath("ems.algo.progress").replace(":parentId", parentId);
      return this.requestEnvelope(path);
    }

    async getLatestTca(parentId: string): Promise<TcaResultResponse> {
      const path = resolvePath("ems.tca.latest").replace(":parentId", parentId);
      return this.requestEnvelope(path);
    }

    async getTcaRevision(
      parentId: string,
      revision: number,
    ): Promise<TcaResultResponse> {
      const path = resolvePath("ems.tca.revision")
        .replace(":parentId", parentId)
        .replace(":revision", revision.toString());
      return this.requestEnvelope(path);
    }

    async computeTca(
      parentId: string,
      request: ComputeTcaRequest,
    ): Promise<TcaResultResponse> {
      const path = resolvePath("ems.tca.compute").replace(":parentId", parentId);
      return this.postEnvelope(path, request);
    }
  };
}
