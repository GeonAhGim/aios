import {
  useActivateMandateRevision,
  useCreateMandateDraft,
  useMandateStatus,
  usePauseMandate,
  useProposeMandateAmendment,
  useResumeMandate,
} from "@aios/shared-hooks";
import { classifyStateConflict } from "@aios/shared-types";
import type { MandateRevisionView, MandateRuleInput } from "@aios/shared-types";
import { Badge, Button, Card, LoadingState, PageHeader, StatusBadge } from "@aios/ui-web";
import { useState } from "react";
import { AppShell } from "../../components/layout/AppShell";
import { MandateActionError } from "./MandateActionError";
import { MandatePolicyPanel } from "./MandatePolicyPanel";
import { MandateRuleForm } from "./MandateRuleForm";

function RevisionRules({ revision }: { revision: MandateRevisionView }) {
  return (
    <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm text-fg-muted">
      <li>총 노출 한도: {revision.maxTotalExposurePct}%</li>
      <li>단일 종목 한도: {revision.maxSingleInstrumentPct}%</li>
      <li>최소 현금 버퍼: {revision.minCashBufferPct}%</li>
      <li>일일 손실 한도: {revision.maxDailyLossPct}%</li>
      <li>허용 자율성: {revision.allowedAutonomy}</li>
      <li>금지 자산: {revision.forbiddenAssets.length > 0 ? revision.forbiddenAssets.join(", ") : "없음"}</li>
    </ul>
  );
}

// DoD(a) fail-closed 표기: activeRevision이 null이면 "제한 없음"이 아니라 "위임장
// 미설정(주문 차단)"이라고 표기한다 — 반대로 렌더하면 반려 대상이다.
function ActiveRevisionCard({
  activeRevision,
  onPause,
  onResume,
  pausePending,
  resumePending,
  lifecycleError,
}: {
  activeRevision: MandateRevisionView | null;
  onPause: () => void;
  onResume: () => void;
  pausePending: boolean;
  resumePending: boolean;
  lifecycleError: unknown;
}) {
  return (
    <Card>
      <div className="flex items-center gap-2">
        <h2 className="font-medium text-fg">현재 활성 리비전</h2>
        {activeRevision ? (
          <StatusBadge status={activeRevision.state} />
        ) : (
          <Badge tone="warning">위임장 미설정(주문 차단)</Badge>
        )}
      </div>
      {activeRevision ? (
        <>
          <RevisionRules revision={activeRevision} />
          <div className="mt-3">
            {activeRevision.state === "ACTIVE" && (
              <Button type="button" variant="secondary" size="sm" loading={pausePending} onClick={onPause}>
                일시정지
              </Button>
            )}
            {activeRevision.state === "PAUSED" && (
              <Button type="button" variant="secondary" size="sm" loading={resumePending} onClick={onResume}>
                재개
              </Button>
            )}
          </div>
          {lifecycleError !== null && (
            <div className="mt-2">
              <MandateActionError error={lifecycleError} />
            </div>
          )}
        </>
      ) : (
        <p className="mt-2 text-sm text-fg-muted">
          활성 위임장이 없습니다 — 규칙이 없다는 뜻이 아니라 모든 주문이 차단된다는 뜻입니다.
        </p>
      )}
    </Card>
  );
}

export function MandatesPage() {
  const { data, isLoading, isError, error, refetch } = useMandateStatus();
  const createDraft = useCreateMandateDraft();
  const proposeAmendment = useProposeMandateAmendment();
  const activate = useActivateMandateRevision();
  const pause = usePauseMandate();
  const resume = useResumeMandate();

  const [createError, setCreateError] = useState<unknown>(null);
  const [activateError, setActivateError] = useState<unknown>(null);
  const [lifecycleError, setLifecycleError] = useState<unknown>(null);

  function handleCreate(rules: MandateRuleInput) {
    setCreateError(null);
    const mutation = data?.activeRevision ? proposeAmendment : createDraft;
    mutation.mutate(rules, { onError: setCreateError });
  }

  function handleActivate(revisionId: string) {
    setActivateError(null);
    activate.mutate(
      { revisionId },
      {
        onError: (err) => {
          setActivateError(err);
          // DoD(c): 동시 activate 경합(409 STATE_CONCURRENCY_CONFLICT)은 재조회로
          // 최신 activeRevision을 다시 반영한다 — 메시지 노출과 별개로 즉시 수행한다.
          if (classifyStateConflict(err) === "refetch_retry") refetch();
        },
      },
    );
  }

  if (isError) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title="위임장(Mandate)" />
          <MandateActionError error={error} onRetry={() => refetch()} />
        </div>
      </AppShell>
    );
  }

  if (isLoading || !data) {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title="위임장(Mandate)" />
          <LoadingState />
        </div>
      </AppShell>
    );
  }

  const { activeRevision, pendingRevision } = data;

  return (
    <AppShell>
      <div className="space-y-6">
        <PageHeader title="위임장(Mandate)" />

        <ActiveRevisionCard
          activeRevision={activeRevision}
          onPause={() => pause.mutate(undefined, { onError: setLifecycleError })}
          onResume={() => resume.mutate(undefined, { onError: setLifecycleError })}
          pausePending={pause.isPending}
          resumePending={resume.isPending}
          lifecycleError={lifecycleError}
        />

        <Card>
          <h2 className="font-medium text-fg">대기 중 개정안</h2>
          {pendingRevision ? (
            <>
              <div className="mt-1">
                <StatusBadge status={pendingRevision.state} />
              </div>
              <RevisionRules revision={pendingRevision} />
              <div className="mt-3">
                <Button type="button" loading={activate.isPending} onClick={() => handleActivate(pendingRevision.id)}>
                  활성화
                </Button>
              </div>
              {/* DoD(b): CM-5 직무분리 위반은 버튼을 비활성화하지 않고 거부 사유를
                  노출한다 — MandateActionError가 그 배너를 담당한다. */}
              {activateError !== null && (
                <div className="mt-2">
                  <MandateActionError error={activateError} onRetry={() => refetch()} />
                </div>
              )}
            </>
          ) : (
            <div className="mt-3">
              <MandateRuleForm
                submitLabel={activeRevision ? "개정안 제안" : "초안 작성"}
                pending={createDraft.isPending || proposeAmendment.isPending}
                onSubmit={handleCreate}
              />
              {createError !== null && (
                <div className="mt-2">
                  <MandateActionError error={createError} />
                </div>
              )}
            </div>
          )}
        </Card>

        <MandatePolicyPanel />
      </div>
    </AppShell>
  );
}
