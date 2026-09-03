import { classifyForbidden, classifyServerError, isSessionExpiredErrorCode } from "@aios/shared-types";
import { resolveEnvelope, resolvePath, type ApiRouteName, type ResolvePathOptions } from "./apiPaths";
import { keysToCamel, keysToSnake } from "./caseConvert";
import type { ApiErrorBody } from "./envelope";
import { resolveRetryAfterSec, resolveTraceId, unwrap } from "./envelope";
import { requestMfaStepUp } from "./mfaStepUp";
import { requestIdHeaders } from "./requestId";
import { refreshAccessToken } from "./tokenRefresh";

export class ApiError extends Error {
  statusCode: number;
  traceId?: string;
  errorCode?: string;
  retryAfterSec?: number;

  constructor(
    statusCode: number,
    message: string,
    traceId?: string,
    errorCode?: string,
    retryAfterSec?: number,
  ) {
    super(message);
    this.statusCode = statusCode;
    this.traceId = traceId;
    this.errorCode = errorCode;
    this.retryAfterSec = retryAfterSec;
  }
}

// L4 platform spec §9 PLT-14/15: 금전 POST는 `Idempotency-Key` 헤더(16~128자,
// [A-Za-z0-9_-])가 필수다. UUID(36자, 하이픈 포함)는 이 규격을 만족한다.
export function generateIdempotencyKey(): string {
  return crypto.randomUUID();
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// spec §3.3: 503(EXCHANGE_UNAVAILABLE/DEPENDENCY_NOT_READY)은 표에 "백오프"만
// 명시되고 retry_after_seconds가 없는 경우가 많아 클라이언트가 직접 스케줄을
// 정한다 — 상한 2회(1초, 2초).
const SERVER_ERROR_BACKOFF_SEC = [1, 2] as const;

// withGetRetry가 다음 재시도까지 기다릴 초. undefined면 그대로 throw한다.
// 429는 attempt 0에서만 서버 값으로 1회, 503(classifyServerError가
// "retryable")은 위 스케줄대로 상한 2회, 502/500 등 그 외는 즉시 안내한다.
function nextRetryDelaySec(err: unknown, attempt: number): number | undefined {
  if (!(err instanceof ApiError)) return undefined;
  if (err.statusCode === 429) {
    return attempt === 0 && typeof err.retryAfterSec === "number" ? err.retryAfterSec : undefined;
  }
  if (classifyServerError(err).kind === "retryable" && attempt < SERVER_ERROR_BACKOFF_SEC.length) {
    return SERVER_ERROR_BACKOFF_SEC[attempt];
  }
  return undefined;
}

function extractDetailMessage(body: unknown): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : JSON.stringify(item),
        )
        .join(", ");
    }
  }
  return "요청을 처리할 수 없습니다.";
}

function isEnvelopeError(body: unknown): body is ApiErrorBody {
  return typeof body === "object" && body !== null && "error_code" in body;
}

export type UnauthorizedHandler = (errorCode: string) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;
let unauthorizedNotified = false;

// task-354: 401 AUTH_* 전역 처리용 훅. api-client는 라우터/스토어를 직접
// import하지 않고(순환 의존 방지 + 계층 분리) 상위 계층(앱 부트스트랩)이
// 이 함수로 콜백을 주입한다. 새 핸들러 등록(예: 재로그인 후 재구독)은
// 알림 가드도 함께 초기화한다.
export function configureUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
  unauthorizedNotified = false;
}

// 로그인 성공 등으로 새 세션이 시작되면 다음 401을 다시 알릴 수 있도록
// 가드를 푼다. useAuthStore.setToken이 호출한다.
export function resetUnauthorizedGuard(): void {
  unauthorizedNotified = false;
}

export type TenantHeadersProvider = () => Record<string, string>;

let tenantHeadersProvider: TenantHeadersProvider | null = null;

// spec §3.5: 활성 테넌트가 있을 때만 X-Tenant-Id를 싣는다. configureUnauthorizedHandler와
// 같은 이유로(순환 의존 방지 + 계층 분리) api-client는 tenantContext.ts의
// createTenantStore 인스턴스를 직접 소유하지 않는다 — 앱 부트스트랩이
// useTenant.ts 등에서 만든 스토어의 tenantHeaders를 이 함수로 주입한다.
export function configureTenantHeadersProvider(provider: TenantHeadersProvider | null): void {
  tenantHeadersProvider = provider;
}

// 화면 진입 시 병렬로 나가는 여러 요청이 동시에 401을 받아도(예: 대시보드의
// useMe+usePortfolio+useExecutions) 콜백은 세션당 1회만 호출한다 —
// 중복 로그아웃·중복 리다이렉트를 막기 위함.
function notifyUnauthorized(errorCode: string): void {
  if (unauthorizedNotified) return;
  unauthorizedNotified = true;
  unauthorizedHandler?.(errorCode);
}

// 429(RATE_LIMIT_EXCEEDED)는 미들웨어가 봉투 미적용 라우트에도 §2.3 봉투로
// 응답하므로(spec §9 PLT-25), 봉투/레거시 두 형태를 여기서 함께 처리한다.
// 순수 함수 — 401 처리(재로그인 유도 vs refresh 후 재시도)는 호출부인
// handleAuthFailure가 비동기로 담당한다(task-386).
function buildApiError(
  status: number,
  body: unknown,
  traceId: string | undefined,
  retryAfterHeader: string | undefined,
): ApiError {
  const envelopeError = isEnvelopeError(body) ? body : undefined;
  const message = envelopeError ? envelopeError.message : extractDetailMessage(body);
  const retryAfterSec = resolveRetryAfterSec(envelopeError?.retry_after_seconds, retryAfterHeader);
  return new ApiError(
    status,
    message,
    resolveTraceId(envelopeError?.trace_id, traceId),
    envelopeError?.error_code,
    retryAfterSec,
  );
}

// task-112(28cf21b)로 ApiResponse 봉투가 적용된 라우터(auth/users/admin)만
// requestEnvelope 계열을 쓴다. 나머지 라우터는 아직 body를 그대로 반환하므로
// request/post/put/patch/del 계열(봉투 미적용)을 그대로 쓴다.
export class ApiClientBase {
  protected baseUrl: string;
  protected getToken: () => string | null;

  constructor(baseUrl: string, getToken: () => string | null) {
    this.baseUrl = baseUrl;
    this.getToken = getToken;
  }

  private async fetchJson(
    path: string,
    init?: RequestInit,
  ): Promise<{ status: number; body: unknown; traceId?: string; retryAfterHeader?: string }> {
    const token = this.getToken();
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    // spec §3.1/§3.5: 요청마다 X-Request-Id를 싣고(호출자가 미리 실어 둔
    // 유효값은 그대로 재사용), 활성 테넌트가 있으면 X-Tenant-Id도 싣는다.
    // 서버가 아직 두 헤더를 다 처리하지 않아도 무해하므로 배선은 항상 켠다.
    headers.set("X-Request-Id", requestIdHeaders(headers.get("X-Request-Id") ?? undefined)["X-Request-Id"]);
    for (const [key, value] of Object.entries(tenantHeadersProvider?.() ?? {})) {
      headers.set(key, value);
    }

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    const traceId = response.headers.get("X-Trace-Id") ?? undefined;
    const retryAfterHeader = response.headers.get("Retry-After") ?? undefined;

    if (response.status === 204) {
      return { status: response.status, body: undefined, traceId, retryAfterHeader };
    }

    const text = await response.text();
    const body: unknown = text ? JSON.parse(text) : undefined;
    return { status: response.status, body, traceId, retryAfterHeader };
  }

  // task-481 decision: 401 refresh·403 step-up 재시도는 서버 관점에서 같은
  // 논리 요청이므로 X-Request-Id를 원 요청과 동일하게 유지해야 한다.
  // fetchJson은 매 호출마다 init.headers를 복사해 새 Headers를 만들 뿐
  // init 자체를 변형하지 않으므로, 재시도 시 같은 init 객체를 그대로
  // 넘겨도 헤더에 값이 없으면 매번 새 ID가 생성된다 — 이를 막기 위해
  // performRequest(Envelope) 진입 시 1회만 ID를 확정해 init에 고정한다.
  private withStableRequestId(init?: RequestInit): RequestInit {
    const headers = new Headers(init?.headers);
    headers.set("X-Request-Id", requestIdHeaders(headers.get("X-Request-Id") ?? undefined)["X-Request-Id"]);
    return { ...init, headers };
  }

  // spec §9 PLT-25/§3.3: GET 계열만 자동 재시도한다 — POST 등은 멱등키 규약과
  // 충돌하므로 절대 재시도하지 않는다. 간격은 nextRetryDelaySec이 결정한다.
  private async withGetRetry<T>(method: string, exec: () => Promise<T>): Promise<T> {
    if (method !== "GET") return exec();

    for (let attempt = 0; ; attempt++) {
      try {
        return await exec();
      } catch (err) {
        const delaySec = nextRetryDelaySec(err, attempt);
        if (delaySec === undefined) throw err;
        await sleep(delaySec * 1000);
      }
    }
  }

  protected async request<T>(path: string, init?: RequestInit): Promise<T> {
    const method = (init?.method ?? "GET").toUpperCase();
    return this.withGetRetry(method, () => this.performRequest<T>(path, init));
  }

  // task-386/task-481: AUTH_TOKEN_EXPIRED(401)는 refresh 후, AUTH_MFA_REQUIRED
  // (403)는 step-up 재인증 후 원요청을 각각 최대 1회만 재시도한다. 재시도(두
  // 번째 executeRequest 호출)는 handleRequestFailure를 다시 거치지 않으므로
  // refresh↔step-up이 서로를 무한히 중첩 호출할 수 없다 — 실패하면 그 자리에서
  // 그대로 던진다.
  private async performRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const stableInit = this.withStableRequestId(init);
    try {
      return await this.executeRequest<T>(path, stableInit);
    } catch (err) {
      return this.handleRequestFailure(err, () => this.executeRequest<T>(path, stableInit));
    }
  }

  private async executeRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const { status, body, traceId, retryAfterHeader } = await this.fetchJson(path, init);
    if (status === 204) return undefined as T;

    if (status < 200 || status >= 300) {
      throw buildApiError(status, body, traceId, retryAfterHeader);
    }

    return keysToCamel<T>(body);
  }

  // 401/403 실패 분기점. 403 AUTH_MFA_REQUIRED만 step-up 대상이고(task-393
  // classifyForbidden 재사용 — 새 403 분류기를 만들지 않는다), 그 외는 기존
  // handleAuthFailure(401 전용) 그대로다.
  private async handleRequestFailure<T>(err: unknown, retry: () => Promise<T>): Promise<T> {
    if (err instanceof ApiError && classifyForbidden(err) === "mfa_required") {
      return this.handleMfaStepUp(err, retry);
    }
    return this.handleAuthFailure(err, retry);
  }

  // AUTH_TOKEN_EXPIRED만 refresh 대상(spec §3.3: "재시도: expired만
  // refresh") — AUTH_TOKEN_INVALID/AUTH_SESSION_REVOKED는 refresh를 시도하지
  // 않고 바로 로그아웃 알림으로 넘어간다. refresh 자체가 실패해도(refresh
  // token도 만료·재사용 감지 등) 동일하게 로그아웃 알림 후 원본 에러를 던진다.
  private async handleAuthFailure<T>(err: unknown, retry: () => Promise<T>): Promise<T> {
    if (!(err instanceof ApiError) || err.statusCode !== 401) throw err;

    if (err.errorCode === "AUTH_TOKEN_EXPIRED") {
      const refreshed = await refreshAccessToken();
      if (refreshed) return retry();
    }

    if (isSessionExpiredErrorCode(err.errorCode)) {
      notifyUnauthorized(err.errorCode!);
    }
    throw err;
  }

  // task-481: mfaStepUp.ts의 single-flight 훅으로 TOTP 재인증을 기다린다.
  // 성공하면 원요청을 1회 재시도하고, 사용자가 취소했거나 재인증 자체가
  // AUTH_MFA_INVALID로 실패했으면(handler가 false 반환) 재시도 없이 원래의
  // 403 ApiError를 그대로 던진다.
  private async handleMfaStepUp<T>(err: ApiError, retry: () => Promise<T>): Promise<T> {
    const verified = await requestMfaStepUp();
    if (verified) return retry();
    throw err;
  }

  protected post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  protected put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  protected patch<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  protected del<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "DELETE" });
  }

  protected withQuery(path: string, params: Record<string, string | number | undefined>): string {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) search.set(key, String(value));
    }
    const qs = search.toString();
    return qs ? `${path}?${qs}` : path;
  }

  protected async requestEnvelope<T>(path: string, init?: RequestInit): Promise<T> {
    const method = (init?.method ?? "GET").toUpperCase();
    return this.withGetRetry(method, () => this.performRequestEnvelope<T>(path, init));
  }

  private async performRequestEnvelope<T>(path: string, init?: RequestInit): Promise<T> {
    const stableInit = this.withStableRequestId(init);
    try {
      return await this.executeRequestEnvelope<T>(path, stableInit);
    } catch (err) {
      return this.handleRequestFailure(err, () => this.executeRequestEnvelope<T>(path, stableInit));
    }
  }

  private async executeRequestEnvelope<T>(path: string, init?: RequestInit): Promise<T> {
    const { status, body, traceId, retryAfterHeader } = await this.fetchJson(path, init);
    if (status === 204) return undefined as T;

    const result = unwrap<unknown>(body);
    if (!result.ok) {
      throw buildApiError(status, body, traceId, retryAfterHeader);
    }
    return keysToCamel<T>(result.data);
  }

  protected postEnvelope<T>(path: string, body?: unknown): Promise<T> {
    return this.requestEnvelope<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  protected putEnvelope<T>(path: string, body?: unknown): Promise<T> {
    return this.requestEnvelope<T>(path, {
      method: "PUT",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  protected patchEnvelope<T>(path: string, body?: unknown): Promise<T> {
    return this.requestEnvelope<T>(path, {
      method: "PATCH",
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  // 금전 라우트(spec §9 PLT-15)용 POST. idempotencyKey를 넘기지 않으면 자동
  // 생성한다 — 재시도 시 같은 키를 재사용하려는 호출자만 명시적으로 넘기면 된다.
  protected postIdempotent<T>(path: string, body: unknown | undefined, idempotencyKey?: string): Promise<T> {
    return this.request<T>(path, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey ?? generateIdempotencyKey() },
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  protected postEnvelopeIdempotent<T>(path: string, body: unknown | undefined, idempotencyKey?: string): Promise<T> {
    return this.requestEnvelope<T>(path, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey ?? generateIdempotencyKey() },
      body: body !== undefined ? JSON.stringify(keysToSnake(body)) : undefined,
    });
  }

  // task-605 준비: apiPaths.ts 레지스트리로 경로/봉투 여부를 함께 해석해 request와
  // requestEnvelope 중 하나로 분기만 한다 — 파서를 새로 만들지 않고 둘 다 그대로
  // 재사용한다. clients/*.ts는 아직 이 메서드를 쓰지 않는다(레지스트리·스위치만
  // 준비하는 단계 — 실제 배선은 PLT-17~21 순서를 따르는 후속 리프의 몫).
  protected requestByRoute<T>(route: ApiRouteName, init?: RequestInit, options?: ResolvePathOptions): Promise<T> {
    const path = resolvePath(route, options);
    return resolveEnvelope(route, options) ? this.requestEnvelope<T>(path, init) : this.request<T>(path, init);
  }
}

export type AnyConstructor<T = ApiClientBase> = new (...args: any[]) => T;
