import { useAdminUsers, useChangeUserStatus, useSuspendSeller } from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import { Button, EmptyState, Input, LoadingState, PageHeader, Select, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

const STATUSES = ["ACTIVE", "SUSPENDED"];

// spec §3.3 에러 taxonomy: 상태변경(changeStatus)/판매정지(suspendSeller) 실패는
// err.message를 직접 노출하지 않고 routeApiError로 판정해 403/그 외를 각각
// ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-483/1072 패턴). 지금까지 두
// mutate 호출 모두 콜백 없이 실행돼 실패를 완전히 조용히 삼켰다 — 에러 상태
// 자체가 없었다.
function UserActionError({ error }: { error: unknown }) {
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  const routed = routeApiError(error);
  return (
    <ErrorMessage
      errorCode={error instanceof ApiError ? error.errorCode : undefined}
      message={error instanceof Error ? error.message : undefined}
      traceId={error instanceof ApiError ? error.traceId : undefined}
      retryAfterSec={routed.kind === "backoff_retry" ? routed.afterSec : undefined}
    />
  );
}

export function UserManagementPage() {
  const [emailSearch, setEmailSearch] = useState("");
  const { data: users, isLoading } = useAdminUsers(emailSearch || undefined);
  const changeStatus = useChangeUserStatus();
  const suspendSeller = useSuspendSeller();
  const [suspendReasons, setSuspendReasons] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<{ userId: string; error: unknown } | null>(null);

  function handleChangeStatus(userId: string, status: string) {
    setActionError(null);
    changeStatus.mutate(
      { userId, status },
      { onError: (err) => setActionError({ userId, error: err }) },
    );
  }

  function handleSuspendSeller(userId: string) {
    setActionError(null);
    suspendSeller.mutate(
      { userId, body: { reason: suspendReasons[userId] || "정책 위반" } },
      { onError: (err) => setActionError({ userId, error: err }) },
    );
  }

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="사용자 관리" />
        <Input
          type="text"
          placeholder="이메일 검색"
          value={emailSearch}
          onChange={(e) => setEmailSearch(e.target.value)}
          className="max-w-sm"
        />
        {isLoading ? (
          <LoadingState />
        ) : users && users.length > 0 ? (
          <ul className="space-y-3">
            {users.map((u) => (
              <li key={u.userId} className="rounded-lg border border-border bg-surface p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="font-medium text-fg">{u.email}</p>
                      <StatusBadge status={u.status} />
                    </div>
                    <p className="text-sm text-fg-muted">
                      가입 {new Date(u.createdAt).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Select
                      value={u.status}
                      onChange={(e) => handleChangeStatus(u.userId, e.target.value)}
                      className="w-32"
                    >
                      {STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </Select>
                    <Input
                      type="text"
                      placeholder="판매정지 사유"
                      value={suspendReasons[u.userId] ?? ""}
                      onChange={(e) =>
                        setSuspendReasons((r) => ({ ...r, [u.userId]: e.target.value }))
                      }
                      className="w-40"
                    />
                    <Button
                      type="button"
                      variant="danger"
                      size="sm"
                      onClick={() => handleSuspendSeller(u.userId)}
                    >
                      판매정지
                    </Button>
                  </div>
                </div>
                {actionError?.userId === u.userId && (
                  <div className="mt-3">
                    <UserActionError error={actionError.error} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState>사용자가 없습니다.</EmptyState>
        )}
      </div>
    </AppShell>
  );
}
