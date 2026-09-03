import { afterEach, describe, expect, it, vi } from "vitest";
import { createSessionsClient } from "./sessions";
import { ApiError, configureUnauthorizedHandler } from "./http";

function jsonResponse(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
  });
}

function envelopeSuccess(data: unknown) {
  return { data, meta: { trace_id: "trace-1", as_of: "2026-09-03T00:00:00Z", page: null } };
}

function envelopeError(errorCode: string) {
  return {
    error_code: errorCode,
    message: "요청을 처리할 수 없습니다.",
    details: {},
    trace_id: "trace-err",
    retry_after_seconds: null,
  };
}

function rawSession(overrides: Record<string, unknown> = {}) {
  return {
    session_id: "session-1",
    created_at: "2026-09-01T00:00:00Z",
    last_seen_at: "2026-09-02T00:00:00Z",
    user_agent: "Mozilla/5.0",
    ip: "203.0.113.10",
    revoked_at: null,
    ...overrides,
  };
}

function makeClient(overrides: Partial<Parameters<typeof createSessionsClient>[0]> = {}) {
  const store = { clear: vi.fn() };
  const logoutAll = vi.fn().mockResolvedValue(undefined);
  const client = createSessionsClient({
    baseUrl: "https://api.example.test",
    getToken: () => "tok",
    getCurrentSessionId: () => null,
    store,
    logoutClient: { logoutAll },
    ...overrides,
  });
  return { client, store, logoutAll };
}

describe("createSessionsClient", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    configureUnauthorizedHandler(null);
  });

  describe("list", () => {
    it("GET /auth/sessions 응답을 파싱해 카멜케이스 세션 목록으로 반환한다", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(200, envelopeSuccess([rawSession(), rawSession({ session_id: "session-2" })])));
      vi.stubGlobal("fetch", fetchMock);
      const { client } = makeClient();

      const sessions = await client.list();

      expect(sessions).toEqual([
        {
          sessionId: "session-1",
          createdAt: "2026-09-01T00:00:00Z",
          lastSeenAt: "2026-09-02T00:00:00Z",
          userAgent: "Mozilla/5.0",
          ip: "203.0.113.10",
          revokedAt: null,
        },
        expect.objectContaining({ sessionId: "session-2" }),
      ]);
      const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("https://api.example.test/auth/sessions");
    });

    it("필수 필드가 누락된 항목은 예외 없이 걸러낸다", async () => {
      const broken = rawSession();
      delete (broken as Record<string, unknown>).session_id;
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, envelopeSuccess([rawSession(), broken])));
      vi.stubGlobal("fetch", fetchMock);
      const { client } = makeClient();

      const sessions = await client.list();

      expect(sessions).toHaveLength(1);
      expect(sessions[0].sessionId).toBe("session-1");
    });
  });

  describe("revoke", () => {
    it("DELETE /auth/sessions/{id}가 성공하면 폐기가 끝난다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(204));
      vi.stubGlobal("fetch", fetchMock);
      const { client } = makeClient();

      await expect(client.revoke("session-1")).resolves.toBeUndefined();

      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("https://api.example.test/auth/sessions/session-1");
      expect(init.method).toBe("DELETE");
    });

    // negative test: 404 RESOURCE_NOT_FOUND(이미 폐기됨)는 예외 없이 성공으로 흡수한다.
    it("negative: 404 RESOURCE_NOT_FOUND(이미 폐기됨)는 예외 없이 성공으로 흡수한다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(404, envelopeError("RESOURCE_NOT_FOUND")));
      vi.stubGlobal("fetch", fetchMock);
      const { client } = makeClient();

      await expect(client.revoke("session-1")).resolves.toBeUndefined();
    });

    // negative test: 401 AUTH_SESSION_REVOKED는 여기서 흡수하지 않고 http.ts의
    // 전역 401 핸들러(task-354)에 그대로 위임한다.
    it("negative: 401 AUTH_SESSION_REVOKED는 흡수하지 않고 전역 401 핸들러에 위임한다", async () => {
      const handler = vi.fn();
      configureUnauthorizedHandler(handler);
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, envelopeError("AUTH_SESSION_REVOKED")));
      vi.stubGlobal("fetch", fetchMock);
      const { client } = makeClient();

      let caught: unknown;
      try {
        await client.revoke("session-1");
      } catch (e) {
        caught = e;
      }

      expect(caught).toBeInstanceOf(ApiError);
      expect((caught as ApiError).errorCode).toBe("AUTH_SESSION_REVOKED");
      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler).toHaveBeenCalledWith("AUTH_SESSION_REVOKED");
    });

    it("현재 세션을 폐기하면 로컬 토큰도 함께 정리한다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(204));
      vi.stubGlobal("fetch", fetchMock);
      const { client, store } = makeClient({ getCurrentSessionId: () => "session-1" });

      await client.revoke("session-1");

      expect(store.clear).toHaveBeenCalledTimes(1);
    });

    it("현재 세션이 아니면 로컬 토큰을 정리하지 않는다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(204));
      vi.stubGlobal("fetch", fetchMock);
      const { client, store } = makeClient({ getCurrentSessionId: () => "session-other" });

      await client.revoke("session-1");

      expect(store.clear).not.toHaveBeenCalled();
    });

    it("현재 세션이 404로 흡수되어도 로컬 토큰은 정리한다", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(404, envelopeError("RESOURCE_NOT_FOUND")));
      vi.stubGlobal("fetch", fetchMock);
      const { client, store } = makeClient({ getCurrentSessionId: () => "session-1" });

      await client.revoke("session-1");

      expect(store.clear).toHaveBeenCalledTimes(1);
    });
  });

  describe("revokeAll", () => {
    it("task-454 logoutClient.logoutAll을 그대로 위임한다(중복 구현 금지)", async () => {
      const { client, logoutAll } = makeClient();

      await client.revokeAll();

      expect(logoutAll).toHaveBeenCalledTimes(1);
    });
  });
});
