import {
  useLogout,
  useRegisterWhitelistEntry,
  useRequestAccountDeletion,
  useWhitelistEntries,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyBadRequest, classifyForbidden, routeApiError } from "@aios/shared-types";
import { Alert, Button, Card, CardTitle, EmptyState, Field, Input, LoadingState, PageHeader, Select } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { AppShell } from "../../components/layout/AppShell";
import { BadRequestNotice } from "../../components/BadRequestNotice";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { exchangeLabel } from "../../lib/exchangeLabels";
import { useTranslation } from "react-i18next";

// 17번 문서 라우팅 표에 출금 화이트리스트(FD-11.5) 전용 화면이 없어(스펙
// 누락으로 판단) 계정 보안 성격이 같은 이 화면에 함께 둔다.
//
// spec §3.3 에러 taxonomy: 화이트리스트 등록·탈퇴 요청 실패는 err.message를 직접
// 노출하지 않고 routeApiError(task-483)로 판정해 400/403/그 외를 각각
// BadRequestNotice/ForbiddenNotice/ErrorMessage 경로로만 보여준다(task-901 패턴).
function AccountSecurityError({ error }: { error: unknown }) {
  if (classifyBadRequest(error)) return <BadRequestNotice error={error} />;
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

export function AccountDeletionPage() {
  const { t } = useTranslation();
  const { data: whitelist, isLoading: whitelistLoading } = useWhitelistEntries();
  const registerWhitelist = useRegisterWhitelistEntry();
  const [wlExchange, setWlExchange] = useState("bitget");
  const [wlAddress, setWlAddress] = useState("");
  const [wlLabel, setWlLabel] = useState("");
  const [wlPassword, setWlPassword] = useState("");
  const [wlError, setWlError] = useState<unknown>(null);

  const requestDeletion = useRequestAccountDeletion();
  const [deletePassword, setDeletePassword] = useState("");
  const [deleteError, setDeleteError] = useState<unknown>(null);
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
      setWlError(err instanceof ApiError ? err : new Error(t("legacy.accountDeletionPage.t15")));
    }
  }

  async function handleDeleteSubmit(e: FormEvent) {
    e.preventDefault();
    setDeleteError(null);
    try {
      const result = await requestDeletion.mutateAsync({ password: deletePassword });
      setDeletionResult(
        t("legacy.accountDeletionPage.t16", { val: new Date(result.deletionEffectiveAt).toLocaleString() }),
      );
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err : new Error(t("legacy.accountDeletionPage.t17")));
    }
  }

  return (
    <AppShell>
      <div className="max-w-lg space-y-8">
        <PageHeader title={t("legacy.accountDeletionPage.title1")} />

        <Card>
          <CardTitle>{t("legacy.accountDeletionPage.t2")}</CardTitle>
          <p className="mb-4 text-xs text-fg-muted">
            {t("legacy.accountDeletionPage.t3")}</p>
          {whitelistLoading ? (
            <LoadingState />
          ) : whitelist && whitelist.length > 0 ? (
            <ul className="mb-4 space-y-1 text-sm text-fg-secondary">
              {whitelist.map((w) => (
                <li key={w.id}>
                  {exchangeLabel(w.exchange)} — {w.destinationAddress} {w.label && `(${w.label})`}
                </li>
              ))}
            </ul>
          ) : (
            <div className="mb-4">
              <EmptyState>{t("legacy.accountDeletionPage.t4")}</EmptyState>
            </div>
          )}
          <form onSubmit={handleWhitelistSubmit} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("legacy.accountDeletionPage.label5")}>
                <Select value={wlExchange} onChange={(e) => setWlExchange(e.target.value)}>
                  <option value="bitget">bitget</option>
                </Select>
              </Field>
              <Field label={t("legacy.accountDeletionPage.label6")}>
                <Input type="text" value={wlLabel} onChange={(e) => setWlLabel(e.target.value)} />
              </Field>
            </div>
            <Field label={t("legacy.accountDeletionPage.label7")} htmlFor="wlAddress">
              <Input
                id="wlAddress"
                type="text"
                required
                value={wlAddress}
                onChange={(e) => setWlAddress(e.target.value)}
              />
            </Field>
            <Field label={t("legacy.accountDeletionPage.label8")} htmlFor="wlPassword">
              <Input
                id="wlPassword"
                type="password"
                required
                value={wlPassword}
                onChange={(e) => setWlPassword(e.target.value)}
              />
            </Field>
            {wlError !== null && <AccountSecurityError error={wlError} />}
            <Button type="submit" loading={registerWhitelist.isPending}>
              {t("legacy.accountDeletionPage.t9")}</Button>
          </form>
        </Card>

        <Card className="border-danger/30">
          <h2 className="mb-2 text-lg font-semibold text-danger">{t("legacy.accountDeletionPage.t10")}</h2>
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
                {t("legacy.accountDeletionPage.t11")}</Button>
            </div>
          ) : (
            <form onSubmit={handleDeleteSubmit} className="space-y-3">
              <p className="text-xs text-fg-muted">
                {t("legacy.accountDeletionPage.t12")}</p>
              <Field label={t("legacy.accountDeletionPage.label13")} htmlFor="deletePassword">
                <Input
                  id="deletePassword"
                  type="password"
                  required
                  value={deletePassword}
                  onChange={(e) => setDeletePassword(e.target.value)}
                />
              </Field>
              {deleteError !== null && <AccountSecurityError error={deleteError} />}
              <Button type="submit" variant="danger" loading={requestDeletion.isPending}>
                {t("legacy.accountDeletionPage.t14")}</Button>
            </form>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
