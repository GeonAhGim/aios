import { useAdminUsers, useChangeUserStatus, useSuspendSeller } from "@aios/shared-hooks";
import { Button, EmptyState, Input, LoadingState, PageHeader, Select, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";

const STATUSES = ["ACTIVE", "SUSPENDED"];

export function UserManagementPage() {
  const [emailSearch, setEmailSearch] = useState("");
  const { data: users, isLoading } = useAdminUsers(emailSearch || undefined);
  const changeStatus = useChangeUserStatus();
  const suspendSeller = useSuspendSeller();
  const [suspendReasons, setSuspendReasons] = useState<Record<string, string>>({});

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
                      onChange={(e) => changeStatus.mutate({ userId: u.userId, status: e.target.value })}
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
                      onClick={() =>
                        suspendSeller.mutate({
                          userId: u.userId,
                          body: { reason: suspendReasons[u.userId] || "정책 위반" },
                        })
                      }
                    >
                      판매정지
                    </Button>
                  </div>
                </div>
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
