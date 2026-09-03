import type {
  AccountBalance,
  CredentialRequest,
  CredentialResponse,
  ExchangeCapability,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// FD-12 거래소 연동 — exchange-credentials 라우터는 봉투 미적용, 기존 경로 유지.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
// task-1159: listExchangeCredentials는 requestByRoute로, :exchange 치환이 필요한
// getExchangeBalance/getExchangeCapabilities는 (경로 치환 자체는 requestByRoute가
// 지원하지 않아) resolvePath로 경로를 만들고 resolveEnvelope(route)로 request/
// requestEnvelope 분기만 apiPaths.ts 레지스트리 단일 출처로 이관했다 — 분기
// 결과는 동일(둘 다 envelope=false → request() 경로 유지).
export function withExchange<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    async registerExchangeCredential(
      body: CredentialRequest,
      idempotencyKey?: string,
    ): Promise<CredentialResponse> {
      return this.postIdempotent(resolvePath("exchange.credentials.base"), body, idempotencyKey);
    }

    async listExchangeCredentials(): Promise<CredentialResponse[]> {
      return this.requestByRoute("exchange.credentials.base");
    }

    async revokeExchangeCredential(exchange: string): Promise<{ exchange: string; status: string }> {
      return this.del(resolvePath("exchange.credentials.item").replace(":exchange", exchange));
    }

    async getExchangeBalance(exchange: string): Promise<AccountBalance[]> {
      const path = resolvePath("exchange.credentials.balance").replace(":exchange", exchange);
      return resolveEnvelope("exchange.credentials.balance") ? this.requestEnvelope(path) : this.request(path);
    }

    async getExchangeCapabilities(exchange: string): Promise<ExchangeCapability> {
      const path = resolvePath("exchange.credentials.capabilities").replace(":exchange", exchange);
      return resolveEnvelope("exchange.credentials.capabilities") ? this.requestEnvelope(path) : this.request(path);
    }
  };
}
