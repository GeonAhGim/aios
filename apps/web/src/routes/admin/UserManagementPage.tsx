import { useAdminUsers, useChangeUserStatus, useSuspendSeller } from "@aios/shared-hooks";
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
        <h1 className="text-2xl font-semibold text-slate-100">사용자 관리</h1>
        <input
          type="text"
          placeholder="이메일 검색"
          value={emailSearch}
          onChange={(e) => setEmailSearch(e.target.value)}
          className="w-full max-w-sm rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
        />
        {isLoading ? (
          <p className="text-slate-500">불러오는 중...</p>
        ) : users && users.length > 0 ? (
          <ul className="space-y-3">
            {users.map((u) => (
              <li key={u.userId} className="rounded-lg border border-slate-800 p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-slate-100">{u.email}</p>
                    <p className="text-sm text-slate-500">
                      상태 {u.status} · 가입 {new Date(u.createdAt).toLocaleDateString()}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <select
                      value={u.status}
                      onChange={(e) =>
                        changeStatus.mutate({ userId: u.userId, status: e.target.value })
                      }
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
                    >
                      {STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                    <input
                      type="text"
                      placeholder="판매정지 사유"
                      value={suspendReasons[u.userId] ?? ""}
                      onChange={(e) =>
                        setSuspendReasons((r) => ({ ...r, [u.userId]: e.target.value }))
                      }
                      className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-100"
                    />
                    <button
                      type="button"
                      onClick={() =>
                        suspendSeller.mutate({
                          userId: u.userId,
                          body: { reason: suspendReasons[u.userId] || "정책 위반" },
                        })
                      }
                      className="rounded border border-red-900 px-3 py-1 text-sm text-red-400 hover:bg-red-950"
                    >
                      판매정지
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-slate-500">사용자가 없습니다.</p>
        )}
      </div>
    </AppShell>
  );
}
