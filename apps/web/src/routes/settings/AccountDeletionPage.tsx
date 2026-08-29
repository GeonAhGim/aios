import {
  useLogout,
  useRegisterWhitelistEntry,
  useRequestAccountDeletion,
  useWhitelistEntries,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";

// 17번 문서 라우팅 표에 출금 화이트리스트(FD-11.5) 전용 화면이 없어(스펙
// 누락으로 판단) 계정 보안 성격이 같은 이 화면에 함께 둔다.
export function AccountDeletionPage() {
  const { data: whitelist, isLoading: whitelistLoading } = useWhitelistEntries();
  const registerWhitelist = useRegisterWhitelistEntry();
  const [wlExchange, setWlExchange] = useState("bitget");
  const [wlAddress, setWlAddress] = useState("");
  const [wlLabel, setWlLabel] = useState("");
  const [wlPassword, setWlPassword] = useState("");
  const [wlError, setWlError] = useState<string | null>(null);

  const requestDeletion = useRequestAccountDeletion();
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletionResult, setDeletionResult] = useState<string | null>(null);
  const logout = useLogout();
  const navigate = useNavigate();

  async function handleWhitelistSubmit(e: FormEvent) {
    e.preventDefault();
    setWlError(null);
    try {
      await registerWhitelist.mutateAsync({
        exchange: wlExchange,
        destinationAddress: wlAddress,
        label: wlLabel || undefined,
        password: wlPassword,
      });
      setWlAddress("");
      setWlLabel("");
      setWlPassword("");
    } catch (err) {
      setWlError(err instanceof ApiError ? err.message : "등록에 실패했습니다.");
    }
  }

  async function handleDeleteSubmit(e: FormEvent) {
    e.preventDefault();
    setDeleteError(null);
    try {
      const result = await requestDeletion.mutateAsync({ password: deletePassword });
      setDeletionResult(
        `탈퇴가 예약됐습니다. ${new Date(result.deletionEffectiveAt).toLocaleString()}에 확정됩니다.`,
      );
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "탈퇴 요청에 실패했습니다.");
    }
  }

  return (
    <AppShell>
      <div className="max-w-lg space-y-8">
        <h1 className="text-2xl font-semibold text-slate-100">계정 보안 설정</h1>

        <section className="rounded-lg border border-slate-800 p-6">
          <h2 className="mb-2 text-lg font-medium text-slate-100">비상 출금 목적지 화이트리스트</h2>
          <p className="mb-4 text-xs text-slate-500">
            위기 상황이 닥친 뒤에는 신규 등록이 불가능합니다 — 평상시에 미리 등록해두세요.
          </p>
          {whitelistLoading ? (
            <p className="text-slate-500">불러오는 중...</p>
          ) : whitelist && whitelist.length > 0 ? (
            <ul className="mb-4 space-y-1 text-sm text-slate-300">
              {whitelist.map((w) => (
                <li key={w.id}>
                  {w.exchange} — {w.destinationAddress} {w.label && `(${w.label})`}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mb-4 text-sm text-slate-500">등록된 목적지가 없습니다.</p>
          )}
          <form onSubmit={handleWhitelistSubmit} className="space-y-2">
            <div className="grid grid-cols-2 gap-2">
              <select
                value={wlExchange}
                onChange={(e) => setWlExchange(e.target.value)}
                className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              >
                <option value="bitget">bitget</option>
              </select>
              <input
                type="text"
                placeholder="라벨(선택)"
                value={wlLabel}
                onChange={(e) => setWlLabel(e.target.value)}
                className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              />
            </div>
            <input
              type="text"
              required
              placeholder="출금 목적지 주소"
              value={wlAddress}
              onChange={(e) => setWlAddress(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
            <input
              type="password"
              required
              placeholder="비밀번호 확인"
              value={wlPassword}
              onChange={(e) => setWlPassword(e.target.value)}
              className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
            />
            {wlError && <p className="text-sm text-red-400">{wlError}</p>}
            <button
              type="submit"
              disabled={registerWhitelist.isPending}
              className="rounded bg-slate-100 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-white disabled:opacity-50"
            >
              {registerWhitelist.isPending ? "등록 중..." : "목적지 등록"}
            </button>
          </form>
        </section>

        <section className="rounded-lg border border-red-900 p-6">
          <h2 className="mb-2 text-lg font-medium text-red-400">회원 탈퇴</h2>
          {deletionResult ? (
            <div className="space-y-3">
              <p className="text-sm text-emerald-300">{deletionResult}</p>
              <button
                type="button"
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
                className="rounded border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800"
              >
                로그아웃
              </button>
            </div>
          ) : (
            <form onSubmit={handleDeleteSubmit} className="space-y-2">
              <p className="text-xs text-slate-500">
                RUNNING 상태 실행이 있으면 탈퇴가 거부됩니다 — 먼저 모든 실행을 중지해주세요.
              </p>
              <input
                type="password"
                required
                placeholder="비밀번호 확인"
                value={deletePassword}
                onChange={(e) => setDeletePassword(e.target.value)}
                className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-slate-100"
              />
              {deleteError && <p className="text-sm text-red-400">{deleteError}</p>}
              <button
                type="submit"
                disabled={requestDeletion.isPending}
                className="rounded border border-red-800 px-4 py-2 text-sm text-red-400 hover:bg-red-950 disabled:opacity-50"
              >
                {requestDeletion.isPending ? "처리 중..." : "탈퇴 요청"}
              </button>
            </form>
          )}
        </section>
      </div>
    </AppShell>
  );
}
