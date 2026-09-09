import type {
  AccountConnectionView,
  AccountSnapshotView,
  BeginConnectionRequest,
  ConnectionListResponse,
} from "@aios/shared-types";
import { resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-2346(FE-OPS-5): 계정 연동(connections) 목록·생성·confirm·sync·revoke
// 클라이언트 — src/api/routers/foundation/connections.py 원문 기준. 5라우트 전부
// `-> ApiResponse[...]`+`ok(...)`로 응답하므로(envelope=true) requestByRoute/
// postEnvelope만 쓴다(trust.ts/reconciliation.ts와 동일 관용, 새 파서 없음).
// 목록(GET)·생성(POST)은 같은 경로("connections.base")를 공유한다.
export function withConnections<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async listConnections(): Promise<ConnectionListResponse> {
      return this.requestByRoute("connections.base");
    }

    async beginConnection(body: BeginConnectionRequest): Promise<AccountConnectionView> {
      return this.postEnvelope(resolvePath("connections.base"), body);
    }

    async confirmConnection(connectionId: string): Promise<AccountConnectionView> {
      const path = resolvePath("connections.confirm").replace(":connectionId", connectionId);
      return this.postEnvelope(path);
    }

    async syncConnection(connectionId: string): Promise<AccountSnapshotView> {
      const path = resolvePath("connections.sync").replace(":connectionId", connectionId);
      return this.postEnvelope(path);
    }

    async revokeConnection(connectionId: string): Promise<AccountConnectionView> {
      const path = resolvePath("connections.revoke").replace(":connectionId", connectionId);
      return this.postEnvelope(path);
    }
  };
}
