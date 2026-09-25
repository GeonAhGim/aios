import type {
  ComputeStatementRequest,
  CorrectStatementRequest,
  GetPerformanceStatementParams,
  ListPerformanceStatementsParams,
  PerformanceStatementListResponse,
  PerformanceStatementView,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-5804(FE-OPS-6): 실적 명세서(performance-statements) compute·목록·상세·
// correct 4라우트 클라이언트 — src/api/routers/foundation/performance.py 원문
// 기준. 4라우트 전부 `-> ApiResponse[...]`+`ok(...)`로 응답한다(envelope=true).
// compute/correct는 body가 있어 postEnvelope를 그대로 쓰고, list/get은 쿼리·경로
// 치환이 있어 requestByRoute를 못 쓰므로 marketplace.ts/notifications.ts와 동일한
// resolveEnvelope(route) ? requestEnvelope(path) : request(path) 삼항을 쓴다
// (apiPaths.clientsScan.test.ts task-1160 가드). scope=LIVE는 서버가
// UnsupportedStatementScopeError(422)로 거부한다(performance.py:73-74) —
// 프론트는 판정을 재구현하지 않고 그대로 ApiError로 올려보낸다.
export function withPerformance<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    // POST :compute는 202 Accepted로 응답한다 — postEnvelope는 상태 코드를
    // 가리지 않고 봉투(data)만 풀어 반환한다(executeRequestEnvelope, http.ts).
    async computePerformanceStatement(body: ComputeStatementRequest): Promise<PerformanceStatementView> {
      return this.postEnvelope(resolvePath("performanceStatements.compute"), body);
    }

    async listPerformanceStatements(
      params: ListPerformanceStatementsParams = {},
    ): Promise<PerformanceStatementListResponse> {
      const path = this.withQuery(resolvePath("performanceStatements.list"), {
        scope: params.scope,
        portfolio_id: params.portfolioId,
      });
      return resolveEnvelope("performanceStatements.list") ? this.requestEnvelope(path) : this.request(path);
    }

    async getPerformanceStatement(
      statementId: string,
      params: GetPerformanceStatementParams = {},
    ): Promise<PerformanceStatementView> {
      const base = resolvePath("performanceStatements.get").replace(":statementId", statementId);
      const path = this.withQuery(base, { portfolio_id: params.portfolioId });
      return resolveEnvelope("performanceStatements.get") ? this.requestEnvelope(path) : this.request(path);
    }

    async correctPerformanceStatement(
      statementId: string,
      body: CorrectStatementRequest,
    ): Promise<PerformanceStatementView> {
      const path = resolvePath("performanceStatements.correct").replace(":statementId", statementId);
      return this.postEnvelope(path, body);
    }
  };
}
