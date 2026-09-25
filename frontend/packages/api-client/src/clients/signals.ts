// task-5998(SIG-6): SignalSourcesPage.tsx(시크릿 발급·회전·최근 수신 로그)가 쓰는
// 클라이언트. follow.ts(task-2699)와 동일 관용 — apiRoutes.ts의 signals.sources.*가
// 유령 경로(implemented=false)라 실제 fetch를 시도하기 전에 typed 오류로
// 단락한다. AiosApiClient 합성(client.ts)에는 얹지 않고 화면이
// createSignalsClient로 직접 만든다 — 라우터가 생기면(src/api/routers/signals.py)
// apiRoutes.ts의 implemented만 true로 바꾸면 이 단락 없이 그대로 배선된다.
import type {
  SignalReceiptListResponse,
  SignalSourceCreateRequest,
  SignalSourceListResponse,
  SignalSourceSecretIssueResponse,
} from "@aios/shared-types";
import { isRouteImplemented, resolveEnvelope, resolvePath, type ApiRouteName } from "../apiPaths";
import { ApiClientBase } from "../http";

type SignalsRouteName =
  | "signals.sources.base"
  | "signals.sources.rotate"
  | "signals.sources.disable"
  | "signals.sources.receipts";

const NOT_IMPLEMENTED_MESSAGE: Record<SignalsRouteName, string> = {
  "signals.sources.base": "신호 소스 API가 아직 제공되지 않습니다.",
  "signals.sources.rotate": "신호 소스 시크릿 회전 API가 아직 제공되지 않습니다.",
  "signals.sources.disable": "신호 소스 비활성화 API가 아직 제공되지 않습니다.",
  "signals.sources.receipts": "신호 수신 로그 API가 아직 제공되지 않습니다.",
};

// follow.ts의 FollowRouteNotImplementedError와 동일 패턴 — 호출부(SignalSourcesPage.tsx)가
// 문자열 매칭 대신 instanceof/route 필드로 "아직 없는 라우트"를 판별할 수 있게 한다.
export class SignalsRouteNotImplementedError extends Error {
  readonly route: ApiRouteName;

  constructor(route: SignalsRouteName) {
    super(NOT_IMPLEMENTED_MESSAGE[route]);
    this.name = "SignalsRouteNotImplementedError";
    this.route = route;
  }
}

function assertImplemented(route: SignalsRouteName): void {
  if (!isRouteImplemented(route)) {
    throw new SignalsRouteNotImplementedError(route);
  }
}

class SignalsApiClient extends ApiClientBase {
  async listSources(): Promise<SignalSourceListResponse> {
    assertImplemented("signals.sources.base");
    const path = resolvePath("signals.sources.base");
    return resolveEnvelope("signals.sources.base") ? this.requestEnvelope(path) : this.request(path);
  }

  async issueSource(body: SignalSourceCreateRequest): Promise<SignalSourceSecretIssueResponse> {
    assertImplemented("signals.sources.base");
    return this.postEnvelope(resolvePath("signals.sources.base"), body);
  }

  async rotateSecret(sourceId: number): Promise<SignalSourceSecretIssueResponse> {
    assertImplemented("signals.sources.rotate");
    const path = resolvePath("signals.sources.rotate").replace(":sourceId", String(sourceId));
    return this.postEnvelope(path, {});
  }

  async disableSource(sourceId: number): Promise<void> {
    assertImplemented("signals.sources.disable");
    const path = resolvePath("signals.sources.disable").replace(":sourceId", String(sourceId));
    await this.postEnvelope<void>(path, {});
  }

  async listReceipts(sourceId: number): Promise<SignalReceiptListResponse> {
    assertImplemented("signals.sources.receipts");
    const path = resolvePath("signals.sources.receipts").replace(":sourceId", String(sourceId));
    return resolveEnvelope("signals.sources.receipts") ? this.requestEnvelope(path) : this.request(path);
  }
}

export interface SignalsClient {
  listSources(): Promise<SignalSourceListResponse>;
  issueSource(body: SignalSourceCreateRequest): Promise<SignalSourceSecretIssueResponse>;
  rotateSecret(sourceId: number): Promise<SignalSourceSecretIssueResponse>;
  disableSource(sourceId: number): Promise<void>;
  listReceipts(sourceId: number): Promise<SignalReceiptListResponse>;
}

export function createSignalsClient(baseUrl: string, getToken: () => string | null): SignalsClient {
  const client = new SignalsApiClient(baseUrl, getToken);
  return {
    listSources: () => client.listSources(),
    issueSource: (body) => client.issueSource(body),
    rotateSecret: (sourceId) => client.rotateSecret(sourceId),
    disableSource: (sourceId) => client.disableSource(sourceId),
    listReceipts: (sourceId) => client.listReceipts(sourceId),
  };
}
