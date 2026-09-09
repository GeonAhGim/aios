import {
  useBeginConnection,
  useConfirmConnection,
  useConnections,
  useRevokeConnection,
  useSyncConnection,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyForbidden, routeApiError } from "@aios/shared-types";
import type { AccountConnectionView, AccountSnapshotView, CapabilityScope } from "@aios/shared-types";
import { Button, EmptyState, Field, Input, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useState, type FormEvent } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";
import { useFieldErrors } from "../../hooks/useFieldErrors";

const CAPABILITY_OPTIONS: CapabilityScope[] = ["READ_BALANCE", "READ_POSITION", "READ_ACTIVITY"];

// spec §3.3 에러 taxonomy: 목록 조회·행별 액션(confirm/sync/revoke) 실패는
// err.message를 직접 노출하지 않고 classifyForbidden/routeApiError로 판정해
// 403/그 외를 각각 ForbiddenNotice/ErrorMessage 경로로만 보여준다(TrustPage.tsx의
// ConsentActionError와 동일 3-way 패턴 재사용 — 새 에러 분류기 신설 금지, decision).
function ConnectionActionError({ error }: { error: unknown }) {
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

function ConnectionRow({
  connection,
  snapshot,
  onConfirm,
  onSync,
  onRevoke,
  confirming,
  syncing,
  revoking,
}: {
  connection: AccountConnectionView;
  snapshot: AccountSnapshotView | null;
  onConfirm: () => void;
  onSync: () => void;
  onRevoke: () => void;
  confirming: boolean;
  syncing: boolean;
  revoking: boolean;
}) {
  return (
    <li className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <p className="font-medium text-fg">{connection.maskedAccountLabel}</p>
            <StatusBadge status={connection.state} />
            {connection.scopeVerified && <span className="text-xs text-fg-muted">범위 검증됨</span>}
          </div>
          <p className="text-xs text-fg-muted">
            제공자 {connection.providerCode} · revision {connection.revision} ·{" "}
            {connection.capabilityProfile.join(", ")}
            {connection.createdAt && ` · 생성: ${new Date(connection.createdAt).toLocaleString()}`}
          </p>
        </div>
        <div className="flex gap-2">
          <Button type="button" variant="secondary" size="sm" loading={confirming} onClick={onConfirm}>
            확인(confirm)
          </Button>
          <Button type="button" variant="secondary" size="sm" loading={syncing} onClick={onSync}>
            동기화(sync)
          </Button>
          <Button type="button" variant="danger" size="sm" loading={revoking} onClick={onRevoke}>
            해제(revoke)
          </Button>
        </div>
      </div>
      {snapshot && (
        <div className="mt-3 rounded-md border border-border-strong bg-bg p-3 text-xs text-fg-muted">
          <p>
            스냅샷 {new Date(snapshot.capturedAt).toLocaleString()} · 신선도 {snapshot.freshness} ·{" "}
            {snapshot.currency}
          </p>
          <ul className="mt-1 space-y-0.5">
            {snapshot.values.map((value) => (
              <li key={`${value.entityType}:${value.entityKey}`}>
                {value.entityType}/{value.entityKey}: {value.value}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

export function ConnectionsPage() {
  const { data, isLoading, isError, error, refetch } = useConnections();
  const beginConnection = useBeginConnection();
  const confirmConnection = useConfirmConnection();
  const syncConnection = useSyncConnection();
  const revokeConnection = useRevokeConnection();
  const { fieldErrors, setFromError, clearField } = useFieldErrors();

  const [providerCode, setProviderCode] = useState("");
  const [opaqueAccountRef, setOpaqueAccountRef] = useState("");
  const [capabilityProfile, setCapabilityProfile] = useState<CapabilityScope[]>([]);
  const [createError, setCreateError] = useState<unknown>(null);

  const [rowAction, setRowAction] = useState<{ connectionId: string; kind: "confirm" | "sync" | "revoke" } | null>(
    null,
  );
  const [rowError, setRowError] = useState<{ connectionId: string; error: unknown } | null>(null);
  const [snapshots, setSnapshots] = useState<Record<string, AccountSnapshotView>>({});

  function toggleCapability(scope: CapabilityScope) {
    setCapabilityProfile((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  }

  function handleCreate(e: FormEvent) {
    e.preventDefault();
    if (!providerCode.trim() || !opaqueAccountRef.trim() || capabilityProfile.length === 0) return;
    setCreateError(null);
    setFromError(null);
    beginConnection.mutate(
      { providerCode: providerCode.trim(), opaqueAccountRef: opaqueAccountRef.trim(), requestedCapabilityProfile: capabilityProfile },
      {
        onSuccess: () => {
          setProviderCode("");
          setOpaqueAccountRef("");
          setCapabilityProfile([]);
        },
        onError: (err) => {
          setCreateError(err);
          setFromError(err);
        },
      },
    );
  }

  function handleConfirm(connectionId: string) {
    setRowError(null);
    setRowAction({ connectionId, kind: "confirm" });
    confirmConnection.mutate(connectionId, {
      onError: (err) => setRowError({ connectionId, error: err }),
      onSettled: () => setRowAction(null),
    });
  }

  function handleSync(connectionId: string) {
    setRowError(null);
    setRowAction({ connectionId, kind: "sync" });
    syncConnection.mutate(connectionId, {
      onSuccess: (snapshot) => setSnapshots((prev) => ({ ...prev, [connectionId]: snapshot })),
      onError: (err) => setRowError({ connectionId, error: err }),
      onSettled: () => setRowAction(null),
    });
  }

  function handleRevoke(connectionId: string) {
    setRowError(null);
    setRowAction({ connectionId, kind: "revoke" });
    revokeConnection.mutate(connectionId, {
      onError: (err) => setRowError({ connectionId, error: err }),
      onSettled: () => setRowAction(null),
    });
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="계정 연동(Connections)" />

        <section className="space-y-3">
          {isError ? (
            <ConnectionActionError error={error} />
          ) : isLoading ? (
            <LoadingState />
          ) : data && data.connections.length > 0 ? (
            <ul className="space-y-3">
              {data.connections.map((connection) => (
                <li key={connection.id}>
                  <ConnectionRow
                    connection={connection}
                    snapshot={snapshots[connection.id] ?? null}
                    confirming={rowAction?.connectionId === connection.id && rowAction.kind === "confirm"}
                    syncing={rowAction?.connectionId === connection.id && rowAction.kind === "sync"}
                    revoking={rowAction?.connectionId === connection.id && rowAction.kind === "revoke"}
                    onConfirm={() => handleConfirm(connection.id)}
                    onSync={() => handleSync(connection.id)}
                    onRevoke={() => handleRevoke(connection.id)}
                  />
                  {rowError?.connectionId === connection.id && (
                    <div className="mt-2">
                      <ConnectionActionError error={rowError.error} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>등록된 계정 연동이 없습니다.</EmptyState>
          )}
          {!isError && !isLoading && (
            <Button type="button" variant="secondary" size="sm" onClick={() => refetch()}>
              새로고침
            </Button>
          )}
        </section>

        <section className="space-y-3 rounded-lg border border-border-strong bg-bg p-4">
          <h2 className="text-sm font-semibold text-fg">새 연동 생성</h2>
          <form className="space-y-3" onSubmit={handleCreate}>
            <div className="flex items-end gap-2">
              <Field label="제공자 코드" error={fieldErrors.provider_code}>
                <Input
                  value={providerCode}
                  onChange={(e) => {
                    setProviderCode(e.target.value);
                    clearField("provider_code");
                  }}
                  placeholder="예: toss"
                  className="w-48"
                />
              </Field>
              <Field label="opaque_account_ref" error={fieldErrors.opaque_account_ref}>
                <Input
                  value={opaqueAccountRef}
                  onChange={(e) => {
                    setOpaqueAccountRef(e.target.value);
                    clearField("opaque_account_ref");
                  }}
                  placeholder="연동 대상 참조값"
                  className="w-72"
                />
              </Field>
            </div>
            <fieldset className="space-y-1.5">
              <legend className="text-sm font-medium text-fg-secondary">요청 권한 범위</legend>
              <div className="flex gap-4">
                {CAPABILITY_OPTIONS.map((scope) => (
                  <label key={scope} className="flex items-center gap-1.5 text-sm text-fg">
                    <input
                      type="checkbox"
                      className="accent-accent"
                      checked={capabilityProfile.includes(scope)}
                      onChange={() => {
                        toggleCapability(scope);
                        clearField("requested_capability_profile");
                      }}
                    />
                    {scope}
                  </label>
                ))}
              </div>
              {fieldErrors.requested_capability_profile && (
                <p className="text-xs text-danger">{fieldErrors.requested_capability_profile}</p>
              )}
            </fieldset>
            <Button
              type="submit"
              size="sm"
              loading={beginConnection.isPending}
              disabled={!providerCode.trim() || !opaqueAccountRef.trim() || capabilityProfile.length === 0}
            >
              생성
            </Button>
          </form>
          {createError ? <ConnectionActionError error={createError} /> : null}
        </section>
      </div>
    </AppShell>
  );
}
