// L4_platform_observability_tenancy_api_v1.0.md §3.5 테넌트 계약 + §9 PLT-29.
// task-617 createMembershipsClient·task-474 parseMembershipView/deriveCapabilities·
// task-455 useTenant만 재사용한다 — 새 클라이언트·새 파서는 만들지 않는다.
//
// GET .../memberships(목록)는 PLT-29 라우터 미구현이라 호출할 계약이 없다.
// TenantSwitcher.tsx는 이 상황에서 "호출자가 목록을 주입"했지만 이 화면은 route
// 컴포넌트라 주입할 상위가 없으므로, SessionsPage.tsx(task-606) 선례대로
// fetchMembers 프롭 + 기본값(표시용 실패 Promise)을 쓴다.
//
// 권한 게이팅: 목록에서 내 subject_id(useMe().userId) 행을 찾아
// deriveCapabilities(ACTIVE+OWNER만 canManageMembers=true)로 판정하고, 못 찾으면
// TenantSwitcher의 UNKNOWN_CAPABILITIES와 동일하게 최소권한(false)으로 둔다.
//
// grant/suspend/revoke 실패는 err.message를 노출하지 않는다 — 403은
// classifyForbidden으로 걸러 ForbiddenNotice/DenialReasons가, 나머지(§3.3 표가
// STATE_INVALID_TRANSITION 409로도 last-owner 거부를 보낼 수 있어
// classifyMembershipError가 두 표현 모두 흡수)는 describeMembershipError의 고정
// 문구만 보여준다(membershipMutation.ts, task-483 routeApiError 경유).
import { ApiError, createMembershipsClient, type MembershipsClient } from "@aios/api-client";
import {
  classifyForbidden,
  classifyMembershipError,
  deriveCapabilities,
  describeMembershipError,
  parseMembershipView,
  routeApiError,
  type MembershipRole,
  type MembershipState,
  type MembershipView,
} from "@aios/shared-types";
import { useAuthStore, useMe } from "@aios/shared-hooks";
import {
  Alert,
  Badge,
  Button,
  Card,
  CardTitle,
  EmptyState,
  Field,
  Input,
  LoadingState,
  PageHeader,
  Select,
} from "@aios/ui-web";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useTenant } from "../../hooks/useTenant";

const ROLE_LABELS: Record<MembershipRole, string> = {
  OWNER: "소유자",
  ADMIN: "관리자",
  MEMBER: "멤버",
  AUDITOR: "감사자(읽기전용)",
  SERVICE: "서비스",
};
const ASSIGNABLE_ROLES = Object.keys(ROLE_LABELS) as MembershipRole[];
const STATE_META: Record<MembershipState, { label: string; tone: "success" | "warning" | "neutral" }> = {
  ACTIVE: { label: "활성", tone: "success" },
  SUSPENDED: { label: "정지", tone: "warning" },
  REVOKED: { label: "폐기", tone: "neutral" },
};
const NO_PERMISSION_TITLE = "이 작업을 수행할 권한이 없습니다.";

export interface MembersPageProps {
  /** GET .../memberships가 서버 미구현(PLT-29)이라 호출자가 주입한다. */
  fetchMembers?: () => Promise<unknown[]>;
  membershipsClient?: Pick<MembershipsClient, "grant" | "suspend" | "revoke">;
  /** 현재 사용자 subject_id. 기본은 useMe()의 userId. */
  currentUserId?: string | null;
}
const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const fetchMembersDefault = (): Promise<unknown[]> =>
  Promise.reject(new Error("멤버 목록 조회 API가 아직 제공되지 않습니다."));

interface ParsedRow {
  index: number;
  view: MembershipView | null;
}

function ListErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
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

function MutationErrorBanner({ error }: { error: unknown }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  return <ErrorMessage message={describeMembershipError(classifyMembershipError(error))} />;
}

interface MemberRowProps {
  view: MembershipView;
  canManage: boolean;
  pending: boolean;
  onSuspend: () => void;
  onRevoke: () => void;
}

function MemberRow({ view, canManage, pending, onSuspend, onRevoke }: MemberRowProps) {
  const state = STATE_META[view.state];
  return (
    <li className="flex items-center justify-between gap-4 py-3">
      <div className="flex items-center gap-2">
        <p className="font-medium text-fg">{view.subjectId}</p>
        <Badge tone="accent">{ROLE_LABELS[view.role]}</Badge>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      <div className="flex gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={!canManage || pending || view.state !== "ACTIVE"}
          title={!canManage ? NO_PERMISSION_TITLE : undefined}
          onClick={onSuspend}
        >
          정지
        </Button>
        <Button
          type="button"
          variant="danger"
          size="sm"
          disabled={!canManage || pending || view.state === "REVOKED"}
          title={!canManage ? NO_PERMISSION_TITLE : undefined}
          onClick={onRevoke}
        >
          폐기
        </Button>
      </div>
    </li>
  );
}

export function MembersPage({ fetchMembers = fetchMembersDefault, membershipsClient, currentUserId }: MembersPageProps) {
  const { activeTenantId } = useTenant();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const resolvedCurrentUserId = currentUserId ?? me?.userId ?? null;

  const [actionError, setActionError] = useState<unknown>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [subjectId, setSubjectId] = useState("");
  const [role, setRole] = useState<MembershipRole>("MEMBER");
  const [grantPending, setGrantPending] = useState(false);

  const client = useMemo<Pick<MembershipsClient, "grant" | "suspend" | "revoke">>(() => {
    if (membershipsClient) return membershipsClient;
    return createMembershipsClient(baseUrl, () => useAuthStore.getState().token);
  }, [membershipsClient]);

  const queryKey = ["memberships", activeTenantId];
  const query = useQuery({
    queryKey,
    queryFn: fetchMembers,
    enabled: activeTenantId !== null,
  });

  const rows: ParsedRow[] = (query.data ?? []).map((raw, index) => ({ index, view: parseMembershipView(raw) }));

  const myMembership = useMemo(
    () => rows.map((r) => r.view).find((v): v is MembershipView => v !== null && v.subjectId === resolvedCurrentUserId) ?? null,
    [rows, resolvedCurrentUserId],
  );
  const canManageMembers = myMembership ? deriveCapabilities(myMembership).canManageMembers : false;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey });
  }

  async function handleGrant(e: FormEvent) {
    e.preventDefault();
    setActionError(null);
    setGrantPending(true);
    try {
      await client.grant({ subjectId, role });
      setSubjectId("");
      setRole("MEMBER");
      await refresh();
    } catch (err) {
      setActionError(err);
    } finally {
      setGrantPending(false);
    }
  }

  async function runOnMembership(membershipId: string, action: () => Promise<unknown>) {
    setActionError(null);
    setPendingId(membershipId);
    try {
      await action();
      await refresh();
    } catch (err) {
      setActionError(err);
    } finally {
      setPendingId(null);
    }
  }

  function handleRevoke(view: MembershipView) {
    if (!window.confirm("이 멤버십을 폐기할까요?")) return;
    runOnMembership(view.membershipId, () => client.revoke(view.membershipId));
  }

  if (activeTenantId === null) {
    return (
      <AppShell>
        <div className="max-w-3xl space-y-6">
          <PageHeader title="멤버 관리" />
          <EmptyState>조직/가구 테넌트를 선택하면 멤버를 관리할 수 있습니다.</EmptyState>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="max-w-3xl space-y-6">
        <PageHeader title="멤버 관리" />

        {query.isError && <ListErrorBanner error={query.error} onRetry={() => query.refetch()} />}
        {actionError !== null && <MutationErrorBanner error={actionError} />}

        {!query.isError && query.isLoading && <LoadingState />}
        {!query.isError && !query.isLoading && rows.length === 0 && <EmptyState>등록된 멤버가 없습니다.</EmptyState>}
        {!query.isError && !query.isLoading && rows.length > 0 && (
          <Card>
            <ul className="divide-y divide-border">
              {rows.map(({ index, view }) =>
                view ? (
                  <MemberRow
                    key={view.membershipId}
                    view={view}
                    canManage={canManageMembers}
                    pending={pendingId === view.membershipId}
                    onSuspend={() => runOnMembership(view.membershipId, () => client.suspend(view.membershipId))}
                    onRevoke={() => handleRevoke(view)}
                  />
                ) : (
                  <li key={`invalid-${index}`} className="py-3">
                    <Alert tone="danger">멤버십 정보를 해석할 수 없습니다.</Alert>
                  </li>
                ),
              )}
            </ul>
          </Card>
        )}

        <Card className="max-w-lg">
          <CardTitle>멤버 초대</CardTitle>
          <form onSubmit={handleGrant} className="space-y-3">
            <Field label="사용자 ID(subject_id)">
              <Input
                required
                value={subjectId}
                onChange={(e) => setSubjectId(e.target.value)}
                disabled={!canManageMembers}
              />
            </Field>
            <Field label="역할">
              <Select
                value={role}
                onChange={(e) => setRole(e.target.value as MembershipRole)}
                disabled={!canManageMembers}
              >
                {ASSIGNABLE_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABELS[r]}
                  </option>
                ))}
              </Select>
            </Field>
            <Button
              type="submit"
              loading={grantPending}
              disabled={!canManageMembers}
              title={!canManageMembers ? NO_PERMISSION_TITLE : undefined}
              className="w-full"
            >
              초대
            </Button>
          </form>
        </Card>
      </div>
    </AppShell>
  );
}
