import {
  useLogout,
  useRegisterWhitelistEntry,
  useRequestAccountDeletion,
  useWhitelistEntries,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { Alert, Button, Card, CardTitle, EmptyState, Field, Input, LoadingState, PageHeader, Select } from "@aios/ui-web";
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
        <PageHeader title="계정 보안 설정" />

        <Card>
          <CardTitle>비상 출금 목적지 화이트리스트</CardTitle>
          <p className="mb-4 text-xs text-fg-muted">
            위기 상황이 닥친 뒤에는 신규 등록이 불가능합니다 — 평상시에 미리 등록해두세요.
          </p>
          {whitelistLoading ? (
            <LoadingState />
          ) : whitelist && whitelist.length > 0 ? (
            <ul className="mb-4 space-y-1 text-sm text-fg-secondary">
              {whitelist.map((w) => (
                <li key={w.id}>
                  {w.exchange} — {w.destinationAddress} {w.label && `(${w.label})`}
                </li>
              ))}
            </ul>
          ) : (
            <div className="mb-4">
              <EmptyState>등록된 목적지가 없습니다.</EmptyState>
            </div>
          )}
          <form onSubmit={handleWhitelistSubmit} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="거래소">
                <Select value={wlExchange} onChange={(e) => setWlExchange(e.target.value)}>
                  <option value="bitget">bitget</option>
                </Select>
              </Field>
              <Field label="라벨 (선택)">
                <Input type="text" value={wlLabel} onChange={(e) => setWlLabel(e.target.value)} />
              </Field>
            </div>
            <Field label="출금 목적지 주소">
              <Input type="text" required value={wlAddress} onChange={(e) => setWlAddress(e.target.value)} />
            </Field>
            <Field label="비밀번호 확인">
              <Input
                type="password"
                required
                value={wlPassword}
                onChange={(e) => setWlPassword(e.target.value)}
              />
            </Field>
            {wlError && <Alert>{wlError}</Alert>}
            <Button type="submit" loading={registerWhitelist.isPending}>
              목적지 등록
            </Button>
          </form>
        </Card>

        <Card className="border-danger/30">
          <h2 className="mb-2 text-lg font-semibold text-danger">회원 탈퇴</h2>
          {deletionResult ? (
            <div className="space-y-3">
              <Alert tone="success">{deletionResult}</Alert>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  logout();
                  navigate("/login");
                }}
              >
                로그아웃
              </Button>
            </div>
          ) : (
            <form onSubmit={handleDeleteSubmit} className="space-y-3">
              <p className="text-xs text-fg-muted">
                RUNNING 상태 실행이 있으면 탈퇴가 거부됩니다 — 먼저 모든 실행을 중지해주세요.
              </p>
              <Field label="비밀번호 확인">
                <Input
                  type="password"
                  required
                  value={deletePassword}
                  onChange={(e) => setDeletePassword(e.target.value)}
                />
              </Field>
              {deleteError && <Alert>{deleteError}</Alert>}
              <Button type="submit" variant="danger" loading={requestDeletion.isPending}>
                탈퇴 요청
              </Button>
            </form>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
