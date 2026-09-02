import { keysToCamel, keysToSnake } from "./caseConvert";
import { unwrap } from "./envelope";

export class ApiError extends Error {
  statusCode: number;

  constructor(statusCode: number, message: string) {
    super(message);
    this.statusCode = statusCode;
  }
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

  private async fetchJson(path: string, init?: RequestInit): Promise<{ status: number; body: unknown }> {
    const token = this.getToken();
    const headers = new Headers(init?.headers);
    headers.set("Content-Type", "application/json");
    if (token) headers.set("Authorization", `Bearer ${token}`);

    const response = await fetch(`${this.baseUrl}${path}`, { ...init, headers });

    if (response.status === 204) {
      return { status: response.status, body: undefined };
    }

    const text = await response.text();
    const body: unknown = text ? JSON.parse(text) : undefined;
    return { status: response.status, body };
  }

  protected async request<T>(path: string, init?: RequestInit): Promise<T> {
    const { status, body } = await this.fetchJson(path, init);
    if (status === 204) return undefined as T;

    if (status < 200 || status >= 300) {
      throw new ApiError(status, extractDetailMessage(body));
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
    const { status, body } = await this.fetchJson(path, init);
    if (status === 204) return undefined as T;

    const result = unwrap<unknown>(body);
    if (!result.ok) {
      throw new ApiError(status, result.error.message);
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
}

export type AnyConstructor<T = ApiClientBase> = new (...args: any[]) => T;
