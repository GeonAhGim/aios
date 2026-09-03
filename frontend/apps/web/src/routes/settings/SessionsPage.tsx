import { ApiError, createLogoutClient, createSessionsClient, type SessionsClient } from "@aios/api-client";
import {
  canRevoke,
  classifyForbidden,
  isCurrentSession,
  parseSessionView,
  routeApiError,
  type ParsedSessionView,
} from "@aios/shared-types";
import { useAuthStore } from "@aios/shared-hooks";
import { Alert, Badge, Button, Card, EmptyState, LoadingState, PageHeader } from "@aios/ui-web";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

// spec §3.4 세션·토큰. task-606(sessions.ts: parseSessionView/createSessionsClient)과
// task-454(logout.ts: logout/logoutAll)를 처음으로 실제 라우트에 배선한다 — 이 파일은
// 새 파서·새 클라이언트를 만들지 않고 두 leaf의 산출물만 조합한다.
//
// 서버에 GET /auth/sessions·DELETE /auth/sessions/{id}가 아직 없다(spec §3.4 표 96행에
// login/refresh/logout/logout-all만 명시) — task-709 선례대로 실 데이터 연결 전까지
// fetchSessions 기본 구현은 표준 에러 경로(routeApiError+ErrorMessage)만 태우고,
// 화면·파싱 로직 자체는 props 주입으로 완성해둔다.
//
// sessionsClient.list()는 파싱에 실패한 항목을 조용히 걸러내도록 설계돼 있다(그 leaf의
// decision) — 이 화면은 그 결정과 별개로 "파싱 실패를 숨기지 않는다"는 이 leaf의 DoD를
// 지켜야 하므로, list()를 거치지 않고 raw 배열을 직접 parseSessionView(재사용)로 파싱해
// 실패 항목도 화면에 남긴다. revoke/revokeAll은 sessionsClient를 그대로 재사용한다.
export interface SessionsPageProps {
  fetchSessions?: () => Promise<unknown[]>;
  sessionsClient?: Pick<SessionsClient, "revoke" | "revokeAll">;
  /** 로컬에 보관 중인 access token이 속한 세션의 id. 앱 로그인 흐름(useAuth.ts)이 아직
   * session_id를 저장하지 않아 실제 기본값은 null(미확보)이다 — 자동 판별은 후속 리프
   * 소관이며 이 프롭으로 주입하면 "이 기기" 표기를 테스트·재사용할 수 있다. */
  getCurrentSessionId?: () => string | null;
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const fetchSessionsDefault = (): Promise<unknown[]> =>
  Promise.reject(new Error("세션 목록 조회 API가 아직 제공되지 않습니다."));

const defaultGetCurrentSessionId = (): string | null => null;

interface ParsedRow {
  index: number;
  view: ParsedSessionView | null;
}

function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  const canRetry = routed.kind === "refetch_retry" || routed.kind === "backoff_retry";
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
      onRetry={canRetry ? onRetry : undefined}
    />
  );
}

function SessionRow({
  view,
  isCurrent,
  disabled,
  onRevoke,
}: {
  view: ParsedSessionView;
  isCurrent: boolean;
  disabled: boolean;
  onRevoke: () => void;
}) {
  const revoked = view.revokedAt !== null;
  return (
    <li className="flex items-center justify-between gap-4 py-3">
      <div>
        <div className="flex items-center gap-2">
          <p className="font-medium text-fg">{view.userAgent ?? "알 수 없는 기기"}</p>
          {isCurrent && <Badge tone="accent">이 기기</Badge>}
          {revoked && <Badge tone="neutral">폐기됨</Badge>}
        </div>
        <p className="text-sm text-fg-muted">
          IP {view.ip ?? "알 수 없음"} · 생성 {new Date(view.createdAt).toLocaleString()} · 최근 활동{" "}
          {new Date(view.lastSeenAt).toLocaleString()}
        </p>
      </div>
      {canRevoke(view) && (
        <Button type="button" variant="danger" size="sm" disabled={disabled} onClick={onRevoke}>
          폐기
        </Button>
      )}
    </li>
  );
}

export function SessionsPage({
  fetchSessions = fetchSessionsDefault,
  sessionsClient,
  getCurrentSessionId = defaultGetCurrentSessionId,
}: SessionsPageProps) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [actionError, setActionError] = useState<unknown>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [pendingAll, setPendingAll] = useState(false);

  const client = useMemo<Pick<SessionsClient, "revoke" | "revokeAll">>(() => {
    if (sessionsClient) return sessionsClient;
    const getToken = () => useAuthStore.getState().token;
    const store = { clear: () => useAuthStore.getState().logout() };
    const logoutClient = createLogoutClient({ baseUrl, getToken, store });
    return createSessionsClient({ baseUrl, getToken, getCurrentSessionId, store, logoutClient });
  }, [sessionsClient, getCurrentSessionId]);

  const query = useQuery({
    queryKey: ["sessions"],
    queryFn: fetchSessions,
  });

  const rows: ParsedRow[] = (query.data ?? []).map((raw, index) => ({
    index,
    view: parseSessionView(raw),
  }));

  async function handleRevoke(view: ParsedSessionView) {
    const isCurrent = isCurrentSession(view, getCurrentSessionId());
    const confirmed = window.confirm(
      isCurrent ? "현재 사용 중인 세션입니다. 폐기하면 로그아웃됩니다. 계속할까요?" : "이 세션을 폐기할까요?",
    );
    if (!confirmed) return;

    setActionError(null);
    setPendingId(view.sessionId);
    try {
      await client.revoke(view.sessionId);
      if (isCurrent) {
        navigate("/login", { replace: true });
        return;
      }
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
    } catch (err) {
      setActionError(err);
    } finally {
      setPendingId(null);
    }
  }

  // logout.ts의 logoutAll은 서버 응답과 무관하게 항상 로컬 정리로 끝나는
  // best-effort 계약이다(task-454 decision) — 여기서 그 결과를 다시 성공/실패로
  // 분기하면 오히려 그 계약을 어기게 되므로, 무조건 정리 후 리다이렉트한다.
  async function handleRevokeAll() {
    const confirmed = window.confirm("모든 기기에서 로그아웃합니다. 계속할까요?");
    if (!confirmed) return;
    setPendingAll(true);
    await client.revokeAll();
    navigate("/login", { replace: true });
  }

  return (
    <AppShell>
      <div className="max-w-3xl space-y-6">
        <PageHeader
          title="활성 세션"
          action={
            <Button type="button" variant="secondary" size="sm" onClick={handleRevokeAll} loading={pendingAll}>
              전체 로그아웃
            </Button>
          }
        />

        {query.isError && <ErrorBanner error={query.error} onRetry={() => query.refetch()} />}
        {actionError !== null && <ErrorBanner error={actionError} />}

        {!query.isError && query.isLoading && <LoadingState />}
        {!query.isError && !query.isLoading && rows.length === 0 && <EmptyState>활성 세션이 없습니다.</EmptyState>}
        {!query.isError && !query.isLoading && rows.length > 0 && (
          <Card>
            <ul className="divide-y divide-border">
              {rows.map(({ index, view }) =>
                view ? (
                  <SessionRow
                    key={view.sessionId}
                    view={view}
                    isCurrent={isCurrentSession(view, getCurrentSessionId())}
                    disabled={pendingId === view.sessionId}
                    onRevoke={() => handleRevoke(view)}
                  />
                ) : (
                  <li key={`invalid-${index}`} className="py-3">
                    <Alert tone="danger">세션 정보를 해석할 수 없습니다.</Alert>
                  </li>
                ),
              )}
            </ul>
          </Card>
        )}
      </div>
    </AppShell>
  );
}
