import { resolvePath, type ApiRouteName } from "../apiPaths";
import type { AnyConstructor } from "../http";

// spec §9 PLT-15 / §3.7 적용 대상: `POST /v1/foundation/paper-control/*`(5개)와
// `POST /v1/foundation/trust/consents`. 실제 마운트 경로는 문서상 라벨
// "paper-control"과 달리 라우터 prefix가 `/v1/foundation/paper-deployments`다
// (src/api/routers/foundation/paper_control.py 확인, 라우터 파일명이 곧
// 모듈명 "paper_control" — REST 리소스명은 "paper-deployments") — 추측 대신
// 원본 라우터를 읽고 확정했다.
// 경로 문자열은 apiPaths.ts(task-605) 레지스트리에만 있다(marketplace.ts와 동일 관용).
// PLT-16(versioning.py) 미구현이라 이 라우트들의 v1Path는 비어 있고
// resolvePath는 항상 legacyPath로 폴백한다 — task-942 decision: v1Path를
// 추측해 채우지 않는다.

// spec §3.7 IdempotencyScope.header_key 패턴 — http.ts의 postIdempotent는 값이
// 없을 때만 자동 생성하고 형식 자체는 검증하지 않으므로, 타입(필수 인자)에
// 더해 런타임에서도 여기서 거부한다.
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

function requireIdempotencyKey(key: string): void {
  if (!IDEMPOTENCY_KEY_PATTERN.test(key)) {
    throw new Error("Idempotency-Key는 16~128자의 [A-Za-z0-9_-] 문자열이어야 합니다.");
  }
}

export type PaperDeploymentState =
  | "REQUESTED"
  | "READY"
  | "RUNNING"
  | "PAUSED"
  | "STOPPED"
  | "FAILED"
  | "DEGRADED"
  | "RECOVERY_REVIEW";

export interface PaperDeploymentView {
  id: string;
  packageRef: string;
  connectionId: string | null;
  state: PaperDeploymentState;
  fenceToken: number;
  createdAt: string | null;
  updatedAt: string | null;
  schemaVersion: string;
}

export interface RequestPaperDeploymentBody {
  packageRef: string;
  connectionId?: string;
  adapterType: string;
  providerSandboxAccountRef: string;
  endpointClassification?: string;
}

export interface PaperDeploymentListResponse {
  deployments: PaperDeploymentView[];
  asOf: string;
}

export type ConsentState = "NONE" | "ACTIVE" | "REVOKED";

export interface ConsentDecision {
  consentId: string;
  tenantId: string;
  purpose: string;
  disclosureId: string;
  disclosureRevision: number;
  state: ConsentState;
  acceptedAt: string | null;
  revokedAt: string | null;
  expiresAt: string | null;
  schemaVersion: string;
}

export interface AcceptTrustConsentBody {
  purpose: string;
  disclosureRevision: number;
}

type PaperDeploymentCommand = "start" | "resume" | "pause" | "stop";

const PAPER_DEPLOYMENT_COMMAND_ROUTES: Record<PaperDeploymentCommand, ApiRouteName> = {
  start: "foundation.paperDeployments.start",
  resume: "foundation.paperDeployments.resume",
  pause: "foundation.paperDeployments.pause",
  stop: "foundation.paperDeployments.stop",
};

// PLT-15 잔여 라우트(§3.7 적용 대상 목록) 멱등 클라이언트. ADR-2026-08-29-E:
// paper-control은 PAPER 하드가드 경로라 이 클라이언트는 LIVE 전환 파라미터를
// 노출하지 않는다(endpointClassification은 SANDBOX 기본값을 서버가 강제).
export function withFoundation<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    // task-1309: paper_control.py 원본 확인 — 5개 명령 라우트와 동일한
    // "/v1/foundation/paper-deployments" 경로를 GET(list)/POST(request)가 공유한다
    // (apiPaths.ts route() 축약 관용, task-1159 admin.ts 선례와 동일).
    async listPaperDeployments(): Promise<PaperDeploymentListResponse> {
      return this.requestByRoute("foundation.paperDeployments.request");
    }

    // 전환기 규칙(spec §3.7): 멱등 키는 헤더로 보내되, 서버가 아직 읽는 body의
    // 기존 `idempotency_key` 필드도 alias로 함께 싣는다. task-1309: 라우터가
    // 처음부터 ApiResponse[PaperDeploymentView]로 응답하므로 postEnvelopeIdempotent를
    // 쓴다(이전 리프가 postIdempotent를 잘못 골라 응답 봉투를 그대로 반환하던
    // 버그 수정 — origin/main paper_control.py 원본 재확인으로 검증).
    async requestPaperDeployment(
      body: RequestPaperDeploymentBody,
      idempotencyKey: string,
    ): Promise<PaperDeploymentView> {
      requireIdempotencyKey(idempotencyKey);
      const outgoing = { ...body, idempotencyKey };
      return this.postEnvelopeIdempotent(resolvePath("foundation.paperDeployments.request"), outgoing, idempotencyKey);
    }

    async startPaperDeployment(deploymentId: string, idempotencyKey: string): Promise<PaperDeploymentView> {
      return this.postPaperDeploymentCommand(deploymentId, "start", idempotencyKey);
    }

    async resumePaperDeployment(deploymentId: string, idempotencyKey: string): Promise<PaperDeploymentView> {
      return this.postPaperDeploymentCommand(deploymentId, "resume", idempotencyKey);
    }

    async pausePaperDeployment(deploymentId: string, idempotencyKey: string): Promise<PaperDeploymentView> {
      return this.postPaperDeploymentCommand(deploymentId, "pause", idempotencyKey);
    }

    async stopPaperDeployment(deploymentId: string, idempotencyKey: string): Promise<PaperDeploymentView> {
      return this.postPaperDeploymentCommand(deploymentId, "stop", idempotencyKey);
    }

    private async postPaperDeploymentCommand(
      deploymentId: string,
      command: PaperDeploymentCommand,
      idempotencyKey: string,
    ): Promise<PaperDeploymentView> {
      requireIdempotencyKey(idempotencyKey);
      // DeploymentCommandRequest(body)는 idempotency_key 단일 필드뿐이다 —
      // 헤더 값과 alias 관계이므로 body 자체가 곧 alias다.
      const outgoing = { idempotencyKey };
      const path = resolvePath(PAPER_DEPLOYMENT_COMMAND_ROUTES[command]).replace(":deploymentId", deploymentId);
      return this.postEnvelopeIdempotent(path, outgoing, idempotencyKey);
    }

    // AcceptDisclosureRequest(body)에는 idempotency 필드가 원래 없었으므로
    // alias할 기존 필드가 없다 — 헤더만 싣는다(전환기 규칙은 기존 body 필드가
    // 있는 paper-control 5개에만 적용). trust.py도 처음부터 ApiResponse[ConsentDecision]
    // 이므로 postEnvelopeIdempotent를 쓴다(task-1309).
    async acceptTrustConsent(body: AcceptTrustConsentBody, idempotencyKey: string): Promise<ConsentDecision> {
      requireIdempotencyKey(idempotencyKey);
      return this.postEnvelopeIdempotent(resolvePath("foundation.trustConsents.accept"), body, idempotencyKey);
    }
  };
}
