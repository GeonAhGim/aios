import { classifyForbidden, isSessionExpiredErrorCode } from "@aios/shared-types";
import { resolveEnvelope, resolvePath, type ApiRouteName, type ResolvePathOptions } from "./apiPaths";
import { keysToCamel, keysToSnake } from "./caseConvert";
import { buildRequestHeaders, notifyUnauthorized, withStableRequestId } from "./httpHeaders";
import { ApiError, buildApiError, withGetRetry } from "./httpErrors";
import { withIdempotent } from "./httpIdempotent";
import { requestMfaStepUp } from "./mfaStepUp";
import { refreshAccessToken } from "./tokenRefresh";
import { unwrap } from "./envelope";

// task-801: http.ts(401줄, P6 300줄 규율 초과) 분할 — 헤더 조립(+401 알림)은
// httpHeaders.ts, ApiError 빌드·GET 재시도 정책은 httpErrors.ts, 멱등 계열은
// httpIdempotent.ts(withIdempotent mixin)로 옮기고, 이 파일은 코어 fetch·
// 401/403 재인증·봉투 분기만 담당한다. 공개 API는 index.ts 기준으로 이름·
// 시그니처가 그대로다(barrel 재수출로 흡수).
export { configureTenantHeadersProvider } from "./httpHeaders";
export type { TenantHeadersProvider } from "./httpHeaders";
export { configureUnauthorizedHandler, resetUnauthorizedGuard } from "./httpHeaders";
export type { UnauthorizedHandler } from "./httpHeaders";
export { ApiError, buildApiError } from "./httpErrors";
export { generateIdempotencyKey } from "./httpIdempotent";

// task-112(28cf21b)로 ApiResponse 봉투가 적용된 라우터(auth/users/admin)만
// requestEnvelope 계열을 쓴다. 나머지 라우터는 아직 body를 그대로 반환하므로
// request/post/put/patch/del 계열(봉투 미적용)을 그대로 쓴다.
export class ApiClientBaseCore {
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
    const headers = buildRequestHeaders(this.getToken(), init);
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

  protected async request<T>(path: string, init?: RequestInit): Promise<T> {
    const method = (init?.method ?? "GET").toUpperCase();
    return withGetRetry(method, () => this.performRequest<T>(path, init));
  }

  // task-386/task-481: AUTH_TOKEN_EXPIRED(401)는 refresh 후, AUTH_MFA_REQUIRED
  // (403)는 step-up 재인증 후 원요청을 각각 최대 1회만 재시도한다. 재시도(두
  // 번째 executeRequest 호출)는 handleRequestFailure를 다시 거치지 않으므로
  // refresh↔step-up이 서로를 무한히 중첩 호출할 수 없다 — 실패하면 그 자리에서
  // 그대로 던진다.
  private async performRequest<T>(path: string, init?: RequestInit): Promise<T> {
    const stableInit = withStableRequestId(init);
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
      if (refreshed) return this.notifyIfSessionExpired(retry);
    }

    if (isSessionExpiredErrorCode(err.errorCode)) {
      notifyUnauthorized(err.errorCode!);
    }
    throw err;
  }

  // task-1166(sessionLifecycle.test.ts 시나리오 2)에서 발견: refresh 성공 후
  // 재시도한 원요청이 또다시 세션 만료 계열 401을 받으면(재시도는 handleRequestFailure를
  // 다시 거치지 않아, task-386의 무한루프 방지 그대로 유지) 이전에는 notifyUnauthorized
  // 없이 에러만 던지고 끝나 로그아웃·리다이렉트 훅이 배선상 끊긴 채로 통과했다.
  // 재시도 횟수는 늘리지 않고(retry 1회 그대로) 그 실패에도 알림만 보정한다.
  private async notifyIfSessionExpired<T>(retry: () => Promise<T>): Promise<T> {
    try {
      return await retry();
    } catch (retryErr) {
      if (retryErr instanceof ApiError && isSessionExpiredErrorCode(retryErr.errorCode)) {
        notifyUnauthorized(retryErr.errorCode!);
      }
      throw retryErr;
    }
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

  // spec §3.2: /readyz는 성공(200)·저하(503) 모두 몸체가 ReadinessReport다 —
  // request()처럼 비2xx를 실패로 던지면 정작 저하 원인(body)을 잃는다. 이
  // 메서드는 HTTP 상태를 판정하지 않고 몸체를 그대로(camelCase 변환 없이,
  // 서버가 as_of·db_pool 같은 snake_case 키를 쓰므로) 반환한다 — 판정은
  // parseReadiness(readiness.ts)가 한다. 네트워크/JSON 파싱 실패는 그대로
  // 던져 호출부가 "확인 불가"로 처리하게 둔다. 재시도는 하지 않는다(1회성 진단 조회).
  protected async fetchRaw<T>(path: string): Promise<T> {
    const { body } = await this.fetchJson(path, withStableRequestId());
    return body as T;
  }

  protected async requestEnvelope<T>(path: string, init?: RequestInit): Promise<T> {
    const method = (init?.method ?? "GET").toUpperCase();
    return withGetRetry(method, () => this.performRequestEnvelope<T>(path, init));
  }

  private async performRequestEnvelope<T>(path: string, init?: RequestInit): Promise<T> {
    const stableInit = withStableRequestId(init);
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

  // task-605 준비: apiPaths.ts 레지스트리로 경로/봉투 여부를 함께 해석해 request와
  // requestEnvelope 중 하나로 분기만 한다 — 파서를 새로 만들지 않고 둘 다 그대로
  // 재사용한다. clients/*.ts는 아직 이 메서드를 쓰지 않는다(레지스트리·스위치만
  // 준비하는 단계 — 실제 배선은 PLT-17~21 순서를 따르는 후속 리프의 몫).
  protected requestByRoute<T>(route: ApiRouteName, init?: RequestInit, options?: ResolvePathOptions): Promise<T> {
    const path = resolvePath(route, options);
    return resolveEnvelope(route, options) ? this.requestEnvelope<T>(path, init) : this.request<T>(path, init);
  }
}

// httpIdempotent.ts의 mixin으로 postIdempotent/postEnvelopeIdempotent를 얹는다
// (client.ts 분할 선례와 동일한 mixin 합성 — task-132). 공개 표면(메서드 이름·
// 시그니처)은 분할 이전과 동일하다.
export class ApiClientBase extends withIdempotent(ApiClientBaseCore) {}

export type AnyConstructor<T = ApiClientBase> = new (...args: any[]) => T;
