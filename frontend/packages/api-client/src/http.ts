import { isSessionExpiredErrorCode } from "@aios/shared-types";
import { keysToCamel, keysToSnake } from "./caseConvert";
import type { ApiErrorBody } from "./envelope";
import { resolveRetryAfterSec, resolveTraceId, unwrap } from "./envelope";
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

  // spec §9 PLT-25: GET 계열만 429 응답의 retryAfterSec 경과 후 1회 자동
  // 재시도한다. POST/PUT/PATCH/DELETE는 멱등키 규약과 충돌하므로(재시도가
  // 곧 새 요청 의미가 될 수 있음) 여기서 절대 재시도하지 않는다 — 호출부는
  // method가 명시된 경우에만 이 분기를 벗어난다.
  private async withGetRetry<T>(method: string, exec: () => Promise<T>): Promise<T> {
    try {
      return await exec();
    } catch (err) {
      if (
        method === "GET" &&
        err instanceof ApiError &&
        err.statusCode === 429 &&
        typeof err.retryAfterSec === "number"
      ) {
        await new Promise((resolve) => setTimeout(resolve, err.retryAfterSec! * 1000));
        return await exec();
      }
      throw err;
    }
  }

  protected async request<T>(path: string, init?: RequestInit): Promise<T> {
    const method = (init?.method ?? "GET").toUpperCase();
    return this.withGetRetry(method, () => this.performRequest<T>(path, init));
  }

  // task-386: AUTH_TOKEN_EXPIRED 1회만 refresh 후 원요청 재시도. 재시도(두
  // 번째 executeRequest 호출)는 handleAuthFailure를 다시 거치지 않으므로
  // refresh가 무한 반복될 수 없다 — 실패하면 그 자리에서 그대로 던진다.
  private async performRequest<T>(path: string, init?: RequestInit): Promise<T> {
    try {
      return await this.executeRequest<T>(path, init);
    } catch (err) {
      return this.handleAuthFailure(err, () => this.executeRequest<T>(path, init));
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
    try {
      return await this.executeRequestEnvelope<T>(path, init);
    } catch (err) {
      return this.handleAuthFailure(err, () => this.executeRequestEnvelope<T>(path, init));
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
}

export type AnyConstructor<T = ApiClientBase> = new (...args: any[]) => T;
