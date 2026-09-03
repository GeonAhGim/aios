import type { AnyConstructor } from "../http";
import { checkDigest, createIdempotencyDigestStore, type IdempotencyDigestStore } from "../idempotencyDigest";

// spec §9 PLT-15 / §3.7 적용 대상: `POST /v1/foundation/paper-control/*`(5개)와
// `POST /v1/foundation/trust/consents`. 실제 마운트 경로는 문서상 라벨
// "paper-control"과 달리 라우터 prefix가 `/v1/foundation/paper-deployments`다
// (src/api/routers/foundation/paper_control.py 확인, 라우터 파일명이 곧
// 모듈명 "paper_control" — REST 리소스명은 "paper-deployments") — 추측 대신
// 원본 라우터를 읽고 확정했다.
const PAPER_DEPLOYMENTS_PATH = "/v1/foundation/paper-deployments";
const TRUST_CONSENTS_PATH = "/v1/foundation/trust/consents";

// spec §3.7 IdempotencyScope.header_key 패턴 — http.ts의 postIdempotent는 값이
// 없을 때만 자동 생성하고 형식 자체는 검증하지 않으므로, 타입(필수 인자)에
// 더해 런타임에서도 여기서 거부한다.
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

function requireIdempotencyKey(key: string): void {
  if (!IDEMPOTENCY_KEY_PATTERN.test(key)) {
    throw new Error("Idempotency-Key는 16~128자의 [A-Za-z0-9_-] 문자열이어야 합니다.");
  }
}

// task-427 checkDigest 재사용 — 같은 키로 다른 body를 보내는 호출을 서버
// 왕복 전에 막는다. 라우트별로 store를 분리해, 같은 UUID가 서로 다른
// 라우트(예: :start와 :pause)에 우연히 재사용돼도 서버 스코프
// (`{route}:{tenant_id}:{subject_id}:{header}`)와 동일하게 별개로 취급한다.
const paperDeploymentDigests = createIdempotencyDigestStore();
const trustConsentDigests = createIdempotencyDigestStore();

async function guardIdempotentBody(
  store: IdempotencyDigestStore,
  routeLabel: string,
  idempotencyKey: string,
  body: unknown,
): Promise<void> {
  const result = await checkDigest(`${routeLabel}:${idempotencyKey}`, body, store);
  if (result === "mismatch") {
    throw new Error(
      `Idempotency-Key(${idempotencyKey})가 ${routeLabel}에서 이전과 다른 요청 본문으로 재사용되었습니다.`,
    );
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

// PLT-15 잔여 라우트(§3.7 적용 대상 목록) 멱등 클라이언트. ADR-2026-08-29-E:
// paper-control은 PAPER 하드가드 경로라 이 클라이언트는 LIVE 전환 파라미터를
// 노출하지 않는다(endpointClassification은 SANDBOX 기본값을 서버가 강제).
export function withFoundation<TBase extends AnyConstructor>(Base: TBase) {
  return class extends Base {
    // 전환기 규칙(spec §3.7): 멱등 키는 헤더로 보내되, 서버가 아직 읽는 body의
    // 기존 `idempotency_key` 필드도 alias로 함께 싣는다.
    async requestPaperDeployment(
      body: RequestPaperDeploymentBody,
      idempotencyKey: string,
    ): Promise<PaperDeploymentView> {
      requireIdempotencyKey(idempotencyKey);
      const outgoing = { ...body, idempotencyKey };
      await guardIdempotentBody(paperDeploymentDigests, "request", idempotencyKey, outgoing);
      return this.postIdempotent(PAPER_DEPLOYMENTS_PATH, outgoing, idempotencyKey);
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
      await guardIdempotentBody(paperDeploymentDigests, command, idempotencyKey, outgoing);
      return this.postIdempotent(
        `${PAPER_DEPLOYMENTS_PATH}/${deploymentId}:${command}`,
        outgoing,
        idempotencyKey,
      );
    }

    // AcceptDisclosureRequest(body)에는 idempotency 필드가 원래 없었으므로
    // alias할 기존 필드가 없다 — 헤더만 싣는다(전환기 규칙은 기존 body 필드가
    // 있는 paper-control 5개에만 적용).
    async acceptTrustConsent(body: AcceptTrustConsentBody, idempotencyKey: string): Promise<ConsentDecision> {
      requireIdempotencyKey(idempotencyKey);
      await guardIdempotentBody(trustConsentDigests, "accept", idempotencyKey, body);
      return this.postIdempotent(TRUST_CONSENTS_PATH, body, idempotencyKey);
    }
  };
}
