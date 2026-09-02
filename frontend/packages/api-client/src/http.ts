import { keysToCamel, keysToSnake } from "./caseConvert";
import { resolveTraceId, unwrap } from "./envelope";

export class ApiError extends Error {
  statusCode: number;
  traceId?: string;

  constructor(statusCode: number, message: string, traceId?: string) {
    super(message);
    this.statusCode = statusCode;
    this.traceId = traceId;
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
  ): Promise<{ status: number; body: unknown; traceId?: string }> {
    const token = this.getToken();
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });
    const traceId = response.headers.get("X-Trace-Id") ?? undefined;

    if (response.status === 204) {
      return { status: response.status, body: undefined, traceId };
    }

    const text = await response.text();
    const body: unknown = text ? JSON.parse(text) : undefined;
    return { status: response.status, body, traceId };
  }

  protected async request<T>(path: string, init?: RequestInit): Promise<T> {
    const { status, body, traceId } = await this.fetchJson(path, init);
    if (status === 204) return undefined as T;

    if (status < 200 || status >= 300) {
      throw new ApiError(status, extractDetailMessage(body), resolveTraceId(undefined, traceId));
    }

    return keysToCamel<T>(body);
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
    const { status, body, traceId } = await this.fetchJson(path, init);
    if (status === 204) return undefined as T;

    const result = unwrap<unknown>(body);
    if (!result.ok) {
      throw new ApiError(status, result.error.message, resolveTraceId(result.error.trace_id, traceId));
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
