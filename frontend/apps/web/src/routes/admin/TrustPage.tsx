import {
  useGrantMembership,
  useRevokeConsent,
  useRevokeMembership,
  useSuspendMembership,
  useTrustStatus,
} from "@aios/shared-hooks";
import { ApiError } from "@aios/api-client";
import { classifyMembershipError, describeMembershipError, classifyForbidden, routeApiError } from "@aios/shared-types";
import type { ConsentDecisionView, MembershipRole, TrustMembershipView } from "@aios/shared-types";
import { Button, EmptyState, Field, Input, LoadingState, PageHeader, Select, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { ErrorMessage } from "../../components/ErrorMessage";
import { ForbiddenNotice } from "../../components/ForbiddenNotice";

const MEMBERSHIP_ROLE_OPTIONS: MembershipRole[] = ["OWNER", "ADMIN", "MEMBER", "AUDITOR", "SERVICE"];

// spec §3.3 에러 taxonomy: 동의 철회 실패는 err.message를 직접 노출하지 않고
// classifyForbidden/routeApiError로 판정해 403/그 외를 ForbiddenNotice/ErrorMessage
// 경로로만 보여준다(ReconciliationPage/SafetyControlsPage와 동일 3-way 패턴). 404
// (교차 테넌트·이미 폐기)도 이 경로로 그대로 보여준다 — EmptyState로 떨어지지
// 않는다(I-10, "빈 목록"으로 위장하지 않는다).
function ConsentActionError({ error }: { error: unknown }) {
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

// 멤버십 grant/suspend/revoke 실패는 classifyMembershipError(membershipMutation.ts,
// task-856 선례)만 쓴다 — 새 403/404/409 분류기를 만들지 않는다(decision). MembersPage.tsx
// (task-617/1159)의 MutationErrorBanner와 동일 순서를 그대로 따른다: last_owner_denied만
// describeMembershipError 고정 문구로 먼저 잡고, 그 외 403(교차 테넌트 포함)은
// ForbiddenNotice에 위임하며, 나머지(404 already_revoked 등)는 describeMembershipError로
// 보여준다. errorCode를 ErrorMessage에 넘기지 않는다 — 넘기면 getApiErrorMessage가
// EXACT_MESSAGES[errorCode]를 이 고정 문구보다 우선시켜 덮어쓴다.
function MembershipActionError({ error }: { error: unknown }) {
  const reason = classifyMembershipError(error);
  if (reason === "last_owner_denied") return <ErrorMessage message={describeMembershipError(reason)} />;
  if (classifyForbidden(error)) return <ForbiddenNotice error={error} />;
  return <ErrorMessage message={describeMembershipError(reason)} />;
}

function ConsentRow({
  consent,
  onRevoke,
  revoking,
}: {
  consent: ConsentDecisionView;
  onRevoke: () => void;
  revoking: boolean;
}) {
  return (
    <li className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <p className="font-medium text-fg">{consent.purpose}</p>
            <StatusBadge status={consent.state} />
          </div>
          <p className="text-xs text-fg-muted">
            동의 ID {consent.consentId} · 공시 개정 {consent.disclosureRevision}
            {consent.acceptedAt && ` · 동의: ${new Date(consent.acceptedAt).toLocaleString()}`}
            {consent.revokedAt && ` · 철회: ${new Date(consent.revokedAt).toLocaleString()}`}
          </p>
        </div>
        <Button type="button" variant="secondary" size="sm" loading={revoking} onClick={onRevoke}>
          동의 철회
        </Button>
      </div>
    </li>
  );
}

// 멤버십 상태 전이 버튼(정지/폐기)은 항상 노출한다 — §4.1 전이표를 프론트에
// 복제해 미리 숨기지 않는다(decision: 서버가 SSOT). 서버가 거부하는 전이(예:
// REVOKED→SUSPENDED, 마지막 OWNER 폐기)를 눌렀을 때는 MembershipActionError가
// 거부 사유를 보여준다.
function MembershipResultCard({
  membership,
  onSuspend,
  onRevoke,
  suspending,
  revoking,
}: {
  membership: TrustMembershipView;
  onSuspend: () => void;
  onRevoke: () => void;
  suspending: boolean;
  revoking: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-center gap-2">
        <p className="font-medium text-fg">{membership.subjectId}</p>
        <StatusBadge status={membership.state} />
        <span className="text-xs text-fg-muted">{membership.role}</span>
      </div>
      <p className="text-xs text-fg-muted">
        멤버십 ID {membership.membershipId} · revision {membership.revision}
      </p>
      <div className="mt-3 flex gap-2">
        <Button type="button" variant="secondary" size="sm" loading={suspending} onClick={onSuspend}>
          정지(suspend)
        </Button>
        <Button type="button" variant="danger" size="sm" loading={revoking} onClick={onRevoke}>
          폐기(revoke)
        </Button>
      </div>
    </div>
  );
}

export function TrustPage() {
  const { data, isLoading, isError, error, refetch } = useTrustStatus();
  const revokeConsent = useRevokeConsent();
  const grantMembership = useGrantMembership();
  const suspendMembership = useSuspendMembership();
  const revokeMembership = useRevokeMembership();

  const [consentActionError, setConsentActionError] = useState<{ consentId: string; error: unknown } | null>(null);

  const [grantSubjectId, setGrantSubjectId] = useState("");
  const [grantRole, setGrantRole] = useState<MembershipRole>("MEMBER");
  const [actionSubjectId, setActionSubjectId] = useState("");
  const [membershipResult, setMembershipResult] = useState<TrustMembershipView | null>(null);
  const [membershipError, setMembershipError] = useState<unknown>(null);

  function handleRevokeConsent(consentId: string) {
    setConsentActionError(null);
    revokeConsent.mutate(consentId, { onError: (err) => setConsentActionError({ consentId, error: err }) });
  }

  function handleGrant() {
    if (!grantSubjectId.trim()) return;
    setMembershipError(null);
    grantMembership.mutate(
      { subjectId: grantSubjectId.trim(), role: grantRole },
      { onSuccess: (view) => setMembershipResult(view), onError: (err) => setMembershipError(err) },
    );
  }

  function handleSuspend() {
    if (!actionSubjectId.trim()) return;
    setMembershipError(null);
    suspendMembership.mutate(actionSubjectId.trim(), {
      onSuccess: (view) => setMembershipResult(view),
      onError: (err) => setMembershipError(err),
    });
  }

  function handleRevokeMembership() {
    if (!actionSubjectId.trim()) return;
    setMembershipError(null);
    revokeMembership.mutate(actionSubjectId.trim(), {
      onSuccess: (view) => setMembershipResult(view),
      onError: (err) => setMembershipError(err),
    });
  }

  return (
    <AppShell>
      <div className="space-y-8">
        <PageHeader title="신뢰(Trust) 멤버십·동의" />

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-fg">동의(Consent) 현황</h2>
          {isError ? (
            <ConsentActionError error={error} />
          ) : isLoading ? (
            <LoadingState />
          ) : data && data.consents.length > 0 ? (
            <ul className="space-y-3">
              {data.consents.map((consent) => (
                <li key={consent.consentId}>
                  <ConsentRow
                    consent={consent}
                    revoking={revokeConsent.isPending}
                    onRevoke={() => handleRevokeConsent(consent.consentId)}
                  />
                  {consentActionError?.consentId === consent.consentId && (
                    <div className="mt-2">
                      <ConsentActionError error={consentActionError.error} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState>등록된 동의 내역이 없습니다.</EmptyState>
          )}
          {!isError && !isLoading && (
            <Button type="button" variant="secondary" size="sm" onClick={() => refetch()}>
              새로고침
            </Button>
          )}
        </section>

        <section className="space-y-3 rounded-lg border border-border-strong bg-bg p-4">
          <h2 className="text-sm font-semibold text-fg">멤버십 부여(Grant)</h2>
          <div className="flex items-end gap-2">
            <Field label="대상 subject_id">
              <Input
                value={grantSubjectId}
                onChange={(e) => setGrantSubjectId(e.target.value)}
                placeholder="부여 대상 subject_id"
                className="w-72"
              />
            </Field>
            <Field label="역할">
              <Select value={grantRole} onChange={(e) => setGrantRole(e.target.value as MembershipRole)}>
                {MEMBERSHIP_ROLE_OPTIONS.map((role) => (
                  <option key={role} value={role}>
                    {role}
                  </option>
                ))}
              </Select>
            </Field>
            <Button
              type="button"
              size="sm"
              loading={grantMembership.isPending}
              disabled={!grantSubjectId.trim()}
              onClick={handleGrant}
            >
              부여
            </Button>
          </div>
        </section>

        <section className="space-y-3 rounded-lg border border-border-strong bg-bg p-4">
          <h2 className="text-sm font-semibold text-fg">멤버십 정지·폐기</h2>
          <div className="flex items-end gap-2">
            <Field label="대상 subject_id">
              <Input
                value={actionSubjectId}
                onChange={(e) => setActionSubjectId(e.target.value)}
                placeholder="정지·폐기 대상 subject_id"
                className="w-72"
              />
            </Field>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={suspendMembership.isPending}
              disabled={!actionSubjectId.trim()}
              onClick={handleSuspend}
            >
              정지(suspend)
            </Button>
            <Button
              type="button"
              variant="danger"
              size="sm"
              loading={revokeMembership.isPending}
              disabled={!actionSubjectId.trim()}
              onClick={handleRevokeMembership}
            >
              폐기(revoke)
            </Button>
          </div>

          {membershipError ? <MembershipActionError error={membershipError} /> : null}
          {membershipResult && (
            <MembershipResultCard
              membership={membershipResult}
              suspending={suspendMembership.isPending}
              revoking={revokeMembership.isPending}
              onSuspend={handleSuspend}
              onRevoke={handleRevokeMembership}
            />
          )}
        </section>
      </div>
    </AppShell>
  );
}
