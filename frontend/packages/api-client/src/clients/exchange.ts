import type {
  AccountBalance,
  CredentialRequest,
  CredentialResponse,
  ExchangeCapability,
} from "@aios/shared-types";
import { resolveEnvelope, resolvePath } from "../apiPaths";
import type { AnyConstructor } from "../http";

// task-4002(FE-OPS-10b): src/data/models/trading.py:88-109 Position 1:1 대응(camelCase).
// 별도 shared-types 파일을 새로 만들지 않는다 — 이 leaf의 files 범위가 이 파일
// 하나뿐이라(task-2437 split), Position 응답 형태를 쓰는 곳이 이 클라이언트 하나뿐인
// 지금은 로컬 타입으로 충분하다. 소비하는 화면이 생기면 그때 shared-types로 승격한다.
export interface ExchangePositionMoney {
  amount: string;
  currency: string;
}

export interface ExchangePosition {
  symbol: string;
  exchange: string;
  strategyId: string;
  executionId: number | null;
  quantity: string;
  averageEntryPrice: ExchangePositionMoney;
  currentPrice: ExchangePositionMoney;
  unrealizedPnl: ExchangePositionMoney;
  realizedPnl: ExchangePositionMoney;
  leverage: string;
  margin: ExchangePositionMoney | null;
  entryTime: string;
  updatedAt: string;
  assetClass: string;
  optionType: string | null;
  strikePrice: string | null;
  expiryDate: string | null;
  contractMultiplier: string | null;
  underlyingSymbol: string | null;
}

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

    async getExchangePositions(exchange: string): Promise<ExchangePosition[]> {
      const path = resolvePath("exchange.credentials.positions").replace(":exchange", exchange);
      return resolveEnvelope("exchange.credentials.positions") ? this.requestEnvelope(path) : this.request(path);
    }
  };
}
